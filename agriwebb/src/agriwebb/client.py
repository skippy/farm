"""AgriWebb API client."""

from datetime import UTC, datetime

import httpx

from agriwebb.config import settings

API_URL = "https://api.agriwebb.com/v2"


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
    # Convert date to millisecond timestamp (noon UTC)
    dt = datetime.fromisoformat(date_str).replace(hour=12, tzinfo=UTC)
    timestamp_ms = int(dt.timestamp() * 1000)

    # Convert inches to mm for AgriWebb
    rainfall_mm = precipitation_inches * 25.4

    sensor = sensor_id or settings.agriwebb_weather_sensor_id

    mutation = f"""
    mutation {{
      addRainfalls(input: {{
        unit: mm
        value: {round(rainfall_mm, 2)}
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
