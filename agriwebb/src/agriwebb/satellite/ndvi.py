"""
Fetch NDVI data for all paddocks using HLS via Google Earth Engine.

Usage:
    uv run python -m agriwebb.fetch_ndvi

First run will prompt for GEE authentication.
"""

import asyncio
import json
from datetime import date, timedelta

from agriwebb.core import get_cache_dir, get_fields, settings
from agriwebb.satellite import gee as satellite


async def main():
    # Ensure cache directory exists
    get_cache_dir().mkdir(parents=True, exist_ok=True)

    print("Initializing Google Earth Engine...")
    print("(First run will open browser for authentication)\n")

    # Initialize GEE - uses service account if GEE_SERVICE_ACCOUNT_KEY env var is set
    satellite.initialize(project=settings.gee_project_id)

    print("Fetching paddocks from AgriWebb...")
    paddocks = await get_fields(min_area_ha=0.2)
    print(f"Found {len(paddocks)} paddocks\n")

    # Calculate date range - use 60 days to ensure we get cloud-free pixels
    # HLS data has significant processing lag, plus PNW is very cloudy in winter
    end_date = date.today()
    start_date = end_date - timedelta(days=60)

    print(f"Fetching NDVI composite for {start_date} to {end_date}...")
    print("(This may take a minute - querying satellite data)\n")

    # Extract NDVI for all paddocks
    results = satellite.extract_all_paddocks_ndvi(
        paddocks,
        start_date.isoformat(),
        end_date.isoformat(),
    )

    # Display results
    print("-" * 80)
    print(f"{'Paddock':<25} {'NDVI':>8} {'StdDev':>8} {'Pixels':>8} {'Trees%':>8} {'Cloud%':>8}")
    print("-" * 80)

    for r in sorted(results, key=lambda x: x["ndvi_mean"] or 0, reverse=True):
        ndvi = f"{r['ndvi_mean']:.3f}" if r["ndvi_mean"] is not None else "N/A"
        stddev = f"{r['ndvi_stddev']:.3f}" if r["ndvi_stddev"] is not None else "N/A"
        tree_pct = f"{r['tree_cover_pct']:.1f}" if r.get("tree_cover_pct") is not None else "N/A"
        print(
            f"{r['paddock_name']:<25} {ndvi:>8} {stddev:>8} "
            f"{r['pixel_count']:>8} {tree_pct:>7}% {r['cloud_free_pct']:>7.1f}%"
        )

    # Save results
    output_file = get_cache_dir() / "ndvi_results.json"
    with open(output_file, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to {output_file}")

    # Summary statistics
    valid_results = [r for r in results if r["ndvi_mean"] is not None]
    if valid_results:
        avg_ndvi = sum(r["ndvi_mean"] for r in valid_results) / len(valid_results)
        avg_cloud = sum(r["cloud_free_pct"] for r in valid_results) / len(valid_results)
        print("\nSummary:")
        print(f"  Paddocks with data: {len(valid_results)}/{len(results)}")
        print(f"  Average NDVI: {avg_ndvi:.3f}")
        print(f"  Average cloud-free: {avg_cloud:.1f}%")


if __name__ == "__main__":
    asyncio.run(main())
