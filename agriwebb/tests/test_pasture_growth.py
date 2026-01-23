"""Tests for pasture growth model."""

from datetime import date

import pytest

from agriwebb.pasture.growth import (
    MOISTURE_OPTIMAL,
    MOISTURE_STRESS_POINT,
    MOISTURE_WATERLOG,
    MOISTURE_WILTING_POINT,
    SEASONAL_MAX_GROWTH,
    # Constants
    TEMP_BASE,
    TEMP_MAX,
    TEMP_OPT_HIGH,
    TEMP_OPT_LOW,
    PaddockGrowthModel,
    # Classes
    Season,
    SoilWaterState,
    calculate_daily_growth,
    # Functions
    get_season,
    moisture_factor,
    soil_quality_factor,
    summarize_growth,
    temperature_factor,
)


class TestGetSeason:
    """Tests for season determination."""

    def test_winter_months(self):
        assert get_season(date(2024, 12, 15)) == Season.WINTER
        assert get_season(date(2024, 1, 15)) == Season.WINTER
        assert get_season(date(2024, 2, 15)) == Season.WINTER

    def test_spring_months(self):
        assert get_season(date(2024, 3, 15)) == Season.SPRING
        assert get_season(date(2024, 4, 15)) == Season.SPRING
        assert get_season(date(2024, 5, 15)) == Season.SPRING

    def test_summer_months(self):
        assert get_season(date(2024, 6, 15)) == Season.SUMMER
        assert get_season(date(2024, 7, 15)) == Season.SUMMER
        assert get_season(date(2024, 8, 15)) == Season.SUMMER

    def test_fall_months(self):
        assert get_season(date(2024, 9, 15)) == Season.FALL
        assert get_season(date(2024, 10, 15)) == Season.FALL
        assert get_season(date(2024, 11, 15)) == Season.FALL


class TestTemperatureFactor:
    """Tests for temperature response function."""

    def test_below_base_temperature(self):
        """No growth below base temperature."""
        assert temperature_factor(TEMP_BASE - 1) == 0.0
        assert temperature_factor(0) == 0.0
        assert temperature_factor(-5) == 0.0

    def test_at_base_temperature(self):
        """Zero growth at base temperature."""
        assert temperature_factor(TEMP_BASE) == 0.0

    def test_between_base_and_optimal(self):
        """Linear increase from base to optimal."""
        mid_temp = (TEMP_BASE + TEMP_OPT_LOW) / 2
        factor = temperature_factor(mid_temp)
        assert 0 < factor < 1
        # Should be approximately 0.5
        assert 0.4 < factor < 0.6

    def test_optimal_range(self):
        """Full growth in optimal range."""
        assert temperature_factor(TEMP_OPT_LOW) == 1.0
        assert temperature_factor(TEMP_OPT_HIGH) == 1.0
        assert temperature_factor((TEMP_OPT_LOW + TEMP_OPT_HIGH) / 2) == 1.0

    def test_between_optimal_and_max(self):
        """Linear decrease from optimal to max."""
        mid_temp = (TEMP_OPT_HIGH + TEMP_MAX) / 2
        factor = temperature_factor(mid_temp)
        assert 0 < factor < 1
        # Should be approximately 0.5
        assert 0.4 < factor < 0.6

    def test_above_max_temperature(self):
        """No growth above max temperature."""
        assert temperature_factor(TEMP_MAX) == 0.0
        assert temperature_factor(TEMP_MAX + 5) == 0.0
        assert temperature_factor(40) == 0.0

    def test_factor_is_bounded(self):
        """Factor should always be between 0 and 1."""
        for temp in range(-10, 50):
            factor = temperature_factor(temp)
            assert 0 <= factor <= 1


