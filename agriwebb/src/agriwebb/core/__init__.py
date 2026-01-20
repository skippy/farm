"""Core module - configuration and API client."""

from agriwebb.core import client
from agriwebb.core.client import (
    AgriWebbAPIError,
    GraphQLError,
    RetryableError,
    get_farm,
    get_farm_today,
    get_farm_location,
    get_farm_timezone,
    get_fields,
    get_map_feature,
    graphql,
    graphql_with_retry,
    update_map_feature,
)
from agriwebb.core.config import get_cache_dir, settings

__all__ = [
    "client",
    "settings",
    "get_cache_dir",
    "graphql",
    "graphql_with_retry",
    "GraphQLError",
    "RetryableError",
    "AgriWebbAPIError",
    "get_farm",
    "get_farm_today",
    "get_farm_location",
    "get_farm_timezone",
    "get_fields",
    "get_map_feature",
    "update_map_feature",
]
