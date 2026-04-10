# Pasture Biomass — Design Notes & Research Trail

This document captures why the satellite-derived Standing Dry Matter (SDM)
pipeline looks the way it does, what's been tried, what's been deliberately
deferred, and where to pick up if we revisit.

**Scope:** the `agriwebb/src/agriwebb/satellite/` + `agriwebb/src/agriwebb/pasture/`
modules, specifically `biomass.py`, `validate.py`, `growth.py`, `gee.py`, and
the SDM sync path in `pasture/cli.py:sync_sdm`.

Read the [lambing analysis doc](./lambing-analysis.md) for conventions around
livestock; this doc is purely about pasture measurement.

---

## The problem

Opalco field (paddock `904f77a6`) produced wildly variable SDM in Dec 2024:
swings of ±1,000–2,000 kg/ha between consecutive observations. Root cause,
traced from `.cache/ndvi_historical.json`:

```json
{"date": "2024-12-01", "ndvi_mean": -0.027, "ndvi_stddev": 4.298, ...}
```

A **negative mean NDVI with stddev of 4.3** is physically impossible — NDVI
is bounded in `[-1, 1]`. This is a bad composite (clouds, shadows, edge
effects) leaking through to the SDM pipeline unchallenged.

The lease covers ~270 acres across 8 soil types with rainfall ranging
from 22" to 50+" annually. Microclimate heterogeneity means a single
farm-wide NDVI calibration cannot be "right" for all paddocks at once.
This shaped several of the decisions below.

---

## Current pipeline (as of skippy/farm#28)

```
paddock geometry + date window
        │
        ▼
┌───────────────────────┐
│ Satellite (gee.py)    │   choose index:
│   HLS → NDVI | EVI    │     NDVI  (default, backward compat)
│   S2   → NDRE         │     EVI   (less saturation, aerosol-resistant)
│                       │     NDRE  (red-edge, best for dense pasture)
└───────────┬───────────┘
            │ PaddockNDVI(ndvi_mean, stddev, cloud_free_pct, pixel_count)
            ▼
┌───────────────────────┐
│ Validation gate       │   layer 1: raw sanity (range, stddev, cloud-free)
│ (validate.py)         │   layer 2: growth-delta vs weather model max
│                       │   layer 3: trend-aware temporal filter
└───────────┬───────────┘
            │ accepted observation
            ▼
┌───────────────────────┐
│ Index → SDM           │   exponential per seasonal calibration model
│ (biomass.py)          │   or NDRE → LAI → SDM via SLW
└───────────┬───────────┘
            │ SDM kg DM/ha
            ▼
     AgriWebb API
```

Three independent vegetation indices, three independent calibration dicts in
`biomass.py`:

| Index | Bands used | Source collection | Calibration dict |
|---|---|---|---|
| NDVI | HLS B4, B5 | `NASA/HLS/HLS{L30,S30}/v002` | `SEASONAL_MODELS` |
| EVI | HLS B2, B4, B5 | (same) | `SEASONAL_MODELS_EVI` |
| NDRE | S2 B5, B8A | `COPERNICUS/S2_SR_HARMONIZED` | `SEASONAL_MODELS_NDRE` |

HLS harmonization drops Sentinel-2's native red-edge bands, which is why
NDRE needs its own collection path. S2 SR requires cloud masking via `SCL`
scene classification, which is handled in `_mask_clouds_s2`.

Physics-lite LAI path is also available:

```
NDRE ──► LAI ──► SDM
      ndre_to_lai      lai_to_standing_dry_matter
      (Baret-Guyot)    (SLW × leaf_to_total ratio)
```

Both the empirical NDRE exponential and the physics LAI path produce
similar-order-of-magnitude SDM for typical pasture (verified by
`test_physics_and_empirical_produce_similar_magnitudes`). The LAI path is
physically interpretable; the exponential path is consistent with how we
treat the other indices. Both are available — pick based on downstream use.

---

## The validation gate (`pasture/validate.py`)

Three layers, ordered cheapest → most expensive:

**Layer 1 — Raw observation sanity.** No model needed. Rejects:
- NDVI outside `[-0.1, 1.0]`
- Spatial stddev > 0.25
- Cloud-free pct < 20%
- Pixel count < 10

