"""Tests for Open-Meteo weather API integration."""

from datetime import date

import pytest
import respx
from httpx import Response

from agriwebb.weather.openmeteo import (
    DEFAULT_LAT,
    DEFAULT_LON,
    FORECAST_API,
    HISTORICAL_API,
    fetch_current_conditions,
    fetch_forecast,
    fetch_historical,
    update_weather_cache,
)


class TestFetchHistorical:
    """Tests for historical weather fetching."""

    @pytest.fixture
    def mock_historical_response(self):
        """Sample Open-Meteo historical API response."""
        return {
            "latitude": DEFAULT_LAT,
            "longitude": DEFAULT_LON,
            "daily": {
                "time": ["2024-01-01", "2024-01-02", "2024-01-03"],
                "temperature_2m_max": [8.5, 9.2, 7.8],
                "temperature_2m_min": [2.1, 3.4, 1.9],
                "temperature_2m_mean": [5.3, 6.3, 4.9],
                "precipitation_sum": [12.5, 0.0, 5.2],
                "et0_fao_evapotranspiration": [0.8, 1.2, 0.9],
            }
        }

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_historical_success(self, mock_historical_response):
        """Successfully fetches and parses historical data."""
        respx.get(HISTORICAL_API).mock(
            return_value=Response(200, json=mock_historical_response)
        )

        result = await fetch_historical(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 3),
        )

        assert len(result) == 3
        assert result[0]["date"] == "2024-01-01"
        assert result[0]["temp_mean_c"] == 5.3
        assert result[0]["precip_mm"] == 12.5
        assert result[0]["et0_mm"] == 0.8

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_historical_handles_nulls(self):
        """Handles null values in API response."""
        response = {
            "daily": {
                "time": ["2024-01-01"],
                "temperature_2m_max": [None],
                "temperature_2m_min": [None],
                "temperature_2m_mean": [None],
                "precipitation_sum": [None],
                "et0_fao_evapotranspiration": [None],
            }
        }
        respx.get(HISTORICAL_API).mock(
            return_value=Response(200, json=response)
        )

        result = await fetch_historical(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 1),
        )

        assert len(result) == 1
        assert result[0]["temp_mean_c"] == 0
        assert result[0]["precip_mm"] == 0

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_historical_custom_location(self, mock_historical_response):
        """Can fetch for custom location."""
        route = respx.get(HISTORICAL_API).mock(
            return_value=Response(200, json=mock_historical_response)
        )

        await fetch_historical(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 3),
            lat=45.0,
            lon=-120.0,
        )

        # Verify latitude was passed
        request = route.calls[0].request
        assert "45" in str(request.url)


class TestFetchForecast:
    """Tests for forecast fetching."""

    @pytest.fixture
    def mock_forecast_response(self):
        """Sample Open-Meteo forecast API response."""
        return {
            "latitude": DEFAULT_LAT,
            "longitude": DEFAULT_LON,
            "daily": {
                "time": ["2024-01-15", "2024-01-16", "2024-01-17"],
                "temperature_2m_max": [10.5, 11.2, 9.8],
                "temperature_2m_min": [4.1, 5.4, 3.9],
                "precipitation_sum": [0.0, 8.5, 2.2],
                "et0_fao_evapotranspiration": [1.5, 1.0, 1.2],
            }
        }

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_forecast_success(self, mock_forecast_response):
        """Successfully fetches forecast data."""
        respx.get(FORECAST_API).mock(
            return_value=Response(200, json=mock_forecast_response)
        )

        result = await fetch_forecast(days=3)

        assert len(result) == 3
        assert result[0]["date"] == "2024-01-15"
        assert result[1]["precip_mm"] == 8.5

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_forecast_calculates_mean_temp(self, mock_forecast_response):
        """Forecast calculates mean from max/min."""
        respx.get(FORECAST_API).mock(
            return_value=Response(200, json=mock_forecast_response)
        )

        result = await fetch_forecast(days=3)

        # Mean should be calculated from max/min
        expected_mean = (10.5 + 4.1) / 2
        assert abs(result[0]["temp_mean_c"] - expected_mean) < 0.1


class TestFetchCurrentConditions:
    """Tests for current conditions fetching."""

    @pytest.fixture
    def mock_current_response(self):
        """Sample Open-Meteo current conditions response."""
        return {
            "latitude": DEFAULT_LAT,
            "longitude": DEFAULT_LON,
            "current": {
                "temperature_2m": 8.5,
                "precipitation": 0.2,
                "wind_speed_10m": 12.5,
                "weather_code": 3,
            }
        }

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_current_conditions(self, mock_current_response):
        """Fetches current weather conditions."""
        respx.get(FORECAST_API).mock(
            return_value=Response(200, json=mock_current_response)
        )

        result = await fetch_current_conditions()

        assert result is not None
        assert result["temperature_c"] == 8.5


