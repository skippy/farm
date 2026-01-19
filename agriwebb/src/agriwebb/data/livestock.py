"""Livestock data helpers for AgriWebb.

Provides functions to fetch and analyze animal data including:
- Animal listings and details
- Genetic lineage (sire/dam relationships)
- Mobs/herds
- Weight records
- Health treatments
- Full cache download for local analysis
"""

import asyncio
import contextlib
import json
from datetime import datetime
from pathlib import Path

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from agriwebb.core import get_cache_dir, graphql, settings


# 500 errors should NOT be retried - they indicate server overload
# Only retry on timeouts/connection errors
class RetryableError(Exception):
    """Transient error that should be retried (timeouts, connection errors)."""

    pass


# =============================================================================
# Configuration Constants
# =============================================================================

# Default cache file name
DEFAULT_CACHE_FILE = "animals.json"

# Cache freshness threshold - skip re-fetch if cache is younger than this
CACHE_FRESHNESS_HOURS = 24

# Concurrency control: max parallel API requests
MAX_CONCURRENT_REQUESTS = 5

# Small delay between paginated requests (seconds)
PAGINATION_DELAY = 0.1

# Retry configuration
MAX_RETRIES = 3  # Reduced - fail faster on persistent errors
MIN_WAIT_SECONDS = 1
MAX_WAIT_SECONDS = 10

# Circuit breaker - stop everything after too many consecutive failures
# Now that we don't retry 500 errors, we can use a lower threshold
MAX_CONSECUTIVE_FAILURES = 5

# =============================================================================
# GraphQL Query Fragments (for individual queries)
# =============================================================================

ANIMAL_IDENTITY_FIELDS = """
    identity {
        name
        eid
        vid
        managementTag
    }
"""

ANIMAL_CHARACTERISTICS_FIELDS = """
    characteristics {
        birthDate
        birthYear
        breedAssessed
        sex
        speciesCommonName
        visualColor
        ageClass
    }
"""

ANIMAL_STATE_FIELDS = """
    state {
        onFarm
        currentLocationId
        fate
        reproductiveStatus
        offspringCount
    }
"""

PARENTAGE_FIELDS = """
    parentage {
        sires {
            parentAnimalId
            parentAnimalIdentity { name vid eid }
            parentType
        }
        dams {
            parentAnimalId
            parentAnimalIdentity { name vid eid }
            parentType
        }
    }
"""

MANAGEMENT_GROUP_FIELDS = """
    managementGroup {
        managementGroupId
        name
    }
"""

# =============================================================================
# GraphQL Queries for Cache Download (using variables)
# =============================================================================

ANIMALS_QUERY = """
query GetAnimals($farmId: String!, $limit: Int!, $skip: Int!) {
  animals(farmId: $farmId, limit: $limit, skip: $skip) {
    animalId
    farmId
    identity {
      name
      eid
      vid
      managementTag
      brand
      tattoo
    }
    characteristics {
      birthDate
      birthYear
      birthDateAccuracy
      breedAssessed
      sex
      speciesCommonName
      visualColor
      ageClass
    }
    state {
      onFarm
      onFarmDate
      offFarmDate
      currentLocationId
      fate
      disposalMethod
      reproductiveStatus
      fertilityStatus
      offspringCount
      weaned
      lastSeen
      daysReared
    }
    parentage {
      sires {
        parentAnimalId
        parentAnimalIdentity { name vid eid }
        parentType
      }
      dams {
        parentAnimalId
        parentAnimalIdentity { name vid eid }
        parentType
      }
      surrogate {
        parentAnimalId
        parentAnimalIdentity { name vid eid }
        parentType
      }
    }
    managementGroupId
    managementGroup {
      managementGroupId
      name
      species
    }
  }
}
"""

# Records query for animal history
# TODO: Re-enable AnimalTreatmentRecord and FeedRecord fragments once AgriWebb fixes
# their API - these fragments cause 500 Internal Server Errors (as of Jan 2026)
#
#     ... on AnimalTreatmentRecord {
#       treatments {
#         healthProduct
#         reasonForTreatment
#         totalApplied { value unit }
#       }
#     }
#     ... on FeedRecord {
#       feeds {
#         feedType
#         amount { value unit }
#       }
#     }
#
RECORDS_QUERY_FULL = """
query GetRecords($farmId: String!, $animalId: String, $limit: Int!, $skip: Int!) {
  records(options: {farmId: $farmId, animalId: $animalId, limit: $limit, skip: $skip}) {
    recordId
    recordType
    observationDate
    ... on WeighRecord {
      weight { value unit }
      weighEvent
    }
    ... on ScoreRecord {
      score { value }
    }
    ... on LocationChangedRecord {
      locationId
    }
    ... on PregnancyScanRecord {
      fetusCount
      fetalAge
    }
  }
}
"""


