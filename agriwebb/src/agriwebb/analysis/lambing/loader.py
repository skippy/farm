"""Lambing analysis data loader.

Shared foundation for every lambing analysis tool and MCP tool.  Operates
purely on cached JSON files -- no API calls.

Key conventions
---------------
- "Born" = live lambs whose fate is Alive *or* Sold.
- fate=Sold means the lamb was successfully raised and harvested -- NOT a loss.
- Use "loss" rather than "death" in user-facing strings.
- daysReared is None or 0 -> stillborn
- daysReared 1-90 -> early loss (lambing-related)
- daysReared > 90 -> late loss (not lambing-related)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from agriwebb.core.cache import load_cache_json
from agriwebb.core.config import get_cache_dir

# ---------------------------------------------------------------------------
# Data Loading
# ---------------------------------------------------------------------------


@dataclass
class FarmData:
    """All cached farm data needed for lambing analysis."""

    animals: list[dict]
    by_id: dict[str, dict] = field(repr=False)
    service_groups: list[dict] = field(default_factory=list)
    loss_records: list[dict] = field(default_factory=list)
    season: int = 0


def _load_json_safe(path: Path) -> list[dict]:
    """Load a JSON file, returning an empty list if it does not exist."""
    if not path.exists():
        return []
    with open(path) as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        # Try common wrapper keys
        for key in ("groups", "records", "data", "items"):
            if key in data:
                return data[key]
        return [data]
    return []


def load_farm_data(season: int | None = None) -> FarmData:
    """Load all cached data for lambing analysis.

    Uses ``core.cache.load_cache_json`` for *animals.json*.
    Gracefully handles missing *natural_service.json* and *lamb_losses* files.
    *season* defaults to the current year.
    """
    if season is None:
        season = datetime.now(UTC).year

    animals = load_cache_json("animals.json", key="animals", default=[])
    by_id: dict[str, dict] = {a["animalId"]: a for a in animals}

    cache_dir = get_cache_dir()
    service_groups = _load_json_safe(cache_dir / "natural_service.json")
    loss_records = _load_json_safe(cache_dir / f"lamb_losses_{season}.json")

    return FarmData(
        animals=animals,
        by_id=by_id,
        service_groups=service_groups,
        loss_records=loss_records,
        season=season,
    )


# ---------------------------------------------------------------------------
# Animal Classification Helpers
# ---------------------------------------------------------------------------


def get_name(animal: dict) -> str:
    """Return best display name: name > vid > eid > animalId[:8]."""
    identity = animal.get("identity") or {}
    return identity.get("name") or identity.get("vid") or identity.get("eid") or animal.get("animalId", "?")[:8]


def get_breed(animal: dict) -> str:
    """Return ``characteristics.breedAssessed`` or ``'?'``."""
    return (animal.get("characteristics") or {}).get("breedAssessed") or "?"


def get_sex(animal: dict) -> str:
    """Return ``characteristics.sex`` or ``'?'``."""
    return (animal.get("characteristics") or {}).get("sex") or "?"


def get_birth_year(animal: dict) -> int | None:
    """Return ``characteristics.birthYear``."""
    return (animal.get("characteristics") or {}).get("birthYear")


def get_age_class(animal: dict) -> str:
    """Return ``characteristics.ageClass`` or ``'?'``."""
    return (animal.get("characteristics") or {}).get("ageClass") or "?"


def is_on_farm(animal: dict) -> bool:
    """Return ``state.onFarm``."""
    return bool((animal.get("state") or {}).get("onFarm"))


def is_alive(animal: dict) -> bool:
    """Return True when ``state.fate`` equals ``'Alive'``."""
    return (animal.get("state") or {}).get("fate") == "Alive"


def is_dead(animal: dict) -> bool:
    """Return True when ``state.fate`` equals ``'Dead'``.

    Note: Sold is *not* dead -- sold/harvested is a successful outcome.
    """
    return (animal.get("state") or {}).get("fate") == "Dead"


def is_sold(animal: dict) -> bool:
    """Return True when ``state.fate`` equals ``'Sold'``.

    Sold/harvested is a successful outcome, not a loss.
    """
    return (animal.get("state") or {}).get("fate") == "Sold"


def was_raised(animal: dict) -> bool:
    """Return True if the animal was successfully raised (alive or sold)."""
    return is_alive(animal) or is_sold(animal)


def is_ewe(animal: dict) -> bool:
    """True for female sheep of breeding age (not lamb/weaner)."""
    chars = animal.get("characteristics") or {}
    if (chars.get("sex") or "").lower() != "female":
        return False
    age_class = (chars.get("ageClass") or "").lower()
    # Exclude lambs and weaners -- they are too young for breeding
    if any(young in age_class for young in ("lamb", "weaner")):
        return False
    # Must be a ewe-class or hogget (hoggets can breed)
    return age_class in ("ewe", "maiden_ewe", "ewe_hogget", "hogget")


def is_intact_ram(animal: dict) -> bool:
    """True for intact male sheep (ram age class, not wether)."""
    chars = animal.get("characteristics") or {}
    if (chars.get("sex") or "").lower() != "male":
        return False
    age_class = (chars.get("ageClass") or "").lower()
    if "wether" in age_class:
        return False
    return "ram" in age_class


def get_days_reared(animal: dict) -> int | None:
    """Return ``state.daysReared``."""
    return (animal.get("state") or {}).get("daysReared")


# ---------------------------------------------------------------------------
# Parentage Helpers
# ---------------------------------------------------------------------------


def _first_parent(animal: dict, role: str) -> dict | None:
    """Return the first parent dict for *role* ('sires' or 'dams')."""
    parents = (animal.get("parentage") or {}).get(role) or []
    return parents[0] if parents else None


def get_sire_id(animal: dict) -> str | None:
    """Return ``parentAnimalId`` of the first sire."""
    p = _first_parent(animal, "sires")
    return p.get("parentAnimalId") if p else None


def get_sire_name(animal: dict) -> str:
    """Return the sire's display name from ``parentAnimalIdentity``, or ``'?'``."""
    p = _first_parent(animal, "sires")
    if not p:
        return "?"
    ident = p.get("parentAnimalIdentity") or {}
    return ident.get("name") or ident.get("vid") or ident.get("eid") or "?"


