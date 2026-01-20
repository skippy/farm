"""
Weather-driven pasture growth model for temperate cool-season grasses.

This model estimates daily pasture growth (kg DM/ha/day) based on:
- Temperature (optimal range for cool-season grasses)
- Soil moisture (water balance using precipitation, ET₀, and soil AWC)
- Soil properties (drainage, organic matter)
- Seasonal base growth rates

The model is calibrated for Pacific Northwest maritime climate with:
- Cool-season grasses (perennial ryegrass, tall fescue, orchardgrass)
- Mild wet winters, dry summers
- Summer dormancy period

References:
-----------
[1] McCall & Bishop-Hurley (2003). "A pasture growth model for use in a
    whole-farm dairy production model" Agricultural Systems 76:1183-1205

[2] Romera et al. (2009). "Development of the Pasture Simulation model"
    Grass and Forage Science 64:379-396

[3] CSIRO GRAZPLAN - GrassGro documentation
"""

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import Enum
from pathlib import Path
from typing import TypedDict

from agriwebb.core import get_cache_dir
from agriwebb.weather.openmeteo import DailyWeather

# -----------------------------------------------------------------------------
# Constants and Configuration
# -----------------------------------------------------------------------------

# PNW cool-season grass parameters
TEMP_BASE = 4.0  # °C - below this, no growth
TEMP_OPT_LOW = 12.0  # °C - start of optimal range
TEMP_OPT_HIGH = 22.0  # °C - end of optimal range
TEMP_MAX = 32.0  # °C - above this, no growth (heat stress)

# Seasonal maximum potential growth rates (kg DM/ha/day)
# Based on PNW maritime climate patterns
SEASONAL_MAX_GROWTH = {
    "winter": 15,  # Dec-Feb: cool, wet, short days
    "spring": 80,  # Mar-May: peak growth, warming + moisture
    "summer": 25,  # Jun-Aug: dry, dormancy without irrigation
    "fall": 50,  # Sep-Nov: recovery after first rains
}

# Soil moisture parameters
MOISTURE_WILTING_POINT = 0.15  # fraction of AWC below which growth stops
MOISTURE_STRESS_POINT = 0.40  # fraction of AWC where stress begins
MOISTURE_OPTIMAL = 0.70  # fraction of AWC for optimal growth
MOISTURE_WATERLOG = 0.95  # above this, waterlogging reduces growth

# Drainage class adjustments (multiplicative factors)
DRAINAGE_FACTORS = {
    "Excessively drained": 0.85,  # dries out fast
    "Somewhat excessively drained": 0.90,
    "Well drained": 1.00,
    "Moderately well drained": 1.00,
    "Somewhat poorly drained": 0.95,  # risk of waterlogging
    "Poorly drained": 0.85,
    "Very poorly drained": 0.70,
}

# Organic matter bonus (per % OM above 3%)
OM_BONUS_PER_PERCENT = 0.02  # 2% growth boost per % OM above baseline


class Season(Enum):
    WINTER = "winter"
    SPRING = "spring"
    SUMMER = "summer"
    FALL = "fall"


def get_season(d: date) -> Season:
    """Get season from date (Northern Hemisphere)."""
    month = d.month
    if month in (12, 1, 2):
        return Season.WINTER
    elif month in (3, 4, 5):
        return Season.SPRING
    elif month in (6, 7, 8):
        return Season.SUMMER
    else:
        return Season.FALL


# -----------------------------------------------------------------------------
# Response Functions
# -----------------------------------------------------------------------------


def temperature_factor(temp_mean_c: float) -> float:
    """
    Calculate growth factor based on mean daily temperature.

    Returns a value between 0 and 1.
    Uses a trapezoidal response curve typical of cool-season grasses.

    Args:
        temp_mean_c: Mean daily temperature in Celsius

    Returns:
        Temperature growth factor (0-1)
    """
    if temp_mean_c <= TEMP_BASE:
        return 0.0
    elif temp_mean_c < TEMP_OPT_LOW:
        # Linear increase from base to optimal
        return (temp_mean_c - TEMP_BASE) / (TEMP_OPT_LOW - TEMP_BASE)
    elif temp_mean_c <= TEMP_OPT_HIGH:
        # Optimal range
        return 1.0
    elif temp_mean_c < TEMP_MAX:
        # Linear decrease from optimal to max
        return (TEMP_MAX - temp_mean_c) / (TEMP_MAX - TEMP_OPT_HIGH)
    else:
        return 0.0


