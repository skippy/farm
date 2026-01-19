"""
Sync all animal data from AgriWebb to a local JSON file.

Downloads animals, their lineage, records, and management groups
for fast local analysis.

Usage:
    uv run agriwebb-sync                    # Syncs to .cache/animals.json
    uv run agriwebb-sync -o custom.json     # Custom output path
"""

import argparse
import asyncio
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

DEFAULT_CACHE_FILE = "animals.json"

# Concurrency control: max parallel API requests
MAX_CONCURRENT_REQUESTS = 5

# Small delay between paginated requests (seconds)
PAGINATION_DELAY = 0.1

# Retry configuration
MAX_RETRIES = 5
MIN_WAIT_SECONDS = 1
MAX_WAIT_SECONDS = 30


class AgriWebbAPIError(Exception):
    """Raised when AgriWebb API returns an error."""

    pass


@retry(
    retry=retry_if_exception_type((httpx.HTTPStatusError, AgriWebbAPIError)),
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential_jitter(initial=MIN_WAIT_SECONDS, max=MAX_WAIT_SECONDS),
    reraise=True,
)
async def _graphql_with_retry(query: str) -> dict:
    """Execute a GraphQL query with exponential backoff retry on server errors."""
    result = await graphql(query)

    if "errors" in result:
        errors = result["errors"]
        error_msg = errors[0].get("message", str(errors)) if errors else str(result)
        # Check if this is a server error we should retry
        if any("Internal Server Error" in str(e) for e in errors):
            raise AgriWebbAPIError(f"GraphQL server error: {error_msg}")
        # Non-retryable error
        raise ValueError(f"GraphQL error: {error_msg}")

    return result


async def fetch_all_animals(page_size: int = 200) -> list[dict]:
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
        query = f"""
        {{
          animals(farmId: "{farm_id}", limit: {page_size}, skip: {skip}) {{
            animalId
            farmId
            identity {{
              name
              eid
              vid
              managementTag
              brand
              tattoo
            }}
            characteristics {{
              birthDate
              birthYear
              birthDateAccuracy
              breedAssessed
              sex
              speciesCommonName
              visualColor
              ageClass
            }}
            state {{
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
            }}
            parentage {{
              sires {{
                parentAnimalId
                parentAnimalIdentity {{ name vid eid }}
                parentType
              }}
              dams {{
                parentAnimalId
                parentAnimalIdentity {{ name vid eid }}
                parentType
              }}
              surrogate {{
                parentAnimalId
                parentAnimalIdentity {{ name vid eid }}
                parentType
              }}
            }}
            managementGroupId
            managementGroup {{
              managementGroupId
              name
              species
            }}
          }}
        }}
        """

        result = await _graphql_with_retry(query)
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


def _build_records_query(farm_id: str, animal_id: str, include_complex: bool = True) -> str:
    """Build the records query with optional complex fragments.

    Args:
        farm_id: The farm ID
        animal_id: The animal ID
        include_complex: If True, include AnimalTreatmentRecord and FeedRecord fragments.
                        These have nested arrays that can cause 500 errors on some animals.
    """
    complex_fragments = ""
    if include_complex:
        complex_fragments = """
        ... on AnimalTreatmentRecord {
          treatments {
            healthProduct
            reasonForTreatment
            totalApplied { value unit }
          }
        }
        ... on FeedRecord {
          feeds {
            feedType
            amount { value unit }
          }
        }
        """

    return f"""
    {{
      records(options: {{
        farmId: "{farm_id}"
        animalId: "{animal_id}"
        limit: 10000
      }}) {{
        recordId
        recordType
        observationDate
        ... on WeighRecord {{
          weight {{ value unit }}
          weighEvent
        }}
        ... on ScoreRecord {{
          score {{ value }}
        }}
        ... on LocationChangedRecord {{
          locationId
        }}
        ... on PregnancyScanRecord {{
          fetusCount
          fetalAge
        }}
        {complex_fragments}
      }}
    }}
    """


