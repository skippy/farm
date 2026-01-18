"""Unified CLI for weather data management.

Combines NOAA/NCEI and Open-Meteo data sources into a single interface.
The implementation details (which API is used) are abstracted away.
"""

import argparse
import asyncio
from datetime import date, timedelta

from agriwebb.weather import openmeteo
from agriwebb.weather.ncei import (
    fetch_ncei_date_range,
    list_rainfalls,
    sync_weather,
    save_weather_json,
)


async def cmd_current(args: argparse.Namespace) -> None:
    """Show current weather conditions."""
    print("Current conditions:")
    current = await openmeteo.fetch_current_conditions()
    print(f"  Temperature: {current['temperature_c']}°C")
    print(f"  Precipitation: {current['precipitation_mm']} mm")
    print(f"  Time: {current['time']}")


async def cmd_forecast(args: argparse.Namespace) -> None:
    """Show weather forecast."""
    days = getattr(args, 'days', 7)
    print(f"\n{days}-day forecast:")
    forecast = await openmeteo.fetch_forecast(days=days, include_past_days=0)
    print(f"{'Date':<12} {'Temp':<10} {'Precip':<10} {'ET0':<8}")
    print("-" * 42)
    for day in forecast:
        print(f"{day['date']:<12} {day['temp_min_c']:.0f}-{day['temp_max_c']:.0f}°C    "
              f"{day['precip_mm']:.1f} mm    {day['et0_mm']:.1f} mm")


async def cmd_sync(args: argparse.Namespace) -> None:
    """Sync rainfall data to AgriWebb."""
    # Calculate total days from flags
    total_days = 0
    if args.days:
        total_days += args.days
    if args.months:
        total_days += args.months * 30
    if args.years:
        total_days += args.years * 365

    if total_days == 0:
        print("Error: Must specify --days, --months, or --years")
        return

    await sync_weather(
        days=total_days,
        push_to_agriwebb=not args.dry_run,
    )


async def cmd_list(args: argparse.Namespace) -> None:
    """List rainfall records in AgriWebb."""
    await list_rainfalls()


async def cmd_cache(args: argparse.Namespace) -> None:
    """Download weather data from both Open-Meteo and NOAA to local cache."""
    refresh = getattr(args, 'refresh', False)

    print("=" * 60)
    print("Weather Data Cache" + (" (refresh)" if refresh else ""))
    print("=" * 60)
    print()

    # Step 1: Open-Meteo (comprehensive historical + forecast)
    print("Fetching Open-Meteo data (historical + forecast)...")
    data = await openmeteo.update_weather_cache(refresh=refresh)
    print(f"  Cached {data['daily_records']} days")
    print(f"  Date range: {data['daily_data'][0]['date']} to {data['daily_data'][-1]['date']}")
    print()

    # Step 2: NOAA/NCEI station data
    # Smart caching: only fetch what's missing unless refresh=True
    print("Fetching NOAA station data...")
    await update_noaa_cache(refresh=refresh)

    print()
    print("Cache complete!")


async def update_noaa_cache(refresh: bool = False) -> None:
    """Update NOAA weather cache smartly."""
    import json
    from agriwebb.core import get_cache_dir

    cache_path = get_cache_dir() / "noaa_weather.json"
    end_date = date.today() - timedelta(days=1)

    # Load existing cache
    existing_dates = set()
    if not refresh and cache_path.exists():
        with open(cache_path) as f:
            existing = json.load(f)
        existing_dates = {r["date"] for r in existing.get("records", [])}
        if existing_dates:
            latest = max(existing_dates)
            print(f"  Cache has data through {latest}")

    if refresh or not existing_dates:
        # Full fetch: 2 years
        start_date = end_date - timedelta(days=730)
        print(f"  Fetching full history ({start_date} to {end_date})...")
    else:
        # Incremental: from latest cached date
        latest_date = date.fromisoformat(max(existing_dates))
        # NOAA data has ~6 day lag, so start from 7 days before latest to catch updates
        start_date = latest_date - timedelta(days=7)
        if start_date >= end_date:
            print("  Cache is up to date")
            return
        print(f"  Fetching updates ({start_date} to {end_date})...")

    try:
        noaa_data = await fetch_ncei_date_range(start_date, end_date)
        if noaa_data:
            if not refresh and existing_dates:
                # Merge with existing
                with open(cache_path) as f:
                    existing = json.load(f)
                existing_records = {r["date"]: r for r in existing.get("records", [])}
                # Update/add new records
                for record in noaa_data:
                    existing_records[record["date"]] = record
                noaa_data = sorted(existing_records.values(), key=lambda x: x["date"])

            json_path = save_weather_json(noaa_data, "noaa_weather.json")
            print(f"  Cached {len(noaa_data)} days from NOAA")
        else:
            print("  No NOAA data available")
    except Exception as e:
        print(f"  Warning: Could not fetch NOAA data: {e}")


async def cli_main() -> None:
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Weather data management for AgriWebb",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  agriwebb-weather current             Show current conditions
  agriwebb-weather forecast            Show 7-day forecast
  agriwebb-weather list                List rainfall records in AgriWebb
  agriwebb-weather sync --days 14      Sync recent rainfall to AgriWebb
  agriwebb-weather sync --years 2      Backfill 2 years of rainfall
  agriwebb-weather cache               Download weather data to local cache
""",
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # current - Show current conditions
    subparsers.add_parser("current", help="Show current weather conditions")

    # forecast - Show forecast
    forecast_parser = subparsers.add_parser("forecast", help="Show weather forecast")
    forecast_parser.add_argument(
        "--days", type=int, default=7, help="Number of forecast days (default: 7)"
    )

    # list - List AgriWebb rainfall records
    subparsers.add_parser("list", help="List rainfall records in AgriWebb")

    # sync - Sync rainfall to AgriWebb
    sync_parser = subparsers.add_parser(
        "sync", help="Sync rainfall data to AgriWebb"
    )
    sync_parser.add_argument(
        "--days", type=int, help="Number of days to sync"
    )
    sync_parser.add_argument(
        "--months", type=int, help="Number of months to sync"
    )
    sync_parser.add_argument(
        "--years", type=int, help="Number of years to sync"
    )
    sync_parser.add_argument(
        "--dry-run", action="store_true", help="Preview without pushing to AgriWebb"
    )

    # cache - Download weather data
    cache_parser = subparsers.add_parser("cache", help="Download weather data to local cache")
    cache_parser.add_argument(
        "--refresh", action="store_true",
        help="Force full re-fetch, ignoring existing cache"
    )

    args = parser.parse_args()

    # Dispatch to command handlers
    commands = {
        "current": cmd_current,
        "forecast": cmd_forecast,
        "list": cmd_list,
        "sync": cmd_sync,
        "cache": cmd_cache,
    }

    if args.command in commands:
        await commands[args.command](args)
    else:
        parser.print_help()


def cli() -> None:
    """CLI entry point."""
    asyncio.run(cli_main())


if __name__ == "__main__":
    cli()
