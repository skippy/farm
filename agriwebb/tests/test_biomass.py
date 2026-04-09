"""Characterization tests for NDVI-to-biomass conversion and grazing correction.

Captures current behavior of the biomass module before any refactoring.
Tests cover ndvi_to_standing_dry_matter(), calculate_grazing_correction(),
calculate_growth_rate(), adjust_foo_for_grazing(), and seasonal calibration.
"""

import math

import pytest

from agriwebb.pasture.biomass import (
    ANNUAL_MODEL,
    EXPECTED_UNCERTAINTY,
    GRAZING_BASE_CORRECTION,
    GRAZING_DECAY_RATE,
    GRAZING_MIN_CORRECTION,
    SEASONAL_MODELS,
    CalibrationModel,
    Season,
    adjust_foo_for_grazing,
    calculate_grazing_correction,
    calculate_growth_rate,
    get_season,
    ndvi_to_standing_dry_matter,
)

# =============================================================================
# Season and calibration model basics
# =============================================================================


class TestSeason:
    """Tests for the Season enum and get_season() function."""

    def test_season_values(self):
        assert Season.WINTER.value == "winter"
        assert Season.SPRING.value == "spring"
        assert Season.SUMMER.value == "summer"
        assert Season.FALL.value == "fall"

    @pytest.mark.parametrize(
        "month,expected",
        [
            (1, Season.WINTER),
            (2, Season.WINTER),
            (3, Season.SPRING),
            (4, Season.SPRING),
            (5, Season.SPRING),
            (6, Season.SUMMER),
            (7, Season.SUMMER),
            (8, Season.SUMMER),
            (9, Season.FALL),
            (10, Season.FALL),
            (11, Season.FALL),
            (12, Season.WINTER),
        ],
    )
    def test_get_season_all_months(self, month, expected):
        assert get_season(month) == expected


class TestSeasonalModels:
    """Verify seasonal model parameters are configured as expected."""

    def test_all_four_seasons_have_models(self):
        assert set(SEASONAL_MODELS.keys()) == {Season.WINTER, Season.SPRING, Season.SUMMER, Season.FALL}

    def test_spring_has_highest_max_sdm(self):
        """Spring allows the highest biomass ceiling (active growth)."""
        spring_max = SEASONAL_MODELS[Season.SPRING].max_sdm
        for season, model in SEASONAL_MODELS.items():
            if season != Season.SPRING:
                assert model.max_sdm <= spring_max

    def test_winter_has_lowest_max_sdm(self):
        """Winter has the lowest biomass ceiling (dormancy)."""
        winter_max = SEASONAL_MODELS[Season.WINTER].max_sdm
        for season, model in SEASONAL_MODELS.items():
            if season != Season.WINTER:
                assert model.max_sdm >= winter_max

    def test_all_models_have_positive_parameters(self):
        for season, model in SEASONAL_MODELS.items():
            assert model.scale > 0, f"{season}: scale must be positive"
            assert model.coef > 0, f"{season}: coef must be positive"
            assert model.offset >= 0, f"{season}: offset must be non-negative"
            assert model.min_ndvi > 0, f"{season}: min_ndvi must be positive"
            assert model.max_sdm > 0, f"{season}: max_sdm must be positive"


# =============================================================================
# ndvi_to_standing_dry_matter()
# =============================================================================


