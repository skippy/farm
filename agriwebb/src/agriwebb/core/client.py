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


async def graphql(query: str) -> dict:
    """Execute a GraphQL query/mutation against AgriWebb."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            API_URL,
            headers={
                "x-api-key": settings.agriwebb_api_key,
                "Content-Type": "application/json",
            },
            json={"query": query},
            timeout=30,
        )
        if response.status_code >= 400:
            print(f"GraphQL error {response.status_code}: {response.text}")
        response.raise_for_status()
        return response.json()


async def get_farm() -> dict:
    """Fetch farm details including location."""
    query = """
    {
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
    result = await graphql(query)
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
    mutation = f"""
    mutation {{
      addMapFeatures(input: {{
        farmId: "{settings.agriwebb_farm_id}"
        features: [{{
          type: rainGauge
          name: "{name}"
          location: {{ lat: {lat}, long: {lng} }}
        }}]
      }}) {{
        features {{
          id
          name
        }}
      }}
    }}
    """
    result = await graphql(mutation)

    if "errors" in result:
        raise ValueError(f"Failed to create rain gauge: {result['errors']}")

    features = result.get("data", {}).get("addMapFeatures", {}).get("features", [])
    if not features:
        raise ValueError("No feature returned from API")

    return features[0]["id"]


async def get_map_feature(feature_id: str) -> dict:
    """Fetch a map feature by ID."""
    query = f"""
    {{
      mapFeatures(filter: {{ id: {{ _eq: "{feature_id}" }} }}) {{
        id
        name
        geometry {{
          type
          coordinates
        }}
      }}
    }}
    """
    result = await graphql(query)

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
    """
    # Fetch current feature to get geometry (required for update)
    feature = await get_map_feature(feature_id)
    geometry = feature.get("geometry", {})

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

    mutation = f"""
    mutation {{
      addRainfalls(input: {{
        unit: mm
        value: {rainfall_mm}
        farmId: "{settings.agriwebb_farm_id}"
        sensorId: "{sensor}"
        time: {timestamp_ms}
        mode: cumulative
      }}) {{
        rainfalls {{
          time
          mode
        }}
      }}
    }}
    """

    return await graphql(mutation)


async def get_rainfalls(
    sensor_id: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[dict]:
    """Get rainfall records for a sensor."""
    sensor = sensor_id or settings.agriwebb_weather_sensor_id
    if not sensor:
        raise ValueError("No sensor ID configured.")

    # Build optional date filter
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
    query = f"""
    {{
      fields(filter: {{ farmId: {{ _eq: "{settings.agriwebb_farm_id}" }} }}) {{
        id
        name
        totalArea
        grazableArea
        geometry {{
          type
          coordinates
        }}
        landUse
        cropType
      }}
    }}
    """
    result = await graphql(query)

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

    mutation = f"""
    mutation {{
      addPastureGrowthRates(input: [{{
        value: {round(growth_rate, 1)}
        farmId: "{settings.agriwebb_farm_id}"
        fieldId: "{field_id}"
        time: {timestamp_ms}
      }}]) {{
        pastureGrowthRates {{
          id
          time
          value
        }}
      }}
    }}
    """

    result = await graphql(mutation)

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
        inputs.append(
            f'{{ value: {round(rec["growth_rate"], 1)}, '
            f'farmId: "{settings.agriwebb_farm_id}", '
            f'fieldId: "{rec["field_id"]}", '
            f'time: {timestamp_ms} }}'
        )

    inputs_str = "[" + ", ".join(inputs) + "]"

    mutation = f"""
    mutation {{
      addPastureGrowthRates(input: {inputs_str}) {{
        pastureGrowthRates {{
          id
          time
          value
        }}
      }}
    }}
    """

    result = await graphql(mutation)

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
        inputs.append(
            f'{{ value: {round(rec["foo_kg_ha"], 0)}, '
            f'farmId: "{settings.agriwebb_farm_id}", '
            f'fieldId: "{rec["field_id"]}", '
            f'time: {timestamp_ms}, '
            f'source: {source} }}'
        )

    inputs_str = "[" + ", ".join(inputs) + "]"

    mutation = f"""
    mutation {{
      addFeedOnOffers(input: {inputs_str}) {{
        feedOnOffers {{
          id
          time
          value
        }}
      }}
    }}
    """

    result = await graphql(mutation)

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
        inputs.append(
            f'{{ value: {round(rec["sdm_kg_ha"], 0)}, '
            f'farmId: "{settings.agriwebb_farm_id}", '
            f'fieldId: "{rec["field_id"]}", '
            f'time: {timestamp_ms} }}'
        )

    inputs_str = "[" + ", ".join(inputs) + "]"

    mutation = f"""
    mutation {{
      addTotalStandingDryMatters(input: {inputs_str}) {{
        totalStandingDryMatters {{
          id
          time
          value
        }}
      }}
    }}
    """

    result = await graphql(mutation)

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
        inputs.append(
            f'{{ value: {round(rec["target_kg_ha"], 0)}, '
            f'farmId: "{settings.agriwebb_farm_id}", '
            f'fieldId: "{rec["field_id"]}", '
            f'time: {timestamp_ms} }}'
        )

    inputs_str = "[" + ", ".join(inputs) + "]"

    mutation = f"""
    mutation {{
      addFeedOnOfferTargets(input: {inputs_str}) {{
        feedOnOfferTargets {{
          id
          time
          value
        }}
      }}
    }}
    """

    result = await graphql(mutation)

    if "errors" in result:
        raise ValueError(f"Failed to add FOO targets: {result['errors']}")

    return result
