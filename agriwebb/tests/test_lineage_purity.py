"""Tests for lineage-derived breed purity (``agriwebb.analysis.lineage.purity``).

The AgriWebb breed label is a per-animal free-text pick from a fixed list and
is used loosely on-farm (a predominantly-Finn cross is often labelled "Finish
Landrace").  Purity must therefore be *derived* from recorded parentage back to
founder animals, whose breed status is asserted by the shepherd in a registry.
"""

from __future__ import annotations

import json

import pytest
from conftest import make_animal as _animal
from conftest import make_parent as _parent

from agriwebb.analysis.lambing.loader import find_animal
from agriwebb.analysis.lineage.purity import (
    Founder,
    FounderRegistry,
    PurityResult,
    RegistryError,
    canonical_breed,
    compute_purity,
    discover_founders,
    find_purebred,
    normalize_key,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ext(name: str | None = None, vid: str | None = None) -> dict:
    """An off-cache (external) parent reference: no parentAnimalId."""
    return {
        "parentAnimalId": None,
        "parentAnimalIdentity": {"name": name, "vid": vid, "eid": None},
        "parentType": "Genetic",
    }


def _by_id(*animals: dict) -> dict[str, dict]:
    return {a["animalId"]: a for a in animals}


def _registry(*founders: Founder) -> FounderRegistry:
    return FounderRegistry(list(founders))


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


class TestNormalisation:
    def test_normalize_key_strips_case_and_punctuation(self):
        assert normalize_key("Blue Man") == "BLUEMAN"
        assert normalize_key("BLUEMAN") == "BLUEMAN"
        assert normalize_key("Lady Jane Jr.") == "LADYJANEJR"
        assert normalize_key("  ww02 ") == "WW02"

    def test_normalize_key_handles_none_and_empty(self):
        assert normalize_key(None) == ""
        assert normalize_key("") == ""

    def test_canonical_breed_fixes_agriwebb_typo(self):
        assert canonical_breed("Finish Landrace") == "Finnish Landrace"
        assert canonical_breed("Finnish Landrace") == "Finnish Landrace"
        assert canonical_breed("finnsheep") == "Finnish Landrace"

    def test_canonical_breed_passthrough(self):
        assert canonical_breed("North Country Cheviot") == "North Country Cheviot"
        assert canonical_breed("Bluefaced Leicester X") == "Bluefaced Leicester X"
        assert canonical_breed(None) == "?"

    def test_canonical_breed_farm_cross_shorthand(self):
        assert canonical_breed("bflx") == "Bluefaced Leicester X"
        assert canonical_breed("BFL X") == "Bluefaced Leicester X"
        assert canonical_breed("Finn X") == "Finnish Landrace X"
        assert canonical_breed("NCC X") == "North Country Cheviot X"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class TestFounderRegistry:
    def test_match_label_by_key_and_alias_case_insensitively(self):
        reg = _registry(Founder(id="BLUEMAN", aliases=["Blue Man"], breed="Bluefaced Leicester", purebred=True))
        assert reg.match_label("Blueman") is not None
        assert reg.match_label("blue man") is not None
        assert reg.match_label("Orion") is None

    def test_match_animal_id(self):
        f = Founder(id="Sansa", animal_id="a-sansa", breed="North Country Cheviot", purebred=True)
        reg = _registry(f)
        assert reg.match_animal_id("a-sansa") is f
        assert reg.match_animal_id("nope") is None

    def test_composition_for_purebred_founder(self):
        f = Founder(id="YR2015", breed="Finnish Landrace", purebred=True)
        assert f.get_composition() == {"Finnish Landrace": 1.0}

    def test_composition_for_known_cross_with_fractions(self):
        f = Founder(
            id="Ladle",
            breed="1st Cross",
            purebred=False,
            composition={"Finnish Landrace": 0.5, "Bluefaced Leicester": 0.5},
        )
        assert f.get_composition() == {"Finnish Landrace": 0.5, "Bluefaced Leicester": 0.5}

    def test_composition_unknown_when_unclassified(self):
        assert Founder(id="X").get_composition() == {"unknown": 1.0}
        assert Founder(id="X", purebred=False).get_composition() == {"unknown": 1.0}

    def test_known_cross_with_breed_label_contributes_that_label(self):
        f = Founder(id="WW02", breed="Bluefaced Leicester X", purebred=False)
        assert f.get_composition() == {"Bluefaced Leicester X": 1.0}

    def test_generic_cross_label_is_still_unknown(self):
        assert Founder(id="X", breed="1st Cross", purebred=False).get_composition() == {"unknown": 1.0}
        assert Founder(id="X", breed="2nd Cross", purebred=False).get_composition() == {"unknown": 1.0}

    def test_composition_fractions_are_normalised(self):
        f = Founder(id="X", purebred=False, composition={"A": 0.3, "B": 0.3})
        assert f.get_composition() == {"A": pytest.approx(0.5), "B": pytest.approx(0.5)}

    def test_all_zero_composition_is_unknown(self):
        assert Founder(id="X", purebred=False, composition={"A": 0.0}).get_composition() == {"unknown": 1.0}

    def test_purebred_requires_breed(self):
        with pytest.raises(RegistryError, match="requires 'breed'"):
            Founder(id="X", purebred=True, breed=None)

    def test_negative_fraction_rejected(self):
        with pytest.raises(RegistryError, match=">= 0"):
            Founder(id="X", purebred=False, composition={"A": -0.1})

    def test_alias_collision_raises(self):
        with pytest.raises(RegistryError, match="alias 'BLUEMAN'"):
            _registry(Founder(id="BLUEMAN"), Founder(id="Stan", aliases=["Blue Man"]))

    def test_animal_id_collision_raises(self):
        with pytest.raises(RegistryError, match="animalId a1"):
            _registry(Founder(id="A", animal_id="a1"), Founder(id="B", animal_id="a1"))

    def test_load_accepts_bare_list(self, tmp_path):
        p = tmp_path / "f.json"
        p.write_text(json.dumps([{"id": "YR2015", "breed": "Finnish Landrace", "purebred": True}]))
        assert FounderRegistry.load(p).match_label("yr2015").purebred is True

    def test_load_missing_file_is_empty(self, tmp_path):
        assert FounderRegistry.load(tmp_path / "nope.json").founders == []

    def test_load_invalid_json_raises(self, tmp_path):
        p = tmp_path / "f.json"
        p.write_text('{"founders": [')
        with pytest.raises(RegistryError, match="invalid JSON"):
            FounderRegistry.load(p)

    def test_load_entry_missing_id_raises(self, tmp_path):
        p = tmp_path / "f.json"
        p.write_text(json.dumps({"founders": [{"breed": "Finnish Landrace"}]}))
        with pytest.raises(RegistryError, match="missing 'id'"):
            FounderRegistry.load(p)

    def test_registry_copies_founder_list(self):
        lst = [Founder(id="A")]
        reg = _registry(*lst)
        lst.append(Founder(id="B"))
        assert [f.id for f in reg.founders] == ["A"]

    def test_round_trip_json(self, tmp_path):
        path = tmp_path / "founders.json"
        reg = _registry(
            Founder(id="YR2015", breed="Finnish Landrace", purebred=True, notes="reg. Finn ram"),
            Founder(id="Sansa", animal_id="a-sansa", breed="North Country Cheviot", purebred=None),
        )
        reg.save(path)
        loaded = FounderRegistry.load(path)
        assert [f.id for f in loaded.founders] == ["YR2015", "Sansa"]
        assert loaded.match_label("yr2015").purebred is True
        assert loaded.match_animal_id("a-sansa").purebred is None
        # File is plain JSON a human can edit
        raw = json.loads(path.read_text())
        assert raw["founders"][0]["id"] == "YR2015"

    def test_default_registry_loads(self):
        # The packaged registry must always parse, even if every entry is unclassified.
        reg = FounderRegistry.load()
        assert isinstance(reg.founders, list)


# ---------------------------------------------------------------------------
# Purity computation
# ---------------------------------------------------------------------------


@pytest.fixture
def finn_reg():
    return _registry(
        Founder(id="YR2015", breed="Finnish Landrace", purebred=True),
        Founder(id="Dube", breed="Finnish Landrace", purebred=True),
        Founder(id="Luann", breed="Finnish Landrace", purebred=True),
        Founder(id="BLUEMAN", aliases=["Blue Man"], breed="Bluefaced Leicester", purebred=True),
        Founder(id="WW02", breed="Bluefaced Leicester X", purebred=False),
    )


class TestComputePurity:
    def test_verified_purebred_through_three_generations(self, finn_reg):
        anise = _animal("anise", name="Anise", breed="Finish Landrace", sires=[_ext("Dube")], dams=[_ext("Luann")])
        madrigal = _animal(
            "madrigal",
            name="Madrigal",
            breed="Finish Landrace",
            sex="Male",
            sires=[_ext("YR2015")],
            dams=[_parent("anise", "Anise")],
        )
        jazz = _animal(
            "jazz", name="Jazz", breed="Finish Landrace", sires=[_ext("YR2015")], dams=[_parent("anise", "Anise")]
        )
        lamb = _animal(
            "lamb",
            name="Bocce",
            breed="Finish Landrace",
            sires=[_parent("madrigal", "Madrigal")],
            dams=[_parent("jazz", "Jazz")],
        )
        r = compute_purity("lamb", _by_id(anise, madrigal, jazz, lamb), finn_reg)
        assert r.purebred is True
        assert r.purebred_breed == "Finnish Landrace"
        assert r.status == "verified"
        assert r.composition == {"Finnish Landrace": 1.0}
        assert r.unresolved == []
        assert r.gaps == []
        assert sorted(r.founders) == ["Dube", "Luann", "YR2015"]

    def test_mixed_is_conclusively_not_purebred_even_with_unresolved_elsewhere(self, finn_reg):
        # Dam side: Solar (unresolved founders) x Morgan (BLUEMAN x WW02 cross) -> definitely not pure
        morgan = _animal(
            "morgan", name="Morgan", breed="Bluefaced Leicester X", sires=[_ext("Blue Man")], dams=[_ext(vid="WW02")]
        )
        solar = _animal(
            "solar", name="Solar", breed="Finish Landrace", sex="Male", sires=[_ext("Conroy")], dams=[_ext("Delia")]
        )
        sonata = _animal("sonata", name="Sonata", breed="1st Cross", sires=[_parent("solar")], dams=[_parent("morgan")])
        madrigal = _animal(
            "madrigal",
            name="Madrigal",
            breed="Finish Landrace",
            sex="Male",
            sires=[_ext("YR2015")],
            dams=[_ext("Luann")],
        )
        bingo = _animal(
            "bingo", name="Bingo", breed="Finish Landrace", sires=[_parent("madrigal")], dams=[_parent("sonata")]
        )
        r = compute_purity("bingo", _by_id(morgan, solar, sonata, madrigal, bingo), finn_reg)
        assert r.purebred is False
        assert r.purebred_breed is None
        assert r.status == "mixed"
        # Composition: 1/2 Madrigal (all Finn) + 1/4 Solar (unknown) + 1/8 BLUEMAN (BFL) + 1/8 WW02 (BFL X)
        assert r.composition["Finnish Landrace"] == pytest.approx(0.5)
        assert r.composition["Bluefaced Leicester"] == pytest.approx(0.125)
        assert r.composition["Bluefaced Leicester X"] == pytest.approx(0.125)
        assert r.composition["unknown"] == pytest.approx(0.25)
        assert sorted(r.unresolved) == ["Conroy", "Delia"]

    def test_unresolved_founder_makes_purity_unverified(self, finn_reg):
        a = _animal("a", name="A", breed="Finish Landrace", sires=[_ext("YR2015")], dams=[_ext("Mystery Ewe")])
        r = compute_purity("a", _by_id(a), finn_reg)
        assert r.purebred is None
        assert r.status == "unverified"
        assert r.unresolved == ["Mystery Ewe"]
        assert r.composition == {"Finnish Landrace": 0.5, "unknown": 0.5}

    def test_missing_parent_is_a_gap(self, finn_reg):
        a = _animal("a", name="A", breed="Finish Landrace", sires=[_ext("YR2015")], dams=[])
        r = compute_purity("a", _by_id(a), finn_reg)
        assert r.purebred is None
        assert r.status == "unverified"
        assert r.gaps == ["A: dam not recorded"]
        assert r.composition == {"Finnish Landrace": 0.5, "unknown": 0.5}

    def test_cached_animal_with_no_parents_is_an_implicit_founder(self, finn_reg):
        sansa = _animal("sansa", name="Sansa", breed="North Country Cheviot")
        r = compute_purity("sansa", _by_id(sansa), finn_reg)
        assert r.purebred is None
        assert r.unresolved == ["Sansa"]

    def test_registry_entry_for_cached_animal_wins(self, finn_reg):
        sansa = _animal("sansa", name="Sansa", breed="North Country Cheviot")
        reg = _registry(
            *finn_reg.founders, Founder(id="Sansa", animal_id="sansa", breed="North Country Cheviot", purebred=True)
        )
        r = compute_purity("sansa", _by_id(sansa), reg)
        assert r.purebred is True
        assert r.purebred_breed == "North Country Cheviot"

    def test_external_parent_matched_by_vid_when_name_missing(self, finn_reg):
        a = _animal("a", name="A", sires=[_ext("YR2015")], dams=[_ext(name=None, vid="ww02")])
        r = compute_purity("a", _by_id(a), finn_reg)
        assert r.status == "mixed"
        assert r.purebred is False

    def test_unknown_animal_raises(self, finn_reg):
        with pytest.raises(KeyError):
            compute_purity("nope", {}, finn_reg)

    def test_cycle_does_not_hang(self, finn_reg):
        a = _animal("a", name="A", sires=[_parent("b")], dams=[_ext("Luann")])
        b = _animal("b", name="B", sex="Male", sires=[_parent("a")], dams=[_ext("Luann")])
        r = compute_purity("a", _by_id(a, b), finn_reg)
        assert r.purebred is None
        assert any("cycle" in g for g in r.gaps)

    def test_status_is_derived_from_purebred(self):
        base = {
            "animal_id": "a",
            "name": "A",
            "breed_label": "?",
            "composition": {},
            "purebred_breed": None,
            "founders": [],
            "unresolved": [],
            "gaps": [],
        }
        assert PurityResult(purebred=True, **base).status == "verified"
        assert PurityResult(purebred=False, **base).status == "mixed"
        assert PurityResult(purebred=None, **base).status == "unverified"
        assert PurityResult(purebred=None, **base).to_dict()["status"] == "unverified"

    def test_two_sires_are_averaged_and_flagged_as_gap(self, finn_reg):
        a = _animal("a", name="A", sires=[_ext("YR2015"), _ext("Blue Man")], dams=[_ext("Luann")])
        r = compute_purity("a", _by_id(a), finn_reg)
        # sire side = 50/50 Finn/BFL -> 25% BFL overall; two breeds -> mixed regardless of the gap
        assert r.composition == {"Finnish Landrace": pytest.approx(0.75), "Bluefaced Leicester": pytest.approx(0.25)}
        assert r.purebred is False
        assert "A: 2 sires recorded (ambiguous)" in r.gaps
        assert sorted(r.founders) == ["BLUEMAN", "Luann", "YR2015"]

    def test_two_sires_of_same_breed_can_never_be_verified(self, finn_reg):
        a = _animal("a", name="A", sires=[_ext("YR2015"), _ext("Dube")], dams=[_ext("Luann")])
        r = compute_purity("a", _by_id(a), finn_reg)
        assert r.composition == {"Finnish Landrace": pytest.approx(1.0)}
        assert r.purebred is None
        assert r.gaps == ["A: 2 sires recorded (ambiguous)"]

    def test_max_depth_guard(self, finn_reg):
        # chain: a0 <- a1 <- a2 <- a3 <- a4 (each has sire = next, dam = Luann); a4's sire is YR2015
        animals = []
        for i in range(5):
            sire = _parent(f"a{i + 1}") if i < 4 else _ext("YR2015")
            animals.append(_animal(f"a{i}", name=f"A{i}", sires=[sire], dams=[_ext("Luann")]))
        by_id = _by_id(*animals)
        assert compute_purity("a0", by_id, finn_reg).purebred is True  # default depth is plenty
        r = compute_purity("a0", by_id, finn_reg, max_depth=3)
        assert r.purebred is None
        assert any("max depth reached" in g for g in r.gaps)

    def test_to_dict_is_json_serialisable(self, finn_reg):
        a = _animal("a", name="A", sires=[_ext("YR2015")], dams=[_ext("Luann")])
        d = compute_purity("a", _by_id(a), finn_reg).to_dict()
        json.dumps(d)
        assert d["purebred"] is True
        assert d["name"] == "A"


# ---------------------------------------------------------------------------
# Herd queries
# ---------------------------------------------------------------------------


class TestFindPurebred:
    @pytest.fixture
    def herd(self):
        pure = _animal(
            "p", name="Pure", breed="Finish Landrace", birth_year=2025, sires=[_ext("YR2015")], dams=[_ext("Luann")]
        )
        cross = _animal(
            "c", name="Cross", breed="Finish Landrace", birth_year=2025, sires=[_ext("YR2015")], dams=[_ext("WW02")]
        )
        unsure = _animal(
            "u", name="Unsure", breed="Finish Landrace", birth_year=2025, sires=[_ext("YR2015")], dams=[_ext("Who")]
        )
        old = _animal(
            "o", name="Old", breed="Finish Landrace", birth_year=2024, sires=[_ext("YR2015")], dams=[_ext("Luann")]
        )
        gone = _animal(
            "g",
            name="Gone",
            breed="Finish Landrace",
            birth_year=2025,
            on_farm=False,
            sires=[_ext("YR2015")],
            dams=[_ext("Luann")],
        )
        return [pure, cross, unsure, old, gone]

    def test_filters_by_breed_year_and_on_farm(self, herd, finn_reg):
        res = find_purebred(herd, _by_id(*herd), finn_reg, breed="Finnish Landrace", year=2025)
        assert [r.name for r in res["purebred"]] == ["Pure"]
        assert [r.name for r in res["unverified"]] == ["Unsure"]
        assert [r.name for r in res["mixed"]] == ["Cross"]

    def test_include_off_farm(self, herd, finn_reg):
        res = find_purebred(herd, _by_id(*herd), finn_reg, breed="Finnish Landrace", year=2025, on_farm_only=False)
        assert sorted(r.name for r in res["purebred"]) == ["Gone", "Pure"]

    def test_verified_purebred_of_other_breed_is_excluded(self, finn_reg):
        reg = _registry(*finn_reg.founders, Founder(id="Sansa", breed="North Country Cheviot", purebred=True))
        # Mislabelled Finn, lineage is 100% NCC -> involved via label only, but excluded from every group
        a = _animal(
            "a", name="A", breed="Finish Landrace", birth_year=2025, sires=[_ext("Sansa")], dams=[_ext("Sansa")]
        )
        res = find_purebred([a], _by_id(a), reg, breed="Finnish Landrace")
        assert res == {"purebred": [], "unverified": [], "mixed": []}

    def test_breed_label_is_not_trusted(self, finn_reg):
        # Labelled 1st Cross but lineage says pure Finn -> counted as purebred Finn
        a = _animal("a", name="A", breed="1st Cross", birth_year=2025, sires=[_ext("YR2015")], dams=[_ext("Luann")])
        res = find_purebred([a], _by_id(a), finn_reg, breed="Finnish Landrace")
        assert [r.name for r in res["purebred"]] == ["A"]


# ---------------------------------------------------------------------------
# Founder discovery
# ---------------------------------------------------------------------------


class TestDiscoverFounders:
    def test_groups_external_spellings_and_counts_descendants(self):
        kid1 = _animal("k1", name="K1", sires=[_ext("BLUEMAN")], dams=[_ext(vid="WW02")])
        kid2 = _animal("k2", name="K2", sires=[_ext("Blue Man")], dams=[_ext(vid="WW02")])
        grand = _animal("g", name="G", sires=[_parent("k1")], dams=[_parent("k2")])
        sansa = _animal("sansa", name="Sansa", breed="North Country Cheviot")  # cached, no parents
        reg = _registry(Founder(id="BLUEMAN", breed="Bluefaced Leicester", purebred=True))
        cands = {c.key: c for c in discover_founders([kid1, kid2, grand, sansa], _by_id(kid1, kid2, grand, sansa), reg)}
        assert set(cands) == {"BLUEMAN", "WW02", "sansa"}
        assert cands["BLUEMAN"].labels == {"BLUEMAN", "Blue Man"}
        assert cands["BLUEMAN"].descendants == 3
        assert cands["BLUEMAN"].resolved is True
        assert cands["WW02"].resolved is False
        assert cands["WW02"].role == "dam"
        assert cands["sansa"].in_cache is True
        assert cands["sansa"].breed_hint == "North Country Cheviot"
        assert cands["sansa"].descendants == 0

    def test_unregistered_spellings_group_by_normalised_name(self):
        k1 = _animal("k1", name="K1", sires=[_ext("Old Tom")], dams=[_ext(vid="EWE-7")])
        k2 = _animal("k2", name="K2", sires=[_ext("OLD TOM!")], dams=[_ext(vid="ewe7")])
        cands = {c.key: c for c in discover_founders([k1, k2], _by_id(k1, k2), _registry())}
        assert set(cands) == {"OLDTOM", "EWE7"}
        assert cands["OLDTOM"].labels == {"Old Tom", "OLD TOM!"}
        assert cands["EWE7"].vids == {"EWE-7", "ewe7"}

    def test_unidentified_parent_refs_do_not_collapse_together(self):
        k1 = _animal("k1", name="K1", sires=[_ext()], dams=[_ext("Luann")])
        k2 = _animal("k2", name="K2", sires=[_ext()], dams=[_ext("Luann")])
        cands = discover_founders([k1, k2], _by_id(k1, k2), _registry())
        unidentified = [c for c in cands if c.key.startswith("unidentified:")]
        assert len(unidentified) == 2
        assert {next(iter(c.labels)) for c in unidentified} == {"unidentified sire of K1", "unidentified sire of K2"}


# ---------------------------------------------------------------------------
# Shared animal matcher (loader.find_animal) -- used by CLI and MCP server
# ---------------------------------------------------------------------------


class TestFindAnimal:
    @pytest.fixture
    def herd(self):
        a = _animal("id-a", name="Bocce", eid="840003309352862")
        b = _animal("id-b", name="Zelda", vid="ZELDA", eid="840003309352867")
        c = _animal("id-c", name="Twin", eid="840003309350862")  # shares the 862 suffix with Bocce
        return [a, b, c]

    def test_exact_matches(self, herd):
        by_id = _by_id(*herd)
        assert find_animal("id-b", herd, by_id)["identity"]["name"] == "Zelda"
        assert find_animal("bocce", herd, by_id)["animalId"] == "id-a"
        assert find_animal("zelda", herd, by_id)["animalId"] == "id-b"
        assert find_animal("840003309352867", herd, by_id)["animalId"] == "id-b"

    def test_unique_eid_suffix(self, herd):
        assert find_animal("2867", herd, _by_id(*herd))["animalId"] == "id-b"

    def test_ambiguous_eid_suffix_returns_none(self, herd):
        assert find_animal("862", herd, _by_id(*herd)) is None

    def test_non_digit_miss_returns_none(self, herd):
        assert find_animal("abc123", herd, _by_id(*herd)) is None
        assert find_animal("   ", herd, _by_id(*herd)) is None


# ---------------------------------------------------------------------------
# Farm cross labels (Finn X / BFL X / NCC X / BFF)
# ---------------------------------------------------------------------------


def _result(composition: dict[str, float], purebred: bool | None = False, breed: str | None = None) -> PurityResult:
    return PurityResult(
        animal_id="x",
        name="X",
        breed_label="?",
        composition=composition,
        purebred=purebred,
        purebred_breed=breed,
        founders=[],
        unresolved=[],
        gaps=[],
    )


class TestCrossLabel:
    def test_purebred_uses_short_breed_name(self):
        assert _result({"Finnish Landrace": 1.0}, purebred=True, breed="Finnish Landrace").cross_label == "Finn"
        r = _result({"North Country Cheviot": 1.0}, purebred=True, breed="North Country Cheviot")
        assert r.cross_label == "NCC"

    def test_bff_is_half_finn_half_bfl_cross(self):
        assert _result({"Finnish Landrace": 0.5, "Bluefaced Leicester X": 0.5}).cross_label == "BFF"
        assert _result({"Finnish Landrace": 0.5, "Bluefaced Leicester": 0.5}).cross_label == "BFF"
        mixed_bfl = {"Finnish Landrace": 0.5, "Bluefaced Leicester": 0.25, "Bluefaced Leicester X": 0.25}
        assert _result(mixed_bfl).cross_label == "BFF"

    def test_dominant_breed_gets_x_suffix(self):
        assert _result({"Finnish Landrace": 0.75, "Bluefaced Leicester X": 0.25}).cross_label == "Finn X"
        assert _result({"Finnish Landrace": 0.875, "unknown": 0.125}).cross_label == "Finn X"
        assert _result({"North Country Cheviot": 0.75, "Finnish Landrace": 0.25}).cross_label == "NCC X"
        assert _result({"Bluefaced Leicester X": 0.75, "Finnish Landrace": 0.25}).cross_label == "BFL X"

    def test_no_dominant_breed_has_no_label(self):
        assert _result({"Finnish Landrace": 0.5, "North Country Cheviot": 0.5}).cross_label is None
        assert _result({"Finnish Landrace": 0.5, "unknown": 0.5}).cross_label is None
        assert _result({"Finnish Landrace": 0.5, "unknown": 0.5}, purebred=None).cross_label is None

    def test_cross_label_in_to_dict(self):
        assert _result({"Finnish Landrace": 0.75, "unknown": 0.25}).to_dict()["cross_label"] == "Finn X"
