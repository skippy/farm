"""Fetch daily weather data from NOAA/NCEI and Open-Meteo, sync to AgriWebb.

Uses two data sources:
- Open-Meteo: Near-real-time data (no delay), model-interpolated
- NOAA/NCEI: Station data (5-6 day delay), more accurate

Strategy: Use Open-Meteo for recent days, overwrite with NOAA when available.
"""

import argparse
import asyncio
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx

from agriwebb.core import client, settings, get_cache_dir
from agriwebb.weather import openmeteo

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
            results.append({
                "date": record.get("DATE"),
                "station": record.get("STATION"),
                "precipitation_inches": float(record.get("PRCP", 0) or 0),
                "temp_max_f": float(record.get("TMAX")) if record.get("TMAX") else None,
                "temp_min_f": float(record.get("TMIN")) if record.get("TMIN") else None,
            })
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

            results.append({
                "date": record_date,
                "source": "open-meteo",
                "precipitation_inches": round(precip_inches, 2),
                "precipitation_mm": precip_mm,
                "temp_max_f": round(record.get("temp_max_c", 0) * 9/5 + 32, 1) if record.get("temp_max_c") else None,
                "temp_min_f": round(record.get("temp_min_c", 0) * 9/5 + 32, 1) if record.get("temp_min_c") else None,
            })

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


async def list_rainfalls() -> list[dict]:
    """List all rainfall records for the configured sensor."""
    print("Fetching existing rainfall records...")
    rainfalls = await client.get_rainfalls()

    if not rainfalls:
        print("No rainfall records found.")
        return []

    print(f"Found {len(rainfalls)} rainfall records.")

    # Sort by time and show summary
    sorted_rainfalls = sorted(rainfalls, key=lambda x: x.get("time", 0))
    if sorted_rainfalls:
        first = sorted_rainfalls[0]
        last = sorted_rainfalls[-1]
        first_date = datetime.fromtimestamp(first["time"] / 1000, tz=UTC).date()
        last_date = datetime.fromtimestamp(last["time"] / 1000, tz=UTC).date()
        print(f"Date range: {first_date} to {last_date}")

    # Note about deletion
    print("\nNOTE: AgriWebb API does not support deleting rainfall records.")
    print("To delete records, use the AgriWebb web interface.")

    return rainfalls


async def backfill_weather(
    days: int | None = None,
    months: int | None = None,
    years: int | None = None,
    push_to_agriwebb: bool = True,
) -> None:
    """
    Fetch historical weather data and optionally push to AgriWebb.

    Args:
        days: Number of days of history to fetch
        months: Number of months of history to fetch
        years: Number of years of history to fetch
        push_to_agriwebb: Whether to push records to AgriWebb

    If multiple time units specified, they are added together.
    If none specified, defaults to 2 years.
    """
    end_date = date.today() - timedelta(days=1)

    # Calculate total days to go back
    total_days = 0
    if days:
        total_days += days
    if months:
        total_days += months * 30
    if years:
        total_days += years * 365

    # Default to 2 years if nothing specified
    if total_days == 0:
        total_days = 365 * 2

    start_date = end_date - timedelta(days=total_days)

    print(f"Fetching weather data from {start_date} to {end_date}...")
    print(f"Station: {settings.ncei_station_id}")

    all_weather = await fetch_ncei_date_range(start_date, end_date)
    print(f"Retrieved {len(all_weather)} days of weather data.")

    # Save all weather data to JSON
    json_path = save_weather_json(all_weather)
    print(f"Saved weather history to: {json_path}")

    if not push_to_agriwebb:
        print("Skipping AgriWebb push (--dry-run specified).")
        return

    # Push to AgriWebb and log each record
    print("\nPushing to AgriWebb...")
    success_count = 0
    error_count = 0

    for i, weather in enumerate(all_weather, 1):
        try:
            response = await client.add_rainfall(
                weather["date"],
                weather["precipitation_inches"]
            )

            if "errors" in response:
                error_count += 1
                log_weather(weather, response)
            else:
                success_count += 1
                log_weather(weather, response)

            if i % 50 == 0:
                print(f"  Processed {i}/{len(all_weather)} days...")

        except Exception as e:
            error_count += 1
            log_weather(weather, {"error": str(e)})

    print(f"\nCompleted: {success_count} successful, {error_count} errors")
    print(f"Log file: {get_cache_dir() / 'weather_log.jsonl'}")


