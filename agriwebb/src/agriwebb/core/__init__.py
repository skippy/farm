"""Core module - configuration and API client."""

from agriwebb.core import client, units
from agriwebb.core.client import (
    AgriWebbAPIError,
    ExternalAPIError,
    GraphQLError,
    RetryableError,
    get_farm,
    get_farm_location,
    get_farm_timezone,
    get_farm_today,
    get_fields,
    get_map_feature,
    graphql,
    graphql_with_retry,
    http_get_with_retry,
    update_map_feature,
)
from agriwebb.core.config import get_cache_dir, settings
from agriwebb.core.units import (
    format_precip,
    format_precip_summary,
    format_temp,
    format_temp_range,
    get_precip_description,
    get_precip_unit,
    get_temp_unit,
    is_imperial,
    precip_mm_to_display,
    temp_c_to_display,
)

__all__ = [
    "client",
    "units",
    "settings",
    "get_cache_dir",
    "graphql",
    "graphql_with_retry",
    "http_get_with_retry",
    "GraphQLError",
    "RetryableError",
    "AgriWebbAPIError",
    "ExternalAPIError",
    "get_farm",
    "get_farm_today",
    "get_farm_location",
    "get_farm_timezone",
    "get_fields",
    "get_map_feature",
    "update_map_feature",
    # Unit conversion helpers
    "format_temp",
    "format_temp_range",
    "format_precip",
    "format_precip_summary",
    "get_precip_description",
    "temp_c_to_display",
    "precip_mm_to_display",
    "get_temp_unit",
    "get_precip_unit",
    "is_imperial",
]
