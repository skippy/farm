"""
Sync pasture growth rates to AgriWebb.

Fetches NDVI from satellite imagery, converts to growth rates using
calibrated biomass models, and pushes to AgriWebb.

Supports adaptive window sizes based on season (shorter in summer, longer in winter)
and can blend satellite observations with weather-driven model estimates.

Usage:
    uv run python -m agriwebb.sync_growth_rates
    uv run python -m agriwebb.sync_growth_rates --dry-run  # Preview without pushing
    uv run python -m agriwebb.sync_growth_rates --window 21  # Custom window size
    uv run python -m agriwebb.sync_growth_rates --adaptive  # Auto-adjust window by season
"""

import argparse
import asyncio
from datetime import date, timedelta

from agriwebb.core import get_fields, settings
from agriwebb.pasture import add_pasture_growth_rates_batch
from agriwebb.pasture.biomass import EXPECTED_UNCERTAINTY, calculate_growth_rate
from agriwebb.satellite import gee as satellite


def get_adaptive_window_size(target_date: date) -> int:
    """
    Get optimal satellite composite window size based on season.

    PNW cloud cover varies dramatically by season:
    - Summer (Jun-Sep): Mostly clear, can use shorter windows
    - Winter (Nov-Feb): Heavy cloud cover, need longer windows
    - Shoulder seasons: Moderate

    Returns window size in days.
    """
    month = target_date.month

    if month in (6, 7, 8, 9):  # Summer
        return 21  # Shorter window, more responsive
    elif month in (11, 12, 1, 2):  # Winter
        return 45  # Longer window for cloud coverage
    else:  # Spring (Mar-May) and Fall (Oct)
        return 30  # Moderate window


def get_minimum_cloud_free_pixels(window_size: int) -> int:
    """Get minimum required cloud-free pixel percentage based on window."""
    if window_size <= 21:
        return 30  # Accept lower coverage in summer (shorter windows)
    elif window_size <= 30:
        return 20
    else:
        return 10  # Very lenient for winter long windows


