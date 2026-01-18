"""
Sync Feed on Offer (FOO) from satellite NDVI to AgriWebb.

FOO is the total available pasture (kg DM/ha) at a point in time.
This is different from growth rate, which is the daily change.

Includes grazing pressure adjustment to correct for NDVI's inability to
detect grass height (grazed paddocks have similar greenness but less biomass).

Usage:
    uv run python -m agriwebb.sync_foo              # Show current FOO estimates
    uv run python -m agriwebb.sync_foo --sync       # Push to AgriWebb
    uv run python -m agriwebb.sync_foo --dry-run    # Show what would be pushed
    uv run python -m agriwebb.sync_foo --no-grazing-adjust  # Skip grazing correction
"""

import argparse
import asyncio
import json
from datetime import date

from agriwebb.core import add_feed_on_offer_batch, add_standing_dry_matter_batch, get_cache_dir
from agriwebb.data.grazing import calculate_paddock_consumption, load_farm_data, load_fields
from agriwebb.pasture.biomass import (
    EXPECTED_UNCERTAINTY,
    adjust_foo_for_grazing,
    get_season,
    ndvi_to_standing_dry_matter,
)
from agriwebb.satellite.moss import get_all_paddock_moss


def load_ndvi_data() -> list[dict]:
    """Load latest NDVI results."""
    ndvi_path = get_cache_dir() / "ndvi_results.json"
    if not ndvi_path.exists():
        raise FileNotFoundError(f"NDVI data not found at {ndvi_path}")

    with open(ndvi_path) as f:
        return json.load(f)


def load_field_mapping() -> dict[str, str]:
    """Load paddock name to field ID mapping."""
    fields_path = get_cache_dir() / "fields.json"
    with open(fields_path) as f:
        data = json.load(f)

    if isinstance(data, list):
        return {f["name"]: f["id"] for f in data}
    return {f["name"]: f["id"] for f in data.get("fields", [])}


def get_grazing_consumption() -> dict[str, dict]:
    """
    Get current grazing consumption by paddock.

    Returns:
        Dict of paddock_id -> consumption data including intake_per_ha_kg_day
    """
    try:
        data = load_farm_data()
        animals = data.get("animals", [])
        fields = load_fields()
        return calculate_paddock_consumption(animals, fields)
    except Exception as e:
        print(f"Warning: Could not load grazing data: {e}")
        return {}


