"""AgriWebb API client."""

from datetime import UTC, date, datetime

import httpx

from agriwebb.core.config import settings

API_URL = "https://api.agriwebb.com/v2"


def _to_timestamp_ms(d: str | date) -> int:
    """Convert a date string or date object to milliseconds timestamp (noon UTC)."""
    if isinstance(d, str):
        d = date.fromisoformat(d)
    dt = datetime(d.year, d.month, d.day, hour=12, tzinfo=UTC)
    return int(dt.timestamp() * 1000)


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

CREATE_RAIN_GAUGE_MUTATION = """
mutation CreateRainGauge($farmId: ID!, $name: String!, $lat: Float!, $lng: Float!) {
  addMapFeatures(input: {
    farmId: $farmId
    features: [{
      type: rainGauge
      name: $name
      location: { lat: $lat, long: $lng }
    }]
  }) {
    features {
      id
      name
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

# Note: update_map_feature still uses f-string for geometry due to complex nested structure
# that doesn't map cleanly to GraphQL variables

ADD_RAINFALL_MUTATION = """
mutation AddRainfall($farmId: ID!, $sensorId: ID!, $value: Float!, $time: Float!) {
  addRainfalls(input: {
    unit: mm
    value: $value
    farmId: $farmId
    sensorId: $sensorId
    time: $time
    mode: cumulative
  }) {
    rainfalls {
      time
      mode
    }
  }
}
"""

RAINFALLS_QUERY = """
query GetRainfalls($farmId: ID!, $sensorId: ID!) {
  rainfalls(filter: {
    farmId: { _eq: $farmId }
    sensorId: { _eq: $sensorId }
  }) {
    id
    time
    value
    unit
    mode
    sensorId
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

ADD_PASTURE_GROWTH_RATE_MUTATION = """
mutation AddPastureGrowthRate($input: [AddPastureGrowthRateInput!]!) {
  addPastureGrowthRates(input: $input) {
    pastureGrowthRates {
      id
      time
      value
    }
  }
}
"""

ADD_FEED_ON_OFFER_MUTATION = """
mutation AddFeedOnOffer($input: [AddFeedOnOfferInput!]!) {
  addFeedOnOffers(input: $input) {
    feedOnOffers {
      id
      time
      value
    }
  }
}
"""

ADD_STANDING_DRY_MATTER_MUTATION = """
mutation AddStandingDryMatter($input: [AddTotalStandingDryMatterInput!]!) {
  addTotalStandingDryMatters(input: $input) {
    totalStandingDryMatters {
      id
      time
      value
    }
  }
}
"""

ADD_FOO_TARGET_MUTATION = """
mutation AddFooTarget($input: [AddFeedOnOfferTargetInput!]!) {
  addFeedOnOfferTargets(input: $input) {
    feedOnOfferTargets {
      id
      time
      value
    }
  }
}
"""


# =============================================================================
# Client Functions
# =============================================================================

async def graphql(query: str, variables: dict | None = None) -> dict:
    """Execute a GraphQL query/mutation against AgriWebb.

    Args:
        query: GraphQL query or mutation string
        variables: Optional dictionary of GraphQL variables

    Returns:
        Parsed JSON response from the API
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
        return response.json()


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


async def create_rain_gauge(name: str, lat: float, lng: float) -> str:
    """
    Create a rain gauge map feature in AgriWebb.

    Args:
        name: Display name for the rain gauge
        lat: Latitude
        lng: Longitude

    Returns:
        The created sensor ID
    """
    variables = {
        "farmId": settings.agriwebb_farm_id,
        "name": name,
        "lat": lat,
        "lng": lng,
    }
    result = await graphql(CREATE_RAIN_GAUGE_MUTATION, variables)

    if "errors" in result:
        raise ValueError(f"Failed to create rain gauge: {result['errors']}")

    features = result.get("data", {}).get("addMapFeatures", {}).get("features", [])
    if not features:
        raise ValueError("No feature returned from API")

    return features[0]["id"]


async def get_map_feature(feature_id: str) -> dict:
    """Fetch a map feature by ID."""
    variables = {"featureId": feature_id}
    result = await graphql(MAP_FEATURE_QUERY, variables)

    if "errors" in result:
        raise ValueError(f"Failed to get feature: {result['errors']}")

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
    result = await graphql(mutation)

    if "errors" in result:
        raise ValueError(f"Failed to update feature: {result['errors']}")

    return result


async def add_rainfall(
    date_str: str,
    precipitation_inches: float,
    sensor_id: str | None = None,
) -> dict:
    """
    Add a rainfall record to AgriWebb.

    Args:
        date_str: Date in ISO format (YYYY-MM-DD)
        precipitation_inches: Rainfall amount in inches
        sensor_id: Optional sensor ID (defaults to config value)

    Returns:
        AgriWebb API response
    """
    sensor = sensor_id or settings.agriwebb_weather_sensor_id
    if not sensor:
        raise ValueError("No sensor ID configured. Run 'python -m agriwebb.setup' first.")

    timestamp_ms = _to_timestamp_ms(date_str)
    rainfall_mm = round(precipitation_inches * 25.4, 2)

    variables = {
        "farmId": settings.agriwebb_farm_id,
        "sensorId": sensor,
        "value": rainfall_mm,
        "time": timestamp_ms,
    }

    return await graphql(ADD_RAINFALL_MUTATION, variables)


async def get_rainfalls(
    sensor_id: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[dict]:
    """Get rainfall records for a sensor.

    Note:
        Date filtering is not yet supported with variables due to complex
        filter syntax. Currently returns all records for the sensor.
    """
    sensor = sensor_id or settings.agriwebb_weather_sensor_id
    if not sensor:
        raise ValueError("No sensor ID configured.")

    # Simple query without date filters for now
    # TODO: Add date filter support when needed
    if start_date or end_date:
        # Fall back to f-string for date filtering
        date_filter = ""
        if start_date:
            date_filter += f", time: {{ _gte: {_to_timestamp_ms(start_date)} }}"
        if end_date:
            date_filter += f", time: {{ _lte: {_to_timestamp_ms(end_date)} }}"

        query = f"""
        {{
          rainfalls(filter: {{
            farmId: {{ _eq: "{settings.agriwebb_farm_id}" }}
            sensorId: {{ _eq: "{sensor}" }}
            {date_filter}
          }}) {{
            id
            time
            value
            unit
            mode
            sensorId
          }}
        }}
        """
        result = await graphql(query)
    else:
        variables = {
            "farmId": settings.agriwebb_farm_id,
            "sensorId": sensor,
        }
        result = await graphql(RAINFALLS_QUERY, variables)

    if "errors" in result:
        raise ValueError(f"GraphQL errors: {result['errors']}")

    return result.get("data", {}).get("rainfalls", [])


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

    if "errors" in result:
        raise ValueError(f"GraphQL errors: {result['errors']}")

    fields = result.get("data", {}).get("fields", [])

    # Filter by minimum area
    return [f for f in fields if (f.get("totalArea") or 0) >= min_area_ha]


async def add_pasture_growth_rate(
    field_id: str,
    growth_rate: float,
    record_date: str | date,
) -> dict:
    """
    Add a pasture growth rate record to AgriWebb.

    Args:
        field_id: AgriWebb field/paddock ID
        growth_rate: Growth rate in kg DM/ha/day
        record_date: Date of the record (ISO format or date object)

    Returns:
        AgriWebb API response

    Note:
        Per AgriWebb docs: "The API will update the previous IOT record
        in a 7 day period, or create a new one."
    """
    timestamp_ms = _to_timestamp_ms(record_date)

    variables = {
        "input": [{
            "value": round(growth_rate, 1),
            "farmId": settings.agriwebb_farm_id,
            "fieldId": field_id,
            "time": timestamp_ms,
        }]
    }

    result = await graphql(ADD_PASTURE_GROWTH_RATE_MUTATION, variables)

    if "errors" in result:
        raise ValueError(f"Failed to add growth rate: {result['errors']}")

    return result


async def add_pasture_growth_rates_batch(
    records: list[dict],
) -> dict:
    """
    Add multiple pasture growth rate records in a single API call.

    Args:
        records: List of dicts with keys: field_id, growth_rate, record_date

    Returns:
        AgriWebb API response
    """
    inputs = []
    for rec in records:
        timestamp_ms = _to_timestamp_ms(rec["record_date"])
        inputs.append({
            "value": round(rec["growth_rate"], 1),
            "farmId": settings.agriwebb_farm_id,
            "fieldId": rec["field_id"],
            "time": timestamp_ms,
        })

    variables = {"input": inputs}
    result = await graphql(ADD_PASTURE_GROWTH_RATE_MUTATION, variables)

    if "errors" in result:
        raise ValueError(f"Failed to add growth rates: {result['errors']}")

    return result


async def add_feed_on_offer_batch(
    records: list[dict],
    source: str = "IOT",
) -> dict:
    """
    Add Feed on Offer (FOO) records to AgriWebb.

    FOO is the total available pasture in kg DM/ha at a point in time.

    Args:
        records: List of dicts with keys: field_id, foo_kg_ha, record_date
        source: Source type - "IOT", "Manual", or "LivestockEstimate"

    Returns:
        AgriWebb API response

    Note:
        API only accepts records within the last 14 days.
    """
    inputs = []
    for rec in records:
        timestamp_ms = _to_timestamp_ms(rec["record_date"])
        inputs.append({
            "value": round(rec["foo_kg_ha"], 0),
            "farmId": settings.agriwebb_farm_id,
            "fieldId": rec["field_id"],
            "time": timestamp_ms,
            "source": source,
        })

    variables = {"input": inputs}
    result = await graphql(ADD_FEED_ON_OFFER_MUTATION, variables)

    if "errors" in result:
        raise ValueError(f"Failed to add feed on offer: {result['errors']}")

    return result


async def add_standing_dry_matter_batch(
    records: list[dict],
) -> dict:
    """
    Add Total Standing Dry Matter (SDM) records to AgriWebb.

    SDM includes all dry matter (green + dead/senescent material).
    AgriWebb converts SDM to FOO using a utilization factor.

    Args:
        records: List of dicts with keys: field_id, sdm_kg_ha, record_date

    Returns:
        AgriWebb API response

    Note:
        API only accepts records within the last 14 days.
    """
    inputs = []
    for rec in records:
        timestamp_ms = _to_timestamp_ms(rec["record_date"])
        inputs.append({
            "value": round(rec["sdm_kg_ha"], 0),
            "farmId": settings.agriwebb_farm_id,
            "fieldId": rec["field_id"],
            "time": timestamp_ms,
        })

    variables = {"input": inputs}
    result = await graphql(ADD_STANDING_DRY_MATTER_MUTATION, variables)

    if "errors" in result:
        raise ValueError(f"Failed to add standing dry matter: {result['errors']}")

    return result


async def add_foo_target_batch(
    records: list[dict],
) -> dict:
    """
    Add Feed on Offer Target (residual) records to AgriWebb.

    FOO Target is the minimum pasture level before moving animals.

    Args:
        records: List of dicts with keys: field_id, target_kg_ha, record_date

    Returns:
        AgriWebb API response
    """
    inputs = []
    for rec in records:
        timestamp_ms = _to_timestamp_ms(rec["record_date"])
        inputs.append({
            "value": round(rec["target_kg_ha"], 0),
            "farmId": settings.agriwebb_farm_id,
            "fieldId": rec["field_id"],
            "time": timestamp_ms,
        })

    variables = {"input": inputs}
    result = await graphql(ADD_FOO_TARGET_MUTATION, variables)

    if "errors" in result:
        raise ValueError(f"Failed to add FOO targets: {result['errors']}")

    return result
