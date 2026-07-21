"""Tests for the WADDL vial-token resolver."""

from __future__ import annotations

from datetime import UTC, date, datetime

import agriwebb.waddl.resolver as waddl
from agriwebb.waddl.resolver import ResolvedAnimal, resolve_token

AS_OF = date(2026, 7, 20)


def _ms(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp() * 1000)


def animal(
    animal_id: str,
    *,
    eid: str | None = None,
    vid: str | None = None,
    name: str | None = None,
    breed: str = "Finnsheep",
    sex: str = "Female",
    age_class: str = "ewe",
    birth: date | None = None,
    birth_year: int | None = None,
    tag: str | None = None,
    sire_id: str | None = None,
    dam_id: str | None = None,
    on_farm: bool = False,
) -> dict:
    def _parent(pid):
        return [{"parentAnimalId": pid, "parentAnimalIdentity": {}}] if pid else []

    return {
        "animalId": animal_id,
        "identity": {"eid": eid, "vid": vid, "name": name, "managementTag": tag},
        "characteristics": {
            "breedAssessed": breed,
            "sex": sex,
            "ageClass": age_class,
            "birthDate": _ms(birth) if birth else None,
            "birthYear": birth_year,
        },
        "parentage": {"sires": _parent(sire_id), "dams": _parent(dam_id)},
        "state": {"onFarm": on_farm},
    }


def _resolve(tok: str, animals: list[dict]) -> ResolvedAnimal:
    by_id = {a["animalId"]: a for a in animals}
    return resolve_token(tok, animals, by_id, today=AS_OF)


# --- matching ---------------------------------------------------------------
def test_matches_eid_last_four():
    a = animal("A", eid="840003288760693", name="Nutcracker", breed="North Country Cheviot", birth=date(2024, 7, 21))
    r = _resolve("0693", [a])
    assert r.found and r.matched_by == "EID last-4"
    assert r.name == "Nutcracker"
    assert r.id_field == "0693"


def test_matches_vid_case_insensitive():
    a = animal("A", vid="WW36", name="Brie")
    assert _resolve("ww36", [a]).matched_by == "VID"


def test_matches_name():
    a = animal("A", name="Camas")
    assert _resolve("camas", [a]).matched_by == "name"


def test_eid_last_four_beats_vid_and_name():
    target = animal("A", eid="840000000000693")
    other = animal("B", vid="0693")
    r = _resolve("0693", [target, other])
    assert r.animal_id == "A"


def test_not_found():
    r = _resolve("9999", [animal("A", eid="840000000001234")])
    assert not r.found
    assert r.id_field == "9999"  # still typed verbatim
    assert r.warnings


# --- sex --------------------------------------------------------------------
def test_ewe_is_female():
    assert _resolve("A", [animal("A", name="A", sex="Female", age_class="ewe")]).sex_code == "Female (F)"


def test_ram_is_male():
    r = _resolve("A", [animal("A", name="A", sex="Male", age_class="ram")])
    assert (r.sex_code, r.sex_code_id) == ("Male (M)", 3)


def test_wether_is_male_neutered():
    r = _resolve("A", [animal("A", name="A", sex="Male", age_class="wether_hogget")])
    assert (r.sex_code, r.sex_code_id) == ("Male Neutered (C)", 5)


# --- age --------------------------------------------------------------------
def test_adult_age_in_years():
    r = _resolve("A", [animal("A", name="A", birth=date(2023, 3, 12))])
    assert (r.age, r.age_unit, r.age_uom_id) == (3, "years", 21)


def test_hogget_under_18_months_in_months():
    r = _resolve("A", [animal("A", name="A", birth=date(2025, 4, 27))])
    assert r.age_unit == "months"
    assert r.age == 15


def test_age_falls_back_to_birth_year():
    r = _resolve("A", [animal("A", name="A", birth=None, birth_year=2022)])
    assert (r.age, r.age_unit) == (4, "years")
    assert any("birthYear" in w for w in r.warnings)


# --- breed / taxon ----------------------------------------------------------
def test_purebred_finn_maps_to_finnish_landrace():
    r = _resolve("A", [animal("A", name="A", breed="Finnsheep", birth=date(2023, 1, 1))])
    assert r.taxon.endswith("Finnish-Landrace")
    assert r.taxon_family == "Finn" and r.breed_id == 3569
    assert not r.warnings


