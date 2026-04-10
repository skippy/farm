"""Tests for the satellite-observation validation gate.

Covers the three layers in pasture/validate.py:
1. Raw NDVI observation sanity
2. Growth-delta plausibility against weather model
3. Temporal smoothing against rolling history
"""

import json
from pathlib import Path

import pytest

from agriwebb.pasture.validate import (
    GROWTH_HEADROOM,
    MIN_CLOUD_FREE_PCT,
    MIN_PIXEL_COUNT,
    NDVI_MAX_STDDEV,
    NDVI_MAX_VALID,
    NDVI_MIN_VALID,
    TEMPORAL_HISTORY_KEEP,
    append_paddock_history,
    apply_temporal_filter,
    get_ndvi_history_dir,
    load_paddock_history,
    validate_growth_delta,
    validate_ndvi_observation,
)

# =============================================================================
# Layer 1: validate_ndvi_observation
# =============================================================================


class TestValidateNdviObservation:
    """Sanity-check raw NDVI observations."""

    def test_clean_observation_passes(self):
        result = validate_ndvi_observation(
            ndvi_mean=0.55,
            ndvi_stddev=0.08,
            cloud_free_pct=85.0,
            pixel_count=2000,
        )
        assert result.valid
        assert result.reasons == []

    def test_none_ndvi_fails(self):
        result = validate_ndvi_observation(ndvi_mean=None)
        assert not result.valid
        assert "no NDVI value" in result.reason

    def test_negative_ndvi_below_min_fails(self):
        # Opalco field actual incident: NDVI=-0.027 was below min
        result = validate_ndvi_observation(ndvi_mean=NDVI_MIN_VALID - 0.01)
        assert not result.valid
        assert "outside" in result.reason

    def test_ndvi_above_max_fails(self):
        result = validate_ndvi_observation(ndvi_mean=NDVI_MAX_VALID + 0.1)
        assert not result.valid
        assert "outside" in result.reason

    def test_high_stddev_fails(self):
        # Opalco field actual incident: stddev=4.298
        result = validate_ndvi_observation(
            ndvi_mean=0.4,
            ndvi_stddev=NDVI_MAX_STDDEV + 0.01,
        )
        assert not result.valid
        assert "stddev" in result.reason

    def test_low_cloud_free_fails(self):
        result = validate_ndvi_observation(
            ndvi_mean=0.5,
            cloud_free_pct=MIN_CLOUD_FREE_PCT - 1,
        )
        assert not result.valid
        assert "cloud-free" in result.reason

    def test_low_pixel_count_fails(self):
        result = validate_ndvi_observation(
            ndvi_mean=0.5,
            pixel_count=MIN_PIXEL_COUNT - 1,
        )
        assert not result.valid
        assert "pixels" in result.reason

    def test_multiple_failures_collected(self):
        result = validate_ndvi_observation(
            ndvi_mean=2.0,  # outside range
            ndvi_stddev=0.5,  # too noisy
            cloud_free_pct=5.0,  # too cloudy
            pixel_count=2,  # too sparse
        )
        assert not result.valid
        assert len(result.reasons) == 4

    def test_stddev_none_skips_stddev_check(self):
        result = validate_ndvi_observation(
            ndvi_mean=0.5,
            ndvi_stddev=None,
            cloud_free_pct=80,
            pixel_count=100,
        )
        assert result.valid

    def test_default_optional_args_pass(self):
        # When stddev/cloud/pixel info isn't available, defaults shouldn't fail
        result = validate_ndvi_observation(ndvi_mean=0.4)
        assert result.valid


# =============================================================================
# Layer 2: validate_growth_delta
# =============================================================================


