"""
Fetch historical NDVI data for all paddocks.

Pulls monthly NDVI from 2018 onwards and saves to cache.

Usage:
    uv run python -m agriwebb.fetch_historical_ndvi
"""

import asyncio
import json
from datetime import date, timedelta
from typing import TypedDict

from agriwebb.core import get_cache_dir, get_fields, settings
from agriwebb.satellite import gee as satellite


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


async def fetch_paddock_history(paddock: dict, start_year: int = 2018) -> list[dict]:
    """Fetch monthly NDVI history for a single paddock."""
    results = []
    current_year = date.today().year
    current_month = date.today().month

    for year in range(start_year, current_year + 1):
        for month in range(1, 13):
            # Skip future months
            if year == current_year and month > current_month:
                break

            start = date(year, month, 1)
            # End of month
            if month == 12:
                end = date(year + 1, 1, 1) - timedelta(days=1)
            else:
                end = date(year, month + 1, 1) - timedelta(days=1)

            try:
                result = satellite.extract_paddock_ndvi(
                    paddock,
                    start.isoformat(),
                    end.isoformat(),
                    scale=30,
                )

                results.append(
                    {
                        "date": start.isoformat(),
                        "year": year,
                        "month": month,
                        "ndvi_mean": result["ndvi_mean"],
                        "ndvi_stddev": result["ndvi_stddev"],
                        "pixel_count": result["pixel_count"],
                        "cloud_free_pct": result["cloud_free_pct"],
                    }
                )

            except Exception as e:
                results.append(
                    {
                        "date": start.isoformat(),
                        "year": year,
                        "month": month,
                        "ndvi_mean": None,
                        "ndvi_stddev": None,
                        "pixel_count": 0,
                        "cloud_free_pct": 0,
                        "error": str(e),
                    }
                )

    return results


async def main():
    print("=" * 70)
    print("Historical NDVI Data Fetch")
    print("=" * 70)
    print()

    # Ensure cache directory exists
    get_cache_dir().mkdir(parents=True, exist_ok=True)

    # Initialize GEE
    print("Initializing Google Earth Engine...")
    satellite.initialize(project=settings.gee_project_id)

    # Fetch paddocks
    print("Fetching paddocks from AgriWebb...")
    paddocks = await get_fields(min_area_ha=0.2)
    print(f"Found {len(paddocks)} paddocks")
    print()

    # Fetch historical data for each paddock
    all_data: NDVIHistoricalData = {
        "fetched_at": date.today().isoformat(),
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

        try:
            history = await fetch_paddock_history(paddock)
            valid_count = sum(1 for r in history if r["ndvi_mean"] is not None)
            print(f"{valid_count} months of data")

            all_data["paddocks"][pid] = {
                "name": name,
                "area_ha": paddock.get("totalArea"),
                "land_use": paddock.get("landUse"),
                "history": history,
            }

        except Exception as e:
            print(f"error: {e}")

    # Save to cache
    output_file = get_cache_dir() / "ndvi_historical.json"
    with open(output_file, "w") as f:
        json.dump(all_data, f, indent=2)

    print()
    print(f"Data saved to: {output_file}")

    # Summary stats
    total_records = sum(len(p["history"]) for p in all_data["paddocks"].values())
    valid_records = sum(
        sum(1 for r in p["history"] if r["ndvi_mean"] is not None) for p in all_data["paddocks"].values()
    )

    print(f"Total records: {total_records}")
    print(f"Valid records: {valid_records}")
    print(f"Coverage: {valid_records / total_records * 100:.1f}%")


if __name__ == "__main__":
    asyncio.run(main())
