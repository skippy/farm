"""Unified CLI for pasture growth management.

Combines weather-driven estimates and satellite-based observations
into a single interface.
"""

import argparse
import asyncio
import json
from datetime import UTC, date, datetime, timedelta
from typing import TypedDict

from agriwebb.core import (
    get_cache_dir,
    get_farm_today,
    get_fields,
    settings,
)
from agriwebb.data.grazing import calculate_paddock_consumption, load_farm_data, load_fields
from agriwebb.data.historical import (
    compare_to_historical,
    get_monthly_averages,
    load_weather_history,
)
from agriwebb.pasture.api import (
    add_pasture_growth_rates_batch,
    add_standing_dry_matter_batch,
    get_pasture_growth_rates,
)
from agriwebb.pasture.biomass import ndvi_to_standing_dry_matter
from agriwebb.pasture.growth import (
    calculate_farm_growth,
    load_paddock_soils,
)
from agriwebb.weather import fetch_ncei_date_range, openmeteo, save_weather_json

# =============================================================================
# Type Definitions
# =============================================================================


class GrowthRateRecord(TypedDict, total=False):
    """Growth rate record for sync."""

    field_id: str
    field_name: str
    growth_rate: float
    record_date: str
    status: str
    existing_value: float


class GrowthSyncResult(TypedDict):
    """Result of filtering growth records for sync."""

    records_to_push: list[GrowthRateRecord]
    skipped_count: int


# =============================================================================
# Growth Rate Sync Helper Functions
# =============================================================================


def _build_existing_growth_lookup(
    growth_records: list[dict],
) -> dict[tuple[str, str], float]:
    """Build a (field_id, date)->value lookup from existing growth rate records.

    Args:
        growth_records: List of growth rate records from AgriWebb API

    Returns:
        Dict mapping (field_id, date_str) tuple to growth rate value
    """
    existing_by_key: dict[tuple[str, str], float] = {}
    for rec in growth_records:
        rec_date = datetime.fromtimestamp(rec["time"] / 1000, tz=UTC).date()
        key = (rec["fieldId"], str(rec_date))
        existing_by_key[key] = rec["value"]
    return existing_by_key


def _growth_values_match(
    new_value: float, existing_value: float, tolerance: float | None = None
) -> bool:
    """Check if two growth rate values match within tolerance.

    Args:
        new_value: New growth rate value
        existing_value: Existing growth rate value
        tolerance: Maximum difference to consider equal.
            If None, uses settings.growth_rate_tolerance.

    Returns:
        True if values match within tolerance
    """
    if tolerance is None:
        tolerance = settings.growth_rate_tolerance
    return abs(existing_value - new_value) <= tolerance


def filter_changed_growth_records(
    records: list[GrowthRateRecord],
    existing_by_key: dict[tuple[str, str], float],
    force: bool = False,
    tolerance: float | None = None,
) -> GrowthSyncResult:
    """Filter growth records to only those that need updating.

    Args:
        records: List of growth rate records to potentially sync
        existing_by_key: Dict of existing (field_id, date)->value from AgriWebb
        force: If True, include all records regardless of existing values
        tolerance: Maximum difference to consider values equal.
            If None, uses settings.growth_rate_tolerance.

    Returns:
        GrowthSyncResult with records_to_push and skipped_count
    """
    if tolerance is None:
        tolerance = settings.growth_rate_tolerance

    records_to_push: list[GrowthRateRecord] = []
    skipped_count = 0

    for record in records:
        key = (record["field_id"], record["record_date"])
        new_value = round(record["growth_rate"], 1)
        existing_value = existing_by_key.get(key)

        if not force and existing_value is not None and _growth_values_match(new_value, existing_value, tolerance):
            skipped_count += 1
            record["status"] = "unchanged"
        else:
            records_to_push.append(record)
            if force:
                record["status"] = "force"
            elif existing_value is not None:
                record["status"] = f"update ({existing_value:.1f}→{new_value:.1f})"
                record["existing_value"] = existing_value
            else:
                record["status"] = "new"

    return {"records_to_push": records_to_push, "skipped_count": skipped_count}