class TestMoistureFactor:
    """Tests for soil moisture response function."""

    def test_below_wilting_point(self):
        """No growth below wilting point."""
        assert moisture_factor(0) == 0.0
        assert moisture_factor(MOISTURE_WILTING_POINT - 0.01) == 0.0

    def test_at_wilting_point(self):
        """Zero growth at wilting point."""
        assert moisture_factor(MOISTURE_WILTING_POINT) == 0.0

    def test_stressed_range(self):
        """Reduced growth when stressed (max 0.5)."""
        mid = (MOISTURE_WILTING_POINT + MOISTURE_STRESS_POINT) / 2
        factor = moisture_factor(mid)
        assert 0 < factor <= 0.5

    def test_suboptimal_range(self):
        """Growth between 0.5 and 1.0 in suboptimal range."""
        mid = (MOISTURE_STRESS_POINT + MOISTURE_OPTIMAL) / 2
        factor = moisture_factor(mid)
        assert 0.5 < factor < 1.0

    def test_optimal_range(self):
        """Full growth in optimal range."""
        assert moisture_factor(MOISTURE_OPTIMAL) == 1.0
        mid = (MOISTURE_OPTIMAL + MOISTURE_WATERLOG) / 2
        assert moisture_factor(mid) == 1.0

    def test_waterlogged(self):
        """Reduced growth when waterlogged."""
        factor = moisture_factor(MOISTURE_WATERLOG + 0.1)
        assert factor < 1.0
        assert factor >= 0.3  # Minimum is 0.3

    def test_severely_waterlogged(self):
        """Heavily reduced but not zero when severely waterlogged."""
        factor = moisture_factor(1.5)  # 150% of AWC
        assert factor == 0.3  # Minimum


class TestSoilQualityFactor:
    """Tests for soil quality adjustments."""

    def test_no_adjustments(self):
        """Default factor is 1.0."""
        assert soil_quality_factor() == 1.0

    def test_drainage_adjustments(self):
        """Drainage class affects growth."""
        assert soil_quality_factor(drainage="Well drained") == 1.0
        assert soil_quality_factor(drainage="Poorly drained") < 1.0
        assert soil_quality_factor(drainage="Excessively drained") < 1.0

    def test_organic_matter_bonus(self):
        """High organic matter boosts growth."""
        # Below 3% - no bonus
        assert soil_quality_factor(organic_matter_pct=2.0) == 1.0
        # At 3% - no bonus
        assert soil_quality_factor(organic_matter_pct=3.0) == 1.0
        # Above 3% - bonus
        assert soil_quality_factor(organic_matter_pct=5.0) > 1.0

    def test_organic_matter_cap(self):
        """Organic matter bonus is capped at 15%."""
        # Very high OM shouldn't give more than 15% boost
        factor = soil_quality_factor(organic_matter_pct=20.0)
        assert factor <= 1.15

    def test_combined_factors(self):
        """Drainage and OM combine multiplicatively."""
        drainage_only = soil_quality_factor(drainage="Poorly drained")
        om_only = soil_quality_factor(organic_matter_pct=5.0)
        combined = soil_quality_factor(drainage="Poorly drained", organic_matter_pct=5.0)
        assert abs(combined - drainage_only * om_only) < 0.01


