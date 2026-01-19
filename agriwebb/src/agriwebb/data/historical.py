"""
Historical growth analysis and comparison.

Uses 8 years of weather data to calculate:
- Average growth rates by month
- Year-over-year trends
- Comparison of current conditions to historical norms

This helps answer questions like:
- "Is growth lower than normal for January?"
- "How does this year compare to last year?"
- "What growth should we expect in March?"
"""

import json
from collections import defaultdict
from datetime import date
from typing import TypedDict

from agriwebb.core import get_cache_dir
from agriwebb.pasture.growth import (
    SoilWaterState,
    calculate_daily_growth,
    get_season,
)


class MonthlyStats(TypedDict):
    """Monthly growth statistics."""

    month: int
    month_name: str
    years_of_data: int
    avg_growth_kg_ha_day: float
    min_growth_kg_ha_day: float
    max_growth_kg_ha_day: float
    std_dev: float
    avg_temp_c: float
    avg_precip_mm: float


class YearlyComparison(TypedDict):
    """Year-over-year comparison."""

    year: int
    month: int
    avg_growth_kg_ha_day: float
    deviation_from_avg: float
    deviation_pct: float


def load_weather_history() -> list[dict]:
    """Load weather history from cache."""
    cache_path = get_cache_dir() / "weather_historical.json"
    with open(cache_path) as f:
        data = json.load(f)
    return data.get("daily_data", [])


def calculate_historical_growth(
    weather_data: list[dict],
    soil_awc_mm: float = 45.0,  # Default AWC for average soil
) -> dict[str, list[float]]:
    """
    Calculate daily growth rates for all historical weather data.

    Returns dict with date string -> growth rate.
    """
    # Initialize soil water at 50% capacity
    soil_water = SoilWaterState(awc_mm=soil_awc_mm)

    growth_by_date = {}

    for day in sorted(weather_data, key=lambda x: x["date"]):
        d = date.fromisoformat(day["date"])

        result = calculate_daily_growth(
            d=d,
            temp_mean_c=day.get("temp_mean_c", 10),
            precip_mm=day.get("precip_mm", 0),
            et0_mm=day.get("et0_mm", 2),
            soil_water=soil_water,
        )

        growth_by_date[day["date"]] = result["growth_kg_ha_day"]

    return growth_by_date


def get_monthly_averages(weather_data: list[dict]) -> dict[int, MonthlyStats]:
    """
    Calculate average growth by month across all years.

    Returns dict of month (1-12) -> MonthlyStats.
    """
    growth_by_date = calculate_historical_growth(weather_data)

    # Group by month
    monthly_data: dict[int, dict] = defaultdict(
        lambda: {
            "growth_rates": [],
            "temps": [],
            "precip": [],
            "years": set(),
        }
    )

    for day in weather_data:
        d = date.fromisoformat(day["date"])
        month = d.month
        year = d.year

        growth = growth_by_date.get(day["date"], 0)

        monthly_data[month]["growth_rates"].append(growth)
        monthly_data[month]["temps"].append(day.get("temp_mean_c", 0))
        monthly_data[month]["precip"].append(day.get("precip_mm", 0))
        monthly_data[month]["years"].add(year)

    # Calculate statistics
    month_names = [
        "",
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    ]

    results = {}
    for month in range(1, 13):
        data = monthly_data[month]
        rates = data["growth_rates"]

        if not rates:
            continue

        avg = sum(rates) / len(rates)
        min_rate = min(rates)
        max_rate = max(rates)

        # Standard deviation
        variance = sum((r - avg) ** 2 for r in rates) / len(rates)
        std_dev = variance**0.5

        results[month] = MonthlyStats(
            month=month,
            month_name=month_names[month],
            years_of_data=len(data["years"]),
            avg_growth_kg_ha_day=round(avg, 1),
            min_growth_kg_ha_day=round(min_rate, 1),
            max_growth_kg_ha_day=round(max_rate, 1),
            std_dev=round(std_dev, 1),
            avg_temp_c=round(sum(data["temps"]) / len(data["temps"]), 1),
            avg_precip_mm=round(sum(data["precip"]) / len(data["precip"]), 1),
        )

    return results


def get_yearly_by_month(weather_data: list[dict]) -> dict[tuple[int, int], float]:
    """
    Calculate average growth by year and month.

    Returns dict of (year, month) -> avg growth rate.
    """
    growth_by_date = calculate_historical_growth(weather_data)

    # Group by year and month
    year_month_data: dict[tuple[int, int], list[float]] = defaultdict(list)

    for day in weather_data:
        d = date.fromisoformat(day["date"])
        key = (d.year, d.month)
        growth = growth_by_date.get(day["date"], 0)
        year_month_data[key].append(growth)

    # Calculate averages
    return {key: sum(rates) / len(rates) for key, rates in year_month_data.items()}


def compare_to_historical(
    current_growth: float,
    month: int,
    monthly_averages: dict[int, MonthlyStats],
) -> dict:
    """
    Compare current growth rate to historical average for the month.

    Returns comparison dict with deviation info.
    """
    if month not in monthly_averages:
        return {"error": f"No historical data for month {month}"}

    stats = monthly_averages[month]
    historical_avg = stats["avg_growth_kg_ha_day"]
    std_dev = stats["std_dev"]

    deviation = current_growth - historical_avg
    deviation_pct = (deviation / historical_avg * 100) if historical_avg > 0 else 0

    # How many standard deviations from mean?
    z_score = deviation / std_dev if std_dev > 0 else 0

    # Interpretation
    if z_score > 1.5:
        status = "well above average"
    elif z_score > 0.5:
        status = "above average"
    elif z_score > -0.5:
        status = "normal"
    elif z_score > -1.5:
        status = "below average"
    else:
        status = "well below average"

    return {
        "month": month,
        "month_name": stats["month_name"],
        "current_growth": round(current_growth, 1),
        "historical_avg": historical_avg,
        "historical_range": f"{stats['min_growth_kg_ha_day']}-{stats['max_growth_kg_ha_day']}",
        "deviation": round(deviation, 1),
        "deviation_pct": round(deviation_pct, 1),
        "z_score": round(z_score, 2),
        "status": status,
        "years_of_data": stats["years_of_data"],
    }


