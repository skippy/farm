"""Resolve vial tokens into WADDL per-animal submission fields.

A vial is identified on the farm by the **last 4 digits of its EID**, or
sometimes a VID or a name when the tag is missing/unreadable. This module maps
each token to an animal in the local cache and derives the four fields the WADDL
"Animals" step needs:

* **ID**      - the token, typed verbatim into the portal's ID box
* **Taxon**   - breed, mapped to the portal's taxonomy string + internal id
* **Sex Code**- from sex + age class (a wether is Male Neutered)
* **Age**     - whole number; months if under 1.5 years, otherwise years

Breed for a cross is resolved by walking the pedigree, weighting each parent
slot equally and tallying base-breed fractions, then taking the dominant family.
A near-tie (top two families within ``TIE_THRESHOLD``) breaks toward the **dam's
breed**; if the dam is unknown it breaks to Finn when Finn is a candidate, else
the highest-ranked candidate (alphabetical among equals). Anything uncertain -
an incomplete pedigree, a near-tie, an ambiguous or missing match - is flagged
in ``warnings`` rather than filled silently.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Literal

from agriwebb.analysis.lambing.loader import (
    get_age_class,
    get_breed,
    get_dam_id,
    get_sex,
    get_sire_id,
    is_on_farm,
)
from agriwebb.core.cache import load_cache_json

# --- WADDL taxonomy ---------------------------------------------------------
TAXON_PREFIX = "mammalia :: bovidae :: ovis :: aries :: Domestic Sheep :: "

# base breed family -> (portal taxon suffix, portal internal breed_id)
# IDs observed in the Oak Knoll production portal; re-derive from the Taxon
# dropdown if the portal renumbers them.
FAMILY_TAXON = {
    "Finn": ("Finnish-Landrace", 3569),
    "NCC": ("North Country Cheviot", 3591),
    "BFL": ("Bluefaced Leicester", 3547),
}
DEFAULT_FAMILY = "Finn"  # tie-break of last resort + unresolved crosses
TIE_THRESHOLD = 0.15  # top-two breed fractions within this = a near-tie
KNOWN_MASS_MIN = 0.999  # below this = an incomplete pedigree, flag it

# --- WADDL sex codes (portal label, internal sex_code_id) -------------------
SEX_FEMALE = ("Female (F)", 2)
SEX_MALE = ("Male (M)", 3)
SEX_MALE_NEUTERED = ("Male Neutered (C)", 5)
SEX_UNKNOWN = ("Unknown (U)", 1)

# --- WADDL age units (label -> internal age_uom_id) -------------------------
# age_fields only emits years/months; weeks/days are here for completeness.
AGE_UOM = {"years": 21, "months": 20, "weeks": 43, "days": 22}
MONTHS_CUTOFF_YEARS = 1.5  # under this, report age in months

# --- WADDL specimen defaults (portal label -> internal id) ------------------
# Not resolver-derived; single-sourced here so the doc/browser recipe and any
# future code stay in sync. Re-verify against the portal if it renumbers.
SPECIMEN_IDS = {
    "Clotted Blood (CB)": 266,
    "Acute": 1097,
    "Red Top Tube (RTT)": 2,
}

_DAYS_PER_YEAR = 365.25
_DAYS_PER_MONTH = 30.44


@dataclass
class ResolvedAnimal:
    """One vial's resolution to WADDL fields.

    ``found`` is False when the token matched no animal. Even when ``found`` is
    True a field may be None if the cache lacked the data (e.g. ``age`` with no
    birth date/year); such cases always carry an explanatory ``warnings`` entry,
    so callers must check ``warnings`` (and None fields) before filling.
    """

    token: str
    found: bool = True
    matched_by: Literal["EID last-4", "VID", "name"] | None = None
    animal_id: str | None = None
    name: str | None = None
    id_field: str | None = None  # what to type into the portal ID box (== token)
    taxon: str | None = None
    taxon_family: str | None = None
    breed_id: int | None = None
    sex_code: str | None = None
    sex_code_id: int | None = None
    age: int | None = None
    age_unit: Literal["years", "months"] | None = None
    age_uom_id: int | None = None
    health_flag: str | None = None  # e.g. "CL+", "Johne's+"
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------
def _identity(animal: dict) -> dict:
    return animal.get("identity") or {}


def _candidates(token: str, animals: list[dict]) -> tuple[list[dict], str | None]:
    """All animals matching *token*, and the channel that matched.

    Match order: last-4 of EID, then exact VID, then exact name
    (case-insensitive for VID/name). Returns every candidate on the winning
    channel so the caller can flag ambiguity.
    """
    if not token:
        return [], None
    low = token.lower()
    by_eid = [a for a in animals if (_identity(a).get("eid") or "")[-4:] == token]
    if by_eid:
        return by_eid, "EID last-4"
    by_vid = [a for a in animals if str(_identity(a).get("vid") or "").lower() == low and _identity(a).get("vid")]
    if by_vid:
        return by_vid, "VID"
    by_name = [a for a in animals if str(_identity(a).get("name") or "").lower() == low and _identity(a).get("name")]
    if by_name:
        return by_name, "name"
    return [], None


def resolve_token(
    token: str,
    animals: list[dict],
    by_id: dict[str, dict],
    today: date | None = None,
) -> ResolvedAnimal:
    """Match one token to an animal and derive its WADDL fields."""
    today = today or datetime.now(UTC).date()
    tok = token.strip()

    candidates, how = _candidates(tok, animals)
    if not candidates:
        return ResolvedAnimal(
            token=tok,
            found=False,
            id_field=tok,
            warnings=["not found in cache (refresh cache, or use full VID/name)"],
        )

    # Prefer an on-farm animal, but flag that more than one matched.
    match = next((a for a in candidates if is_on_farm(a)), candidates[0])
    warnings: list[str] = []
    if len(candidates) > 1:
        warnings.append(f"ambiguous match - {len(candidates)} animals share this {how}, confirm the right one")

    ident = _identity(match)
    taxon_suffix, family, breed_id, tax_warn = _taxon(match, by_id)
    sex_label, sex_id = sex_code(match)
    age, unit, uom_id, age_warn = age_fields(match, today)
    warnings.extend(w for w in (tax_warn, age_warn) if w)

    return ResolvedAnimal(
        token=tok,
        matched_by=how,
        animal_id=match.get("animalId"),
        name=ident.get("name") or ident.get("vid"),
        id_field=tok,
        taxon=TAXON_PREFIX + taxon_suffix,
        taxon_family=family,
        breed_id=breed_id,
        sex_code=sex_label,
        sex_code_id=sex_id,
        age=age,
        age_unit=unit,
        age_uom_id=uom_id,
        health_flag=ident.get("managementTag"),
        warnings=warnings,
    )


def resolve(
    tokens: list[str],
    today: date | None = None,
) -> list[ResolvedAnimal]:
    """Resolve a batch of tokens against the cached ``animals.json``.

    Duplicate tokens within the batch are flagged (matching is case- and
    whitespace-insensitive, so ``"0693"`` and ``" 0693 "`` collide).
    """
    animals = load_cache_json("animals.json", key="animals", default=[])
    by_id = {a["animalId"]: a for a in animals}

    seen: set[str] = set()
    out: list[ResolvedAnimal] = []
    for tok in tokens:
        r = resolve_token(tok, animals, by_id, today=today)
        key = tok.strip().lower()
        if key in seen:
            r.warnings.append("duplicate in this batch")
        seen.add(key)
        out.append(r)
    return out


# ---------------------------------------------------------------------------
# Sex
# ---------------------------------------------------------------------------
def sex_code(animal: dict) -> tuple[str, int]:
    """Map AgriWebb sex + age class to a WADDL (label, sex_code_id).

    A castrated male (``ageClass`` contains 'wether') is Male Neutered (C),
    even though its ``sex`` is still 'Male'.
    """
    if "wether" in get_age_class(animal).lower():
        return SEX_MALE_NEUTERED
    sex = get_sex(animal).lower()
    if sex == "female":
        return SEX_FEMALE
    if sex == "male":
        return SEX_MALE
    return SEX_UNKNOWN


# ---------------------------------------------------------------------------
# Age
# ---------------------------------------------------------------------------
def _birth_date(animal: dict) -> date | None:
    ms = (animal.get("characteristics") or {}).get("birthDate")
    if not ms:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=UTC).date()


def age_fields(animal: dict, today: date) -> tuple[int | None, str | None, int | None, str | None]:
    """Return (age, unit, age_uom_id, warning).

    Whole-number age; months when under 1.5 years old, else years. Falls back
    to ``birthYear`` (years only) when a precise birth date is missing.
    """
    dob = _birth_date(animal)
    if dob is None:
        by = (animal.get("characteristics") or {}).get("birthYear")
        if by:
            return round(today.year - by), "years", AGE_UOM["years"], "age approximated from birthYear (no birth date)"
        return None, None, None, "no birth date or birth year - set age manually"

    days = (today - dob).days
    years = days / _DAYS_PER_YEAR
    if years < MONTHS_CUTOFF_YEARS:
        months = round(days / _DAYS_PER_MONTH)
        return months, "months", AGE_UOM["months"], None
    return round(years), "years", AGE_UOM["years"], None


# ---------------------------------------------------------------------------
# Breed / taxon (with pedigree-based cross resolution)
# ---------------------------------------------------------------------------
def base_family(breed: str | None) -> str | None:
    """Map an AgriWebb breed string to a base family, or None if it's a cross.

    Returns None for crosses so they route through the pedigree: the farm's
    ``"<Breed> X"`` notation (e.g. 'Bluefaced Leicester X') is a first-cross
    designation, NOT a purebred, and ``"1st Cross"`` etc. are obviously crosses.
    Handles the cache's 'Finish Landrace' typo.
    """
    if not breed:
        return None
    b = breed.strip().lower()
    if b.endswith(" x") or "cross" in b:
        return None  # crossbred -> resolve via pedigree
    if "finn" in b or "finish landrace" in b or "finnish landrace" in b:
        return "Finn"
    if "cheviot" in b:
        return "NCC"
    if "bluefaced" in b or "bfl" in b:
        return "BFL"
    return None  # unknown -> resolve via pedigree


def _named_family(breed: str | None) -> str | None:
    """Family named in a '<Breed> X' cross label (the X ignored) - a hint only."""
    if not breed:
        return None
    b = breed.lower()
    if "finn" in b or "landrace" in b:
        return "Finn"
    if "cheviot" in b:
        return "NCC"
    if "bluefaced" in b or "bfl" in b:
        return "BFL"
    return None


def _composition(animal: dict | None, by_id: dict[str, dict], depth: int = 4) -> dict[str, float]:
    """Fractional base-breed makeup, recursing through the pedigree.

    A parent with a known base breed contributes 1.0 of that family; a
    cross/unknown parent recurses. **Each of the two parent slots is weighted
    0.5 regardless of whether it resolves**, so a missing/off-cache parent
    leaves the returned fractions summing to < 1.0 - the caller reads that
    shortfall as an incomplete pedigree.
    """
    if animal is None or depth < 0:
        return {}
    fam = base_family(get_breed(animal))
    if fam:
        return {fam: 1.0}
    out: dict[str, float] = defaultdict(float)
    for parent_id in (get_sire_id(animal), get_dam_id(animal)):
        sub = _composition(by_id.get(parent_id or ""), by_id, depth - 1)
        total = sum(sub.values())
        if total > 0:
            for fam, frac in sub.items():
                out[fam] += 0.5 * frac / total
        # else: this 0.5 slot is unknown and intentionally left out of the sum
    return dict(out)


def _dam_family(animal: dict, by_id: dict[str, dict], depth: int = 4) -> str | None:
    """Dominant base family of the maternal line (recurses if the dam is a cross)."""
    comp = _composition(by_id.get(get_dam_id(animal) or ""), by_id, depth - 1)
    return max(comp, key=comp.get) if comp else None


def _taxon(animal: dict, by_id: dict[str, dict]) -> tuple[str, str, int, str | None]:
    """Return (taxon_suffix, family, breed_id, warning) for an animal.

    Purebreds map directly. Crosses use the dominant pedigree fraction; a
    near-tie breaks toward the dam's breed (then Finn, then the highest-ranked
    candidate). Incomplete pedigrees and near-ties are flagged.
    """
    fam = base_family(get_breed(animal))
    if fam:
        suffix, breed_id = FAMILY_TAXON[fam]
        return suffix, fam, breed_id, None

    comp = _composition(animal, by_id)
    if not comp:
        named = _named_family(get_breed(animal))
        best = named or DEFAULT_FAMILY
        suffix, breed_id = FAMILY_TAXON[best]
        why = f"used the breed named on the label ({best})" if named else "defaulted to Finn"
        return suffix, best, breed_id, f"cross with no pedigree - {why}, confirm"

    # deterministic ranking: highest fraction, then family name
    ranked = sorted(comp.items(), key=lambda kv: (-kv[1], kv[0]))
    comp_str = " / ".join(f"{fam} {round(frac * 100)}%" for fam, frac in ranked)
    warnings: list[str] = []

    known_mass = sum(comp.values())
    if known_mass < KNOWN_MASS_MIN:
        warnings.append(f"incomplete pedigree [{comp_str}], ~{round((1 - known_mass) * 100)}% unknown - confirm breed")

    if len(ranked) > 1 and ranked[0][1] - ranked[1][1] < TIE_THRESHOLD:
        dam = _dam_family(animal, by_id)
        if dam and dam in comp:
            best = dam
            warnings.append(f"near-tie [{comp_str}] - broke to dam's breed ({best}), confirm")
        else:
            best = DEFAULT_FAMILY if DEFAULT_FAMILY in comp else ranked[0][0]
            warnings.append(f"near-tie [{comp_str}], dam unknown -> {best}, confirm")
    else:
        best = ranked[0][0]

    suffix, breed_id = FAMILY_TAXON[best]
    return suffix, best, breed_id, "; ".join(warnings) if warnings else None