async def fetch_animal_records(animal_id: str) -> list[dict]:
    """Fetch all records for a specific animal with automatic fallback.

    Record types in AgriWebb API (from schema introspection):
    - WeighRecord: weight measurements
    - ScoreRecord: body condition scores
    - LocationChangedRecord: paddock movements
    - PregnancyScanRecord: pregnancy scans
    - AnimalTreatmentRecord: health treatments
    - FeedRecord: feed records

    The function first tries to fetch all record types. If AgriWebb's API
    returns persistent 500 errors (which happens with complex nested fragments
    for some animals), it falls back to a simpler query without treatment
    and feed record details.
    """
    farm_id = settings.agriwebb_farm_id

    # Try full query first
    try:
        query = _build_records_query(farm_id, animal_id, include_complex=True)
        result = await _graphql_with_retry(query)
        return result.get("data", {}).get("records", [])
    except (AgriWebbAPIError, httpx.HTTPStatusError):
        # Fall back to simpler query without complex nested fragments
        pass

    # Fallback: query without complex fragments
    try:
        query = _build_records_query(farm_id, animal_id, include_complex=False)
        result = await _graphql_with_retry(query)
        return result.get("data", {}).get("records", [])
    except (AgriWebbAPIError, httpx.HTTPStatusError) as e:
        raise RuntimeError(f"Failed to fetch records for animal {animal_id} after fallback: {e}") from e


async def fetch_fields() -> list[dict]:
    """Fetch all fields/paddocks for location name mapping."""
    farm_id = settings.agriwebb_farm_id

    query = f"""
    {{
      fields(filter: {{ farmId: {{ _eq: "{farm_id}" }} }}) {{
        id
        name
        totalArea
        grazableArea
        landUse
      }}
    }}
    """

    print("Fetching fields/paddocks...")
    try:
        result = await _graphql_with_retry(query)
    except Exception as e:
        print(f"  Warning: Could not fetch fields: {e}")
        return []

    fields = result.get("data", {}).get("fields", [])
    print(f"  Found {len(fields)} fields/paddocks")
    return fields


async def sync_all(output_path: Path) -> dict:
    """Sync all animal data to a local file."""
    print("=" * 60)
    print("AgriWebb Animal Data Sync")
    print("=" * 60)
    print()

    # Fetch animals (without embedded records - we'll fetch those separately)
    animals = await fetch_all_animals()

    # Fetch fields for location name mapping
    fields = await fetch_fields()
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
    progress = {"completed": 0, "records": 0, "errors": 0}
    progress_lock = asyncio.Lock()

    async def fetch_with_semaphore(animal: dict) -> None:
        """Fetch records for a single animal with semaphore control."""
        async with semaphore:
            animal_id = animal["animalId"]
            try:
                records = await fetch_animal_records(animal_id)
                animal["records"] = records
                async with progress_lock:
                    progress["completed"] += 1
                    progress["records"] += len(records)
            except RuntimeError as e:
                animal["records"] = []
                async with progress_lock:
                    progress["completed"] += 1
                    progress["errors"] += 1
                print(f"  Warning: {e}")

            # Progress update every 25 animals
            async with progress_lock:
                if progress["completed"] % 25 == 0 or progress["completed"] == len(animals):
                    print(f"  Progress: {progress['completed']}/{len(animals)} animals, {progress['records']} records")

    # Run all fetches concurrently with semaphore limiting
    await asyncio.gather(*[fetch_with_semaphore(animal) for animal in animals])

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


def cli():
    default_path = get_cache_dir() / DEFAULT_CACHE_FILE

    parser = argparse.ArgumentParser(
        description="Sync all animal data from AgriWebb to a local JSON file"
    )
    parser.add_argument(
        "--output", "-o",
        type=Path,
        default=default_path,
        help=f"Output file path (default: {default_path})",
    )
    args = parser.parse_args()

    asyncio.run(sync_all(args.output))


if __name__ == "__main__":
    cli()
