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
from datetime import date, timedelta
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
MIN_PIXEL_COUNT_ABSOLUTE = 3  # Hard floor: fewer than 3 pixels is unusable
MIN_PIXEL_COUNT_DEFAULT = 10  # Used when paddock area is not provided
MIN_PIXEL_FRACTION = 0.2  # With known area, require at least 20% of expected pixels

# Growth-delta gate
GROWTH_HEADROOM = 1.5  # Allow NDVI delta up to 1.5x weather model potential

# Temporal filter
TEMPORAL_HISTORY_LIMIT = 6  # Use last N observations for the rolling stats
TEMPORAL_MIN_HISTORY = 4  # Need at least N points (3 deltas) to filter
TEMPORAL_OUTLIER_SIGMA = 3.0  # > Nσ from expected delta = replace
TEMPORAL_HISTORY_KEEP = 30  # Keep at most N observations on disk per paddock
TEMPORAL_MAX_SPAN_DAYS = 90  # Only consider history within this many days of the new observation


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


def _min_pixels_for_area(area_ha: float | None, scale_m: int) -> int:
    """Compute the effective minimum pixel count for a paddock.

    Small paddocks physically cannot produce many pixels at coarse
    satellite resolution. A 0.22 ha paddock at 30m HLS has only ~2 pixels,
    so a fixed threshold of 10 would reject every observation regardless
    of quality. We scale to expected pixel yield instead.

    Args:
        area_ha: Paddock area in hectares. When None, use the module default.
        scale_m: Satellite resolution in meters (30 for HLS, 20 for S2 B5/B8A, 10 for S2 main).

    Returns:
        The minimum acceptable pixel count for this paddock.
    """
    if area_ha is None:
        return MIN_PIXEL_COUNT_DEFAULT
    # Expected pixels = area_m2 / pixel_area_m2
    expected = int(area_ha * 10_000 / (scale_m * scale_m))
    # Require 20% of expected, but never below the absolute floor
    return max(MIN_PIXEL_COUNT_ABSOLUTE, int(expected * MIN_PIXEL_FRACTION))


def validate_ndvi_observation(
    ndvi_mean: float | None,
    ndvi_stddev: float | None = None,
    cloud_free_pct: float = 100.0,
    pixel_count: int = 1_000_000,
    area_ha: float | None = None,
    scale_m: int = 30,
) -> ValidationResult:
    """Sanity-check a single NDVI observation. No reference model needed.

    Args:
        ndvi_mean: NDVI value (typically -1.0 to 1.0). None = no data.
        ndvi_stddev: Spatial stddev across the paddock. High = noisy.
        cloud_free_pct: Percentage of valid pixels in the composite.
        pixel_count: Number of valid pixels actually contributing.
        area_ha: Paddock area in hectares. When provided, the minimum pixel
            count scales to paddock size (20% of expected pixels, floor 3).
            This prevents tiny paddocks like OKF-NW (0.22 ha ≈ 2 HLS pixels)
            from failing every observation on a fixed 10-pixel threshold.
        scale_m: Satellite resolution in meters (default 30 for HLS).

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

    effective_min_pixels = _min_pixels_for_area(area_ha, scale_m)
    if pixel_count < effective_min_pixels:
        reasons.append(f"only {pixel_count} valid pixels (< {effective_min_pixels})")

    return ValidationResult(valid=not reasons, reasons=reasons)


# =============================================================================
# Layer 2: growth-delta plausibility against weather model
# =============================================================================


def validate_growth_delta(
    sdm_curr: float,
    sdm_prev: float,
    days: int,
    weather_max_growth_kg_ha_day: float | None = None,
    weather_max_total_kg_ha: float | None = None,
    headroom: float = GROWTH_HEADROOM,
) -> ValidationResult:
    """Check whether NDVI-derived SDM change is physically possible.

    Compares against the weather model's *potential* growth — i.e., what the
    paddock could grow with optimal soil moisture and no grazing offtake.
    A real observation should be at or below this ceiling; an observation
    well above it is impossible and indicates NDVI noise.

    Provide **either** ``weather_max_growth_kg_ha_day`` (farm-wide per-day
    rate, cheapest) **or** ``weather_max_total_kg_ha`` (per-paddock total
    over the specific window, more accurate — see PR #28 Fix #2). If both
    are given, the total takes precedence.

    Args:
        sdm_curr: Current SDM estimate (kg DM/ha).
        sdm_prev: Previous SDM estimate (kg DM/ha) from last observation.
        days: Days between the two observations.
        weather_max_growth_kg_ha_day: Per-day weather ceiling (kg/ha/day).
            Historically this was the farm-wide ``SEASONAL_MAX_GROWTH``
            constant; new callers should prefer the total form.
        weather_max_total_kg_ha: Total potential growth for the specific
            window (kg/ha), computed from
            :func:`agriwebb.pasture.growth.calculate_farm_growth` for this
            paddock with its own soil and (optionally) per-paddock weather.
        headroom: Multiplicative tolerance above weather max (default 1.5x).

    Returns:
        ValidationResult.valid is False if the observation is implausible.
    """
    reasons: list[str] = []

    if days <= 0:
        return ValidationResult(False, ["days must be positive"])

    if weather_max_total_kg_ha is None and weather_max_growth_kg_ha_day is None:
        return ValidationResult(False, ["no weather ceiling provided"])

    if weather_max_total_kg_ha is not None:
        max_possible_gain = weather_max_total_kg_ha * headroom
    else:
        max_possible_gain = weather_max_growth_kg_ha_day * days * headroom

    delta = sdm_curr - sdm_prev
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


def filter_history_by_span(
    history: list[dict],
    current_date: date,
    max_span_days: int = TEMPORAL_MAX_SPAN_DAYS,
) -> list[dict]:
    """Keep only observations within the last ``max_span_days`` of the current date.

    The temporal filter assumes the history it sees represents a single
    continuous growth regime (same season, same management). Observations
    that are months apart can span seasonal sign flips — spring up, winter
    down — which confuses a delta-based filter into over-correcting
    legitimate seasonal transitions as "spikes."

    This helper drops stale history so the filter only sees recent,
    regime-consistent data. For monthly observations this typically leaves
    2-3 points (below ``TEMPORAL_MIN_HISTORY``), meaning the filter
    gracefully opts out. For weekly/near-real-time data it leaves 12+
    points, so the filter still works as intended.

    Args:
        history: List of observation dicts, each with a ``date`` field
            (ISO string or date object).
        current_date: The date of the new observation being filtered.
        max_span_days: Maximum age of history entries to keep.

    Returns:
        Filtered history list (oldest to newest, subset of input).
    """
    cutoff = current_date - timedelta(days=max_span_days)
    result = []
    for entry in history:
        raw = entry.get("date")
        if isinstance(raw, str):
            try:
                entry_date = date.fromisoformat(raw)
            except ValueError:
                continue
        elif isinstance(raw, date):
            entry_date = raw
        else:
            continue
        if entry_date >= cutoff:
            result.append(entry)
    return result


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

    **Seasonality note:** the filter assumes history represents a
    consistent growth regime. Callers should pre-filter stale entries
    via :func:`filter_history_by_span` before passing values here, so a
    3-month gap between observations doesn't straddle a seasonal sign flip.

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