def get_seasonal_summary(weather_data: list[dict]) -> dict:
    """
    Get growth summary by season.
    """
    growth_by_date = calculate_historical_growth(weather_data)

    seasonal_data = defaultdict(list)

    for day in weather_data:
        d = date.fromisoformat(day["date"])
        season = get_season(d)
        growth = growth_by_date.get(day["date"], 0)
        seasonal_data[season.value].append(growth)

    return {
        season: {
            "avg_growth_kg_ha_day": round(sum(rates) / len(rates), 1),
            "days": len(rates),
        }
        for season, rates in seasonal_data.items()
    }


def get_trend_analysis(weather_data: list[dict]) -> dict:
    """
    Analyze year-over-year trends.

    Returns annual totals and trend direction.
    """
    growth_by_date = calculate_historical_growth(weather_data)

    # Group by year
    yearly_totals = defaultdict(lambda: {"total_growth": 0, "days": 0})

    for day in weather_data:
        d = date.fromisoformat(day["date"])
        year = d.year
        growth = growth_by_date.get(day["date"], 0)

        yearly_totals[year]["total_growth"] += growth
        yearly_totals[year]["days"] += 1

    # Calculate annual averages
    yearly_avgs = {}
    for year, data in yearly_totals.items():
        if data["days"] >= 300:  # Need most of the year
            yearly_avgs[year] = round(data["total_growth"] / data["days"], 1)

    # Calculate trend (simple linear regression)
    years = sorted(yearly_avgs.keys())
    if len(years) >= 3:
        n = len(years)
        sum_x = sum(years)
        sum_y = sum(yearly_avgs[y] for y in years)
        sum_xy = sum(y * yearly_avgs[y] for y in years)
        sum_x2 = sum(y**2 for y in years)

        slope = (n * sum_xy - sum_x * sum_y) / (n * sum_x2 - sum_x**2)
        trend = "increasing" if slope > 0.1 else "decreasing" if slope < -0.1 else "stable"
    else:
        slope = 0
        trend = "insufficient data"

    return {
        "yearly_averages": yearly_avgs,
        "trend": trend,
        "trend_slope_per_year": round(slope, 2),
        "years_analyzed": len(yearly_avgs),
    }


def generate_historical_report() -> dict:
    """
    Generate a complete historical analysis report.
    """
    weather_data = load_weather_history()

    monthly = get_monthly_averages(weather_data)
    seasonal = get_seasonal_summary(weather_data)
    trends = get_trend_analysis(weather_data)

    # Current comparison
    today = date.today()
    current_month = today.month

    return {
        "generated_at": today.isoformat(),
        "data_years": trends["years_analyzed"],
        "monthly_averages": monthly,
        "seasonal_summary": seasonal,
        "trend_analysis": trends,
        "current_month_context": monthly.get(current_month),
    }


# CLI for testing
def main():
    """Print historical analysis."""
    print("=" * 70)
    print("Historical Pasture Growth Analysis")
    print("=" * 70)

    weather_data = load_weather_history()
    print(f"\nAnalyzing {len(weather_data)} days of weather data...")

    monthly = get_monthly_averages(weather_data)
    seasonal = get_seasonal_summary(weather_data)
    trends = get_trend_analysis(weather_data)

    print(f"\n--- Monthly Averages (based on {list(monthly.values())[0]['years_of_data']} years) ---")
    print(f"{'Month':<12} {'Avg Growth':<12} {'Range':<15} {'Avg Temp':<10}")
    print("-" * 55)

    for month in range(1, 13):
        if month in monthly:
            m = monthly[month]
            print(
                f"{m['month_name']:<12} "
                f"{m['avg_growth_kg_ha_day']:>8.1f} kg   "
                f"{m['min_growth_kg_ha_day']:.0f}-{m['max_growth_kg_ha_day']:.0f} kg       "
                f"{m['avg_temp_c']:>5.1f}°C"
            )

    print("\n--- Seasonal Summary ---")
    for season, data in seasonal.items():
        print(f"  {season.capitalize():<10}: {data['avg_growth_kg_ha_day']:.1f} kg DM/ha/day")

    print("\n--- Year-over-Year Trend ---")
    print(f"  Trend: {trends['trend']}")
    print(f"  Slope: {trends['trend_slope_per_year']:+.2f} kg/ha/day per year")

    print("\n  Yearly averages:")
    for year, avg in sorted(trends["yearly_averages"].items()):
        print(f"    {year}: {avg:.1f} kg DM/ha/day")

    # Current month comparison
    today = date.today()
    current_month = today.month

    if current_month in monthly:
        m = monthly[current_month]
        print(f"\n--- {m['month_name']} Context ---")
        print(f"  Historical average: {m['avg_growth_kg_ha_day']:.1f} kg DM/ha/day")
        print(f"  Historical range: {m['min_growth_kg_ha_day']:.0f}-{m['max_growth_kg_ha_day']:.0f} kg")
        print(f"  Std deviation: ±{m['std_dev']:.1f} kg")


if __name__ == "__main__":
    main()