The Opalco Dec 2024 garbage reading is caught at layer 1 on `stddev`.

**Layer 2 — Growth-delta plausibility.** Compares SDM change against the
weather-driven growth model's **potential** growth:

```
max_possible_gain = SEASONAL_MAX_GROWTH[season] × days × headroom
```

The weather model (`pasture/growth.py`) is fully independent of NDVI —
it uses temperature, precipitation, ET₀, soil drainage, and AWC. This
makes it a legitimate cross-check. An NDVI delta implying growth faster
than weather allows means the NDVI reading is wrong.

Default headroom is 1.5×. `--strict` on the CLI converts layer-2 flags
from warn-and-keep to hard reject.

**Layer 3 — Trend-aware temporal filter.** Delta-based:
- Compute deltas between consecutive observations in history
- If the new delta is >3σ from the median delta, replace it with
  `last_value + median_delta` (the expected continuation of the trend)
- Requires ≥4 prior observations; passes through if history is too short

Why delta-based instead of value-based median? Because spring growth is
real and trending — a value-based median would flag every normal spring
observation as an outlier. The delta approach asks "is this change
consistent with recent changes?" which is noise-invariant to trend.

History persists in `.cache/ndvi_history/<paddock_id>.json`.

---

## Why we did NOT port the SNAP biophysical NN

**TL;DR:** the research said it's not worth the complexity for a single
property, and we'd be at real risk of getting NN coefficients wrong.

### What the SNAP NN is

ESA's official Sentinel-2 L2B biophysical processor (Weiss & Baret 2016,
ATBD_S2ToolBox_L2B_V1.1.pdf). Small neural network:

- **Inputs:** 8 normalized S2 reflectance bands (B3, B4, B5, B6, B7, **B8A**
  — not B8 — B11, B12) + 3 angular terms (cos of view zenith, sun zenith,
  relative azimuth). All 11 inputs min-max normalized to roughly `[-1, 1]`.
- **Architecture:** input layer → 5-neuron hidden layer with tansig
  activation → 1-neuron linear output → de-normalization.
- **Output:** LAI in m²/m² (also fAPAR, fCOVER from sibling networks).

The network weights are NOT in the ATBD body; they live in SNAP's
`auxdata/s2tbx/Biophysical/2_1/S2A/LAI/` as plain text files:
`Weights_Layer1_Neurons.txt`, `Weights_Layer1_Bias.txt`, `Weights_Layer2_Neurons.txt`,
`Weights_Layer2_Bias.txt`, plus `_DomainMin.txt` / `_DomainMax.txt` for
normalization vectors.

### Where to get the weights