class TestSoilWaterState:
    """Tests for soil water balance tracking."""

    def test_initialization(self):
        """Soil water initializes at 50% capacity."""
        state = SoilWaterState(awc_mm=100)
        assert state.current_mm == 50
        assert state.fraction == 0.5

    def test_from_soil_data(self):
        """Can create from soil data dict."""
        soil = {"awc_cm_cm": 0.15}
        state = SoilWaterState.from_soil_data(soil, root_depth_mm=300)
        assert state.awc_mm == 45  # 0.15 * 300

    def test_precipitation_adds_water(self):
        """Precipitation increases soil water."""
        state = SoilWaterState(awc_mm=100, current_mm=50)
        state.update(precip_mm=20, et0_mm=0)
        assert state.current_mm > 50

    def test_et_removes_water(self):
        """ET decreases soil water."""
        state = SoilWaterState(awc_mm=100, current_mm=70)
        state.update(precip_mm=0, et0_mm=5)
        assert state.current_mm < 70

    def test_water_capped_at_awc(self):
        """Water can't exceed AWC (excess drains)."""
        state = SoilWaterState(awc_mm=100, current_mm=90)
        state.update(precip_mm=50, et0_mm=0)
        assert state.current_mm == 100  # Capped at AWC

    def test_water_cant_go_negative(self):
        """Water can't go below zero."""
        state = SoilWaterState(awc_mm=100, current_mm=5)
        state.update(precip_mm=0, et0_mm=20)
        assert state.current_mm >= 0

    def test_et_reduced_when_stressed(self):
        """Actual ET is reduced when soil is dry."""
        # Well-watered - full ET
        state1 = SoilWaterState(awc_mm=100, current_mm=70)
        et1 = state1.update(precip_mm=0, et0_mm=5)

        # Stressed - reduced ET
        state2 = SoilWaterState(awc_mm=100, current_mm=25)
        et2 = state2.update(precip_mm=0, et0_mm=5)

        assert et2 < et1


class TestCalculateDailyGrowth:
    """Tests for daily growth calculation."""

    @pytest.fixture
    def soil_water(self):
        """Provide soil water state at optimal moisture."""
        state = SoilWaterState(awc_mm=50)
        state.current_mm = 35  # ~70% = optimal
        return state

    def test_returns_expected_fields(self, soil_water):
        """Result contains all expected fields."""
        result = calculate_daily_growth(
            d=date(2024, 4, 15),
            temp_mean_c=15,
            precip_mm=5,
            et0_mm=3,
            soil_water=soil_water,
        )
        assert "date" in result
        assert "growth_kg_ha_day" in result
        assert "temp_factor" in result
        assert "moisture_factor" in result
        assert "soil_factor" in result
        assert "season" in result

    def test_spring_has_highest_potential(self, soil_water):
        """Spring has highest maximum potential."""
        result = calculate_daily_growth(
            d=date(2024, 4, 15),  # Spring
            temp_mean_c=15,
            precip_mm=5,
            et0_mm=3,
            soil_water=soil_water,
        )
        assert result["max_potential"] == SEASONAL_MAX_GROWTH["spring"]

    def test_cold_limits_growth(self, soil_water):
        """Cold temperature limits growth."""
        result = calculate_daily_growth(
            d=date(2024, 4, 15),
            temp_mean_c=2,  # Below base
            precip_mm=5,
            et0_mm=3,
            soil_water=soil_water,
        )
        assert result["growth_kg_ha_day"] == 0
        assert result["temp_factor"] == 0
        assert "temp limited" in result["notes"]

    def test_drought_limits_growth(self):
        """Drought limits growth."""
        dry_soil = SoilWaterState(awc_mm=50, current_mm=5)  # Very dry
        result = calculate_daily_growth(
            d=date(2024, 4, 15),
            temp_mean_c=15,
            precip_mm=0,
            et0_mm=5,
            soil_water=dry_soil,
        )
        assert result["growth_kg_ha_day"] < 10
        assert "drought stress" in result["notes"]

    def test_optimal_conditions_high_growth(self):
        """Optimal conditions produce high growth."""
        optimal_soil = SoilWaterState(awc_mm=50, current_mm=35)
        result = calculate_daily_growth(
            d=date(2024, 4, 15),  # Spring
            temp_mean_c=15,  # Optimal temp
            precip_mm=5,
            et0_mm=3,
            soil_water=optimal_soil,
        )
        # Should be close to max potential for spring (80)
        assert result["growth_kg_ha_day"] > 50
        assert result["notes"] == "normal"

    def test_growth_is_rounded(self, soil_water):
        """Growth value is rounded to 1 decimal."""
        result = calculate_daily_growth(
            d=date(2024, 4, 15),
            temp_mean_c=15,
            precip_mm=5,
            et0_mm=3,
            soil_water=soil_water,
        )
        # Check it's a clean decimal
        assert result["growth_kg_ha_day"] == round(result["growth_kg_ha_day"], 1)