def moisture_factor(soil_moisture_fraction: float) -> float:
    """
    Calculate growth factor based on soil moisture.

    Args:
        soil_moisture_fraction: Current soil moisture as fraction of AWC (0-1+)

    Returns:
        Moisture growth factor (0-1)
    """
    if soil_moisture_fraction <= MOISTURE_WILTING_POINT:
        return 0.0
    elif soil_moisture_fraction < MOISTURE_STRESS_POINT:
        # Stressed - linear increase
        return (
            (soil_moisture_fraction - MOISTURE_WILTING_POINT) / (MOISTURE_STRESS_POINT - MOISTURE_WILTING_POINT) * 0.5
        )  # max 0.5 when stressed
    elif soil_moisture_fraction < MOISTURE_OPTIMAL:
        # Suboptimal but not stressed
        return 0.5 + (soil_moisture_fraction - MOISTURE_STRESS_POINT) / (MOISTURE_OPTIMAL - MOISTURE_STRESS_POINT) * 0.5
    elif soil_moisture_fraction <= MOISTURE_WATERLOG:
        # Optimal range
        return 1.0
    else:
        # Waterlogged - reduced growth
        excess = soil_moisture_fraction - MOISTURE_WATERLOG
        return max(0.3, 1.0 - excess * 2)


def soil_quality_factor(
    drainage: str | None = None,
    organic_matter_pct: float | None = None,
) -> float:
    """
    Calculate growth factor based on soil quality.

    Args:
        drainage: USDA drainage class
        organic_matter_pct: Organic matter percentage

    Returns:
        Soil quality factor (typically 0.7-1.2)
    """
    factor = 1.0

    # Drainage adjustment
    if drainage:
        factor *= DRAINAGE_FACTORS.get(drainage, 1.0)

    # Organic matter bonus (above 3% baseline)
    if organic_matter_pct and organic_matter_pct > 3.0:
        bonus = (organic_matter_pct - 3.0) * OM_BONUS_PER_PERCENT
        factor *= 1.0 + min(bonus, 0.15)  # cap at 15% bonus

    return factor


# -----------------------------------------------------------------------------
# Water Balance Model
# -----------------------------------------------------------------------------


@dataclass
class SoilWaterState:
    """Tracks soil water balance for a paddock."""

    awc_mm: float  # Available water capacity in mm (AWC cm/cm * root depth mm)
    current_mm: float = field(default=0.0)  # Current water content in mm
    root_depth_mm: float = 300.0  # Effective root depth for pasture

    def __post_init__(self):
        # Initialize at 50% capacity if not set
        if self.current_mm == 0.0:
            self.current_mm = self.awc_mm * 0.5

    @classmethod
    def from_soil_data(cls, soil: dict, root_depth_mm: float = 300.0) -> SoilWaterState:
        """Create from paddock soil data."""
        awc_cm_cm = float(soil.get("awc_cm_cm") or 0.15)  # default if missing
        awc_mm = awc_cm_cm * root_depth_mm
        return cls(awc_mm=awc_mm, root_depth_mm=root_depth_mm)

    @property
    def fraction(self) -> float:
        """Current moisture as fraction of AWC."""
        if self.awc_mm <= 0:
            return 0.5
        return self.current_mm / self.awc_mm

    def update(self, precip_mm: float, et0_mm: float, crop_coefficient: float = 0.9) -> float:
        """
        Update water balance for one day.

        Args:
            precip_mm: Precipitation in mm
            et0_mm: Reference evapotranspiration in mm
            crop_coefficient: Kc for pasture (typically 0.85-1.0)

        Returns:
            Actual ET (mm) - may be less than potential if soil is dry
        """
        # Add precipitation
        self.current_mm += precip_mm

        # Calculate potential ET
        potential_et = et0_mm * crop_coefficient

        # Actual ET depends on soil moisture
        if self.fraction > MOISTURE_STRESS_POINT:
            actual_et = potential_et
        elif self.fraction > MOISTURE_WILTING_POINT:
            # Reduced ET when stressed
            stress_factor = (self.fraction - MOISTURE_WILTING_POINT) / (MOISTURE_STRESS_POINT - MOISTURE_WILTING_POINT)
            actual_et = potential_et * stress_factor
        else:
            actual_et = 0.0

        # Remove ET
        self.current_mm = max(0, self.current_mm - actual_et)

        # Cap at AWC (excess drains)
        if self.current_mm > self.awc_mm:
            self.current_mm = self.awc_mm

        return actual_et


