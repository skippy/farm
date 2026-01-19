"""Core module - configuration and API client."""

from agriwebb.core import client
from agriwebb.core.client import (
    GraphQLError,
    get_farm,
    get_farm_location,
    get_fields,
    get_map_feature,
    graphql,
    update_map_feature,
)
from agriwebb.core.config import get_cache_dir, settings

__all__ = [
    "client",
    "settings",
    "get_cache_dir",
    "graphql",
    "GraphQLError",
    "get_farm",
    "get_farm_location",
    "get_fields",
    "get_map_feature",
    "update_map_feature",
]