class TestPaddockGrowthModel:
    """Tests for paddock-level growth model."""

    def test_from_paddock_data(self):
        """Can create model from paddock data."""
        paddock = {"id": "test-id", "name": "Test Paddock", "totalArea": 5.5}
        soil = {
            "paddock_id": "test-id",
            "soil": {
                "awc_cm_cm": 0.15,
                "drainage": "Well drained",
                "organic_matter_pct": 4.5,
            },
        }
        model = PaddockGrowthModel.from_paddock_data(paddock, soil)
        assert model.paddock_name == "Test Paddock"
        assert model.area_ha == 5.5
        assert model.drainage == "Well drained"
        assert model.organic_matter_pct == 4.5

    def test_calculate_growth(self):
        """Model can calculate daily growth."""
        model = PaddockGrowthModel(
            paddock_id="test",
            paddock_name="Test",
            area_ha=5.0,
            soil_water=SoilWaterState(awc_mm=50),
            drainage="Well drained",
        )
        result = model.calculate_growth(
            d=date(2024, 4, 15),
            temp_mean_c=15,
            precip_mm=5,
            et0_mm=3,
        )
        assert result["growth_kg_ha_day"] > 0


class TestSummarizeGrowth:
    """Tests for growth summary function."""

    def test_empty_results(self):
        """Empty results produce empty summary."""
        assert summarize_growth({}) == {}

    def test_summary_fields(self):
        """Summary contains expected fields."""
        results = {
            "Test Paddock": [
                {"date": "2024-04-01", "growth_kg_ha_day": 50},
                {"date": "2024-04-02", "growth_kg_ha_day": 60},
                {"date": "2024-04-03", "growth_kg_ha_day": 55},
            ]
        }
        summary = summarize_growth(results)
        assert "Test Paddock" in summary
        s = summary["Test Paddock"]
        assert s["days"] == 3
        assert s["total_growth_kg_ha"] == 165
        assert s["avg_growth_kg_ha_day"] == 55.0
        assert s["min_growth"] == 50
        assert s["max_growth"] == 60

    def test_handles_empty_paddock(self):
        """Paddocks with no results are skipped."""
        results = {
            "Full Paddock": [{"date": "2024-04-01", "growth_kg_ha_day": 50}],
            "Empty Paddock": [],
        }
        summary = summarize_growth(results)
        assert "Full Paddock" in summary
        assert "Empty Paddock" not in summary


class TestSeasonalGrowthPatterns:
    """Integration tests for seasonal growth patterns."""

    def test_spring_growth_highest(self):
        """Spring produces highest growth rates."""
        spring = calculate_daily_growth(
            d=date(2024, 4, 15),
            temp_mean_c=15,
            precip_mm=5,
            et0_mm=3,
            soil_water=SoilWaterState(awc_mm=50, current_mm=35),
        )

        winter = calculate_daily_growth(
            d=date(2024, 1, 15),
            temp_mean_c=8,
            precip_mm=10,
            et0_mm=1,
            soil_water=SoilWaterState(awc_mm=50, current_mm=45),
        )

        assert spring["growth_kg_ha_day"] > winter["growth_kg_ha_day"]

    def test_summer_dry_low_growth(self):
        """Dry summer conditions limit growth."""
        # Simulate mid-summer dry conditions
        dry_soil = SoilWaterState(awc_mm=50, current_mm=10)

        summer = calculate_daily_growth(
            d=date(2024, 7, 15),
            temp_mean_c=22,
            precip_mm=0,
            et0_mm=6,
            soil_water=dry_soil,
        )

        # Growth should be very limited
        assert summer["growth_kg_ha_day"] < 10


# =============================================================================
# Tests for Growth Rate Sync Filtering (CLI helper functions)
# =============================================================================