def test_finish_landrace_typo_maps_to_finn():
    assert _resolve("A", [animal("A", name="A", breed="Finish Landrace")]).taxon_family == "Finn"


def test_ncc_and_bfl_ids():
    ncc = _resolve("A", [animal("A", name="A", breed="North Country Cheviot")])
    bfl = _resolve("B", [animal("B", name="B", breed="Bluefaced Leicester")])
    assert (ncc.taxon_family, ncc.breed_id) == ("NCC", 3591)
    assert (bfl.taxon_family, bfl.breed_id) == ("BFL", 3547)


def test_cross_with_clear_dominant_breed_no_warning():
    # sire Finn, dam is a Finn x (Finn/BFL) -> Finn dominates well past the tie threshold
    sire = animal("S", breed="Finnsheep")
    granddam = animal("GD", breed="Bluefaced Leicester")
    grandsire = animal("GS", breed="Finnsheep")
    dam = animal("D", breed="1st Cross", sire_id="GS", dam_id="GD")
    cross = animal("X", name="X", breed="1st Cross", sire_id="S", dam_id="D", birth=date(2023, 1, 1))
    r = _resolve("X", [cross, sire, dam, granddam, grandsire])
    assert r.taxon_family == "Finn"
    assert not r.warnings  # 75/25 is not a tie (and animal has a birth date)


def test_cross_tie_breaks_to_dams_breed():
    sire = animal("S", breed="Finnsheep")
    dam = animal("D", breed="Bluefaced Leicester")
    cross = animal("X", name="X", breed="1st Cross", sire_id="S", dam_id="D")
    r = _resolve("X", [cross, sire, dam])
    assert r.taxon_family == "BFL"  # 50/50 -> dam's breed
    assert any("dam" in w.lower() for w in r.warnings)


def test_cross_tie_dam_finn_stays_finn():
    sire = animal("S", breed="North Country Cheviot")
    dam = animal("D", breed="Finnsheep")
    cross = animal("X", name="X", breed="1st Cross", sire_id="S", dam_id="D")
    r = _resolve("X", [cross, sire, dam])
    assert r.taxon_family == "Finn"
    assert r.warnings


def test_cross_no_pedigree_defaults_to_finn_with_warning():
    r = _resolve("X", [animal("X", name="X", breed="1st Cross")])
    assert r.taxon_family == "Finn"
    assert any("no pedigree" in w for w in r.warnings)


# --- health flag ------------------------------------------------------------
def test_health_flag_surfaced():
    r = _resolve("A", [animal("A", name="A", tag="CL+")])
    assert r.health_flag == "CL+"


# --- crossbred "<Breed> X" notation (Critical #1) ---------------------------
def test_breed_x_suffix_is_not_a_purebred():
    # "Bluefaced Leicester X" / "North Country Cheviot X" are first-cross labels,
    # not purebreds -> base_family must route them through the pedigree.
    assert waddl.base_family("Bluefaced Leicester X") is None
    assert waddl.base_family("North Country Cheviot X") is None
    # a real purebred (no " X") still maps
    assert waddl.base_family("Bluefaced Leicester") == "BFL"


def test_breed_x_no_pedigree_defaults_to_named_breed():
    # "Bluefaced Leicester X" with no cached parents -> default to BFL (the named
    # breed), flagged, rather than throwing the signal away and defaulting to Finn.
    r = _resolve("X", [animal("X", name="X", breed="Bluefaced Leicester X", birth=date(2023, 1, 1))])
    assert r.taxon_family == "BFL"
    assert any("no pedigree" in w for w in r.warnings)


def test_breed_x_suffix_resolves_via_pedigree_not_silently_pure():
    # BFL-X ewe by a Finn sire out of a BFL dam -> pedigree tie, dam's breed, flagged
    sire = animal("S", breed="Finnsheep")
    dam = animal("D", breed="Bluefaced Leicester")
    x = animal("X", name="X", breed="Bluefaced Leicester X", sire_id="S", dam_id="D")
    r = _resolve("X", [x, sire, dam])
    assert r.taxon_family == "BFL"  # from the dam via pedigree, not the " X" substring
    assert r.warnings  # not filled silently


