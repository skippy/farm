"""Tests for data fetching modules (fields, soils, lactation)."""

import httpx
import pytest
import respx

from agriwebb.core import client

# --- Fixtures ---


@pytest.fixture
def mock_usda_soilweb():
    """Mock USDA SoilWeb API responses."""
    with respx.mock(base_url="https://casoilresource.lawr.ucdavis.edu") as mock:
        yield mock


@pytest.fixture
def mock_usda_sda():
    """Mock USDA Soil Data Access API responses."""
    with respx.mock(base_url="https://SDMDataAccess.sc.egov.usda.gov") as mock:
        yield mock


@pytest.fixture
def sample_fields_response():
    """Sample AgriWebb fields query response."""
    return {
        "data": {
            "fields": [
                {
                    "id": "field-1",
                    "name": "North Pasture",
                    "totalArea": 5.5,
                    "grazableArea": 5.0,
                    "landUse": "GRAZING",
                    "cropType": None,
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[
                            [-123.05, 48.50],
                            [-123.04, 48.50],
                            [-123.04, 48.51],
                            [-123.05, 48.51],
                            [-123.05, 48.50],
                        ]]
                    }
                },
                {
                    "id": "field-2",
                    "name": "South Field",
                    "totalArea": 3.2,
                    "grazableArea": 3.0,
                    "landUse": "GRAZING",
                    "cropType": None,
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[
                            [-123.06, 48.49],
                            [-123.05, 48.49],
                            [-123.05, 48.50],
                            [-123.06, 48.50],
                            [-123.06, 48.49],
                        ]]
                    }
                },
                {
                    "id": "field-3",
                    "name": "Tiny Plot",
                    "totalArea": 0.1,  # Below min threshold
                    "grazableArea": 0.1,
                    "landUse": "OTHER",
                    "cropType": None,
                    "geometry": None
                }
            ]
        }
    }


@pytest.fixture
def sample_animals_with_lineage():
    """Sample animals response with birth/dam relationships for lactation calc."""
    return {
        "data": {
            "animals": [
                # Ewe with offspring
                {
                    "animalId": "ewe-1",
                    "identity": {"vid": "E001", "name": "Daisy", "eid": None, "managementTag": None},
                    "characteristics": {
                        "sex": "Female",
                        "speciesCommonName": "Sheep",
                        "breedAssessed": "Finn",
                        "birthDate": 1546300800000,  # 2019-01-01
                        "birthYear": 2019,
                        "visualColor": None,
                        "ageClass": "ewe",
                    },
                    "state": {"onFarm": True, "currentLocationId": None, "fate": None,
                             "reproductiveStatus": None, "offspringCount": 3},
                    "parentage": {"sires": [], "dams": []},
                    "managementGroup": None,
                },
                # Lamb 1 - born March 2024
                {
                    "animalId": "lamb-1",
                    "identity": {"vid": "L001", "name": None, "eid": None, "managementTag": None},
                    "characteristics": {
                        "sex": "Female",
                        "speciesCommonName": "Sheep",
                        "breedAssessed": "Finn Cross",
                        "birthDate": 1709251200000,  # 2024-03-01
                        "birthYear": 2024,
                        "visualColor": None,
                        "ageClass": "lamb",
                    },
                    "state": {"onFarm": True, "currentLocationId": None, "fate": None,
                             "reproductiveStatus": None, "offspringCount": 0},
                    "parentage": {
                        "sires": [],
                        "dams": [{
                            "parentAnimalId": "ewe-1",
                            "parentAnimalIdentity": {"vid": "E001", "name": "Daisy", "eid": None},
                            "parentType": "GENETIC",
                        }]
                    },
                    "managementGroup": None,
                },
                # Lamb 2 - twin, born same day
                {
                    "animalId": "lamb-2",
                    "identity": {"vid": "L002", "name": None, "eid": None, "managementTag": None},
                    "characteristics": {
                        "sex": "Male",
                        "speciesCommonName": "Sheep",
                        "breedAssessed": "Finn Cross",
                        "birthDate": 1709251200000,  # 2024-03-01 (twin)
                        "birthYear": 2024,
                        "visualColor": None,
                        "ageClass": "lamb",
                    },
                    "state": {"onFarm": False, "currentLocationId": None, "fate": "SOLD",
                             "reproductiveStatus": None, "offspringCount": 0},
                    "parentage": {
                        "sires": [],
                        "dams": [{
                            "parentAnimalId": "ewe-1",
                            "parentAnimalIdentity": {"vid": "E001", "name": "Daisy", "eid": None},
                            "parentType": "GENETIC",
                        }]
                    },
                    "managementGroup": None,
                },
                # Ram (male, not a ewe)
                {
                    "animalId": "ram-1",
                    "identity": {"vid": "R001", "name": "Thunder", "eid": None, "managementTag": None},
                    "characteristics": {
                        "sex": "Male",
                        "speciesCommonName": "Sheep",
                        "breedAssessed": "Finn",
                        "birthDate": 1514764800000,  # 2018-01-01
                        "birthYear": 2018,
                        "visualColor": None,
                        "ageClass": "ram",
                    },
                    "state": {"onFarm": True, "currentLocationId": None, "fate": None,
                             "reproductiveStatus": None, "offspringCount": 0},
                    "parentage": {"sires": [], "dams": []},
                    "managementGroup": None,
                },
            ]
        }
    }


