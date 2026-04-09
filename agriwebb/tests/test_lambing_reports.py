"""Tests for lambing season and loss report functions."""

import pytest
from conftest import make_animal, make_parent

from agriwebb.analysis.lambing.loader import FarmData
from agriwebb.analysis.lambing.losses import loss_report
from agriwebb.analysis.lambing.season import lambing_season_report

# Aliases for readability
_animal = make_animal
_parent = make_parent


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def farm_data() -> FarmData:
    """A small flock with two ewes lambing in 2026.

    Sire: Big John (ram, NCC)
    Ewe 1: Daisy (NCC, experienced - lambed in 2025 too)
      -> 2025: one lamb (alive)
      -> 2026: twin live lambs (one male, one female)
    Ewe 2: Clover (Finnsheep, maiden)
      -> 2026: triplets (two alive, one stillborn)
    Sire 2: Atlas (ram, NCC)
      -> not joined to anyone this year, but sire of Clover's lambs via
         parentage records
    """
    sire = _animal(
        animal_id="ram-1", name="Big John", sex="Male", age_class="ram", birth_year=2019,
    )
    sire2 = _animal(
        animal_id="ram-2", name="Atlas", sex="Male", age_class="ram", birth_year=2020,
    )
    ewe1 = _animal(animal_id="ewe-1", name="Daisy", birth_year=2021)
    ewe2 = _animal(
        animal_id="ewe-2", name="Clover", breed="Finnsheep", birth_year=2023,
    )

    sire_ref = _parent("ram-1", name="Big John")
    sire2_ref = _parent("ram-2", name="Atlas")
    dam1_ref = _parent("ewe-1", name="Daisy")
    dam2_ref = _parent("ewe-2", name="Clover")

    # 2025 lamb from Daisy (makes her experienced in 2026)
    lamb_2025 = _animal(
        animal_id="l-2025-1", vid="L25-01", birth_year=2025,
        age_class="ewe_hogget", days_reared=365,
        sires=[sire_ref], dams=[dam1_ref],
    )

    # 2026 lambs from Daisy: twins, both live
    lamb_2026_1 = _animal(
        animal_id="l-2026-1", name="Lamb A", birth_year=2026,
        sex="Male", age_class="ram_lamb", days_reared=30,
        sires=[sire_ref], dams=[dam1_ref],
    )
    lamb_2026_2 = _animal(
        animal_id="l-2026-2", name="Lamb B", birth_year=2026,
        sex="Female", age_class="ewe_lamb", days_reared=30,
        sires=[sire_ref], dams=[dam1_ref],
    )

    # 2026 lambs from Clover: triplets (two alive, one stillborn)
    lamb_2026_3 = _animal(
        animal_id="l-2026-3", name="Lamb C", birth_year=2026,
        sex="Female", age_class="ewe_lamb", days_reared=30,
        sires=[sire2_ref], dams=[dam2_ref],
    )
    lamb_2026_4 = _animal(
        animal_id="l-2026-4", name="Lamb D", birth_year=2026,
        sex="Male", age_class="ram_lamb", fate="Sold", on_farm=False,
        days_reared=90,
        sires=[sire2_ref], dams=[dam2_ref],
    )
    lamb_2026_5 = _animal(
        animal_id="l-2026-5", name="Lamb E", birth_year=2026,
        sex="Male", age_class="ram_lamb", fate="Dead", on_farm=False,
        days_reared=0,
        sires=[sire2_ref], dams=[dam2_ref],
    )

    animals = [sire, sire2, ewe1, ewe2, lamb_2025,
               lamb_2026_1, lamb_2026_2, lamb_2026_3, lamb_2026_4, lamb_2026_5]
    by_id = {a["animalId"]: a for a in animals}

    service_groups = [
        {"sire_name": "Big John", "ewe_ids": ["ewe-1", "ewe-3"]},
        {"sire_name": "Atlas", "ewe_ids": ["ewe-2"]},
    ]

    return FarmData(
        animals=animals,
        by_id=by_id,
        service_groups=service_groups,
        loss_records=[],
        season=2026,
    )


# ---------------------------------------------------------------------------
# Season report tests
# ---------------------------------------------------------------------------