def _get_growth_record_status(
    record: GrowthRateRecord,
    existing_by_key: dict[tuple[str, str], float],
    force: bool = False,
) -> str:
    """Get display status for a growth rate record.

    Args:
        record: Growth rate record
        existing_by_key: Dict of existing (field_id, date)->value from AgriWebb
        force: If True, all records show "force" status

    Returns:
        Status string for display
    """
    if force:
        return "force"

    key = (record["field_id"], record["record_date"])
    new_value = round(record["growth_rate"], 1)
    existing_value = existing_by_key.get(key)

    if existing_value is None:
        return "new"
    elif _growth_values_match(new_value, existing_value):
        return "unchanged"
    else:
        return f"update ({existing_value:.1f}→{new_value:.1f})"


def _print_growth_sync_table(
    records: list[GrowthRateRecord],
    force: bool = False,
) -> None:
    """Print the growth rate sync status table."""
    print(f"\n{'Paddock':<25} {'Growth Rate':>12} {'Status':<20}")
    print("-" * 60)
    for record in records:
        status = record.get("status", "unknown")
        print(f"{record['field_name']:<25} {record['growth_rate']:>10.1f} kg {status}")


# =============================================================================
# Field Loading
# =============================================================================


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

    soils = load_paddock_soils()
    return {name: data.get("paddock_id", "") for name, data in soils.items() if data.get("paddock_id")}


# -----------------------------------------------------------------------------
# Estimate Command (weather-driven)
# -----------------------------------------------------------------------------


async def estimate_current_growth(
    days_back: int = 7,
    include_forecast: bool = False,
    include_grazing: bool = True,
) -> dict:
    """Estimate current pasture growth rates for all paddocks."""
    print("Updating weather data...")
    weather_data = await openmeteo.update_weather_cache()

    paddock_soils = load_paddock_soils()
    print(f"Loaded {len(paddock_soils)} paddocks with soil data")

    grazing_by_paddock = {}
    if include_grazing:
        try:
            farm_data = load_farm_data()
            animals = farm_data.get("animals", [])
            fields = load_fields()
            consumption = calculate_paddock_consumption(animals, fields, min_area_ha=0.2)

            for pid, data in consumption.items():
                grazing_by_paddock[data["paddock_name"]] = {
                    "paddock_id": pid,
                    "animal_count": data["animal_count"],
                    "consumption_kg_ha_day": data["intake_per_ha_kg_day"],
                    "total_intake_kg_day": data["total_intake_kg_day"],
                }
            print(f"Loaded grazing data for {len(grazing_by_paddock)} paddocks")
        except FileNotFoundError:
            print("No animal data found - skipping grazing consumption")
            include_grazing = False

    # Use yesterday in farm's local timezone - ensures full day of data
    today = await get_farm_today()
    end_date = today - timedelta(days=1)
    start_date = end_date - timedelta(days=days_back - 1)

    print(f"\nCalculating growth for {start_date} to {end_date}...")
    results = calculate_farm_growth(
        start_date=start_date,
        end_date=end_date,
        paddock_soils=paddock_soils,
        weather_data=weather_data["daily_data"],
    )

    current_estimates = {}
    for name, daily_results in results.items():
        if daily_results:
            recent = [r for r in daily_results if r["date"] <= end_date.isoformat()]
            if recent:
                latest = recent[-1]
                week_growth = [r["growth_kg_ha_day"] for r in recent[-7:]]
                avg_7day = sum(week_growth) / len(week_growth) if week_growth else 0

                grazing = grazing_by_paddock.get(name, {})
                consumption = grazing.get("consumption_kg_ha_day", 0)
                animal_count = grazing.get("animal_count", 0)
                net_change = round(avg_7day - consumption, 1)

                current_estimates[name] = {
                    "date": latest["date"],
                    "growth_kg_ha_day": latest["growth_kg_ha_day"],
                    "growth_7day_avg": round(avg_7day, 1),
                    "consumption_kg_ha_day": round(consumption, 1),
                    "net_change_kg_ha_day": net_change,
                    "animal_count": animal_count,
                    "soil_moisture": latest["soil_moisture_fraction"],
                    "temp_factor": latest["temp_factor"],
                    "moisture_factor": latest["moisture_factor"],
                    "season": latest["season"],
                    "notes": latest["notes"],
                }

    forecast_estimates = {}
    if include_forecast:
        print("\nCalculating 7-day growth projection...")
        forecast_end = today + timedelta(days=7)
        forecast_results = calculate_farm_growth(
            start_date=today + timedelta(days=1),
            end_date=forecast_end,
            paddock_soils=paddock_soils,
            weather_data=weather_data["daily_data"],
        )

        for name, daily_results in forecast_results.items():
            if daily_results:
                total_forecast = sum(r["growth_kg_ha_day"] for r in daily_results)
                avg_forecast = total_forecast / len(daily_results) if daily_results else 0
                forecast_estimates[name] = {
                    "days": len(daily_results),
                    "total_growth_kg_ha": round(total_forecast, 0),
                    "avg_growth_kg_ha_day": round(avg_forecast, 1),
                }

    print("Fetching current conditions...")
    try:
        current_conditions = await openmeteo.fetch_current_conditions()
    except Exception:
        current_conditions = None

    return {
        "generated_at": today.isoformat(),
        "weather": {
            "current": current_conditions,
            "data_through": weather_data["daily_data"][-1]["date"] if weather_data["daily_data"] else None,
        },
        "paddock_count": len(current_estimates),
        "estimates": current_estimates,
        "forecast": forecast_estimates if include_forecast else None,
    }