@pytest.fixture
def sample_soilweb_html():
    """Sample HTML response from USDA SoilWeb with mukey."""
    return """
    <html>
    <body>
    <table>
        <tr><td>Map Unit Key:</td><td><a href="?mukey=123456">123456</a></td></tr>
        <tr><td>Map Unit Name:</td><td>Mitchellbay gravelly sandy loam</td></tr>
    </table>
    </body>
    </html>
    """


@pytest.fixture
def sample_soil_query_response():
    """Sample USDA Soil Data Access query response."""
    return {
        "Table": [
            [
                "123456",  # mukey
                "Mitchellbay gravelly sandy loam, 0 to 5 percent slopes",  # muname
                "Consociation",  # mukind
                "Mitchellbay",  # compname
                85,  # comppct
                "Inceptisols",  # taxorder
                "Somewhat poorly drained",  # drainage
                "C/D",  # hydgrp
                65,  # sand_pct
                23,  # silt_pct
                12,  # clay_pct
                7.5,  # organic_matter_pct
                1.5,  # ksat
                0.15,  # awc
            ]
        ]
    }


# --- Tests for Fields/Paddocks ---


class TestGetFields:
    """Tests for fetching field/paddock data."""

    async def test_returns_fields_list(self, mock_agriwebb, sample_fields_response):
        """Verify fields are returned (default min_area filters small plots)."""
        mock_agriwebb.post("/v2").mock(
            return_value=httpx.Response(200, json=sample_fields_response)
        )

        result = await client.get_fields()  # Default min_area_ha=0.2

        # Tiny Plot (0.1 ha) filtered out by default
        assert len(result) == 2
        assert result[0]["name"] == "North Pasture"

    async def test_filters_by_min_area(self, mock_agriwebb, sample_fields_response):
        """Verify min_area_ha filter works."""
        mock_agriwebb.post("/v2").mock(
            return_value=httpx.Response(200, json=sample_fields_response)
        )

        result = await client.get_fields(min_area_ha=0.2)

        # Should exclude "Tiny Plot" with 0.1 ha
        assert len(result) == 2
        assert all(f["totalArea"] >= 0.2 for f in result)

    async def test_includes_geometry(self, mock_agriwebb, sample_fields_response):
        """Verify geometry data is included."""
        mock_agriwebb.post("/v2").mock(
            return_value=httpx.Response(200, json=sample_fields_response)
        )

        result = await client.get_fields()

        north_pasture = next(f for f in result if f["name"] == "North Pasture")
        assert north_pasture["geometry"]["type"] == "Polygon"
        assert len(north_pasture["geometry"]["coordinates"][0]) == 5

    async def test_handles_empty_response(self, mock_agriwebb):
        """Verify empty list returned when no fields."""
        mock_agriwebb.post("/v2").mock(
            return_value=httpx.Response(200, json={"data": {"fields": []}})
        )

        result = await client.get_fields()

        assert result == []

    async def test_raises_on_error(self, mock_agriwebb):
        """Verify error raised on GraphQL errors."""
        mock_agriwebb.post("/v2").mock(
            return_value=httpx.Response(200, json={"errors": [{"message": "Unauthorized"}]})
        )

        with pytest.raises(ValueError, match="GraphQL errors"):
            await client.get_fields()


# --- Tests for Soil Data Fetching ---


def calculate_centroid(geometry: dict) -> tuple[float, float] | None:
    """Calculate centroid of a polygon geometry (test helper)."""
    geom_type = geometry.get("type")
    coords = geometry.get("coordinates", [])

    if not coords:
        return None

    points = []

    if geom_type == "Polygon":
        if coords and coords[0]:
            points = coords[0]
    elif geom_type == "MultiPolygon":
        for polygon in coords:
            if polygon and polygon[0]:
                points.extend(polygon[0])

    if not points:
        return None

    lon_sum = sum(p[0] for p in points)
    lat_sum = sum(p[1] for p in points)
    n = len(points)

    return (lat_sum / n, lon_sum / n)


