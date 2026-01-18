#!/usr/bin/env python3
"""
Fetch birth records from AgriWebb and calculate lactation-adjusted methane emissions.

Lactating ewes have ~25% higher methane emissions due to increased feed intake.
This script:
1. Fetches all ewes and their offspring
2. Determines lactation periods (birth date + ~90 days)
3. Calculates monthly lactation counts
4. Adjusts methane emissions accordingly
"""

import asyncio
import json
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from agriwebb.livestock import get_animals, get_offspring
from agriwebb.client import graphql
from agriwebb.config import settings
from agriwebb.core import get_cache_dir

# Lactation parameters
LACTATION_DURATION_DAYS = 90  # Ewes typically lactate 3 months
LACTATION_METHANE_FACTOR = 1.25  # 25% increase during lactation


async def get_birth_records():
    """
    Fetch birth records from AgriWebb.

    Try to get birthRecords if available, otherwise derive from
    animal birth dates and dam relationships.
    """
    farm_id = settings.agriwebb_farm_id

    # First try to query birthRecords directly
    try:
        query = f"""
        {{
          birthRecords(farmId: "{farm_id}") {{
            id
            recordedAt
            birthDate
            damId
            sireId
            offspringCount
            offspringIds
          }}
        }}
        """

        result = await graphql(query)

        if "errors" not in result:
            records = result.get("data", {}).get("birthRecords", [])
            if records:
                print(f"Found {len(records)} birth records directly")
                return records
    except Exception as e:
        print(f"  birthRecords query not available: {e}")

    print("No direct birth records found, will derive from animal data...")
    return None


async def derive_births_from_offspring():
    """
    Derive birth events from offspring birth dates and dam relationships.

    Returns list of birth events with dam_id and birth_date.
    """
    # Get all animals on farm
    animals = await get_animals(status="onFarm", include_lineage=True)

    # Also get animals that may have left (sold lambs, etc.)
    all_animals = await get_animals(include_lineage=True)

    print(f"Found {len(animals)} animals on farm, {len(all_animals)} total")

    births = []

    for animal in all_animals:
        birth_date = animal.get("birthDate")
        dam = animal.get("dam")

        if birth_date and dam and dam.get("id"):
            # Handle birth_date as timestamp (ms) or string
            if isinstance(birth_date, (int, float)):
                # Convert milliseconds timestamp to ISO date string
                birth_dt = datetime.fromtimestamp(birth_date / 1000)
                birth_date_str = birth_dt.isoformat()
                birth_year = str(birth_dt.year)
            else:
                birth_date_str = str(birth_date)
                birth_year = birth_date_str[:4] if birth_date_str else None

            births.append({
                "offspring_id": animal.get("id"),
                "offspring_tag": animal.get("visualTag") or animal.get("name"),
                "dam_id": dam.get("id"),
                "dam_tag": dam.get("visualTag") or dam.get("name"),
                "birth_date": birth_date_str,
                "birth_year": birth_year
            })

    print(f"Found {len(births)} births with known dams")
    return births


async def get_ewes_on_farm():
    """Get all female sheep currently on farm."""
    animals = await get_animals(status="onFarm")

    ewes = [
        a for a in animals
        if a.get("sex") in ["FEMALE", "Female", "female", "F"]
        and a.get("species") in ["SHEEP", "Sheep", "sheep", None]  # Include if species not set
    ]

    print(f"Found {len(ewes)} ewes on farm")
    return ewes


def calculate_lactation_periods(births: list[dict]) -> dict:
    """
    Calculate when each dam was lactating based on offspring birth dates.

    Returns dict: dam_id -> list of (start_date, end_date) tuples
    """
    lactation_by_dam = defaultdict(list)

    for birth in births:
        dam_id = birth.get("dam_id")
        birth_date_str = birth.get("birth_date")

        if not dam_id or not birth_date_str:
            continue

        try:
            # Parse birth date - handle various formats
            if "T" in birth_date_str:
                birth_date = datetime.fromisoformat(birth_date_str.replace("Z", "+00:00"))
            else:
                birth_date = datetime.fromisoformat(birth_date_str)

            # Lactation period
            lactation_end = birth_date + timedelta(days=LACTATION_DURATION_DAYS)

            lactation_by_dam[dam_id].append({
                "start": birth_date,
                "end": lactation_end,
                "offspring": birth.get("offspring_tag")
            })
        except (ValueError, TypeError) as e:
            print(f"  Warning: Could not parse birth date '{birth_date_str}': {e}")

    return dict(lactation_by_dam)


def calculate_monthly_lactating_ewes(lactation_periods: dict, start_year: int = 2020, end_year: int = 2026) -> dict:
    """
    Calculate number of lactating ewes per month.

    Returns dict: "YYYY-MM" -> count of lactating ewes
    """
    monthly_counts = {}

    for year in range(start_year, end_year + 1):
        for month in range(1, 13):
            key = f"{year}-{month:02d}"

            # Check middle of month
            check_date = datetime(year, month, 15)

            count = 0
            for dam_id, periods in lactation_periods.items():
                for period in periods:
                    if period["start"] <= check_date <= period["end"]:
                        count += 1
                        break  # Count each dam only once per month

            monthly_counts[key] = count

    return monthly_counts


