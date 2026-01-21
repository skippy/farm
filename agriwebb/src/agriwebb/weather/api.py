"""AgriWebb API functions for weather/rainfall data."""

from datetime import UTC, date, datetime

from agriwebb.core.config import settings

# =============================================================================
# GraphQL Queries and Mutations
# =============================================================================

CREATE_RAIN_GAUGE_MUTATION = """
mutation CreateRainGauge($farmId: String!, $name: String!, $lat: Float!, $lng: Float!) {
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

ADD_RAINFALL_MUTATION = """
mutation AddRainfall($farmId: String!, $sensorId: String!, $value: Float!, $time: Timestamp!) {
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
query GetRainfalls($farmId: String!, $sensorId: String!) {
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


def _to_timestamp_ms(d: str | date) -> int:
    """Convert a date string or date object to milliseconds timestamp (noon UTC)."""
    if isinstance(d, str):
        d = date.fromisoformat(d)
    dt = datetime(d.year, d.month, d.day, hour=12, tzinfo=UTC)
    return int(dt.timestamp() * 1000)


# =============================================================================
# API Functions
# =============================================================================


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
    from agriwebb.core.client import graphql_with_retry

    variables = {
        "farmId": settings.agriwebb_farm_id,
        "name": name,
        "lat": lat,
        "lng": lng,
    }
    result = await graphql_with_retry(CREATE_RAIN_GAUGE_MUTATION, variables)

    features = result.get("data", {}).get("addMapFeatures", {}).get("features", [])
    if not features:
        raise ValueError("No feature returned from API")

    return features[0]["id"]


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
    from agriwebb.core.client import graphql_with_retry

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

    return await graphql_with_retry(ADD_RAINFALL_MUTATION, variables)


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
    from agriwebb.core.client import graphql_with_retry

    sensor = sensor_id or settings.agriwebb_weather_sensor_id
    if not sensor:
        raise ValueError("No sensor ID configured.")

    if start_date or end_date:
        # Build time filter - must combine _gte and _lte in a single time object
        time_conditions = []
        if start_date:
            time_conditions.append(f"_gte: {_to_timestamp_ms(start_date)}")
        if end_date:
            time_conditions.append(f"_lte: {_to_timestamp_ms(end_date)}")
        time_filter = f", time: {{ {', '.join(time_conditions)} }}" if time_conditions else ""

        query = f"""
        {{
          rainfalls(filter: {{
            farmId: {{ _eq: "{settings.agriwebb_farm_id}" }}
            sensorId: {{ _eq: "{sensor}" }}
            {time_filter}
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
        result = await graphql_with_retry(query)
    else:
        variables = {
            "farmId": settings.agriwebb_farm_id,
            "sensorId": sensor,
        }
        result = await graphql_with_retry(RAINFALLS_QUERY, variables)

    return result.get("data", {}).get("rainfalls", [])
