"""
Moss/evergreen vegetation detection using seasonal NDVI comparison and soil drainage.

Moss and other non-grazable evergreen vegetation:
- Stays green year-round (low seasonal NDVI variation)
- Thrives in poorly drained soils
- Contributes to NDVI but not to Feed on Offer

This module estimates moss fraction to correct FOO calculations.
"""

import json
from pathlib import Path
from typing import TypedDict

from agriwebb.core import get_cache_dir

# Manual moss overrides for paddocks with known values
# Set by ground-truthing - overrides the model estimate
# Format: paddock_name -> moss_fraction (0.0 to 1.0)
MANUAL_MOSS_OVERRIDES: dict[str, float] = {
    "Lauren": 0.05,           # 5% - productive pasture
    "Solstice Field": 0.18,   # 15-20% - some mossy areas
    "OKF-Hay Field": 0.00,    # 0% - no moss
    "OKF-South Field": 0.03,  # 2-3% - minimal moss
}


class MossEstimate(TypedDict):
    """Moss estimation result for a paddock."""
    paddock_id: str
    paddock_name: str
    seasonality_index: float | None  # 0 = evergreen, 1 = fully seasonal
    drainage_score: float  # 0 = poorly drained, 1 = well drained
    moss_fraction: float  # Estimated fraction of ground cover that is moss
    correction_factor: float  # Multiply FOO by this to correct for moss
    data_quality: str  # "good", "limited", "insufficient"


# Soil drainage classes from SSURGO, mapped to drainage scores
# Lower score = poorer drainage = more likely moss
DRAINAGE_SCORES = {
    "Very poorly drained": 0.0,
    "Poorly drained": 0.1,
    "Somewhat poorly drained": 0.3,
    "Moderately well drained": 0.5,
    "Well drained": 0.8,
    "Somewhat excessively drained": 0.9,
    "Excessively drained": 1.0,
}

# Hydrologic groups as fallback (A=best drainage, D=worst)
HYDGRP_SCORES = {
    "A": 1.0,
    "A/D": 0.7,
    "B": 0.8,
    "B/D": 0.5,
    "C": 0.4,
    "C/D": 0.3,
    "D": 0.1,
}


def load_historical_ndvi() -> dict:
    """Load cached historical NDVI data."""
    path = get_cache_dir() / "ndvi_historical.json"
    if not path.exists():
        return {}
    with open(path) as f:
        return json.load(f)


def load_soil_data() -> dict:
    """Load cached soil data."""
    path = get_cache_dir() / "paddock_soils.json"
    if not path.exists():
        return {}
    with open(path) as f:
        return json.load(f)


def calculate_seasonality_index(history: list[dict]) -> tuple[float | None, str]:
    """
    Calculate seasonality index from NDVI history.

    Compares peak growing season (May-July) to dormant season (Dec-Feb).

    Returns:
        Tuple of (seasonality_index, data_quality)
        - seasonality_index: 0 = evergreen (no change), 1 = fully seasonal
        - data_quality: "good" (3+ years), "limited" (1-2 years), "insufficient"
    """
    # Group by season
    summer_values = []  # May, June, July
    winter_values = []  # December, January, February

    for record in history:
        ndvi = record.get("ndvi_mean")
        if ndvi is None or ndvi < 0:
            continue

        month = record.get("month")
        if month in (5, 6, 7):
            summer_values.append(ndvi)
        elif month in (12, 1, 2):
            winter_values.append(ndvi)

    # Need at least some data from both seasons
    if len(summer_values) < 2 or len(winter_values) < 2:
        return None, "insufficient"

    # Calculate averages
    summer_avg = sum(summer_values) / len(summer_values)
    winter_avg = sum(winter_values) / len(winter_values)

    # Seasonality = (summer - winter) / summer
    # High value = grass (goes dormant in winter)
    # Low value = evergreen/moss (stays green)
    if summer_avg <= 0:
        return None, "insufficient"

    seasonality = (summer_avg - winter_avg) / summer_avg

    # Clamp to 0-1 range
    seasonality = max(0.0, min(1.0, seasonality))

    # Determine data quality based on years of data
    years = len(set(r.get("year") for r in history if r.get("ndvi_mean") is not None))
    if years >= 3:
        quality = "good"
    elif years >= 1:
        quality = "limited"
    else:
        quality = "insufficient"

    return round(seasonality, 3), quality


