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


class TestFilterChangedRecords:
    """Tests for the filter_changed_records function."""

    def test_skips_unchanged_records(self):
        """Records with same value in AgriWebb should be skipped."""
        from agriwebb.weather.cli import filter_changed_records

        weather_data = [
            {"date": "2026-01-15", "precipitation_inches": 0.25, "source": "noaa"},
        ]
        # 0.25 inches = 6.35 mm
        existing_by_date = {"2026-01-15": 6.35}

        result = filter_changed_records(weather_data, existing_by_date)

        assert len(result["records_to_push"]) == 0
        assert result["skipped_count"] == 1

    def test_includes_new_records(self):
        """Records not in AgriWebb should be included."""
        from agriwebb.weather.cli import filter_changed_records

        weather_data = [
            {"date": "2026-01-15", "precipitation_inches": 0.25, "source": "noaa"},
        ]
        existing_by_date = {}  # No existing records

        result = filter_changed_records(weather_data, existing_by_date)

        assert len(result["records_to_push"]) == 1
        assert result["skipped_count"] == 0
        assert result["records_to_push"][0]["date"] == "2026-01-15"

    def test_includes_changed_records(self):
        """Records with different values should be included."""
        from agriwebb.weather.cli import filter_changed_records

        weather_data = [
            {"date": "2026-01-15", "precipitation_inches": 0.50, "source": "noaa"},
        ]
        # Existing has 0.25 inches (6.35 mm), new is 0.50 inches (12.7 mm)
        existing_by_date = {"2026-01-15": 6.35}

        result = filter_changed_records(weather_data, existing_by_date)

        assert len(result["records_to_push"]) == 1
        assert result["skipped_count"] == 0

    def test_force_includes_all_records(self):
        """With force=True, all records should be included."""
        from agriwebb.weather.cli import filter_changed_records

        weather_data = [
            {"date": "2026-01-15", "precipitation_inches": 0.25, "source": "noaa"},
            {"date": "2026-01-16", "precipitation_inches": 0.10, "source": "noaa"},
        ]
        # Both dates have same values in AgriWebb
        existing_by_date = {
            "2026-01-15": 6.35,  # 0.25 inches
            "2026-01-16": 2.54,  # 0.10 inches
        }

        result = filter_changed_records(weather_data, existing_by_date, force=True)

        assert len(result["records_to_push"]) == 2
        assert result["skipped_count"] == 0

    def test_tolerance_within_threshold(self):
        """Values within 0.01mm tolerance should be considered equal."""
        from agriwebb.weather.cli import filter_changed_records

        weather_data = [
            {"date": "2026-01-15", "precipitation_inches": 0.25, "source": "noaa"},
        ]
        # 0.25 inches = 6.35 mm, existing is 6.351 mm (within 0.01 tolerance)
        existing_by_date = {"2026-01-15": 6.351}

        result = filter_changed_records(weather_data, existing_by_date)

        assert len(result["records_to_push"]) == 0
        assert result["skipped_count"] == 1

    def test_tolerance_outside_threshold(self):
        """Values outside 0.01mm tolerance should be considered different."""
        from agriwebb.weather.cli import filter_changed_records

        weather_data = [
            {"date": "2026-01-15", "precipitation_inches": 0.25, "source": "noaa"},
        ]
        # 0.25 inches = 6.35 mm, existing is 6.37 mm (0.02 difference, outside tolerance)
        existing_by_date = {"2026-01-15": 6.37}

        result = filter_changed_records(weather_data, existing_by_date)

        assert len(result["records_to_push"]) == 1
        assert result["skipped_count"] == 0

    def test_mixed_records(self):
        """Mix of new, unchanged, and changed records."""
        from agriwebb.weather.cli import filter_changed_records

        weather_data = [
            {"date": "2026-01-13", "precipitation_inches": 0.10, "source": "noaa"},  # New
            {"date": "2026-01-14", "precipitation_inches": 0.25, "source": "noaa"},  # Unchanged
            {"date": "2026-01-15", "precipitation_inches": 0.50, "source": "noaa"},  # Changed
        ]
        existing_by_date = {
            "2026-01-14": 6.35,  # 0.25 inches - unchanged
            "2026-01-15": 2.54,  # 0.10 inches - will be updated to 0.50
        }

        result = filter_changed_records(weather_data, existing_by_date)

        assert len(result["records_to_push"]) == 2  # New + Changed
        assert result["skipped_count"] == 1  # Unchanged
        dates_to_push = [r["date"] for r in result["records_to_push"]]
        assert "2026-01-13" in dates_to_push  # New
        assert "2026-01-15" in dates_to_push  # Changed
        assert "2026-01-14" not in dates_to_push  # Unchanged