class TestFilterChangedGrowthRecords:
    """Tests for the filter_changed_growth_records function."""

    def test_skips_unchanged_records(self):
        """Records with same value in AgriWebb should be skipped."""
        from agriwebb.pasture.cli import filter_changed_growth_records

        records = [
            {"field_id": "f1", "field_name": "Paddock A", "growth_rate": 25.0, "record_date": "2026-01-15"},
        ]
        # Same value exists
        existing_by_key = {("f1", "2026-01-15"): 25.0}

        result = filter_changed_growth_records(records, existing_by_key)

        assert len(result["records_to_push"]) == 0
        assert result["skipped_count"] == 1
        assert records[0]["status"] == "unchanged"

    def test_includes_new_records(self):
        """Records not in AgriWebb should be included."""
        from agriwebb.pasture.cli import filter_changed_growth_records

        records = [
            {"field_id": "f1", "field_name": "Paddock A", "growth_rate": 25.0, "record_date": "2026-01-15"},
        ]
        existing_by_key = {}  # No existing records

        result = filter_changed_growth_records(records, existing_by_key)

        assert len(result["records_to_push"]) == 1
        assert result["skipped_count"] == 0
        assert records[0]["status"] == "new"

    def test_includes_changed_records(self):
        """Records with different values should be included."""
        from agriwebb.pasture.cli import filter_changed_growth_records

        records = [
            {"field_id": "f1", "field_name": "Paddock A", "growth_rate": 30.0, "record_date": "2026-01-15"},
        ]
        # Existing has different value
        existing_by_key = {("f1", "2026-01-15"): 25.0}

        result = filter_changed_growth_records(records, existing_by_key)

        assert len(result["records_to_push"]) == 1
        assert result["skipped_count"] == 0
        assert "update" in records[0]["status"]
        assert "25.0" in records[0]["status"]
        assert "30.0" in records[0]["status"]

    def test_force_includes_all_records(self):
        """With force=True, all records should be included."""
        from agriwebb.pasture.cli import filter_changed_growth_records

        records = [
            {"field_id": "f1", "field_name": "Paddock A", "growth_rate": 25.0, "record_date": "2026-01-15"},
            {"field_id": "f2", "field_name": "Paddock B", "growth_rate": 30.0, "record_date": "2026-01-15"},
        ]
        # Both have same values in AgriWebb
        existing_by_key = {
            ("f1", "2026-01-15"): 25.0,
            ("f2", "2026-01-15"): 30.0,
        }

        result = filter_changed_growth_records(records, existing_by_key, force=True)

        assert len(result["records_to_push"]) == 2
        assert result["skipped_count"] == 0
        assert records[0]["status"] == "force"
        assert records[1]["status"] == "force"

    def test_tolerance_within_threshold(self):
        """Values within 1.0 kg tolerance should be considered equal."""
        from agriwebb.pasture.cli import filter_changed_growth_records

        records = [
            {"field_id": "f1", "field_name": "Paddock A", "growth_rate": 25.0, "record_date": "2026-01-15"},
        ]
        # Existing is 25.8 (within 1.0 tolerance of 25.0)
        existing_by_key = {("f1", "2026-01-15"): 25.8}

        result = filter_changed_growth_records(records, existing_by_key)

        assert len(result["records_to_push"]) == 0
        assert result["skipped_count"] == 1

    def test_tolerance_outside_threshold(self):
        """Values outside 1.0 kg tolerance should be considered different."""
        from agriwebb.pasture.cli import filter_changed_growth_records

        records = [
            {"field_id": "f1", "field_name": "Paddock A", "growth_rate": 25.0, "record_date": "2026-01-15"},
        ]
        # Existing is 26.5 (outside 1.0 tolerance of 25.0)
        existing_by_key = {("f1", "2026-01-15"): 26.5}

        result = filter_changed_growth_records(records, existing_by_key)

        assert len(result["records_to_push"]) == 1
        assert result["skipped_count"] == 0

    def test_mixed_records(self):
        """Mix of new, unchanged, and changed records."""
        from agriwebb.pasture.cli import filter_changed_growth_records

        records = [
            {"field_id": "f1", "field_name": "Paddock A", "growth_rate": 20.0, "record_date": "2026-01-15"},
            {"field_id": "f2", "field_name": "Paddock B", "growth_rate": 25.0, "record_date": "2026-01-15"},
            {"field_id": "f3", "field_name": "Paddock C", "growth_rate": 35.0, "record_date": "2026-01-15"},
        ]
        existing_by_key = {
            ("f2", "2026-01-15"): 25.0,  # Unchanged
            ("f3", "2026-01-15"): 30.0,  # Will be updated to 35.0
        }

        result = filter_changed_growth_records(records, existing_by_key)

        assert len(result["records_to_push"]) == 2  # New + Changed
        assert result["skipped_count"] == 1  # Unchanged
        pushed_names = [r["field_name"] for r in result["records_to_push"]]
        assert "Paddock A" in pushed_names  # New
        assert "Paddock C" in pushed_names  # Changed
        assert "Paddock B" not in pushed_names  # Unchanged


