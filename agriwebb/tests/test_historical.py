"""Tests for historical growth analysis."""

import pytest

from agriwebb.data.historical import (
    calculate_historical_growth,
    compare_to_historical,
    get_monthly_averages,
    get_seasonal_summary,
    get_trend_analysis,
    get_yearly_by_month,
)


class TestCalculateHistoricalGrowth:
    """Tests for historical growth calculation."""

    @pytest.fixture
    def sample_weather_data(self):
        """Sample weather data for testing."""
        return [
            {"date": "2024-01-01", "temp_mean_c": 5, "precip_mm": 10, "et0_mm": 1},
            {"date": "2024-01-02", "temp_mean_c": 6, "precip_mm": 5, "et0_mm": 1},
            {"date": "2024-01-03", "temp_mean_c": 7, "precip_mm": 0, "et0_mm": 2},
            {"date": "2024-04-01", "temp_mean_c": 12, "precip_mm": 8, "et0_mm": 3},
            {"date": "2024-04-02", "temp_mean_c": 14, "precip_mm": 2, "et0_mm": 3},
            {"date": "2024-07-01", "temp_mean_c": 22, "precip_mm": 0, "et0_mm": 6},
            {"date": "2024-07-02", "temp_mean_c": 24, "precip_mm": 0, "et0_mm": 7},
        ]

    def test_returns_growth_by_date(self, sample_weather_data):
        """Returns dict mapping dates to growth rates."""
        result = calculate_historical_growth(sample_weather_data)
        assert isinstance(result, dict)
        assert "2024-01-01" in result
        assert "2024-04-01" in result

    def test_growth_rates_are_floats(self, sample_weather_data):
        """Growth rates are numeric."""
        result = calculate_historical_growth(sample_weather_data)
        for _date_str, growth in result.items():
            assert isinstance(growth, (int, float))

    def test_spring_higher_than_winter(self, sample_weather_data):
        """Spring growth should be higher than winter."""
        result = calculate_historical_growth(sample_weather_data)
        winter_growth = result["2024-01-02"]
        spring_growth = result["2024-04-02"]
        assert spring_growth > winter_growth


class TestGetMonthlyAverages:
    """Tests for monthly average calculation."""

    @pytest.fixture
    def multi_year_weather(self):
        """Weather data spanning multiple years."""
        data = []
        # Generate 3 years of data (simplified)
        for year in [2022, 2023, 2024]:
            for month in range(1, 13):
                for day in [1, 15]:
                    # Seasonal temperature pattern
                    if month in (12, 1, 2):
                        temp = 5
                    elif month in (3, 4, 5):
                        temp = 12
                    elif month in (6, 7, 8):
                        temp = 20
                    else:
                        temp = 10

                    data.append(
                        {
                            "date": f"{year}-{month:02d}-{day:02d}",
                            "temp_mean_c": temp,
                            "precip_mm": 5 if month in (10, 11, 12, 1, 2, 3) else 0,
                            "et0_mm": 2 if month in (12, 1, 2) else 4,
                        }
                    )
        return data

    def test_returns_all_months(self, multi_year_weather):
        """Returns data for all 12 months."""
        result = get_monthly_averages(multi_year_weather)
        for month in range(1, 13):
            assert month in result

    def test_includes_expected_fields(self, multi_year_weather):
        """Each month has expected fields."""
        result = get_monthly_averages(multi_year_weather)
        jan = result[1]
        assert "month" in jan
        assert "month_name" in jan
        assert "years_of_data" in jan
        assert "avg_growth_kg_ha_day" in jan
        assert "min_growth_kg_ha_day" in jan
        assert "max_growth_kg_ha_day" in jan
        assert "std_dev" in jan

    def test_month_names_correct(self, multi_year_weather):
        """Month names are correct."""
        result = get_monthly_averages(multi_year_weather)
        assert result[1]["month_name"] == "January"
        assert result[6]["month_name"] == "June"
        assert result[12]["month_name"] == "December"

    def test_years_of_data_correct(self, multi_year_weather):
        """Years of data count is correct."""
        result = get_monthly_averages(multi_year_weather)
        assert result[1]["years_of_data"] == 3


class TestGetYearlyByMonth:
    """Tests for year-month breakdown."""

    @pytest.fixture
    def multi_year_weather(self):
        """Simple multi-year weather data."""
        return [
            {"date": "2023-01-15", "temp_mean_c": 5, "precip_mm": 10, "et0_mm": 1},
            {"date": "2023-04-15", "temp_mean_c": 12, "precip_mm": 5, "et0_mm": 3},
            {"date": "2024-01-15", "temp_mean_c": 6, "precip_mm": 8, "et0_mm": 1},
            {"date": "2024-04-15", "temp_mean_c": 14, "precip_mm": 4, "et0_mm": 3},
        ]

    def test_returns_tuples_as_keys(self, multi_year_weather):
        """Keys are (year, month) tuples."""
        result = get_yearly_by_month(multi_year_weather)
        assert (2023, 1) in result
        assert (2024, 4) in result

    def test_returns_averages(self, multi_year_weather):
        """Values are average growth rates."""
        result = get_yearly_by_month(multi_year_weather)
        for _key, value in result.items():
            assert isinstance(value, float)


