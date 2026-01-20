"""AgriWebb API client - core functions only."""

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from agriwebb.core.config import settings

API_URL = "https://api.agriwebb.com/v2"

# =============================================================================
# Retry Configuration
# =============================================================================

MAX_RETRIES = 3
MIN_WAIT_SECONDS = 1
MAX_WAIT_SECONDS = 10


# =============================================================================
# Exceptions
# =============================================================================


class GraphQLError(Exception):
    """Raised when a GraphQL query returns errors."""

    def __init__(self, errors: list[dict], query: str | None = None):
        self.errors = errors
        self.query = query
        messages = [e.get("message", str(e)) for e in errors]
        super().__init__(f"GraphQL errors: {'; '.join(messages)}")


class RetryableError(Exception):
    """Transient error that should be retried (timeouts, connection errors, 5xx)."""

    pass


class AgriWebbAPIError(Exception):
    """Non-retryable error from AgriWebb API."""

    pass


# =============================================================================
# GraphQL Queries and Mutations
# =============================================================================

FARMS_QUERY = """
query GetFarms {
  farms {
    id
    name
    timeZone
    address {
      location {
        lat
        long
      }
    }
  }
}
"""

MAP_FEATURE_QUERY = """
query GetMapFeature($featureId: String!) {
  mapFeatures(filter: { id: { _eq: $featureId } }) {
    id
    name
    geometry {
      type
      coordinates
    }
  }
}
"""

FIELDS_QUERY = """
query GetFields($farmId: String!) {
  fields(filter: { farmId: { _eq: $farmId } }) {
    id
    name
    totalArea
    grazableArea
    geometry {
      type
      coordinates
    }
    landUse
    cropType
  }
}
"""


# =============================================================================
# Client Functions
# =============================================================================


async def graphql(query: str, variables: dict | None = None) -> dict:
    """Execute a GraphQL query/mutation against AgriWebb.

    This is the low-level function that makes a single request without retry.
    For most use cases, prefer `graphql_with_retry()` which handles transient errors.

    Args:
        query: GraphQL query or mutation string
        variables: Optional dictionary of GraphQL variables

    Returns:
        Parsed JSON response from the API

    Raises:
        GraphQLError: If the response contains GraphQL errors
        httpx.HTTPStatusError: If the HTTP request fails
    """
    payload = {"query": query}
    if variables:
        payload["variables"] = variables

    async with httpx.AsyncClient() as client:
        response = await client.post(
            API_URL,
            headers={
                "x-api-key": settings.agriwebb_api_key,
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=30,
        )
        response.raise_for_status()

        result = response.json()

        if "errors" in result:
            raise GraphQLError(result["errors"], query)

        return result


@retry(
    retry=retry_if_exception_type(RetryableError),
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential_jitter(initial=MIN_WAIT_SECONDS, max=MAX_WAIT_SECONDS, jitter=2),
    reraise=True,
)
async def graphql_with_retry(query: str, variables: dict | None = None) -> dict:
    """Execute a GraphQL query with automatic retry on transient errors.

    Retries on:
    - Timeouts
    - Connection errors
    - HTTP 5xx errors (server overload)
    - GraphQL errors with "Internal Server Error"

    After MAX_RETRIES failures, raises AgriWebbAPIError.

    Args:
        query: GraphQL query or mutation string
        variables: Optional dictionary of GraphQL variables

    Returns:
        Parsed JSON response from the API

    Raises:
        AgriWebbAPIError: If all retries fail or non-retryable error occurs
    """
    try:
        return await graphql(query, variables)
    except httpx.TimeoutException as e:
        raise RetryableError(f"Request timed out: {e}") from e
    except httpx.ConnectError as e:
        raise RetryableError(f"Connection failed: {e}") from e
    except httpx.HTTPStatusError as e:
        # Try to get the response body for better error messages
        try:
            body = e.response.text
        except Exception:
            body = "(unable to read response body)"

        if e.response.status_code >= 500:
            # Server error - retry with backoff
            raise RetryableError(f"HTTP {e.response.status_code}: {body}") from e
        # Client error (4xx) - don't retry, include full response
        raise AgriWebbAPIError(f"HTTP {e.response.status_code}: {body}") from e
    except GraphQLError as e:
        # Check if this is a server error we should retry
        if any("Internal Server Error" in err.get("message", "") for err in e.errors):
            raise RetryableError(f"GraphQL server error: {e}") from e
        # Non-retryable GraphQL error
        raise AgriWebbAPIError(f"GraphQL error: {e}") from e


async def get_farm() -> dict:
    """Fetch farm details including location."""
    result = await graphql_with_retry(FARMS_QUERY)
    farms = result.get("data", {}).get("farms", [])

    for farm in farms:
        if farm["id"] == settings.agriwebb_farm_id:
            return farm

    raise ValueError(f"Farm {settings.agriwebb_farm_id} not found")


async def get_farm_location() -> tuple[float, float]:
    """Get farm latitude and longitude from AgriWebb."""
    farm = await get_farm()
    location = farm["address"]["location"]
    return location["lat"], location["long"]


async def get_farm_timezone() -> str:
    """Get farm timezone from settings or AgriWebb.

    Returns IANA timezone string (e.g., "America/Los_Angeles").
    Checks TZ environment variable first, falls back to AgriWebb farm data.
    """
    if settings.tz:
        return settings.tz
    farm = await get_farm()
    return farm.get("timeZone", "UTC")


async def get_map_feature(feature_id: str) -> dict:
    """Fetch a map feature by ID."""
    variables = {"featureId": feature_id}
    result = await graphql_with_retry(MAP_FEATURE_QUERY, variables)

    features = result.get("data", {}).get("mapFeatures", [])
    if not features:
        raise ValueError(f"Feature {feature_id} not found")

    return features[0]


async def update_map_feature(feature_id: str, name: str) -> dict:
    """
    Update a map feature's name.

    Args:
        feature_id: The feature ID to update
        name: New display name

    Returns:
        API response

    Note:
        This function still uses f-string interpolation for the geometry
        object due to its complex nested structure that doesn't map cleanly
        to GraphQL input types.
    """
    # Fetch current feature to get geometry (required for update)
    feature = await get_map_feature(feature_id)
    geometry = feature.get("geometry", {})

    # Note: geometry requires special handling - keeping f-string for this mutation
    mutation = f"""
    mutation {{
      updateMapFeature(input: {{
        farmId: "{settings.agriwebb_farm_id}"
        id: "{feature_id}"
        name: "{name}"
        geometry: {{
          type: {geometry.get("type", "Point")}
          coordinates: {geometry.get("coordinates", [])}
        }}
      }}) {{
        mapFeature {{
          id
          name
        }}
      }}
    }}
    """
    return await graphql_with_retry(mutation)


async def get_fields(min_area_ha: float = 0.2) -> list[dict]:
    """
    Fetch all fields/paddocks for the farm.

    Args:
        min_area_ha: Minimum area in hectares (default 0.2 = ~0.5 acres)

    Returns:
        List of field dictionaries with geometry
    """
    variables = {"farmId": settings.agriwebb_farm_id}
    result = await graphql_with_retry(FIELDS_QUERY, variables)

    fields = result.get("data", {}).get("fields", [])

    # Filter by minimum area
    return [f for f in fields if (f.get("totalArea") or 0) >= min_area_ha]