class TestNdviToStandingDryMatter:
    """Tests for the main NDVI-to-SDM conversion function."""

    def test_zero_ndvi_returns_zero(self):
        """NDVI of 0 is below all min_ndvi thresholds, returns 0."""
        sdm, model = ndvi_to_standing_dry_matter(0.0, month=4)
        assert sdm == 0.0

    def test_negative_ndvi_returns_zero(self):
        """Negative NDVI (water/bare soil) returns 0."""
        sdm, model = ndvi_to_standing_dry_matter(-0.1, month=6)
        assert sdm == 0.0

    def test_ndvi_below_min_threshold_returns_zero(self):
        """NDVI just below the seasonal min_ndvi returns 0."""
        spring_model = SEASONAL_MODELS[Season.SPRING]
        ndvi_just_below = spring_model.min_ndvi - 0.01
        sdm, model = ndvi_to_standing_dry_matter(ndvi_just_below, month=4)
        assert sdm == 0.0

    def test_ndvi_at_min_threshold_returns_nonzero(self):
        """NDVI at exactly the seasonal min_ndvi returns a positive value."""
        spring_model = SEASONAL_MODELS[Season.SPRING]
        sdm, model = ndvi_to_standing_dry_matter(spring_model.min_ndvi, month=4)
        assert sdm > 0

    def test_ndvi_1_is_capped_at_max_sdm(self):
        """Very high NDVI (1.0) is capped at the model's max_sdm."""
        sdm, model = ndvi_to_standing_dry_matter(1.0, month=4)
        assert sdm == model.max_sdm

    def test_sdm_increases_with_ndvi(self):
        """Higher NDVI should yield higher SDM (monotonic increase)."""
        ndvi_values = [0.2, 0.3, 0.4, 0.5, 0.6, 0.7]
        sdm_values = [ndvi_to_standing_dry_matter(n, month=4)[0] for n in ndvi_values]
        for i in range(1, len(sdm_values)):
            assert sdm_values[i] >= sdm_values[i - 1], (
                f"SDM should increase: NDVI {ndvi_values[i]} gave {sdm_values[i]} "
                f"but NDVI {ndvi_values[i-1]} gave {sdm_values[i-1]}"
            )

    def test_sdm_is_rounded(self):
        """SDM values are rounded to whole numbers."""
        sdm, _ = ndvi_to_standing_dry_matter(0.5, month=6)
        assert sdm == round(sdm, 0)

    def test_uses_annual_model_when_no_month(self):
        """When month is None, the annual model is used."""
        sdm, model = ndvi_to_standing_dry_matter(0.5)
        assert model is ANNUAL_MODEL

    def test_uses_seasonal_model_when_month_given(self):
        """When month is provided, the correct seasonal model is used."""
        _, model = ndvi_to_standing_dry_matter(0.5, month=1)
        assert model is SEASONAL_MODELS[Season.WINTER]

        _, model = ndvi_to_standing_dry_matter(0.5, month=4)
        assert model is SEASONAL_MODELS[Season.SPRING]

        _, model = ndvi_to_standing_dry_matter(0.5, month=7)
        assert model is SEASONAL_MODELS[Season.SUMMER]

        _, model = ndvi_to_standing_dry_matter(0.5, month=10)
        assert model is SEASONAL_MODELS[Season.FALL]

    def test_custom_model_override(self):
        """A custom model can be provided to override seasonal selection."""
        custom = CalibrationModel(
            name="Custom",
            scale=1000,
            coef=2.0,
            offset=50,
            min_ndvi=0.05,
            max_sdm=5000,
            source="test",
        )
        sdm, model = ndvi_to_standing_dry_matter(0.5, model=custom)
        assert model is custom
        expected = min(1000 * math.exp(2.0 * 0.5) + 50, 5000)
        assert sdm == round(expected, 0)

    def test_winter_vs_spring_same_ndvi(self):
        """Same NDVI produces different SDM in winter vs spring (seasonal calibration)."""
        ndvi = 0.4
        sdm_winter, _ = ndvi_to_standing_dry_matter(ndvi, month=1)
        sdm_spring, _ = ndvi_to_standing_dry_matter(ndvi, month=4)
        # These should differ because the models have different parameters
        assert sdm_winter != sdm_spring

    def test_summer_model_has_higher_offset(self):
        """Summer model has higher offset (dry matter remains even with low NDVI)."""
        summer = SEASONAL_MODELS[Season.SUMMER]
        spring = SEASONAL_MODELS[Season.SPRING]
        assert summer.offset > spring.offset

    def test_known_sdm_values_spring(self):
        """Characterize specific known SDM outputs for spring."""
        # Capture current values for regression detection
        sdm, model = ndvi_to_standing_dry_matter(0.695, month=4)  # Peak April NDVI
        assert model.name == "Spring (peak growth)"
        # Exponential: 600 * exp(4.0 * 0.695) + 100 = 600 * 16.28 + 100 = 9867
        # But capped at max_sdm=4500
        assert sdm == 4500.0

    def test_known_sdm_values_winter(self):
        """Characterize specific known SDM outputs for winter."""
        sdm, model = ndvi_to_standing_dry_matter(0.364, month=1)
        assert model.name == "Winter (dormant)"
        # 800 * exp(3.0 * 0.364) + 200 = 800 * 2.985 + 200 = 2588
        expected = min(800 * math.exp(3.0 * 0.364) + 200, 2500)
        assert sdm == round(expected, 0)

    def test_known_sdm_values_summer_low_ndvi(self):
        """Characterize summer with low NDVI (dormant/dry)."""
        sdm, model = ndvi_to_standing_dry_matter(0.122, month=8)
        assert model.name == "Summer (dry/senescent)"
        # 1200 * exp(2.5 * 0.122) + 300 = 1200 * 1.357 + 300 = 1928
        expected = min(1200 * math.exp(2.5 * 0.122) + 300, 3000)
        assert sdm == round(expected, 0)