def calculate_foo_from_ndvi(
    ndvi_data: list[dict],
    reference_date: date | None = None,
    apply_grazing_adjustment: bool = True,
    apply_moss_adjustment: bool = False,  # Disabled by default - requires manual calibration
) -> list[dict]:
    """
    Calculate FOO (Feed on Offer) from NDVI data.

    Args:
        ndvi_data: List of NDVI records per paddock
        reference_date: Date for seasonal model selection
        apply_grazing_adjustment: If True, adjust FOO based on grazing pressure
        apply_moss_adjustment: If True, adjust FOO based on estimated moss cover

    Returns:
        List of FOO records with quality flags
    """
    if reference_date is None:
        reference_date = date.today()

    month = reference_date.month
    results = []

    # Get grazing consumption data if adjustment is enabled
    grazing_data = get_grazing_consumption() if apply_grazing_adjustment else {}

    # Get moss estimates if adjustment is enabled
    moss_data = {}
    if apply_moss_adjustment:
        try:
            moss_data = get_all_paddock_moss()
        except Exception as e:
            print(f"Warning: Could not load moss data: {e}")

    for record in ndvi_data:
        paddock_id = record.get("paddock_id", "")
        paddock_name = record.get("paddock_name", "Unknown")
        ndvi = record.get("ndvi_mean", 0)
        ndvi_std = record.get("ndvi_stddev", 0)
        tree_cover_pct = record.get("tree_cover_pct")

        # Quality flags
        quality_flags = []

        # Invalid NDVI
        if ndvi < 0:
            quality_flags.append("negative_ndvi")
            ndvi = 0  # Treat as bare
        elif ndvi > 1:
            quality_flags.append("ndvi_over_1")
            ndvi = min(ndvi, 0.8)  # Cap at reasonable max

        # High variability suggests mixed pixels
        if ndvi_std > 0.3:
            quality_flags.append("high_variance")

        # Very low NDVI (bare soil, water, or dormant)
        if 0 < ndvi < 0.1:
            quality_flags.append("near_bare")

        # High tree cover (>20%) may affect accuracy
        if tree_cover_pct is not None and tree_cover_pct > 20:
            quality_flags.append("high_tree_cover")

        # Convert to SDM
        sdm, model = ndvi_to_standing_dry_matter(ndvi, month=month)

        # Estimate FOO as percentage of SDM (utilization factor)
        # Typical: 70-85% of SDM is usable feed
        utilization = 0.75  # Conservative estimate
        foo_raw = sdm * utilization

        # Apply grazing pressure adjustment
        grazing_pressure = 0.0
        grazing_correction = 1.0
        consumption = grazing_data.get(paddock_id)

        if apply_grazing_adjustment:
            if consumption:
                grazing_pressure = consumption.get("intake_per_ha_kg_day", 0)
                foo_after_grazing, grazing_correction = adjust_foo_for_grazing(
                    foo_raw, grazing_pressure
                )
            else:
                # No animals currently grazing - use base correction (0.85)
                foo_after_grazing, grazing_correction = adjust_foo_for_grazing(foo_raw, 0)
        else:
            foo_after_grazing = foo_raw

        # Apply moss adjustment
        moss_fraction = 0.0
        moss_correction = 1.0
        moss_estimate = moss_data.get(paddock_id)

        if moss_estimate:
            moss_fraction = moss_estimate.get("moss_fraction", 0)
            moss_correction = moss_estimate.get("correction_factor", 1.0)

        # High moss coverage flag
        if moss_fraction > 0.25:
            quality_flags.append("high_moss")

        # Final FOO with both corrections applied
        # Moss correction applied to the grazing-adjusted FOO
        foo_final = foo_after_grazing * moss_correction

        results.append({
            "paddock_id": paddock_id,
            "paddock_name": paddock_name,
            "ndvi": round(ndvi, 3),
            "ndvi_std": round(ndvi_std, 3),
            "tree_cover_pct": tree_cover_pct,
            "sdm_kg_ha": round(sdm, 0),
            "foo_raw_kg_ha": round(foo_raw, 0),  # Before any adjustment
            "foo_kg_ha": round(foo_final, 0),  # After all adjustments
            "grazing_pressure_kg_ha_day": round(grazing_pressure, 1),
            "grazing_correction": grazing_correction,
            "moss_fraction": round(moss_fraction, 2),
            "moss_correction": moss_correction,
            "model": model.name,
            "quality_flags": quality_flags,
            "date": reference_date.isoformat(),
            "uncertainty_kg_ha": EXPECTED_UNCERTAINTY["sdm_error_kg_ha"],
        })

    return results


async def sync_foo_to_agriwebb(
    foo_data: list[dict],
    dry_run: bool = False,
    push_sdm: bool = False,
) -> dict:
    """
    Push FOO/SDM data to AgriWebb.

    Args:
        foo_data: FOO records from calculate_foo_from_ndvi()
        dry_run: If True, don't actually push
        push_sdm: If True, push SDM instead of FOO

    Returns:
        Sync result
    """
    # Filter out low-quality records
    good_records = [
        r for r in foo_data
        if "negative_ndvi" not in r["quality_flags"]
        and "ndvi_over_1" not in r["quality_flags"]
    ]

    print(f"\nRecords to sync: {len(good_records)} of {len(foo_data)}")
    print(f"Skipped: {len(foo_data) - len(good_records)} (quality issues)")

    if not good_records:
        return {"error": "No valid records to sync"}

    records = [
        {
            "field_id": r["paddock_id"],
            "foo_kg_ha" if not push_sdm else "sdm_kg_ha": r["foo_kg_ha" if not push_sdm else "sdm_kg_ha"],
            "record_date": r["date"],
        }
        for r in good_records
    ]

    if dry_run:
        print("\n[DRY RUN] Would push:")
        for r in records[:10]:
            print(f"  {r}")
        if len(records) > 10:
            print(f"  ... and {len(records) - 10} more")
        return {"dry_run": True, "records": len(records)}

    print("\nPushing to AgriWebb...")
    if push_sdm:
        result = await add_standing_dry_matter_batch(records)
    else:
        result = await add_feed_on_offer_batch(records, source="IOT")

    return result


