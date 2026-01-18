"""Pasture growth modeling and analysis.

This module provides:
- Weather-driven pasture growth models (growth.py)
- NDVI-to-biomass conversion models (biomass.py)
- Unified CLI for pasture operations (cli.py)
"""

from agriwebb.pasture.growth import (
    calculate_daily_growth,
    calculate_farm_growth,
    PaddockGrowthModel,
    SoilWaterState,
    SEASONAL_MAX_GROWTH,
    summarize_growth,
    load_paddock_soils,
    load_weather_history,
)
from agriwebb.pasture.biomass import (
    ndvi_to_standing_dry_matter,
    calculate_growth_rate,
    get_season,
    calculate_grazing_correction,
    adjust_foo_for_grazing,
    EXPECTED_UNCERTAINTY,
    Season,
)
from agriwebb.pasture.cli import cli

__all__ = [
    # growth
    "calculate_daily_growth",
    "calculate_farm_growth",
    "PaddockGrowthModel",
    "SoilWaterState",
    "SEASONAL_MAX_GROWTH",
    "summarize_growth",
    "load_paddock_soils",
    "load_weather_history",
    # biomass
    "ndvi_to_standing_dry_matter",
    "calculate_growth_rate",
    "get_season",
    "calculate_grazing_correction",
    "adjust_foo_for_grazing",
    "EXPECTED_UNCERTAINTY",
    "Season",
    # cli
    "cli",
]
