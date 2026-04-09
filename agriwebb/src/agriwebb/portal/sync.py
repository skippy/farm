"""Sync portal-only data into the local cache."""

import json
from datetime import UTC, datetime
from pathlib import Path

from agriwebb.core.config import get_cache_dir


async def sync_portal_data():
    """Fetch portal-only records and save to cache.

    Fetches: death-record, note-record, natural-service-record, ai-record.
    Saves each to .cache/portal/<type>.json with a synced_at timestamp.

    If not full_refresh, only fetches records with observationDate after last sync.
    (Note: observationDate is "when it happened" not "when entered" -- this is
    imperfect but catches most new records. Full refresh catches everything.)
    """
    from agriwebb.portal.client import PortalClient

    cache_dir = get_cache_dir() / "portal"
    cache_dir.mkdir(exist_ok=True)

    async with PortalClient() as client:
        now = datetime.now(UTC).isoformat()

        # Types to sync -- the ones the public API doesn't expose
        types_to_sync = [
            "death-record",
            "note-record",
            "natural-service-record",
            "ai-record",
        ]

        for record_type in types_to_sync:
            cache_file = cache_dir / f"{record_type}.json"

            # Fetch all records (pagination if needed)
            records, total = await client.search_with_count(record_type, limit=1000)

            print(f"  {record_type}: {total} records")

            # Save
            with open(cache_file, "w") as f:
                json.dump(
                    {
                        "synced_at": now,
                        "record_type": record_type,
                        "count": total,
                        "records": records,
                    },
                    f,
                    indent=2,
                )

        # Also update the natural_service.json in the format the loader expects
        # (convert from event-sourcing format to our cached format)
        await _sync_natural_service(client, cache_dir)

        print(f"\nPortal sync complete at {now}")


async def _sync_natural_service(client, cache_dir: Path):
    """Convert natural-service-record format to the loader's expected format."""
    records = await client.search("natural-service-record", limit=100)

    # The event-sourcing record has:
    # - animalDictionary: {animalId: "Male"/"Female"}
    # - startDate, observationDate (= end date)
    # - animalIds

    groups = []
    for rec in records:
        animal_dict = rec.get("animalDictionary", {})

        # Find the ram (Male) and ewes (Female)
        ram_ids = [aid for aid, sex in animal_dict.items() if sex == "Male"]
        ewe_ids = [aid for aid, sex in animal_dict.items() if sex == "Female"]

        if not ram_ids:
            continue

        start_ms = rec.get("startDate") or rec.get("observationDate")
        end_ms = rec.get("observationDate")

        groups.append(
            {
                "record_id": rec.get("recordId"),
                "ram_ids": ram_ids,
                "ewe_ids": ewe_ids,
                "ewe_count": len(ewe_ids),
                "start_date_ms": start_ms,
                "end_date_ms": end_ms,
                "observation_date_ms": rec.get("observationDate"),
            }
        )

    now = datetime.now(UTC).isoformat()

    # Save in the portal cache (detailed format)
    cache_file = get_cache_dir() / "portal" / "natural-service-parsed.json"
    with open(cache_file, "w") as f:
        json.dump({"synced_at": now, "count": len(groups), "groups": groups}, f, indent=2)

    # Also write to .cache/natural_service.json in the format the loader expects
    loader_groups = []
    for g in groups:
        loader_groups.append({
            "sire_name": None,  # Would need animal lookup to resolve; left for enrichment
            "start_date": None,
            "end_date": None,
            "ewe_count": g["ewe_count"],
            "ewes": [{"animalId": eid} for eid in g["ewe_ids"]],
            "ram_ids": g["ram_ids"],
            "start_date_ms": g.get("start_date_ms"),
            "end_date_ms": g.get("end_date_ms"),
        })

    loader_file = get_cache_dir() / "natural_service.json"
    with open(loader_file, "w") as f:
        json.dump({
            "source": "portal event-sourcing API",
            "scraped_at": now,
            "groups": loader_groups,
        }, f, indent=2)

    print(f"  natural-service (parsed): {len(groups)} groups")