class TestUpdateWeatherCache:
    """Tests for cache updating."""

    @pytest.fixture
    def mock_api_responses(self):
        """Set up mock API responses for cache update."""
        historical_response = {
            "daily": {
                "time": ["2024-01-01"],
                "temperature_2m_max": [8.5],
                "temperature_2m_min": [2.1],
                "temperature_2m_mean": [5.3],
                "precipitation_sum": [12.5],
                "et0_fao_evapotranspiration": [0.8],
            }
        }
        forecast_response = {
            "daily": {
                "time": ["2024-01-15", "2024-01-16"],
                "temperature_2m_max": [10.5, 11.2],
                "temperature_2m_min": [4.1, 5.4],
                "precipitation_sum": [0.0, 8.5],
                "et0_fao_evapotranspiration": [1.5, 1.0],
            }
        }
        return historical_response, forecast_response

    @respx.mock
    @pytest.mark.asyncio
    async def test_update_cache_structure(self, mock_api_responses, tmp_path):
        """Cache update returns proper structure."""
        historical, forecast = mock_api_responses

        respx.get(HISTORICAL_API).mock(
            return_value=Response(200, json=historical)
        )
        respx.get(FORECAST_API).mock(
            return_value=Response(200, json=forecast)
        )

        cache_path = tmp_path / "weather.json"
        result = await update_weather_cache(cache_path=cache_path)

        assert "daily_data" in result
        assert "fetched_at" in result
        assert "location" in result


class TestDailyWeatherTypedDict:
    """Tests for DailyWeather structure."""

    def test_daily_weather_fields(self):
        """Verify DailyWeather has expected fields."""
        from agriwebb.weather.openmeteo import DailyWeather

        record: DailyWeather = {
            "date": "2024-01-15",
            "temp_mean_c": 8.5,
            "temp_max_c": 12.0,
            "temp_min_c": 5.0,
            "precip_mm": 10.5,
            "et0_mm": 1.5,
        }

        assert record["date"] == "2024-01-15"
        assert record["temp_mean_c"] == 8.5
        assert record["et0_mm"] == 1.5


class TestAPIEndpoints:
    """Tests for API endpoint configuration."""

    def test_historical_api_url(self):
        """Historical API uses archive endpoint."""
        assert "archive" in HISTORICAL_API
        assert "open-meteo.com" in HISTORICAL_API

    def test_forecast_api_url(self):
        """Forecast API uses main endpoint."""
        assert "api.open-meteo.com" in FORECAST_API
        assert "forecast" in FORECAST_API

    def test_default_location_is_san_juan(self):
        """Default location is San Juan Islands."""
        # San Juan Islands, WA is around 48.5°N, 123°W
        assert 48.0 < DEFAULT_LAT < 49.0
        assert -124.0 < DEFAULT_LON < -122.0


class TestWeatherDataIntegration:
    """Integration tests for weather data processing."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_historical_data_for_growth_model(self):
        """Historical data works with growth model requirements."""
        response = {
            "daily": {
                "time": ["2024-04-15"],
                "temperature_2m_max": [18.0],
                "temperature_2m_min": [8.0],
                "temperature_2m_mean": [13.0],
                "precipitation_sum": [5.0],
                "et0_fao_evapotranspiration": [3.5],
            }
        }
        respx.get(HISTORICAL_API).mock(
            return_value=Response(200, json=response)
        )

        result = await fetch_historical(
            start_date=date(2024, 4, 15),
            end_date=date(2024, 4, 15),
        )

        # Verify data can be used by growth model
        record = result[0]
        assert "temp_mean_c" in record
        assert "precip_mm" in record
        assert "et0_mm" in record

        # Values are in expected ranges
        assert 0 <= record["temp_mean_c"] <= 50
        assert 0 <= record["precip_mm"] <= 200
        assert 0 <= record["et0_mm"] <= 15


class TestErrorHandling:
    """Tests for API error handling."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_historical_api_error(self):
        """Handles API errors gracefully."""
        respx.get(HISTORICAL_API).mock(
            return_value=Response(500, text="Server Error")
        )

        with pytest.raises(Exception):
            await fetch_historical(
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 3),
            )

    @respx.mock
    @pytest.mark.asyncio
    async def test_forecast_api_error(self):
        """Handles forecast API errors."""
        respx.get(FORECAST_API).mock(
            return_value=Response(503, text="Service Unavailable")
        )

        with pytest.raises(Exception):
            await fetch_forecast(days=7)