class TestValuesMatch:
    """Tests for the _values_match helper function."""

    def test_exact_match(self):
        """Exact same values should match."""
        from agriwebb.weather.cli import _values_match

        assert _values_match(6.35, 6.35) is True

    def test_within_default_tolerance(self):
        """Values within 0.01mm should match."""
        from agriwebb.weather.cli import _values_match

        assert _values_match(6.35, 6.355) is True
        assert _values_match(6.35, 6.345) is True

    def test_outside_default_tolerance(self):
        """Values outside 0.01mm should not match."""
        from agriwebb.weather.cli import _values_match

        assert _values_match(6.35, 6.37) is False
        assert _values_match(6.35, 6.33) is False

    def test_custom_tolerance(self):
        """Custom tolerance should be respected."""
        from agriwebb.weather.cli import _values_match

        # With larger tolerance, these should match
        assert _values_match(6.35, 6.40, tolerance=0.1) is True
        # With smaller tolerance, they should not
        assert _values_match(6.35, 6.36, tolerance=0.005) is False


class TestGetRecordStatus:
    """Tests for the _get_record_status helper function."""

    def test_new_record(self):
        """Record not in existing should show 'new'."""
        from agriwebb.weather.cli import _get_record_status

        record = {"date": "2026-01-15", "precipitation_inches": 0.25}
        status = _get_record_status(record, {})

        assert status == "new"

    def test_unchanged_record(self):
        """Record with same value should show 'unchanged'."""
        from agriwebb.weather.cli import _get_record_status

        record = {"date": "2026-01-15", "precipitation_inches": 0.25}
        existing = {"2026-01-15": 6.35}  # 0.25 inches = 6.35 mm
        status = _get_record_status(record, existing)

        assert status == "unchanged"

    def test_updated_record(self):
        """Record with different value should show update with values."""
        from agriwebb.weather.cli import _get_record_status

        record = {"date": "2026-01-15", "precipitation_inches": 0.50}
        existing = {"2026-01-15": 6.35}  # 0.25 inches
        status = _get_record_status(record, existing)

        assert "update" in status
        assert "0.25" in status  # Old value in inches
        assert "0.50" in status  # New value in inches

    def test_force_status(self):
        """With force=True, all records should show 'force'."""
        from agriwebb.weather.cli import _get_record_status

        record = {"date": "2026-01-15", "precipitation_inches": 0.25}
        existing = {"2026-01-15": 6.35}  # Same value
        status = _get_record_status(record, existing, force=True)

        assert status == "force"


class TestBuildExistingRainfallLookup:
    """Tests for the _build_existing_rainfall_lookup helper function."""

    def test_builds_date_to_value_dict(self):
        """Should convert API records to date->value dict."""
        from agriwebb.weather.cli import _build_existing_rainfall_lookup

        # Timestamps are in milliseconds, noon UTC
        rainfalls = [
            {"time": 1705320000000, "value": 6.35},  # 2024-01-15 12:00 UTC
            {"time": 1705406400000, "value": 12.7},  # 2024-01-16 12:00 UTC
        ]

        lookup = _build_existing_rainfall_lookup(rainfalls)

        assert lookup["2024-01-15"] == 6.35
        assert lookup["2024-01-16"] == 12.7

    def test_empty_list_returns_empty_dict(self):
        """Empty input should return empty dict."""
        from agriwebb.weather.cli import _build_existing_rainfall_lookup

        lookup = _build_existing_rainfall_lookup([])

        assert lookup == {}


class TestCalculateTotalDays:
    """Tests for the _calculate_total_days helper function."""

    def test_days_only(self):
        """Days argument should be returned directly."""
        from agriwebb.weather.cli import _calculate_total_days

        assert _calculate_total_days(14, None, None) == 14

    def test_months_converted(self):
        """Months should be converted to days (30 days per month)."""
        from agriwebb.weather.cli import _calculate_total_days

        assert _calculate_total_days(None, 1, None) == 30
        assert _calculate_total_days(None, 2, None) == 60

    def test_years_converted(self):
        """Years should be converted to days (365 days per year)."""
        from agriwebb.weather.cli import _calculate_total_days

        assert _calculate_total_days(None, None, 1) == 365
        assert _calculate_total_days(None, None, 2) == 730

    def test_combined(self):
        """Multiple arguments should be summed."""
        from agriwebb.weather.cli import _calculate_total_days

        # 7 days + 1 month (30) + 1 year (365) = 402
        assert _calculate_total_days(7, 1, 1) == 402

    def test_all_none(self):
        """All None should return 0."""
        from agriwebb.weather.cli import _calculate_total_days

        assert _calculate_total_days(None, None, None) == 0
