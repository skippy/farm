"""Tests for the lineage/breed-purity MCP tools."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from conftest import make_animal as _animal
from conftest import make_parent as _parent

from agriwebb.analysis.lambing.loader import FarmData
from agriwebb.analysis.lineage.purity import Founder, FounderRegistry
from agriwebb.mcp_server import get_breed_purity, get_purebred_animals, get_unclassified_founders


def _ext(name: str | None = None, vid: str | None = None) -> dict:
    return {
        "parentAnimalId": None,
        "parentAnimalIdentity": {"name": name, "vid": vid, "eid": None},
        "parentType": "Genetic",
    }


@pytest.fixture
def herd():
    solar = _animal(
        "solar",
        name="Solar",
        breed="Finish Landrace",
        sex="Male",
        on_farm=False,
        sires=[_ext("Conroy")],
        dams=[_ext("Delia")],
    )
    jazz = _animal("jazz", name="Jazz", breed="Finish Landrace", sires=[_ext("YR2015")], dams=[_ext("Luann")])
    morgan = _animal(
        "morgan", name="Morgan", breed="Bluefaced Leicester X", sires=[_ext("Blue Man")], dams=[_ext(vid="WW02")]
    )
    bocce = _animal(
        "bocce",
        name="Bocce",
        breed="Finish Landrace",
        sex="Male",
        birth_year=2025,
        sires=[_parent("solar")],
        dams=[_parent("jazz")],
    )
    bingo = _animal(
        "bingo",
        name="Bingo",
        breed="Finish Landrace",
        sex="Male",
        birth_year=2025,
        sires=[_parent("solar")],
        dams=[_parent("morgan")],
    )
    sansa = _animal("sansa", name="Sansa", breed="North Country Cheviot", birth_year=2017, on_farm=False)
    animals = [solar, jazz, morgan, bocce, bingo, sansa]
    return FarmData(animals=animals, by_id={a["animalId"]: a for a in animals}, season=2026)


@pytest.fixture
def registry():
    return FounderRegistry(
        [
            Founder(id="Conroy", breed="Finnish Landrace", purebred=True),
            Founder(id="Delia", breed="Finnish Landrace", purebred=True),
            Founder(id="YR2015", breed="Finnish Landrace", purebred=True),
            Founder(id="Luann", breed="Finnish Landrace", purebred=True),
            Founder(id="BLUEMAN", aliases=["Blue Man"], breed="Bluefaced Leicester", purebred=True),
            Founder(id="WW02", breed="Bluefaced Leicester X", purebred=None),
        ]
    )


@pytest.fixture(autouse=True)
def _patch(herd, registry):
    with (
        patch("agriwebb.mcp_server._farm_data", return_value=herd),
        patch("agriwebb.mcp_server._founder_registry", return_value=registry),
    ):
        yield


class TestGetBreedPurity:
    async def test_purebred(self):
        r = json.loads(await get_breed_purity("Bocce"))
        assert r["purebred"] is True
        assert r["purebredBreed"] == "Finnish Landrace"
        assert r["breedLabel"] == "Finish Landrace"
        assert "purebred_breed" not in r  # camelCase at the MCP boundary
        assert r["composition"] == {"Finnish Landrace": 1.0}
        assert sorted(r["founders"]) == ["Conroy", "Delia", "Luann", "YR2015"]

    async def test_mixed_reports_unresolved_too(self):
        r = json.loads(await get_breed_purity("bingo"))
        assert r["purebred"] is False
        assert r["status"] == "mixed"
        assert r["unresolved"] == ["WW02"]

    async def test_not_found(self):
        r = json.loads(await get_breed_purity("Nobody"))
        assert "error" in r


class TestGetPurebredAnimals:
    async def test_groups_and_counts(self):
        r = json.loads(await get_purebred_animals("Finn", year=2025))
        assert r["breed"] == "Finnish Landrace"
        assert r["counts"] == {"purebred": 1, "unverified": 0, "mixed": 1}
        assert r["purebred"][0]["name"] == "Bocce"
        assert r["mixed"][0]["name"] == "Bingo"

    async def test_unverified_lists_needs(self, registry):
        # Make Luann unclassified -> Bocce becomes unverified
        registry.match_label("Luann").purebred = None
        r = json.loads(await get_purebred_animals("Finnish Landrace", year=2025))
        assert r["counts"]["unverified"] == 1
        assert r["unverified"][0]["needs"] == ["Luann"]

    async def test_on_farm_filter(self):
        r = json.loads(await get_purebred_animals("Finn", on_farm_only=False))
        names = {x["name"] for x in r["purebred"]}
        assert {"Bocce", "Jazz", "Solar"} <= names
        r2 = json.loads(await get_purebred_animals("Finn", on_farm_only=True))
        assert "Solar" not in {x["name"] for x in r2["purebred"]}


class TestGetUnclassifiedFounders:
    async def test_lists_only_unresolved_sorted_by_descendants(self):
        r = json.loads(await get_unclassified_founders())
        keys = [f["key"] for f in r["founders"]]
        assert keys == ["WW02", "sansa"]  # WW02 has descendants (Morgan, Bingo); Sansa none
        assert r["founders"][0]["descendants"] == 2
        assert r["founders"][1]["inCache"] is True
        assert r["founders"][1]["animalId"] == "sansa"
        assert "registryPath" not in r  # no host paths in MCP output
