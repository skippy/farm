"""Tests for the livestock module."""

import httpx
import pytest

from agriwebb.core.client import GraphQLError
from agriwebb.data import livestock


def make_parent(parent_id: str, vid: str) -> dict:
    """Create a mock parent reference in AgriWebb API format."""
    return {
        "parentAnimalId": parent_id,
        "parentAnimalIdentity": {"vid": vid, "name": None, "eid": None},
    }


def make_animal(
    animal_id: str = "a1",
    vid: str = "001",
    name: str | None = None,
    eid: str | None = None,
    breed: str = "Angus",
    species: str = "CATTLE",
    sex: str = "FEMALE",
    birth_year: int = 2020,
    on_farm: bool = True,
    sires: list | None = None,
    dams: list | None = None,
) -> dict:
    """Create a mock animal in AgriWebb API format."""
    return {
        "animalId": animal_id,
        "identity": {
            "vid": vid,
            "name": name,
            "eid": eid,
            "managementTag": None,
        },
        "characteristics": {
            "breedAssessed": breed,
            "speciesCommonName": species,
            "sex": sex,
            "birthYear": birth_year,
            "birthDate": None,
            "visualColor": None,
            "ageClass": None,
        },
        "state": {
            "onFarm": on_farm,
            "currentLocationId": None,
            "fate": None if on_farm else "SOLD",
            "reproductiveStatus": None,
            "offspringCount": None,
        },
        "parentage": {
            "sires": sires or [],
            "dams": dams or [],
        },
        "managementGroup": None,
    }


class TestGetAnimals:
    """Tests for the get_animals function."""

    async def test_returns_animal_list(self, mock_agriwebb):
        """Verify animals are returned."""
        mock_agriwebb.post("/v2").mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": {
                        "animals": [
                            make_animal("a1", "001", breed="Angus"),
                            make_animal("a2", "002", breed="Hereford"),
                        ]
                    }
                },
            )
        )

        result = await livestock.get_animals()

        assert len(result) == 2
        assert result[0]["visualTag"] == "001"

    async def test_filters_by_status(self, mock_agriwebb):
        """Verify status filter is applied."""
        route = mock_agriwebb.post("/v2").mock(return_value=httpx.Response(200, json={"data": {"animals": []}}))

        await livestock.get_animals(status="onFarm")

        body = route.calls[0].request.content.decode()
        assert "onFarm" in body

    async def test_raises_on_error(self, mock_agriwebb):
        """Verify error raised on GraphQL errors."""
        mock_agriwebb.post("/v2").mock(return_value=httpx.Response(200, json={"errors": [{"message": "Failed"}]}))

        with pytest.raises(GraphQLError, match="Failed"):
            await livestock.get_animals()


class TestFindAnimal:
    """Tests for the find_animal function."""

    async def test_finds_by_id_or_tag(self, mock_agriwebb):
        """Verify lookup by ID, EID, tag, or name works."""
        # First query by animalId returns empty
        # Second query by name returns the animal
        mock_agriwebb.post("/v2").mock(
            side_effect=[
                httpx.Response(200, json={"data": {"animals": []}}),  # by animalId
                httpx.Response(200, json={"data": {"animals": [make_animal("a1", "001")]}}),  # by name
            ]
        )

        result = await livestock.find_animal("001")
        assert result["id"] == "a1"

    async def test_raises_when_not_found(self, mock_agriwebb):
        """Verify error when no match."""
        mock_agriwebb.post("/v2").mock(return_value=httpx.Response(200, json={"data": {"animals": []}}))

        with pytest.raises(ValueError, match="No animal found"):
            await livestock.find_animal("missing")

    async def test_raises_on_multiple_matches(self, mock_agriwebb):
        """Verify error when multiple animals match."""
        # First query by animalId returns empty, second by name returns 2 animals
        mock_agriwebb.post("/v2").mock(
            side_effect=[
                httpx.Response(200, json={"data": {"animals": []}}),  # by animalId
                httpx.Response(
                    200,
                    json={
                        "data": {
                            "animals": [
                                make_animal("a1", "001"),
                                make_animal("a2", "002"),
                            ]
                        }
                    },
                ),  # by name - multiple matches
            ]
        )

        with pytest.raises(ValueError, match="Multiple animals match"):
            await livestock.find_animal("ambiguous")


class TestGetAnimal:
    """Tests for the get_animal function."""

    async def test_returns_animal_details(self, mock_agriwebb):
        """Verify single animal is returned."""
        # First query by animalId returns the animal
        mock_agriwebb.post("/v2").mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": {
                        "animals": [
                            make_animal(
                                "a1",
                                "001",
                                breed="Angus",
                                sex="FEMALE",
                            )
                        ]
                    }
                },
            )
        )

        result = await livestock.get_animal("a1")

        assert result["id"] == "a1"
        assert result["breed"] == "Angus"

    async def test_raises_when_not_found(self, mock_agriwebb):
        """Verify error raised when animal not found."""
        mock_agriwebb.post("/v2").mock(return_value=httpx.Response(200, json={"data": {"animals": []}}))

        with pytest.raises(ValueError, match="No animal found"):
            await livestock.get_animal("missing")


