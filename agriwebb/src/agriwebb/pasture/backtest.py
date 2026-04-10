"""Historical backtest of the satellite SDM validation gate.

Replays cached monthly NDVI history (``.cache/ndvi_historical.json``)
through the three-layer gate and reports which observations would have
been rejected, smoothed, or passed through.

Useful for:
- Verifying the gate catches the known incidents (e.g. Opalco Dec 2024)
- Spot-checking how the gate behaves on cloudy winter months
- Tuning thresholds before rolling them out farm-wide

Run with ``agriwebb-pasture backtest-gate``.
"""

import argparse
import json
from collections import defaultdict
from datetime import date

from agriwebb.core import get_cache_dir
from agriwebb.pasture.biomass import ndvi_to_standing_dry_matter
from agriwebb.pasture.growth import (
    SEASONAL_MAX_GROWTH,
    get_season,
    soil_quality_factor,
)
from agriwebb.pasture.validate import (
    apply_temporal_filter,
    filter_history_by_span,
    validate_growth_delta,
    validate_ndvi_observation,
)


def load_ndvi_history() -> dict:
    """Load .cache/ndvi_historical.json, or return None if missing."""
    path = get_cache_dir() / "ndvi_historical.json"
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


def _paddock_soil_factor(paddock_soil: dict | None) -> float:
    """Compute the soil quality factor for a paddock (0.7 to 1.2).

    Drainage × organic matter bonus — see pasture/growth.py:soil_quality_factor.
    Used to adjust the farm-wide seasonal max growth into a paddock-specific
    theoretical ceiling.
    """
    if not paddock_soil:
        return 1.0
    soil = paddock_soil.get("soil", {})
    drainage = soil.get("drainage")
    om_raw = soil.get("organic_matter_pct")
    try:
        om_pct = float(om_raw) if om_raw else None
    except (ValueError, TypeError):
        om_pct = None
    return soil_quality_factor(drainage=drainage, organic_matter_pct=om_pct)


def _parse_entry_date(entry: dict) -> date:
    """Parse an NDVI history entry's date field."""
    raw = entry.get("date")
    if not raw:
        return date(entry["year"], entry["month"], 1)
    return date.fromisoformat(raw)


