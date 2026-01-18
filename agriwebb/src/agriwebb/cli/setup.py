"""Setup command to verify configuration and create required resources."""

import asyncio
import os

import httpx

from agriwebb.core import client
from agriwebb.core.config import settings
from agriwebb.weather.ncei import NCEI_API_URL


def check_mark(success: bool) -> str:
    """Return a check mark or X based on success."""
    return "[OK]" if success else "[MISSING]"


def format_station_name(raw_name: str) -> str:
    """Format NCEI station name for display.

    Converts "FRIDAY HARBOR AIRPORT, WA US" to "Friday Harbor Airport".
    """
    name = raw_name.split(",")[0]
    return name.title()


async def check_env_vars() -> dict[str, bool]:
    """Check which environment variables are configured."""
    print("Checking environment variables...")
    print("-" * 50)

    checks = {
        "AGRIWEBB_API_KEY": bool(os.getenv("AGRIWEBB_API_KEY")),
        "AGRIWEBB_FARM_ID": bool(os.getenv("AGRIWEBB_FARM_ID")),
        "NCEI_STATION_ID": bool(os.getenv("NCEI_STATION_ID")),
        "AGRIWEBB_WEATHER_SENSOR_ID": bool(os.getenv("AGRIWEBB_WEATHER_SENSOR_ID")),
        "GEE_PROJECT_ID": bool(os.getenv("GEE_PROJECT_ID")),
    }

    required = ["AGRIWEBB_API_KEY", "AGRIWEBB_FARM_ID", "NCEI_STATION_ID"]
    optional = ["AGRIWEBB_WEATHER_SENSOR_ID", "GEE_PROJECT_ID"]

    for var in required:
        status = check_mark(checks[var])
        print(f"  {status} {var} (required)")

    for var in optional:
        status = check_mark(checks[var])
        print(f"  {status} {var} (optional)")

    print()
    return checks


async def test_agriwebb_connection() -> dict | None:
    """Test AgriWebb API connection by fetching farm info."""
    print("Testing AgriWebb API connection...")
    print("-" * 50)

    try:
        farm = await client.get_farm()
        print("  [OK] Connected to AgriWebb")
        print(f"       Farm: {farm['name']}")
        print(f"       Timezone: {farm.get('timeZone', 'Unknown')}")
        location = farm.get("address", {}).get("location", {})
        if location:
            print(f"       Location: {location.get('lat')}, {location.get('long')}")
        print()
        return farm
    except Exception as e:
        print(f"  [FAILED] Could not connect to AgriWebb: {e}")
        print()
        return None


async def test_ncei_connection() -> str | None:
    """Test NOAA/NCEI station by fetching station info."""
    print("Testing NOAA/NCEI station...")
    print("-" * 50)

    station_id = settings.ncei_station_id
    params = {
        "dataset": "daily-summaries",
        "stations": station_id,
        "dataTypes": "PRCP",
        "startDate": "2024-01-01",
        "endDate": "2024-01-02",
        "format": "json",
        "includeStationName": "true",
    }

    try:
        async with httpx.AsyncClient() as http:
            response = await http.get(NCEI_API_URL, params=params, timeout=15)
            if response.status_code == 200:
                data = response.json()
                if data:
                    raw_name = data[0].get("NAME", "Unknown")
                    station_name = format_station_name(raw_name)
                    print("  [OK] Station found")
                    print(f"       ID: {station_id}")
                    print(f"       Name: {station_name}")
                    print()
                    return station_name
                else:
                    print(f"  [WARNING] Station {station_id} returned no data")
                    print("            Station may be inactive or ID may be incorrect")
                    print()
                    return None
            else:
                print(f"  [FAILED] NCEI API error: {response.status_code}")
                print()
                return None
    except Exception as e:
        print(f"  [FAILED] Could not connect to NCEI: {e}")
        print()
        return None