async def sync_growth_to_agriwebb(estimates: dict, dry_run: bool = False, force: bool = False) -> dict:
    """Push growth estimates to AgriWebb.

    Only pushes records that have changed to avoid unnecessary API calls,
    unless force is specified.
    """
    # Build records from estimates
    field_ids = load_fields_for_sync()
    records: list[GrowthRateRecord] = []
    for name, data in estimates["estimates"].items():
        if name not in field_ids:
            print(f"  Skipping {name}: no AgriWebb field ID")
            continue

        records.append(
            {
                "field_id": field_ids[name],
                "field_name": name,
                "growth_rate": data["growth_7day_avg"],
                "record_date": data["date"],
            }
        )

    if not records:
        return {"error": "No records to sync"}

    # Fetch existing records (unless --force)
    existing_by_key: dict[tuple[str, str], float] = {}
    if not force:
        print("\nFetching existing AgriWebb growth rate records...")
        record_dates = [r["record_date"] for r in records]
        min_date = min(record_dates)
        max_date = max(record_dates)

        existing_records = await get_pasture_growth_rates(
            start_date=min_date, end_date=max_date
        )
        existing_by_key = _build_existing_growth_lookup(existing_records)
        print(f"Found {len(existing_by_key)} existing records in date range")
    else:
        print("\n--force specified, pushing all records")

    # Filter to only changed records
    result = filter_changed_growth_records(records, existing_by_key, force)
    records_to_push = result["records_to_push"]
    skipped_count = result["skipped_count"]

    # Display sync table
    _print_growth_sync_table(records, force)

    # Print summary
    if force:
        print(f"\nRecords to push: {len(records_to_push)} (--force, pushing all)")
    else:
        print(f"\nRecords to push: {len(records_to_push)} ({skipped_count} unchanged, skipping)")

    # Handle dry run
    if dry_run:
        print("DRY RUN - not pushing to AgriWebb")
        return {"dry_run": True, "records": len(records_to_push), "skipped": skipped_count}

    # Handle nothing to push
    if not records_to_push:
        print("No records need updating.")
        return {"pushed": 0, "skipped": skipped_count}

    # Push to AgriWebb
    print("\nPushing to AgriWebb...")
    api_result = await add_pasture_growth_rates_batch(
        [
            {
                "field_id": r["field_id"],
                "growth_rate": r["growth_rate"],
                "record_date": r["record_date"],
            }
            for r in records_to_push
        ]
    )

    print(f"Completed: {len(records_to_push)} records synced")
    return api_result