class TestLambingSeasonReport:
    def test_headline_live_lambs(self, farm_data: FarmData):
        report = lambing_season_report(farm_data)
        h = report["headline"]
        # 4 live lambs in 2026: Lamb A, B, C, D (Sold counts as raised)
        assert h["live_lambs"] == 4

    def test_headline_sex_counts(self, farm_data: FarmData):
        report = lambing_season_report(farm_data)
        h = report["headline"]
        # Males: Lamb A (alive male), Lamb D (sold male) = 2
        # Females: Lamb B (alive female), Lamb C (alive female) = 2
        assert h["males"] == 2
        assert h["females"] == 2

    def test_headline_ewes_lambed(self, farm_data: FarmData):
        report = lambing_season_report(farm_data)
        h = report["headline"]
        # Two ewes lambed in 2026
        assert h["ewes_lambed"] == 2

    def test_headline_ewes_joined(self, farm_data: FarmData):
        report = lambing_season_report(farm_data)
        h = report["headline"]
        # Service groups: Big John -> ewe-1, ewe-3 (2); Atlas -> ewe-2 (1) = 3
        assert h["ewes_joined"] == 3

    def test_headline_lambing_rate_per_lambed(self, farm_data: FarmData):
        report = lambing_season_report(farm_data)
        h = report["headline"]
        # 4 live lambs / 2 ewes lambed = 2.0
        assert h["lambing_rate_per_lambed"] == 2.0

    def test_headline_lambing_rate_per_joined(self, farm_data: FarmData):
        report = lambing_season_report(farm_data)
        h = report["headline"]
        # 4 live lambs / 3 ewes joined = 1.33
        assert h["lambing_rate_per_joined"] == 1.33

    def test_litter_distribution(self, farm_data: FarmData):
        report = lambing_season_report(farm_data)
        dist = report["litter_distribution"]
        # Daisy: 2 lambs -> twins; Clover: 3 lambs -> triplets
        assert dist == {2: 1, 3: 1}

    def test_by_sire_structure(self, farm_data: FarmData):
        report = lambing_season_report(farm_data)
        by_sire = report["by_sire"]
        sire_names = {s["sire"] for s in by_sire}
        assert "Big John" in sire_names
        assert "Atlas" in sire_names

    def test_by_sire_counts(self, farm_data: FarmData):
        report = lambing_season_report(farm_data)
        by_sire = {s["sire"]: s for s in report["by_sire"]}
        bj = by_sire["Big John"]
        assert bj["live"] == 2
        assert bj["lost"] == 0
        assert bj["lambed"] == 1

        atlas = by_sire["Atlas"]
        assert atlas["live"] == 2
        assert atlas["lost"] == 1
        assert atlas["lambed"] == 1

    def test_by_breed(self, farm_data: FarmData):
        report = lambing_season_report(farm_data)
        by_breed = {b["breed"]: b for b in report["by_breed"]}
        assert "North Country Cheviot" in by_breed
        assert "Finnsheep" in by_breed
        ncc = by_breed["North Country Cheviot"]
        assert ncc["dams"] == 1
        assert ncc["live"] == 2
        assert ncc["lost"] == 0

    def test_maiden_vs_experienced(self, farm_data: FarmData):
        report = lambing_season_report(farm_data)
        mx = report["maiden_vs_experienced"]
        # Clover is maiden (first lambs in 2026)
        assert mx["maiden"]["dams"] == 1
        assert mx["maiden"]["live"] == 2
        assert mx["maiden"]["lost"] == 1
        # Daisy is experienced (lambed in 2025)
        assert mx["experienced"]["dams"] == 1
        assert mx["experienced"]["live"] == 2
        assert mx["experienced"]["lost"] == 0

    def test_report_has_season(self, farm_data: FarmData):
        report = lambing_season_report(farm_data)
        assert report["season"] == 2026

    def test_empty_flock(self):
        """Report on an empty flock should not raise."""
        data = FarmData(animals=[], by_id={}, service_groups=[], loss_records=[], season=2026)
        report = lambing_season_report(data)
        assert report["headline"]["live_lambs"] == 0
        assert report["litter_distribution"] == {}
        assert report["by_sire"] == []
        assert report["by_breed"] == []