async def main():
    parser = argparse.ArgumentParser(
        description="Sync Feed on Offer from satellite NDVI to AgriWebb"
    )
    parser.add_argument(
        "--sync",
        action="store_true",
        help="Push FOO to AgriWebb",
    )
    parser.add_argument(
        "--sdm",
        action="store_true",
        help="Push as SDM instead of FOO",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be pushed without pushing",
    )
    parser.add_argument(
        "--no-grazing-adjust",
        action="store_true",
        help="Skip grazing pressure adjustment (use raw NDVI estimates)",
    )
    parser.add_argument(
        "--no-moss-adjust",
        action="store_true",
        help="Skip moss/evergreen adjustment",
    )
    args = parser.parse_args()

    apply_grazing = not args.no_grazing_adjust
    apply_moss = False  # Moss correction disabled - requires manual calibration

    print("=" * 80)
    print("Feed on Offer (FOO) from Satellite NDVI")
    if apply_grazing:
        print("(with grazing pressure adjustment)")
    print("=" * 80)

    # Load data
    try:
        ndvi_data = load_ndvi_data()
        print(f"\nLoaded {len(ndvi_data)} paddocks from NDVI data")
    except FileNotFoundError as e:
        print(f"Error: {e}")
        print("Run satellite NDVI analysis first:")
        print("  uv run python -m agriwebb.fetch_ndvi")
        return

    # Calculate FOO
    today = date.today()
    foo_data = calculate_foo_from_ndvi(
        ndvi_data, today,
        apply_grazing_adjustment=apply_grazing,
        apply_moss_adjustment=apply_moss,
    )

    # Display results
    print(f"\nFeed on Offer Estimates ({today}, {get_season(today.month).value} model)")
    if apply_grazing:
        print(f"{'Paddock':<25} {'NDVI':>6} {'Trees':>6} {'Raw':>7} {'Adj':>7} {'Grz':>5} {'Corr':>5} {'Flags'}")
        print("-" * 80)
    else:
        print(f"{'Paddock':<25} {'NDVI':>7} {'SDM':>8} {'FOO':>8} {'Flags'}")
        print("-" * 70)

    # Sort by adjusted FOO descending
    sorted_foo = sorted(foo_data, key=lambda x: x["foo_kg_ha"], reverse=True)

    for r in sorted_foo:
        flags = ",".join(r["quality_flags"]) if r["quality_flags"] else "ok"
        tree_pct = f"{r['tree_cover_pct']:.0f}%" if r.get("tree_cover_pct") is not None else "-"

        if apply_grazing:
            grz = r.get("grazing_pressure_kg_ha_day", 0)
            grz_corr = r.get("grazing_correction", 1.0)
            print(
                f"{r['paddock_name']:<25} "
                f"{r['ndvi']:>6.3f} "
                f"{tree_pct:>6} "
                f"{r['foo_raw_kg_ha']:>6.0f}  "
                f"{r['foo_kg_ha']:>6.0f}  "
                f"{grz:>5.0f} "
                f"{grz_corr:>5.2f} "
                f"{flags}"
            )
        else:
            print(
                f"{r['paddock_name']:<25} "
                f"{r['ndvi']:>7.3f} "
                f"{r['sdm_kg_ha']:>6.0f}   "
                f"{r['foo_kg_ha']:>6.0f}   "
                f"{flags}"
            )

    # Summary stats
    valid = [r for r in foo_data if not r["quality_flags"]]
    if valid:
        avg_foo = sum(r["foo_kg_ha"] for r in valid) / len(valid)
        avg_raw = sum(r.get("foo_raw_kg_ha", r["foo_kg_ha"]) for r in valid) / len(valid)
        print(f"\n--- Summary ({len(valid)} paddocks with good data) ---")
        if apply_grazing:
            print(f"Average FOO (raw NDVI):    {avg_raw:.0f} kg DM/ha")
            print(f"Average FOO (adjusted):    {avg_foo:.0f} kg DM/ha")
            print(f"Grazing correction effect: {(1 - avg_foo/avg_raw)*100:.0f}% reduction")
        else:
            print(f"Average FOO: {avg_foo:.0f} kg DM/ha")
        print(f"Model uncertainty: ±{EXPECTED_UNCERTAINTY['sdm_error_kg_ha']} kg/ha")

    # Sync if requested
    if args.sync or args.dry_run:
        result = await sync_foo_to_agriwebb(
            foo_data,
            dry_run=args.dry_run,
            push_sdm=args.sdm,
        )
        if not args.dry_run:
            if "errors" in str(result):
                print(f"Sync failed: {result}")
            else:
                valid_count = len([r for r in foo_data if 'negative_ndvi' not in r['quality_flags']])
                print(f"Sync complete: {valid_count} records pushed")


def cli():
    asyncio.run(main())


if __name__ == "__main__":
    cli()
