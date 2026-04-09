"""Tests for shared utility modules consolidated from duplicated code."""

from datetime import date

import pytest

from agriwebb.core.cache import load_cache_json
from agriwebb.core.timestamps import to_timestamp_ms
from agriwebb.pasture.growth import Season, get_season

# =============================================================================
# TestToTimestampMs - canonical location is core.timestamps
# =============================================================================


class TestToTimestampMs:
    """Test the to_timestamp_ms conversion utility."""

    def test_date_object(self):
        """A date object should convert to noon-UTC milliseconds."""
        ts = to_timestamp_ms(date(2024, 1, 15))
        # 2024-01-15 12:00 UTC
        assert ts == 1705320000000

    def test_date_string(self):
        """An ISO date string should convert identically to a date object."""
        ts_str = to_timestamp_ms("2024-01-15")
        ts_date = to_timestamp_ms(date(2024, 1, 15))
        assert ts_str == ts_date

    def test_epoch(self):
        """1970-01-01 should give noon UTC in ms."""
        ts = to_timestamp_ms(date(1970, 1, 1))
        assert ts == 12 * 3600 * 1000  # 43200000

    def test_import_from_weather_api(self):
        """The weather.api module should re-export the shared utility."""
        from agriwebb.weather.api import to_timestamp_ms as weather_ts

        assert weather_ts is to_timestamp_ms

    def test_import_from_pasture_api(self):
        """The pasture.api module should re-export the shared utility."""
        from agriwebb.pasture.api import to_timestamp_ms as pasture_ts

        assert pasture_ts is to_timestamp_ms

    def test_import_from_core(self):
        """The core package should export to_timestamp_ms."""
        from agriwebb.core import to_timestamp_ms as core_ts

        assert core_ts is to_timestamp_ms


# =============================================================================
# TestSeasonConsistency - canonical enum in growth.py, re-exported by biomass.py
# =============================================================================


class TestSeasonConsistency:
    """Verify Season enum and get_season are consistent across both modules."""

    def test_biomass_season_is_same_enum(self):
        """biomass.Season should be the exact same class as growth.Season."""
        from agriwebb.pasture.biomass import Season as BiomassSeason

        assert BiomassSeason is Season

    def test_biomass_get_season_matches_growth(self):
        """biomass.get_season(month) should agree with growth.get_season(date)."""
        from agriwebb.pasture.biomass import get_season as biomass_get_season

        for month in range(1, 13):
            d = date(2024, month, 15)
            assert biomass_get_season(month) == get_season(d), f"Mismatch for month {month}"

    def test_all_months_covered(self):
        """Every month 1-12 should map to a valid Season."""
        for month in range(1, 13):
            season = get_season(date(2024, month, 15))
            assert isinstance(season, Season)

    def test_season_values(self):
        """Season enum values should be lowercase season names."""
        assert Season.WINTER.value == "winter"
        assert Season.SPRING.value == "spring"
        assert Season.SUMMER.value == "summer"
        assert Season.FALL.value == "fall"

    def test_month_to_season_mapping(self):
        """Verify the exact month-to-season mapping."""
        expected = {
            1: Season.WINTER,
            2: Season.WINTER,
            3: Season.SPRING,
            4: Season.SPRING,
            5: Season.SPRING,
            6: Season.SUMMER,
            7: Season.SUMMER,
            8: Season.SUMMER,
            9: Season.FALL,
            10: Season.FALL,
            11: Season.FALL,
            12: Season.WINTER,
        }
        for month, expected_season in expected.items():
            assert get_season(date(2024, month, 15)) == expected_season

    def test_pasture_init_exports_season(self):
        """The pasture package should export Season from its __init__."""
        from agriwebb.pasture import Season as PastureSeason

        assert PastureSeason is Season


# =============================================================================
# TestLoadCacheJson
# =============================================================================


class TestLoadCacheJson:
    """Test the shared cache loading utility."""

    def test_missing_file_raises(self, tmp_path, monkeypatch):
        """A missing cache file should raise FileNotFoundError."""
        monkeypatch.setattr("agriwebb.core.cache.get_cache_dir", lambda: tmp_path)
        with pytest.raises(FileNotFoundError, match="Cache file not found"):
            load_cache_json("nonexistent.json")

    def test_load_full_json(self, tmp_path, monkeypatch):
        """Loading without a key should return the full parsed JSON."""
        monkeypatch.setattr("agriwebb.core.cache.get_cache_dir", lambda: tmp_path)
        import json

        data = {"animals": [{"id": 1}], "meta": {"count": 1}}
        (tmp_path / "test.json").write_text(json.dumps(data))
        result = load_cache_json("test.json")
        assert result == data

    def test_load_with_key(self, tmp_path, monkeypatch):
        """Loading with a key should extract that top-level key."""
        monkeypatch.setattr("agriwebb.core.cache.get_cache_dir", lambda: tmp_path)
        import json

        data = {"animals": [{"id": 1}], "meta": {"count": 1}}
        (tmp_path / "test.json").write_text(json.dumps(data))
        result = load_cache_json("test.json", key="animals")
        assert result == [{"id": 1}]

    def test_load_with_missing_key_returns_default(self, tmp_path, monkeypatch):
        """A missing key should return the default (empty list by default)."""
        monkeypatch.setattr("agriwebb.core.cache.get_cache_dir", lambda: tmp_path)
        import json

        data = {"animals": [{"id": 1}]}
        (tmp_path / "test.json").write_text(json.dumps(data))
        result = load_cache_json("test.json", key="missing_key")
        assert result == []

    def test_load_with_missing_key_custom_default(self, tmp_path, monkeypatch):
        """A missing key with a custom default should return that default."""
        monkeypatch.setattr("agriwebb.core.cache.get_cache_dir", lambda: tmp_path)
        import json

        data = {"animals": [{"id": 1}]}
        (tmp_path / "test.json").write_text(json.dumps(data))
        result = load_cache_json("test.json", key="missing_key", default={})
        assert result == {}

    def test_import_from_core(self):
        """The core package should export load_cache_json."""
        from agriwebb.core import load_cache_json as core_load

        assert core_load is load_cache_json
