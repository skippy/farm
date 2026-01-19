"""Tests for the AgriWebb client module."""

import httpx
import pytest

from agriwebb.core import client
from agriwebb.weather import api as weather_api


class TestGraphQL:
    """Tests for the graphql function."""

    async def test_graphql_sends_correct_headers(self, mock_agriwebb):
        """Verify correct headers are sent with GraphQL requests."""
        mock_agriwebb.post("/v2").mock(return_value=httpx.Response(200, json={"data": {}}))

        await client.graphql("{ farms { id } }")

        request = mock_agriwebb.calls[0].request
        assert request.headers["x-api-key"] is not None
        assert request.headers["content-type"] == "application/json"

    async def test_graphql_sends_query_in_body(self, mock_agriwebb):
        """Verify query is sent in request body."""
        mock_agriwebb.post("/v2").mock(return_value=httpx.Response(200, json={"data": {}}))

        await client.graphql("{ farms { id } }")

        request = mock_agriwebb.calls[0].request
        body = request.content.decode()
        assert "farms" in body
        assert "query" in body

    async def test_graphql_raises_on_http_error(self, mock_agriwebb):
        """Verify HTTP errors are raised."""
        mock_agriwebb.post("/v2").mock(return_value=httpx.Response(500))

        with pytest.raises(httpx.HTTPStatusError):
            await client.graphql("{ farms { id } }")


class TestGetFarm:
    """Tests for the get_farm function."""

    async def test_get_farm_returns_matching_farm(self, mock_agriwebb, sample_farm_response):
        """Verify correct farm is returned when ID matches."""
        mock_agriwebb.post("/v2").mock(return_value=httpx.Response(200, json=sample_farm_response))

        # Temporarily override settings for test
        original_farm_id = client.settings.agriwebb_farm_id
        client.settings.agriwebb_farm_id = "test-farm-id"

        try:
            farm = await client.get_farm()
            assert farm["name"] == "Test Farm"
            assert farm["id"] == "test-farm-id"
        finally:
            client.settings.agriwebb_farm_id = original_farm_id

    async def test_get_farm_raises_when_not_found(self, mock_agriwebb):
        """Verify error raised when farm not found."""
        mock_agriwebb.post("/v2").mock(return_value=httpx.Response(200, json={"data": {"farms": []}}))

        with pytest.raises(ValueError, match="not found"):
            await client.get_farm()


class TestGetFarmLocation:
    """Tests for the get_farm_location function."""

    async def test_get_farm_location_returns_coordinates(self, mock_agriwebb, sample_farm_response):
        """Verify lat/long tuple is returned."""
        mock_agriwebb.post("/v2").mock(return_value=httpx.Response(200, json=sample_farm_response))

        original_farm_id = client.settings.agriwebb_farm_id
        client.settings.agriwebb_farm_id = "test-farm-id"

        try:
            lat, long = await client.get_farm_location()
            assert lat == 48.501762
            assert long == -123.042906
        finally:
            client.settings.agriwebb_farm_id = original_farm_id


class TestAddRainfall:
    """Tests for the add_rainfall function."""

    async def test_add_rainfall_converts_inches_to_mm(self, mock_agriwebb, sample_rainfall_response):
        """Verify inches are converted to mm correctly."""
        route = mock_agriwebb.post("/v2").mock(return_value=httpx.Response(200, json=sample_rainfall_response))

        await weather_api.add_rainfall("2026-01-15", 1.0)  # 1 inch = 25.4 mm

        request_body = route.calls[0].request.content.decode()
        assert "25.4" in request_body

    async def test_add_rainfall_uses_correct_timestamp_format(self, mock_agriwebb, sample_rainfall_response):
        """Verify timestamp is in milliseconds."""
        route = mock_agriwebb.post("/v2").mock(return_value=httpx.Response(200, json=sample_rainfall_response))

        await weather_api.add_rainfall("2026-01-15", 0.5)

        request_body = route.calls[0].request.content.decode()
        # Timestamp should be 13 digits (milliseconds) in variables
        assert '"time":17' in request_body  # Starts with 17... for 2026

    async def test_add_rainfall_uses_default_sensor_id(self, mock_agriwebb, sample_rainfall_response):
        """Verify default sensor ID from settings is used."""
        route = mock_agriwebb.post("/v2").mock(return_value=httpx.Response(200, json=sample_rainfall_response))

        await weather_api.add_rainfall("2026-01-15", 0.5)

        request_body = route.calls[0].request.content.decode()
        assert client.settings.agriwebb_weather_sensor_id in request_body

    async def test_add_rainfall_allows_custom_sensor_id(self, mock_agriwebb, sample_rainfall_response):
        """Verify custom sensor ID can be provided."""
        route = mock_agriwebb.post("/v2").mock(return_value=httpx.Response(200, json=sample_rainfall_response))

        await weather_api.add_rainfall("2026-01-15", 0.5, sensor_id="custom-sensor")

        request_body = route.calls[0].request.content.decode()
        assert "custom-sensor" in request_body

    async def test_add_rainfall_returns_response(self, mock_agriwebb, sample_rainfall_response):
        """Verify API response is returned."""
        mock_agriwebb.post("/v2").mock(return_value=httpx.Response(200, json=sample_rainfall_response))

        response = await weather_api.add_rainfall("2026-01-15", 0.5)

        assert "data" in response
        assert response["data"]["addRainfalls"]["rainfalls"][0]["mode"] == "cumulative"

    async def test_add_rainfall_raises_without_sensor_id(self, mock_agriwebb):
        """Verify error raised when no sensor ID configured."""
        original = client.settings.agriwebb_weather_sensor_id
        client.settings.agriwebb_weather_sensor_id = None

        try:
            with pytest.raises(ValueError, match="No sensor ID configured"):
                await weather_api.add_rainfall("2026-01-15", 0.5)
        finally:
            client.settings.agriwebb_weather_sensor_id = original