async def cmd_estimate(args: argparse.Namespace) -> None:
    """Weather-driven pasture growth estimates."""
    print("=" * 70)
    print("Pasture Growth Estimate (Weather-Driven Model)")
    print("=" * 70)

    days_back = getattr(args, "days", 14)
    estimates = await estimate_current_growth(
        days_back=days_back,
        include_forecast=args.forecast,
    )

    if args.json:
        print(json.dumps(estimates, indent=2))
        return

    if estimates["weather"]["current"]:
        current = estimates["weather"]["current"]
        print(f"\nCurrent conditions: {current.get('temperature_c', 'N/A')}°C")

    print(f"\nPasture Balance ({estimates['generated_at']}):")
    print(f"{'Paddock':<22} {'Growth':<9} {'Grazing':<9} {'Net':<9} {'Animals':<8} {'Status'}")
    print("-" * 75)

    sorted_estimates = sorted(
        estimates["estimates"].items(),
        key=lambda x: x[1]["net_change_kg_ha_day"],
    )

    for name, data in sorted_estimates:
        growth = data["growth_7day_avg"]
        consumption = data["consumption_kg_ha_day"]
        net = data["net_change_kg_ha_day"]
        animals = data["animal_count"]

        if animals == 0:
            status = "resting"
        elif net < -20:
            status = "DEPLETING"
        elif net < 0:
            status = "declining"
        elif net < 10:
            status = "stable"
        else:
            status = "building"

        print(f"{name:<22} {growth:>6.1f}    {consumption:>6.1f}    {net:>+6.1f}    {animals:>5}    {status}")

    grazed = [(n, d) for n, d in estimates["estimates"].items() if d["animal_count"] > 0]
    resting = [(n, d) for n, d in estimates["estimates"].items() if d["animal_count"] == 0]

    print("\n--- Summary ---")
    print(f"Paddocks with animals: {len(grazed)}")
    print(f"Paddocks resting: {len(resting)}")

    if grazed:
        net_changes = [d["net_change_kg_ha_day"] for _, d in grazed]
        print(f"Grazed paddocks avg net change: {sum(net_changes) / len(net_changes):+.1f} kg/ha/day")

    growth_rates = [d["growth_7day_avg"] for d in estimates["estimates"].values()]
    if growth_rates:
        avg = sum(growth_rates) / len(growth_rates)
        print(f"Farm avg growth potential: {avg:.1f} kg DM/ha/day")

    try:
        weather_history = load_weather_history()
        monthly_avgs = get_monthly_averages(weather_history)
        current_month = date.today().month

        if growth_rates and current_month in monthly_avgs:
            comparison = compare_to_historical(avg, current_month, monthly_avgs)

            print(f"\n--- Historical Context ({comparison['month_name']}) ---")
            print(f"Current growth: {comparison['current_growth']:.1f} kg/ha/day")
            print(f"Historical avg: {comparison['historical_avg']:.1f} kg/ha/day ({comparison['years_of_data']} years)")
            status = comparison["status"].upper()
            dev, dev_pct = comparison["deviation"], comparison["deviation_pct"]
            print(f"Status: {status} ({dev:+.1f} kg, {dev_pct:+.1f}%)")
    except Exception as e:
        print(f"\n(Historical comparison unavailable: {e})")

    if args.forecast and estimates.get("forecast"):
        print("\n7-Day Growth Projection:")
        print(f"{'Paddock':<25} {'Projected Total':<18} {'Avg/Day'}")
        print("-" * 55)

        sorted_forecast = sorted(
            estimates["forecast"].items(),
            key=lambda x: x[1]["total_growth_kg_ha"],
            reverse=True,
        )

        for name, data in sorted_forecast:
            print(f"{name:<25} {data['total_growth_kg_ha']:>12.0f} kg     {data['avg_growth_kg_ha_day']:>6.1f} kg")

    cache_path = get_cache_dir() / "growth_estimates.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_path, "w") as f:
        json.dump(estimates, f, indent=2)
    print(f"\nEstimates saved to: {cache_path}")


# -----------------------------------------------------------------------------
# Sync Command
# -----------------------------------------------------------------------------


async def sync_growth_rates(args: argparse.Namespace) -> None:
    """Sync growth rates from weather model."""
    print("=" * 70)
    print("Syncing Growth Rates (Weather Model)")
    print("=" * 70)

    days_back = getattr(args, "days", 14)
    include_forecast = getattr(args, "forecast", False)

    estimates = await estimate_current_growth(
        days_back=days_back,
        include_forecast=include_forecast,
    )

    print(f"\nPrepared estimates for {len(estimates['estimates'])} paddocks")

    result = await sync_growth_to_agriwebb(estimates, dry_run=args.dry_run, force=getattr(args, "force", False))

    if "error" in result:
        print(f"Error: {result['error']}")
    elif args.dry_run:
        print(f"Would sync {result['records']} growth rate records")
    else:
        print("Growth rates synced!")

    # Save estimates to cache
    cache_path = get_cache_dir() / "growth_estimates.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_path, "w") as f:
        json.dump(estimates, f, indent=2)


