"""AgriWebb MCP server for livestock analysis.

Exposes lambing and livestock data-access tools that an AI agent can use
for herd analysis, breeding decisions, and loss reporting.

Run via:
    python -m agriwebb.mcp_server
    agriwebb-mcp

Register with Claude Code:
    claude mcp add agriwebb -- uv run --project agriwebb python -m agriwebb.mcp_server
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from mcp.server.fastmcp import FastMCP

server = FastMCP("agriwebb")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


_STALE_HOURS = 24


def _load():
    """Lazy-import and return the loader module."""
    from agriwebb.analysis.lambing import loader

    return loader


def _cache_age_hours() -> float | None:
    """Return the age of animals.json in hours, or None if missing."""
    from agriwebb.core.config import get_cache_dir

    path = get_cache_dir() / "animals.json"
    if not path.exists():
        return None
    mtime = path.stat().st_mtime
    age_seconds = datetime.now(UTC).timestamp() - mtime
    return age_seconds / 3600


def _staleness_warning() -> str | None:
    """Return a warning string if the cache is stale, or None if fresh."""
    hours = _cache_age_hours()
    if hours is None:
        return "WARNING: No animal cache found. Run: agriwebb-livestock cache"
    if hours > _STALE_HOURS:
        days = hours / 24
        if days >= 2:
            return f"NOTE: Animal data is {days:.0f} days old. Consider running: agriwebb-livestock cache --refresh"
        return f"NOTE: Animal data is {hours:.0f} hours old. Consider running: agriwebb-livestock cache --refresh"
    return None


def _portal_cache_age_hours(record_type: str) -> float | None:
    """Return the age of a portal cache file in hours, or None if missing."""
    from agriwebb.core.config import get_cache_dir

    path = get_cache_dir() / "portal" / f"{record_type}.json"
    if not path.exists():
        return None
    mtime = path.stat().st_mtime
    return (datetime.now(UTC).timestamp() - mtime) / 3600


def _portal_staleness_warning() -> str | None:
    """Return a warning if portal data is stale or missing."""
    age = _portal_cache_age_hours("note-record")
    if age is None:
        return "NOTE: No portal data cached. Ask the agent to sync portal data via Playwright."
    if age > _STALE_HOURS:
        days = age / 24
        if days >= 2:
            return f"NOTE: Portal data is {days:.0f} days old. Ask the agent to refresh portal data."
        return f"NOTE: Portal data is {age:.0f} hours old. Ask the agent to refresh portal data."
    return None


def _add_warnings(result: dict) -> dict:
    """Add staleness warnings to a result dict if applicable."""
    warnings = []
    w = _staleness_warning()
    if w:
        warnings.append(w)
    w = _portal_staleness_warning()
    if w:
        warnings.append(w)
    if warnings:
        result["_warnings"] = warnings
    return result


def _farm_data(season: int | None = None):
    """Load farm data, defaulting season to current year."""
    return _load().load_farm_data(season=season)


def _load_portal_cache(record_type: str) -> list[dict]:
    """Load portal-only records from .cache/portal/<type>.json if available."""
    from agriwebb.core.config import get_cache_dir

    path = get_cache_dir() / "portal" / f"{record_type}.json"
    if not path.exists():
        return []

    with open(path) as f:
        raw = json.load(f)
    # Handle double-JSON-encoded files (string wrapping)
    if isinstance(raw, str):
        raw = json.loads(raw)
    return raw.get("records", [])


def _find_portal_records_for_animal(animal_id: str, record_type: str) -> list[dict]:
    """Find portal records that reference a specific animal."""
    records = _load_portal_cache(record_type)
    return [r for r in records if animal_id in (r.get("animalIds") or [])]


def _find_animal_in_cache(identifier: str, animals: list[dict], by_id: dict[str, dict]) -> dict | None:
    """Find an animal by name, VID, EID, or animalId (case-insensitive where appropriate)."""
    # Exact match on animalId
    if identifier in by_id:
        return by_id[identifier]

    needle = identifier.strip().lower()

    for a in animals:
        identity = a.get("identity") or {}
        # Match by name
        if (identity.get("name") or "").lower() == needle:
            return a
        # Match by VID
        if (identity.get("vid") or "").lower() == needle:
            return a
        # Match by EID
        if (identity.get("eid") or "").lower() == needle:
            return a

    return None


def _animal_summary(animal: dict, loader) -> dict:
    """Build a concise summary dict for one animal."""
    birth_year = loader.get_birth_year(animal)
    current_year = datetime.now(UTC).year
    age = current_year - birth_year if birth_year else None

    return {
        "animalId": animal.get("animalId"),
        "name": loader.get_name(animal),
        "breed": loader.get_breed(animal),
        "sex": loader.get_sex(animal),
        "ageClass": loader.get_age_class(animal),
        "birthYear": birth_year,
        "age": age,
        "onFarm": loader.is_on_farm(animal),
        "fate": (animal.get("state") or {}).get("fate"),
        "sire": loader.get_sire_name(animal),
        "dam": loader.get_dam_name(animal),
    }


def _lamb_summary(animal: dict, loader, loss_records: list[dict] | None = None) -> dict:
    """Build a summary dict for a lamb including outcome."""
    summary = _animal_summary(animal, loader)
    fate = (animal.get("state") or {}).get("fate")
    if fate == "Alive" or fate == "Sold":
        summary["outcome"] = "raised" if fate == "Sold" else "alive"
    elif fate == "Dead":
        summary["outcome"] = "loss"
        summary["lossCategory"] = loader.classify_loss(animal, loss_records)
    else:
        summary["outcome"] = fate
    return summary


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------


@server.tool()
async def get_animal(identifier: str) -> str:
    """Look up an animal by name, VID, EID, or animalId.

    Returns name, breed, sex, age, status, parentage, and management info.
    """
    loader = _load()
    data = _farm_data()
    animal = _find_animal_in_cache(identifier, data.animals, data.by_id)
    if not animal:
        return json.dumps({"error": f"No animal found matching '{identifier}'"})

    result = _animal_summary(animal, loader)
    # Add management group if present
    mgmt = animal.get("managementGroup")
    if mgmt:
        result["managementGroup"] = mgmt.get("name")

    # Enrich with portal data if available
    aid = animal["animalId"]
    notes = _find_portal_records_for_animal(aid, "note-record")
    if notes:
        result["notes"] = [
            {"date": n.get("observationDate"), "note": n.get("note")}
            for n in sorted(notes, key=lambda x: x.get("observationDate") or 0, reverse=True)
        ]
    death_recs = _find_portal_records_for_animal(aid, "death-record")
    if death_recs:
        d = death_recs[0]
        fate = d.get("fate") or {}
        result["deathDetail"] = {
            "reason": fate.get("fateReason"),
            "details": fate.get("fateDetails"),
            "disposalMethod": fate.get("disposalMethod"),
        }

    _add_warnings(result)
    return json.dumps(result, indent=2)


@server.tool()
async def get_offspring(parent: str, year: int | None = None) -> str:
    """Get all offspring of an animal (by name/ID). Optionally filter by birth year."""
    loader = _load()
    data = _farm_data()
    animal = _find_animal_in_cache(parent, data.animals, data.by_id)
    if not animal:
        return json.dumps({"error": f"No animal found matching '{parent}'"})

    parent_id = animal["animalId"]
    offspring = loader.get_offspring(parent_id, data.animals)

    if year is not None:
        offspring = [a for a in offspring if loader.get_birth_year(a) == year]

    results = [_animal_summary(a, loader) for a in offspring]
    return json.dumps(
        {
            "parent": loader.get_name(animal),
            "count": len(results),
            "offspring": results,
        },
        indent=2,
    )


@server.tool()
async def get_ancestors(animal: str, max_depth: int = 4) -> str:
    """Get ancestor names for inbreeding analysis.

    Returns ancestor IDs and names up to the specified generation depth.
    """
    loader = _load()
    data = _farm_data()
    found = _find_animal_in_cache(animal, data.animals, data.by_id)
    if not found:
        return json.dumps({"error": f"No animal found matching '{animal}'"})

    ancestor_ids = loader.get_ancestors(found["animalId"], data.by_id, max_depth=max_depth)

    # Separate UUIDs from name strings and resolve names where possible
    names = []
    for aid in ancestor_ids:
        if aid in data.by_id:
            names.append(loader.get_name(data.by_id[aid]))
        elif not any(c == "-" for c in aid) or len(aid) < 20:
            # Likely a name (uppercase), not a UUID
            names.append(aid)

    return json.dumps(
        {
            "animal": loader.get_name(found),
            "maxDepth": max_depth,
            "ancestorCount": len(names),
            "ancestors": sorted(set(names)),
        },
        indent=2,
    )


@server.tool()
async def get_litter(dam: str, year: int) -> str:
    """Get all lambs from one ewe in one year, with outcomes.

    Outcomes: alive, raised (sold/harvested = success), loss.
    """
    loader = _load()
    data = _farm_data(season=year)
    found = _find_animal_in_cache(dam, data.animals, data.by_id)
    if not found:
        return json.dumps({"error": f"No animal found matching '{dam}'"})

    dam_id = found["animalId"]
    lambs = loader.get_litter(dam_id, year, data.animals)
    results = [_lamb_summary(a, loader, data.loss_records) for a in lambs]

    return json.dumps(
        {
            "dam": loader.get_name(found),
            "year": year,
            "litterSize": len(results),
            "lambs": results,
        },
        indent=2,
    )


@server.tool()
async def get_lambing_season(year: int | None = None) -> str:
    """Season dashboard: live lambs, sex breakdown, lambing rate, litter distribution.

    Lambing rate uses live lambs only (per farm convention).
    'Born' = live lambs (fate=Alive). Sold = successfully raised, not a loss.
    """
    from agriwebb.analysis.lambing.season import lambing_season_report

    if year is None:
        year = datetime.now(UTC).year
    data = _farm_data(season=year)
    result = lambing_season_report(data)
    _add_warnings(result)
    return json.dumps(result, indent=2)


@server.tool()
async def get_lambs(year: int | None = None, dam: str | None = None, sire: str | None = None) -> str:
    """Get all lambs for a season with full outcome data.

    Returns each lamb with: name, sex, breed, dam, sire, fate, lossCategory.
    Filter by year, dam name, or sire name. Defaults to current year.
    The agent can filter/group these results for loss analysis, sire analysis, etc.
    """
    loader = _load()
    if year is None:
        year = datetime.now(UTC).year
    data = _farm_data(season=year)

    lambs = [a for a in data.animals if loader.get_birth_year(a) == year]

    # Filter by dam name
    if dam:
        dam_animal = _find_animal_in_cache(dam, data.animals, data.by_id)
        if dam_animal:
            dam_id = dam_animal["animalId"]
            lambs = [a for a in lambs if loader.get_dam_id(a) == dam_id]
        else:
            return json.dumps({"error": f"No animal found matching dam '{dam}'"})

    # Filter by sire name
    if sire:
        sire_needle = sire.strip().upper()
        lambs = [a for a in lambs if loader.get_sire_name(a).upper() == sire_needle]

    results = [_lamb_summary(a, loader, data.loss_records) for a in lambs]
    response = {"year": year, "count": len(results), "lambs": results}
    _add_warnings(response)
    return json.dumps(response, indent=2)


@server.tool()
async def get_sire_stats(sire: str | None = None) -> str:
    """Lambing loss rate per sire across all years.

    If sire specified, deep dive on that sire's outcomes.
    If not, returns a summary table of all sires.
    """
    loader = _load()
    data = _farm_data()

    # Build sire -> offspring mapping
    sire_data: dict[str, dict] = {}
    for a in data.animals:
        sire_id = loader.get_sire_id(a)
        if not sire_id:
            continue
        sire_name = loader.get_sire_name(a)

        if sire_name not in sire_data:
            sire_data[sire_name] = {"total": 0, "raised": 0, "losses": 0, "byYear": {}}

        entry = sire_data[sire_name]
        birth_year = loader.get_birth_year(a)

        # Only count lambs (animals with a known birth year that are offspring)
        if birth_year is None:
            continue

        entry["total"] += 1
        year_key = str(birth_year)
        if year_key not in entry["byYear"]:
            entry["byYear"][year_key] = {"total": 0, "raised": 0, "losses": 0}
        entry["byYear"][year_key]["total"] += 1

        if loader.was_raised(a):
            entry["raised"] += 1
            entry["byYear"][year_key]["raised"] += 1
        elif loader.is_dead(a):
            entry["losses"] += 1
            entry["byYear"][year_key]["losses"] += 1

    if sire is not None:
        # Find the specific sire — try exact match first, then case-insensitive
        found = _find_animal_in_cache(sire, data.animals, data.by_id)
        sire_name = loader.get_name(found) if found else sire
        # Try both the resolved name and the original input (case may differ)
        stats = sire_data.get(sire_name) or sire_data.get(sire.upper()) or sire_data.get(sire)
        if not stats:
            # Last resort: case-insensitive search through all keys
            for key in sire_data:
                if key.upper() == sire.upper():
                    stats = sire_data[key]
                    sire_name = key
                    break
        if not stats:
            return json.dumps({"error": f"No offspring data found for sire '{sire}'"})
        loss_rate = round(stats["losses"] / stats["total"] * 100, 1) if stats["total"] else 0
        return json.dumps(
            {
                "sire": sire_name,
                "totalOffspring": stats["total"],
                "raised": stats["raised"],
                "losses": stats["losses"],
                "lossRate": f"{loss_rate}%",
                "byYear": stats["byYear"],
            },
            indent=2,
        )

    # Summary table for all sires
    summary = []
    for name, stats in sorted(sire_data.items()):
        loss_rate = round(stats["losses"] / stats["total"] * 100, 1) if stats["total"] else 0
        summary.append(
            {
                "sire": name,
                "totalOffspring": stats["total"],
                "raised": stats["raised"],
                "losses": stats["losses"],
                "lossRate": f"{loss_rate}%",
            }
        )

    return json.dumps({"sires": summary}, indent=2)


@server.tool()
async def get_joining_groups(year: int | None = None) -> str:
    """Natural service groups: which ewes were joined to which ram, dates, pastures."""
    loader = _load()
    if year is None:
        year = datetime.now(UTC).year
    data = _farm_data(season=year)

    if not data.service_groups:
        return json.dumps({"year": year, "groups": [], "note": "No natural service records found"})

    groups = []
    for g in data.service_groups:
        sire_name = g.get("sire_name") or g.get("sire") or "?"
        ewe_ids = g.get("ewe_ids") or g.get("ewes") or []
        # Resolve ewe names
        ewe_names = []
        for eid in ewe_ids:
            if eid in data.by_id:
                ewe_names.append(loader.get_name(data.by_id[eid]))
            else:
                ewe_names.append(eid[:8])

        groups.append(
            {
                "sire": sire_name,
                "ewes": ewe_names,
                "eweCount": len(ewe_names),
                "startDate": g.get("start_date"),
                "endDate": g.get("end_date"),
                "pasture": g.get("pasture") or g.get("paddock"),
            }
        )

    return json.dumps({"year": year, "groups": groups}, indent=2)


@server.tool()
async def get_ncc_compatibility(ram: str, ewe: str) -> str:
    """Check if two NCC animals share ancestors (inbreeding risk).

    Returns shared ancestor names and estimated inbreeding risk level.
    """
    loader = _load()
    data = _farm_data()

    ram_animal = _find_animal_in_cache(ram, data.animals, data.by_id)
    if not ram_animal:
        return json.dumps({"error": f"No animal found matching '{ram}'"})

    ewe_animal = _find_animal_in_cache(ewe, data.animals, data.by_id)
    if not ewe_animal:
        return json.dumps({"error": f"No animal found matching '{ewe}'"})

    ram_ancestors = loader.get_ancestors(ram_animal["animalId"], data.by_id, max_depth=4)
    ewe_ancestors = loader.get_ancestors(ewe_animal["animalId"], data.by_id, max_depth=4)

    shared = ram_ancestors & ewe_ancestors

    # Resolve shared ancestor names
    shared_names = []
    for aid in shared:
        if aid in data.by_id:
            shared_names.append(loader.get_name(data.by_id[aid]))
        elif not any(c == "-" for c in aid) or len(aid) < 20:
            shared_names.append(aid)

    shared_names = sorted(set(shared_names))

    if not shared_names:
        risk = "none detected"
    elif len(shared_names) <= 2:
        risk = "low-moderate"
    else:
        risk = "moderate-high"

    return json.dumps(
        {
            "ram": loader.get_name(ram_animal),
            "ewe": loader.get_name(ewe_animal),
            "sharedAncestors": shared_names,
            "sharedCount": len(shared_names),
            "inbreedingRisk": risk,
        },
        indent=2,
    )


@server.tool()
async def get_breedable_ewes(breed: str | None = None) -> str:
    """List on-farm breeding-age females, optionally filtered by breed.

    Includes ewes, maiden ewes, and ewe hoggets that are currently on-farm.
    """
    loader = _load()
    data = _farm_data()

    ewes = [a for a in data.animals if loader.is_ewe(a) and loader.is_on_farm(a)]

    if breed:
        breed_lower = breed.lower()
        ewes = [a for a in ewes if loader.get_breed(a).lower() == breed_lower]

    results = [_animal_summary(a, loader) for a in ewes]

    return json.dumps(
        {
            "count": len(results),
            "breed": breed or "all",
            "ewes": results,
        },
        indent=2,
    )


# ---------------------------------------------------------------------------
# Portal data tools (read from .cache/portal/)
# ---------------------------------------------------------------------------


@server.tool()
async def get_notes(animal: str) -> str:
    """Get clinical notes for an animal from AgriWebb portal data.

    Returns notes sorted by date (most recent first).
    Requires portal data to be cached — run portal sync first.
    """
    loader = _load()
    data = _farm_data()
    found = _find_animal_in_cache(animal, data.animals, data.by_id)
    if not found:
        return json.dumps({"error": f"No animal found matching '{animal}'"})

    notes = _find_portal_records_for_animal(found["animalId"], "note-record")
    if not notes:
        return json.dumps(
            {
                "animal": loader.get_name(found),
                "notes": [],
                "message": "No notes found. Portal data may need refreshing.",
            }
        )

    return json.dumps(
        {
            "animal": loader.get_name(found),
            "count": len(notes),
            "notes": [
                {"date": n.get("observationDate"), "note": n.get("note")}
                for n in sorted(notes, key=lambda x: x.get("observationDate") or 0, reverse=True)
            ],
        },
        indent=2,
    )


@server.tool()
async def get_death_details(animal: str) -> str:
    """Get death/loss details for an animal from AgriWebb portal data.

    Returns fateReason, fateDetails (clinical notes), and disposal info.
    Requires portal data to be cached — run portal sync first.
    """
    loader = _load()
    data = _farm_data()
    found = _find_animal_in_cache(animal, data.animals, data.by_id)
    if not found:
        return json.dumps({"error": f"No animal found matching '{animal}'"})

    deaths = _find_portal_records_for_animal(found["animalId"], "death-record")
    if not deaths:
        fate = (found.get("state") or {}).get("fate")
        if fate == "Dead":
            return json.dumps(
                {
                    "animal": loader.get_name(found),
                    "fate": "Dead",
                    "message": "No portal death record found. Portal data may need refreshing.",
                }
            )
        return json.dumps(
            {
                "animal": loader.get_name(found),
                "fate": fate,
                "message": "This animal is not recorded as dead.",
            }
        )

    d = deaths[0]
    fate = d.get("fate") or {}
    return json.dumps(
        {
            "animal": loader.get_name(found),
            "fateReason": fate.get("fateReason"),
            "fateDetails": fate.get("fateDetails"),
            "disposalMethod": fate.get("disposalMethod"),
            "disposalDate": fate.get("disposalDate"),
            "observationDate": d.get("observationDate"),
        },
        indent=2,
    )


@server.tool()
async def get_ai_records() -> str:
    """Get all artificial insemination records from portal data.

    Returns donor sire details, ewe IDs, and dates.
    """
    records = _load_portal_cache("ai-record")
    if not records:
        return json.dumps({"records": [], "message": "No AI records cached. Run portal sync first."})

    loader = _load()
    data = _farm_data()

    results = []
    for rec in records:
        straw = rec.get("straw") or {}
        sire_details = straw.get("sireDetails") or {}
        ewe_ids = list(rec.get("animalIds") or [])
        ewe_names = []
        for eid in ewe_ids:
            a = data.by_id.get(eid)
            if a:
                ewe_names.append(loader.get_name(a))

        results.append(
            {
                "date": rec.get("observationDate"),
                "sireName": sire_details.get("name"),
                "sireBreed": sire_details.get("breed"),
                "semenType": straw.get("semenType"),
                "ewes": ewe_names,
            }
        )

    return json.dumps({"count": len(results), "records": results}, indent=2)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    """Run the MCP server over stdio."""
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
