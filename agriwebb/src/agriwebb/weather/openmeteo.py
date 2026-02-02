"""
Open-Meteo weather API integration.

Provides real-time, historical, and forecast weather data for pasture growth modeling.
Open-Meteo is free, requires no API key, and includes ET₀ (reference evapotranspiration).

API Documentation: https://open-meteo.com/en/docs
"""

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TypedDict

from agriwebb.core import get_cache_dir, http_get_with_retry
from agriwebb.core.units import (
    format_precip,
    format_precip_summary,
    format_temp,
    format_temp_range,
    get_precip_description,
)

# API endpoints
HISTORICAL_API = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_API = "https://api.open-meteo.com/v1/forecast"

# Default location (San Juan Islands, WA)
DEFAULT_LAT = 48.501762
DEFAULT_LON = -123.042906


class DailyWeather(TypedDict):
    """Daily weather record."""

    date: str
    temp_mean_c: float
    temp_max_c: float
    temp_min_c: float
    precip_mm: float
    et0_mm: float


class WeatherData(TypedDict):
    """Complete weather data structure."""

    location: dict
    fetched_at: str
    daily_records: int
    daily_data: list[DailyWeather]


async def fetch_historical(
    start_date: date,
    end_date: date,
    lat: float = DEFAULT_LAT,
    lon: float = DEFAULT_LON,
) -> list[DailyWeather]:
    """
    Fetch historical weather data from Open-Meteo archive.

    Args:
        start_date: Start date
        end_date: End date (inclusive)
        lat: Latitude
        lon: Longitude

    Returns:
        List of daily weather records
    """
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "daily": [
            "temperature_2m_max",
            "temperature_2m_min",
            "temperature_2m_mean",
            "precipitation_sum",
            "et0_fao_evapotranspiration",
        ],
        "timezone": "America/Los_Angeles",
    }

    response = await http_get_with_retry(HISTORICAL_API, params=params, timeout=60)
    data = response.json()

    daily = data.get("daily", {})
    dates = daily.get("time", [])
    temp_max = daily.get("temperature_2m_max", [])
    temp_min = daily.get("temperature_2m_min", [])
    temp_mean = daily.get("temperature_2m_mean", [])
    precip = daily.get("precipitation_sum", [])
    et0 = daily.get("et0_fao_evapotranspiration", [])

    results = []
    for i, d in enumerate(dates):
        results.append(
            DailyWeather(
                date=d,
                temp_mean_c=temp_mean[i] if temp_mean[i] is not None else 0,
                temp_max_c=temp_max[i] if temp_max[i] is not None else 0,
                temp_min_c=temp_min[i] if temp_min[i] is not None else 0,
                precip_mm=precip[i] if precip[i] is not None else 0,
                et0_mm=et0[i] if et0[i] is not None else 0,
            )
        )

    return results


async def fetch_forecast(
    days: int = 7,
    lat: float = DEFAULT_LAT,
    lon: float = DEFAULT_LON,
    include_past_days: int = 7,
) -> list[DailyWeather]:
    """
    Fetch weather forecast and recent past from Open-Meteo.

    Args:
        days: Number of forecast days (max 16 from API, values >16 are capped)
        lat: Latitude
        lon: Longitude
        include_past_days: Include this many past days (max 92)

    Returns:
        List of daily weather records (past + forecast)
    """
    # Open-Meteo free API supports max 16 forecast days
    days = min(days, 16)
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": [
            "temperature_2m_max",
            "temperature_2m_min",
            "precipitation_sum",
            "et0_fao_evapotranspiration",
        ],
        "timezone": "America/Los_Angeles",
        "forecast_days": days,
        "past_days": include_past_days,
    }

    response = await http_get_with_retry(FORECAST_API, params=params, timeout=30)
    data = response.json()

    daily = data.get("daily", {})
    dates = daily.get("time", [])
    temp_max = daily.get("temperature_2m_max", [])
    temp_min = daily.get("temperature_2m_min", [])
    precip = daily.get("precipitation_sum", [])
    et0 = daily.get("et0_fao_evapotranspiration", [])

    results = []
    for i, d in enumerate(dates):
        t_max = temp_max[i] if temp_max[i] is not None else 0
        t_min = temp_min[i] if temp_min[i] is not None else 0
        results.append(
            DailyWeather(
                date=d,
                temp_mean_c=round((t_max + t_min) / 2, 1),
                temp_max_c=t_max,
                temp_min_c=t_min,
                precip_mm=precip[i] if precip[i] is not None else 0,
                et0_mm=et0[i] if et0[i] is not None else 0,
            )
        )

    return results


