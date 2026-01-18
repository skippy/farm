"""
Analyze carbon flux and sequestration from historical NDVI data.

Usage:
    uv run python -m agriwebb.analyze_carbon
    uv run python -m agriwebb.analyze_carbon --paddock "Solstice Field"
"""

import argparse
import json
from pathlib import Path

from agriwebb.analysis.carbon import (
    calculate_gpp,
    estimate_annual_sequestration,
    PastureType,
    CO2_PER_C,
)
from agriwebb.core import get_cache_dir


def analyze_paddock_carbon(paddock_data: dict, year: int | None = None) -> dict:
    """Analyze carbon metrics for a single paddock."""
    history = paddock_data.get("history", [])

    if not history:
        return None

    # Group by year
    years_data = {}
    for record in history:
        y = record.get("year")
        if y not in years_data:
            years_data[y] = [None] * 12
        month_idx = record.get("month", 1) - 1
        ndvi = record.get("ndvi_mean")
        if ndvi is not None and ndvi >= 0:
            years_data[y][month_idx] = ndvi

    results = []

    for y, monthly_ndvi in sorted(years_data.items()):
        if year is not None and y != year:
            continue

        # Count valid months
        valid_months = sum(1 for n in monthly_ndvi if n is not None)
        if valid_months < 6:
            continue

        # Fill missing months with interpolation or neighbor values
        filled_ndvi = monthly_ndvi.copy()
        for i, n in enumerate(filled_ndvi):
            if n is None:
                # Use average of neighbors or overall mean
                neighbors = [
                    filled_ndvi[j] for j in [i-1, i+1]
                    if 0 <= j < 12 and filled_ndvi[j] is not None
                ]
                if neighbors:
                    filled_ndvi[i] = sum(neighbors) / len(neighbors)
                else:
                    # Use annual mean
                    valid = [x for x in filled_ndvi if x is not None]
                    filled_ndvi[i] = sum(valid) / len(valid) if valid else 0.2

        # Calculate annual carbon metrics
        carbon = estimate_annual_sequestration(
            filled_ndvi,
            latitude=48.5,
            pasture_type=PastureType.MODERATE,
        )

        results.append({
            "year": y,
            "valid_months": valid_months,
            "avg_ndvi": sum(n for n in monthly_ndvi if n) / valid_months,
            **carbon,
        })

    return results


def main():
    parser = argparse.ArgumentParser(description="Analyze carbon from NDVI data")
    parser.add_argument("--paddock", type=str, help="Specific paddock name to analyze")
    parser.add_argument("--year", type=int, help="Specific year to analyze")
    args = parser.parse_args()

    # Load historical data
    cache_file = get_cache_dir() / "ndvi_historical.json"
    if not cache_file.exists():
        print(f"Historical data not found: {cache_file}")
        print("Run: uv run python -m agriwebb.fetch_historical_ndvi")
        return

    with open(cache_file) as f:
        data = json.load(f)

    print("=" * 80)
    print("Carbon Flux Analysis from Satellite NDVI")
    print("=" * 80)
    print()
    print(f"Data fetched: {data.get('fetched_at')}")
    print(f"Paddocks: {data.get('paddock_count')}")
    print()

    paddocks = data.get("paddocks", {})

    # Filter to specific paddock if requested
    if args.paddock:
        paddocks = {
            pid: p for pid, p in paddocks.items()
            if args.paddock.lower() in p.get("name", "").lower()
        }
        if not paddocks:
            print(f"No paddock found matching: {args.paddock}")
            return

    # Analyze each paddock
    all_results = {}

    for pid, pdata in paddocks.items():
        name = pdata.get("name")
        area = pdata.get("area_ha", 0)

        results = analyze_paddock_carbon(pdata, args.year)
        if not results:
            continue

        all_results[pid] = {
            "name": name,
            "area_ha": area,
            "annual_data": results,
        }

    # Print summary table
    print(f"{'Paddock':<25} {'Year':>6} {'GPP':>8} {'NPP':>8} {'Seq':>8} {'Seq CO2':>10}")
    print(f"{'':25} {'':>6} {'t C/ha':>8} {'t C/ha':>8} {'t C/ha':>8} {'t CO2/ha':>10}")
    print("-" * 80)

    farm_totals = {}

    for pid, pdata in all_results.items():
        name = pdata["name"][:24]
        area = pdata["area_ha"]

        for annual in pdata["annual_data"]:
            year = annual["year"]
            gpp = annual["annual_gpp_t_c_ha"]
            npp = annual["annual_npp_t_c_ha"]
            seq = annual["soil_sequestration_t_c_ha"]
            seq_co2 = annual["soil_sequestration_t_co2_ha"]

            print(f"{name:<25} {year:>6} {gpp:>8.2f} {npp:>8.2f} {seq:>8.2f} {seq_co2:>10.2f}")

            # Accumulate farm totals
            if year not in farm_totals:
                farm_totals[year] = {"area": 0, "gpp": 0, "npp": 0, "seq": 0}
            farm_totals[year]["area"] += area
            farm_totals[year]["gpp"] += gpp * area
            farm_totals[year]["npp"] += npp * area
            farm_totals[year]["seq"] += seq * area

    print()
    print("=" * 80)
    print("Farm-Wide Annual Totals")
    print("=" * 80)
    print()
    print(f"{'Year':>6} {'Area':>8} {'Total GPP':>12} {'Total NPP':>12} {'C Sequestered':>14} {'CO2 Seq':>12}")
    print(f"{'':>6} {'(ha)':>8} {'(t C)':>12} {'(t C)':>12} {'(t C)':>14} {'(t CO2)':>12}")
    print("-" * 70)

    for year in sorted(farm_totals.keys()):
        t = farm_totals[year]
        seq_co2 = t["seq"] * CO2_PER_C
        print(f"{year:>6} {t['area']:>8.1f} {t['gpp']:>12.1f} {t['npp']:>12.1f} {t['seq']:>14.2f} {seq_co2:>12.1f}")

    # Save results
    output_file = get_cache_dir() / "carbon_analysis.json"
    with open(output_file, "w") as f:
        json.dump({
            "analyzed_at": data.get("fetched_at"),
            "paddocks": all_results,
            "farm_totals": farm_totals,
        }, f, indent=2)

    print()
    print(f"Results saved to: {output_file}")
    print()
    print("Notes:")
    print("  - GPP = Gross Primary Production (total carbon fixed by photosynthesis)")
    print("  - NPP = Net Primary Production (GPP minus plant respiration)")
    print("  - Sequestration = estimated soil carbon accumulation")
    print("  - Based on MOD17/CASA models with PNW climate assumptions")
    print("  - Actual values vary with weather, soil type, and management")


if __name__ == "__main__":
    main()
