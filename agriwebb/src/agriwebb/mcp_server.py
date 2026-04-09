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
from collections import Counter
from datetime import UTC, datetime

from mcp.server.fastmcp import FastMCP

server = FastMCP("agriwebb")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _load():
    """Lazy-import and return the loader module."""
    from agriwebb.analysis.lambing import loader

    return loader


def _farm_data(season: int | None = None):
    """Load farm data, defaulting season to current year."""
    return _load().load_farm_data(season=season)


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
    """Season report: total live lambs, sex breakdown, lambing rate, litter size distribution.

    'Born' = live lambs (alive or sold). Sold = successfully raised, not a loss.
    """
    loader = _load()
    if year is None:
        year = datetime.now(UTC).year
    data = _farm_data(season=year)

    # Find all lambs born this year
    born_this_year = [a for a in data.animals if loader.get_birth_year(a) == year]

    # Live-born = fate is Alive or Sold (not Dead)
    live_born = [a for a in born_this_year if loader.was_raised(a)]
    dead_born = [a for a in born_this_year if loader.is_dead(a)]

    # Sex breakdown of all born
    sex_counts = Counter(loader.get_sex(a) for a in born_this_year)

    # Litter size distribution (group by dam)
    dam_litters: dict[str, int] = {}
    for a in born_this_year:
        dam_id = loader.get_dam_id(a) or "unknown"
        dam_litters[dam_id] = dam_litters.get(dam_id, 0) + 1
    litter_dist = Counter(dam_litters.values())

    # Lambing rate = total born / number of dams that lambed
    num_dams = len(dam_litters)
    lambing_rate = round(len(born_this_year) / num_dams, 2) if num_dams else 0

    return json.dumps(
        {
            "year": year,
            "totalBorn": len(born_this_year),
            "liveBorn": len(live_born),
            "losses": len(dead_born),
            "sexBreakdown": dict(sex_counts),
            "lambingRate": lambing_rate,
            "damsLambed": num_dams,
            "litterSizeDistribution": {str(k): v for k, v in sorted(litter_dist.items())},
        },
        indent=2,
    )


@server.tool()
async def get_losses(year: int | None = None) -> str:
    """Loss report: count by category, by sire, and by dam breed.

    Categories: stillborn, early_loss (0-90 days), late_death (>90 days),
    or detailed categories from loss records (prenatal, intrapartum, perinatal).
    """
    loader = _load()
    if year is None:
        year = datetime.now(UTC).year
    data = _farm_data(season=year)

    dead_this_year = [
        a for a in data.animals if loader.get_birth_year(a) == year and loader.is_dead(a)
    ]

    by_category: dict[str, int] = {}
    by_sire: dict[str, int] = {}
    by_dam_breed: dict[str, int] = {}

    for a in dead_this_year:
        cat = loader.classify_loss(a, data.loss_records) or "unknown"
        by_category[cat] = by_category.get(cat, 0) + 1

        sire_name = loader.get_sire_name(a)
        by_sire[sire_name] = by_sire.get(sire_name, 0) + 1

        dam_id = loader.get_dam_id(a)
        dam_breed = loader.get_breed(data.by_id[dam_id]) if dam_id and dam_id in data.by_id else "?"
        by_dam_breed[dam_breed] = by_dam_breed.get(dam_breed, 0) + 1

    return json.dumps(
        {
            "year": year,
            "totalLosses": len(dead_this_year),
            "byCategory": by_category,
            "bySire": by_sire,
            "byDamBreed": by_dam_breed,
        },
        indent=2,
    )


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
        # Find the specific sire
        found = _find_animal_in_cache(sire, data.animals, data.by_id)
        sire_name = loader.get_name(found) if found else sire
        stats = sire_data.get(sire_name)
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
# Entry point
# ---------------------------------------------------------------------------


def main():
    """Run the MCP server over stdio."""
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