# ---------------------------------------------------------------------------
# Loss report tests
# ---------------------------------------------------------------------------


class TestLossReport:
    def test_summary_total(self, farm_data: FarmData):
        report = loss_report(farm_data)
        # One dead lamb (Lamb E, stillborn)
        assert report["summary"]["total"] == 1

    def test_summary_stillborn(self, farm_data: FarmData):
        report = loss_report(farm_data)
        assert report["summary"]["stillborn"] == 1

    def test_summary_no_early_or_late(self, farm_data: FarmData):
        report = loss_report(farm_data)
        assert report["summary"]["early_loss"] == 0
        assert report["summary"]["late_death"] == 0

    def test_by_dam(self, farm_data: FarmData):
        report = loss_report(farm_data)
        by_dam = report["by_dam"]
        assert len(by_dam) == 1
        assert by_dam[0]["dam"] == "Clover"
        assert by_dam[0]["lost"] == 1
        assert by_dam[0]["litter_size"] == 3

    def test_by_sire(self, farm_data: FarmData):
        report = loss_report(farm_data)
        by_sire = {s["sire"]: s for s in report["by_sire"]}
        atlas = by_sire["Atlas"]
        assert atlas["lambs"] == 3
        assert atlas["lost"] == 1
        assert atlas["raised"] == 2

    def test_year_over_year(self, farm_data: FarmData):
        report = loss_report(farm_data)
        yoy = {y["year"]: y for y in report["year_over_year"]}
        # 2026 should be present
        assert 2026 in yoy
        y26 = yoy[2026]
        assert y26["born"] == 5
        assert y26["raised"] == 4
        assert y26["stillborn"] == 1

    def test_year_over_year_survival_rate(self, farm_data: FarmData):
        report = loss_report(farm_data)
        yoy = {y["year"]: y for y in report["year_over_year"]}
        # 4 raised / 5 born = 80%
        assert yoy[2026]["rate"] == 80.0

    def test_report_has_season(self, farm_data: FarmData):
        report = loss_report(farm_data)
        assert report["season"] == 2026

    def test_detailed_loss_records_override(self, farm_data: FarmData):
        """Loss records with explicit categories should be used."""
        farm_data.loss_records = [{"animalId": "l-2026-5", "category": "intrapartum"}]
        report = loss_report(farm_data)
        assert report["summary"]["intrapartum"] == 1
        assert report["summary"]["stillborn"] == 0

    def test_early_loss_counted(self):
        """An early loss (days_reared 1-90) should appear in summary."""
        sire_ref = _parent("s1", name="Sire")
        dam_ref = _parent("d1", name="Dam")
        dam = _animal(animal_id="d1", name="Dam", birth_year=2020)
        sire = _animal(animal_id="s1", name="Sire", sex="Male", age_class="ram")
        lamb = _animal(
            animal_id="dead-early", name="Early", birth_year=2026,
            fate="Dead", days_reared=5, on_farm=False,
            sires=[sire_ref], dams=[dam_ref],
        )
        data = FarmData(
            animals=[dam, sire, lamb],
            by_id={a["animalId"]: a for a in [dam, sire, lamb]},
            service_groups=[],
            loss_records=[],
            season=2026,
        )
        report = loss_report(data)
        assert report["summary"]["early_loss"] == 1
        assert report["summary"]["total"] == 1

    def test_sold_not_counted_as_loss(self, farm_data: FarmData):
        """Sold lambs should not appear as losses."""
        report = loss_report(farm_data)
        # Lamb D was sold - should not be in losses
        for d in report["by_dam"]:
            for detail in d["detail"].split("; "):
                assert "Lamb D" not in detail

    def test_empty_flock(self):
        """Loss report on an empty flock should not raise."""
        data = FarmData(animals=[], by_id={}, service_groups=[], loss_records=[], season=2026)
        report = loss_report(data)
        assert report["summary"]["total"] == 0
        assert report["by_dam"] == []
        assert report["by_sire"] == []
        assert report["year_over_year"] == []
