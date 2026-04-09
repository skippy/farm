"""Tests for the lambing analysis loader module."""

import json

import pytest

# Shared builders from conftest
from conftest import make_animal as _animal
from conftest import make_parent as _parent

from agriwebb.analysis.lambing.loader import (
    classify_loss,
    get_age_at_first_lambing,
    get_age_class,
    get_ancestors,
    get_birth_year,
    get_breed,
    get_breed_cross,
    get_dam_id,
    get_dam_name,
    get_days_reared,
    get_ewes_in_group,
    get_joined_sire,
    get_joining_group,
    get_lambing_history,
    get_litter,
    get_name,
    get_offspring,
    get_offspring_by_year,
    get_sex,
    get_sire_id,
    get_sire_name,
    is_alive,
    is_dead,
    is_ewe,
    is_first_time_mother,
    is_intact_ram,
    is_lambing_loss,
    is_on_farm,
    is_sold,
    load_farm_data,
    was_raised,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def ram():
    return _animal(
        animal_id="ram-1",
        name="Big John",
        breed="North Country Cheviot",
        sex="Male",
        age_class="ram",
        birth_year=2020,
    )


@pytest.fixture
def wether():
    return _animal(
        animal_id="wether-1",
        vid="W001",
        breed="1st Cross",
        sex="Male",
        age_class="wether",
        birth_year=2023,
        fate="Sold",
        on_farm=False,
    )


@pytest.fixture
def ewe():
    return _animal(
        animal_id="ewe-1",
        name="Daisy",
        breed="North Country Cheviot",
        sex="Female",
        age_class="ewe",
        birth_year=2021,
    )


@pytest.fixture
def ewe_lamb():
    return _animal(
        animal_id="ewe-lamb-1",
        vid="EL01",
        sex="Female",
        age_class="ewe_lamb",
        birth_year=2025,
    )


@pytest.fixture
def dead_lamb():
    return _animal(
        animal_id="dead-1",
        vid="D001",
        sex="Female",
        age_class="ewe_lamb",
        birth_year=2026,
        fate="Dead",
        days_reared=0,
        on_farm=False,
    )


@pytest.fixture
def family(ram, ewe):
    """A ram, a ewe, and three lambs (alive, sold, dead)."""
    sire_ref = _parent("ram-1", name="Big John")
    dam_ref = _parent("ewe-1", name="Daisy")
    lamb_alive = _animal(
        animal_id="lamb-1",
        name="Lamb A",
        birth_year=2026,
        age_class="ewe_lamb",
        sires=[sire_ref],
        dams=[dam_ref],
    )
    lamb_sold = _animal(
        animal_id="lamb-2",
        vid="L02",
        birth_year=2026,
        sex="Male",
        age_class="ram_lamb",
        fate="Sold",
        on_farm=False,
        sires=[sire_ref],
        dams=[dam_ref],
    )
    lamb_dead = _animal(
        animal_id="lamb-3",
        vid="L03",
        birth_year=2026,
        sex="Male",
        age_class="ram_lamb",
        fate="Dead",
        days_reared=0,
        on_farm=False,
        sires=[sire_ref],
        dams=[dam_ref],
    )
    return [ram, ewe, lamb_alive, lamb_sold, lamb_dead]


@pytest.fixture
def three_gen():
    """Three-generation family tree for ancestor testing.

    Granddam (gd) -> Dam (d) -> Lamb (l)
    Grandsire (gs) -> Dam (d)
    Sire (s) -> Lamb (l)
    """
    granddam = _animal(animal_id="gd", name="GrandDam")
    grandsire = _animal(animal_id="gs", name="GrandSire", sex="Male", age_class="ram")
    dam = _animal(
        animal_id="d",
        name="Dam",
        sires=[_parent("gs", name="GrandSire")],
        dams=[_parent("gd", name="GrandDam")],
    )
    sire = _animal(animal_id="s", name="Sire", sex="Male", age_class="ram")
    lamb = _animal(
        animal_id="l",
        name="Lamb",
        birth_year=2026,
        sires=[_parent("s", name="Sire")],
        dams=[_parent("d", name="Dam")],
    )
    return [granddam, grandsire, dam, sire, lamb]


# ---------------------------------------------------------------------------
# load_farm_data
# ---------------------------------------------------------------------------

class TestLoadFarmData:
    def test_loads_animals(self, tmp_path, monkeypatch):
        """Loading animals.json via load_farm_data should populate animals and by_id."""
        cache_dir = tmp_path / ".cache"
        cache_dir.mkdir()
        animals_data = {
            "exported_at": "2026-04-05",
            "animals": [_animal(animal_id="a1", name="Ewe One")],
        }
        (cache_dir / "animals.json").write_text(json.dumps(animals_data))

        # Point get_cache_dir at our tmp dir
        monkeypatch.setattr("agriwebb.core.cache.get_cache_dir", lambda: cache_dir)
        monkeypatch.setattr("agriwebb.analysis.lambing.loader.get_cache_dir", lambda: cache_dir)

        fd = load_farm_data(season=2026)
        assert len(fd.animals) == 1
        assert "a1" in fd.by_id
        assert fd.season == 2026

    def test_missing_optional_files(self, tmp_path, monkeypatch):
        """Missing service/loss files should produce empty lists, not errors."""
        cache_dir = tmp_path / ".cache"
        cache_dir.mkdir()
        (cache_dir / "animals.json").write_text(json.dumps({"animals": []}))
        monkeypatch.setattr("agriwebb.core.cache.get_cache_dir", lambda: cache_dir)
        monkeypatch.setattr("agriwebb.analysis.lambing.loader.get_cache_dir", lambda: cache_dir)

        fd = load_farm_data(season=2026)
        assert fd.service_groups == []
        assert fd.loss_records == []

    def test_loads_service_groups(self, tmp_path, monkeypatch):
        """natural_service.json should populate service_groups."""
        cache_dir = tmp_path / ".cache"
        cache_dir.mkdir()
        (cache_dir / "animals.json").write_text(json.dumps({"animals": []}))
        groups = [{"sire_name": "Atlas", "ewe_ids": ["e1", "e2"]}]
        (cache_dir / "natural_service.json").write_text(json.dumps(groups))
        monkeypatch.setattr("agriwebb.core.cache.get_cache_dir", lambda: cache_dir)
        monkeypatch.setattr("agriwebb.analysis.lambing.loader.get_cache_dir", lambda: cache_dir)

        fd = load_farm_data(season=2026)
        assert len(fd.service_groups) == 1
        assert fd.service_groups[0]["sire_name"] == "Atlas"

    def test_loads_loss_records(self, tmp_path, monkeypatch):
        """lamb_losses_YYYY.json should populate loss_records for that season."""
        cache_dir = tmp_path / ".cache"
        cache_dir.mkdir()
        (cache_dir / "animals.json").write_text(json.dumps({"animals": []}))
        losses = [{"animalId": "x1", "category": "perinatal"}]
        (cache_dir / "lamb_losses_2026.json").write_text(json.dumps(losses))
        monkeypatch.setattr("agriwebb.core.cache.get_cache_dir", lambda: cache_dir)
        monkeypatch.setattr("agriwebb.analysis.lambing.loader.get_cache_dir", lambda: cache_dir)

        fd = load_farm_data(season=2026)
        assert len(fd.loss_records) == 1

    def test_default_season(self, tmp_path, monkeypatch):
        """Season should default to current year."""
        cache_dir = tmp_path / ".cache"
        cache_dir.mkdir()
        (cache_dir / "animals.json").write_text(json.dumps({"animals": []}))
        monkeypatch.setattr("agriwebb.core.cache.get_cache_dir", lambda: cache_dir)
        monkeypatch.setattr("agriwebb.analysis.lambing.loader.get_cache_dir", lambda: cache_dir)

        fd = load_farm_data()
        assert fd.season > 2020  # Sanity check


# ---------------------------------------------------------------------------
# Classification helpers
# ---------------------------------------------------------------------------

class TestClassificationHelpers:
    def test_get_name_prefers_name(self):
        a = _animal(name="Flora", vid="V1", eid="E1")
        assert get_name(a) == "Flora"

    def test_get_name_fallback_vid(self):
        a = _animal(vid="V1", eid="E1")
        assert get_name(a) == "V1"

    def test_get_name_fallback_eid(self):
        a = _animal(eid="840003218100593")
        assert get_name(a) == "840003218100593"

    def test_get_name_fallback_id(self):
        a = _animal(animal_id="abcdef12-3456-7890-abcd-ef1234567890")
        assert get_name(a) == "abcdef12"

    def test_get_breed(self, ewe):
        assert get_breed(ewe) == "North Country Cheviot"

    def test_get_breed_missing(self):
        a = _animal()
        a["characteristics"]["breedAssessed"] = None
        assert get_breed(a) == "?"

    def test_get_sex(self, ram):
        assert get_sex(ram) == "Male"

    def test_get_sex_missing(self):
        a = {"characteristics": None}
        assert get_sex(a) == "?"

    def test_get_birth_year(self, ewe):
        assert get_birth_year(ewe) == 2021

    def test_get_birth_year_missing(self):
        a = {"characteristics": {}}
        assert get_birth_year(a) is None

    def test_get_age_class(self, ram):
        assert get_age_class(ram) == "ram"

    def test_get_age_class_missing(self):
        a = {}
        assert get_age_class(a) == "?"

    def test_is_on_farm_true(self, ewe):
        assert is_on_farm(ewe) is True

    def test_is_on_farm_false(self, wether):
        assert is_on_farm(wether) is False

    def test_is_alive(self, ewe):
        assert is_alive(ewe) is True

    def test_is_alive_dead(self, dead_lamb):
        assert is_alive(dead_lamb) is False

    def test_is_dead(self, dead_lamb):
        assert is_dead(dead_lamb) is True

    def test_is_dead_alive(self, ewe):
        assert is_dead(ewe) is False

    def test_is_dead_sold(self, wether):
        """Sold is NOT dead."""
        assert is_dead(wether) is False

    def test_is_sold(self, wether):
        assert is_sold(wether) is True

    def test_is_sold_alive(self, ewe):
        assert is_sold(ewe) is False

    def test_was_raised_alive(self, ewe):
        assert was_raised(ewe) is True

    def test_was_raised_sold(self, wether):
        assert was_raised(wether) is True

    def test_was_raised_dead(self, dead_lamb):
        assert was_raised(dead_lamb) is False

    def test_is_ewe(self, ewe):
        assert is_ewe(ewe) is True

    def test_is_ewe_lamb_excluded(self, ewe_lamb):
        """Ewe lambs are too young for breeding classification."""
        assert is_ewe(ewe_lamb) is False

    def test_is_ewe_weaner_excluded(self):
        a = _animal(sex="Female", age_class="ewe_weaner")
        assert is_ewe(a) is False

    def test_is_ewe_maiden(self):
        a = _animal(sex="Female", age_class="maiden_ewe")
        assert is_ewe(a) is True

    def test_is_ewe_hogget(self):
        a = _animal(sex="Female", age_class="ewe_hogget")
        assert is_ewe(a) is True

    def test_is_ewe_male_excluded(self, ram):
        assert is_ewe(ram) is False

    def test_is_intact_ram(self, ram):
        assert is_intact_ram(ram) is True

    def test_is_intact_ram_wether(self, wether):
        assert is_intact_ram(wether) is False

    def test_is_intact_ram_female(self, ewe):
        assert is_intact_ram(ewe) is False

    def test_is_intact_ram_ram_lamb(self):
        a = _animal(sex="Male", age_class="ram_lamb")
        assert is_intact_ram(a) is True

    def test_is_intact_ram_wether_hogget(self):
        a = _animal(sex="Male", age_class="wether_hogget")
        assert is_intact_ram(a) is False

    def test_get_days_reared(self, ewe):
        assert get_days_reared(ewe) == 500

    def test_get_days_reared_none(self, dead_lamb):
        """daysReared=0 should return 0, not None."""
        assert get_days_reared(dead_lamb) == 0


# ---------------------------------------------------------------------------
# Parentage helpers
# ---------------------------------------------------------------------------

class TestParentageHelpers:
    def test_get_sire_id(self, family):
        lamb = family[2]  # lamb_alive
        assert get_sire_id(lamb) == "ram-1"

    def test_get_sire_id_none(self, ewe):
        assert get_sire_id(ewe) is None

    def test_get_sire_name(self, family):
        lamb = family[2]
        assert get_sire_name(lamb) == "Big John"

    def test_get_sire_name_missing(self, ewe):
        assert get_sire_name(ewe) == "?"

    def test_get_dam_id(self, family):
        lamb = family[2]
        assert get_dam_id(lamb) == "ewe-1"

    def test_get_dam_name(self, family):
        lamb = family[2]
        assert get_dam_name(lamb) == "Daisy"

    def test_get_dam_name_missing(self, ram):
        assert get_dam_name(ram) == "?"

    def test_sire_name_fallback_vid(self):
        """If sire has no name, fall back to vid."""
        lamb = _animal(sires=[_parent("s1", vid="R100")])
        assert get_sire_name(lamb) == "R100"

    def test_dam_name_fallback_vid(self):
        """If dam has no name, fall back to vid."""
        lamb = _animal(dams=[_parent("d1", vid="E200")])
        assert get_dam_name(lamb) == "E200"


# ---------------------------------------------------------------------------
# Lineage: get_ancestors
# ---------------------------------------------------------------------------

class TestGetAncestors:
    def test_three_generations(self, three_gen):
        by_id = {a["animalId"]: a for a in three_gen}
        ancestors = get_ancestors("l", by_id, max_depth=6)
        # Should include sire, dam, grandsire, granddam by id
        assert "s" in ancestors
        assert "d" in ancestors
        assert "gs" in ancestors
        assert "gd" in ancestors

    def test_includes_names_uppercase(self, three_gen):
        by_id = {a["animalId"]: a for a in three_gen}
        ancestors = get_ancestors("l", by_id)
        assert "SIRE" in ancestors
        assert "DAM" in ancestors
        assert "GRANDSIRE" in ancestors
        assert "GRANDDAM" in ancestors

    def test_no_ancestors(self):
        a = _animal(animal_id="solo")
        by_id = {"solo": a}
        assert get_ancestors("solo", by_id) == set()

    def test_unknown_id(self):
        assert get_ancestors("nonexistent", {}) == set()

    def test_max_depth_limits(self, three_gen):
        by_id = {a["animalId"]: a for a in three_gen}
        # Depth 0 walks the starting animal only -> finds direct parents
        ancestors = get_ancestors("l", by_id, max_depth=0)
        assert "s" in ancestors
        assert "d" in ancestors
        # Grandparents should not be reached at depth 0
        assert "gs" not in ancestors
        assert "gd" not in ancestors


# ---------------------------------------------------------------------------
# Offspring and litter
# ---------------------------------------------------------------------------

class TestOffspringAndLitter:
    def test_get_offspring_by_sire(self, family):
        offspring = get_offspring("ram-1", family)
        assert len(offspring) == 3  # All three lambs

    def test_get_offspring_by_dam(self, family):
        offspring = get_offspring("ewe-1", family)
        assert len(offspring) == 3

    def test_get_offspring_none(self, family):
        offspring = get_offspring("nonexistent", family)
        assert offspring == []

    def test_get_offspring_by_year(self, family):
        by_year = get_offspring_by_year("ewe-1", family)
        assert 2026 in by_year
        assert len(by_year[2026]) == 3

    def test_get_litter(self, family):
        litter = get_litter("ewe-1", 2026, family)
        assert len(litter) == 3

    def test_get_litter_wrong_year(self, family):
        litter = get_litter("ewe-1", 2025, family)
        assert litter == []

    def test_get_litter_wrong_dam(self, family):
        litter = get_litter("nonexistent", 2026, family)
        assert litter == []


# ---------------------------------------------------------------------------
# Loss classification
# ---------------------------------------------------------------------------

class TestLossClassification:
    def test_stillborn(self):
        a = _animal(fate="Dead", days_reared=0)
        assert classify_loss(a) == "stillborn"

    def test_stillborn_none_days(self):
        a = _animal(fate="Dead", days_reared=None)
        assert classify_loss(a) == "stillborn"

    def test_early_loss(self):
        a = _animal(fate="Dead", days_reared=14)
        assert classify_loss(a) == "early_loss"

    def test_early_loss_boundary(self):
        a = _animal(fate="Dead", days_reared=90)
        assert classify_loss(a) == "early_loss"

    def test_late_death(self):
        a = _animal(fate="Dead", days_reared=200)
        assert classify_loss(a) == "late_death"

    def test_alive_returns_none(self, ewe):
        assert classify_loss(ewe) is None

    def test_sold_returns_none(self, wether):
        assert classify_loss(wether) is None

    def test_detailed_records_override(self):
        a = _animal(animal_id="x1", fate="Dead", days_reared=0)
        records = [{"animalId": "x1", "category": "intrapartum"}]
        assert classify_loss(a, loss_records=records) == "intrapartum"

    def test_detailed_records_no_match_falls_back(self):
        a = _animal(animal_id="x2", fate="Dead", days_reared=5)
        records = [{"animalId": "x1", "category": "intrapartum"}]
        assert classify_loss(a, loss_records=records) == "early_loss"

    def test_is_lambing_loss_stillborn(self):
        a = _animal(fate="Dead", days_reared=0)
        assert is_lambing_loss(a) is True

    def test_is_lambing_loss_early(self):
        a = _animal(fate="Dead", days_reared=30)
        assert is_lambing_loss(a) is True

    def test_is_lambing_loss_late(self):
        a = _animal(fate="Dead", days_reared=200)
        assert is_lambing_loss(a) is False

    def test_is_lambing_loss_alive(self, ewe):
        assert is_lambing_loss(ewe) is False


# ---------------------------------------------------------------------------
# Breeding group helpers
# ---------------------------------------------------------------------------

class TestBreedingGroupHelpers:
    @pytest.fixture
    def groups(self):
        return [
            {"sire_name": "Atlas", "ewe_ids": ["e1", "e2", "e3"]},
            {"sire_name": "Thor", "ewe_ids": ["e4", "e5"]},
        ]

    def test_get_joining_group(self, groups):
        group = get_joining_group("e2", groups)
        assert group is not None
        assert group["sire_name"] == "Atlas"

    def test_get_joining_group_none(self, groups):
        assert get_joining_group("e99", groups) is None

    def test_get_joined_sire(self, groups):
        assert get_joined_sire("e4", groups) == "Thor"

    def test_get_joined_sire_none(self, groups):
        assert get_joined_sire("e99", groups) is None

    def test_get_ewes_in_group(self, groups):
        ewes = get_ewes_in_group("Atlas", groups)
        assert ewes == ["e1", "e2", "e3"]

    def test_get_ewes_in_group_empty(self, groups):
        assert get_ewes_in_group("Nobody", groups) == []


# ---------------------------------------------------------------------------
# Experience & age helpers
# ---------------------------------------------------------------------------

class TestExperienceHelpers:
    def test_get_lambing_history(self, family):
        history = get_lambing_history("ewe-1", family)
        assert 2026 in history
        assert len(history[2026]) == 3

    def test_is_first_time_mother_true(self, family):
        """All lambs are born in 2026, so for season=2026 she is first-time."""
        assert is_first_time_mother("ewe-1", 2026, family) is True

    def test_is_first_time_mother_false(self, family):
        """With season=2027, she had lambs in 2026, so not first-time."""
        assert is_first_time_mother("ewe-1", 2027, family) is False

    def test_is_first_time_mother_no_offspring(self, family):
        """A ewe with no offspring is considered first-time."""
        assert is_first_time_mother("nonexistent", 2026, family) is True

    def test_get_age_at_first_lambing(self, family):
        by_id = {a["animalId"]: a for a in family}
        # Ewe born 2021, first lambed 2026 -> age 5
        age = get_age_at_first_lambing("ewe-1", family, by_id)
        assert age == 5

    def test_get_age_at_first_lambing_no_offspring(self, family):
        by_id = {a["animalId"]: a for a in family}
        assert get_age_at_first_lambing("ram-1", family, by_id) is None

    def test_get_age_at_first_lambing_unknown_dam(self, family):
        by_id = {a["animalId"]: a for a in family}
        assert get_age_at_first_lambing("nonexistent", family, by_id) is None

    def test_get_age_at_first_lambing_no_birth_year(self, family):
        """Dam with no birth year returns None."""
        by_id = {a["animalId"]: a for a in family}
        by_id["ewe-1"]["characteristics"]["birthYear"] = None
        assert get_age_at_first_lambing("ewe-1", family, by_id) is None

    def test_multi_year_history(self):
        """A dam with offspring across two years."""
        dam = _animal(animal_id="d1", birth_year=2020)
        sire_ref = _parent("s1", name="Sire")
        dam_ref = _parent("d1", name="Dam")
        lamb_2024 = _animal(animal_id="l1", birth_year=2024, dams=[dam_ref], sires=[sire_ref])
        lamb_2025 = _animal(animal_id="l2", birth_year=2025, dams=[dam_ref], sires=[sire_ref])
        animals = [dam, lamb_2024, lamb_2025]
        by_id = {a["animalId"]: a for a in animals}

        history = get_lambing_history("d1", animals)
        assert 2024 in history
        assert 2025 in history

        assert is_first_time_mother("d1", 2025, animals) is False
        assert is_first_time_mother("d1", 2024, animals) is True

        assert get_age_at_first_lambing("d1", animals, by_id) == 4


# ---------------------------------------------------------------------------
# Breed cross classification
# ---------------------------------------------------------------------------

class TestBreedCross:
    def _by_id(self, *animals):
        return {a["animalId"]: a for a in animals}

    def test_ncc_x_ncc(self):
        sire = _animal(animal_id="s", breed="North Country Cheviot", sex="Male", age_class="ram")
        dam = _animal(animal_id="d", breed="North Country Cheviot")
        lamb = _animal(sires=[_parent("s")], dams=[_parent("d")])
        assert get_breed_cross(lamb, self._by_id(sire, dam, lamb)) == "NCC x NCC"

    def test_ncc_x_other(self):
        sire = _animal(animal_id="s", breed="North Country Cheviot", sex="Male", age_class="ram")
        dam = _animal(animal_id="d", breed="1st Cross")
        lamb = _animal(sires=[_parent("s")], dams=[_parent("d")])
        assert get_breed_cross(lamb, self._by_id(sire, dam, lamb)) == "NCC x other"

    def test_ncc_x_other_dam_ncc(self):
        """NCC dam, non-NCC sire should still be NCC x other."""
        sire = _animal(animal_id="s", breed="Bluefaced Leicester", sex="Male", age_class="ram")
        dam = _animal(animal_id="d", breed="NCC")
        lamb = _animal(sires=[_parent("s")], dams=[_parent("d")])
        assert get_breed_cross(lamb, self._by_id(sire, dam, lamb)) == "NCC x other"

    def test_finn_involved_sire(self):
        sire = _animal(animal_id="s", breed="Finnsheep", sex="Male", age_class="ram")
        dam = _animal(animal_id="d", breed="North Country Cheviot")
        lamb = _animal(sires=[_parent("s")], dams=[_parent("d")])
        assert get_breed_cross(lamb, self._by_id(sire, dam, lamb)) == "Finn-involved"

    def test_finn_involved_dam(self):
        sire = _animal(animal_id="s", breed="North Country Cheviot", sex="Male", age_class="ram")
        dam = _animal(animal_id="d", breed="Finnish Landrace")
        lamb = _animal(sires=[_parent("s")], dams=[_parent("d")])
        assert get_breed_cross(lamb, self._by_id(sire, dam, lamb)) == "Finn-involved"

    def test_other(self):
        sire = _animal(animal_id="s", breed="Bluefaced Leicester", sex="Male", age_class="ram")
        dam = _animal(animal_id="d", breed="1st Cross")
        lamb = _animal(sires=[_parent("s")], dams=[_parent("d")])
        assert get_breed_cross(lamb, self._by_id(sire, dam, lamb)) == "other"

    def test_unknown_parents(self):
        """Lamb with no parent IDs in by_id falls back to parent identity names."""
        lamb = _animal(
            sires=[_parent("unknown-s", name="SomeRam")],
            dams=[_parent("unknown-d", name="SomeEwe")],
        )
        # Neither parent in by_id, names don't match known breeds
        assert get_breed_cross(lamb, {}) == "other"

    def test_sire_name_matches_ncc(self):
        """Off-cache sire with NCC name should be detected."""
        lamb = _animal(
            sires=[_parent("unknown-s", name="Cheviot")],
            dams=[_parent("d")],
        )
        dam = _animal(animal_id="d", breed="North Country Cheviot")
        assert get_breed_cross(lamb, {"d": dam}) == "NCC x NCC"


# ---------------------------------------------------------------------------
# Edge cases: missing / None fields
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_animal(self):
        """An animal dict with no fields should not raise."""
        a = {}
        assert get_name(a) == "?"[:8]  # falls through to animalId which is missing
        assert get_breed(a) == "?"
        assert get_sex(a) == "?"
        assert get_birth_year(a) is None
        assert get_age_class(a) == "?"
        assert is_on_farm(a) is False
        assert is_alive(a) is False
        assert is_dead(a) is False
        assert is_sold(a) is False
        assert was_raised(a) is False
        assert get_days_reared(a) is None
        assert get_sire_id(a) is None
        assert get_dam_id(a) is None
        assert get_sire_name(a) == "?"
        assert get_dam_name(a) == "?"

    def test_none_characteristics(self):
        a = {"characteristics": None}
        assert get_breed(a) == "?"
        assert get_sex(a) == "?"
        assert is_ewe(a) is False
        assert is_intact_ram(a) is False

    def test_none_state(self):
        a = {"state": None}
        assert is_on_farm(a) is False
        assert is_alive(a) is False
        assert is_dead(a) is False
        assert get_days_reared(a) is None

    def test_none_parentage(self):
        a = {"parentage": None}
        assert get_sire_id(a) is None
        assert get_dam_id(a) is None

    def test_empty_parentage_lists(self):
        a = {"parentage": {"sires": [], "dams": []}}
        assert get_sire_id(a) is None
        assert get_dam_id(a) is None

    def test_classify_loss_no_state(self):
        """An animal with no state should not be classified as dead."""
        a = {}
        assert classify_loss(a) is None

    def test_get_name_empty_identity(self):
        """Identity dict with all None values should fall back to animalId."""
        a = {
            "animalId": "12345678-abcd",
            "identity": {"name": None, "vid": None, "eid": None},
        }
        assert get_name(a) == "12345678"
