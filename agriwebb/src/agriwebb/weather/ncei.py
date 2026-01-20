"""Fetch daily weather data from NOAA/NCEI and Open-Meteo.

This module handles data fetching and local caching only.
AgriWebb sync logic is in weather/cli.py.

Uses two data sources:
- Open-Meteo: Near-real-time data (no delay), model-interpolated
- NOAA/NCEI: Station data (5-6 day delay), more accurate

Strategy: Use Open-Meteo for recent days, overwrite with NOAA when available.
"""

import json
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

import httpx

from agriwebb.core import get_cache_dir, settings
from agriwebb.weather import openmeteo

if TYPE_CHECKING:
    from pathlib import Path

NCEI_API_URL = "https://www.ncei.noaa.gov/access/services/data/v1"


async def fetch_ncei_precipitation(target_date: date) -> dict | None:
    """Fetch precipitation data from NOAA/NCEI for a specific date."""
    params = {
        "dataset": "daily-summaries",
        "stations": settings.ncei_station_id,
        "dataTypes": "PRCP,TMAX,TMIN",
        "startDate": target_date.isoformat(),
        "endDate": target_date.isoformat(),
        "format": "json",
        "units": "standard",
    }

    async with httpx.AsyncClient() as http:
        response = await http.get(NCEI_API_URL, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()

        if not data:
            return None

        record = data[0]
        return {
            "date": record.get("DATE"),
            "station": record.get("STATION"),
            "precipitation_inches": float(record.get("PRCP", 0) or 0),
            "temp_max_f": float(record.get("TMAX")) if record.get("TMAX") else None,
            "temp_min_f": float(record.get("TMIN")) if record.get("TMIN") else None,
        }


async def fetch_ncei_date_range(start_date: date, end_date: date) -> list[dict]:
    """Fetch precipitation data from NOAA/NCEI for a date range."""
    params = {
        "dataset": "daily-summaries",
        "stations": settings.ncei_station_id,
        "dataTypes": "PRCP,TMAX,TMIN",
        "startDate": start_date.isoformat(),
        "endDate": end_date.isoformat(),
        "format": "json",
        "units": "standard",
    }

    async with httpx.AsyncClient() as http:
        response = await http.get(NCEI_API_URL, params=params, timeout=60)
        response.raise_for_status()
        data = response.json()

        if not data:
            return []

        results = []
        for record in data:
            results.append(
                {
                    "date": record.get("DATE"),
                    "station": record.get("STATION"),
                    "precipitation_inches": float(record.get("PRCP", 0) or 0),
                    "temp_max_f": float(record.get("TMAX")) if record.get("TMAX") else None,
                    "temp_min_f": float(record.get("TMIN")) if record.get("TMIN") else None,
                }
            )
        return results


async def fetch_openmeteo_precipitation(
    start_date: date,
    end_date: date,
) -> list[dict]:
    """
    Fetch precipitation data from Open-Meteo for a date range.

    Open-Meteo provides near-real-time data with no delay, but is model-interpolated
    rather than from a specific weather station.

    Args:
        start_date: Start date
        end_date: End date (inclusive)

    Returns:
        List of weather records with precipitation in inches
    """
    # Use forecast API for recent/current data
    today = date.today()
    days_back = (today - start_date).days
    days_forward = max(0, (end_date - today).days)

    data = await openmeteo.fetch_forecast(
        days=days_forward + 1,
        include_past_days=min(days_back, 92),  # API limit
    )

    results = []
    for record in data:
        record_date = record["date"]
        if start_date.isoformat() <= record_date <= end_date.isoformat():
            # Convert mm to inches
            precip_mm = record.get("precip_mm", 0) or 0
            precip_inches = precip_mm / 25.4

            results.append(
                {
                    "date": record_date,
                    "source": "open-meteo",
                    "precipitation_inches": round(precip_inches, 2),
                    "precipitation_mm": precip_mm,
                    "temp_max_f": round(record.get("temp_max_c", 0) * 9 / 5 + 32, 1)
                    if record.get("temp_max_c")
                    else None,
                    "temp_min_f": round(record.get("temp_min_c", 0) * 9 / 5 + 32, 1)
                    if record.get("temp_min_c")
                    else None,
                }
            )

    return results


async def fetch_combined_precipitation(
    start_date: date,
    end_date: date,
) -> list[dict]:
    """
    Fetch precipitation from both NOAA and Open-Meteo, preferring NOAA where available.

    Strategy:
    - Get data from both sources
    - Use NOAA data where available (more accurate station data)
    - Fall back to Open-Meteo for recent days where NOAA has no data yet

    Args:
        start_date: Start date
        end_date: End date

    Returns:
        List of weather records, with source indicated
    """
    # Fetch from both sources
    noaa_data = await fetch_ncei_date_range(start_date, end_date)
    openmeteo_data = await fetch_openmeteo_precipitation(start_date, end_date)

    # Index NOAA data by date
    noaa_by_date = {r["date"]: r for r in noaa_data}

    # Index Open-Meteo data by date
    openmeteo_by_date = {r["date"]: r for r in openmeteo_data}

    # Combine: prefer NOAA, fall back to Open-Meteo
    results = []
    current = start_date
    while current <= end_date:
        date_str = current.isoformat()

        if date_str in noaa_by_date:
            record = noaa_by_date[date_str]
            record["source"] = "noaa"
            results.append(record)
        elif date_str in openmeteo_by_date:
            results.append(openmeteo_by_date[date_str])
        # else: no data from either source for this date

        current += timedelta(days=1)

    return results


def log_weather(weather: dict, agriwebb_response: dict | None = None) -> Path:
    """Append weather data to the local log file."""
    get_cache_dir().mkdir(exist_ok=True)
    log_file = get_cache_dir() / "weather_log.jsonl"

    log_entry = {
        **weather,
        "agriwebb_response": agriwebb_response,
        "logged_at": datetime.now(UTC).isoformat(),
    }

    with log_file.open("a") as f:
        f.write(json.dumps(log_entry) + "\n")

    return log_file


def save_weather_json(weather_data: list[dict], filename: str = "weather_history.json") -> Path:
    """Save all weather data to a comprehensive JSON file."""
    get_cache_dir().mkdir(exist_ok=True)
    json_file = get_cache_dir() / filename

    output = {
        "station_id": settings.ncei_station_id,
        "generated_at": datetime.now(UTC).isoformat(),
        "record_count": len(weather_data),
        "records": weather_data,
    }

    with json_file.open("w") as f:
        json.dump(output, f, indent=2)

    return json_file
