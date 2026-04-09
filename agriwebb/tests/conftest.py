"""Shared test fixtures and builders."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import respx

# ---------------------------------------------------------------------------
# Animal data builders — used by lambing loader, reports, and MCP tests
# ---------------------------------------------------------------------------


def make_parent(parent_id: str, name: str | None = None, vid: str | None = None) -> dict:
    """Build a parentage entry (sire or dam reference)."""
    return {
        "parentAnimalId": parent_id,
        "parentAnimalIdentity": {"name": name, "vid": vid, "eid": None},
        "parentType": "Genetic",
    }


def make_animal(
    animal_id: str = "a1",
    name: str | None = None,
    vid: str | None = None,
    eid: str | None = None,
    breed: str = "North Country Cheviot",
    sex: str = "Female",
    age_class: str = "ewe",
    birth_year: int = 2022,
    on_farm: bool = True,
    fate: str = "Alive",
    days_reared: int | None = 500,
    sires: list | None = None,
    dams: list | None = None,
    mgmt_group_id: str | None = None,
) -> dict:
    """Build a minimal animal dict matching the animals.json shape."""
    return {
        "animalId": animal_id,
        "identity": {
            "name": name,
            "vid": vid,
            "eid": eid,
            "managementTag": None,
        },
        "characteristics": {
            "breedAssessed": breed,
            "sex": sex,
            "ageClass": age_class,
            "birthYear": birth_year,
            "birthDate": None,
            "speciesCommonName": "Sheep",
            "visualColor": None,
        },
        "state": {
            "onFarm": on_farm,
            "fate": fate,
            "daysReared": days_reared,
            "currentLocationId": None,
            "reproductiveStatus": None,
            "offspringCount": None,
        },
        "parentage": {
            "sires": sires or [],
            "dams": dams or [],
        },
        "managementGroupId": mgmt_group_id,
    }

# Add src/ to path so tests can import agriwebb
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


@pytest.fixture
def mock_openmeteo():
    """Mock Open-Meteo API responses."""
    with respx.mock(base_url="https://api.open-meteo.com", assert_all_called=False) as mock:
        yield mock


@pytest.fixture
def mock_openmeteo_archive():
    """Mock Open-Meteo Archive API responses."""
    with respx.mock(base_url="https://archive-api.open-meteo.com", assert_all_called=False) as mock:
        yield mock


@pytest.fixture
def mock_agriwebb():
    """Mock AgriWebb API responses."""
    with respx.mock(base_url="https://api.agriwebb.com", assert_all_called=False) as mock:
        yield mock


@pytest.fixture
def mock_ncei():
    """Mock NCEI API responses."""
    with respx.mock(base_url="https://www.ncei.noaa.gov", assert_all_called=False) as mock:
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
