"""Tests for the weather module."""

import json
from datetime import date

import httpx
import pytest

from agriwebb.core.client import AgriWebbAPIError
from agriwebb.weather import api as weather_api
from agriwebb.weather import ncei as weather


class TestFetchNceiPrecipitation:
    """Tests for the fetch_ncei_precipitation function."""

    async def test_fetch_returns_parsed_data(self, mock_ncei, sample_ncei_response):
        """Verify NCEI response is parsed correctly."""
        mock_ncei.get("/access/services/data/v1").mock(return_value=httpx.Response(200, json=sample_ncei_response))

        result = await weather.fetch_ncei_precipitation(date(2026, 1, 15))

        assert result["date"] == "2026-01-15"
        assert result["station"] == "USW00094276"
        assert result["precipitation_inches"] == 0.25
        assert result["temp_max_f"] == 50.0
        assert result["temp_min_f"] == 42.0

    async def test_fetch_returns_none_when_no_data(self, mock_ncei):
        """Verify None is returned when NCEI has no data."""
        mock_ncei.get("/access/services/data/v1").mock(return_value=httpx.Response(200, json=[]))

        result = await weather.fetch_ncei_precipitation(date(2026, 1, 15))

        assert result is None

    async def test_fetch_handles_missing_optional_fields(self, mock_ncei):
        """Verify missing temp fields are handled."""
        mock_ncei.get("/access/services/data/v1").mock(
            return_value=httpx.Response(
                200,
                json=[{"DATE": "2026-01-15", "STATION": "USW00094276", "PRCP": "0.10"}],
            )
        )

        result = await weather.fetch_ncei_precipitation(date(2026, 1, 15))

        assert result["precipitation_inches"] == 0.10
        assert result["temp_max_f"] is None
        assert result["temp_min_f"] is None

    async def test_fetch_handles_null_precipitation(self, mock_ncei):
        """Verify null/missing precipitation defaults to 0."""
        mock_ncei.get("/access/services/data/v1").mock(
            return_value=httpx.Response(
                200,
                json=[{"DATE": "2026-01-15", "STATION": "USW00094276", "PRCP": None}],
            )
        )

        result = await weather.fetch_ncei_precipitation(date(2026, 1, 15))

        assert result["precipitation_inches"] == 0.0

    async def test_fetch_sends_correct_params(self, mock_ncei, sample_ncei_response):
        """Verify correct query parameters are sent."""
        route = mock_ncei.get("/access/services/data/v1").mock(
            return_value=httpx.Response(200, json=sample_ncei_response)
        )

        await weather.fetch_ncei_precipitation(date(2026, 1, 15))

        request = route.calls[0].request
        assert "dataset=daily-summaries" in str(request.url)
        assert "startDate=2026-01-15" in str(request.url)
        assert "endDate=2026-01-15" in str(request.url)
        assert "PRCP" in str(request.url)

    async def test_fetch_raises_on_http_error(self, mock_ncei):
        """Verify HTTP errors are raised."""
        mock_ncei.get("/access/services/data/v1").mock(return_value=httpx.Response(500))

        with pytest.raises(httpx.HTTPStatusError):
            await weather.fetch_ncei_precipitation(date(2026, 1, 15))


class TestLogWeather:
    """Tests for the log_weather function."""

    def test_log_weather_creates_file(self, tmp_path, monkeypatch):
        """Verify log file is created."""
        monkeypatch.setattr(weather, "get_cache_dir", lambda: tmp_path)

        weather_data = {"date": "2026-01-15", "precipitation_inches": 0.25}
        response = {"data": {"addRainfalls": {}}}

        log_path = weather.log_weather(weather_data, response)

        assert log_path.exists()
        assert log_path.name == "weather_log.jsonl"

    def test_log_weather_appends_entry(self, tmp_path, monkeypatch):
        """Verify entries are appended."""
        monkeypatch.setattr(weather, "get_cache_dir", lambda: tmp_path)

        for i in range(3):
            weather.log_weather(
                {"date": f"2026-01-{15 + i}", "precipitation_inches": 0.1 * i},
                {"data": {}},
            )

        log_path = tmp_path / "weather_log.jsonl"
        lines = log_path.read_text().strip().split("\n")
        assert len(lines) == 3

    def test_log_weather_includes_timestamp(self, tmp_path, monkeypatch):
        """Verify logged_at timestamp is included."""
        monkeypatch.setattr(weather, "get_cache_dir", lambda: tmp_path)

        weather.log_weather({"date": "2026-01-15"}, {"data": {}})

        log_path = tmp_path / "weather_log.jsonl"
        entry = json.loads(log_path.read_text().strip())
        assert "logged_at" in entry
        # Verify it's a valid ISO timestamp (contains T separator)
        assert "T" in entry["logged_at"]


