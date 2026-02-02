from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

# .env file is in agriwebb/ directory (parent of src/)
# Only use if it exists (CI uses environment variables directly)
# Path: core/config.py -> agriwebb -> src -> agriwebb (package root) -> .env
_ENV_FILE = Path(__file__).parent.parent.parent.parent / ".env"
_ENV_FILE = _ENV_FILE if _ENV_FILE.exists() else None


@lru_cache
def get_cache_dir() -> Path:
    """Get the cache directory (.cache/ in workspace root).

    Looks for project root by finding .git or .claude directory,
    then returns .cache/ within that root.
    """
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / ".git").exists() or (parent / ".claude").exists():
            cache_dir = parent / ".cache"
            cache_dir.mkdir(exist_ok=True)
            return cache_dir
    # Fallback to current working directory
    cache_dir = Path.cwd() / ".cache"
    cache_dir.mkdir(exist_ok=True)
    return cache_dir


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # NOAA/NCEI station for official precipitation data
    ncei_station_id: str

    # AgriWebb API
    agriwebb_api_key: str
    agriwebb_farm_id: str
    agriwebb_weather_sensor_id: str | None = None  # Created via setup command

    # Google Earth Engine
    gee_project_id: str | None = None  # GEE Cloud Project ID

    # Farm timezone (IANA format, e.g., "America/Los_Angeles")
    # If not set, will be fetched from AgriWebb farm data
    tz: str | None = None

    # Pasture growth rate sync tolerance (kg DM/ha/day)
    # Skip syncing if new value is within this tolerance of existing value
    growth_rate_tolerance: float = 1.0

    # Display units for CLI output ("imperial" = °F/inches, "metric" = °C/mm)
    # Note: AgriWebb API always uses metric internally
    display_units: Literal["imperial", "metric"] = "imperial"


settings = Settings()
