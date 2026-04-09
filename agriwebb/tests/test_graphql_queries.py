"""Characterization tests for GraphQL query building.

These tests capture the CURRENT behavior of f-string-based query construction
before converting to parameterized GraphQL variables. After the fix, these
tests should still pass — proving the fix didn't break functionality.

Tested functions:
  - livestock.find_animal() — 4 query paths (by ID, name, vid, eid)
  - client.update_map_feature() — mutation with f-string interpolation
  - weather_api.get_rainfalls() — date-filtered query path
  - pasture_api.get_pasture_growth_rates() — date-filtered query path
"""

import json

import httpx
import pytest

from agriwebb.data import livestock
from agriwebb.pasture import api as pasture_api
from agriwebb.weather import api as weather_api

# =============================================================================
# Helpers
# =============================================================================


def make_animal(
    animal_id: str = "a1",
    vid: str = "001",
    name: str | None = None,
    eid: str | None = None,
    breed: str = "Finnsheep",
    species: str = "SHEEP",
    sex: str = "FEMALE",
    birth_year: int = 2023,
    on_farm: bool = True,
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
            "sires": [],
            "dams": [],
        },
        "managementGroup": None,
    }


def empty_animals_response():
    """Return an empty animals response."""
    return httpx.Response(200, json={"data": {"animals": []}})


def animals_response(*animals):
    """Return an animals response containing the given animals."""
    return httpx.Response(200, json={"data": {"animals": list(animals)}})


# =============================================================================
# find_animal() — Query Building Tests
# =============================================================================


class TestFindAnimalQueryPaths:
    """Test that find_animal builds correct queries for each lookup strategy."""

    async def test_finds_by_animal_id_first_query(self, mock_agriwebb):
        """When the first query (by animalId) succeeds, return immediately."""
        animal = make_animal("abc-123", vid="T001", name="Daisy")

        mock_agriwebb.post("/v2").mock(
            side_effect=[
                animals_response(animal),  # by animalId — found!
            ]
        )

        result = await livestock.find_animal("abc-123")

        assert result["id"] == "abc-123"
        assert result["visualTag"] == "T001"
        assert result["name"] == "Daisy"

    async def test_finds_by_name_second_query(self, mock_agriwebb):
        """When animalId query returns empty, try by name."""
        animal = make_animal("abc-123", vid="T001", name="Clover")

        mock_agriwebb.post("/v2").mock(
            side_effect=[
                empty_animals_response(),   # by animalId — not found
                animals_response(animal),   # by name — found!
            ]
        )

        result = await livestock.find_animal("Clover")

        assert result["id"] == "abc-123"
        assert result["name"] == "Clover"

    async def test_finds_by_vid_third_query(self, mock_agriwebb):
        """When animalId and name queries return empty, try by vid."""
        animal = make_animal("abc-123", vid="T042")

        mock_agriwebb.post("/v2").mock(
            side_effect=[
                empty_animals_response(),   # by animalId
                empty_animals_response(),   # by name
                animals_response(animal),   # by vid — found!
            ]
        )

        result = await livestock.find_animal("T042")

        assert result["id"] == "abc-123"
        assert result["visualTag"] == "T042"

    async def test_finds_by_eid_fourth_query(self, mock_agriwebb):
        """When all other queries return empty, try by eid."""
        animal = make_animal("abc-123", eid="840-000-123456789")

        mock_agriwebb.post("/v2").mock(
            side_effect=[
                empty_animals_response(),   # by animalId
                empty_animals_response(),   # by name
                empty_animals_response(),   # by vid
                animals_response(animal),   # by eid — found!
            ]
        )

        result = await livestock.find_animal("840-000-123456789")

        assert result["id"] == "abc-123"
        assert result["eid"] == "840-000-123456789"

    async def test_raises_when_nothing_found(self, mock_agriwebb):
        """When all 4 queries return empty, raise ValueError."""
        mock_agriwebb.post("/v2").mock(
            return_value=empty_animals_response(),
        )

        with pytest.raises(ValueError, match="No animal found matching 'ghost'"):
            await livestock.find_animal("ghost")

    async def test_query_includes_identifier_in_variables(self, mock_agriwebb):
        """Verify the identifier is passed as a GraphQL variable."""
        route = mock_agriwebb.post("/v2").mock(
            side_effect=[
                animals_response(make_animal("test-id-99")),
            ]
        )

        await livestock.find_animal("test-id-99")

        payload = json.loads(route.calls[0].request.content)
        assert payload["variables"]["identifier"] == "test-id-99"
        # Identifier should NOT be interpolated into the query string
        assert "test-id-99" not in payload["query"]

    async def test_query_includes_farm_id_in_variables(self, mock_agriwebb):
        """Verify the farm ID is passed as a GraphQL variable."""
        from agriwebb.core.config import settings

        route = mock_agriwebb.post("/v2").mock(
            side_effect=[
                animals_response(make_animal("a1")),
            ]
        )

        await livestock.find_animal("a1")

        payload = json.loads(route.calls[0].request.content)
        assert payload["variables"]["farmId"] == settings.agriwebb_farm_id
        # Farm ID should NOT be interpolated into the query string
        assert settings.agriwebb_farm_id not in payload["query"]

    async def test_multiple_matches_by_name_raises(self, mock_agriwebb):
        """When name query returns multiple animals, raise ValueError."""
        mock_agriwebb.post("/v2").mock(
            side_effect=[
                empty_animals_response(),  # by animalId
                animals_response(
                    make_animal("a1", vid="T001"),
                    make_animal("a2", vid="T002"),
                ),  # by name — multiple!
            ]
        )

        with pytest.raises(ValueError, match="Multiple animals match"):
            await livestock.find_animal("Daisy")

    async def test_multiple_matches_by_vid_raises(self, mock_agriwebb):
        """When vid query returns multiple animals, raise ValueError."""
        mock_agriwebb.post("/v2").mock(
            side_effect=[
                empty_animals_response(),  # by animalId
                empty_animals_response(),  # by name
                animals_response(
                    make_animal("a1", vid="001"),
                    make_animal("a2", vid="001"),
                ),  # by vid — multiple!
            ]
        )

        with pytest.raises(ValueError, match="Multiple animals match"):
            await livestock.find_animal("001")

    async def test_multiple_matches_by_eid_raises(self, mock_agriwebb):
        """When eid query returns multiple animals, raise ValueError."""
        mock_agriwebb.post("/v2").mock(
            side_effect=[
                empty_animals_response(),  # by animalId
                empty_animals_response(),  # by name
                empty_animals_response(),  # by vid
                animals_response(
                    make_animal("a1", eid="840-DUP"),
                    make_animal("a2", eid="840-DUP"),
                ),  # by eid — multiple!
            ]
        )

        with pytest.raises(ValueError, match="Multiple animals match"):
            await livestock.find_animal("840-DUP")


