"""Tests for the historical gate backtest."""

from agriwebb.pasture.backtest import backtest_paddock


class TestBacktestPaddock:
    def test_empty_history_returns_empty(self):
        assert backtest_paddock("id-1", "Test", []) == []

    def test_opalco_dec_2024_caught_on_stddev(self):
        """The actual Opalco incident: negative NDVI, absurd stddev."""
        history = [
            {
                "date": "2024-12-01",
                "year": 2024,
                "month": 12,
                "ndvi_mean": -0.02743,
                "ndvi_stddev": 4.298,
                "pixel_count": 139,
                "cloud_free_pct": 189.6,
            }
        ]
        results = backtest_paddock("904f77a6", "Opalco", history)
        assert len(results) == 1
        assert results[0]["verdict"] == "rejected_l1"
        assert "stddev" in results[0]["reason"]

    def test_clean_observation_passes(self):
        history = [
            {
                "date": "2024-04-01",
                "year": 2024,
                "month": 4,
                "ndvi_mean": 0.55,
                "ndvi_stddev": 0.08,
                "pixel_count": 500,
                "cloud_free_pct": 85.0,
            }
        ]
        results = backtest_paddock("p1", "Alpha", history)
        assert len(results) == 1
        assert results[0]["verdict"] == "passed"
        assert "sdm" in results[0]

    def test_month_filter(self):
        history = [
            {
                "date": "2024-03-01",
                "year": 2024,
                "month": 3,
                "ndvi_mean": 0.5,
                "ndvi_stddev": 0.05,
                "pixel_count": 100,
                "cloud_free_pct": 80,
            },
            {
                "date": "2024-12-01",
                "year": 2024,
                "month": 12,
                "ndvi_mean": 0.3,
                "ndvi_stddev": 0.05,
                "pixel_count": 100,
                "cloud_free_pct": 80,
            },
        ]
        results = backtest_paddock("p1", "Alpha", history, months_filter={12, 1})
        assert len(results) == 1
        assert results[0]["month"] == 12

    def test_skips_null_no_data_entries(self):
        """Entries with no NDVI and zero pixels are skipped silently."""
        history = [
            {
                "date": "2024-12-01",
                "year": 2024,
                "month": 12,
                "ndvi_mean": None,
                "ndvi_stddev": None,
                "pixel_count": 0,
                "cloud_free_pct": 0.0,
            }
        ]
        results = backtest_paddock("p1", "Alpha", history)
        assert results == []

    def test_growth_delta_rejection(self):
        """Two consecutive observations with a too-big jump are L2 rejected."""
        # Both observations pass L1 (clean NDVI, good stats)
        history = [
            {
                "date": "2024-01-01",
                "year": 2024,
                "month": 1,
                "ndvi_mean": 0.15,  # Low winter baseline
                "ndvi_stddev": 0.04,
                "pixel_count": 200,
                "cloud_free_pct": 80,
            },
            {
                "date": "2024-02-01",
                "year": 2024,
                "month": 2,
                "ndvi_mean": 0.95,  # Implausible jump in one month of winter
                "ndvi_stddev": 0.04,
                "pixel_count": 200,
                "cloud_free_pct": 80,
            },
        ]
        results = backtest_paddock("p1", "Alpha", history)
        assert len(results) == 2
        assert results[0]["verdict"] == "passed"
        assert results[1]["verdict"] == "rejected_l2"
        assert "impossible" in results[1]["reason"]
