"""Shared test fixtures."""

import sys
from pathlib import Path

import pytest
import respx

# Add src/ to path so tests can import agriwebb
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


@pytest.fixture
def mock_openmeteo():
    """Mock Open-Meteo API responses."""
    with respx.mock(base_url="https://api.open-meteo.com") as mock:
        yield mock


@pytest.fixture
def mock_openmeteo_archive():
    """Mock Open-Meteo Archive API responses."""
    with respx.mock(base_url="https://archive-api.open-meteo.com") as mock:
        yield mock


@pytest.fixture
def mock_agriwebb():
    """Mock AgriWebb API responses."""
    with respx.mock(base_url="https://api.agriwebb.com") as mock:
        yield mock


@pytest.fixture
def mock_ncei():
    """Mock NCEI API responses."""
    with respx.mock(base_url="https://www.ncei.noaa.gov") as mock:
        yield mock


@pytest.fixture
def sample_farm_response():
    """Sample AgriWebb farm query response."""
    return {
        "data": {
            "farms": [
                {
                    "id": "test-farm-id",
                    "name": "Test Farm",
                    "timeZone": "America/Los_Angeles",
                    "address": {
                        "location": {
                            "lat": 48.501762,
                            "long": -123.042906,
                        }
                    },
                }
            ]
        }
    }


@pytest.fixture
def sample_rainfall_response():
    """Sample AgriWebb addRainfalls mutation response."""
    return {
        "data": {
            "addRainfalls": {
                "rainfalls": [
                    {
                        "time": 1768305600000,
                        "mode": "cumulative",
                    }
                ]
            }
        }
    }


@pytest.fixture
def sample_ncei_response():
    """Sample NCEI daily summaries response."""
    return [
        {
            "DATE": "2026-01-15",
            "STATION": "USW00094276",
            "PRCP": "0.25",
            "TMAX": "50",
            "TMIN": "42",
        }
    ]