# =============================================================================
# calculate_growth_rate()
# =============================================================================


class TestCalculateGrowthRate:
    """Tests for NDVI-based growth rate calculation."""

    def test_positive_ndvi_change_gives_positive_growth(self):
        """Increasing NDVI means positive growth."""
        rate, notes = calculate_growth_rate(
            ndvi_current=0.6,
            ndvi_previous=0.4,
            days_between=30,
            month_current=5,
            month_previous=4,
        )
        assert rate > 0
        assert "SDM:" in notes

    def test_negative_ndvi_change_gives_negative_growth(self):
        """Decreasing NDVI means biomass loss (senescence/grazing)."""
        rate, notes = calculate_growth_rate(
            ndvi_current=0.3,
            ndvi_previous=0.5,
            days_between=30,
            month_current=7,
            month_previous=6,
        )
        assert rate < 0

    def test_same_ndvi_zero_growth(self):
        """Same NDVI in same season gives zero growth."""
        rate, notes = calculate_growth_rate(
            ndvi_current=0.5,
            ndvi_previous=0.5,
            days_between=30,
            month_current=4,
            month_previous=4,
        )
        assert rate == 0.0

    def test_zero_days_raises_error(self):
        """days_between=0 raises ValueError."""
        with pytest.raises(ValueError, match="days_between must be positive"):
            calculate_growth_rate(0.5, 0.4, days_between=0)

    def test_negative_days_raises_error(self):
        """Negative days_between raises ValueError."""
        with pytest.raises(ValueError, match="days_between must be positive"):
            calculate_growth_rate(0.5, 0.4, days_between=-5)

    def test_negative_ndvi_capped_to_zero(self):
        """Negative NDVI inputs are capped to 0 before conversion."""
        rate, notes = calculate_growth_rate(
            ndvi_current=0.3,
            ndvi_previous=-0.1,
            days_between=30,
            month_current=4,
            month_previous=3,
        )
        # Previous NDVI capped to 0 -> SDM should be 0
        # Current NDVI 0.3 in spring -> positive SDM
        assert rate > 0

    def test_growth_rate_is_rounded(self):
        """Growth rate is rounded to 1 decimal."""
        rate, _ = calculate_growth_rate(0.5, 0.3, days_between=15, month_current=4, month_previous=3)
        assert rate == round(rate, 1)

    def test_notes_contain_model_names(self):
        """Notes string describes the models used."""
        _, notes = calculate_growth_rate(
            ndvi_current=0.5,
            ndvi_previous=0.4,
            days_between=30,
            month_current=4,
            month_previous=1,
        )
        assert "Spring" in notes
        assert "Winter" in notes

    def test_without_months_uses_annual_model(self):
        """When months are not provided, the annual model is used."""
        _, notes = calculate_growth_rate(0.5, 0.4, days_between=30)
        assert "Annual" in notes