def get_dam_id(animal: dict) -> str | None:
    """Return ``parentAnimalId`` of the first dam."""
    p = _first_parent(animal, "dams")
    return p.get("parentAnimalId") if p else None


def get_dam_name(animal: dict) -> str:
    """Return the dam's display name from ``parentAnimalIdentity``, or ``'?'``."""
    p = _first_parent(animal, "dams")
    if not p:
        return "?"
    ident = p.get("parentAnimalIdentity") or {}
    return ident.get("name") or ident.get("vid") or ident.get("eid") or "?"


# ---------------------------------------------------------------------------
# Lineage Utilities
# ---------------------------------------------------------------------------


def get_ancestors(animal_id: str, by_id: dict[str, dict], max_depth: int = 6) -> set[str]:
    """Return all ancestor animal IDs *and* names (for matching off-cache ancestors).

    Returns both UUIDs (for on-cache matching) and uppercase names (for
    off-cache matching, e.g. AI donor sires that are not in *animals.json*).
    """
    ancestors: set[str] = set()
    walked: set[str] = set()

    def _walk(aid: str, depth: int) -> None:
        if depth > max_depth or aid in walked:
            return
        walked.add(aid)
        animal = by_id.get(aid)
        if not animal:
            return
        for role in ("sires", "dams"):
            for p in (animal.get("parentage") or {}).get(role) or []:
                pid = p.get("parentAnimalId")
                if pid:
                    ancestors.add(pid)
                    _walk(pid, depth + 1)
                # Also capture the name for off-cache matching
                ident = p.get("parentAnimalIdentity") or {}
                for field_name in ("name", "vid"):
                    val = ident.get(field_name)
                    if val:
                        ancestors.add(val.upper())

    _walk(animal_id, 0)
    return ancestors


def get_offspring(parent_id: str, animals: list[dict]) -> list[dict]:
    """Return all animals that list *parent_id* as sire or dam."""
    results = []
    for a in animals:
        parentage = a.get("parentage") or {}
        for role in ("sires", "dams"):
            for p in parentage.get(role) or []:
                if p.get("parentAnimalId") == parent_id:
                    results.append(a)
                    break
            else:
                continue
            break
    return results


def get_offspring_by_year(parent_id: str, animals: list[dict]) -> dict[int, list[dict]]:
    """Return offspring grouped by birth year."""
    by_year: dict[int, list[dict]] = {}
    for a in get_offspring(parent_id, animals):
        year = get_birth_year(a)
        if year is not None:
            by_year.setdefault(year, []).append(a)
    return by_year


def get_litter(dam_id: str, birth_year: int, animals: list[dict]) -> list[dict]:
    """Return all lambs from one ewe in one year."""
    results = []
    for a in animals:
        if get_birth_year(a) != birth_year:
            continue
        parentage = a.get("parentage") or {}
        for p in parentage.get("dams") or []:
            if p.get("parentAnimalId") == dam_id:
                results.append(a)
                break
    return results


# ---------------------------------------------------------------------------
# Loss Classification
# ---------------------------------------------------------------------------


