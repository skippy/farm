"""Satellite modules - Google Earth Engine, NDVI, moss detection."""

from agriwebb.satellite.gee import (
    initialize,
    extract_paddock_ndvi,
    extract_all_paddocks_ndvi,
)

__all__ = [
    "initialize",
    "extract_paddock_ndvi",
    "extract_all_paddocks_ndvi",
]
