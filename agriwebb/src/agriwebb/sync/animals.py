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

from agriwebb.core import get_cache_dir, graphql, settings

DEFAULT_CACHE_FILE = "animals.json"


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

        result = await graphql(query)

        if "errors" in result:
            raise ValueError(f"GraphQL errors: {result['errors']}")

        animals = result.get("data", {}).get("animals", [])
        all_animals.extend(animals)

        if len(animals) < page_size:
            # Last page - no more animals
            break

        skip += page_size
        print(f"  Fetched {len(all_animals)} animals so far...")

    print(f"  Found {len(all_animals)} animals total")
    return all_animals


async def fetch_animal_records(animal_id: str, max_retries: int = 3) -> list[dict]:
    """Fetch all records for a specific animal.

    Record types supported by AgriWebb API:
    - weigh: Weight measurements
    - score: Body condition scores
    - locationChanged: Paddock movements
    - animalTreatment: Health treatments
    - feed: Feed records
    """
    farm_id = settings.agriwebb_farm_id

    query = f"""
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
        }}
        ... on ScoreRecord {{
          score {{ value }}
        }}
        ... on LocationChangedRecord {{
          locationId
        }}
        ... on AnimalTreatmentRecord {{
          treatments {{
            healthProduct
            reasonForTreatment
            totalApplied {{ value unit }}
          }}
        }}
        ... on FeedRecord {{
          sessionId
        }}
      }}
    }}
    """

    for attempt in range(max_retries):
        try:
            result = await graphql(query)

            if "errors" in result:
                errors = result["errors"]
                error_msg = errors[0].get("message", str(errors)) if errors else str(result)
                raise ValueError(f"GraphQL error for animal {animal_id}: {error_msg}")

            return result.get("data", {}).get("records", [])
        except httpx.HTTPStatusError as e:
            if attempt < max_retries - 1:
                await asyncio.sleep(2 ** attempt)
            else:
                raise RuntimeError(f"HTTP error fetching records for {animal_id}: {e}") from e

    return []


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
    result = await graphql(query)

    if "errors" in result:
        print(f"  Warning: Could not fetch fields: {result['errors']}")
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

    # Fetch complete records for each animal
    print()
    print("Fetching complete records for each animal...")
    total_records = 0
    for i, animal in enumerate(animals):
        animal_id = animal["animalId"]
        records = await fetch_animal_records(animal_id)
        animal["records"] = records
        total_records += len(records)

        # Progress update every 25 animals
        if (i + 1) % 25 == 0 or (i + 1) == len(animals):
            print(f"  Progress: {i + 1}/{len(animals)} animals, {total_records} records")

    print(f"  Total records fetched: {total_records}")

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