class TestFetchNceiDateRange:
    """Tests for the fetch_ncei_date_range function."""

    async def test_returns_list_of_records(self, mock_ncei):
        """Verify date range returns multiple records."""
        mock_ncei.get("/access/services/data/v1").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {"DATE": "2026-01-13", "STATION": "USW00094276", "PRCP": "0.10", "TMAX": "48", "TMIN": "40"},
                    {"DATE": "2026-01-14", "STATION": "USW00094276", "PRCP": "0.25", "TMAX": "50", "TMIN": "42"},
                    {"DATE": "2026-01-15", "STATION": "USW00094276", "PRCP": "0.00", "TMAX": "52", "TMIN": "44"},
                ],
            )
        )

        result = await weather.fetch_ncei_date_range(date(2026, 1, 13), date(2026, 1, 15))

        assert len(result) == 3
        assert result[0]["date"] == "2026-01-13"
        assert result[1]["precipitation_inches"] == 0.25
        assert result[2]["temp_max_f"] == 52.0

    async def test_returns_empty_list_when_no_data(self, mock_ncei):
        """Verify empty list returned when NCEI has no data."""
        mock_ncei.get("/access/services/data/v1").mock(return_value=httpx.Response(200, json=[]))

        result = await weather.fetch_ncei_date_range(date(2026, 1, 13), date(2026, 1, 15))

        assert result == []

    async def test_sends_correct_date_range_params(self, mock_ncei):
        """Verify correct start/end dates are sent."""
        route = mock_ncei.get("/access/services/data/v1").mock(return_value=httpx.Response(200, json=[]))

        await weather.fetch_ncei_date_range(date(2026, 1, 1), date(2026, 1, 31))

        request = route.calls[0].request
        assert "startDate=2026-01-01" in str(request.url)
        assert "endDate=2026-01-31" in str(request.url)

    async def test_handles_missing_temperature_fields(self, mock_ncei):
        """Verify missing temp fields default to None."""
        mock_ncei.get("/access/services/data/v1").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {"DATE": "2026-01-15", "STATION": "USW00094276", "PRCP": "0.50"},
                ],
            )
        )

        result = await weather.fetch_ncei_date_range(date(2026, 1, 15), date(2026, 1, 15))

        assert len(result) == 1
        assert result[0]["precipitation_inches"] == 0.50
        assert result[0]["temp_max_f"] is None
        assert result[0]["temp_min_f"] is None


class TestSaveWeatherJson:
    """Tests for the save_weather_json function."""

    def test_creates_json_file(self, tmp_path, monkeypatch):
        """Verify JSON file is created."""
        monkeypatch.setattr(weather, "get_cache_dir", lambda: tmp_path)

        weather_data = [
            {"date": "2026-01-15", "precipitation_inches": 0.25},
            {"date": "2026-01-16", "precipitation_inches": 0.10},
        ]

        path = weather.save_weather_json(weather_data)

        assert path.exists()
        assert path.name == "weather_history.json"

    def test_includes_metadata(self, tmp_path, monkeypatch):
        """Verify metadata is included in output."""
        monkeypatch.setattr(weather, "get_cache_dir", lambda: tmp_path)

        weather_data = [{"date": "2026-01-15", "precipitation_inches": 0.25}]

        path = weather.save_weather_json(weather_data)

        with path.open() as f:
            output = json.load(f)

        assert "station_id" in output
        assert "generated_at" in output
        assert output["record_count"] == 1
        assert output["records"] == weather_data

    def test_accepts_custom_filename(self, tmp_path, monkeypatch):
        """Verify custom filename is used."""
        monkeypatch.setattr(weather, "get_cache_dir", lambda: tmp_path)

        path = weather.save_weather_json([], filename="custom.json")

        assert path.name == "custom.json"