def classify_loss(animal: dict, loss_records: list[dict] | None = None) -> str | None:
    """Classify a dead animal's loss type.

    If detailed *loss_records* are available (from ``lamb_losses_YYYY.json``),
    uses those for category (prenatal/intrapartum/perinatal).

    Falls back to ``daysReared``-based classification:

    - daysReared is None or 0 -> ``'stillborn'``
    - daysReared 1-90 -> ``'early_loss'``
    - daysReared > 90 -> ``'late_death'`` (not a lambing loss)

    Returns ``None`` for alive or sold animals.
    """
    if not is_dead(animal):
        return None

    # Check detailed loss records first
    if loss_records:
        animal_id = animal.get("animalId")
        for rec in loss_records:
            if rec.get("animalId") == animal_id and rec.get("category"):
                return rec["category"]

    # Fallback to daysReared
    days = get_days_reared(animal)
    if days is None or days == 0:
        return "stillborn"
    if days <= 90:
        return "early_loss"
    return "late_death"


def is_lambing_loss(animal: dict, loss_records: list[dict] | None = None) -> bool:
    """True if this is a lambing-related loss (stillborn or early_loss)."""
    classification = classify_loss(animal, loss_records)
    return classification in ("stillborn", "early_loss")


# ---------------------------------------------------------------------------
# Breeding Group Helpers
# ---------------------------------------------------------------------------


def get_joining_group(dam_id: str, service_groups: list[dict]) -> dict | None:
    """Return the natural service group this ewe was in, or ``None``."""
    for group in service_groups:
        ewe_ids = group.get("ewe_ids") or group.get("ewes") or []
        if dam_id in ewe_ids:
            return group
    return None


def get_joined_sire(dam_id: str, service_groups: list[dict]) -> str | None:
    """Return the sire name this ewe was joined with, or ``None``."""
    group = get_joining_group(dam_id, service_groups)
    if group is None:
        return None
    return group.get("sire_name") or group.get("sire")


def get_ewes_in_group(sire_name: str, service_groups: list[dict]) -> list[str]:
    """Return ``animalId`` values of all ewes joined to a sire."""
    result: list[str] = []
    for group in service_groups:
        name = group.get("sire_name") or group.get("sire") or ""
        if name == sire_name:
            result.extend(group.get("ewe_ids") or group.get("ewes") or [])
    return result


# ---------------------------------------------------------------------------
# Experience & Age Helpers
# ---------------------------------------------------------------------------


def _get_dam_offspring(dam_id: str, animals: list[dict]) -> list[dict]:
    """Return all animals that list *dam_id* as their dam (not sire)."""
    results = []
    for a in animals:
        parentage = a.get("parentage") or {}
        for p in parentage.get("dams") or []:
            if p.get("parentAnimalId") == dam_id:
                results.append(a)
                break
    return results


def get_lambing_history(dam_id: str, animals: list[dict]) -> dict[int, list[dict]]:
    """Return ``{year: [lambs]}`` for all years this dam has offspring."""
    by_year: dict[int, list[dict]] = {}
    for a in _get_dam_offspring(dam_id, animals):
        year = get_birth_year(a)
        if year is not None:
            by_year.setdefault(year, []).append(a)
    return by_year


def is_first_time_mother(dam_id: str, season: int, animals: list[dict]) -> bool:
    """True if this dam has no offspring in any year before *season*."""
    history = get_lambing_history(dam_id, animals)
    return all(year >= season for year in history)


def get_age_at_first_lambing(dam_id: str, animals: list[dict], by_id: dict[str, dict]) -> int | None:
    """Return the age (in years) at which this dam first lambed, or ``None``."""
    history = get_lambing_history(dam_id, animals)
    if not history:
        return None
    first_year = min(history)
    dam = by_id.get(dam_id)
    if not dam:
        return None
    dam_birth_year = get_birth_year(dam)
    if dam_birth_year is None:
        return None
    return first_year - dam_birth_year


# ---------------------------------------------------------------------------
# Breed Cross Classification
# ---------------------------------------------------------------------------

_NCC_NAMES = frozenset({"north country cheviot", "ncc", "cheviot"})
_FINN_NAMES = frozenset({"finnsheep", "finn", "finnish landrace"})


def _is_ncc(breed: str) -> bool:
    return breed.lower() in _NCC_NAMES


def _is_finn(breed: str) -> bool:
    return breed.lower() in _FINN_NAMES


def get_breed_cross(lamb: dict, by_id: dict[str, dict]) -> str:
    """Classify the breed cross of a lamb based on sire and dam breeds.

    Returns one of: ``'NCC x NCC'``, ``'NCC x other'``, ``'Finn-involved'``,
    ``'other'``.
    """
    sire_id = get_sire_id(lamb)
    dam_id = get_dam_id(lamb)

    sire_breed = get_breed(by_id[sire_id]) if sire_id and sire_id in by_id else get_sire_name(lamb)
    dam_breed = get_breed(by_id[dam_id]) if dam_id and dam_id in by_id else get_dam_name(lamb)

    # Check Finn involvement first (either parent)
    if _is_finn(sire_breed) or _is_finn(dam_breed):
        return "Finn-involved"

    if _is_ncc(sire_breed) and _is_ncc(dam_breed):
        return "NCC x NCC"

    if _is_ncc(sire_breed) or _is_ncc(dam_breed):
        return "NCC x other"

    return "other"
