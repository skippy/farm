"""Shared cache file loading utilities."""

import json

from agriwebb.core.config import get_cache_dir


def load_cache_json(filename: str, *, key: str | None = None, default=None):
    """Load a JSON file from the cache directory.

    Args:
        filename: Name of the file in the cache directory (e.g., "animals.json")
        key: Optional top-level key to extract from the JSON data
        default: Value to return if the key doesn't exist (only used when key is specified)

    Returns:
        The loaded JSON data, or the value at `key` if specified.

    Raises:
        FileNotFoundError: If the cache file doesn't exist (with a helpful message)
    """
    path = get_cache_dir() / filename
    if not path.exists():
        raise FileNotFoundError(
            f"Cache file not found: {path}\n"
            f"Run the appropriate sync command first to populate the cache."
        )
    with open(path) as f:
        data = json.load(f)
    if key is not None:
        return data.get(key, default if default is not None else [])
    return data