# =============================================================================
# calculate_grazing_correction()
# =============================================================================


class TestCalculateGrazingCorrection:
    """Tests for grazing pressure correction factor."""

    def test_rested_paddock(self):
        """Zero grazing pressure gives the base correction (0.85)."""
        correction = calculate_grazing_correction(0)
        assert correction == GRAZING_BASE_CORRECTION

    def test_moderate_grazing(self):
        """Moderate grazing (50 kg/ha/day) gives a reduced correction."""
        correction = calculate_grazing_correction(50)
        assert correction < GRAZING_BASE_CORRECTION
        assert correction > GRAZING_MIN_CORRECTION
        # Verify against formula: 0.85 * exp(-0.004 * 50) = 0.85 * 0.8187 = 0.696
        expected = round(GRAZING_BASE_CORRECTION * math.exp(-GRAZING_DECAY_RATE * 50), 2)
        assert correction == expected

    def test_heavy_grazing(self):
        """Heavy grazing (94 kg/ha/day) gives ~0.48 correction."""
        correction = calculate_grazing_correction(94)
        expected = round(GRAZING_BASE_CORRECTION * math.exp(-GRAZING_DECAY_RATE * 94), 2)
        assert correction == expected

    def test_very_heavy_grazing_floors_at_minimum(self):
        """Very heavy grazing floors at GRAZING_MIN_CORRECTION."""
        correction = calculate_grazing_correction(500)
        assert correction == GRAZING_MIN_CORRECTION

    def test_correction_decreases_with_pressure(self):
        """Higher grazing pressure gives lower correction (monotonic decrease)."""
        pressures = [0, 20, 50, 80, 100, 150]
        corrections = [calculate_grazing_correction(p) for p in pressures]
        for i in range(1, len(corrections)):
            assert corrections[i] <= corrections[i - 1]

    def test_correction_is_rounded_to_2_decimals(self):
        """Correction factor is rounded to 2 decimal places."""
        correction = calculate_grazing_correction(37)
        assert correction == round(correction, 2)

    def test_recovery_with_days_since_rest(self):
        """Paddock rested for days should recover toward base correction."""
        # After 21 days of rest (with 0 pressure), should be near base
        correction_rested = calculate_grazing_correction(0, days_since_rest=21)
        # With 0 pressure and no rest info, correction is already at base
        correction_base = calculate_grazing_correction(0)
        # Both should be GRAZING_BASE_CORRECTION since pressure is 0
        assert correction_rested == correction_base

    def test_recovery_only_applies_when_pressure_is_zero(self):
        """Days since rest recovery only applies when current pressure is 0."""
        # If still grazing (pressure > 0), days_since_rest should not matter
        correction_with_rest = calculate_grazing_correction(50, days_since_rest=30)
        correction_without_rest = calculate_grazing_correction(50)
        assert correction_with_rest == correction_without_rest

    def test_bounded_between_min_and_base(self):
        """Correction is always between GRAZING_MIN_CORRECTION and GRAZING_BASE_CORRECTION."""
        for pressure in range(0, 300, 10):
            correction = calculate_grazing_correction(pressure)
            assert GRAZING_MIN_CORRECTION <= correction <= GRAZING_BASE_CORRECTION


# =============================================================================
# adjust_foo_for_grazing()
# =============================================================================