async def fetch_current_conditions(
    lat: float = DEFAULT_LAT,
    lon: float = DEFAULT_LON,
) -> dict:
    """
    Fetch current weather conditions.

    Returns current temperature, precipitation, etc.
    """
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": [
            "temperature_2m",
            "precipitation",
            "rain",
            "weather_code",
        ],
        "timezone": "America/Los_Angeles",
    }

    response = await http_get_with_retry(FORECAST_API, params=params, timeout=15)
    data = response.json()

    current = data.get("current", {})
    return {
        "time": current.get("time"),
        "temperature_c": current.get("temperature_2m"),
        "precipitation_mm": current.get("precipitation"),
        "rain_mm": current.get("rain"),
        "weather_code": current.get("weather_code"),
    }


def get_climatology_for_dates(
    start_date: date,
    end_date: date,
    weather_data: list[DailyWeather],
) -> list[DailyWeather]:
    """
    Generate climatological weather estimates for a date range.

    Uses historical averages for each day-of-year from the weather cache.
    This provides reasonable estimates for forecasts beyond 16 days.

    Args:
        start_date: First date to generate
        end_date: Last date to generate (inclusive)
        weather_data: Historical weather data to calculate averages from

    Returns:
        List of DailyWeather records with historical averages
    """
    from collections import defaultdict

    # Calculate day-of-year averages from historical data
    doy_data: dict[int, dict] = defaultdict(
        lambda: {"temps": [], "precip": [], "et0": [], "temp_max": [], "temp_min": []}
    )

    for record in weather_data:
        try:
            d = date.fromisoformat(record["date"])
            doy = d.timetuple().tm_yday
            doy_data[doy]["temps"].append(record.get("temp_mean_c", 10))
            doy_data[doy]["temp_max"].append(record.get("temp_max_c", 15))
            doy_data[doy]["temp_min"].append(record.get("temp_min_c", 5))
            doy_data[doy]["precip"].append(record.get("precip_mm", 0))
            doy_data[doy]["et0"].append(record.get("et0_mm", 2))
        except (ValueError, KeyError):
            continue

    # Generate synthetic records for requested dates
    results = []
    current = start_date
    while current <= end_date:
        doy = current.timetuple().tm_yday
        data = doy_data.get(doy)

        if data and data["temps"]:
            avg_temp = sum(data["temps"]) / len(data["temps"])
            avg_temp_max = sum(data["temp_max"]) / len(data["temp_max"])
            avg_temp_min = sum(data["temp_min"]) / len(data["temp_min"])
            avg_precip = sum(data["precip"]) / len(data["precip"])
            avg_et0 = sum(data["et0"]) / len(data["et0"])
        else:
            # Fallback to reasonable defaults
            avg_temp, avg_temp_max, avg_temp_min = 10.0, 15.0, 5.0
            avg_precip, avg_et0 = 2.0, 2.0

        results.append(
            DailyWeather(
                date=current.isoformat(),
                temp_mean_c=round(avg_temp, 1),
                temp_max_c=round(avg_temp_max, 1),
                temp_min_c=round(avg_temp_min, 1),
                precip_mm=round(avg_precip, 1),
                et0_mm=round(avg_et0, 2),
            )
        )
        current += timedelta(days=1)

    return results


def load_cached_weather(cache_path: Path | None = None) -> WeatherData | None:
    """Load cached weather data."""
    if cache_path is None:
        cache_path = get_cache_dir() / "weather_historical.json"

    if not cache_path.exists():
        return None

    with open(cache_path) as f:
        return json.load(f)


def save_weather_cache(data: WeatherData, cache_path: Path | None = None) -> Path:
    """Save weather data to cache."""
    if cache_path is None:
        cache_path = get_cache_dir() / "weather_historical.json"

    cache_path.parent.mkdir(parents=True, exist_ok=True)

    with open(cache_path, "w") as f:
        json.dump(data, f, indent=2)

    return cache_path