class TestFindAnimalSpecialCharacters:
    """Test find_animal safely handles special characters via parameterized variables.

    After the parameterized-variables fix, special characters are passed as
    GraphQL variables rather than interpolated into the query string. This
    prevents injection attacks and query breakage.
    """

    async def test_identifier_with_double_quotes(self, mock_agriwebb):
        """An identifier with double quotes is passed via variables, not in the query string."""
        route = mock_agriwebb.post("/v2").mock(
            return_value=empty_animals_response(),
        )

        with pytest.raises(ValueError, match="No animal found"):
            await livestock.find_animal('say "hello"')

        # The identifier should be in variables, not interpolated into the query
        payload = json.loads(route.calls[0].request.content)
        query = payload["query"]
        variables = payload["variables"]
        assert 'say "hello"' not in query
        assert variables["identifier"] == 'say "hello"'

    async def test_identifier_with_backslash(self, mock_agriwebb):
        """An identifier with backslash is passed via variables, not in the query string."""
        route = mock_agriwebb.post("/v2").mock(
            return_value=empty_animals_response(),
        )

        with pytest.raises(ValueError, match="No animal found"):
            await livestock.find_animal("path\\to\\thing")

        payload = json.loads(route.calls[0].request.content)
        query = payload["query"]
        variables = payload["variables"]
        assert "path\\to\\thing" not in query
        assert variables["identifier"] == "path\\to\\thing"

    async def test_identifier_with_newline(self, mock_agriwebb):
        """An identifier with a newline is passed via variables, not in the query string."""
        route = mock_agriwebb.post("/v2").mock(
            return_value=empty_animals_response(),
        )

        with pytest.raises(ValueError, match="No animal found"):
            await livestock.find_animal("line1\nline2")

        payload = json.loads(route.calls[0].request.content)
        query = payload["query"]
        variables = payload["variables"]
        assert "line1\nline2" not in query
        assert variables["identifier"] == "line1\nline2"


