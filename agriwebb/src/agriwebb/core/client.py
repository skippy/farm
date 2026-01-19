"""AgriWebb API client - core functions only."""

import httpx

from agriwebb.core.config import settings

API_URL = "https://api.agriwebb.com/v2"


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
query GetMapFeature($featureId: ID!) {
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
query GetFields($farmId: ID!) {
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


class GraphQLError(Exception):
    """Raised when a GraphQL query returns errors."""

    def __init__(self, errors: list[dict], query: str | None = None):
        self.errors = errors
        self.query = query
        messages = [e.get("message", str(e)) for e in errors]
        super().__init__(f"GraphQL errors: {'; '.join(messages)}")


async def graphql(query: str, variables: dict | None = None) -> dict:
    """Execute a GraphQL query/mutation against AgriWebb.

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
        if response.status_code >= 400:
            print(f"GraphQL error {response.status_code}: {response.text}")
        response.raise_for_status()

        result = response.json()

        if "errors" in result:
            raise GraphQLError(result["errors"], query)

        return result


async def get_farm() -> dict:
    """Fetch farm details including location."""
    result = await graphql(FARMS_QUERY)
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


async def get_map_feature(feature_id: str) -> dict:
    """Fetch a map feature by ID."""
    variables = {"featureId": feature_id}
    result = await graphql(MAP_FEATURE_QUERY, variables)

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
    return await graphql(mutation)


async def get_fields(min_area_ha: float = 0.2) -> list[dict]:
    """
    Fetch all fields/paddocks for the farm.

    Args:
        min_area_ha: Minimum area in hectares (default 0.2 = ~0.5 acres)

    Returns:
        List of field dictionaries with geometry
    """
    variables = {"farmId": settings.agriwebb_farm_id}
    result = await graphql(FIELDS_QUERY, variables)

    fields = result.get("data", {}).get("fields", [])

    # Filter by minimum area
    return [f for f in fields if (f.get("totalArea") or 0) >= min_area_ha]