async def update_weather_cache(
    lat: float = DEFAULT_LAT,
    lon: float = DEFAULT_LON,
    cache_path: Path | None = None,
    refresh: bool = False,
    forecast_days: int = 7,
) -> WeatherData:
    """
    Update the weather cache with latest data.

    - Loads existing cache (unless refresh=True)
    - Fetches any missing historical days
    - Adds recent days and forecast
    - Saves updated cache

    Args:
        lat: Latitude
        lon: Longitude
        cache_path: Path to cache file
        refresh: If True, ignore existing cache and fetch everything
        forecast_days: Number of forecast days to fetch (default 7, max 16)

    Returns:
        Updated weather data
    """
    if cache_path is None:
        cache_path = get_cache_dir() / "weather_historical.json"

    # Load existing cache (unless refreshing)
    cached = None if refresh else load_cached_weather(cache_path)

    today = date.today()
    yesterday = today - timedelta(days=1)

    if cached:
        # Find the latest date in cache
        existing_dates = {d["date"] for d in cached["daily_data"]}
        latest_cached = max(existing_dates)
        latest_date = date.fromisoformat(latest_cached)

        # Fetch missing historical days (archive API is ~5 days behind)
        archive_end = today - timedelta(days=5)
        if latest_date < archive_end:
            print(f"Fetching historical data from {latest_date + timedelta(days=1)} to {archive_end}...")
            new_historical = await fetch_historical(latest_date + timedelta(days=1), archive_end, lat, lon)
            # Merge new historical data
            for record in new_historical:
                if record["date"] not in existing_dates:
                    cached["daily_data"].append(record)
                    existing_dates.add(record["date"])

        # Fetch recent + forecast (covers gap between archive and today)
        print(f"Fetching recent days and {min(forecast_days, 16)}-day forecast...")
        recent_forecast = await fetch_forecast(
            days=forecast_days,
            lat=lat,
            lon=lon,
            include_past_days=14,  # Overlap to fill any gaps
        )

        # Merge, preferring existing historical data over forecast for past dates
        for record in recent_forecast:
            record_date = date.fromisoformat(record["date"])
            if record["date"] not in existing_dates:
                cached["daily_data"].append(record)
                existing_dates.add(record["date"])
            elif record_date > yesterday:
                # Update forecast days
                for i, existing in enumerate(cached["daily_data"]):
                    if existing["date"] == record["date"]:
                        cached["daily_data"][i] = record
                        break

        # Sort by date
        cached["daily_data"].sort(key=lambda x: x["date"])
        cached["fetched_at"] = datetime.now().isoformat()
        cached["daily_records"] = len(cached["daily_data"])

        save_weather_cache(cached, cache_path)
        return cached

    else:
        # No cache - fetch full history
        print("No cache found. Fetching full historical data (this may take a moment)...")

        # Fetch from 2018 to 5 days ago (archive API limit)
        start = date(2018, 1, 1)
        archive_end = today - timedelta(days=5)

        historical = await fetch_historical(start, archive_end, lat, lon)

        # Fetch recent + forecast
        recent_forecast = await fetch_forecast(
            days=forecast_days,
            lat=lat,
            lon=lon,
            include_past_days=14,
        )

        # Combine
        all_dates = {d["date"] for d in historical}
        for record in recent_forecast:
            if record["date"] not in all_dates:
                historical.append(record)

        historical.sort(key=lambda x: x["date"])

        data: WeatherData = {
            "location": {
                "lat": lat,
                "lon": lon,
                "name": "San Juan Islands, WA",
            },
            "fetched_at": datetime.now().isoformat(),
            "daily_records": len(historical),
            "daily_data": historical,
        }

        save_weather_cache(data, cache_path)
        return data


async def get_weather_for_date(
    target_date: date,
    cache_path: Path | None = None,
) -> DailyWeather | None:
    """
    Get weather for a specific date from cache.

    Updates cache if needed for recent dates.
    """
    cached = load_cached_weather(cache_path)

    if cached:
        date_str = target_date.isoformat()
        for record in cached["daily_data"]:
            if record["date"] == date_str:
                return record

    return None


async def get_weather_range(
    start_date: date,
    end_date: date,
    cache_path: Path | None = None,
) -> list[DailyWeather]:
    """
    Get weather for a date range from cache.

    Returns list of daily records within the range.
    """
    cached = load_cached_weather(cache_path)

    if not cached:
        return []

    start_str = start_date.isoformat()
    end_str = end_date.isoformat()

    return [record for record in cached["daily_data"] if start_str <= record["date"] <= end_str]