class TestGetRainfalls:
    """Tests for the get_rainfalls function in client module."""

    async def test_returns_rainfall_list(self, mock_agriwebb):
        """Verify rainfalls are returned."""
        mock_agriwebb.post("/v2").mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": {
                        "rainfalls": [
                            {"id": "r1", "time": 1705320000000, "value": 6.35, "unit": "mm", "mode": "cumulative"},
                            {"id": "r2", "time": 1705406400000, "value": 12.7, "unit": "mm", "mode": "cumulative"},
                        ]
                    }
                },
            )
        )

        result = await weather_api.get_rainfalls()

        assert len(result) == 2
        assert result[0]["value"] == 6.35
        assert result[1]["value"] == 12.7

    async def test_returns_empty_list_when_no_records(self, mock_agriwebb):
        """Verify empty list returned when no rainfalls exist."""
        mock_agriwebb.post("/v2").mock(return_value=httpx.Response(200, json={"data": {"rainfalls": []}}))

        result = await weather_api.get_rainfalls()

        assert result == []

    async def test_raises_on_error(self, mock_agriwebb):
        """Verify error raised on GraphQL errors."""
        mock_agriwebb.post("/v2").mock(return_value=httpx.Response(200, json={"errors": [{"message": "Unauthorized"}]}))

        with pytest.raises(AgriWebbAPIError, match="Unauthorized"):
            await weather_api.get_rainfalls()

    async def test_sends_sensor_filter(self, mock_agriwebb):
        """Verify sensor ID is included in query."""
        route = mock_agriwebb.post("/v2").mock(return_value=httpx.Response(200, json={"data": {"rainfalls": []}}))

        await weather_api.get_rainfalls()

        body = route.calls[0].request.content.decode()
        assert "sensorId" in body


class TestRainfallIntegration:
    """Integration tests for the full rainfall flow."""

    async def test_fetch_and_push_rainfall(
        self, mock_ncei, mock_agriwebb, sample_ncei_response, sample_rainfall_response
    ):
        """Verify NCEI data can be fetched and pushed to AgriWebb."""
        # Mock NCEI response
        mock_ncei.get("/access/services/data/v1").mock(return_value=httpx.Response(200, json=sample_ncei_response))

        # Mock AgriWebb push
        mock_agriwebb.post("/v2").mock(return_value=httpx.Response(200, json=sample_rainfall_response))

        # Fetch from NCEI
        weather_data = await weather.fetch_ncei_precipitation(date(2026, 1, 15))
        assert weather_data is not None
        assert weather_data["precipitation_inches"] == 0.25

        # Push to AgriWebb
        response = await weather_api.add_rainfall(weather_data["date"], weather_data["precipitation_inches"])

        assert "data" in response
        assert "addRainfalls" in response["data"]

    async def test_handles_zero_precipitation(self, mock_ncei, mock_agriwebb, sample_rainfall_response):
        """Verify zero precipitation is handled correctly."""
        mock_ncei.get("/access/services/data/v1").mock(
            return_value=httpx.Response(200, json=[{"DATE": "2026-01-15", "STATION": "USW00094276", "PRCP": "0.00"}])
        )

        mock_agriwebb.post("/v2").mock(return_value=httpx.Response(200, json=sample_rainfall_response))

        weather_data = await weather.fetch_ncei_precipitation(date(2026, 1, 15))
        assert weather_data["precipitation_inches"] == 0.0

        # Should still be able to push zero rainfall
        response = await weather_api.add_rainfall(weather_data["date"], weather_data["precipitation_inches"])
        assert "data" in response
