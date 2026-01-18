"""Core module - configuration and API client."""

from agriwebb.core import client
from agriwebb.core.config import settings, get_cache_dir
from agriwebb.core.client import (
    graphql,
    get_farm,
    get_farm_location,
    get_fields,
    add_rainfall,
    get_rainfalls,
    create_rain_gauge,
    get_map_feature,
    update_map_feature,
    add_pasture_growth_rate,
    add_pasture_growth_rates_batch,
    add_feed_on_offer_batch,
    add_standing_dry_matter_batch,
    add_foo_target_batch,
)

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
