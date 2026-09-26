"""Resolve vial tokens into WADDL per-animal submission fields.

A vial is identified on the farm by the **last 4 digits of its EID**, or
sometimes a VID (full or last 4) or a name when the tag is missing/unreadable. This module maps
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
the highest-ranked candidate (alphabetical among equals).

Anything uncertain is flagged in ``warnings`` rather than filled silently:
a not-found / ambiguous / off-farm match, an unknown sex, an approximated or
unknown age, a duplicate token in the batch, and three distinct breed cases -
a *cross with no pedigree* (defaults to the label's named breed, else Finn), an
*incomplete pedigree* (a parent off-cache, so breed is only partly known), and a
*near-tie* between families. Callers must surface every warning before filling.
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

# Fixed vocabularies, aliased so the dataclass fields and the functions that
# produce them share one definition and can't drift (keeps ``ty`` honest).
MatchChannel = Literal["EID last-4", "VID", "VID last-4", "name"]
AgeUnit = Literal["years", "months"]

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
    matched_by: MatchChannel | None = None
    animal_id: str | None = None
    name: str | None = None
    id_field: str | None = None  # what to type into the portal ID box (== token)
    taxon: str | None = None
    taxon_family: str | None = None
    breed_id: int | None = None
    sex_code: str | None = None
    sex_code_id: int | None = None
    age: int | None = None
    age_unit: AgeUnit | None = None
    age_uom_id: int | None = None
    health_flag: str | None = None  # e.g. "CL+", "Johne's+"
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------
def _identity(animal: dict) -> dict:
    return animal.get("identity") or {}


def match_candidates(token: str, animals: list[dict]) -> tuple[list[dict], MatchChannel | None]:
    """All animals matching *token*, and the channel that matched.

    Match order: last-4 of EID, then exact VID, then last-4 of VID (lambs
    carry VIDs like ``26055`` and are called by ``6055``), then exact name
    (case-insensitive for VID/name). Returns every candidate on the winning
    channel so the caller can flag ambiguity.
    """
    if not token:
        return [], None
    low = token.lower()
    by_eid = [a for a in animals if (_identity(a).get("eid") or "")[-4:] == token]
    if by_eid:
        return by_eid, "EID last-4"
    # `low` is non-empty (guarded above), so an equality match already implies a
    # truthy field - no extra `and .get(...)` guard needed.
    by_vid = [a for a in animals if str(_identity(a).get("vid") or "").lower() == low]
    if by_vid:
        return by_vid, "VID"
    if len(token) == 4:
        by_vid4 = [a for a in animals if len(vid := str(_identity(a).get("vid") or "")) > 4 and vid[-4:].lower() == low]
        if by_vid4:
            return by_vid4, "VID last-4"
    by_name = [a for a in animals if str(_identity(a).get("name") or "").lower() == low]
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

    candidates, how = match_candidates(tok, animals)
    if not candidates:
        return ResolvedAnimal(
            token=tok,
            found=False,
            id_field=tok,
            warnings=["not found in cache (refresh cache, or use full VID/name)"],
        )

    # Prefer an on-farm animal, but flag both ambiguity and an off-farm match.
    match = next((a for a in candidates if is_on_farm(a)), candidates[0])
    warnings: list[str] = []
    if len(candidates) > 1:
        warnings.append(f"ambiguous match - {len(candidates)} animals share this {how}, confirm the right one")
    if not is_on_farm(match):
        warnings.append("matched animal is not marked on-farm (sold/dead?) - confirm this is the right animal")

    ident = _identity(match)
    taxon_suffix, family, breed_id, tax_warn = _taxon(match, by_id)
    sex_label, sex_id = sex_code(match)
    sex_warn = "sex unknown in cache - confirm and set manually" if sex_id == SEX_UNKNOWN[1] else None
    age, unit, uom_id, age_warn = age_fields(match, today)
    warnings.extend(w for w in (tax_warn, sex_warn, age_warn) if w)

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
    # Skip malformed records rather than KeyError-ing the whole batch over one.
    by_id = {aid: a for a in animals if (aid := a.get("animalId"))}

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
    even though its ``sex`` is still 'Male'. A missing/unrecognized sex returns
    Unknown (U); ``resolve_token`` warns on that so it is never filled silently.
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


def age_fields(animal: dict, today: date) -> tuple[int | None, AgeUnit | None, int | None, str | None]:
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
# family -> keywords that identify it in a breed string ("landrace" covers both
# 'Finnish Landrace' and the cache's 'Finish Landrace' typo). Order = precedence.
_FAMILY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "Finn": ("finn", "landrace"),
    "NCC": ("cheviot",),
    "BFL": ("bluefaced", "bfl"),
}


def _match_family(breed_lower: str) -> str | None:
    """First base family whose keyword appears in a lowercased breed string."""
    return next((fam for fam, kws in _FAMILY_KEYWORDS.items() if any(kw in breed_lower for kw in kws)), None)


def _is_cross_label(breed_lower: str) -> bool:
    """True for the farm's cross notations: '<Breed> X' or anything with 'cross'."""
    return breed_lower.endswith(" x") or "cross" in breed_lower