# -----------------------------------------------------------------------------
# Daily Growth Calculation
# -----------------------------------------------------------------------------


class DailyGrowthResult(TypedDict):
    """Result of daily growth calculation."""

    date: str
    growth_kg_ha_day: float
    temp_factor: float
    moisture_factor: float
    soil_factor: float
    soil_moisture_fraction: float
    season: str
    max_potential: float
    notes: str


def calculate_daily_growth(
    d: date,
    temp_mean_c: float,
    precip_mm: float,
    et0_mm: float,
    soil_water: SoilWaterState,
    drainage: str | None = None,
    organic_matter_pct: float | None = None,
) -> DailyGrowthResult:
    """
    Calculate pasture growth for a single day.

    Args:
        d: Date
        temp_mean_c: Mean temperature (°C)
        precip_mm: Precipitation (mm)
        et0_mm: Reference evapotranspiration (mm)
        soil_water: SoilWaterState object (will be mutated)
        drainage: USDA drainage class
        organic_matter_pct: Soil organic matter %

    Returns:
        DailyGrowthResult with growth rate and factors
    """
    # Update soil water balance
    soil_water.update(precip_mm, et0_mm)

    # Get seasonal maximum
    season = get_season(d)
    max_potential = SEASONAL_MAX_GROWTH[season.value]

    # Calculate factors
    t_factor = temperature_factor(temp_mean_c)
    m_factor = moisture_factor(soil_water.fraction)
    s_factor = soil_quality_factor(drainage, organic_matter_pct)

    # Calculate growth
    growth = max_potential * t_factor * m_factor * s_factor

    # Build notes
    notes_parts = []
    if t_factor < 0.3:
        notes_parts.append("temp limited")
    if m_factor < 0.3:
        if soil_water.fraction < MOISTURE_STRESS_POINT:
            notes_parts.append("drought stress")
        else:
            notes_parts.append("waterlogged")

    return DailyGrowthResult(
        date=d.isoformat(),
        growth_kg_ha_day=round(growth, 1),
        temp_factor=round(t_factor, 2),
        moisture_factor=round(m_factor, 2),
        soil_factor=round(s_factor, 2),
        soil_moisture_fraction=round(soil_water.fraction, 2),
        season=season.value,
        max_potential=max_potential,
        notes=", ".join(notes_parts) if notes_parts else "normal",
    )


# -----------------------------------------------------------------------------
# Paddock Growth Calculator
# -----------------------------------------------------------------------------


@dataclass
class PaddockGrowthModel:
    """Growth model for a specific paddock."""

    paddock_id: str
    paddock_name: str
    area_ha: float
    soil_water: SoilWaterState
    drainage: str | None = None
    organic_matter_pct: float | None = None

    @classmethod
    def from_paddock_data(cls, paddock: dict, soil: dict | None = None) -> PaddockGrowthModel:
        """Create model from paddock and soil data."""
        soil = soil or {}
        soil_data = soil.get("soil", {})

        return cls(
            paddock_id=paddock.get("id") or soil.get("paddock_id", ""),
            paddock_name=paddock.get("name", "Unknown"),
            area_ha=paddock.get("totalArea") or soil.get("area_ha", 0),
            soil_water=SoilWaterState.from_soil_data(soil_data),
            drainage=soil_data.get("drainage"),
            organic_matter_pct=(
                float(soil_data.get("organic_matter_pct")) if soil_data.get("organic_matter_pct") else None
            ),
        )

    def calculate_growth(
        self,
        d: date,
        temp_mean_c: float,
        precip_mm: float,
        et0_mm: float,
    ) -> DailyGrowthResult:
        """Calculate growth for one day."""
        return calculate_daily_growth(
            d=d,
            temp_mean_c=temp_mean_c,
            precip_mm=precip_mm,
            et0_mm=et0_mm,
            soil_water=self.soil_water,
            drainage=self.drainage,
            organic_matter_pct=self.organic_matter_pct,
        )