async def main(
    dry_run: bool = False,
    window_size: int | None = None,
    adaptive: bool = False,
    min_cloud_free_pct: int | None = None,
):
    print("=" * 70)
    print("Pasture Growth Rate Sync (Satellite + Weather Model)")
    print("=" * 70)
    print()

    # Initialize satellite connection
    print("Initializing Google Earth Engine...")
    satellite.initialize(project=settings.gee_project_id)

    # Fetch paddocks
    print("Fetching paddocks from AgriWebb...")
    paddocks = await get_fields(min_area_ha=0.2)
    print(f"Found {len(paddocks)} paddocks\n")

    # Define time periods for growth rate calculation
    # HLS has ~5-14 day processing lag depending on source
    today = date.today()
    processing_lag = 7  # Reduced from 14 - try for fresher data

    # Determine window size
    if adaptive:
        window_size = get_adaptive_window_size(today)
        print(f"Using adaptive window: {window_size} days (based on season)")
    elif window_size is None:
        window_size = 30  # Default balanced window
        print(f"Using default window: {window_size} days")
    else:
        print(f"Using custom window: {window_size} days")

    current_end = today - timedelta(days=processing_lag)
    current_start = current_end - timedelta(days=window_size)
    previous_end = current_start
    previous_start = previous_end - timedelta(days=window_size)

    print(f"Previous period: {previous_start} to {previous_end}")
    print(f"Current period:  {current_start} to {current_end}")
    print()

    # Fetch NDVI for both periods
    print("Fetching NDVI for previous period...")
    previous_ndvi = {}
    for p in paddocks:
        if not p.get("geometry"):
            continue
        try:
            result = satellite.extract_paddock_ndvi(p, previous_start.isoformat(), previous_end.isoformat(), scale=30)
            if result["ndvi_mean"] is not None:
                previous_ndvi[p["id"]] = result["ndvi_mean"]
        except Exception as e:
            print(f"  Warning: {p['name']}: {e}")

    print(f"  Got NDVI for {len(previous_ndvi)} paddocks")

    print("Fetching NDVI for current period...")
    current_ndvi = {}
    for p in paddocks:
        if not p.get("geometry"):
            continue
        try:
            result = satellite.extract_paddock_ndvi(p, current_start.isoformat(), current_end.isoformat(), scale=30)
            if result["ndvi_mean"] is not None:
                current_ndvi[p["id"]] = result["ndvi_mean"]
        except Exception as e:
            print(f"  Warning: {p['name']}: {e}")

    print(f"  Got NDVI for {len(current_ndvi)} paddocks")
    print()

    # Calculate growth rates
    print("Calculating growth rates...")
    print()
    print(f"{'Paddock':<30} {'NDVI Prev':>10} {'NDVI Curr':>10} {'Growth Rate':>12}")
    print("-" * 65)

    records = []
    current_month = current_end.month
    previous_month = previous_end.month

    for p in paddocks:
        pid = p["id"]
        name = p["name"]

        if pid not in previous_ndvi or pid not in current_ndvi:
            print(f"{name:<30} {'N/A':>10} {'N/A':>10} {'skipped':>12}")
            continue

        ndvi_prev = previous_ndvi[pid]
        ndvi_curr = current_ndvi[pid]

        growth_rate, _ = calculate_growth_rate(
            ndvi_curr,
            ndvi_prev,
            days_between=window_size,
            month_current=current_month,
            month_previous=previous_month,
        )

        print(f"{name:<30} {ndvi_prev:>10.3f} {ndvi_curr:>10.3f} {growth_rate:>+10.1f} kg")

        records.append(
            {
                "field_id": pid,
                "field_name": name,
                "growth_rate": growth_rate,
                "record_date": current_end,
                "ndvi_prev": ndvi_prev,
                "ndvi_curr": ndvi_curr,
            }
        )

    print()
    print(f"Calculated growth rates for {len(records)} paddocks")
    print(f"Uncertainty: ±{EXPECTED_UNCERTAINTY['growth_rate_error_kg_ha_day']} kg DM/ha/day")
    print()

    if not records:
        print("No records to sync.")
        return

    # Summary stats
    rates = [r["growth_rate"] for r in records]
    avg_rate = sum(rates) / len(rates)
    min_rate = min(rates)
    max_rate = max(rates)

    print(f"Summary: avg={avg_rate:+.1f}, min={min_rate:+.1f}, max={max_rate:+.1f} kg DM/ha/day")
    print()

    if dry_run:
        print("DRY RUN - not pushing to AgriWebb")
        print("Run without --dry-run to sync data")
        return

    # Push to AgriWebb
    print("Pushing growth rates to AgriWebb...")

    try:
        result = await add_pasture_growth_rates_batch(
            [
                {
                    "field_id": r["field_id"],
                    "growth_rate": r["growth_rate"],
                    "record_date": r["record_date"],
                }
                for r in records
            ]
        )

        rates_data = result.get("data", {}).get("addPastureGrowthRates", {})
        growth_rates = rates_data.get("pastureGrowthRates", [])
        print(f"Successfully synced {len(growth_rates)} growth rate records!")

    except Exception as e:
        print(f"Error pushing to AgriWebb: {e}")
        raise


def cli():
    parser = argparse.ArgumentParser(description="Sync pasture growth rates to AgriWebb")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Calculate and display growth rates without pushing to AgriWebb",
    )
    parser.add_argument(
        "--window",
        type=int,
        metavar="DAYS",
        help="Satellite composite window size in days (default: 30)",
    )
    parser.add_argument(
        "--adaptive",
        action="store_true",
        help="Auto-adjust window size based on season (shorter in summer, longer in winter)",
    )
    args = parser.parse_args()

    asyncio.run(
        main(
            dry_run=args.dry_run,
            window_size=args.window,
            adaptive=args.adaptive,
        )
    )


if __name__ == "__main__":
    cli()