def calculate_annual_lactation_adjustment(monthly_counts: dict, total_ewes: int) -> dict:
    """
    Calculate annual methane adjustment factor based on lactation.

    Returns dict by year with adjustment factors.
    """
    annual_adjustments = {}

    # Group by year
    years = set(k[:4] for k in monthly_counts.keys())

    for year in sorted(years):
        year_months = {k: v for k, v in monthly_counts.items() if k.startswith(year)}

        if not year_months:
            continue

        # Calculate average lactating ewes for the year
        avg_lactating = sum(year_months.values()) / len(year_months)

        # Calculate adjustment factor
        # If X ewes are lactating out of total, and lactating ewes produce 25% more CH4:
        # adjustment = (non_lactating * 1.0 + lactating * 1.25) / total
        #            = 1.0 + (lactating/total) * 0.25

        if total_ewes > 0:
            lactation_fraction = avg_lactating / total_ewes
            adjustment_factor = 1.0 + (lactation_fraction * (LACTATION_METHANE_FACTOR - 1))
        else:
            lactation_fraction = 0
            adjustment_factor = 1.0

        annual_adjustments[year] = {
            "avg_lactating_ewes": round(avg_lactating, 1),
            "total_ewes": total_ewes,
            "lactation_fraction": round(lactation_fraction, 3),
            "methane_adjustment_factor": round(adjustment_factor, 4),
            "monthly_detail": year_months
        }

    return annual_adjustments


async def main():
    print("=" * 70)
    print("Fetching Lactation Data for Methane Adjustment")
    print("=" * 70)

    # Try direct birth records first
    print("\n1. Checking for birth records...")
    birth_records = await get_birth_records()

    # Derive from offspring if no direct records
    print("\n2. Deriving births from offspring data...")
    births = await derive_births_from_offspring()

    if not births:
        print("No birth data found. Cannot calculate lactation adjustment.")
        return

    # Get current ewes for reference count
    print("\n3. Getting current ewe count...")
    ewes = await get_ewes_on_farm()
    ewe_count = len(ewes)

    # Calculate lactation periods
    print("\n4. Calculating lactation periods...")
    lactation_periods = calculate_lactation_periods(births)
    print(f"   Found lactation data for {len(lactation_periods)} dams")

    # Calculate monthly counts
    print("\n5. Calculating monthly lactating ewe counts...")
    monthly_counts = calculate_monthly_lactating_ewes(lactation_periods, 2020, 2026)

    # Calculate annual adjustments
    print("\n6. Calculating annual methane adjustments...")
    annual_adjustments = calculate_annual_lactation_adjustment(monthly_counts, ewe_count)

    # Print summary
    print("\n" + "=" * 70)
    print("LACTATION SUMMARY BY YEAR")
    print("=" * 70)
    print(f"\n{'Year':<8} {'Avg Lactating':<15} {'Fraction':<12} {'CH4 Adjustment'}")
    print("-" * 55)

    for year, data in sorted(annual_adjustments.items()):
        print(f"{year:<8} {data['avg_lactating_ewes']:>10.1f}     {data['lactation_fraction']:>8.1%}     {data['methane_adjustment_factor']:>10.2%}")

    # Calculate overall average adjustment
    recent_years = [y for y in annual_adjustments.keys() if int(y) >= 2022]
    if recent_years:
        avg_adjustment = sum(annual_adjustments[y]['methane_adjustment_factor'] for y in recent_years) / len(recent_years)
        print("-" * 55)
        print(f"{'Avg (2022+)':<8} {'':<15} {'':<12} {avg_adjustment:>10.2%}")

    # Save results
    output = {
        "generated_at": datetime.now().isoformat(),
        "parameters": {
            "lactation_duration_days": LACTATION_DURATION_DAYS,
            "lactation_methane_factor": LACTATION_METHANE_FACTOR
        },
        "current_ewe_count": ewe_count,
        "births_with_known_dams": len(births),
        "dams_with_lactation_data": len(lactation_periods),
        "annual_adjustments": annual_adjustments,
        "birth_records": births[:50],  # Sample of birth records
    }

    output_path = get_cache_dir() / "lactation_data.json"
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2, default=str)

    print(f"\nData saved to: {output_path}")

    # Calculate impact on current emissions
    print("\n" + "=" * 70)
    print("IMPACT ON METHANE EMISSIONS")
    print("=" * 70)

    # Load current report for comparison
    try:
        with open(get_cache_dir() / "carbon_balance_report.json") as f:
            report = json.load(f)

        current_ch4 = report['livestock_emissions']['total_ch4_kg_year']
        current_co2eq = report['livestock_emissions']['total_co2eq_t_year']

        # Apply lactation adjustment
        if recent_years:
            adjusted_ch4 = current_ch4 * avg_adjustment
            adjusted_co2eq = current_co2eq * avg_adjustment

            print(f"\nCurrent methane estimate: {current_ch4:.1f} kg CH4/yr ({current_co2eq:.1f} t CO2eq)")
            print(f"Lactation adjustment factor: {avg_adjustment:.2%}")
            print(f"Adjusted methane estimate: {adjusted_ch4:.1f} kg CH4/yr ({adjusted_co2eq:.1f} t CO2eq)")
            print(f"Change: +{adjusted_ch4 - current_ch4:.1f} kg CH4 (+{adjusted_co2eq - current_co2eq:.1f} t CO2eq)")

    except FileNotFoundError:
        print("Could not load carbon balance report for comparison")

    return output


if __name__ == "__main__":
    asyncio.run(main())