def get_drainage_score(soil_data: dict) -> float:
    """
    Get drainage score from soil data.

    Returns 0-1 where 0 = poorly drained (moss likely), 1 = well drained
    """
    if not soil_data:
        return 0.5  # Default to moderate

    soil = soil_data.get("soil", {})

    # Try drainage class first
    drainage = soil.get("drainage", "")
    if drainage in DRAINAGE_SCORES:
        return DRAINAGE_SCORES[drainage]

    # Fall back to hydrologic group
    hydgrp = soil.get("hydgrp", "")
    if hydgrp in HYDGRP_SCORES:
        return HYDGRP_SCORES[hydgrp]

    return 0.5  # Default


def estimate_moss_fraction(
    seasonality_index: float | None,
    drainage_score: float,
) -> float:
    """
    Estimate moss fraction from seasonality and drainage.

    The model:
    - Low seasonality (evergreen) + poor drainage = higher moss
    - High seasonality (seasonal grass) + good drainage = lower moss

    Note: PNW coastal climate keeps grass relatively green year-round,
    so seasonality alone is not a strong indicator. We use it as a
    modifier on drainage-based estimates.

    Returns fraction 0-1 representing estimated moss ground cover.
    """
    # Base moss estimate from drainage
    # Poor drainage is the primary driver of moss in PNW
    # drainage_score 0 (very poor) -> 0.35 base moss
    # drainage_score 0.3 (somewhat poor) -> 0.25 base moss
    # drainage_score 0.5 (moderate) -> 0.15 base moss
    # drainage_score 0.8 (well drained) -> 0.05 base moss
    base_moss = 0.35 - (drainage_score * 0.35)

    # Seasonality modifier
    # In PNW, typical grass seasonality is 0.2-0.5 (not fully dormant)
    # Very low seasonality (<0.1) suggests more evergreen cover
    # This adds up to 0.15 additional moss for very low seasonality
    if seasonality_index is not None:
        # Map seasonality to moss modifier
        # seasonality 0.0 -> +0.15 moss
        # seasonality 0.3 -> +0.05 moss (typical PNW grass)
        # seasonality 0.5+ -> +0.00 moss
        if seasonality_index < 0.3:
            seasonal_modifier = 0.15 * (1 - seasonality_index / 0.3)
        else:
            seasonal_modifier = 0.0
    else:
        seasonal_modifier = 0.05  # Default small addition if no data

    moss = base_moss + seasonal_modifier

    # Cap at reasonable maximum (most paddocks aren't >40% moss)
    return round(min(0.40, max(0.0, moss)), 2)


def calculate_moss_correction(moss_fraction: float) -> float:
    """
    Calculate FOO correction factor based on moss fraction.

    The moss area contributes to NDVI but not to FOO.
    Correction = 1 - moss_fraction

    Example:
        moss_fraction = 0.3 -> correction = 0.7
        FOO_corrected = FOO_raw * 0.7
    """
    return round(1 - moss_fraction, 2)