# =============================================================================
# update_map_feature() — Mutation Building Tests
# =============================================================================


class TestUpdateMapFeatureMutation:
    """Test that update_map_feature builds correct mutation."""

    async def test_builds_mutation_with_correct_fields(self, mock_agriwebb):
        """Verify mutation includes feature_id, name, and geometry."""
        from agriwebb.core import client, config

        # First call: get_map_feature (fetches current geometry)
        # Second call: the actual mutation
        mock_agriwebb.post("/v2").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "data": {
                            "mapFeatures": [
                                {
                                    "id": "feat-001",
                                    "name": "Old Name",
                                    "geometry": {
                                        "type": "Point",
                                        "coordinates": [-123.04, 48.50],
                                    },
                                }
                            ]
                        }
                    },
                ),
                httpx.Response(
                    200,
                    json={
                        "data": {
                            "updateMapFeature": {
                                "mapFeature": {
                                    "id": "feat-001",
                                    "name": "New Paddock Name",
                                }
                            }
                        }
                    },
                ),
            ]
        )

        result = await client.update_map_feature("feat-001", "New Paddock Name")

        assert result["data"]["updateMapFeature"]["mapFeature"]["name"] == "New Paddock Name"

        # Verify the mutation uses parameterized variables
        payload = json.loads(mock_agriwebb.calls[1].request.content)
        assert "updateMapFeature" in payload["query"]
        variables = payload["variables"]
        assert variables["featureId"] == "feat-001"
        assert variables["name"] == "New Paddock Name"
        assert variables["farmId"] == config.settings.agriwebb_farm_id

    async def test_mutation_includes_geometry_from_fetch(self, mock_agriwebb):
        """Verify geometry from get_map_feature is included in mutation."""
        from agriwebb.core import client

        mock_agriwebb.post("/v2").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "data": {
                            "mapFeatures": [
                                {
                                    "id": "feat-002",
                                    "name": "Gate Field",
                                    "geometry": {
                                        "type": "Polygon",
                                        "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]],
                                    },
                                }
                            ]
                        }
                    },
                ),
                httpx.Response(
                    200,
                    json={
                        "data": {
                            "updateMapFeature": {
                                "mapFeature": {"id": "feat-002", "name": "Renamed"}
                            }
                        }
                    },
                ),
            ]
        )

        await client.update_map_feature("feat-002", "Renamed")

        payload = json.loads(mock_agriwebb.calls[1].request.content)
        variables = payload["variables"]
        assert variables["geometryType"] == "Polygon"
        assert variables["coordinates"] == [[[0, 0], [1, 0], [1, 1], [0, 0]]]

    async def test_mutation_with_special_chars_in_name(self, mock_agriwebb):
        """A name with quotes is safely passed via variables, not interpolated."""
        from agriwebb.core import client

        mock_agriwebb.post("/v2").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "data": {
                            "mapFeatures": [
                                {
                                    "id": "feat-003",
                                    "name": "Old",
                                    "geometry": {"type": "Point", "coordinates": [0, 0]},
                                }
                            ]
                        }
                    },
                ),
                httpx.Response(
                    200,
                    json={
                        "data": {
                            "updateMapFeature": {
                                "mapFeature": {"id": "feat-003", "name": 'Bob\'s "Field"'}
                            }
                        }
                    },
                ),
            ]
        )

        await client.update_map_feature("feat-003", 'Bob\'s "Field"')

        # The name should be in variables, not interpolated into the mutation string
        payload = json.loads(mock_agriwebb.calls[1].request.content)
        assert payload["variables"]["name"] == 'Bob\'s "Field"'
        assert 'Bob\'s "Field"' not in payload["query"]


# =============================================================================
# get_rainfalls() — Date-Filtered Query Building Tests
# =============================================================================