class TestCreateRainGauge:
    """Tests for the create_rain_gauge function."""

    async def test_create_rain_gauge_returns_id(self, mock_agriwebb):
        """Verify feature ID is returned on success."""
        mock_agriwebb.post("/v2").mock(
            return_value=httpx.Response(
                200, json={"data": {"addMapFeatures": {"features": [{"id": "new-gauge-id", "name": "Test Gauge"}]}}}
            )
        )

        result = await weather_api.create_rain_gauge("Test Gauge", 48.5, -123.0)

        assert result == "new-gauge-id"

    async def test_create_rain_gauge_sends_correct_data(self, mock_agriwebb):
        """Verify mutation contains correct fields."""
        route = mock_agriwebb.post("/v2").mock(
            return_value=httpx.Response(
                200, json={"data": {"addMapFeatures": {"features": [{"id": "id", "name": "name"}]}}}
            )
        )

        await weather_api.create_rain_gauge("My Gauge", 48.5, -123.0)

        body = route.calls[0].request.content.decode()
        assert "rainGauge" in body
        assert "My Gauge" in body
        assert "48.5" in body
        assert "-123.0" in body

    async def test_create_rain_gauge_raises_on_error(self, mock_agriwebb):
        """Verify error raised when API returns errors."""
        mock_agriwebb.post("/v2").mock(return_value=httpx.Response(200, json={"errors": [{"message": "Failed"}]}))

        with pytest.raises(ValueError, match="Failed to create"):
            await weather_api.create_rain_gauge("Test", 0, 0)


class TestGetMapFeature:
    """Tests for the get_map_feature function."""

    async def test_get_map_feature_returns_feature(self, mock_agriwebb):
        """Verify feature is returned."""
        mock_agriwebb.post("/v2").mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": {
                        "mapFeatures": [
                            {
                                "id": "feature-123",
                                "name": "Test Feature",
                                "geometry": {"type": "Point", "coordinates": [-123, 48]},
                            }
                        ]
                    }
                },
            )
        )

        result = await client.get_map_feature("feature-123")

        assert result["id"] == "feature-123"
        assert result["name"] == "Test Feature"

    async def test_get_map_feature_raises_when_not_found(self, mock_agriwebb):
        """Verify error raised when feature not found."""
        mock_agriwebb.post("/v2").mock(return_value=httpx.Response(200, json={"data": {"mapFeatures": []}}))

        with pytest.raises(ValueError, match="not found"):
            await client.get_map_feature("missing-id")


class TestUpdateMapFeature:
    """Tests for the update_map_feature function."""

    async def test_update_map_feature_preserves_geometry(self, mock_agriwebb):
        """Verify geometry is preserved when updating name."""
        # First call returns existing feature, second call is the update
        mock_agriwebb.post("/v2").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "data": {
                            "mapFeatures": [
                                {
                                    "id": "feature-123",
                                    "name": "Old Name",
                                    "geometry": {"type": "Point", "coordinates": [-123.5, 48.5]},
                                }
                            ]
                        }
                    },
                ),
                httpx.Response(
                    200, json={"data": {"updateMapFeature": {"mapFeature": {"id": "feature-123", "name": "New Name"}}}}
                ),
            ]
        )

        await client.update_map_feature("feature-123", "New Name")

        # Check the update mutation includes geometry
        update_body = mock_agriwebb.calls[1].request.content.decode()
        assert "New Name" in update_body
        assert "-123.5" in update_body
        assert "48.5" in update_body

    async def test_update_map_feature_raises_on_error(self, mock_agriwebb):
        """Verify error raised when update fails."""
        mock_agriwebb.post("/v2").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "data": {
                            "mapFeatures": [
                                {"id": "id", "name": "name", "geometry": {"type": "Point", "coordinates": [0, 0]}}
                            ]
                        }
                    },
                ),
                httpx.Response(200, json={"errors": [{"message": "Update failed"}]}),
            ]
        )

        with pytest.raises(ValueError, match="Failed to update"):
            await client.update_map_feature("id", "New Name")