CACHE_FIELDS_QUERY = """
query GetFields($farmId: String) {
  fields(filter: {farmId: {_eq: $farmId}}) {
    id
    name
    totalArea
    grazableArea
    landUse
  }
}
"""


# =============================================================================
# Exceptions
# =============================================================================


class AgriWebbAPIError(Exception):
    """Raised when AgriWebb API returns an error."""

    pass


class CircuitBreakerOpen(Exception):
    """Raised when too many consecutive failures have occurred."""

    pass


class CircuitBreaker:
    """Simple circuit breaker to stop after too many consecutive failures."""

    def __init__(self, max_failures: int = MAX_CONSECUTIVE_FAILURES):
        self.max_failures = max_failures
        self.consecutive_failures = 0
        self.is_open = False
        self._lock = asyncio.Lock()

    async def record_success(self):
        async with self._lock:
            self.consecutive_failures = 0

    async def record_failure(self):
        async with self._lock:
            self.consecutive_failures += 1
            if self.consecutive_failures >= self.max_failures:
                self.is_open = True
                print(f"\n  CIRCUIT BREAKER: {self.consecutive_failures} consecutive failures - stopping")

    async def check(self):
        if self.is_open:
            raise CircuitBreakerOpen(
                f"Stopping after {self.consecutive_failures} consecutive API failures. "
                "The AgriWebb API may be overloaded. Try again later."
            )


# =============================================================================
# GraphQL Helpers with Retry
# =============================================================================


@retry(
    retry=retry_if_exception_type(RetryableError),
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential_jitter(initial=MIN_WAIT_SECONDS, max=MAX_WAIT_SECONDS, jitter=2),
    reraise=True,
)
async def _graphql_with_retry(query: str, variables: dict | None = None) -> dict:
    """Execute a GraphQL query with exponential backoff + jitter on errors.

    Retries on:
    - Timeouts
    - Connection errors
    - HTTP 5xx errors (server overload)
    - GraphQL errors with "Internal Server Error"

    After MAX_RETRIES failures, raises AgriWebbAPIError.
    """
    try:
        result = await graphql(query, variables)
    except httpx.TimeoutException as e:
        raise RetryableError(f"Request timed out: {e}") from e
    except httpx.ConnectError as e:
        raise RetryableError(f"Connection failed: {e}") from e
    except httpx.HTTPStatusError as e:
        if e.response.status_code >= 500:
            # Server error - retry with backoff
            raise RetryableError(f"HTTP {e.response.status_code}") from e
        # Client error (4xx) - don't retry
        raise AgriWebbAPIError(f"HTTP {e.response.status_code}: {e}") from e

    if "errors" in result:
        errors = result["errors"]
        error_msg = errors[0].get("message", str(errors)) if errors else str(result)
        # Check if this is a server error we should retry
        if any("Internal Server Error" in str(e) for e in errors):
            raise RetryableError(f"GraphQL server error: {error_msg}")
        # Non-retryable GraphQL error
        raise AgriWebbAPIError(f"GraphQL error: {error_msg}")

    return result


# =============================================================================
# Normalization Helpers
# =============================================================================


def _normalize_animal(animal: dict) -> dict:
    """
    Flatten nested animal structure into simpler format for easier use.

    Converts:
        { animalId, identity: { vid, name }, characteristics: { breed, sex }, ... }
    To:
        { id, visualTag, name, breed, sex, ... }
    """
    identity = animal.get("identity") or {}
    characteristics = animal.get("characteristics") or {}
    state = animal.get("state") or {}
    parentage = animal.get("parentage") or {}
    mgmt_group = animal.get("managementGroup") or {}

    # Extract first sire/dam if present
    sires = parentage.get("sires") or []
    dams = parentage.get("dams") or []

    sire = None
    if sires:
        s = sires[0]
        sire_identity = s.get("parentAnimalIdentity") or {}
        sire = {
            "id": s.get("parentAnimalId"),
            "visualTag": sire_identity.get("vid"),
            "name": sire_identity.get("name"),
            "eid": sire_identity.get("eid"),
        }

    dam = None
    if dams:
        d = dams[0]
        dam_identity = d.get("parentAnimalIdentity") or {}
        dam = {
            "id": d.get("parentAnimalId"),
            "visualTag": dam_identity.get("vid"),
            "name": dam_identity.get("name"),
            "eid": dam_identity.get("eid"),
        }

    return {
        "id": animal.get("animalId"),
        "visualTag": identity.get("vid"),
        "eid": identity.get("eid"),
        "name": identity.get("name"),
        "managementTag": identity.get("managementTag"),
        "breed": characteristics.get("breedAssessed"),
        "species": characteristics.get("speciesCommonName"),
        "sex": characteristics.get("sex"),
        "birthDate": characteristics.get("birthDate"),
        "birthYear": characteristics.get("birthYear"),
        "color": characteristics.get("visualColor"),
        "ageClass": characteristics.get("ageClass"),
        "onFarm": state.get("onFarm"),
        "status": "onFarm" if state.get("onFarm") else state.get("fate"),
        "currentLocationId": state.get("currentLocationId"),
        "reproductiveStatus": state.get("reproductiveStatus"),
        "offspringCount": state.get("offspringCount"),
        "mob": {"id": mgmt_group.get("managementGroupId"), "name": mgmt_group.get("name")}
        if mgmt_group.get("managementGroupId")
        else None,
        "sire": sire,
        "dam": dam,
        # Keep raw data for advanced use
        "_raw": animal,
    }