# --- incomplete pedigree (Critical #2) --------------------------------------
def test_single_missing_parent_flags_incomplete_pedigree():
    # sire known Finn, dam absent from cache -> only ~50% breed mass is known
    sire = animal("S", breed="Finnsheep")
    x = animal("X", name="X", breed="1st Cross", sire_id="S", dam_id="MISSING", birth=date(2023, 1, 1))
    r = _resolve("X", [x, sire])  # dam "MISSING" not in list
    assert r.taxon_family == "Finn"
    assert any("incomplete pedigree" in w for w in r.warnings)


# --- ambiguous match (Critical #3) ------------------------------------------
def test_ambiguous_name_flags_and_prefers_on_farm():
    off = animal("OFF", name="Maple", breed="North Country Cheviot", on_farm=False)
    on = animal("ON", name="Maple", breed="Finnsheep", on_farm=True, birth=date(2023, 1, 1))
    r = _resolve("Maple", [off, on])
    assert r.animal_id == "ON"  # prefers the on-farm animal
    assert any("ambiguous" in w for w in r.warnings)


# --- found-but-incomplete: no birth date and no birth year (Important #4) ----
def test_found_but_age_unknown():
    r = _resolve("A", [animal("A", name="A", birth=None, birth_year=None)])
    assert r.found is True
    assert r.age is None and r.age_unit is None
    assert any("manually" in w for w in r.warnings)


# --- tie with unknown dam, Finn absent (Important #5) -----------------------
def test_tie_dam_unknown_finn_absent_is_deterministic():
    # X's sire is itself an NCC/BFL cross; X's dam is off-cache. The known
    # composition is a NCC/BFL near-tie with an unresolvable dam and no Finn ->
    # the fallback must pick deterministically (alphabetical: BFL) and flag it.
    gsire = animal("GS", breed="North Country Cheviot")
    gdam = animal("GD", breed="Bluefaced Leicester")
    sire = animal("S", breed="1st Cross", sire_id="GS", dam_id="GD")
    x = animal("X", name="X", breed="1st Cross", sire_id="S", dam_id="OFFCACHE", birth=date(2023, 1, 1))
    r = _resolve("X", [x, sire, gsire, gdam])
    assert r.taxon_family == "BFL"  # deterministic, not dict-order-dependent
    assert any("dam unknown" in w for w in r.warnings)


# --- age boundary + unknown sex + empty token -------------------------------
def test_age_boundary_just_under_18_months_is_months():
    # ~547 days (< 1.5yr) -> months
    r = _resolve("A", [animal("A", name="A", birth=date(2025, 1, 20))])
    assert r.age_unit == "months"


def test_age_boundary_just_over_18_months_is_years():
    # ~2 years -> years
    r = _resolve("A", [animal("A", name="A", birth=date(2024, 7, 20))])
    assert r.age_unit == "years"


def test_unknown_sex_maps_to_unknown():
    r = _resolve("A", [animal("A", name="A", sex="", age_class="")])
    assert (r.sex_code, r.sex_code_id) == ("Unknown (U)", 1)


def test_empty_token_is_not_found():
    r = _resolve("", [animal("A", name="A", vid="")])
    assert not r.found


# --- batch resolve + dedup --------------------------------------------------
def test_resolve_flags_duplicate_tokens(monkeypatch):
    animals = [animal("A", eid="840000000000693", name="Nutcracker", birth=date(2024, 1, 1))]
    monkeypatch.setattr("agriwebb.waddl.resolver.load_cache_json", lambda *a, **k: animals)
    results = waddl.resolve(["0693", "0693"], today=AS_OF)
    assert len(results) == 2
    assert not any("duplicate" in w for w in results[0].warnings)
    assert any("duplicate" in w for w in results[1].warnings)


def test_resolve_dedup_is_case_and_whitespace_insensitive(monkeypatch):
    animals = [animal("A", name="Camas", birth=date(2023, 1, 1))]
    monkeypatch.setattr("agriwebb.waddl.resolver.load_cache_json", lambda *a, **k: animals)
    results = waddl.resolve(["Camas", " camas "], today=AS_OF)
    assert any("duplicate" in w for w in results[1].warnings)