async def test_gee_connection() -> bool:
    """Test Google Earth Engine connection."""
    print("Testing Google Earth Engine...")
    print("-" * 50)

    if not settings.gee_project_id:
        print("  [SKIPPED] GEE_PROJECT_ID not configured")
        print("            Satellite features will be unavailable")
        print()
        return False

    try:
        import ee
        ee.Initialize(project=settings.gee_project_id)
        # Simple test - get a known image
        ee.Image("USGS/SRTMGL1_003").getInfo()
        print("  [OK] Connected to Google Earth Engine")
        print(f"       Project: {settings.gee_project_id}")
        print()
        return True
    except ImportError:
        print("  [FAILED] earthengine-api not installed")
        print()
        return False
    except Exception as e:
        print(f"  [FAILED] Could not connect to GEE: {e}")
        print()
        return False


async def setup_rain_gauge(station_name: str | None, farm: dict | None) -> str | None:
    """Create or verify rain gauge in AgriWebb."""
    print("Checking rain gauge...")
    print("-" * 50)

    if settings.agriwebb_weather_sensor_id:
        print("  [OK] Rain gauge configured")
        print(f"       ID: {settings.agriwebb_weather_sensor_id}")
        print()
        return settings.agriwebb_weather_sensor_id

    if not farm:
        print("  [SKIPPED] Cannot create rain gauge without AgriWebb connection")
        print()
        return None

    # Build gauge name
    if station_name:
        gauge_name = f"NOAA Station: {station_name}"
    else:
        gauge_name = f"NOAA Station: {settings.ncei_station_id}"

    print(f"  [ACTION] Creating rain gauge '{gauge_name}'...")

    try:
        location = farm.get("address", {}).get("location", {})
        lat = location.get("lat")
        lng = location.get("long")

        if not lat or not lng:
            print("  [FAILED] No farm location available")
            print()
            return None

        sensor_id = await client.create_rain_gauge(gauge_name, lat, lng)
        print("  [OK] Rain gauge created!")
        print()
        print("  Add to your .env file:")
        print(f"    AGRIWEBB_WEATHER_SENSOR_ID={sensor_id}")
        print()
        print(f"  Or add as GitHub secret with value: {sensor_id}")
        print()
        return sensor_id
    except Exception as e:
        print(f"  [FAILED] Could not create rain gauge: {e}")
        print()
        return None


async def main() -> None:
    """Run setup checks and create required resources."""
    print("=" * 50)
    print("AgriWebb Setup")
    print("=" * 50)
    print()

    # Step 1: Check environment variables
    env_checks = await check_env_vars()

    # Check if required vars are missing
    required_missing = []
    for var in ["AGRIWEBB_API_KEY", "AGRIWEBB_FARM_ID", "NCEI_STATION_ID"]:
        if not env_checks.get(var):
            required_missing.append(var)

    if required_missing:
        print("ERROR: Missing required environment variables:")
        for var in required_missing:
            print(f"  - {var}")
        print()
        print("Create a .env file or set these as environment variables.")
        return

    # Step 2: Test connections
    farm = await test_agriwebb_connection()
    station_name = await test_ncei_connection()
    gee_ok = await test_gee_connection()

    # Step 3: Setup rain gauge if needed
    sensor_id = await setup_rain_gauge(station_name, farm)

    # Summary
    print("=" * 50)
    print("Summary")
    print("=" * 50)
    print()

    all_ok = True

    if farm:
        print(f"  AgriWebb:  Connected ({farm['name']})")
    else:
        print("  AgriWebb:  NOT CONNECTED")
        all_ok = False

    if station_name:
        print(f"  NOAA:      Connected ({station_name})")
    else:
        print("  NOAA:      NOT CONNECTED")
        all_ok = False

    if gee_ok:
        print("  Satellite: Connected (GEE)")
    else:
        print("  Satellite: Not configured (optional)")

    if sensor_id:
        print("  Rain gauge: Ready")
    else:
        print("  Rain gauge: NOT CONFIGURED")
        all_ok = False

    print()
    if all_ok:
        print("Setup complete! All required services are connected.")
    else:
        print("Setup incomplete. See errors above.")


def cli() -> None:
    """CLI entry point."""
    asyncio.run(main())


if __name__ == "__main__":
    cli()