class TestCompareToHistorical:
    """Tests for historical comparison."""

    @pytest.fixture
    def monthly_averages(self):
        """Sample monthly averages."""
        return {
            1: {
                "month": 1,
                "month_name": "January",
                "years_of_data": 5,
                "avg_growth_kg_ha_day": 5.0,
                "min_growth_kg_ha_day": 2.0,
                "max_growth_kg_ha_day": 8.0,
                "std_dev": 2.0,
            },
            4: {
                "month": 4,
                "month_name": "April",
                "years_of_data": 5,
                "avg_growth_kg_ha_day": 50.0,
                "min_growth_kg_ha_day": 35.0,
                "max_growth_kg_ha_day": 65.0,
                "std_dev": 10.0,
            },
        }

    def test_returns_comparison_dict(self, monthly_averages):
        """Returns comparison dictionary."""
        result = compare_to_historical(7.0, 1, monthly_averages)
        assert isinstance(result, dict)
        assert "current_growth" in result
        assert "historical_avg" in result
        assert "deviation" in result

    def test_above_average_status(self, monthly_averages):
        """High growth shows above average status."""
        # 9 kg when average is 5 kg (2 std devs above)
        result = compare_to_historical(9.0, 1, monthly_averages)
        assert "above" in result["status"].lower()
        assert result["deviation"] > 0

    def test_below_average_status(self, monthly_averages):
        """Low growth shows below average status."""
        # 1 kg when average is 5 kg (2 std devs below)
        result = compare_to_historical(1.0, 1, monthly_averages)
        assert "below" in result["status"].lower()
        assert result["deviation"] < 0

    def test_normal_status(self, monthly_averages):
        """Average growth shows normal status."""
        result = compare_to_historical(5.0, 1, monthly_averages)
        assert result["status"] == "normal"

    def test_deviation_percentage(self, monthly_averages):
        """Calculates deviation percentage correctly."""
        result = compare_to_historical(7.5, 1, monthly_averages)
        # 7.5 vs 5.0 = 50% above
        assert result["deviation_pct"] == 50.0

    def test_missing_month_error(self, monthly_averages):
        """Returns error for missing month."""
        result = compare_to_historical(10.0, 6, monthly_averages)  # June not in data
        assert "error" in result


class TestGetSeasonalSummary:
    """Tests for seasonal summary."""

    @pytest.fixture
    def full_year_weather(self):
        """Weather data covering all seasons."""
        data = []
        for month in range(1, 13):
            for day in [1, 15]:
                if month in (12, 1, 2):
                    temp = 5
                elif month in (3, 4, 5):
                    temp = 14
                elif month in (6, 7, 8):
                    temp = 22
                else:
                    temp = 12
                data.append(
                    {
                        "date": f"2024-{month:02d}-{day:02d}",
                        "temp_mean_c": temp,
                        "precip_mm": 5,
                        "et0_mm": 3,
                    }
                )
        return data

    def test_returns_all_seasons(self, full_year_weather):
        """Returns data for all four seasons."""
        result = get_seasonal_summary(full_year_weather)
        assert "winter" in result
        assert "spring" in result
        assert "summer" in result
        assert "fall" in result

    def test_includes_growth_rate(self, full_year_weather):
        """Each season has growth rate."""
        result = get_seasonal_summary(full_year_weather)
        for _season, data in result.items():
            assert "avg_growth_kg_ha_day" in data
            assert data["avg_growth_kg_ha_day"] >= 0

    def test_spring_highest_growth(self, full_year_weather):
        """Spring should have highest growth potential."""
        result = get_seasonal_summary(full_year_weather)
        spring = result["spring"]["avg_growth_kg_ha_day"]
        winter = result["winter"]["avg_growth_kg_ha_day"]
        assert spring > winter


class TestGetTrendAnalysis:
    """Tests for year-over-year trend analysis."""

    @pytest.fixture
    def multi_year_full_data(self):
        """Complete multi-year data for trend analysis (need 300+ days/year)."""
        data = []
        for year in range(2020, 2025):
            for month in range(1, 13):
                # Generate ~30 days per month to exceed 300/year threshold
                for day in range(1, 29):
                    temp = 10 + (year - 2020)  # Slight warming trend
                    data.append(
                        {
                            "date": f"{year}-{month:02d}-{day:02d}",
                            "temp_mean_c": temp,
                            "precip_mm": 5,
                            "et0_mm": 3,
                        }
                    )
        return data

    def test_returns_yearly_averages(self, multi_year_full_data):
        """Returns yearly average growth rates."""
        result = get_trend_analysis(multi_year_full_data)
        assert "yearly_averages" in result
        assert len(result["yearly_averages"]) > 0

    def test_returns_trend_direction(self, multi_year_full_data):
        """Returns trend direction."""
        result = get_trend_analysis(multi_year_full_data)
        assert "trend" in result
        assert result["trend"] in ["increasing", "decreasing", "stable", "insufficient data"]

    def test_returns_slope(self, multi_year_full_data):
        """Returns trend slope."""
        result = get_trend_analysis(multi_year_full_data)
        assert "trend_slope_per_year" in result
        assert isinstance(result["trend_slope_per_year"], (int, float))

    def test_requires_sufficient_data(self):
        """Returns 'insufficient data' with less than 3 years."""
        short_data = [{"date": "2024-01-15", "temp_mean_c": 10, "precip_mm": 5, "et0_mm": 2} for _ in range(100)]
        result = get_trend_analysis(short_data)
        assert result["trend"] == "insufficient data"


class TestMonthlyStatsTypedDict:
    """Tests for MonthlyStats structure."""

    def test_monthly_stats_fields(self):
        """Verify expected fields in MonthlyStats."""
        # This is more of a documentation test
        from agriwebb.data.historical import MonthlyStats

        # Create a valid MonthlyStats
        stats: MonthlyStats = {
            "month": 1,
            "month_name": "January",
            "years_of_data": 5,
            "avg_growth_kg_ha_day": 5.0,
            "min_growth_kg_ha_day": 2.0,
            "max_growth_kg_ha_day": 8.0,
            "std_dev": 2.0,
            "avg_temp_c": 5.0,
            "avg_precip_mm": 10.0,
        }

        assert stats["month"] == 1
        assert stats["month_name"] == "January"
