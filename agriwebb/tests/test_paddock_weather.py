"""Tests for per-paddock weather caching.

Verifies the cache format, flattening into ``weather_by_paddock``, and the
``update_paddock_weather_cache`` flow via mocked Open-Meteo responses.
"""

import json
from pathlib import Path

import pytest
import respx
from httpx import Response

from agriwebb.weather.openmeteo import FORECAST_API, HISTORICAL_API
from agriwebb.weather.paddock_weather import (
    load_paddock_weather_cache,
    save_paddock_weather_cache,
    update_paddock_weather_cache,
    weather_by_paddock_from_cache,
)


@pytest.fixture
def isolated_cache(tmp_path, monkeypatch):
    from agriwebb.core import config as core_config

    def _fake_cache_dir() -> Path:
        return tmp_path

    monkeypatch.setattr(core_config, "get_cache_dir", _fake_cache_dir)
    import agriwebb.core as core_pkg

    monkeypatch.setattr(core_pkg, "get_cache_dir", _fake_cache_dir)
    yield tmp_path


class TestLoadSave:
    def test_load_missing_returns_none(self, isolated_cache):
        assert load_paddock_weather_cache() is None

    def test_save_then_load_roundtrip(self, isolated_cache):
        data = {
            "fetched_at": "2026-04-10T12:00:00",
            "paddocks": {
                "Alpha": {
                    "paddock_id": "id-1",
                    "centroid": {"lat": 48.5, "lon": -123.0},
                    "daily_data": [
                        {
                            "date": "2026-04-01",
                            "temp_mean_c": 10,
                            "temp_max_c": 15,
                            "temp_min_c": 5,
                            "precip_mm": 5,
                            "et0_mm": 2,
                        },
                    ],
                }
            },
        }
        save_paddock_weather_cache(data)
        loaded = load_paddock_weather_cache()
        assert loaded is not None
        assert loaded["paddocks"]["Alpha"]["paddock_id"] == "id-1"
        assert len(loaded["paddocks"]["Alpha"]["daily_data"]) == 1

    def test_corrupt_file_returns_none(self, isolated_cache):
        path = isolated_cache / "paddock_weather.json"
        path.write_text("not valid json {[")
        assert load_paddock_weather_cache() is None


class TestWeatherByPaddockFromCache:
    def test_none_cache_returns_empty(self):
        assert weather_by_paddock_from_cache(None) == {}

    def test_flattens_paddocks(self):
        cache = {
            "fetched_at": "2026-04-10",
            "paddocks": {
                "Alpha": {
                    "paddock_id": "a",
                    "centroid": {"lat": 48.5, "lon": -123.0},
                    "daily_data": [{"date": "2026-04-01"}],
                },
                "Beta": {
                    "paddock_id": "b",
                    "centroid": {"lat": 48.6, "lon": -123.1},
                    "daily_data": [{"date": "2026-04-01"}, {"date": "2026-04-02"}],
                },
            },
        }
        result = weather_by_paddock_from_cache(cache)
        assert set(result.keys()) == {"Alpha", "Beta"}
        assert len(result["Alpha"]) == 1
        assert len(result["Beta"]) == 2

    def test_missing_daily_data_empty_list(self):
        cache = {
            "paddocks": {"Alpha": {"paddock_id": "a", "centroid": {}}},
        }
        result = weather_by_paddock_from_cache(cache)
        assert result["Alpha"] == []


class TestUpdatePaddockWeatherCache:
    @pytest.fixture
    def mock_historical(self):
        return {
            "daily": {
                "time": ["2024-01-01", "2024-01-02"],
                "temperature_2m_max": [10.0, 11.0],
                "temperature_2m_min": [5.0, 6.0],
                "temperature_2m_mean": [7.5, 8.5],
                "precipitation_sum": [2.0, 5.0],
                "et0_fao_evapotranspiration": [1.0, 1.5],
            }
        }

    @pytest.fixture
    def mock_forecast(self):
        return {
            "daily": {
                "time": ["2026-04-08", "2026-04-09"],
                "temperature_2m_max": [12.0, 13.0],
                "temperature_2m_min": [6.0, 7.0],
                "precipitation_sum": [0.0, 1.0],
                "et0_fao_evapotranspiration": [2.0, 2.5],
            }
        }

    @respx.mock
    @pytest.mark.asyncio
    async def test_skips_paddocks_without_centroid(self, isolated_cache, mock_historical, mock_forecast):
        respx.get(HISTORICAL_API).mock(return_value=Response(200, json=mock_historical))
        respx.get(FORECAST_API).mock(return_value=Response(200, json=mock_forecast))

        paddocks = {
            "Alpha": {"paddock_id": "a", "centroid": {"lat": 48.5, "lon": -123.0}},
            "Broken": {"paddock_id": "b"},  # No centroid
        }
        result = await update_paddock_weather_cache(paddocks, verbose=False)
        assert "Alpha" in result["paddocks"]
        assert "Broken" not in result["paddocks"]

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetches_and_caches(self, isolated_cache, mock_historical, mock_forecast):
        respx.get(HISTORICAL_API).mock(return_value=Response(200, json=mock_historical))
        respx.get(FORECAST_API).mock(return_value=Response(200, json=mock_forecast))

        paddocks = {
            "Alpha": {"paddock_id": "a", "centroid": {"lat": 48.5, "lon": -123.0}},
        }
        result = await update_paddock_weather_cache(paddocks, verbose=False)

        assert "Alpha" in result["paddocks"]
        entry = result["paddocks"]["Alpha"]
        assert entry["paddock_id"] == "a"
        assert entry["centroid"]["lat"] == 48.5
        # Should have merged historical + forecast records
        assert len(entry["daily_data"]) >= 2

        # Cache file should exist
        cache_file = isolated_cache / "paddock_weather.json"
        assert cache_file.exists()

        # Loading it should roundtrip
        loaded = json.loads(cache_file.read_text())
        assert "Alpha" in loaded["paddocks"]

    @respx.mock
    @pytest.mark.asyncio
    async def test_survives_one_paddock_error(self, isolated_cache, mock_historical, mock_forecast):
        # First call succeeds, second fails
        call_count = [0]

        def side_effect(request):
            call_count[0] += 1
            if call_count[0] <= 2:  # First paddock: historical + forecast
                return Response(200, json=mock_historical if "archive" in str(request.url) else mock_forecast)
            return Response(500, text="boom")

        respx.get(HISTORICAL_API).mock(side_effect=side_effect)
        respx.get(FORECAST_API).mock(side_effect=side_effect)

        paddocks = {
            "Alpha": {"paddock_id": "a", "centroid": {"lat": 48.5, "lon": -123.0}},
            "Beta": {"paddock_id": "b", "centroid": {"lat": 48.6, "lon": -123.1}},
        }
        result = await update_paddock_weather_cache(paddocks, verbose=False)
        # Alpha should succeed; Beta should not crash the whole thing
        assert "Alpha" in result["paddocks"]
        # Beta may or may not be present depending on retry behavior
        assert isinstance(result["paddocks"], dict)
