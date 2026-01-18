"""AgriWebb integration tools.

This package provides tools for integrating with AgriWebb's API,
including weather data sync, satellite-based pasture analysis,
and livestock management.

Subpackages:
- agriwebb.core: Configuration and API client
- agriwebb.data: Livestock, fields, grazing data
- agriwebb.weather: NOAA/NCEI and Open-Meteo weather APIs
- agriwebb.satellite: Google Earth Engine NDVI analysis
- agriwebb.analysis: Biomass, carbon, and growth models
- agriwebb.sync: Data sync to AgriWebb
- agriwebb.cli: Command-line tools
"""

# Re-export common items for convenience
from agriwebb.core import (
    client,
    settings,
    graphql,
    get_farm,
    get_farm_location,
    get_fields,
)

__all__ = [
    "client",
    "settings",
    "graphql",
    "get_farm",
    "get_farm_location",
    "get_fields",
]

__version__ = "0.1.0"