- **ESA ATBD** (Weiss & Baret 2016):
  [`step.esa.int/docs/extra/ATBD_S2ToolBox_L2B_V1.1.pdf`](https://step.esa.int/docs/extra/ATBD_S2ToolBox_L2B_V1.1.pdf)
  and the v2.0 update. Architecture documented; weights are not.
- **`ollinevalainen/satellitetools`** on GitHub (actively maintained as of
  Jan 2026): ships the weight files directly under
  `satellitetools/biophys/snap-auxdata/biophysical/2_1/`. **This is the
  cleanest place to copy from if we ever port.** Warning: its README notes
  it skips SNAP's convex-hull domain-validity check.
- **`rfernand387/LEAF-Toolbox`** (CCRS, Canada): canonical GEE port
  (JavaScript). Peer-reviewed via Fernandes et al. 2023
  ([ScienceDirect S0034425723001517](https://www.sciencedirect.com/science/article/pii/S0034425723001517)).
  Last major release Dec 2020 but has the weights embedded in GEE JS
  dictionaries. Not pip-installable; copy-and-adapt only. Use `Source-GEE/`
  as a reference for the exact normalization ranges and domain-validity masks.
- **Not useful:** `IPL-UV/ee_BioNet` is Landsat-only with its own
  PROSAIL-trained net; `MarcYin/Sen2Yield` is crop-specific.

### Accuracy for pasture (what the NN actually buys you)

- **Temperate European grassland (Brandenburg):** R² ≈ 0.62–0.79, NRMSE
  ≈ 19–28% (Punalekar et al.; PFG model-comparison).
- **Pasture biomass (Tasmania, S2 + ML):** R² ≈ 0.60, RMSE ≈ 356 kg DM/ha
  ([MDPI 2072-4292/13/4/603](https://www.mdpi.com/2072-4292/13/4/603)).
- **Green LAI over European sites:** RMSE ≈ 0.67 m²/m², R² ≈ 0.70.
- **Saturation:** SNAP NN outputs cap at LAI 6–8; reliable dynamic range
  for grassland is LAI 0.5–5. Above LAI ~4 in dense swards the signal
  flattens and red-edge indices become more informative than NDVI.

**The punchline:** a well-calibrated red-edge index ceils at the same
R² (~0.6–0.8) as the full NN for grassland. For a one-property farm
we're bounded by the calibration, not the index sophistication.

### When to revisit

Reasons we'd pick this up later:

1. **Publication or formal report** where LAI as a peer-reviewed
   biophysical quantity is required rather than a custom exponential.
2. **Multi-site comparison** with other farms — LAI is standardized, our
   custom exponential is not.
3. **The empirical NDRE calibration hits a wall** we can't close with
   local clip-and-weigh tuning.
4. **Someone packages a clean GEE LAI product** — watch the EE catalog
   for an official `COPERNICUS/S2_L2B` or similar.

If we do pick it up:

1. Copy the weight files from `satellitetools/biophys/snap-auxdata/biophysical/2_1/`
   into `agriwebb/src/agriwebb/satellite/snap_lai/` (preserve attribution).
2. Use `rfernand387/LEAF-Toolbox/Source-GEE/` as a reference for the
   exact input normalization and the domain-validity mask (don't skip this —
   the mask is what keeps the NN honest outside its training envelope).
3. Port is ~40 lines of `ee.Image` arithmetic: matrix multiply + tanh for
   the hidden layer, linear for the output, min-max denormalization.
4. Add as `--index=LAI_SNAP` or similar — don't replace the NDRE path.

---

## Why we did NOT do clip-and-weigh calibration yet

The published SLW for cool-season grass (`SLW_DEFAULT_KG_M2 = 0.040`)
and the NDVI/EVI/NDRE exponential coefficients are all from literature,
not from this farm. Any of the following would likely shift calibration:

- Dominant species (ryegrass vs orchardgrass vs fescue vs mixed)
- Stem/leaf ratio through the season (spring flush vs mature)
- Local sward density (grazed vs rested)

### Local calibration plan (when we're ready)

A clip-and-weigh session is the honest fix. Minimal useful design:

- **Paddocks to sample:** 6–8, chosen to span the soil + microclimate range
  (pick from paddock_soils.json: 2 wet-side, 2 dry-side, 2 mid, plus the
  two worst microclimate outliers)
- **Samples per paddock:** 5 quadrats (0.25 m²) at random positions within
  the paddock interior (>3m from fence)
- **Timing:** 4 sessions through the growing year — late winter dormancy,
  spring peak, summer dormancy, fall recovery
- **Protocol per quadrat:**
  1. Cut to ~3cm stubble
  2. Weigh fresh
  3. Oven-dry at 60°C for 48h
  4. Weigh dry → kg DM/ha = dry_g × 40 (for 0.25 m² quadrat)
- **Satellite pairing:** match each clip date to the nearest clean
  satellite observation (< 7 days offset), record all three indices
  (NDVI, EVI, NDRE) for that paddock
- **Output:** a 4-season × 6-paddock × 3-index calibration table. Fit
  updated exponentials per season per index, replace the literature-derived
  coefficients in `biomass.py`.

Budget: one day in the field per season × 4 sessions = 4 days/year.
Drying is ~2 days of turnaround in a toaster oven.

This is the single biggest win available. Until it happens, the
SDM numbers are "reasonable order of magnitude" but not authoritative.

---

## Per-paddock weather (important prerequisite)

`pasture/growth.py` has always supported per-paddock soil properties
(AWC, drainage, organic matter). It was getting fed a **single farm-wide
weather point** for all paddocks until PR #28. With 22→50" rainfall
variation across the lease, that silently erased the heterogeneity the
model was designed to capture.

Now:

- `weather/paddock_weather.py` fetches Open-Meteo per paddock centroid
  (centroids come from `paddock_soils.json`)
- Cache: `.cache/paddock_weather.json`, keyed by paddock name
- `calculate_farm_growth(weather_by_paddock=...)` uses it opportunistically
  with farm-wide fallback
- `agriwebb-pasture cache` populates it after soils are loaded

**This matters for the validation gate too:** layer 2 (growth-delta
plausibility) uses `SEASONAL_MAX_GROWTH` as a farm-wide constant today.
A future improvement is to compute per-paddock potential growth from
`calculate_farm_growth` output over the same window and use that as the
layer-2 ceiling. Would eliminate false positives on wet paddocks in dry
seasons and false negatives on dry paddocks in wet seasons.

---

## Key references

**Indices and calibration:**
- Trotter et al. 2010 — NDVI + active sensors for pasture biomass, R² = 0.68 for tall fescue
- Insua et al. 2019 — exponential NDVI fit, R² = 0.83 ± 0.04, MAE 170 kg DM/ha
- Gargiulo et al. 2019 — UAV biomass, R² = 0.80, 226–4208 kg DM/ha range ([PMC6415791](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC6415791/))
- Waikato dairy study — satellite RMSE ≈ 260 kg DM/ha over 1500–3400 kg/ha
- Punalekar et al. 2018 — Sentinel-2 for temperate pasture via radiative transfer ([Reading centaur](https://centaur.reading.ac.uk/79547/))
- Frampton et al. 2013 — Sentinel-2 red-edge indices for vegetation
- Fitzgerald et al. 2010 — red-edge canopy chlorophyll

**SNAP biophysical processor:**
- Weiss & Baret 2016 — [ATBD v1.1](https://step.esa.int/docs/extra/ATBD_S2ToolBox_L2B_V1.1.pdf) / [ATBD v2.0](https://step.esa.int/docs/extra/ATBD_S2ToolBox_V2.0.pdf)
- Fernandes et al. 2023 — LEAF-Toolbox validation over North American forests
- [`rfernand387/LEAF-Toolbox`](https://github.com/rfernand387/LEAF-Toolbox) — canonical GEE JS port (CCRS)
- [`ollinevalainen/satellitetools`](https://github.com/ollinevalainen/satellitetools) — Python port with bundled weights

**Weather-driven growth model:**
- McCall & Bishop-Hurley 2003 — whole-farm dairy pasture growth model
- Romera et al. 2009 — Pasture Simulation model
- CSIRO GRAZPLAN / GrassGro — the operational AU/NZ benchmark

**Specific leaf weight for cool-season grasses:**
- [TRY Plant Trait Database](https://www.try-db.org/) — SLA for *Lolium perenne* ~20–30 m²/kg DM
- Ryegrass / fescue / orchardgrass cluster around SLW 33–55 g/m² leaf

---

## Historical backtest findings (PR #28 baseline)

Running `agriwebb-pasture backtest-gate` against `.cache/ndvi_historical.json`
(8 years × 35 paddocks × ~97 months = 3,098 observations) produced the
following baseline with the gate as shipped:

| Layer | Count | % | Notes |
|---|---|---|---|
| Passed | 1,805 | 58.3% | |
| L1 rejected (raw sanity) | 243 | 7.8% | Mostly cloud-contaminated winter composites |
| L2 rejected (growth delta) | 466 | 15.0% | **Probably too strict — see below** |
| L3 smoothed (temporal filter) | 584 | 18.8% | **Over-corrects seasonal transitions — see below** |

### What works
- **Opalco Dec 2024 is caught.** NDVI=-0.027, stddev=4.298 rejected at L1
  on stddev — the motivating incident is handled.
- **A macro cloud event in Dec 2024** shows up across ~15 paddocks with
  wildly negative NDVI means and huge stddevs. All caught at L1.
- Winter/Jan observations have ~22% L1 rejection rate, which matches the
  known PNW winter cloud cover reality — the gate reflects the truth.

### Known tuning issues the backtest surfaced

1. **OKF-NW is too small (0.22 ha = ~6 HLS pixels).** Every observation
   fails the `MIN_PIXEL_COUNT = 10` threshold. Options: (a) special-case
   small paddocks to a lower threshold, (b) exclude OKF-NW from SDM sync,
   (c) use 10m S2 data for small paddocks instead of 30m HLS. **Tall** (3.36 ha)
   has a similar issue in some months, suggesting the HLS 30m scale is the
   real constraint.

2. **Layer 2 is too strict for high-productivity paddocks.** FBS shows 7
   winter L2 rejections for growth deltas of 700–900 kg/ha/30d against a
   cap of 675 kg/ha/30d (winter max 15 kg/ha/day × 30 × 1.5 headroom).
   These are probably *real* winter growth on peat or high-OM soil that
   exceeds the farm-wide seasonal constant. Fix: replace the `SEASONAL_MAX_GROWTH`
   constant with a per-paddock potential growth computed from
   `calculate_farm_growth` over the same window (now possible since Part 4
   landed per-paddock weather).

3. **Layer 3 over-corrects seasonal transitions.** Multiple OKF-* paddocks
   show legitimate Dec/Jan NDVI drops being "smoothed up" toward summer
   baseline values. The delta-based filter handles *linear* trends but
   fails on seasonal sign changes. Options:
   - Require history to be within the same season
   - Detrend via a simple harmonic before filtering
   - Narrow the history window (currently 6 months → could be 3)
   - Skip L3 entirely for Dec/Jan transitions
4. **The 18.8% L3 smoothing rate is suspicious.** If the filter is legitimately
   correcting spikes, ~5% would be expected. 18.8% suggests systematic
   over-correction, most likely from issue #3 above.

### How to re-run

```bash
# Full backtest across all months
agriwebb-pasture backtest-gate

# Winter only (Dec + Jan)
agriwebb-pasture backtest-gate --months 12 1

# Single paddock, with per-observation details
agriwebb-pasture backtest-gate --paddock Opalco --details

# JSON for scripting
agriwebb-pasture backtest-gate --json > .cache/gate_backtest.json
```

The backtest lives in `agriwebb/src/agriwebb/pasture/backtest.py`. It uses
the cached monthly NDVI history from `.cache/ndvi_historical.json` — no
live satellite calls, fast to iterate on. Re-run after any threshold
change to see the effect.

---

## Quick reference: current constants

| Constant | Value | Source | File |
|---|---|---|---|
| `NDVI_MIN_VALID` | -0.1 | Below = water/cloud/shadow | `validate.py` |
| `NDVI_MAX_STDDEV` | 0.25 | Above = unreliable composite | `validate.py` |
| `MIN_CLOUD_FREE_PCT` | 20.0 | Below = bad composite | `validate.py` |
| `MIN_PIXEL_COUNT` | 10 | Below = paddock too small/cloudy | `validate.py` |
| `GROWTH_HEADROOM` | 1.5 | Layer-2 tolerance above weather max | `validate.py` |
| `TEMPORAL_OUTLIER_SIGMA` | 3.0 | Layer-3 spike threshold | `validate.py` |
| `NDRE_SOIL` | 0.05 | Bare-soil NDRE baseline | `biomass.py` |
| `NDRE_MAX` | 0.85 | Asymptotic max NDRE | `biomass.py` |
| `LAI_K` | 0.5 | Beer's law random-leaf canopy | `biomass.py` |
| `SLW_DEFAULT_KG_M2` | 0.040 | Cool-season grass avg (TRY) | `biomass.py` |
| `SEASONAL_MAX_GROWTH[spring]` | 80 kg/ha/day | PNW maritime benchmark | `growth.py` |
| `SEASONAL_MAX_GROWTH[summer]` | 25 kg/ha/day | PNW summer dormancy | `growth.py` |
| `SEASONAL_MAX_GROWTH[fall]` | 50 kg/ha/day | Fall recovery | `growth.py` |
| `SEASONAL_MAX_GROWTH[winter]` | 15 kg/ha/day | Winter maintenance | `growth.py` |