class TestAdjustFooForGrazing:
    """Tests for FOO adjustment with grazing pressure."""

    def test_rested_paddock_uses_base_correction(self):
        """A rested paddock applies the 0.85 base correction."""
        adjusted, correction = adjust_foo_for_grazing(1000, 0)
        assert correction == GRAZING_BASE_CORRECTION
        assert adjusted == round(1000 * GRAZING_BASE_CORRECTION, 0)

    def test_returns_tuple_of_float_and_correction(self):
        """Returns (adjusted_foo, correction_factor)."""
        result = adjust_foo_for_grazing(1000, 50)
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_heavy_grazing_example(self):
        """Characterize the Hay Field example from the docstring."""
        adjusted, correction = adjust_foo_for_grazing(1366, 94)
        assert correction == calculate_grazing_correction(94)
        assert adjusted == round(1366 * correction, 0)

    def test_adjusted_foo_is_rounded(self):
        """Adjusted FOO is rounded to whole number."""
        adjusted, _ = adjust_foo_for_grazing(1234.5, 25)
        assert adjusted == round(adjusted, 0)

    def test_zero_foo_stays_zero(self):
        """Zero FOO input stays zero regardless of correction."""
        adjusted, correction = adjust_foo_for_grazing(0, 50)
        assert adjusted == 0.0

    def test_days_since_rest_passed_through(self):
        """days_since_rest parameter is forwarded to calculate_grazing_correction."""
        # With 0 pressure, days_since_rest doesn't change the result
        adjusted1, corr1 = adjust_foo_for_grazing(1000, 0, days_since_rest=None)
        adjusted2, corr2 = adjust_foo_for_grazing(1000, 0, days_since_rest=30)
        assert corr1 == corr2  # Both are 0.85 since pressure is 0


# =============================================================================
# Edge cases and integration
# =============================================================================


class TestEdgeCases:
    """Edge cases and integration scenarios."""

    def test_ndvi_exactly_one(self):
        """NDVI = 1.0 should cap at max_sdm for each season."""
        for month in [1, 4, 7, 10]:
            sdm, model = ndvi_to_standing_dry_matter(1.0, month=month)
            assert sdm == model.max_sdm

    def test_ndvi_just_above_zero(self):
        """Very small positive NDVI may be below min_ndvi threshold."""
        sdm, model = ndvi_to_standing_dry_matter(0.01, month=4)
        assert sdm == 0.0  # Below spring min_ndvi of 0.15

    def test_full_pipeline_ndvi_to_adjusted_foo(self):
        """End-to-end: NDVI -> SDM -> FOO -> grazing-adjusted FOO."""
        ndvi = 0.5
        month = 4  # Spring

        sdm, model = ndvi_to_standing_dry_matter(ndvi, month=month)
        assert sdm > 0

        # FOO is typically 75% of SDM (utilization factor from sync/feed.py)
        foo_raw = sdm * 0.75

        # Apply grazing correction for moderate grazing
        adjusted, correction = adjust_foo_for_grazing(foo_raw, grazing_pressure_kg_ha_day=40)
        assert adjusted < foo_raw
        assert adjusted > 0

    def test_uncertainty_constants_documented(self):
        """Expected uncertainty values are documented."""
        assert EXPECTED_UNCERTAINTY["sdm_error_kg_ha"] == 260
        assert EXPECTED_UNCERTAINTY["sdm_error_percent"] == 10
        assert EXPECTED_UNCERTAINTY["growth_rate_error_kg_ha_day"] == 15

    def test_seasonal_sdm_ordering_for_moderate_ndvi(self):
        """For moderate NDVI, characterize seasonal SDM differences."""
        ndvi = 0.4
        sdm_by_season = {}
        months_for_season = {
            "winter": 1,
            "spring": 4,
            "summer": 7,
            "fall": 10,
        }
        for season_name, month in months_for_season.items():
            sdm, _ = ndvi_to_standing_dry_matter(ndvi, month=month)
            sdm_by_season[season_name] = sdm

        # All should be positive for NDVI=0.4
        for season_name, sdm in sdm_by_season.items():
            assert sdm > 0, f"NDVI=0.4 in {season_name} should give positive SDM"

    def test_growth_rate_cross_season_boundary(self):
        """Growth rate across a season boundary uses appropriate models."""
        # Feb (winter) -> Mar (spring)
        rate, notes = calculate_growth_rate(
            ndvi_current=0.45,
            ndvi_previous=0.35,
            days_between=28,
            month_current=3,
            month_previous=2,
        )
        assert "Winter" in notes
        assert "Spring" in notes
        # Growth should be positive (NDVI increased)
        assert rate > 0
