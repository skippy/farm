"""AgriWebb API functions for weather/rainfall data."""

from agriwebb.core.config import settings
from agriwebb.core.timestamps import to_timestamp_ms

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

# AgriWebb returns at most 500 rainfall records per query, even with a larger limit.
RAINFALLS_PAGE_SIZE = 500

RAINFALLS_QUERY = """
query GetRainfalls($farmId: String!, $sensorId: String!, $limit: Int!, $skip: Int!) {
  rainfalls(filter: {
    farmId: { _eq: $farmId }
    sensorId: { _eq: $sensorId }
  }, limit: $limit, skip: $skip) {
    id
    time
    value
    unit
    mode
    sensorId
  }
}
"""


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

    timestamp_ms = to_timestamp_ms(date_str)
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

    AgriWebb caps each response at RAINFALLS_PAGE_SIZE records, so this pages
    with limit/skip until a short page comes back.
    """
    from agriwebb.core.client import graphql_with_retry

    sensor = sensor_id or settings.agriwebb_weather_sensor_id
    if not sensor:
        raise ValueError("No sensor ID configured.")

    if start_date or end_date:
        # Build parameterized time-filtered query
        var_defs = ["$farmId: String!", "$sensorId: String!", "$limit: Int!", "$skip: Int!"]
        time_filter_parts = []
        variables = {
            "farmId": settings.agriwebb_farm_id,
            "sensorId": sensor,
        }
        if start_date:
            var_defs.append("$startTime: Float!")
            time_filter_parts.append("_gte: $startTime")
            variables["startTime"] = to_timestamp_ms(start_date)
        if end_date:
            var_defs.append("$endTime: Float!")
            time_filter_parts.append("_lte: $endTime")
            variables["endTime"] = to_timestamp_ms(end_date)
        time_filter = f", time: {{ {', '.join(time_filter_parts)} }}" if time_filter_parts else ""

        query = f"""
        query GetRainfallsFiltered({", ".join(var_defs)}) {{
          rainfalls(filter: {{
            farmId: {{ _eq: $farmId }}
            sensorId: {{ _eq: $sensorId }}
            {time_filter}
          }}, limit: $limit, skip: $skip) {{
            id
            time
            value
            unit
            mode
            sensorId
          }}
        }}
        """
    else:
        variables = {
            "farmId": settings.agriwebb_farm_id,
            "sensorId": sensor,
        }
        query = RAINFALLS_QUERY

    rainfalls: list[dict] = []
    while True:
        page_vars = {**variables, "limit": RAINFALLS_PAGE_SIZE, "skip": len(rainfalls)}
        result = await graphql_with_retry(query, page_vars)
        page = result.get("data", {}).get("rainfalls", [])
        rainfalls.extend(page)
        if len(page) < RAINFALLS_PAGE_SIZE:
            return rainfalls
