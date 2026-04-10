"""Validation gate for satellite-derived pasture observations.

Filters out garbage NDVI/SDM readings before they propagate to AgriWebb.
Three layers, in order of increasing cost:

1. **Raw observation sanity** — range, stddev, cloud-free pct, pixel count.
   No model needed. Catches the obvious garbage (negative NDVI from clouds,
   high-stddev observations, sparse pixel counts).

2. **Growth-delta plausibility** — compares the change in SDM since the last
   observation against the weather model's *potential* growth (no grazing
   offtake). NDVI cannot legitimately say a paddock grew faster than weather
   physically allows.

3. **Temporal smoothing** — replaces single-point spikes with the rolling
   median of recent observations. Catches isolated outliers that pass the
   first two layers.

Each paddock's recent observation history is stored in
``.cache/ndvi_history/<paddock_id>.json`` so the temporal filter has context
across runs.

Reference incident: Opalco field Dec 2024 — NDVI=-0.027, stddev=4.298
produced wild SDM swings of ±1500 kg/ha between consecutive observations.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median, stdev

# =============================================================================
# Quality thresholds
# =============================================================================

# Raw observation gates
NDVI_MIN_VALID = -0.1  # Below this is water/cloud/shadow
NDVI_MAX_VALID = 1.0
NDVI_MAX_STDDEV = 0.25  # Spatial heterogeneity above this = unreliable
MIN_CLOUD_FREE_PCT = 20.0  # Below this = composite is mostly cloud
MIN_PIXEL_COUNT = 10  # Below this = paddock too small or too cloudy

# Growth-delta gate
GROWTH_HEADROOM = 1.5  # Allow NDVI delta up to 1.5x weather model potential

# Temporal filter
TEMPORAL_HISTORY_LIMIT = 6  # Use last N observations for the rolling stats
TEMPORAL_MIN_HISTORY = 4  # Need at least N points (3 deltas) to filter
TEMPORAL_OUTLIER_SIGMA = 3.0  # > Nσ from expected delta = replace
TEMPORAL_HISTORY_KEEP = 30  # Keep at most N observations on disk per paddock


# =============================================================================
# Result type
# =============================================================================


@dataclass
class ValidationResult:
    """Result of a single validation step."""

    valid: bool
    reasons: list[str] = field(default_factory=list)

    @property
    def reason(self) -> str:
        return "; ".join(self.reasons) if self.reasons else ""


# =============================================================================
# Layer 1: raw NDVI observation sanity
# =============================================================================


def validate_ndvi_observation(
    ndvi_mean: float | None,
    ndvi_stddev: float | None = None,
    cloud_free_pct: float = 100.0,
    pixel_count: int = 1_000_000,
) -> ValidationResult:
    """Sanity-check a single NDVI observation. No reference model needed.

    Args:
        ndvi_mean: NDVI value (typically -1.0 to 1.0). None = no data.
        ndvi_stddev: Spatial stddev across the paddock. High = noisy.
        cloud_free_pct: Percentage of valid pixels in the composite.
        pixel_count: Number of valid pixels actually contributing.

    Returns:
        ValidationResult.valid is False if any check fails.
    """
    reasons: list[str] = []

    if ndvi_mean is None:
        return ValidationResult(False, ["no NDVI value"])

    if not (NDVI_MIN_VALID <= ndvi_mean <= NDVI_MAX_VALID):
        reasons.append(f"NDVI {ndvi_mean:.3f} outside [{NDVI_MIN_VALID}, {NDVI_MAX_VALID}]")

    if ndvi_stddev is not None and ndvi_stddev > NDVI_MAX_STDDEV:
        reasons.append(f"NDVI stddev {ndvi_stddev:.3f} > {NDVI_MAX_STDDEV} (too noisy)")

    if cloud_free_pct < MIN_CLOUD_FREE_PCT:
        reasons.append(f"cloud-free {cloud_free_pct:.0f}% < {MIN_CLOUD_FREE_PCT:.0f}%")

    if pixel_count < MIN_PIXEL_COUNT:
        reasons.append(f"only {pixel_count} valid pixels (< {MIN_PIXEL_COUNT})")

    return ValidationResult(valid=not reasons, reasons=reasons)


# =============================================================================
# Layer 2: growth-delta plausibility against weather model
# =============================================================================


def validate_growth_delta(
    sdm_curr: float,
    sdm_prev: float,
    days: int,
    weather_max_growth_kg_ha_day: float,
    headroom: float = GROWTH_HEADROOM,
) -> ValidationResult:
    """Check whether NDVI-derived SDM change is physically possible.

    Compares against the weather model's *potential* growth — i.e., what the
    paddock could grow with optimal soil moisture and no grazing offtake.
    A real observation should be at or below this ceiling; an observation
    well above it is impossible and indicates NDVI noise.

    Args:
        sdm_curr: Current SDM estimate (kg DM/ha).
        sdm_prev: Previous SDM estimate (kg DM/ha) from last observation.
        days: Days between the two observations.
        weather_max_growth_kg_ha_day: Weather model's max potential growth
            for the season/period (kg DM/ha/day). See pasture/growth.py
            SEASONAL_MAX_GROWTH for typical values.
        headroom: Multiplicative tolerance above weather max (default 1.5x).

    Returns:
        ValidationResult.valid is False if the observation is implausible.
    """
    reasons: list[str] = []

    if days <= 0:
        return ValidationResult(False, ["days must be positive"])

    delta = sdm_curr - sdm_prev
    max_possible_gain = weather_max_growth_kg_ha_day * days * headroom

    if delta > max_possible_gain:
        reasons.append(
            f"SDM gained {delta:.0f} kg/ha in {days}d, weather max is {max_possible_gain:.0f} kg/ha (impossible)"
        )

    # Floor: SDM cannot drop below zero. A drop of more than the entire prior
    # standing biomass is impossible — this catches "garbage NDVI says
    # everything disappeared" cases.
    if sdm_curr < 0:
        reasons.append(f"negative SDM {sdm_curr:.0f} (impossible)")

    return ValidationResult(valid=not reasons, reasons=reasons)


# =============================================================================
# Layer 3: temporal smoothing
# =============================================================================


def apply_temporal_filter(
    history: list[float],
    new_value: float,
    sigma: float = TEMPORAL_OUTLIER_SIGMA,
) -> tuple[float, bool]:
    """Replace single-point outliers using a delta-based (trend-aware) filter.

    Examines the distribution of differences between consecutive observations
    in the history. If the new observation's delta from the last point is
    more than ``sigma`` standard deviations from the typical delta, it's
    replaced with ``last_value + median_delta`` (the expected continuation
    of the trend).

    This is trend-aware: a paddock growing linearly through spring passes
    cleanly, but a single-point spike (or drop) gets replaced with the
    expected next step.

    Requires at least ``TEMPORAL_MIN_HISTORY`` prior observations (so we
    have ≥3 deltas to compute stats from). Otherwise passes through.

    Args:
        history: Recent values (oldest to newest).
        new_value: The new observation to filter.
        sigma: Outlier threshold in standard deviations of the delta
            distribution. Default is 3.0 — deliberately generous since the
            stdev of 3 deltas is itself a noisy estimator.

    Returns:
        (filtered_value, was_replaced)
    """
    if len(history) < TEMPORAL_MIN_HISTORY:
        return new_value, False

    deltas = [history[i + 1] - history[i] for i in range(len(history) - 1)]
    expected_delta = median(deltas)
    sd = stdev(deltas)

    if sd == 0:
        # Perfectly linear (or flat) history — any change is equally valid.
        return new_value, False

    new_delta = new_value - history[-1]
    if abs(new_delta - expected_delta) > sigma * sd:
        return history[-1] + expected_delta, True

    return new_value, False


# =============================================================================
# History cache
# =============================================================================


def get_ndvi_history_dir() -> Path:
    """Get the directory where per-paddock NDVI history is cached."""
    from agriwebb.core import get_cache_dir

    d = get_cache_dir() / "ndvi_history"
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_paddock_history(paddock_id: str, limit: int = TEMPORAL_HISTORY_LIMIT) -> list[dict]:
    """Load the most recent NDVI observations for a paddock.

    Returns a list of dicts (oldest to newest), each with at least:
        date, ndvi, sdm, ndvi_stddev, cloud_free_pct
    Empty list if no history exists.
    """
    path = get_ndvi_history_dir() / f"{paddock_id}.json"
    if not path.exists():
        return []
    try:
        with open(path) as f:
            records = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(records, list):
        return []
    return records[-limit:]


def append_paddock_history(paddock_id: str, record: dict) -> None:
    """Append a new observation to a paddock's history file.

    Keeps at most TEMPORAL_HISTORY_KEEP observations on disk.
    """
    path = get_ndvi_history_dir() / f"{paddock_id}.json"
    history: list[dict] = []
    if path.exists():
        try:
            with open(path) as f:
                loaded = json.load(f)
            if isinstance(loaded, list):
                history = loaded
        except (json.JSONDecodeError, OSError):
            history = []
    history.append(record)
    history = history[-TEMPORAL_HISTORY_KEEP:]
    with open(path, "w") as f:
        json.dump(history, f, indent=2)
