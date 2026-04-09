"""Tests for the AgriWebb MCP server tools.

Each tool function is called directly (not via MCP protocol) with a mocked
``load_farm_data()`` that returns a small FarmData with ~10 test animals.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from agriwebb.analysis.lambing.loader import FarmData
from agriwebb.mcp_server import (
    get_ancestors,
    get_animal,
    get_breedable_ewes,
    get_joining_groups,
    get_lambing_season,
    get_lambs,
    get_litter,
    get_ncc_compatibility,
    get_offspring,
    get_sire_stats,
)

# ---------------------------------------------------------------------------
# Test data builders (mirrors test_lambing_loader.py)
# ---------------------------------------------------------------------------


def _parent(parent_id: str, name: str | None = None, vid: str | None = None) -> dict:
    """Build a parent reference in AgriWebb format."""
    return {
        "parentAnimalId": parent_id,
        "parentAnimalIdentity": {"name": name, "vid": vid, "eid": None},
        "parentType": "Genetic",
    }


def _animal(
    animal_id: str = "a1",
    name: str | None = None,
    vid: str | None = None,
    eid: str | None = None,
    breed: str = "North Country Cheviot",
    sex: str = "Female",
    age_class: str = "ewe",
    birth_year: int = 2022,
    on_farm: bool = True,
    fate: str = "Alive",
    days_reared: int | None = 500,
    sires: list | None = None,
    dams: list | None = None,
) -> dict:
    """Build a minimal animal dict matching the animals.json shape."""
    return {
        "animalId": animal_id,
        "identity": {
            "name": name,
            "vid": vid,
            "eid": eid,
            "managementTag": None,
        },
        "characteristics": {
            "breedAssessed": breed,
            "sex": sex,
            "ageClass": age_class,
            "birthYear": birth_year,
            "birthDate": None,
            "speciesCommonName": "Sheep",
            "visualColor": None,
        },
        "state": {
            "onFarm": on_farm,
            "fate": fate,
            "daysReared": days_reared,
            "currentLocationId": None,
            "reproductiveStatus": None,
            "offspringCount": None,
        },
        "parentage": {
            "sires": sires or [],
            "dams": dams or [],
        },
    }


# ---------------------------------------------------------------------------
# Shared fixture: a small herd with realistic relationships
# ---------------------------------------------------------------------------


@pytest.fixture
def farm_data():
    """Build a FarmData with ~10 animals for testing all tools.

    Family tree:
        GrandSire (gs) + GrandDam (gd) -> Dam Daisy (ewe-1)
        Ram Big John (ram-1) + Daisy -> Lamb A (alive), Lamb B (sold), Lamb C (dead)
        Ram Atlas (ram-2) + Fern (ewe-2) -> Lamb D (alive, 2025)
        Maiden ewe Holly (ewe-3) -- no offspring, Finnsheep

    Service groups: Big John joined with Daisy; Atlas joined with Fern
    """
    sire_ref_john = _parent("ram-1", name="Big John")
    dam_ref_daisy = _parent("ewe-1", name="Daisy")
    sire_ref_atlas = _parent("ram-2", name="Atlas")
    dam_ref_fern = _parent("ewe-2", name="Fern")

    grandsire = _animal(
        animal_id="gs",
        name="GrandSire",
        sex="Male",
        age_class="ram",
        birth_year=2018,
        on_farm=False,
        fate="Sold",
    )
    granddam = _animal(
        animal_id="gd",
        name="GrandDam",
        birth_year=2017,
        on_farm=False,
        fate="Sold",
    )
    ram_john = _animal(
        animal_id="ram-1",
        name="Big John",
        breed="North Country Cheviot",
        sex="Male",
        age_class="ram",
        birth_year=2020,
    )
    ram_atlas = _animal(
        animal_id="ram-2",
        name="Atlas",
        breed="North Country Cheviot",
        sex="Male",
        age_class="ram",
        birth_year=2021,
    )
    ewe_daisy = _animal(
        animal_id="ewe-1",
        name="Daisy",
        breed="North Country Cheviot",
        birth_year=2021,
        sires=[_parent("gs", name="GrandSire")],
        dams=[_parent("gd", name="GrandDam")],
    )
    ewe_fern = _animal(
        animal_id="ewe-2",
        name="Fern",
        breed="North Country Cheviot",
        birth_year=2022,
    )
    ewe_holly = _animal(
        animal_id="ewe-3",
        name="Holly",
        breed="Finnsheep",
        age_class="maiden_ewe",
        birth_year=2023,
    )
    lamb_a = _animal(
        animal_id="lamb-1",
        name="Lamb A",
        birth_year=2026,
        age_class="ewe_lamb",
        sires=[sire_ref_john],
        dams=[dam_ref_daisy],
    )
    lamb_b = _animal(
        animal_id="lamb-2",
        vid="L02",
        birth_year=2026,
        sex="Male",
        age_class="ram_lamb",
        fate="Sold",
        on_farm=False,
        sires=[sire_ref_john],
        dams=[dam_ref_daisy],
    )
    lamb_c = _animal(
        animal_id="lamb-3",
        vid="L03",
        birth_year=2026,
        sex="Male",
        age_class="ram_lamb",
        fate="Dead",
        days_reared=0,
        on_farm=False,
        sires=[sire_ref_john],
        dams=[dam_ref_daisy],
    )
    lamb_d = _animal(
        animal_id="lamb-4",
        name="Lamb D",
        birth_year=2025,
        age_class="ewe_lamb",
        sires=[sire_ref_atlas],
        dams=[dam_ref_fern],
    )

    animals = [
        grandsire,
        granddam,
        ram_john,
        ram_atlas,
        ewe_daisy,
        ewe_fern,
        ewe_holly,
        lamb_a,
        lamb_b,
        lamb_c,
        lamb_d,
    ]
    by_id = {a["animalId"]: a for a in animals}

    service_groups = [
        {
            "sire_name": "Big John",
            "ewe_ids": ["ewe-1"],
            "start_date": "2025-10-01",
            "end_date": "2025-12-01",
            "pasture": "North Pasture",
        },
        {
            "sire_name": "Atlas",
            "ewe_ids": ["ewe-2"],
            "start_date": "2025-10-15",
            "end_date": "2025-12-15",
            "pasture": "South Pasture",
        },
    ]

    loss_records = [
        {"animalId": "lamb-3", "category": "intrapartum"},
    ]

    return FarmData(
        animals=animals,
        by_id=by_id,
        service_groups=service_groups,
        loss_records=loss_records,
        season=2026,
    )


@pytest.fixture(autouse=True)
def _mock_load(farm_data):
    """Patch load_farm_data to return our test FarmData for all tools."""
    with patch("agriwebb.mcp_server._farm_data", return_value=farm_data):
        yield


def _parse(result: str) -> dict:
    """Parse a JSON tool result string."""
    return json.loads(result)


# ---------------------------------------------------------------------------
# get_animal
# ---------------------------------------------------------------------------


class TestGetAnimal:
    async def test_by_name(self):
        result = _parse(await get_animal("Big John"))
        assert result["name"] == "Big John"
        assert result["breed"] == "North Country Cheviot"
        assert result["sex"] == "Male"

    async def test_by_vid(self):
        result = _parse(await get_animal("L02"))
        assert result["animalId"] == "lamb-2"

    async def test_by_animal_id(self):
        result = _parse(await get_animal("ewe-1"))
        assert result["name"] == "Daisy"

    async def test_case_insensitive(self):
        result = _parse(await get_animal("big john"))
        assert result["name"] == "Big John"

    async def test_not_found(self):
        result = _parse(await get_animal("Nonexistent"))
        assert "error" in result
        assert "Nonexistent" in result["error"]

    async def test_includes_parentage(self):
        result = _parse(await get_animal("Lamb A"))
        assert result["sire"] == "Big John"
        assert result["dam"] == "Daisy"


# ---------------------------------------------------------------------------
# get_offspring
# ---------------------------------------------------------------------------


class TestGetOffspring:
    async def test_by_dam(self):
        result = _parse(await get_offspring("Daisy"))
        assert result["parent"] == "Daisy"
        assert result["count"] == 3

    async def test_by_sire(self):
        result = _parse(await get_offspring("Big John"))
        assert result["count"] == 3

    async def test_filter_by_year(self):
        result = _parse(await get_offspring("Atlas", year=2025))
        assert result["count"] == 1
        assert result["offspring"][0]["name"] == "Lamb D"

    async def test_no_offspring(self):
        result = _parse(await get_offspring("Holly"))
        assert result["count"] == 0

    async def test_parent_not_found(self):
        result = _parse(await get_offspring("Ghost"))
        assert "error" in result


# ---------------------------------------------------------------------------
# get_ancestors
# ---------------------------------------------------------------------------


class TestGetAncestors:
    async def test_finds_grandparents(self):
        result = _parse(await get_ancestors("Lamb A", max_depth=4))
        assert result["animal"] == "Lamb A"
        # Should find Big John (sire), Daisy (dam), GrandSire, GrandDam
        ancestors = result["ancestors"]
        assert "Big John" in ancestors or "BIG JOHN" in ancestors
        assert "Daisy" in ancestors or "DAISY" in ancestors

    async def test_depth_limit(self):
        # depth=0 should find parents but not grandparents
        result = _parse(await get_ancestors("Lamb A", max_depth=0))
        ancestors = result["ancestors"]
        # Parents should be there
        has_john = "Big John" in ancestors or "BIG JOHN" in ancestors
        assert has_john
        # Grandparents should NOT be reachable at depth 0
        has_grandsire = "GrandSire" in ancestors or "GRANDSIRE" in ancestors
        assert not has_grandsire

    async def test_not_found(self):
        result = _parse(await get_ancestors("Nobody"))
        assert "error" in result

    async def test_no_ancestors(self):
        result = _parse(await get_ancestors("Big John"))
        assert result["ancestorCount"] == 0


# ---------------------------------------------------------------------------
# get_litter
# ---------------------------------------------------------------------------


class TestGetLitter:
    async def test_litter_from_daisy(self):
        result = _parse(await get_litter("Daisy", 2026))
        assert result["dam"] == "Daisy"
        assert result["litterSize"] == 3

    async def test_litter_outcomes(self):
        result = _parse(await get_litter("Daisy", 2026))
        outcomes = {lamb["name"] or lamb.get("animalId"): lamb["outcome"] for lamb in result["lambs"]}
        assert outcomes["Lamb A"] == "alive"
        # Lamb B (sold) = raised
        sold_lamb = [x for x in result["lambs"] if x["animalId"] == "lamb-2"][0]
        assert sold_lamb["outcome"] == "raised"
        # Lamb C (dead) = loss
        dead_lamb = [x for x in result["lambs"] if x["animalId"] == "lamb-3"][0]
        assert dead_lamb["outcome"] == "loss"

    async def test_wrong_year(self):
        result = _parse(await get_litter("Daisy", 2025))
        assert result["litterSize"] == 0

    async def test_dam_not_found(self):
        result = _parse(await get_litter("Nobody", 2026))
        assert "error" in result


# ---------------------------------------------------------------------------
# get_lambing_season
# ---------------------------------------------------------------------------


class TestGetLambingSeason:
    async def test_season_2026(self):
        result = _parse(await get_lambing_season(2026))
        assert result["season"] == 2026
        headline = result["headline"]
        assert headline["live_lambs"] == 2  # A alive, B sold (was_raised)
        assert headline["ewes_lambed"] >= 1
        assert "lambing_rate_per_lambed" in headline

    async def test_season_default_year(self):
        """When year matches the fixture season (2026), returns correct data."""
        result = _parse(await get_lambing_season())
        # Default year = current year; fixture season = 2026
        assert "headline" in result

    async def test_season_has_by_sire(self):
        result = _parse(await get_lambing_season(2026))
        assert "by_sire" in result

    async def test_has_litter_distribution(self):
        result = _parse(await get_lambing_season(2026))
        assert "litter_distribution" in result


# ---------------------------------------------------------------------------
# get_lambs
# ---------------------------------------------------------------------------


class TestGetLambs:
    async def test_lambs_2026(self):
        result = _parse(await get_lambs(2026))
        assert result["year"] == 2026
        assert result["count"] > 0
        assert "lambs" in result

    async def test_lambs_include_outcome(self):
        result = _parse(await get_lambs(2026))
        for lamb in result["lambs"]:
            assert "outcome" in lamb

    async def test_lambs_filter_by_sire(self):
        result = _parse(await get_lambs(2026, sire="Big John"))
        assert result["count"] > 0
        for lamb in result["lambs"]:
            assert lamb["sire"] == "Big John"

    async def test_lambs_filter_by_dam(self):
        result = _parse(await get_lambs(2026, dam="Daisy"))
        for lamb in result["lambs"]:
            assert lamb["dam"] == "Daisy"

    async def test_lambs_no_results(self):
        result = _parse(await get_lambs(2010))
        assert result["count"] == 0


# ---------------------------------------------------------------------------
# get_sire_stats
# ---------------------------------------------------------------------------


class TestGetSireStats:
    async def test_specific_sire(self):
        result = _parse(await get_sire_stats("Big John"))
        assert result["sire"] == "Big John"
        assert result["totalOffspring"] == 3
        assert result["raised"] == 2  # alive + sold
        assert result["losses"] == 1
        assert "lossRate" in result

    async def test_all_sires(self):
        result = _parse(await get_sire_stats())
        assert "sires" in result
        sire_names = {s["sire"] for s in result["sires"]}
        assert "Big John" in sire_names
        assert "Atlas" in sire_names

    async def test_sire_not_found(self):
        result = _parse(await get_sire_stats("Nobody"))
        assert "error" in result


# ---------------------------------------------------------------------------
# get_joining_groups
# ---------------------------------------------------------------------------


class TestGetJoiningGroups:
    async def test_has_groups(self):
        result = _parse(await get_joining_groups(2026))
        assert len(result["groups"]) == 2
        sires = {g["sire"] for g in result["groups"]}
        assert "Big John" in sires
        assert "Atlas" in sires

    async def test_group_details(self):
        result = _parse(await get_joining_groups(2026))
        john_group = [g for g in result["groups"] if g["sire"] == "Big John"][0]
        assert john_group["eweCount"] == 1
        assert "Daisy" in john_group["ewes"]
        assert john_group["pasture"] == "North Pasture"


# ---------------------------------------------------------------------------
# get_ncc_compatibility
# ---------------------------------------------------------------------------


class TestGetNccCompatibility:
    async def test_no_shared_ancestors(self):
        # Atlas and Fern have no shared ancestors in our test data
        result = _parse(await get_ncc_compatibility("Atlas", "Fern"))
        assert result["sharedCount"] == 0
        assert result["inbreedingRisk"] == "none detected"

    async def test_ram_not_found(self):
        result = _parse(await get_ncc_compatibility("Ghost", "Fern"))
        assert "error" in result

    async def test_ewe_not_found(self):
        result = _parse(await get_ncc_compatibility("Atlas", "Ghost"))
        assert "error" in result

    async def test_returns_names(self):
        result = _parse(await get_ncc_compatibility("Atlas", "Daisy"))
        assert result["ram"] == "Atlas"
        assert result["ewe"] == "Daisy"


# ---------------------------------------------------------------------------
# get_breedable_ewes
# ---------------------------------------------------------------------------


class TestGetBreedableEwes:
    async def test_all_breeds(self):
        result = _parse(await get_breedable_ewes())
        # Daisy (ewe), Fern (ewe), Holly (maiden_ewe) are on-farm ewes
        assert result["count"] == 3
        assert result["breed"] == "all"

    async def test_filter_by_breed(self):
        result = _parse(await get_breedable_ewes(breed="Finnsheep"))
        assert result["count"] == 1
        assert result["ewes"][0]["name"] == "Holly"

    async def test_filter_case_insensitive(self):
        result = _parse(await get_breedable_ewes(breed="finnsheep"))
        assert result["count"] == 1

    async def test_no_matches(self):
        result = _parse(await get_breedable_ewes(breed="Merino"))
        assert result["count"] == 0
        assert result["ewes"] == []