def base_family(breed: str | None) -> str | None:
    """Map an AgriWebb breed string to a base family, or None if it's a cross.

    Returns None for crosses so they route through the pedigree: the farm's
    ``"<Breed> X"`` notation (e.g. 'Bluefaced Leicester X') is a first-cross
    designation, NOT a purebred, and ``"1st Cross"`` etc. are obviously crosses.
    Also returns None for an unrecognized breed (routes through the pedigree).
    """
    if not breed:
        return None
    b = breed.strip().lower()
    return None if _is_cross_label(b) else _match_family(b)


def _named_family(breed: str | None) -> str | None:
    """Family named in a '<Breed> X' cross label (the X ignored) - a hint only."""
    return _match_family(breed.lower()) if breed else None


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
        # Scale each parent slot by 0.5 WITHOUT renormalizing: sub already carries
        # its own known mass (<= 1.0), so a partially-unknown ancestor propagates
        # its shortfall upward instead of being inflated to fill the slot.
        for fam, frac in sub.items():
            out[fam] += 0.5 * frac
        # a fully-missing parent contributes nothing -> total stays < 1.0
    return dict(out)


def _dam_family(animal: dict, by_id: dict[str, dict], depth: int = 4) -> str | None:
    """Dominant base family of the maternal line (recurses if the dam is a cross)."""
    comp = _composition(by_id.get(get_dam_id(animal) or ""), by_id, depth - 1)
    return max(comp, key=comp.__getitem__) if comp else None


def _taxon(animal: dict, by_id: dict[str, dict]) -> tuple[str, str, int, str | None]:
    """Return (taxon_suffix, family, breed_id, warning) for an animal.

    Purebreds map directly (via ``_composition``'s ``{fam: 1.0}`` base case).
    Crosses use the dominant pedigree fraction; a near-tie breaks toward the
    dam's breed (then Finn, then the highest-ranked candidate). A cross/unmapped
    breed with no usable pedigree, an incomplete pedigree, and near-ties are all
    flagged.
    """
    comp = _composition(animal, by_id)
    if not comp:
        # No pedigree at all: purebred (single family, 1.0), cross, or unmapped.
        breed = get_breed(animal)
        named = _named_family(breed)
        best = named or DEFAULT_FAMILY
        suffix, breed_id = FAMILY_TAXON[best]
        b = (breed or "").strip().lower()
        if named:
            warn = f"cross with no pedigree - used the breed named on the label ({best}), confirm"
        elif b and not _is_cross_label(b):
            warn = f"unrecognized breed '{breed}' with no pedigree - defaulted to Finn, confirm"
        else:
            warn = "cross with no pedigree - defaulted to Finn, confirm"
        return suffix, best, breed_id, warn

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