async def sync_sdm(args: argparse.Namespace) -> None:
    """Sync standing dry matter from satellite NDVI."""
    from agriwebb.satellite import gee as satellite

    print("=" * 70)
    print("Syncing Standing Dry Matter (Satellite NDVI)")
    print("=" * 70)
    print()

    print("Initializing Google Earth Engine...")
    satellite.initialize(project=settings.gee_project_id)

    print("Fetching paddocks from AgriWebb...")
    paddocks = await get_fields(min_area_ha=0.2)
    print(f"Found {len(paddocks)} paddocks\n")

    today = await get_farm_today()
    processing_lag = 7  # Satellite data is typically delayed
    window_days = getattr(args, "window", 14) or 14

    end_date = today - timedelta(days=processing_lag)
    start_date = end_date - timedelta(days=window_days)

    print(f"Satellite window: {start_date} to {end_date}")
    print()

    print("Fetching NDVI and calculating SDM...")
    print()
    print(f"{'Paddock':<30} {'NDVI':>8} {'SDM (kg/ha)':>12}")
    print("-" * 55)

    records = []
    current_month = end_date.month

    for p in paddocks:
        pid = p["id"]
        name = p["name"]

        if not p.get("geometry"):
            print(f"{name:<30} {'N/A':>8} {'skipped':>12}")
            continue

        try:
            result = satellite.extract_paddock_ndvi(p, start_date.isoformat(), end_date.isoformat(), scale=30)
            ndvi = result.get("ndvi_mean")

            if ndvi is None:
                print(f"{name:<30} {'N/A':>8} {'no data':>12}")
                continue

            sdm, model = ndvi_to_standing_dry_matter(ndvi, month=current_month)
            print(f"{name:<30} {ndvi:>8.3f} {sdm:>10.0f}")

            records.append(
                {
                    "field_id": pid,
                    "field_name": name,
                    "sdm_kg_ha": sdm,
                    "ndvi": ndvi,
                    "record_date": end_date,
                }
            )

        except Exception as e:
            print(f"{name:<30} {'error':>8} {str(e)[:12]:>12}")

    print()
    print(f"Calculated SDM for {len(records)} paddocks")

    if not records:
        print("No SDM records to sync.")
        return

    sdm_values = [r["sdm_kg_ha"] for r in records]
    avg_sdm = sum(sdm_values) / len(sdm_values)
    min_sdm = min(sdm_values)
    max_sdm = max(sdm_values)

    print(f"Summary: avg={avg_sdm:.0f}, min={min_sdm:.0f}, max={max_sdm:.0f} kg DM/ha")
    print()

    if args.dry_run:
        print("DRY RUN - not pushing SDM to AgriWebb")
        return

    print("Pushing SDM to AgriWebb...")

    try:
        result = await add_standing_dry_matter_batch(
            [
                {
                    "field_id": r["field_id"],
                    "sdm_kg_ha": r["sdm_kg_ha"],
                    "record_date": r["record_date"],
                }
                for r in records
            ]
        )

        sdm_data = result.get("data", {}).get("addTotalStandingDryMatters", {})
        sdm_records = sdm_data.get("feedOnOffers", [])
        print(f"Successfully synced {len(sdm_records)} SDM records!")

    except Exception as e:
        print(f"Error pushing SDM to AgriWebb: {e}")
        raise


async def cmd_sync(args: argparse.Namespace) -> None:
    """Sync pasture data to AgriWebb."""
    sync_growth = getattr(args, "growth_rate", False)
    sync_standing = getattr(args, "sdm", False)

    if not sync_growth and not sync_standing:
        print("Error: Must specify --growth-rate, --sdm, or both")
        return

    if sync_growth:
        await sync_growth_rates(args)
        if sync_standing:
            print()

    if sync_standing:
        await sync_sdm(args)


# -----------------------------------------------------------------------------
# Cache Command
# -----------------------------------------------------------------------------