class TestGetAnimalLineage:
    """Tests for the get_animal_lineage function."""

    async def test_returns_nested_lineage(self, mock_agriwebb):
        """Verify lineage with sire/dam is returned."""
        # First call finds the animal with parentage refs
        # Subsequent calls fetch sire and dam details
        mock_agriwebb.post("/v2").mock(
            side_effect=[
                # First: find animal by ID
                httpx.Response(
                    200,
                    json={
                        "data": {
                            "animals": [
                                make_animal(
                                    "a1",
                                    "001",
                                    sires=[make_parent("s1", "S001")],
                                    dams=[make_parent("d1", "D001")],
                                )
                            ]
                        }
                    },
                ),
                # Second: fetch sire by ID
                httpx.Response(200, json={"data": {"animals": [make_animal("s1", "S001", breed="Angus Sire")]}}),
                # Third: fetch dam by ID
                httpx.Response(200, json={"data": {"animals": [make_animal("d1", "D001", breed="Angus Dam")]}}),
            ]
        )

        result = await livestock.get_animal_lineage("a1", generations=1)

        assert result["sire"]["visualTag"] == "S001"
        assert result["dam"]["visualTag"] == "D001"


class TestGetOffspring:
    """Tests for the get_offspring function."""

    async def test_combines_sire_and_dam_offspring(self, mock_agriwebb):
        """Verify offspring from parentage are found."""
        mock_agriwebb.post("/v2").mock(
            side_effect=[
                # First call: find_animal (resolve_animal_id)
                httpx.Response(200, json={"data": {"animals": [make_animal("parent-id", "P01")]}}),
                # Second call: get all animals to filter for offspring
                httpx.Response(
                    200,
                    json={
                        "data": {
                            "animals": [
                                make_animal("o1", "O01", sires=[make_parent("parent-id", "P01")]),
                                make_animal("o2", "O02", dams=[make_parent("parent-id", "P01")]),
                                make_animal("other", "X01"),  # not offspring
                            ]
                        }
                    },
                ),
            ]
        )

        result = await livestock.get_offspring("parent-id")

        assert len(result) == 2

    async def test_deduplicates_offspring(self, mock_agriwebb):
        """Verify duplicate offspring are removed."""
        parent = make_parent("parent-id", "P01")
        mock_agriwebb.post("/v2").mock(
            side_effect=[
                # First call: find_animal (resolve_animal_id)
                httpx.Response(200, json={"data": {"animals": [make_animal("parent-id", "P01")]}}),
                # Second call: get all animals - offspring appears as both sire and dam offspring
                httpx.Response(
                    200,
                    json={
                        "data": {
                            "animals": [
                                make_animal("o1", "O01", sires=[parent], dams=[parent]),
                            ]
                        }
                    },
                ),
            ]
        )

        result = await livestock.get_offspring("parent-id")

        assert len(result) == 1


class TestGetMobs:
    """Tests for the get_mobs function."""

    async def test_returns_mob_list(self, mock_agriwebb):
        """Verify mobs are returned."""
        mock_agriwebb.post("/v2").mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": {
                        "managementGroups": [
                            {"id": "m1", "name": "Herd A", "animalCount": 25},
                            {"id": "m2", "name": "Herd B", "animalCount": 30},
                        ]
                    }
                },
            )
        )

        result = await livestock.get_mobs()

        assert len(result) == 2
        assert result[0]["animalCount"] == 25


class TestFormatLineageTree:
    """Tests for the format_lineage_tree function."""

    def test_formats_simple_animal(self):
        """Verify basic animal formatting."""
        animal = {"visualTag": "001", "breed": "Angus", "birthYear": 2020}

        result = livestock.format_lineage_tree(animal)

        assert "001" in result
        assert "Angus" in result
        assert "2020" in result

    def test_formats_nested_lineage(self):
        """Verify nested sire/dam formatting."""
        animal = {
            "visualTag": "001",
            "sire": {"visualTag": "S01", "breed": "Angus"},
            "dam": {"visualTag": "D01", "breed": "Hereford"},
        }

        result = livestock.format_lineage_tree(animal)

        assert "001" in result
        assert "Sire:" in result
        assert "S01" in result
        assert "Dam:" in result
        assert "D01" in result


class TestSummarizeAnimals:
    """Tests for the summarize_animals function."""

    def test_counts_by_category(self):
        """Verify counts are correct."""
        animals = [
            {"species": "CATTLE", "breed": "Angus", "sex": "FEMALE", "status": "onFarm"},
            {"species": "CATTLE", "breed": "Angus", "sex": "MALE", "status": "onFarm"},
            {"species": "CATTLE", "breed": "Hereford", "sex": "FEMALE", "status": "sold"},
        ]

        result = livestock.summarize_animals(animals)

        assert result["total"] == 3
        assert result["by_species"]["CATTLE"] == 3
        assert result["by_breed"]["Angus"] == 2
        assert result["by_breed"]["Hereford"] == 1
        assert result["by_sex"]["FEMALE"] == 2
        assert result["by_status"]["onFarm"] == 2