class TestValidateGrowthDelta:
    """Compare NDVI delta against weather model upper bound."""

    def test_plausible_growth_passes(self):
        # 30 kg/ha/day growth over 14 days = 420 kg/ha gain
        # Weather max for spring is 80 → headroom 1.5 → ceiling 1680
        result = validate_growth_delta(
            sdm_curr=2000,
            sdm_prev=1580,
            days=14,
            weather_max_growth_kg_ha_day=80,
        )
        assert result.valid

    def test_impossible_growth_fails(self):
        # 200 kg/ha/day for 14 days = 2800 kg/ha gain
        # Spring max with headroom: 80 * 14 * 1.5 = 1680 → fail
        result = validate_growth_delta(
            sdm_curr=4000,
            sdm_prev=1200,
            days=14,
            weather_max_growth_kg_ha_day=80,
        )
        assert not result.valid
        assert "impossible" in result.reason

    def test_decreasing_sdm_passes(self):
        # Decline (grazing or senescence) is always allowed by this gate
        result = validate_growth_delta(
            sdm_curr=1500,
            sdm_prev=2000,
            days=14,
            weather_max_growth_kg_ha_day=80,
        )
        assert result.valid

    def test_zero_days_fails(self):
        result = validate_growth_delta(
            sdm_curr=2000,
            sdm_prev=1500,
            days=0,
            weather_max_growth_kg_ha_day=80,
        )
        assert not result.valid
        assert "days" in result.reason

    def test_negative_sdm_fails(self):
        result = validate_growth_delta(
            sdm_curr=-100,
            sdm_prev=500,
            days=14,
            weather_max_growth_kg_ha_day=80,
        )
        assert not result.valid
        assert "negative" in result.reason

    def test_winter_lower_threshold(self):
        # Winter max is much lower (15 kg/ha/day)
        # With headroom 1.5 over 14 days: ceiling = 315 kg/ha
        result = validate_growth_delta(
            sdm_curr=2000,
            sdm_prev=1500,  # 500 kg gain
            days=14,
            weather_max_growth_kg_ha_day=15,
        )
        assert not result.valid

    def test_custom_headroom(self):
        # With more generous headroom, the same delta passes
        result = validate_growth_delta(
            sdm_curr=2000,
            sdm_prev=1500,
            days=14,
            weather_max_growth_kg_ha_day=15,
            headroom=3.0,
        )
        assert result.valid

    def test_default_headroom_is_constant(self):
        # The default should match the module constant
        # (just a guard against accidental drift)
        result_default = validate_growth_delta(
            sdm_curr=1100,
            sdm_prev=1000,
            days=10,
            weather_max_growth_kg_ha_day=80,
        )
        result_explicit = validate_growth_delta(
            sdm_curr=1100,
            sdm_prev=1000,
            days=10,
            weather_max_growth_kg_ha_day=80,
            headroom=GROWTH_HEADROOM,
        )
        assert result_default.valid == result_explicit.valid


# =============================================================================
# Layer 3: apply_temporal_filter
# =============================================================================


class TestApplyTemporalFilter:
    """Trend-aware delta-based filter for single-point spikes."""

    def test_too_short_history_passes_through(self):
        # Need at least 4 points (3 deltas) to do anything
        for history in [[], [100.0], [100.0, 110.0], [100.0, 110.0, 105.0]]:
            value, replaced = apply_temporal_filter(history, 9999.0)
            assert value == 9999.0
            assert not replaced

    def test_spike_on_stable_baseline_replaced(self):
        # Stable ~1000 baseline → spike to 5000 → replaced with expected next
        history = [1000.0, 1050.0, 980.0, 1020.0, 1010.0]
        value, replaced = apply_temporal_filter(history, 5000.0)
        assert replaced
        # Expected = last + median(deltas) = 1010 + median([50,-70,40,-10]) = 1010 + 15
        assert value == 1025.0

    def test_linear_spring_growth_passes(self):
        # Consistent 100 kg/window growth in spring → next 100 kg is fine
        # Add small jitter so stdev is nonzero (perfectly linear returns passthrough)
        history = [1000.0, 1095.0, 1200.0, 1305.0, 1400.0]  # deltas 95, 105, 105, 95
        value, replaced = apply_temporal_filter(history, 1500.0)
        # new delta = 100, median delta = 100, within 3σ → passes
        assert not replaced
        assert value == 1500.0

    def test_trending_then_spike_replaced(self):
        # Linear growth, then a huge jump
        history = [1000.0, 1100.0, 1205.0, 1295.0, 1410.0]
        value, replaced = apply_temporal_filter(history, 3000.0)
        assert replaced

    def test_zero_stddev_history_passes_through(self):
        # Perfectly flat history → stddev=0 → any change allowed
        history = [1000.0, 1000.0, 1000.0, 1000.0]
        value, replaced = apply_temporal_filter(history, 1500.0)
        assert not replaced
        assert value == 1500.0

    def test_perfectly_linear_passes_through(self):
        # Perfectly linear history (delta stddev = 0) → any change allowed
        history = [1000.0, 1100.0, 1200.0, 1300.0]
        value, replaced = apply_temporal_filter(history, 5000.0)
        assert not replaced

    def test_custom_sigma(self):
        # Tight sigma catches milder deviations
        history = [1000.0, 1050.0, 980.0, 1020.0, 1010.0]
        value, replaced = apply_temporal_filter(history, 1500.0, sigma=1.0)
        assert replaced

    def test_negative_spike_replaced(self):
        # Drops are also flagged
        history = [2000.0, 2100.0, 1950.0, 2050.0, 2020.0]
        value, replaced = apply_temporal_filter(history, 50.0)
        assert replaced


# =============================================================================
# History cache I/O
# =============================================================================