# -----------------------------------------------------------------------------
# Farm-wide Growth Calculation
# -----------------------------------------------------------------------------


def load_paddock_soils(cache_path: Path | None = None, auto_fetch: bool = True) -> dict:
    """Load paddock soil data from cache.

    If cache doesn't exist and auto_fetch=True, fetches from USDA API.
    """
    if cache_path is None:
        cache_path = get_cache_dir() / "paddock_soils.json"

    if not cache_path.exists():
        if auto_fetch:
            import asyncio

            from agriwebb.data.soils import fetch_all_paddock_soils

            # Handle both sync and async contexts (no progress output when auto-fetching)
            try:
                asyncio.get_running_loop()
                # We're in an async context - use thread pool
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor() as pool:
                    pool.submit(asyncio.run, fetch_all_paddock_soils()).result()
            except RuntimeError:
                # No running loop - safe to use asyncio.run
                asyncio.run(fetch_all_paddock_soils())
        else:
            return {}

    if not cache_path.exists():
        return {}

    with open(cache_path) as f:
        data = json.load(f)

    return data.get("paddocks", {})


def load_weather_history(cache_path: Path | None = None) -> list[dict]:
    """Load weather history from cache."""
    if cache_path is None:
        cache_path = get_cache_dir() / "weather_historical.json"

    with open(cache_path) as f:
        data = json.load(f)

    return data.get("daily_data", [])


def calculate_farm_growth(
    start_date: date,
    end_date: date,
    paddock_soils: dict | None = None,
    weather_data: list[DailyWeather] | None = None,
) -> dict[str, list[DailyGrowthResult]]:
    """
    Calculate daily growth for all paddocks over a date range.

    Args:
        start_date: Start date
        end_date: End date (inclusive)
        paddock_soils: Paddock soil data (loaded if not provided)
        weather_data: Weather history (loaded if not provided)

    Returns:
        Dict mapping paddock name to list of daily results
    """
    # Load data if needed
    if paddock_soils is None:
        paddock_soils = load_paddock_soils()

    if weather_data is None:
        weather_data = load_weather_history()

    # Index weather by date
    weather_by_date = {w["date"]: w for w in weather_data}

    # Create models for each paddock
    models = {}
    for name, soil_data in paddock_soils.items():
        models[name] = PaddockGrowthModel.from_paddock_data({}, soil_data)
        models[name].paddock_name = name  # Override with soil data name

    # Calculate growth for each day
    results: dict[str, list[DailyGrowthResult]] = {name: [] for name in models}

    current = start_date
    while current <= end_date:
        date_str = current.isoformat()
        weather = weather_by_date.get(date_str)

        if weather:
            for name, model in models.items():
                result = model.calculate_growth(
                    d=current,
                    temp_mean_c=weather.get("temp_mean_c", 10),
                    precip_mm=weather.get("precip_mm", 0),
                    et0_mm=weather.get("et0_mm", 2),
                )
                results[name].append(result)

        current += timedelta(days=1)

    return results


def summarize_growth(
    results: dict[str, list[DailyGrowthResult]],
) -> dict[str, dict]:
    """
    Summarize growth results by paddock.

    Returns dict with total growth, average rate, etc. per paddock.
    """
    summaries = {}

    for name, daily_results in results.items():
        if not daily_results:
            continue

        total_growth = sum(r["growth_kg_ha_day"] for r in daily_results)
        avg_rate = total_growth / len(daily_results)

        summaries[name] = {
            "paddock_name": name,
            "days": len(daily_results),
            "total_growth_kg_ha": round(total_growth, 0),
            "avg_growth_kg_ha_day": round(avg_rate, 1),
            "min_growth": min(r["growth_kg_ha_day"] for r in daily_results),
            "max_growth": max(r["growth_kg_ha_day"] for r in daily_results),
            "start_date": daily_results[0]["date"],
            "end_date": daily_results[-1]["date"],
        }

    return summaries