# =============================================================================
# Individual Animal Queries
# =============================================================================


async def find_animal(identifier: str) -> dict:
    """
    Find an animal by any identifier (ID, EID, visual tag, or name).

    Args:
        identifier: AgriWebb ID, EID, visual tag, or name

    Returns:
        Animal record (normalized)

    Raises:
        ValueError: If no animal found or multiple matches
    """
    farm_id = settings.agriwebb_farm_id

    # Try by animalId first
    query = f"""
    {{
      animals(farmId: "{farm_id}", filter: {{ animalId: {{ _eq: "{identifier}" }} }}) {{
        animalId
        {ANIMAL_IDENTITY_FIELDS}
        {ANIMAL_CHARACTERISTICS_FIELDS}
        {ANIMAL_STATE_FIELDS}
        {PARENTAGE_FIELDS}
        {MANAGEMENT_GROUP_FIELDS}
      }}
    }}
    """
    result = await graphql(query)

    if "errors" in result:
        raise ValueError(f"GraphQL errors: {result['errors']}")

    animals = result.get("data", {}).get("animals", [])

    if animals:
        return _normalize_animal(animals[0])

    # Try by name (case-insensitive search via _ilike if supported, otherwise _eq)
    query = f"""
    {{
      animals(farmId: "{farm_id}", filter: {{ identity: {{ name: {{ _eq: "{identifier}" }} }} }}) {{
        animalId
        {ANIMAL_IDENTITY_FIELDS}
        {ANIMAL_CHARACTERISTICS_FIELDS}
        {ANIMAL_STATE_FIELDS}
        {PARENTAGE_FIELDS}
        {MANAGEMENT_GROUP_FIELDS}
      }}
    }}
    """
    result = await graphql(query)

    if "errors" in result:
        raise ValueError(f"GraphQL errors: {result['errors']}")

    animals = result.get("data", {}).get("animals", [])

    if animals:
        if len(animals) > 1:
            matches = ", ".join(
                _normalize_animal(a).get("visualTag") or _normalize_animal(a).get("name") or a["animalId"]
                for a in animals
            )
            raise ValueError(f"Multiple animals match '{identifier}': {matches}")
        return _normalize_animal(animals[0])

    # Try by vid (visual tag)
    query = f"""
    {{
      animals(farmId: "{farm_id}", filter: {{ identity: {{ vid: {{ _eq: "{identifier}" }} }} }}) {{
        animalId
        {ANIMAL_IDENTITY_FIELDS}
        {ANIMAL_CHARACTERISTICS_FIELDS}
        {ANIMAL_STATE_FIELDS}
        {PARENTAGE_FIELDS}
        {MANAGEMENT_GROUP_FIELDS}
      }}
    }}
    """
    result = await graphql(query)

    if "errors" in result:
        raise ValueError(f"GraphQL errors: {result['errors']}")

    animals = result.get("data", {}).get("animals", [])

    if animals:
        if len(animals) > 1:
            matches = ", ".join(
                _normalize_animal(a).get("visualTag") or _normalize_animal(a).get("name") or a["animalId"]
                for a in animals
            )
            raise ValueError(f"Multiple animals match '{identifier}': {matches}")
        return _normalize_animal(animals[0])

    # Try by eid
    query = f"""
    {{
      animals(farmId: "{farm_id}", filter: {{ identity: {{ eid: {{ _eq: "{identifier}" }} }} }}) {{
        animalId
        {ANIMAL_IDENTITY_FIELDS}
        {ANIMAL_CHARACTERISTICS_FIELDS}
        {ANIMAL_STATE_FIELDS}
        {PARENTAGE_FIELDS}
        {MANAGEMENT_GROUP_FIELDS}
      }}
    }}
    """
    result = await graphql(query)

    if "errors" in result:
        raise ValueError(f"GraphQL errors: {result['errors']}")

    animals = result.get("data", {}).get("animals", [])

    if animals:
        if len(animals) > 1:
            matches = ", ".join(
                _normalize_animal(a).get("visualTag") or _normalize_animal(a).get("name") or a["animalId"]
                for a in animals
            )
            raise ValueError(f"Multiple animals match '{identifier}': {matches}")
        return _normalize_animal(animals[0])

    raise ValueError(f"No animal found matching '{identifier}'")


