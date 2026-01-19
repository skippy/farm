"""Analyze historical NDVI for fields."""

import asyncio
from datetime import date, timedelta

from agriwebb.core import get_fields, settings
from agriwebb.satellite import gee as satellite


async def analyze_field(paddock: dict, end_date: date) -> list[dict]:
    """Get monthly NDVI for a paddock over 12 months."""
    results = []

    for months_back in range(12, 0, -1):
        month_end = end_date - timedelta(days=30 * (months_back - 1))
        month_start = month_end - timedelta(days=30)

        try:
            result = satellite.extract_paddock_ndvi(
                paddock,
                month_start.isoformat(),
                month_end.isoformat(),
                scale=30,
            )
            results.append(
                {
                    "month": month_start.strftime("%b %Y"),
                    "ndvi": result["ndvi_mean"],
                    "stddev": result["ndvi_stddev"],
                    "pixels": result["pixel_count"],
                }
            )
        except Exception as e:
            results.append(
                {
                    "month": month_start.strftime("%b %Y"),
                    "ndvi": None,
                    "stddev": None,
                    "pixels": 0,
                    "error": str(e),
                }
            )

    return results


async def main():
    print("Initializing GEE...")
    satellite.initialize(project=settings.gee_project_id)

    print("Fetching paddocks from AgriWebb...")
    paddocks = await get_fields(min_area_ha=0.2)

    # Find the two fields to compare
    solstice = next((p for p in paddocks if "solstice" in p["name"].lower()), None)
    okf_hay = next((p for p in paddocks if p["name"] == "OKF-Hay Field"), None)

    if not solstice or not okf_hay:
        print("Could not find both fields!")
        return

    print("\nComparing:")
    print(f"  1. {solstice['name']} ({solstice['totalArea']:.1f} ha) - {solstice.get('landUse')}")
    print(f"  2. {okf_hay['name']} ({okf_hay['totalArea']:.1f} ha) - {okf_hay.get('landUse')}")
    print()

    end = date.today()

    print("Fetching 12 months of data for both fields...")
    print()

    # Fetch data for both
    solstice_data = await analyze_field(solstice, end)
    okf_hay_data = await analyze_field(okf_hay, end)

    # Print comparison table
    print(f"{'Month':<12} {'Solstice':>10} {'OKF-Hay':>10} {'Diff':>10}")
    print("-" * 44)

    for s, h in zip(solstice_data, okf_hay_data, strict=True):
        s_ndvi = f"{s['ndvi']:.3f}" if s["ndvi"] is not None else "N/A"
        h_ndvi = f"{h['ndvi']:.3f}" if h["ndvi"] is not None else "N/A"

        if s["ndvi"] is not None and h["ndvi"] is not None:
            diff = s["ndvi"] - h["ndvi"]
            diff_str = f"{diff:+.3f}"
        else:
            diff_str = "-"

        print(f"{s['month']:<12} {s_ndvi:>10} {h_ndvi:>10} {diff_str:>10}")

    print()
    print("Positive diff = Solstice greener, Negative = OKF-Hay greener")


if __name__ == "__main__":
    asyncio.run(main())
