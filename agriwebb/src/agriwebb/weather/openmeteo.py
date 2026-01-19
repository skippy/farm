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

import httpx

from agriwebb.core import get_cache_dir

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

    async with httpx.AsyncClient() as client:
        response = await client.get(HISTORICAL_API, params=params, timeout=60)
        response.raise_for_status()
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
        days: Number of forecast days (max 16)
        lat: Latitude
        lon: Longitude
        include_past_days: Include this many past days (max 92)

    Returns:
        List of daily weather records (past + forecast)
    """
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

    async with httpx.AsyncClient() as client:
        response = await client.get(FORECAST_API, params=params, timeout=30)
        response.raise_for_status()
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

    async with httpx.AsyncClient() as client:
        response = await client.get(FORECAST_API, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()

    current = data.get("current", {})
    return {
        "time": current.get("time"),
        "temperature_c": current.get("temperature_2m"),
        "precipitation_mm": current.get("precipitation"),
        "rain_mm": current.get("rain"),
        "weather_code": current.get("weather_code"),
    }


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
        print("Fetching recent days and forecast...")
        recent_forecast = await fetch_forecast(
            days=7,
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
            days=7,
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


# CLI interface
async def main():
    """CLI to update weather cache and show summary."""
    import argparse

    parser = argparse.ArgumentParser(description="Open-Meteo weather data management")
    parser.add_argument("--update", action="store_true", help="Update weather cache")
    parser.add_argument("--current", action="store_true", help="Show current conditions")
    parser.add_argument("--forecast", action="store_true", help="Show 7-day forecast")
    parser.add_argument("--recent", type=int, default=7, help="Show N recent days")
    args = parser.parse_args()

    if args.current:
        print("Current conditions:")
        current = await fetch_current_conditions()
        print(f"  Temperature: {current['temperature_c']}°C")
        print(f"  Precipitation: {current['precipitation_mm']} mm")
        print(f"  Time: {current['time']}")
        return

    if args.update:
        print("Updating weather cache...")
        data = await update_weather_cache()
        print(f"Cache updated: {data['daily_records']} days")
        print(f"Date range: {data['daily_data'][0]['date']} to {data['daily_data'][-1]['date']}")

    if args.forecast:
        print("\n7-day forecast:")
        forecast = await fetch_forecast(days=7, include_past_days=0)
        print(f"{'Date':<12} {'Temp':<10} {'Precip':<10} {'ET0':<8}")
        print("-" * 42)
        for day in forecast:
            print(
                f"{day['date']:<12} {day['temp_min_c']:.0f}-{day['temp_max_c']:.0f}°C    "
                f"{day['precip_mm']:.1f} mm    {day['et0_mm']:.1f} mm"
            )
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