class TestGetRainfallsDateFilter:
    """Test that get_rainfalls builds correct date-filtered queries."""

    async def test_no_dates_uses_parameterized_query(self, mock_agriwebb):
        """Without date filters, the static RAINFALLS_QUERY with variables is used."""
        route = mock_agriwebb.post("/v2").mock(
            return_value=httpx.Response(
                200, json={"data": {"rainfalls": [{"id": "r1", "time": 1700000000000, "value": 5.0}]}}
            )
        )

        result = await weather_api.get_rainfalls()

        assert len(result) == 1
        assert result[0]["value"] == 5.0

        # Verify variables were sent (parameterized path)
        payload = json.loads(route.calls[0].request.content)
        assert "variables" in payload
        assert "farmId" in payload["variables"]
        assert "sensorId" in payload["variables"]

    async def test_start_date_builds_gte_filter(self, mock_agriwebb):
        """With start_date, query includes _gte time filter via variables."""
        route = mock_agriwebb.post("/v2").mock(
            return_value=httpx.Response(200, json={"data": {"rainfalls": []}})
        )

        await weather_api.get_rainfalls(start_date="2026-03-01")

        payload = json.loads(route.calls[0].request.content)
        assert "_gte" in payload["query"]
        # 2026-03-01 noon UTC — now in variables
        assert payload["variables"]["startTime"] == 1772366400000

    async def test_end_date_builds_lte_filter(self, mock_agriwebb):
        """With end_date, query includes _lte time filter via variables."""
        route = mock_agriwebb.post("/v2").mock(
            return_value=httpx.Response(200, json={"data": {"rainfalls": []}})
        )

        await weather_api.get_rainfalls(end_date="2026-03-31")

        payload = json.loads(route.calls[0].request.content)
        assert "_lte" in payload["query"]
        # 2026-03-31 noon UTC — now in variables
        assert payload["variables"]["endTime"] == 1774958400000

    async def test_both_dates_builds_combined_filter(self, mock_agriwebb):
        """With both dates, query includes both _gte and _lte via variables."""
        route = mock_agriwebb.post("/v2").mock(
            return_value=httpx.Response(200, json={"data": {"rainfalls": []}})
        )

        await weather_api.get_rainfalls(start_date="2026-03-01", end_date="2026-03-31")

        payload = json.loads(route.calls[0].request.content)
        assert "_gte" in payload["query"]
        assert "_lte" in payload["query"]
        assert payload["variables"]["startTime"] == 1772366400000
        assert payload["variables"]["endTime"] == 1774958400000

    async def test_date_filtered_query_includes_farm_and_sensor(self, mock_agriwebb):
        """Date-filtered path includes farmId and sensorId as variables."""
        from agriwebb.core.config import settings

        route = mock_agriwebb.post("/v2").mock(
            return_value=httpx.Response(200, json={"data": {"rainfalls": []}})
        )

        await weather_api.get_rainfalls(start_date="2026-01-01")

        payload = json.loads(route.calls[0].request.content)
        assert payload["variables"]["farmId"] == settings.agriwebb_farm_id
        sensor = settings.agriwebb_weather_sensor_id
        if sensor:
            assert payload["variables"]["sensorId"] == sensor

    async def test_date_filtered_returns_results(self, mock_agriwebb):
        """Verify the date-filtered path returns parsed results correctly."""
        mock_agriwebb.post("/v2").mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": {
                        "rainfalls": [
                            {"id": "r1", "time": 1772452800000, "value": 3.2, "unit": "mm", "mode": "cumulative",
                             "sensorId": "s1"},
                            {"id": "r2", "time": 1772539200000, "value": 7.6, "unit": "mm", "mode": "cumulative",
                             "sensorId": "s1"},
                        ]
                    }
                },
            )
        )

        result = await weather_api.get_rainfalls(start_date="2026-03-01", end_date="2026-03-31")

        assert len(result) == 2
        assert result[0]["value"] == 3.2
        assert result[1]["value"] == 7.6

    async def test_date_filter_with_special_chars_in_sensor_id(self, mock_agriwebb, monkeypatch):
        """A sensor ID with special characters is passed via variables, not in the query string."""
        from agriwebb.core import config

        # Temporarily override sensor ID to include a quote
        monkeypatch.setattr(config.settings, "agriwebb_weather_sensor_id", 'sensor"inject')

        route = mock_agriwebb.post("/v2").mock(
            return_value=httpx.Response(200, json={"data": {"rainfalls": []}})
        )

        await weather_api.get_rainfalls(start_date="2026-01-01")

        # Parse JSON to get the query and variables
        payload = json.loads(route.calls[0].request.content)
        query = payload["query"]
        variables = payload["variables"]
        # The sensor ID with quotes should be in variables, not interpolated into the query
        assert 'sensor"inject' not in query
        assert variables["sensorId"] == 'sensor"inject'


# =============================================================================
# get_pasture_growth_rates() — Date-Filtered Query Building Tests
# =============================================================================