async def resolve_animal_id(identifier: str) -> str:
    """
    Resolve any identifier to an AgriWebb animal ID.

    Args:
        identifier: AgriWebb ID, EID, visual tag, or name

    Returns:
        AgriWebb animal ID
    """
    animal = await find_animal(identifier)
    return animal["id"]


async def get_animals(
    status: str | None = None,
    species: str | None = None,
    include_lineage: bool = False,
) -> list[dict]:
    """
    Fetch all animals from the farm.

    Args:
        status: Filter by status (e.g., "onFarm")
        species: Filter by species (e.g., "CATTLE", "SHEEP")
        include_lineage: Include sire/dam info in results

    Returns:
        List of animal records (normalized)
    """
    farm_id = settings.agriwebb_farm_id

    # Build filter
    filters = []
    if status == "onFarm":
        filters.append("state: { onFarm: { _eq: true } }")
    if species:
        filters.append(f"characteristics: {{ speciesCommonName: {{ _eq: {species} }} }}")

    filter_str = f", filter: {{ {', '.join(filters)} }}" if filters else ""

    lineage_fields = PARENTAGE_FIELDS if include_lineage else ""

    query = f"""
    {{
      animals(farmId: "{farm_id}"{filter_str}) {{
        animalId
        {ANIMAL_IDENTITY_FIELDS}
        {ANIMAL_CHARACTERISTICS_FIELDS}
        {ANIMAL_STATE_FIELDS}
        {lineage_fields}
        {MANAGEMENT_GROUP_FIELDS}
      }}
    }}
    """
    result = await graphql(query)

    if "errors" in result:
        raise ValueError(f"GraphQL errors: {result['errors']}")

    animals = result.get("data", {}).get("animals", [])
    return [_normalize_animal(a) for a in animals]


async def get_animal(identifier: str) -> dict:
    """
    Fetch detailed info for a single animal.

    Args:
        identifier: AgriWebb ID, EID, visual tag, or name

    Returns:
        Animal record with full details (normalized)
    """
    return await find_animal(identifier)


async def get_animal_lineage(identifier: str, generations: int = 3) -> dict:
    """
    Fetch genetic lineage (family tree) for an animal.

    Note: AgriWebb API returns parentage with parentAnimalId references,
    so we fetch each generation separately.

    Args:
        identifier: AgriWebb ID, EID, visual tag, or name
        generations: Number of generations to fetch (default 3)

    Returns:
        Nested dict with sire/dam lineage
    """
    animal = await find_animal(identifier)

    async def fetch_parents(animal_data: dict, depth: int) -> dict:
        """Recursively fetch parent details."""
        if depth <= 0:
            return animal_data

        result = dict(animal_data)

        # Fetch sire details if we have an ID
        if result.get("sire") and result["sire"].get("id"):
            try:
                sire = await find_animal(result["sire"]["id"])
                result["sire"] = await fetch_parents(sire, depth - 1)
            except ValueError:
                pass  # Keep basic sire info

        # Fetch dam details if we have an ID
        if result.get("dam") and result["dam"].get("id"):
            try:
                dam = await find_animal(result["dam"]["id"])
                result["dam"] = await fetch_parents(dam, depth - 1)
            except ValueError:
                pass  # Keep basic dam info

        return result

    return await fetch_parents(animal, generations)


async def get_offspring(identifier: str) -> list[dict]:
    """
    Find all offspring of an animal (works for both sires and dams).

    Args:
        identifier: AgriWebb ID, EID, visual tag, or name

    Returns:
        List of offspring records (normalized)
    """
    parent = await find_animal(identifier)
    parent_id = parent["id"]
    farm_id = settings.agriwebb_farm_id

    # Query for animals where this animal is in sires or dams
    # We need to fetch all animals and filter client-side since
    # the parentage filter structure may vary
    query = f"""
    {{
      animals(farmId: "{farm_id}") {{
        animalId
        {ANIMAL_IDENTITY_FIELDS}
        {ANIMAL_CHARACTERISTICS_FIELDS}
        {ANIMAL_STATE_FIELDS}
        {PARENTAGE_FIELDS}
      }}
    }}
    """
    result = await graphql(query)

    if "errors" in result:
        raise ValueError(f"GraphQL errors: {result['errors']}")

    all_animals = result.get("data", {}).get("animals", [])

    # Filter to offspring
    offspring = []
    for a in all_animals:
        parentage = a.get("parentage") or {}
        sires = parentage.get("sires") or []
        dams = parentage.get("dams") or []

        is_offspring = any(s.get("parentAnimalId") == parent_id for s in sires) or any(
            d.get("parentAnimalId") == parent_id for d in dams
        )

        if is_offspring:
            offspring.append(_normalize_animal(a))

    return offspring