def _get_weekly_summary(days: list[DailyWeather]) -> dict:
    """Summarize a week of weather data."""
    if not days:
        return {}

    temps = [d["temp_mean_c"] for d in days]
    temp_highs = [d["temp_max_c"] for d in days]
    temp_lows = [d["temp_min_c"] for d in days]
    precip = [d["precip_mm"] for d in days]

    return {
        "temp_avg_c": sum(temps) / len(temps),
        "temp_high_c": max(temp_highs),
        "temp_low_c": min(temp_lows),
        "precip_total_mm": sum(precip),
        "precip_days": sum(1 for p in precip if p >= 0.1),
        "days": len(days),
    }


async def show_weather_forecast(days: int = 7) -> None:
    """Show weather forecast for specified number of days."""
    today = date.today()

    # Fetch current conditions
    print("=" * 70)
    print("Weather Forecast - San Juan Islands, WA")
    print("=" * 70)

    try:
        current = await fetch_current_conditions()
        temp_display = format_temp(current['temperature_c'])
        print(f"\nCurrent: {temp_display}")
    except Exception:
        print("\nCurrent conditions unavailable")

    # Load historical data for climatology
    cached = load_cached_weather()
    historical_data = cached["daily_data"] if cached else []

    # Determine how much is actual forecast vs climatology
    api_forecast_days = min(days, 16)
    climatology_days = max(0, days - 16)

    # Fetch actual forecast
    print(f"\nFetching {api_forecast_days}-day forecast from Open-Meteo...")
    forecast_data = await fetch_forecast(days=api_forecast_days, include_past_days=0)

    # Add climatology for extended periods
    all_forecast = list(forecast_data)
    if climatology_days > 0 and historical_data:
        climatology_start = today + timedelta(days=api_forecast_days + 1)
        climatology_end = today + timedelta(days=days)
        climatology = get_climatology_for_dates(climatology_start, climatology_end, historical_data)
        all_forecast.extend(climatology)

    # Display based on horizon
    if days <= 14:
        # Show daily detail for shorter forecasts
        _print_daily_forecast(all_forecast, api_forecast_days)
    else:
        # Show weekly summaries for longer forecasts
        _print_weekly_forecast(all_forecast, api_forecast_days, days)

    # Show data source notes
    print("\n" + "-" * 70)
    if climatology_days > 0:
        print("Data sources:")
        print(f"  Days 1-{api_forecast_days}: Open-Meteo weather forecast")
        years_of_data = len({d['date'][:4] for d in historical_data})
        print(f"  Days {api_forecast_days + 1}-{days}: Historical averages ({years_of_data} years of data)")
    else:
        print("Source: Open-Meteo weather forecast")


def _print_daily_forecast(forecast: list[DailyWeather], api_days: int) -> None:
    """Print daily forecast details."""
    print(f"\n{'Date':<12} {'Day':<10} {'High/Low':<14} {'Precip':<10} {'Conditions'}")
    print("-" * 60)

    day_names = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
    today = date.today()

    for i, day in enumerate(forecast):
        d = date.fromisoformat(day["date"])
        day_name = day_names[d.weekday()]

        # Mark today/tomorrow
        if d == today:
            day_name = "TODAY"
        elif d == today + timedelta(days=1):
            day_name = "Tomorrow"

        temp_range = format_temp_range(day['temp_min_c'], day['temp_max_c'])
        precip = format_precip(day['precip_mm'])
        conditions = get_precip_description(day['precip_mm'])

        # Mark climatology days
        marker = "" if i < api_days else " *"

        print(f"{day['date']:<12} {day_name:<10} {temp_range:<14} {precip:<10} {conditions}{marker}")


