"""Weather data modules - NOAA/NCEI and Open-Meteo APIs.

Data fetching is in ncei.py and openmeteo.py.
AgriWebb integration and CLI commands are in cli.py.
"""

from agriwebb.weather import openmeteo
from agriwebb.weather.api import (
    add_rainfall,
    create_rain_gauge,
    get_rainfalls,
)
from agriwebb.weather.cli import cli
from agriwebb.weather.ncei import (
    NCEI_API_URL,
    fetch_combined_precipitation,
    fetch_ncei_date_range,
    fetch_ncei_precipitation,
    log_weather,
    save_weather_json,
)

__all__ = [
    "openmeteo",
    "cli",
    # AgriWebb API functions
    "add_rainfall",
    "get_rainfalls",
    "create_rain_gauge",
    # NCEI data fetching functions
    "NCEI_API_URL",
    "fetch_ncei_precipitation",
    "fetch_ncei_date_range",
    "fetch_combined_precipitation",
    "log_weather",
    "save_weather_json",
]