async def update_noaa_cache_smart(refresh: bool = False) -> None:
    """Update NOAA weather cache smartly (only fetch missing data)."""
    import json

    cache_path = get_cache_dir() / "noaa_weather.json"
    # Use yesterday in farm's local timezone
    end_date = await get_farm_today() - timedelta(days=1)

    # Load existing cache
    existing_dates = set()
    if not refresh and cache_path.exists():
        with open(cache_path) as f:
            existing = json.load(f)
        existing_dates = {r["date"] for r in existing.get("records", [])}
        if existing_dates:
            latest = max(existing_dates)
            print(f"    Cache has data through {latest}")

    if refresh or not existing_dates:
        # Full fetch: 2 years
        start_date = end_date - timedelta(days=730)
        print(f"    Fetching full history ({start_date} to {end_date})...")
    else:
        # Incremental: from latest cached date
        latest_date = date.fromisoformat(max(existing_dates))
        # NOAA data has ~6 day lag, so start from 7 days before latest
        start_date = latest_date - timedelta(days=7)
        if start_date >= end_date:
            print("    Cache is up to date")
            return
        print(f"    Fetching updates ({start_date} to {end_date})...")

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

        save_weather_json(noaa_data, "noaa_weather.json")
        print(f"    Cached {len(noaa_data)} days from NOAA")
    else:
        print("    No NOAA data available")


class PaddockNDVIData(TypedDict):
    """NDVI data for a single paddock."""

    name: str
    area_ha: float | None
    land_use: str | None
    history: list[dict]


class NDVIHistoricalData(TypedDict):
    """Historical NDVI data for all paddocks."""

    fetched_at: str
    start_year: int
    paddock_count: int
    paddocks: dict[str, PaddockNDVIData]


async def update_ndvi_cache_smart(refresh: bool = False) -> None:
    """Update NDVI historical cache smartly (only fetch missing months)."""
    import json

    from agriwebb.satellite import gee as satellite
    from agriwebb.satellite.ndvi_historical import fetch_paddock_history

    cache_path = get_cache_dir() / "ndvi_historical.json"
    today = await get_farm_today()

    # Load existing cache
    existing_data = None
    if not refresh and cache_path.exists():
        with open(cache_path) as f:
            existing_data = json.load(f)
        if existing_data:
            print(f"    Cache has data for {existing_data.get('paddock_count', 0)} paddocks")
            print(f"    Last fetched: {existing_data.get('fetched_at', 'unknown')}")

    print("Initializing Google Earth Engine...")
    satellite.initialize(project=settings.gee_project_id)

    print("Fetching paddocks from AgriWebb...")
    paddocks = await get_fields(min_area_ha=0.2)
    print(f"Found {len(paddocks)} paddocks")
    print()

    all_data: NDVIHistoricalData = {
        "fetched_at": today.isoformat(),
        "start_year": 2018,
        "paddock_count": len(paddocks),
        "paddocks": {},
    }

    for i, paddock in enumerate(paddocks, 1):
        name = paddock["name"]
        pid = paddock["id"]

        print(f"[{i}/{len(paddocks)}] {name}...", end=" ", flush=True)

        if not paddock.get("geometry"):
            print("skipped (no geometry)")
            continue

        # Check if we can skip (use cached data)
        if not refresh and existing_data and pid in existing_data.get("paddocks", {}):
            cached_history = existing_data["paddocks"][pid].get("history", [])
            if cached_history:
                # Check if we need to update (last month might be incomplete)
                last_month = cached_history[-1].get("month") if cached_history else None
                current_month = f"{today.year}-{today.month:02d}"

                if last_month and last_month >= current_month:
                    # Cache is up to date
                    all_data["paddocks"][pid] = existing_data["paddocks"][pid]
                    valid_count = sum(1 for r in cached_history if r.get("ndvi_mean") is not None)
                    print(f"{valid_count} months (cached)")
                    continue

        try:
            history = await fetch_paddock_history(paddock)
            valid_count = sum(1 for r in history if r["ndvi_mean"] is not None)
            print(f"{valid_count} months")

            all_data["paddocks"][pid] = {
                "name": name,
                "area_ha": paddock.get("totalArea"),
                "land_use": paddock.get("landUse"),
                "history": history,
            }
        except Exception as e:
            print(f"error: {e}")
            # Keep cached data if available
            if not refresh and existing_data and pid in existing_data.get("paddocks", {}):
                all_data["paddocks"][pid] = existing_data["paddocks"][pid]

    # Save to cache
    get_cache_dir().mkdir(parents=True, exist_ok=True)
    with open(cache_path, "w") as f:
        json.dump(all_data, f, indent=2)

    print(f"\nNDVI data saved to: {cache_path}")