class TestSoilDataFetching:
    """Tests for USDA soil data fetching logic."""

    def test_calculate_centroid_polygon(self):
        """Verify centroid calculation for polygon."""
        geometry = {
            "type": "Polygon",
            "coordinates": [[
                [-123.0, 48.0],
                [-123.0, 49.0],
                [-122.0, 49.0],
                [-122.0, 48.0],
                [-123.0, 48.0],
            ]]
        }

        lat, lon = calculate_centroid(geometry)

        # Centroid should be approximately center (simple average of points)
        # Note: closed polygon repeats first point, so avg is slightly off center
        assert abs(lat - 48.4) < 0.1  # (48+49+49+48+48)/5 = 48.4
        assert abs(lon - (-122.6)) < 0.1  # (-123-123-122-122-123)/5 = -122.6

    def test_calculate_centroid_multipolygon(self):
        """Verify centroid calculation for multipolygon."""
        geometry = {
            "type": "MultiPolygon",
            "coordinates": [
                [[[-123.0, 48.0], [-123.0, 49.0], [-122.0, 48.0], [-123.0, 48.0]]],
                [[[-121.0, 48.0], [-121.0, 49.0], [-120.0, 48.0], [-121.0, 48.0]]],
            ]
        }

        result = calculate_centroid(geometry)

        assert result is not None
        lat, lon = result
        assert 48.0 <= lat <= 49.0

    def test_calculate_centroid_empty_geometry(self):
        """Verify None returned for empty geometry."""
        assert calculate_centroid({}) is None
        assert calculate_centroid({"type": "Polygon", "coordinates": []}) is None

    async def test_query_soil_by_mukey(self, mock_usda_sda, sample_soil_query_response):
        """Verify soil properties query works."""
        # Test the query format and response parsing
        mock_usda_sda.post("/TABULAR/post.rest").mock(
            return_value=httpx.Response(200, json=sample_soil_query_response)
        )

        # Inline version of the query logic for testing
        async with httpx.AsyncClient() as http_client:
            response = await http_client.post(
                "https://SDMDataAccess.sc.egov.usda.gov/TABULAR/post.rest",
                data={"query": "SELECT * FROM mapunit WHERE mukey = '123456'", "format": "JSON"},
                timeout=30,
            )
            result = response.json()

        # Verify response structure
        assert "Table" in result
        row = result["Table"][0]
        assert row[0] == "123456"  # mukey
        assert row[3] == "Mitchellbay"  # compname
        assert row[6] == "Somewhat poorly drained"  # drainage

    async def test_soilweb_html_parsing(self, mock_usda_soilweb, sample_soilweb_html):
        """Verify mukey extraction from SoilWeb HTML response."""
        import re

        mock_usda_soilweb.get("/soil_web/reflector_api/soils.php").mock(
            return_value=httpx.Response(200, text=sample_soilweb_html)
        )

        async with httpx.AsyncClient() as http_client:
            response = await http_client.get(
                "https://casoilresource.lawr.ucdavis.edu/soil_web/reflector_api/soils.php",
                params={"what": "mapunit", "lat": 48.5, "lon": -123.0},
            )
            html = response.text

        # Extract mukey from HTML
        mukey_match = re.search(r'mukey=(\d{6,7})', html)
        assert mukey_match is not None
        assert mukey_match.group(1) == "123456"


# --- Tests for Lactation Data ---