class TestGrowthValuesMatch:
    """Tests for the _growth_values_match helper function."""

    def test_exact_match(self):
        """Exact same values should match."""
        from agriwebb.pasture.cli import _growth_values_match

        assert _growth_values_match(25.0, 25.0) is True

    def test_within_default_tolerance(self):
        """Values within 1.0 kg should match."""
        from agriwebb.pasture.cli import _growth_values_match

        assert _growth_values_match(25.0, 25.5) is True
        assert _growth_values_match(25.0, 24.5) is True
        assert _growth_values_match(25.0, 26.0) is True  # Exactly at tolerance

    def test_outside_default_tolerance(self):
        """Values outside 1.0 kg should not match."""
        from agriwebb.pasture.cli import _growth_values_match

        assert _growth_values_match(25.0, 26.5) is False
        assert _growth_values_match(25.0, 23.5) is False

    def test_custom_tolerance(self):
        """Custom tolerance should be respected."""
        from agriwebb.pasture.cli import _growth_values_match

        # With larger tolerance, these should match
        assert _growth_values_match(25.0, 27.0, tolerance=2.0) is True
        # With smaller tolerance, they should not
        assert _growth_values_match(25.0, 25.2, tolerance=0.1) is False


class TestBuildExistingGrowthLookup:
    """Tests for the _build_existing_growth_lookup helper function."""

    def test_builds_composite_key_dict(self):
        """Should convert API records to (field_id, date)->value dict."""
        from agriwebb.pasture.cli import _build_existing_growth_lookup

        # Timestamps are in milliseconds, noon UTC
        growth_records = [
            {"time": 1705320000000, "value": 25.0, "fieldId": "f1"},  # 2024-01-15 12:00 UTC
            {"time": 1705406400000, "value": 30.0, "fieldId": "f2"},  # 2024-01-16 12:00 UTC
        ]

        lookup = _build_existing_growth_lookup(growth_records)

        assert lookup[("f1", "2024-01-15")] == 25.0
        assert lookup[("f2", "2024-01-16")] == 30.0

    def test_empty_list_returns_empty_dict(self):
        """Empty input should return empty dict."""
        from agriwebb.pasture.cli import _build_existing_growth_lookup

        lookup = _build_existing_growth_lookup([])

        assert lookup == {}

    def test_same_field_different_dates(self):
        """Same field on different dates should have separate entries."""
        from agriwebb.pasture.cli import _build_existing_growth_lookup

        growth_records = [
            {"time": 1705320000000, "value": 25.0, "fieldId": "f1"},  # 2024-01-15
            {"time": 1705406400000, "value": 28.0, "fieldId": "f1"},  # 2024-01-16
        ]

        lookup = _build_existing_growth_lookup(growth_records)

        assert lookup[("f1", "2024-01-15")] == 25.0
        assert lookup[("f1", "2024-01-16")] == 28.0
