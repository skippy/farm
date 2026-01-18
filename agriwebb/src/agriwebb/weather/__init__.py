"""Weather data modules - NOAA/NCEI and Open-Meteo APIs."""

from agriwebb.weather import openmeteo
from agriwebb.weather.cli import cli
from agriwebb.weather.ncei import (
    NCEI_API_URL,
    fetch_combined_precipitation,
    fetch_ncei_date_range,
    fetch_ncei_precipitation,
    save_weather_json,
    sync_weather,
)

__all__ = [
    "openmeteo",
    "cli",
    "NCEI_API_URL",
    "fetch_ncei_precipitation",
    "fetch_ncei_date_range",
    "fetch_combined_precipitation",
    "sync_weather",
    "save_weather_json",
]