def backtest_paddock(
    paddock_id: str,
    paddock_name: str,
    history: list[dict],
    months_filter: set[int] | None = None,
    area_ha: float | None = None,
    paddock_soil: dict | None = None,
) -> list[dict]:
    """Replay a single paddock's history through the gate.

    Args:
        paddock_id: Paddock ID (for logging only).
        paddock_name: Human-readable paddock name.
        history: List of history entries, each with ndvi_mean, ndvi_stddev,
            cloud_free_pct, pixel_count, date.
        months_filter: If provided, only replay entries whose month is in
            this set (e.g. ``{12, 1}`` for December and January).
        area_ha: Paddock area in hectares, used to scale the L1 pixel
            count threshold for small paddocks.
        paddock_soil: Soil dict from ``paddock_soils.json`` (Fix #2). When
            provided, L2 uses the seasonal max × soil-quality factor as
            the per-paddock ceiling (productive peat/high-OM soils get a
            higher ceiling than poorly drained ones).

    Returns:
        List of per-observation result dicts.
    """
    results = []
    # Compute the paddock's soil-adjusted ceiling once
    soil_factor = _paddock_soil_factor(paddock_soil)
    # Track full accepted-observation history as dicts so we can apply the
    # date-span filter before invoking the temporal filter.
    accepted_history: list[dict] = []

    for entry in history:
        if entry.get("ndvi_mean") is None and entry.get("pixel_count", 0) == 0:
            # No data at all — skip silently
            continue

        entry_date = _parse_entry_date(entry)
        if months_filter and entry_date.month not in months_filter:
            continue

        record: dict = {
            "paddock_name": paddock_name,
            "date": entry_date.isoformat(),
            "month": entry_date.month,
            "year": entry_date.year,
            "ndvi_mean": entry.get("ndvi_mean"),
            "ndvi_stddev": entry.get("ndvi_stddev"),
            "cloud_free_pct": entry.get("cloud_free_pct", 0),
            "pixel_count": entry.get("pixel_count", 0),
        }

        # -------- Layer 1: raw NDVI sanity --------
        layer1 = validate_ndvi_observation(
            ndvi_mean=entry.get("ndvi_mean"),
            ndvi_stddev=entry.get("ndvi_stddev"),
            cloud_free_pct=entry.get("cloud_free_pct", 0) or 0,
            pixel_count=entry.get("pixel_count", 0) or 0,
            area_ha=area_ha,
            scale_m=30,  # Historical cache was fetched at HLS 30m
        )
        if not layer1.valid:
            record["verdict"] = "rejected_l1"
            record["reason"] = layer1.reason
            results.append(record)
            continue

        ndvi = entry["ndvi_mean"]
        sdm, _model = ndvi_to_standing_dry_matter(ndvi, month=entry_date.month)
        record["sdm"] = sdm

        # Span-filter accepted history to avoid straddling seasons (Fix #3).
        recent = filter_history_by_span(accepted_history, entry_date)

        # -------- Layer 2: growth-delta plausibility --------
        if recent:
            prev = recent[-1]
            prev_sdm = prev["sdm"]
            prev_date = date.fromisoformat(prev["date"])
            days_since = (entry_date - prev_date).days or 1

            # Fix #2: farm-wide seasonal constant × per-paddock soil factor.
            # Productive soils (peat, high-OM) get a higher ceiling.
            season = get_season(entry_date).value
            weather_max = SEASONAL_MAX_GROWTH[season] * soil_factor

            layer2 = validate_growth_delta(
                sdm_curr=sdm,
                sdm_prev=prev_sdm,
                days=days_since,
                weather_max_growth_kg_ha_day=weather_max,
            )

            if not layer2.valid:
                record["verdict"] = "rejected_l2"
                record["reason"] = layer2.reason
                record["sdm_prev"] = prev_sdm
                results.append(record)
                continue

        # -------- Layer 3: temporal filter --------
        sdm_values = [h["sdm"] for h in recent]
        filtered, replaced = apply_temporal_filter(sdm_values, sdm)
        if replaced:
            record["verdict"] = "smoothed_l3"
            record["reason"] = f"smoothed {sdm:.0f}→{filtered:.0f}"
            record["sdm_original"] = sdm
            record["sdm"] = filtered
            sdm = filtered
        else:
            record["verdict"] = "passed"
            record["reason"] = ""

        accepted_history.append({"date": entry_date.isoformat(), "sdm": sdm})
        # Keep the in-memory accepted history bounded so long paddock runs
        # don't grow unbounded. 12 months is plenty — the span filter will
        # trim to ~3 months for the actual filter input.
        if len(accepted_history) > 12:
            accepted_history = accepted_history[-12:]

        results.append(record)

    return results


def run_backtest(
    months_filter: set[int] | None = None,
    paddock_name_filter: str | None = None,
) -> dict:
    """Run the backtest across all paddocks in the NDVI historical cache."""
    cache = load_ndvi_history()
    if cache is None:
        raise FileNotFoundError("No .cache/ndvi_historical.json found. Run 'agriwebb-pasture cache' first.")

    # Load per-paddock soils for the per-paddock L2 ceiling (Fix #2).
    # Silently falls back to the farm-wide seasonal constant if missing.
    soils_by_name: dict[str, dict] = {}
    try:
        from agriwebb.pasture.growth import load_paddock_soils

        soils_raw = load_paddock_soils(auto_fetch=False)
        for pname, pdata in soils_raw.items():
            soils_by_name[pname] = pdata
    except Exception:
        pass

    all_results: list[dict] = []
    per_paddock_counts: dict[str, dict[str, int]] = defaultdict(
        lambda: {"passed": 0, "rejected_l1": 0, "rejected_l2": 0, "smoothed_l3": 0}
    )

    paddocks = cache.get("paddocks", {})
    for pid, paddock in paddocks.items():
        name = paddock.get("name", "Unknown")
        if paddock_name_filter and paddock_name_filter.lower() not in name.lower():
            continue
        history = paddock.get("history", [])
        if not history:
            continue

        area_ha = paddock.get("area_ha")
        results = backtest_paddock(
            pid,
            name,
            history,
            months_filter=months_filter,
            area_ha=area_ha,
            paddock_soil=soils_by_name.get(name),
        )
        all_results.extend(results)
        for r in results:
            per_paddock_counts[name][r["verdict"]] += 1

    return {
        "results": all_results,
        "per_paddock": dict(per_paddock_counts),
        "total_observations": len(all_results),
        "verdict_counts": _count_verdicts(all_results),
    }