class TestGetPastureGrowthRatesDateFilter:
    """Test that get_pasture_growth_rates builds correct date-filtered queries."""

    async def test_no_dates_uses_parameterized_query(self, mock_agriwebb):
        """Without date filters, the static query with variables is used."""
        route = mock_agriwebb.post("/v2").mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": {
                        "pastureGrowthRates": [
                            {"id": "pg1", "time": 1700000000000, "value": 15.5, "fieldId": "f1"}
                        ]
                    }
                },
            )
        )

        result = await pasture_api.get_pasture_growth_rates()

        assert len(result) == 1
        assert result[0]["value"] == 15.5

        payload = json.loads(route.calls[0].request.content)
        assert "variables" in payload
        assert "farmId" in payload["variables"]

    async def test_start_date_builds_gte_filter(self, mock_agriwebb):
        """With start_date, query includes _gte time filter via variables."""
        route = mock_agriwebb.post("/v2").mock(
            return_value=httpx.Response(200, json={"data": {"pastureGrowthRates": []}})
        )

        await pasture_api.get_pasture_growth_rates(start_date="2026-03-01")

        payload = json.loads(route.calls[0].request.content)
        assert "_gte" in payload["query"]
        # 2026-03-01 noon UTC — now in variables
        assert payload["variables"]["startTime"] == 1772366400000

    async def test_end_date_builds_lte_filter(self, mock_agriwebb):
        """With end_date, query includes _lte time filter via variables."""
        route = mock_agriwebb.post("/v2").mock(
            return_value=httpx.Response(200, json={"data": {"pastureGrowthRates": []}})
        )

        await pasture_api.get_pasture_growth_rates(end_date="2026-03-31")

        payload = json.loads(route.calls[0].request.content)
        assert "_lte" in payload["query"]
        # 2026-03-31 noon UTC — now in variables
        assert payload["variables"]["endTime"] == 1774958400000

    async def test_both_dates_builds_combined_filter(self, mock_agriwebb):
        """With both dates, query includes both _gte and _lte via variables."""
        route = mock_agriwebb.post("/v2").mock(
            return_value=httpx.Response(200, json={"data": {"pastureGrowthRates": []}})
        )

        await pasture_api.get_pasture_growth_rates(start_date="2026-03-01", end_date="2026-03-31")

        payload = json.loads(route.calls[0].request.content)
        assert "_gte" in payload["query"]
        assert "_lte" in payload["query"]

    async def test_date_filtered_query_includes_farm_id(self, mock_agriwebb):
        """Date-filtered path includes farmId as a variable."""
        from agriwebb.core.config import settings

        route = mock_agriwebb.post("/v2").mock(
            return_value=httpx.Response(200, json={"data": {"pastureGrowthRates": []}})
        )

        await pasture_api.get_pasture_growth_rates(start_date="2026-01-01")

        payload = json.loads(route.calls[0].request.content)
        assert payload["variables"]["farmId"] == settings.agriwebb_farm_id

    async def test_date_filtered_returns_results(self, mock_agriwebb):
        """Verify the date-filtered path returns parsed results correctly."""
        mock_agriwebb.post("/v2").mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": {
                        "pastureGrowthRates": [
                            {"id": "pg1", "time": 1772452800000, "value": 12.3, "fieldId": "f1"},
                            {"id": "pg2", "time": 1772539200000, "value": 18.7, "fieldId": "f2"},
                        ]
                    }
                },
            )
        )

        result = await pasture_api.get_pasture_growth_rates(
            start_date="2026-03-01", end_date="2026-03-31"
        )

        assert len(result) == 2
        assert result[0]["fieldId"] == "f1"
        assert result[1]["value"] == 18.7

    async def test_date_filter_query_structure(self, mock_agriwebb):
        """Verify the parameterized query has expected GraphQL structure."""
        route = mock_agriwebb.post("/v2").mock(
            return_value=httpx.Response(200, json={"data": {"pastureGrowthRates": []}})
        )

        await pasture_api.get_pasture_growth_rates(start_date="2026-04-01", end_date="2026-04-05")

        payload = json.loads(route.calls[0].request.content)
        query = payload["query"]
        variables = payload.get("variables")

        # The parameterized path uses variables for farmId and timestamps
        assert variables is not None
        assert "farmId" in variables
        assert "startTime" in variables
        assert "endTime" in variables

        # Query should have the expected fields
        assert "pastureGrowthRates" in query
        assert "farmId" in query
        assert "time" in query
        assert "value" in query
        assert "fieldId" in query
