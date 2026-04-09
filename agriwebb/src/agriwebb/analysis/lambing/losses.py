"""Loss analysis report — structured data for CLI and MCP consumers.

Pure functions that operate on a :class:`FarmData` instance and return plain
dicts suitable for JSON serialisation or formatted printing.

Terminology:
- "loss" not "death"
- fate=Sold is a success, not a loss
- Stillborn = daysReared is None or 0
- Early loss = daysReared 1-90
- Late loss = daysReared > 90 (not lambing-related)
"""

from __future__ import annotations

from agriwebb.analysis.lambing.loader import (
    FarmData,
    classify_loss,
    get_birth_year,
    get_breed,
    get_dam_id,
    get_dam_name,
    get_days_reared,
    get_litter,
    get_name,
    get_sire_name,
    is_dead,
    was_raised,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CATEGORY_DISPLAY = {
    "stillborn": "Stillborn",
    "early_loss": "Early loss",
    "late_death": "Late loss",
    "prenatal": "Prenatal",
    "intrapartum": "Intrapartum",
    "perinatal": "Perinatal",
}

# Categories considered preventable for the summary counter
_PREVENTABLE = frozenset({"early_loss", "perinatal"})


def _season_lambs(data: FarmData) -> list[dict]:
    """Return all lambs born in the report season."""
    return [a for a in data.animals if get_birth_year(a) == data.season]


def _count_category(lambs: list[dict], loss_records: list[dict]) -> dict[str, int]:
    """Count losses by category."""
    counts: dict[str, int] = {}
    for lamb in lambs:
        cat = classify_loss(lamb, loss_records)
        if cat:
            counts[cat] = counts.get(cat, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def loss_report(data: FarmData) -> dict:
    """Generate the loss analysis report.

    Returns dict with:
    - summary: {total, prenatal, intrapartum, perinatal, stillborn,
                early_loss, late_death, preventable}
    - by_dam: [{dam, breed, litter_size, lost, category, detail}, ...]
    - by_sire: [{sire, lambs, raised, lost, rate}, ...]
    - year_over_year: [{year, dams, born, raised, stillborn, early, late, rate}, ...]
    """
    lambs = _season_lambs(data)
    dead_lambs = [a for a in lambs if is_dead(a)]

    # -- Summary --
    cats = _count_category(dead_lambs, data.loss_records)
    preventable = sum(v for k, v in cats.items() if k in _PREVENTABLE)

    summary = {
        "total": len(dead_lambs),
        "prenatal": cats.get("prenatal", 0),
        "intrapartum": cats.get("intrapartum", 0),
        "perinatal": cats.get("perinatal", 0),
        "stillborn": cats.get("stillborn", 0),
        "early_loss": cats.get("early_loss", 0),
        "late_death": cats.get("late_death", 0),
        "preventable": preventable,
    }

    # -- By dam --
    by_dam: list[dict] = []
    # Group dead lambs by dam
    dam_losses: dict[str, list[dict]] = {}
    for lamb in dead_lambs:
        dam_id = get_dam_id(lamb) or "?"
        dam_losses.setdefault(dam_id, []).append(lamb)

    for dam_id, lost_lambs in sorted(dam_losses.items()):
        dam = data.by_id.get(dam_id)
        dam_display = get_name(dam) if dam else get_dam_name(lost_lambs[0])
        breed = get_breed(dam) if dam else "?"
        litter = get_litter(dam_id, data.season, data.animals)
        categories = []
        details = []
        for lamb in lost_lambs:
            cat = classify_loss(lamb, data.loss_records) or "?"
            categories.append(_CATEGORY_DISPLAY.get(cat, cat))
            days = get_days_reared(lamb)
            if days is not None and days > 0:
                details.append(f"{get_name(lamb)}: {days}d")
            else:
                details.append(get_name(lamb))
        by_dam.append({
            "dam": dam_display,
            "breed": breed,
            "litter_size": len(litter),
            "lost": len(lost_lambs),
            "category": ", ".join(sorted(set(categories))),
            "detail": "; ".join(details),
        })

    # -- By sire --
    sire_stats: dict[str, dict] = {}
    for lamb in lambs:
        sire = get_sire_name(lamb)
        if sire not in sire_stats:
            sire_stats[sire] = {"sire": sire, "lambs": 0, "raised": 0, "lost": 0, "rate": 0.0}
        sire_stats[sire]["lambs"] += 1
        if was_raised(lamb):
            sire_stats[sire]["raised"] += 1
        elif is_dead(lamb):
            sire_stats[sire]["lost"] += 1

    for stats in sire_stats.values():
        stats["rate"] = round(stats["lost"] / stats["lambs"], 2) if stats["lambs"] else 0.0

    by_sire = sorted(sire_stats.values(), key=lambda s: s["lost"], reverse=True)

    # -- Year over year --
    year_over_year: list[dict] = []
    # Gather all birth years present
    birth_years: set[int] = set()
    for a in data.animals:
        by = get_birth_year(a)
        if by is not None:
            birth_years.add(by)

    for year in sorted(birth_years):
        yr_lambs = [a for a in data.animals if get_birth_year(a) == year]
        if not yr_lambs:
            continue
        yr_raised = [a for a in yr_lambs if was_raised(a)]
        yr_dead = [a for a in yr_lambs if is_dead(a)]
        yr_cats = _count_category(yr_dead, data.loss_records)

        # Count unique dams
        yr_dam_ids: set[str] = set()
        for a in yr_lambs:
            did = get_dam_id(a)
            if did:
                yr_dam_ids.add(did)

        total_born = len(yr_lambs)
        year_over_year.append({
            "year": year,
            "dams": len(yr_dam_ids),
            "born": total_born,
            "raised": len(yr_raised),
            "stillborn": yr_cats.get("stillborn", 0) + yr_cats.get("prenatal", 0) + yr_cats.get("intrapartum", 0),
            "early": yr_cats.get("early_loss", 0) + yr_cats.get("perinatal", 0),
            "late": yr_cats.get("late_death", 0),
            "rate": round(len(yr_raised) / total_born * 100, 1) if total_born else 0.0,
        })

    return {
        "season": data.season,
        "summary": summary,
        "by_dam": by_dam,
        "by_sire": by_sire,
        "year_over_year": year_over_year,
    }