def _print_weekly_forecast(forecast: list[DailyWeather], api_days: int, total_days: int) -> None:
    """Print weekly summary forecast for longer horizons."""
    # Group into weeks
    weeks = []
    current_week = []
    week_start = None

    for i, day in enumerate(forecast):
        d = date.fromisoformat(day["date"])
        if week_start is None:
            week_start = d

        current_week.append(day)

        # End week on Sunday or at end of data
        if d.weekday() == 6 or i == len(forecast) - 1:
            weeks.append({
                "start": week_start,
                "end": d,
                "days": current_week,
                "is_forecast": i < api_days,
            })
            current_week = []
            week_start = None

    # Print header
    print(f"\n{'Period':<20} {'Avg Temp':<12} {'High/Low':<14} {'Precip':<12} {'Source'}")
    print("-" * 70)

    running_days = 0
    for week in weeks:
        summary = _get_weekly_summary(week["days"])
        running_days += summary["days"]

        # Determine source
        if running_days <= api_days:
            source = "Forecast"
        elif running_days - summary["days"] >= api_days:
            source = "Historical"
        else:
            source = "Mixed"

        period = f"{week['start'].strftime('%b %d')} - {week['end'].strftime('%b %d')}"

        avg_temp = format_temp(summary['temp_avg_c'])
        high_low = format_temp_range(summary['temp_low_c'], summary['temp_high_c'])
        precip = format_precip_summary(summary['precip_total_mm'], summary['precip_days'])

        print(f"{period:<20} {avg_temp:<12} {high_low:<14} {precip:<12} {source}")

    # Print monthly outlook for 90-day forecasts
    if total_days >= 60:
        print("\n" + "=" * 70)
        print("Monthly Outlook (based on historical averages)")
        print("-" * 70)

        # Group by month
        from collections import defaultdict
        monthly: dict[str, list] = defaultdict(list)
        for day in forecast:
            d = date.fromisoformat(day["date"])
            month_key = d.strftime("%B %Y")
            monthly[month_key].append(day)

        print(f"{'Month':<16} {'Avg Temp':<12} {'Typical Range':<14} {'Expected Precip'}")
        print("-" * 60)

        for month, days in monthly.items():
            summary = _get_weekly_summary(days)
            avg_temp = format_temp(summary['temp_avg_c'])
            temp_range = format_temp_range(summary['temp_low_c'], summary['temp_high_c'])
            precip = format_precip(summary['precip_total_mm'], decimals=1)
            print(f"{month:<16} {avg_temp:<12} {temp_range:<14} {precip} over {summary['days']}d")


# CLI interface
async def main():
    """CLI to update weather cache and show summary."""
    import argparse

    parser = argparse.ArgumentParser(description="Open-Meteo weather data management")
    parser.add_argument("--update", action="store_true", help="Update weather cache")
    parser.add_argument("--current", action="store_true", help="Show current conditions")
    parser.add_argument("--forecast", type=int, nargs="?", const=7, metavar="DAYS",
                        help="Show forecast (default: 7 days, options: 7, 14, 30, 90)")
    parser.add_argument("--recent", type=int, default=7, help="Show N recent days")
    args = parser.parse_args()

    if args.current:
        print("Current conditions:")
        current = await fetch_current_conditions()
        temp_f = current['temperature_c'] * 9/5 + 32
        print(f"  Temperature: {current['temperature_c']:.1f}°C ({temp_f:.0f}°F)")
        print(f"  Precipitation: {current['precipitation_mm']} mm")
        print(f"  Time: {current['time']}")
        return

    if args.update:
        print("Updating weather cache...")
        data = await update_weather_cache(forecast_days=16)
        print(f"Cache updated: {data['daily_records']} days")
        print(f"Date range: {data['daily_data'][0]['date']} to {data['daily_data'][-1]['date']}")

    if args.forecast is not None:
        await show_weather_forecast(days=args.forecast)
        return

    # Default: show recent days
    cached = load_cached_weather()
    if cached:
        recent = cached["daily_data"][-args.recent :]
        print(f"\nRecent {len(recent)} days:")
        print(f"{'Date':<12} {'Mean':<8} {'Range':<12} {'Precip':<10} {'ET0':<8}")
        print("-" * 52)
        for day in recent:
            print(
                f"{day['date']:<12} {day['temp_mean_c']:>5.1f}°C  "
                f"{day['temp_min_c']:.0f}-{day['temp_max_c']:.0f}°C    "
                f"{day['precip_mm']:>5.1f} mm   {day['et0_mm']:>5.2f} mm"
            )
    else:
        print("No cached weather data. Run with --update to fetch.")


def cli():
    """Entry point for CLI."""
    import asyncio

    asyncio.run(main())


if __name__ == "__main__":
    cli()