async def sync_weather(
    days: int = 14,
    push_to_agriwebb: bool = True,
) -> dict:
    """
    Sync recent rainfall using combined NOAA + Open-Meteo sources.

    Uses NOAA station data where available (preferred, but 5-6 day delay),
    falls back to Open-Meteo for recent days (near-real-time).

    Args:
        days: Number of days to sync (default 14 to cover NOAA lag)
        push_to_agriwebb: Whether to push records to AgriWebb

    Returns:
        Dict with sync results
    """
    end_date = date.today()
    start_date = end_date - timedelta(days=days)

    print(f"Syncing rainfall from {start_date} to {end_date}...")
    print(f"Sources: NOAA station {settings.ncei_station_id} + Open-Meteo")

    # Fetch combined data
    all_weather = await fetch_combined_precipitation(start_date, end_date)

    # Count by source
    noaa_count = sum(1 for w in all_weather if w.get("source") == "noaa")
    openmeteo_count = sum(1 for w in all_weather if w.get("source") == "open-meteo")

    print(f"Retrieved {len(all_weather)} days: {noaa_count} from NOAA, {openmeteo_count} from Open-Meteo")

    # Show data
    print(f"\n{'Date':<12} {'Source':<12} {'Precip':>8}")
    print("-" * 34)
    for w in all_weather:
        print(f"{w['date']:<12} {w.get('source', 'unknown'):<12} {w['precipitation_inches']:>7.2f}\"")

    if not push_to_agriwebb:
        print("\nSkipping AgriWebb push (dry run).")
        return {"days": len(all_weather), "noaa": noaa_count, "openmeteo": openmeteo_count}

    # Push to AgriWebb
    print("\nPushing to AgriWebb...")
    success_count = 0
    error_count = 0

    for weather in all_weather:
        try:
            response = await client.add_rainfall(
                weather["date"],
                weather["precipitation_inches"]
            )

            if "errors" in response:
                error_count += 1
            else:
                success_count += 1

        except Exception as e:
            error_count += 1
            print(f"  Error for {weather['date']}: {e}")

    print(f"Completed: {success_count} successful, {error_count} errors")

    return {
        "days": len(all_weather),
        "noaa": noaa_count,
        "openmeteo": openmeteo_count,
        "success": success_count,
        "errors": error_count,
    }


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
    response = await client.add_rainfall(weather["date"], weather["precipitation_inches"])

    if "errors" in response:
        print(f"AgriWebb error: {response['errors']}")
    else:
        print("Successfully pushed to AgriWebb")

    log_path = log_weather(weather, response)
    print(f"Logged to: {log_path}")


async def cli_main() -> None:
    """CLI entry point with argument parsing."""
    parser = argparse.ArgumentParser(description="Weather data management for AgriWebb")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Default: fetch yesterday's weather
    subparsers.add_parser("daily", help="Fetch yesterday's weather (default)")

    # Backfill command
    backfill_parser = subparsers.add_parser("backfill", help="Backfill historical weather data")
    backfill_parser.add_argument(
        "--days", type=int, help="Number of days to backfill"
    )
    backfill_parser.add_argument(
        "--months", type=int, help="Number of months to backfill"
    )
    backfill_parser.add_argument(
        "--years", type=int, help="Number of years to backfill (default: 2 if nothing specified)"
    )
    backfill_parser.add_argument(
        "--dry-run", action="store_true", help="Don't push to AgriWebb, just save JSON"
    )

    # List command
    subparsers.add_parser("list", help="List all rainfall records for the sensor")

    # Sync command (combined NOAA + Open-Meteo)
    sync_parser = subparsers.add_parser(
        "sync",
        help="Sync recent rainfall (NOAA + Open-Meteo for real-time)"
    )
    sync_parser.add_argument(
        "--days", type=int, default=14,
        help="Number of days to sync (default: 14)"
    )
    sync_parser.add_argument(
        "--dry-run", action="store_true",
        help="Don't push to AgriWebb, just show data"
    )

    args = parser.parse_args()

    if args.command == "sync":
        await sync_weather(
            days=args.days,
            push_to_agriwebb=not args.dry_run,
        )
    elif args.command == "backfill":
        await backfill_weather(
            days=args.days,
            months=args.months,
            years=args.years,
            push_to_agriwebb=not args.dry_run,
        )
    elif args.command == "list":
        await list_rainfalls()
    else:
        await main()


def cli() -> None:
    """CLI entry point."""
    asyncio.run(cli_main())


if __name__ == "__main__":
    cli()