@pytest.fixture
def isolated_cache(tmp_path, monkeypatch):
    """Point the cache dir at a temp location for the duration of one test.

    ``get_cache_dir`` is an lru_cache'd function that walks up to find .git.
    We monkeypatch it to return ``tmp_path`` and reimport ``validate`` so its
    reference to ``get_cache_dir`` picks up the patch.
    """
    from agriwebb.core import config as core_config

    def _fake_cache_dir() -> Path:
        return tmp_path

    monkeypatch.setattr(core_config, "get_cache_dir", _fake_cache_dir)
    # Also patch the re-export from agriwebb.core (the package __init__)
    import agriwebb.core as core_pkg

    monkeypatch.setattr(core_pkg, "get_cache_dir", _fake_cache_dir)
    yield tmp_path


class TestHistoryCache:
    def test_load_missing_returns_empty(self, isolated_cache):
        assert load_paddock_history("paddock-1") == []

    def test_append_then_load(self, isolated_cache):
        record = {"date": "2026-04-01", "ndvi": 0.5, "sdm": 1500}
        append_paddock_history("paddock-1", record)
        loaded = load_paddock_history("paddock-1")
        assert len(loaded) == 1
        assert loaded[0]["sdm"] == 1500

    def test_append_multiple_in_order(self, isolated_cache):
        for i in range(5):
            append_paddock_history(
                "paddock-1",
                {"date": f"2026-04-0{i + 1}", "ndvi": 0.5, "sdm": 1000 + i * 10},
            )
        loaded = load_paddock_history("paddock-1", limit=10)
        assert len(loaded) == 5
        assert loaded[0]["sdm"] == 1000
        assert loaded[-1]["sdm"] == 1040

    def test_limit_returns_most_recent(self, isolated_cache):
        for i in range(10):
            append_paddock_history(
                "paddock-1",
                {"date": f"2026-04-{i + 1:02d}", "ndvi": 0.5, "sdm": 1000 + i * 10},
            )
        loaded = load_paddock_history("paddock-1", limit=3)
        assert len(loaded) == 3
        assert loaded[0]["sdm"] == 1070
        assert loaded[-1]["sdm"] == 1090

    def test_history_truncated_to_keep_limit(self, isolated_cache):
        # Append more than TEMPORAL_HISTORY_KEEP and ensure truncation
        for i in range(TEMPORAL_HISTORY_KEEP + 10):
            append_paddock_history(
                "paddock-1",
                {"date": f"2026-{(i // 30) + 1:02d}-{(i % 30) + 1:02d}", "sdm": i},
            )
        # Read raw file to verify truncation
        path = get_ndvi_history_dir() / "paddock-1.json"
        with open(path) as f:
            raw = json.load(f)
        assert len(raw) == TEMPORAL_HISTORY_KEEP
        # The last record should still be the most recent
        assert raw[-1]["sdm"] == TEMPORAL_HISTORY_KEEP + 9

    def test_per_paddock_isolation(self, isolated_cache):
        append_paddock_history("paddock-A", {"date": "2026-04-01", "sdm": 1000})
        append_paddock_history("paddock-B", {"date": "2026-04-01", "sdm": 2000})
        a = load_paddock_history("paddock-A")
        b = load_paddock_history("paddock-B")
        assert a[0]["sdm"] == 1000
        assert b[0]["sdm"] == 2000

    def test_corrupt_file_returns_empty(self, isolated_cache):
        path = get_ndvi_history_dir() / "paddock-x.json"
        path.write_text("{ this is not valid json")
        assert load_paddock_history("paddock-x") == []


# =============================================================================
# Integration: full gate scenario
# =============================================================================


class TestGateIntegration:
    """End-to-end scenarios mirroring real-world conditions."""

    def test_opalco_dec_2024_caught(self):
        """The actual Opalco field garbage reading should be rejected."""
        result = validate_ndvi_observation(
            ndvi_mean=-0.027,  # Negative
            ndvi_stddev=4.298,  # Massive
            cloud_free_pct=15.0,
            pixel_count=50,
        )
        assert not result.valid

    def test_clean_spring_observation_survives(self):
        """A normal spring observation passes all gates."""
        # Layer 1: clean
        layer1 = validate_ndvi_observation(
            ndvi_mean=0.62,
            ndvi_stddev=0.07,
            cloud_free_pct=88,
            pixel_count=3500,
        )
        assert layer1.valid

        # Layer 2: 30 kg/ha/day for 14 days = 420 kg gain. Spring max 80 → fine.
        layer2 = validate_growth_delta(
            sdm_curr=2200,
            sdm_prev=1780,
            days=14,
            weather_max_growth_kg_ha_day=80,
        )
        assert layer2.valid

        # Layer 3: history shows consistent ~90 kg/window growth, next step ~100
        history = [1500.0, 1590.0, 1685.0, 1775.0]
        value, replaced = apply_temporal_filter(history, 1870.0)
        # 1870 continues the trend → passes
        assert not replaced
