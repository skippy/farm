"""Core module - configuration and API client."""

from agriwebb.core import client
from agriwebb.core.client import (
    add_feed_on_offer_batch,
    add_foo_target_batch,
    add_pasture_growth_rate,
    add_pasture_growth_rates_batch,
    add_rainfall,
    add_standing_dry_matter_batch,
    create_rain_gauge,
    get_farm,
    get_farm_location,
    get_fields,
    get_map_feature,
    get_rainfalls,
    graphql,
    update_map_feature,
)
from agriwebb.core.config import get_cache_dir, settings

__all__ = [
    "client",
    "settings",
    "get_cache_dir",
    "graphql",
    "get_farm",
    "get_farm_location",
    "get_fields",
    "add_rainfall",
    "get_rainfalls",
    "create_rain_gauge",
    "get_map_feature",
    "update_map_feature",
    "add_pasture_growth_rate",
    "add_pasture_growth_rates_batch",
    "add_feed_on_offer_batch",
    "add_standing_dry_matter_batch",
    "add_foo_target_batch",
]