def _count_verdicts(results: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for r in results:
        counts[r["verdict"]] += 1
    return dict(counts)


def print_backtest_report(backtest: dict, show_details: bool = False) -> None:
    """Human-friendly report of backtest results."""
    total = backtest["total_observations"]
    counts = backtest["verdict_counts"]

    print("=" * 70)
    print("SDM Validation Gate — Historical Backtest")
    print("=" * 70)
    print(f"Observations evaluated: {total}")
    print()
    print(f"  Passed       : {counts.get('passed', 0):>4}")
    print(f"  Rejected L1  : {counts.get('rejected_l1', 0):>4}  (raw sanity: range/stddev/cloud)")
    print(f"  Rejected L2  : {counts.get('rejected_l2', 0):>4}  (growth delta vs weather max)")
    print(f"  Smoothed L3  : {counts.get('smoothed_l3', 0):>4}  (trend-aware temporal filter)")
    print()

    # Per-paddock summary (only show paddocks with any rejections)
    interesting = {
        name: c
        for name, c in backtest["per_paddock"].items()
        if c["rejected_l1"] + c["rejected_l2"] + c["smoothed_l3"] > 0
    }
    if interesting:
        print("Paddocks with rejections or filters:")
        print(f"{'Paddock':<30} {'Pass':>5} {'L1 rej':>7} {'L2 rej':>7} {'L3 smo':>7}")
        print("-" * 60)
        for name in sorted(interesting):
            c = interesting[name]
            print(f"{name:<30} {c['passed']:>5} {c['rejected_l1']:>7} {c['rejected_l2']:>7} {c['smoothed_l3']:>7}")
        print()

    if show_details:
        # Show every rejection / smoothing in chronological order
        incidents = [r for r in backtest["results"] if r["verdict"] in ("rejected_l1", "rejected_l2", "smoothed_l3")]
        if incidents:
            print("Rejection/filter details:")
            print(f"{'Paddock':<25} {'Date':<12} {'Verdict':<14} {'NDVI':>7} {'Stddev':>7}  Reason")
            print("-" * 100)
            incidents.sort(key=lambda r: (r["paddock_name"], r["date"]))
            for r in incidents:
                ndvi_s = f"{r['ndvi_mean']:.3f}" if r.get("ndvi_mean") is not None else "N/A"
                std_s = f"{r['ndvi_stddev']:.3f}" if r.get("ndvi_stddev") is not None else "N/A"
                reason = r["reason"][:50]
                print(f"{r['paddock_name']:<25} {r['date']:<12} {r['verdict']:<14} {ndvi_s:>7} {std_s:>7}  {reason}")


def cli_main(args: argparse.Namespace) -> None:
    """CLI entry point."""
    months_filter = None
    if args.months:
        months_filter = set(args.months)

    backtest = run_backtest(
        months_filter=months_filter,
        paddock_name_filter=args.paddock,
    )

    if args.json:
        print(json.dumps(backtest, indent=2, default=str))
    else:
        print_backtest_report(backtest, show_details=args.details)
