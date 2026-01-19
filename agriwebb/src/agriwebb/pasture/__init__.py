"""Pasture growth modeling and analysis.

This module provides:
- Weather-driven pasture growth models (growth.py)
- NDVI-to-biomass conversion models (biomass.py)
- AgriWebb API functions for pasture data (api.py)
- Unified CLI for pasture operations (cli.py)
"""

from agriwebb.pasture.api import (
    add_feed_on_offer_batch,
    add_pasture_growth_rates_batch,
    add_standing_dry_matter_batch,
)
from agriwebb.pasture.biomass import (
    EXPECTED_UNCERTAINTY,
    Season,
    adjust_foo_for_grazing,
    calculate_grazing_correction,
    calculate_growth_rate,
    get_season,
    ndvi_to_standing_dry_matter,
)
from agriwebb.pasture.cli import cli
from agriwebb.pasture.growth import (
    SEASONAL_MAX_GROWTH,
    PaddockGrowthModel,
    SoilWaterState,
    calculate_daily_growth,
    calculate_farm_growth,
    load_paddock_soils,
    load_weather_history,
    summarize_growth,
)

__all__ = [
    # AgriWebb API
    "add_pasture_growth_rates_batch",
    "add_feed_on_offer_batch",
    "add_standing_dry_matter_batch",
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
