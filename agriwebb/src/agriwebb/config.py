from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# .env file is in agriwebb/ directory (parent of src/)
_ENV_FILE = Path(__file__).parent.parent.parent / ".env"


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
    agriwebb_weather_sensor_id: str


settings = Settings()
