"""Per-paddock weather data from Open-Meteo.

The farm-wide weather cache (``weather_historical.json``) is a single point
— fine for a small property but inadequate when the farm spans 22"→50"+
rainfall variation across multiple microclimates. This module fetches a
separate weather timeseries per paddock centroid and caches them in
``.cache/paddock_weather.json``.

Consumers:
- ``pasture.growth.calculate_farm_growth`` accepts
  ``weather_by_paddock`` to use per-paddock weather when available.
- ``pasture.cli.estimate_current_growth`` opportunistically loads the
  per-paddock cache and falls back to farm-wide weather when missing.

The cache format is:
    {
      "fetched_at": "2026-04-10T...",
      "paddocks": {
        "<paddock_name>": {
          "paddock_id": "...",
          "centroid": {"lat": ..., "lon": ...},
          "daily_data": [ DailyWeather, ... ]
        },
        ...
      }
    }
"""

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TypedDict

from agriwebb.weather.openmeteo import DailyWeather, fetch_forecast, fetch_historical


class PaddockWeatherEntry(TypedDict):
    """One paddock's weather timeseries."""

    paddock_id: str
    centroid: dict  # {"lat": float, "lon": float}
    daily_data: list[DailyWeather]


class PaddockWeatherCache(TypedDict):
    """Full per-paddock weather cache structure."""

    fetched_at: str
    paddocks: dict[str, PaddockWeatherEntry]  # keyed by paddock NAME


CACHE_FILENAME = "paddock_weather.json"


def get_cache_path() -> Path:
    # Imported lazily so tests can monkeypatch get_cache_dir after import.
    from agriwebb.core import get_cache_dir

    return get_cache_dir() / CACHE_FILENAME


def load_paddock_weather_cache(cache_path: Path | None = None) -> PaddockWeatherCache | None:
    """Load the per-paddock weather cache. Returns None if missing."""
    path = cache_path or get_cache_path()
    if not path.exists():
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def save_paddock_weather_cache(data: PaddockWeatherCache, cache_path: Path | None = None) -> Path:
    path = cache_path or get_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    return path


def weather_by_paddock_from_cache(
    cache: PaddockWeatherCache | None,
) -> dict[str, list[DailyWeather]]:
    """Flatten a cache into {paddock_name: [DailyWeather, ...]}.

    Returns an empty dict if the cache is missing or malformed.
    """
    if not cache:
        return {}
    return {name: entry.get("daily_data", []) for name, entry in cache.get("paddocks", {}).items()}


async def update_paddock_weather_cache(
    paddocks_with_centroids: dict[str, dict],
    cache_path: Path | None = None,
    refresh: bool = False,
    forecast_days: int = 7,
    verbose: bool = True,
) -> PaddockWeatherCache:
    """Fetch per-paddock weather for every paddock with a centroid.

    Args:
        paddocks_with_centroids: dict[paddock_name → {
            "paddock_id": str,
            "centroid": {"lat": float, "lon": float},
            ...
        }]. The ``paddock_soils.json`` structure already matches this shape.
        cache_path: Cache file path (default .cache/paddock_weather.json).
        refresh: If True, re-fetch full history even if cached.
        forecast_days: Days of forecast to request per paddock.
        verbose: Print progress output.

    Returns:
        Updated PaddockWeatherCache.
    """
    path = cache_path or get_cache_path()
    existing = load_paddock_weather_cache(path) if not refresh else None

    today = date.today()
    archive_end = today - timedelta(days=5)  # Open-Meteo archive lag

    result: PaddockWeatherCache = {
        "fetched_at": datetime.now().isoformat(),
        "paddocks": existing.get("paddocks", {}) if existing else {},
    }

    count = len(paddocks_with_centroids)
    for i, (name, meta) in enumerate(paddocks_with_centroids.items(), 1):
        centroid = meta.get("centroid") or {}
        lat = centroid.get("lat")
        lon = centroid.get("lon")
        if lat is None or lon is None:
            if verbose:
                print(f"  [{i}/{count}] {name}: no centroid, skipping")
            continue

        if verbose:
            print(f"  [{i}/{count}] {name} @ ({lat:.4f}, {lon:.4f})...", end=" ", flush=True)

        cached_entry = result["paddocks"].get(name)
        existing_dates: set[str] = set()
        if cached_entry and not refresh:
            existing_dates = {d["date"] for d in cached_entry.get("daily_data", [])}

        # Determine fetch range
        if existing_dates and cached_entry is not None:
            latest = max(existing_dates)
            latest_date = date.fromisoformat(latest)
            fetch_start = latest_date + timedelta(days=1)
            daily_data = list(cached_entry.get("daily_data", []))
        else:
            fetch_start = date(2018, 1, 1)
            daily_data = []

        try:
            if fetch_start <= archive_end:
                new_history = await fetch_historical(fetch_start, archive_end, lat=lat, lon=lon)
                for record in new_history:
                    if record["date"] not in existing_dates:
                        daily_data.append(record)
                        existing_dates.add(record["date"])

            # Fetch recent + forecast window to cover the archive lag gap
            recent = await fetch_forecast(
                days=forecast_days,
                lat=lat,
                lon=lon,
                include_past_days=14,
            )
            for record in recent:
                record_date = date.fromisoformat(record["date"])
                if record["date"] not in existing_dates:
                    daily_data.append(record)
                    existing_dates.add(record["date"])
                elif record_date > today - timedelta(days=1):
                    # Update forecast days in place
                    for j, existing_rec in enumerate(daily_data):
                        if existing_rec["date"] == record["date"]:
                            daily_data[j] = record
                            break

            daily_data.sort(key=lambda x: x["date"])

            result["paddocks"][name] = PaddockWeatherEntry(
                paddock_id=meta.get("paddock_id", ""),
                centroid={"lat": lat, "lon": lon},
                daily_data=daily_data,
            )

            if verbose:
                print(f"{len(daily_data)} days")
        except Exception as e:
            if verbose:
                print(f"error: {e}")
            # Keep cached data if available
            if cached_entry:
                result["paddocks"][name] = cached_entry

    save_paddock_weather_cache(result, path)
    return result
