"""
Backfill historical pasture growth data to AgriWebb.

Calculates growth rates for past dates using weather history and pushes to AgriWebb.
This is useful for populating historical records for analysis and comparison.

Usage:
    uv run python -m agriwebb.backfill_growth --start 2023-01-01 --end 2023-12-31
    uv run python -m agriwebb.backfill_growth --start 2023-01-01 --dry-run
    uv run python -m agriwebb.backfill_growth --years 2  # Last 2 years
"""

import argparse
import asyncio
import json
from datetime import date, timedelta
from pathlib import Path

from agriwebb.core import add_pasture_growth_rates_batch, get_cache_dir
from agriwebb.data.historical import load_weather_history
from agriwebb.pasture.growth import calculate_farm_growth, load_paddock_soils


def load_fields_for_sync() -> dict[str, str]:
    """Load paddock name to AgriWebb field ID mapping."""
    fields_path = get_cache_dir() / "fields.json"
    if fields_path.exists():
        with open(fields_path) as f:
            data = json.load(f)
        if isinstance(data, list):
            fields = data
        else:
            fields = data.get("fields", [])
        return {f["name"]: f["id"] for f in fields}
    return {}


async def backfill_growth(
    start_date: date,
    end_date: date,
    dry_run: bool = False,
    batch_size: int = 100,
) -> dict:
    """
    Calculate and push historical growth rates.

    Args:
        start_date: First date to calculate
        end_date: Last date to calculate
        dry_run: If True, don't actually push to AgriWebb
        batch_size: Number of records per API call

    Returns:
        Summary of backfill operation
    """
    print("=" * 70)
    print("Pasture Growth Backfill")
    print("=" * 70)
    print(f"\nDate range: {start_date} to {end_date}")
    print(f"Days to process: {(end_date - start_date).days + 1}")

    # Load data
    print("\nLoading data...")
    weather_data = load_weather_history()
    paddock_soils = load_paddock_soils()
    field_ids = load_fields_for_sync()

    print(f"  Weather data: {len(weather_data)} days")
    print(f"  Paddocks with soil data: {len(paddock_soils)}")
    print(f"  Paddocks with field IDs: {len(field_ids)}")

    # Filter weather data to date range
    weather_dates = {d["date"]: d for d in weather_data}
    available_start = min(weather_dates.keys())
    available_end = max(weather_dates.keys())

    print(f"\nWeather data available: {available_start} to {available_end}")

    if start_date.isoformat() < available_start:
        print(f"  WARNING: Requested start {start_date} is before weather data")
        start_date = date.fromisoformat(available_start)
        print(f"  Adjusted start to: {start_date}")

    if end_date.isoformat() > available_end:
        print(f"  WARNING: Requested end {end_date} is after weather data")
        end_date = date.fromisoformat(available_end)
        print(f"  Adjusted end to: {end_date}")

    # Calculate growth for entire period
    print(f"\nCalculating growth rates...")
    results = calculate_farm_growth(
        start_date=start_date,
        end_date=end_date,
        paddock_soils=paddock_soils,
        weather_data=weather_data,
    )

    # Prepare records for sync
    all_records = []
    for paddock_name, daily_results in results.items():
        if paddock_name not in field_ids:
            continue

        field_id = field_ids[paddock_name]
        for day_result in daily_results:
            all_records.append({
                "field_id": field_id,
                "field_name": paddock_name,
                "growth_rate": day_result["growth_kg_ha_day"],
                "record_date": day_result["date"],
            })

    print(f"  Generated {len(all_records)} records for {len(field_ids)} paddocks")

    if not all_records:
        return {"error": "No records to sync"}

    # Sort by date for orderly processing
    all_records.sort(key=lambda r: r["record_date"])

    # Show sample of data
    print(f"\nSample records (first 5):")
    for rec in all_records[:5]:
        print(f"  {rec['record_date']}: {rec['field_name']:<20} {rec['growth_rate']:.1f} kg/ha/day")

    if dry_run:
        print(f"\n[DRY RUN] Would push {len(all_records)} records to AgriWebb")

        # Show monthly summary
        by_month = {}
        for rec in all_records:
            month = rec["record_date"][:7]  # YYYY-MM
            if month not in by_month:
                by_month[month] = {"count": 0, "total_growth": 0}
            by_month[month]["count"] += 1
            by_month[month]["total_growth"] += rec["growth_rate"]

        print(f"\nMonthly summary:")
        for month in sorted(by_month.keys()):
            data = by_month[month]
            avg = data["total_growth"] / data["count"]
            print(f"  {month}: {data['count']} records, avg {avg:.1f} kg/ha/day")

        return {
            "dry_run": True,
            "records": len(all_records),
            "date_range": f"{start_date} to {end_date}",
            "paddocks": len(field_ids),
        }

    # Push in batches
    print(f"\nPushing to AgriWebb in batches of {batch_size}...")
    total_pushed = 0
    errors = []

    for i in range(0, len(all_records), batch_size):
        batch = all_records[i:i + batch_size]
        batch_start = batch[0]["record_date"]
        batch_end = batch[-1]["record_date"]

        try:
            await add_pasture_growth_rates_batch([
                {
                    "field_id": r["field_id"],
                    "growth_rate": r["growth_rate"],
                    "record_date": r["record_date"],
                }
                for r in batch
            ])
            total_pushed += len(batch)
            print(f"  Pushed batch {i // batch_size + 1}: {batch_start} to {batch_end} ({len(batch)} records)")
        except Exception as e:
            errors.append(f"Batch {i // batch_size + 1}: {e}")
            print(f"  ERROR on batch {i // batch_size + 1}: {e}")

        # Small delay between batches to be nice to the API
        if i + batch_size < len(all_records):
            await asyncio.sleep(0.5)

    print(f"\nBackfill complete!")
    print(f"  Records pushed: {total_pushed}")
    if errors:
        print(f"  Errors: {len(errors)}")

    return {
        "records_pushed": total_pushed,
        "errors": errors,
        "date_range": f"{start_date} to {end_date}",
    }


async def main():
    parser = argparse.ArgumentParser(
        description="Backfill historical pasture growth data to AgriWebb"
    )
    parser.add_argument(
        "--start",
        type=str,
        help="Start date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--end",
        type=str,
        help="End date (YYYY-MM-DD), defaults to yesterday",
    )
    parser.add_argument(
        "--years",
        type=int,
        help="Backfill last N years (alternative to --start/--end)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Don't actually push to AgriWebb",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Records per API call (default: 100)",
    )
    args = parser.parse_args()

    # Determine date range
    yesterday = date.today() - timedelta(days=1)

    if args.years:
        start_date = date(yesterday.year - args.years, yesterday.month, yesterday.day)
        end_date = yesterday
    elif args.start:
        start_date = date.fromisoformat(args.start)
        end_date = date.fromisoformat(args.end) if args.end else yesterday
    else:
        parser.error("Must specify --start or --years")

    await backfill_growth(
        start_date=start_date,
        end_date=end_date,
        dry_run=args.dry_run,
        batch_size=args.batch_size,
    )


def cli():
    asyncio.run(main())


if __name__ == "__main__":
    cli()
