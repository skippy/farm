"""Fetch daily weather data from NOAA/NCEI and log to AgriWebb."""

import json
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

import httpx

from agriwebb.config import settings
from agriwebb.core import get_cache_dir
from agriwebb.weather import api as weather_api

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


def log_weather(weather: dict, agriwebb_response: dict) -> Path:
    """Append weather data to the local log file."""
    get_cache_dir().mkdir(parents=True, exist_ok=True)
    log_file = get_cache_dir() / "weather_log.jsonl"

    log_entry = {
        **weather,
        "agriwebb_response": agriwebb_response,
        "logged_at": datetime.now(UTC).isoformat(),
    }

    with log_file.open("a") as f:
        f.write(json.dumps(log_entry) + "\n")

    return log_file


async def main() -> None:
    """Fetch yesterday's weather and push to AgriWebb."""
    yesterday = date.today() - timedelta(days=1)

    print(f"Fetching weather for {yesterday} from NCEI station {settings.ncei_station_id}...")
    weather = await fetch_ncei_precipitation(yesterday)

    if not weather:
        print(f"No weather data available for {yesterday}")
        return

    print(f"Date: {weather['date']}")
    print(f"Precipitation: {weather['precipitation_inches']} inches")
    print(f"Temp: {weather['temp_min_f']}°F - {weather['temp_max_f']}°F")

    print("\nPushing to AgriWebb...")
    response = await weather_api.add_rainfall(weather["date"], weather["precipitation_inches"])

    if "errors" in response:
        print(f"AgriWebb error: {response['errors']}")
    else:
        print("Successfully pushed to AgriWebb")

    log_path = log_weather(weather, response)
    print(f"Logged to: {log_path}")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