async def get_mobs() -> list[dict]:
    """
    Fetch all mobs (animal groups/herds) on the farm.

    Returns:
        List of mob records with animal counts
    """
    farm_id = settings.agriwebb_farm_id

    query = f"""
    {{
      managementGroups(farmId: "{farm_id}") {{
        id
        name
        speciesCommonName
        animalCount
        currentLocationId
      }}
    }}
    """
    result = await graphql(query)

    if "errors" in result:
        raise ValueError(f"GraphQL errors: {result['errors']}")

    groups = result.get("data", {}).get("managementGroups", [])
    return [
        {
            "id": g["id"],
            "name": g.get("name"),
            "species": g.get("speciesCommonName"),
            "animalCount": g.get("animalCount"),
            "currentLocationId": g.get("currentLocationId"),
        }
        for g in groups
    ]


async def get_weights(
    animal_id: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[dict]:
    """
    Fetch weight records.

    Args:
        animal_id: Filter to specific animal (optional)
        start_date: Filter from date (ISO format)
        end_date: Filter to date (ISO format)

    Returns:
        List of weight records
    """
    farm_id = settings.agriwebb_farm_id

    filters = []
    if animal_id:
        filters.append(f'animalId: {{ _eq: "{animal_id}" }}')

    filter_str = f", filter: {{ {', '.join(filters)} }}" if filters else ""

    query = f"""
    {{
      weightRecords(farmId: "{farm_id}"{filter_str}) {{
        id
        recordedAt
        weight
        weightUnit
        animalId
      }}
    }}
    """
    result = await graphql(query)

    if "errors" in result:
        raise ValueError(f"GraphQL errors: {result['errors']}")

    return result.get("data", {}).get("weightRecords", [])


async def get_treatments(
    animal_id: str | None = None,
    treatment_type: str | None = None,
) -> list[dict]:
    """
    Fetch health treatment records.

    Args:
        animal_id: Filter to specific animal (optional)
        treatment_type: Filter by type (optional)

    Returns:
        List of treatment records
    """
    farm_id = settings.agriwebb_farm_id

    filters = []
    if animal_id:
        filters.append(f'animalId: {{ _eq: "{animal_id}" }}')

    filter_str = f", filter: {{ {', '.join(filters)} }}" if filters else ""

    query = f"""
    {{
      treatmentRecords(farmId: "{farm_id}"{filter_str}) {{
        id
        recordedAt
        treatmentType
        productName
        dose
        doseUnit
        animalId
      }}
    }}
    """
    result = await graphql(query)

    if "errors" in result:
        raise ValueError(f"GraphQL errors: {result['errors']}")

    return result.get("data", {}).get("treatmentRecords", [])


async def get_pregnancies(animal_id: str | None = None) -> list[dict]:
    """
    Fetch pregnancy/breeding records.

    Args:
        animal_id: Filter to specific animal (optional)

    Returns:
        List of pregnancy records
    """
    farm_id = settings.agriwebb_farm_id

    filters = []
    if animal_id:
        filters.append(f'animalId: {{ _eq: "{animal_id}" }}')

    filter_str = f", filter: {{ {', '.join(filters)} }}" if filters else ""

    query = f"""
    {{
      pregnancyRecords(farmId: "{farm_id}"{filter_str}) {{
        id
        recordedAt
        pregnancyStatus
        conceptionDate
        expectedBirthDate
        animalId
        sireId
      }}
    }}
    """
    result = await graphql(query)

    if "errors" in result:
        raise ValueError(f"GraphQL errors: {result['errors']}")

    return result.get("data", {}).get("pregnancyRecords", [])


# =============================================================================
# Cache Download Functions
# =============================================================================


async def _fetch_all_animals_for_cache(page_size: int = 200) -> list[dict]:
    """
    Fetch all animals with full details using pagination.

    Args:
        page_size: Number of animals per page (default 200)

    Returns:
        List of all animal records
    """
    farm_id = settings.agriwebb_farm_id
    all_animals = []
    skip = 0

    print("Fetching animals from AgriWebb...")

    while True:
        variables = {"farmId": farm_id, "limit": page_size, "skip": skip}
        result = await _graphql_with_retry(ANIMALS_QUERY, variables)
        animals = result.get("data", {}).get("animals", [])
        all_animals.extend(animals)

        if len(animals) < page_size:
            # Last page - no more animals
            break

        skip += page_size
        print(f"  Fetched {len(all_animals)} animals so far...")
        await asyncio.sleep(PAGINATION_DELAY)

    print(f"  Found {len(all_animals)} animals total")
    return all_animals


async def _fetch_animal_records(animal_id: str, page_size: int = 100) -> list[dict]:
    """Fetch all records for a specific animal with pagination.

    Record types in AgriWebb API (from schema introspection):
    - WeighRecord: weight measurements
    - ScoreRecord: body condition scores
    - LocationChangedRecord: paddock movements
    - PregnancyScanRecord: pregnancy scans
    - AnimalTreatmentRecord: health treatments
    - FeedRecord: feed records

    Args:
        animal_id: The animal ID to fetch records for
        page_size: Number of records per page (default 100)

    Returns:
        List of all records for the animal
    """
    farm_id = settings.agriwebb_farm_id
    all_records = []
    skip = 0

    while True:
        variables = {
            "farmId": farm_id,
            "animalId": animal_id,
            "limit": page_size,
            "skip": skip,
        }
        result = await _graphql_with_retry(RECORDS_QUERY_FULL, variables)
        records = result.get("data", {}).get("records", [])
        all_records.extend(records)

        if len(records) < page_size:
            # Last page
            break

        skip += page_size
        await asyncio.sleep(PAGINATION_DELAY)

    return all_records


async def _fetch_fields_for_cache() -> list[dict]:
    """Fetch all fields/paddocks for location name mapping."""
    farm_id = settings.agriwebb_farm_id
    variables = {"farmId": farm_id}

    print("Fetching fields/paddocks...")
    try:
        result = await _graphql_with_retry(CACHE_FIELDS_QUERY, variables)
    except Exception as e:
        print(f"  Warning: Could not fetch fields: {e}")
        return []

    fields = result.get("data", {}).get("fields", [])
    print(f"  Found {len(fields)} fields/paddocks")
    return fields


async def cache_all_animals(output_path: Path) -> dict:
    """Download all animal data to a local cache file.

    This function fetches all animals with their full details and records,
    then saves them to a JSON file for fast local analysis.

    Args:
        output_path: Path to write the cache file

    Returns:
        The cached data dict
    """
    print("=" * 60)
    print("AgriWebb Animal Data Cache")
    print("=" * 60)
    print()

    # Fetch animals (without embedded records - we'll fetch those separately)
    animals = await _fetch_all_animals_for_cache()

    # Fetch fields for location name mapping
    fields = await _fetch_fields_for_cache()
    field_names = {f["id"]: f["name"] for f in fields}

    # Extract management groups from animals (since direct query may lack permissions)
    groups_by_id = {}
    for a in animals:
        mg = a.get("managementGroup")
        if mg and mg.get("managementGroupId"):
            groups_by_id[mg["managementGroupId"]] = mg
    groups = list(groups_by_id.values())
    print(f"  Extracted {len(groups)} management groups from animals")

    # Fetch complete records for each animal with concurrency control
    print()
    print(f"Fetching records for {len(animals)} animals ({MAX_CONCURRENT_REQUESTS} concurrent)...")

    semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
    circuit_breaker = CircuitBreaker()
    progress = {"completed": 0, "records": 0, "errors": 0, "skipped": 0}
    progress_lock = asyncio.Lock()

    async def fetch_with_semaphore(animal: dict) -> None:
        """Fetch records for a single animal with semaphore control."""
        # Check circuit breaker before attempting
        try:
            await circuit_breaker.check()
        except CircuitBreakerOpen:
            animal["records"] = []
            async with progress_lock:
                progress["skipped"] += 1
            return

        async with semaphore:
            animal_id = animal["animalId"]
            try:
                records = await _fetch_animal_records(animal_id)
                animal["records"] = records
                await circuit_breaker.record_success()
                async with progress_lock:
                    progress["completed"] += 1
                    progress["records"] += len(records)
            except RuntimeError as e:
                animal["records"] = []
                await circuit_breaker.record_failure()
                async with progress_lock:
                    progress["completed"] += 1
                    progress["errors"] += 1
                # Only print if circuit breaker hasn't tripped yet
                if not circuit_breaker.is_open:
                    print(f"  Warning: {e}")

            # Progress update every 25 animals
            async with progress_lock:
                if progress["completed"] % 25 == 0 or progress["completed"] == len(animals):
                    print(f"  Progress: {progress['completed']}/{len(animals)} animals, {progress['records']} records")

    # Run all fetches concurrently with semaphore limiting
    # Circuit breaker may have stopped some tasks - suppress any exceptions
    with contextlib.suppress(Exception):
        await asyncio.gather(*[fetch_with_semaphore(animal) for animal in animals])

    # Check if we stopped due to circuit breaker
    if circuit_breaker.is_open:
        print("\n  Stopped early due to API errors. Saving partial data...")
        print(f"  Skipped {progress['skipped']} animals due to circuit breaker")

    total_records = progress["records"]
    fallback_count = progress["errors"]

    print(f"  Total records fetched: {total_records}")
    if fallback_count > 0:
        print(f"  Warning: {fallback_count} animals had issues fetching records")

    # Build the export
    data = {
        "exported_at": datetime.now().isoformat(),
        "farm_id": settings.agriwebb_farm_id,
        "summary": {
            "total_animals": len(animals),
            "total_records": total_records,
            "management_groups": len(groups),
            "fields": len(fields),
        },
        "animals": animals,
        "management_groups": groups,
        "fields": fields,
        "field_names": field_names,
    }

    # Add some computed indices for easier lookup
    data["indices"] = {
        "by_id": {a["animalId"]: i for i, a in enumerate(animals)},
        "by_name": {},
        "by_vid": {},
        "by_eid": {},
    }

    for i, a in enumerate(animals):
        identity = a.get("identity") or {}
        if identity.get("name"):
            data["indices"]["by_name"][identity["name"].lower()] = i
        if identity.get("vid"):
            data["indices"]["by_vid"][identity["vid"].lower()] = i
        if identity.get("eid"):
            data["indices"]["by_eid"][identity["eid"]] = i

    # Write to file
    print()
    print(f"Writing to {output_path}...")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(data, f, indent=2, default=str)

    size_mb = output_path.stat().st_size / (1024 * 1024)
    print(f"  Wrote {size_mb:.2f} MB")
    print()
    print("Done! You can now analyze the data locally.")
    print(f"  File: {output_path}")

    return data


# =============================================================================
# Analysis Helpers
# =============================================================================


def format_lineage_tree(animal: dict, indent: int = 0) -> str:
    """
    Format a lineage dict as a readable tree string.

    Args:
        animal: Animal dict with nested sire/dam
        indent: Current indentation level

    Returns:
        Formatted tree string
    """
    prefix = "  " * indent
    tag = animal.get("visualTag") or animal.get("name") or animal.get("id", "?")
    breed = animal.get("breed", "")
    birth = str(animal.get("birthYear", "")) or (
        str(animal.get("birthDate", ""))[:4] if animal.get("birthDate") else ""
    )

    line = f"{prefix}{tag}"
    if breed:
        line += f" ({breed})"
    if birth:
        line += f" [{birth}]"

    lines = [line]

    sire = animal.get("sire")
    dam = animal.get("dam")

    if sire:
        lines.append(f"{prefix}  ├─ Sire:")
        lines.append(format_lineage_tree(sire, indent + 2))
    if dam:
        lines.append(f"{prefix}  └─ Dam:")
        lines.append(format_lineage_tree(dam, indent + 2))

    return "\n".join(lines)


def summarize_animals(animals: list[dict]) -> dict:
    """
    Generate summary statistics for a list of animals.

    Args:
        animals: List of animal records

    Returns:
        Summary dict with counts by species, breed, sex, status
    """
    summary = {
        "total": len(animals),
        "by_species": {},
        "by_breed": {},
        "by_sex": {},
        "by_status": {},
    }

    for animal in animals:
        species = animal.get("species") or "unknown"
        breed = animal.get("breed") or "unknown"
        sex = animal.get("sex") or "unknown"
        status = animal.get("status") or "unknown"

        summary["by_species"][species] = summary["by_species"].get(species, 0) + 1
        summary["by_breed"][breed] = summary["by_breed"].get(breed, 0) + 1
        summary["by_sex"][sex] = summary["by_sex"].get(sex, 0) + 1
        summary["by_status"][status] = summary["by_status"].get(status, 0) + 1

    return summary


# =============================================================================
# CLI
# =============================================================================


async def cli_main() -> None:
    """CLI entry point for livestock data."""
    import argparse
    from datetime import timedelta

    parser = argparse.ArgumentParser(description="Livestock data from AgriWebb")
    subparsers = parser.add_subparsers(dest="command", help="Command")

    # list command
    list_parser = subparsers.add_parser("list", help="List all animals")
    list_parser.add_argument("--status", help="Filter by status")
    list_parser.add_argument("--species", help="Filter by species")
    list_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # get command
    get_parser = subparsers.add_parser("get", help="Get single animal details")
    get_parser.add_argument("id", help="Animal ID, EID, visual tag, or name")
    get_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # lineage command
    lineage_parser = subparsers.add_parser("lineage", help="Show animal lineage")
    lineage_parser.add_argument("id", help="Animal ID, EID, visual tag, or name")
    lineage_parser.add_argument("--generations", type=int, default=3, help="Generations to fetch")
    lineage_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # offspring command
    offspring_parser = subparsers.add_parser("offspring", help="List offspring")
    offspring_parser.add_argument("id", help="Animal ID, EID, visual tag, or name")
    offspring_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # mobs command
    subparsers.add_parser("mobs", help="List all mobs/management groups")

    # summary command
    subparsers.add_parser("summary", help="Show herd summary")

    # cache command - download all data to local JSON
    cache_parser = subparsers.add_parser("cache", help="Download all animal data to local cache")
    cache_parser.add_argument("--output", "-o", type=str, help="Output file path")
    cache_parser.add_argument("--refresh", action="store_true", help="Force full re-fetch, ignoring cache age")

    args = parser.parse_args()

    if args.command == "list":
        animals = await get_animals(status=args.status, species=args.species)
        if args.json:
            # Remove _raw for cleaner output
            for a in animals:
                a.pop("_raw", None)
            print(json.dumps(animals, indent=2, default=str))
        else:
            for a in animals:
                tag = a.get("visualTag") or a.get("name") or a["id"][:8]
                breed = a.get("breed") or ""
                status = a.get("status") or ""
                print(f"{tag:<15} {breed:<20} {status}")

    elif args.command == "get":
        animal = await get_animal(args.id)
        if args.json:
            animal.pop("_raw", None)
            print(json.dumps(animal, indent=2, default=str))
        else:
            print(f"ID: {animal.get('id')}")
            print(f"Tag: {animal.get('visualTag')}")
            print(f"Name: {animal.get('name')}")
            print(f"EID: {animal.get('eid')}")
            print(f"Breed: {animal.get('breed')}")
            print(f"Species: {animal.get('species')}")
            print(f"Sex: {animal.get('sex')}")
            print(f"Birth Year: {animal.get('birthYear')}")
            print(f"Status: {animal.get('status')}")
            print(f"On Farm: {animal.get('onFarm')}")
            if animal.get("sire"):
                sire = animal["sire"]
                print(f"Sire: {sire.get('visualTag') or sire.get('name') or sire.get('id')}")
            if animal.get("dam"):
                dam = animal["dam"]
                print(f"Dam: {dam.get('visualTag') or dam.get('name') or dam.get('id')}")
            if animal.get("mob"):
                print(f"Mob: {animal['mob'].get('name')}")

    elif args.command == "lineage":
        animal = await get_animal_lineage(args.id, generations=args.generations)
        if args.json:
            # Remove _raw recursively
            def remove_raw(obj):
                if isinstance(obj, dict):
                    obj.pop("_raw", None)
                    for v in obj.values():
                        remove_raw(v)
                elif isinstance(obj, list):
                    for item in obj:
                        remove_raw(item)

            remove_raw(animal)
            print(json.dumps(animal, indent=2, default=str))
        else:
            print(format_lineage_tree(animal))

    elif args.command == "offspring":
        offspring = await get_offspring(args.id)
        if args.json:
            for o in offspring:
                o.pop("_raw", None)
            print(json.dumps(offspring, indent=2, default=str))
        else:
            print(f"Found {len(offspring)} offspring:")
            for o in offspring:
                tag = o.get("visualTag") or o.get("name") or o["id"][:8]
                sex = o.get("sex") or "?"
                birth = o.get("birthYear") or ""
                print(f"  {tag} ({sex}) {birth}")

    elif args.command == "mobs":
        mobs = await get_mobs()
        for m in mobs:
            print(f"{m['name'] or 'Unnamed':<20} {m.get('animalCount') or 0:>4} animals")

    elif args.command == "summary":
        animals = await get_animals()
        summary = summarize_animals(animals)
        print(f"Total animals: {summary['total']}\n")
        print("By species:")
        for k, v in summary["by_species"].items():
            print(f"  {k}: {v}")
        print("\nBy breed:")
        for k, v in summary["by_breed"].items():
            print(f"  {k}: {v}")
        print("\nBy status:")
        for k, v in summary["by_status"].items():
            print(f"  {k}: {v}")

    elif args.command == "cache":
        if args.output:
            output_path = Path(args.output)
        else:
            output_path = get_cache_dir() / DEFAULT_CACHE_FILE

        refresh = getattr(args, "refresh", False)

        # Smart caching: skip if cache is fresh (unless --refresh)
        if not refresh and output_path.exists():
            mtime = datetime.fromtimestamp(output_path.stat().st_mtime)
            age = datetime.now() - mtime
            if age < timedelta(hours=CACHE_FRESHNESS_HOURS):
                hours_old = int(age.total_seconds() / 3600)
                print(f"Cache is fresh ({hours_old} hours old, threshold: {CACHE_FRESHNESS_HOURS}h)")
                print("Use --refresh to force re-download")
                print(f"File: {output_path}")
                return

        await cache_all_animals(output_path)

    else:
        parser.print_help()


def cli() -> None:
    """Sync CLI entry point."""
    asyncio.run(cli_main())


if __name__ == "__main__":
    cli()
