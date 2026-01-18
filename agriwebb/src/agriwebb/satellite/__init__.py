"""Satellite modules - Google Earth Engine, NDVI, moss detection."""

from agriwebb.satellite.gee import (
    extract_all_paddocks_ndvi,
    extract_paddock_ndvi,
    initialize,
)

__all__ = [
    "initialize",
    "extract_paddock_ndvi",
    "extract_all_paddocks_ndvi",
]