class TestLactationDataFetching:
    """Tests for lactation data calculation from birth records."""

    def test_derive_births_from_animals(self, sample_animals_with_lineage):
        """Verify births are extracted from animal parentage."""
        animals = sample_animals_with_lineage["data"]["animals"]

        births = []
        for animal in animals:
            birth_date = animal["characteristics"].get("birthDate")
            parentage = animal.get("parentage") or {}
            dams = parentage.get("dams") or []

            if birth_date and dams:
                dam = dams[0]
                births.append({
                    "offspring_id": animal["animalId"],
                    "dam_id": dam["parentAnimalId"],
                    "birth_date": birth_date,
                })

        # Should find 2 lambs with dam references
        assert len(births) == 2
        assert all(b["dam_id"] == "ewe-1" for b in births)

    def test_calculate_lactation_periods(self):
        """Verify lactation period calculation."""
        from datetime import datetime, timedelta

        births = [
            {"dam_id": "ewe-1", "birth_date": "2024-03-01T00:00:00"},
            {"dam_id": "ewe-1", "birth_date": "2024-03-01T00:00:00"},  # Twin
            {"dam_id": "ewe-2", "birth_date": "2024-03-15T00:00:00"},
        ]

        LACTATION_DURATION_DAYS = 120

        # Group by dam
        from collections import defaultdict
        lactation_by_dam = defaultdict(list)

        for birth in births:
            dam_id = birth["dam_id"]
            birth_date_str = birth["birth_date"]
            birth_date = datetime.fromisoformat(birth_date_str)
            lactation_end = birth_date + timedelta(days=LACTATION_DURATION_DAYS)

            lactation_by_dam[dam_id].append({
                "start": birth_date,
                "end": lactation_end,
            })

        # Should have 2 dams
        assert len(lactation_by_dam) == 2
        # Ewe-1 has 2 entries (twins counted separately before grouping)
        assert len(lactation_by_dam["ewe-1"]) == 2

    def test_count_lactating_ewes_for_month(self):
        """Verify monthly lactating ewe count."""
        from datetime import datetime

        lactation_periods = {
            "ewe-1": [{"start": datetime(2024, 3, 1), "end": datetime(2024, 7, 1)}],
            "ewe-2": [{"start": datetime(2024, 3, 15), "end": datetime(2024, 7, 15)}],
            "ewe-3": [{"start": datetime(2024, 8, 1), "end": datetime(2024, 12, 1)}],
        }

        # Count lactating on April 15, 2024
        check_date = datetime(2024, 4, 15)
        count = 0
        for dam_id, periods in lactation_periods.items():
            for period in periods:
                if period["start"] <= check_date <= period["end"]:
                    count += 1
                    break

        # Ewe-1 and ewe-2 should be lactating, ewe-3 not yet
        assert count == 2

    def test_filter_ewes_by_sex(self, sample_animals_with_lineage):
        """Verify only female sheep are counted as ewes."""
        animals = sample_animals_with_lineage["data"]["animals"]

        ewes = [
            a for a in animals
            if a["characteristics"].get("sex") in ["FEMALE", "Female", "female", "F"]
            and a["characteristics"].get("speciesCommonName") in ["SHEEP", "Sheep", "sheep", None]
        ]

        # Should find 2 females (1 ewe, 1 female lamb)
        assert len(ewes) == 2
        assert all(e["characteristics"]["sex"] == "Female" for e in ewes)


# --- Tests for Birth Date Parsing ---


class TestBirthDateParsing:
    """Tests for handling various birth date formats."""

    def test_parse_timestamp_milliseconds(self):
        """Verify millisecond timestamp parsing."""
        from datetime import UTC, datetime

        birth_date = 1709251200000  # 2024-03-01 00:00:00 UTC

        if isinstance(birth_date, (int, float)):
            # Use UTC to avoid timezone issues
            birth_dt = datetime.fromtimestamp(birth_date / 1000, tz=UTC)

        assert birth_dt.year == 2024
        assert birth_dt.month == 3
        assert birth_dt.day == 1

    def test_parse_iso_string(self):
        """Verify ISO date string parsing."""
        from datetime import datetime

        birth_date = "2024-03-01T00:00:00Z"

        if "T" in birth_date:
            birth_dt = datetime.fromisoformat(birth_date.replace("Z", "+00:00"))

        assert birth_dt.year == 2024
        assert birth_dt.month == 3

    def test_parse_date_only_string(self):
        """Verify date-only string parsing."""
        from datetime import datetime

        birth_date = "2024-03-01"

        birth_dt = datetime.fromisoformat(birth_date)

        assert birth_dt.year == 2024
        assert birth_dt.month == 3


# --- Integration-style Tests ---


class TestFieldsToSoilsIntegration:
    """Tests for the fields -> soils data flow."""

    async def test_fields_have_geometry_for_centroid(self, mock_agriwebb, sample_fields_response):
        """Verify fields have geometry needed for soil lookup."""
        mock_agriwebb.post("/v2").mock(
            return_value=httpx.Response(200, json=sample_fields_response)
        )

        fields = await client.get_fields(min_area_ha=0.2)

        # Both filtered fields should have geometry
        fields_with_geometry = [f for f in fields if f.get("geometry")]
        assert len(fields_with_geometry) == 2

        # Each geometry should be parseable for centroid
        for field in fields_with_geometry:
            centroid = calculate_centroid(field["geometry"])
            assert centroid is not None
            lat, lon = centroid
            assert -90 <= lat <= 90
            assert -180 <= lon <= 180