def estimate_paddock_moss(
    paddock_id: str,
    paddock_name: str,
    ndvi_history: list[dict] | None = None,
    soil_data: dict | None = None,
) -> MossEstimate:
    """
    Estimate moss fraction for a single paddock.

    Uses manual override if available, otherwise falls back to
    a conservative default (5% moss).

    Args:
        paddock_id: Paddock ID
        paddock_name: Paddock name for display
        ndvi_history: Historical NDVI records (optional, will load from cache)
        soil_data: Soil properties (optional, will load from cache)

    Returns:
        MossEstimate with seasonality, drainage, moss fraction, and correction
    """
    # Check for manual override first
    if paddock_name in MANUAL_MOSS_OVERRIDES:
        moss = MANUAL_MOSS_OVERRIDES[paddock_name]
        correction = calculate_moss_correction(moss)
        return MossEstimate(
            paddock_id=paddock_id,
            paddock_name=paddock_name,
            seasonality_index=None,
            drainage_score=0.5,
            moss_fraction=moss,
            correction_factor=correction,
            data_quality="manual",
        )

    # Load data if not provided
    if ndvi_history is None:
        all_ndvi = load_historical_ndvi()
        paddock_data = all_ndvi.get("paddocks", {}).get(paddock_id, {})
        ndvi_history = paddock_data.get("history", [])

    if soil_data is None:
        all_soils = load_soil_data()
        # Soils are keyed by name, not ID
        soil_data = all_soils.get("paddocks", {}).get(paddock_name, {})

    # Calculate seasonality
    seasonality, quality = calculate_seasonality_index(ndvi_history)

    # Get drainage score
    drainage = get_drainage_score(soil_data)

    # Use conservative default: 5% moss for unknown paddocks
    # The drainage/seasonality model overestimates, so we use
    # a simple default until manually calibrated
    moss = 0.05

    # Calculate correction
    correction = calculate_moss_correction(moss)

    return MossEstimate(
        paddock_id=paddock_id,
        paddock_name=paddock_name,
        seasonality_index=seasonality,
        drainage_score=round(drainage, 2),
        moss_fraction=moss,
        correction_factor=correction,
        data_quality=quality + " (default 5%)",
    )


def get_all_paddock_moss() -> dict[str, MossEstimate]:
    """
    Calculate moss estimates for all paddocks with cached data.

    Returns:
        Dict of paddock_id -> MossEstimate
    """
    ndvi_data = load_historical_ndvi()
    soil_data = load_soil_data()

    results = {}

    for paddock_id, paddock_info in ndvi_data.get("paddocks", {}).items():
        name = paddock_info.get("name", "Unknown")
        history = paddock_info.get("history", [])

        # Get soil data by name
        soil = soil_data.get("paddocks", {}).get(name, {})

        estimate = estimate_paddock_moss(
            paddock_id=paddock_id,
            paddock_name=name,
            ndvi_history=history,
            soil_data=soil,
        )

        results[paddock_id] = estimate

    return results


def main():
    """Display moss estimates for all paddocks."""
    print("=" * 85)
    print("Moss Detection - Seasonal NDVI + Soil Drainage Analysis")
    print("=" * 85)

    estimates = get_all_paddock_moss()

    if not estimates:
        print("\nNo data available. Run these first:")
        print("  uv run python -m agriwebb.fetch_historical_ndvi")
        print("  uv run python fetch_paddock_soils.py")
        return

    print(f"\n{'Paddock':<25} {'Season':>8} {'Drain':>7} {'Moss%':>7} {'Corr':>6} {'Quality'}")
    print("-" * 85)

    # Sort by moss fraction descending
    sorted_estimates = sorted(
        estimates.values(),
        key=lambda x: x["moss_fraction"],
        reverse=True,
    )

    for e in sorted_estimates:
        season = f"{e['seasonality_index']:.2f}" if e["seasonality_index"] is not None else "N/A"
        print(
            f"{e['paddock_name']:<25} "
            f"{season:>8} "
            f"{e['drainage_score']:>7.2f} "
            f"{e['moss_fraction']*100:>6.1f}% "
            f"{e['correction_factor']:>6.2f} "
            f"{e['data_quality']}"
        )

    # Summary
    avg_moss = sum(e["moss_fraction"] for e in estimates.values()) / len(estimates)
    high_moss = [e for e in estimates.values() if e["moss_fraction"] > 0.2]

    print(f"\n--- Summary ---")
    print(f"Paddocks analyzed: {len(estimates)}")
    print(f"Average moss fraction: {avg_moss*100:.1f}%")
    print(f"High moss (>20%): {len(high_moss)} paddocks")

    if high_moss:
        print(f"\nHigh moss paddocks:")
        for e in sorted(high_moss, key=lambda x: -x["moss_fraction"]):
            print(f"  {e['paddock_name']}: {e['moss_fraction']*100:.0f}% moss")


if __name__ == "__main__":
    main()