async def cmd_cache(args: argparse.Namespace) -> None:
    """Download weather, soil, and satellite data for pasture analysis."""
    refresh = getattr(args, "refresh", False)

    print("=" * 70)
    print("Pasture Data Cache" + (" (refresh)" if refresh else ""))
    print("=" * 70)
    print()

    # Step 1: Fetch weather data (reuse weather module logic)
    print("Step 1: Fetching weather data...")
    print("-" * 70)

    print("  Open-Meteo (historical + forecast)...")
    try:
        weather_data = await openmeteo.update_weather_cache(refresh=refresh)
        print(f"    Cached {weather_data['daily_records']} days")
        print(f"    Range: {weather_data['daily_data'][0]['date']} to {weather_data['daily_data'][-1]['date']}")
    except Exception as e:
        print(f"    Warning: Could not fetch Open-Meteo data: {e}")

    print("  NOAA station data...")
    try:
        await update_noaa_cache_smart(refresh=refresh)
    except Exception as e:
        print(f"    Warning: Could not fetch NOAA data: {e}")

    print()

    # Step 2: Fetch soil data
    print("Step 2: Fetching soil data from USDA...")
    print("-" * 70)
    try:
        from agriwebb.data.soils import fetch_all_paddock_soils

        await fetch_all_paddock_soils(on_progress=print)
    except Exception as e:
        print(f"Warning: Could not fetch soil data: {e}")

    print()

    # Step 3: Fetch NDVI historical data
    print("Step 3: Fetching historical NDVI from satellite...")
    print("-" * 70)
    try:
        await update_ndvi_cache_smart(refresh=refresh)
    except Exception as e:
        print(f"Warning: Could not fetch NDVI data: {e}")
        print("Ensure Google Earth Engine is configured correctly")

    print()
    print("=" * 70)
    print("Cache complete!")


# -----------------------------------------------------------------------------
# CLI Entry Point
# -----------------------------------------------------------------------------


async def cli_main() -> None:
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Pasture growth management for AgriWebb",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  agriwebb-pasture estimate                   Weather-driven growth estimates
  agriwebb-pasture estimate --forecast        Include 7-day projection
  agriwebb-pasture sync --growth-rate         Push growth rates to AgriWebb
  agriwebb-pasture sync --sdm                 Push standing dry matter from satellite
  agriwebb-pasture sync --growth-rate --sdm   Push both
  agriwebb-pasture sync --sdm --dry-run       Preview SDM without pushing
  agriwebb-pasture cache                      Download weather, soil, and NDVI data
""",
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # estimate - Weather-driven estimates
    estimate_parser = subparsers.add_parser("estimate", help="Weather-driven pasture growth estimates")
    estimate_parser.add_argument("--days", type=int, default=14, help="Days to look back for averages (default: 14)")
    estimate_parser.add_argument("--forecast", action="store_true", help="Include 7-day growth projection")
    estimate_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # sync - Push pasture data to AgriWebb
    sync_parser = subparsers.add_parser("sync", help="Push pasture data to AgriWebb")
    sync_parser.add_argument("--growth-rate", action="store_true", help="Sync growth rates (weather model)")
    sync_parser.add_argument("--sdm", action="store_true", help="Sync standing dry matter (satellite NDVI)")
    sync_parser.add_argument("--days", type=int, default=14, help="Days to look back for growth rates (default: 14)")
    sync_parser.add_argument("--window", type=int, default=14, help="Satellite composite window for SDM (default: 14)")
    sync_parser.add_argument("--forecast", action="store_true", help="Include forecast in growth rate sync")
    sync_parser.add_argument("--dry-run", action="store_true", help="Preview without pushing to AgriWebb")
    sync_parser.add_argument("--force", action="store_true", help="Push all records, even if unchanged")

    # cache - Download weather, soil, and NDVI data
    cache_parser = subparsers.add_parser("cache", help="Download weather, soil, and satellite data")
    cache_parser.add_argument("--refresh", action="store_true", help="Force full re-fetch, ignoring existing cache")

    args = parser.parse_args()

    if args.command == "estimate":
        await cmd_estimate(args)
    elif args.command == "sync":
        await cmd_sync(args)
    elif args.command == "cache":
        await cmd_cache(args)
    else:
        parser.print_help()


def cli() -> None:
    """CLI entry point."""
    asyncio.run(cli_main())


if __name__ == "__main__":
    cli()
