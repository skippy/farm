"""Test biomass conversion with Solstice Field data."""

from agriwebb.pasture.biomass import (
    EXPECTED_UNCERTAINTY,
    calculate_growth_rate,
    get_season,
    ndvi_to_standing_dry_matter,
)


def main():
    # Solstice Field monthly NDVI data (from our analysis)
    monthly_data = [
        (1, 0.364),  # Jan
        (2, 0.312),  # Feb
        (3, 0.356),  # Mar
        (4, 0.695),  # Apr - peak!
        (5, 0.485),  # May
        (6, 0.370),  # Jun
        (7, 0.160),  # Jul
        (8, 0.122),  # Aug - low
        (9, 0.138),  # Sep
        (10, 0.449),  # Oct - recovery
        (11, 0.198),  # Nov
    ]

    print("Solstice Field - NDVI to Biomass Conversion")
    print("=" * 70)
    print()
    print(f"{'Month':<8} {'NDVI':>6} {'Season':<10} {'SDM (kg/ha)':>12} {'Model Used':<25}")
    print("-" * 70)

    for month, ndvi in monthly_data:
        season = get_season(month)
        sdm, model = ndvi_to_standing_dry_matter(ndvi, month)
        month_name = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][month - 1]
        print(f"{month_name:<8} {ndvi:>6.3f} {season.value:<10} {sdm:>12,.0f} {model.name:<25}")

    print()
    print("Growth Rates (kg DM/ha/day)")
    print("=" * 70)
    print()
    print(f"{'Period':<15} {'NDVI Change':>12} {'Growth Rate':>14} {'Notes':<30}")
    print("-" * 70)

    for i in range(1, len(monthly_data)):
        month_prev, ndvi_prev = monthly_data[i - 1]
        month_curr, ndvi_curr = monthly_data[i]

        growth_rate, notes = calculate_growth_rate(
            ndvi_curr,
            ndvi_prev,
            days_between=30,
            month_current=month_curr,
            month_previous=month_prev,
        )

        month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        period = f"{month_names[month_prev - 1]}→{month_names[month_curr - 1]}"
        ndvi_change = f"{ndvi_prev:.2f}→{ndvi_curr:.2f}"

        # Interpret the growth rate
        if growth_rate > 50:
            interp = "🌱 Rapid growth"
        elif growth_rate > 20:
            interp = "📈 Good growth"
        elif growth_rate > 0:
            interp = "→ Slow growth"
        elif growth_rate > -20:
            interp = "→ Stable/slight loss"
        else:
            interp = "📉 Senescence"

        print(f"{period:<15} {ndvi_change:>12} {growth_rate:>+10.1f}     {interp:<30}")

    print()
    print("Model Uncertainty (from literature):")
    print(f"  - SDM estimate: ±{EXPECTED_UNCERTAINTY['sdm_error_kg_ha']} kg DM/ha")
    print(f"  - Growth rate: ±{EXPECTED_UNCERTAINTY['growth_rate_error_kg_ha_day']} kg DM/ha/day")
    print()
    print("⚠️  These are ESTIMATES using generic temperate pasture models.")
    print("   Local calibration with actual harvest data will improve accuracy.")


if __name__ == "__main__":
    main()
