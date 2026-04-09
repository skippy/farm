"""Lambing season report — structured data for CLI and MCP consumers.

Pure functions that operate on a :class:`FarmData` instance and return plain
dicts suitable for JSON serialisation or formatted printing.
"""

from __future__ import annotations

from collections import Counter

from agriwebb.analysis.lambing.loader import (
    FarmData,
    get_birth_year,
    get_breed,
    get_dam_id,
    get_ewes_in_group,
    get_litter,
    get_sex,
    get_sire_name,
    is_dead,
    is_first_time_mother,
    was_raised,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _season_lambs(data: FarmData) -> list[dict]:
    """Return all lambs born in the report season."""
    return [a for a in data.animals if get_birth_year(a) == data.season]


def _unique_dam_ids(lambs: list[dict]) -> set[str]:
    """Return the set of dam IDs from a list of lambs."""
    ids: set[str] = set()
    for lamb in lambs:
        dam_id = get_dam_id(lamb)
        if dam_id:
            ids.add(dam_id)
    return ids


def _unique_joined_ewes(data: FarmData) -> set[str]:
    """Return all ewe IDs that were in any service group."""
    ids: set[str] = set()
    for group in data.service_groups:
        sire_name = group.get("sire_name") or group.get("sire") or ""
        ids.update(get_ewes_in_group(sire_name, data.service_groups))
    return ids


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def lambing_season_report(data: FarmData) -> dict:
    """Generate the lambing season report.

    Returns dict with:
    - headline: {live_lambs, males, females, ewes_lambed, ewes_joined,
                 lambing_rate_per_lambed, lambing_rate_per_joined}
    - litter_distribution: {1: n_singles, 2: n_twins, 3: n_triplets, ...}
    - by_sire: [{sire, joined, lambed, live, lost, rate}, ...]
    - by_breed: [{breed, dams, live, lost, rate}, ...]
    - maiden_vs_experienced: {maiden: {dams, live, lost}, experienced: {dams, live, lost}}
    """
    lambs = _season_lambs(data)
    live_lambs = [a for a in lambs if was_raised(a)]

    males = sum(1 for a in live_lambs if get_sex(a) == "Male")
    females = sum(1 for a in live_lambs if get_sex(a) == "Female")

    dam_ids = _unique_dam_ids(lambs)
    ewes_lambed = len(dam_ids)
    joined_ewe_ids = _unique_joined_ewes(data)
    ewes_joined = len(joined_ewe_ids) if joined_ewe_ids else ewes_lambed

    lambing_rate_per_lambed = round(len(live_lambs) / ewes_lambed, 2) if ewes_lambed else 0.0
    lambing_rate_per_joined = round(len(live_lambs) / ewes_joined, 2) if ewes_joined else 0.0

    headline = {
        "live_lambs": len(live_lambs),
        "males": males,
        "females": females,
        "ewes_lambed": ewes_lambed,
        "ewes_joined": ewes_joined,
        "lambing_rate_per_lambed": lambing_rate_per_lambed,
        "lambing_rate_per_joined": lambing_rate_per_joined,
    }

    # -- Litter distribution --
    litter_sizes: Counter[int] = Counter()
    for dam_id in dam_ids:
        litter = get_litter(dam_id, data.season, data.animals)
        litter_sizes[len(litter)] += 1
    litter_distribution = dict(sorted(litter_sizes.items()))

    # -- By sire --
    sire_stats: dict[str, dict] = {}
    for lamb in lambs:
        sire = get_sire_name(lamb)
        if sire not in sire_stats:
            sire_stats[sire] = {"sire": sire, "joined": 0, "lambed": 0, "live": 0, "lost": 0, "rate": 0.0}
        # Count live/lost per sire
        if was_raised(lamb):
            sire_stats[sire]["live"] += 1
        elif is_dead(lamb):
            sire_stats[sire]["lost"] += 1

    # Count joined/lambed ewes per sire
    for sire_name, stats in sire_stats.items():
        joined_ids = get_ewes_in_group(sire_name, data.service_groups)
        stats["joined"] = len(joined_ids)
        # Lambed = dams that actually produced lambs sired by this sire
        lambed_ids: set[str] = set()
        for lamb in lambs:
            if get_sire_name(lamb) == sire_name:
                dam_id = get_dam_id(lamb)
                if dam_id:
                    lambed_ids.add(dam_id)
        stats["lambed"] = len(lambed_ids)
        stats["rate"] = round(stats["live"] / stats["lambed"], 2) if stats["lambed"] else 0.0

    by_sire = sorted(sire_stats.values(), key=lambda s: s["live"], reverse=True)

    # -- By breed (dam breed) --
    breed_stats: dict[str, dict] = {}
    for dam_id in dam_ids:
        dam = data.by_id.get(dam_id)
        breed = get_breed(dam) if dam else "?"
        if breed not in breed_stats:
            breed_stats[breed] = {"breed": breed, "dams": 0, "live": 0, "lost": 0, "rate": 0.0}
        breed_stats[breed]["dams"] += 1
        litter = get_litter(dam_id, data.season, data.animals)
        for lamb in litter:
            if was_raised(lamb):
                breed_stats[breed]["live"] += 1
            elif is_dead(lamb):
                breed_stats[breed]["lost"] += 1

    for stats in breed_stats.values():
        stats["rate"] = round(stats["live"] / stats["dams"], 2) if stats["dams"] else 0.0
    by_breed = sorted(breed_stats.values(), key=lambda s: s["dams"], reverse=True)

    # -- Maiden vs experienced --
    maiden = {"dams": 0, "live": 0, "lost": 0}
    experienced = {"dams": 0, "live": 0, "lost": 0}
    for dam_id in dam_ids:
        first_time = is_first_time_mother(dam_id, data.season, data.animals)
        bucket = maiden if first_time else experienced
        bucket["dams"] += 1
        litter = get_litter(dam_id, data.season, data.animals)
        for lamb in litter:
            if was_raised(lamb):
                bucket["live"] += 1
            elif is_dead(lamb):
                bucket["lost"] += 1

    return {
        "season": data.season,
        "headline": headline,
        "litter_distribution": litter_distribution,
        "by_sire": by_sire,
        "by_breed": by_breed,
        "maiden_vs_experienced": {"maiden": maiden, "experienced": experienced},
    }
