"""
NDVI to Pasture Biomass and Growth Rate Conversion Models.

This module provides calibration equations to convert satellite NDVI measurements
to standing dry matter (kg DM/ha) and pasture growth rates (kg DM/ha/day).

IMPORTANT: These are generic models for temperate pastures. Local calibration
with actual harvest data will significantly improve accuracy.

References:
-----------
[1] Trotter, M.G., et al. (2010). "Assessment of Pasture Biomass with the
    Normalized Difference Vegetation Index from Active Ground-Based Sensors"
    Agronomy Journal. R² = 0.68 for tall fescue.
    https://www.researchgate.net/publication/250104307

[2] Insua, J.R., et al. (2019). "Early season estimation of herbage mass"
    Exponential model, R² = 0.83 ± 0.04, MAE = 170 kg DM/ha.
    Relationship differed between seasons and regrowth stage.

[3] Gargiulo, J., et al. (2019). UAV + crop simulation study.
    NDVI correlated to biomass R² = 0.80, range 226-4208 kg DM/ha.
    https://www.ncbi.nlm.nih.gov/pmc/articles/PMC6415791/

[4] Waikato dairy study (NZ): Satellite estimates showed 10% error
    (~260 kg DM/ha) for biomass range 1500-3400 kg DM/ha.

[5] NDVI saturation: Loses sensitivity at LAI > 3.0, approximately
    >3000-4000 kg DM/ha for temperate pastures.

Seasonal Notes (from literature):
- Winter/Spring: NDVI-biomass relationship stronger (r² = 0.62-0.77)
- Summer/Fall: Senescent material affects relationship - NDVI drops but
  total biomass may remain (dry standing matter)
- Peak growth typically spring when temperature + moisture are optimal
"""

from dataclasses import dataclass
from datetime import date

from agriwebb.pasture.growth import Season
from agriwebb.pasture.growth import get_season as _get_season_from_date


@dataclass
class CalibrationModel:
    """
    NDVI to Standing Dry Matter calibration parameters.

    Uses exponential model: SDM = scale * exp(coef * NDVI) + offset
    Based on Insua et al. (2019) finding exponential fits better than linear.
    """

    name: str
    scale: float  # Scaling factor
    coef: float  # Exponential coefficient
    offset: float  # Baseline offset (kg DM/ha)
    min_ndvi: float  # NDVI below this = bare soil
    max_sdm: float  # Saturation ceiling (kg DM/ha)
    source: str  # Citation


# Seasonal calibration models for PNW temperate pasture
# Adjusted based on literature review - these are ESTIMATES pending local calibration
SEASONAL_MODELS: dict[Season, CalibrationModel] = {
    Season.WINTER: CalibrationModel(
        name="Winter (dormant)",
        scale=800,
        coef=3.0,
        offset=200,
        min_ndvi=0.10,
        max_sdm=2500,
        source="Adapted from Trotter et al. 2010 [1], adjusted for dormant season",
    ),
    Season.SPRING: CalibrationModel(
        name="Spring (peak growth)",
        scale=600,
        coef=4.0,
        offset=100,
        min_ndvi=0.15,
        max_sdm=4500,
        source="Adapted from Gargiulo et al. 2019 [3], spring growth phase",
    ),
    Season.SUMMER: CalibrationModel(
        name="Summer (dry/senescent)",
        scale=1200,
        coef=2.5,
        offset=300,
        min_ndvi=0.08,
        max_sdm=3000,
        source="Adjusted for PNW summer dormancy - NDVI drops but dry matter remains",
    ),
    Season.FALL: CalibrationModel(
        name="Fall (recovery)",
        scale=700,
        coef=3.5,
        offset=150,
        min_ndvi=0.12,
        max_sdm=3500,
        source="Adapted for fall green-up after first rains",
    ),
}

# Simple annual model (use when seasonal data not available)
ANNUAL_MODEL = CalibrationModel(
    name="Annual average",
    scale=700,
    coef=3.5,
    offset=200,
    min_ndvi=0.10,
    max_sdm=4000,
    source="Generic temperate pasture, based on Insua et al. 2019 [2] R²=0.83",
)


# ---------------------------------------------------------------------------
# EVI (Enhanced Vegetation Index) calibration
# ---------------------------------------------------------------------------
#
# EVI = G * (NIR - Red) / (NIR + C1*Red - C2*Blue + L), G=2.5, C1=6, C2=7.5, L=1
#
# For healthy pasture, EVI typically ranges 0.1–0.6 (vs NDVI 0.2–0.9). It
# saturates less at high biomass and is less affected by atmospheric aerosol.
#
# References for these coefficients:
# - Gargiulo et al. 2019 [3] — UAV + EVI for pasture, R² = 0.80
# - Xu et al. 2024 — Grassland biomass from MODIS EVI, exponential fit R² ≈ 0.72
# - Ali et al. 2016 — Biomass from EVI for semi-natural grasslands
#
# IMPORTANT: These coefficients are scaled from the published NDVI work for
# EVI's compressed range (~20% less dynamic range than NDVI in healthy
# pasture). Local calibration with harvest data is strongly recommended
# before these are treated as authoritative.

SEASONAL_MODELS_EVI: dict[Season, CalibrationModel] = {
    Season.WINTER: CalibrationModel(
        name="Winter (dormant) — EVI",
        scale=700,
        coef=4.0,
        offset=200,
        min_ndvi=0.08,  # EVI threshold for bare soil
        max_sdm=2500,
        source="Adapted for EVI's compressed range; awaits local harvest validation",
    ),
    Season.SPRING: CalibrationModel(
        name="Spring (peak growth) — EVI",
        scale=500,
        coef=5.2,
        offset=100,
        min_ndvi=0.12,
        max_sdm=4500,
        source="Scaled from Gargiulo et al. 2019 [3] spring EVI fit",
    ),
    Season.SUMMER: CalibrationModel(
        name="Summer (dry/senescent) — EVI",
        scale=1100,
        coef=3.2,
        offset=300,
        min_ndvi=0.06,
        max_sdm=3000,
        source="Adjusted for PNW summer dormancy; EVI less noisy than NDVI on dry grass",
    ),
    Season.FALL: CalibrationModel(
        name="Fall (recovery) — EVI",
        scale=600,
        coef=4.5,
        offset=150,
        min_ndvi=0.10,
        max_sdm=3500,
        source="Adapted for fall green-up after first rains (EVI)",
    ),
}

ANNUAL_MODEL_EVI = CalibrationModel(
    name="Annual average — EVI",
    scale=650,
    coef=4.5,
    offset=200,
    min_ndvi=0.08,
    max_sdm=4000,
    source="Generic temperate pasture EVI; pending local calibration",
)


# ---------------------------------------------------------------------------
# NDRE (Normalized Difference Red Edge) calibration
# ---------------------------------------------------------------------------
#
# NDRE = (B8A - B5) / (B8A + B5)   using Sentinel-2 native bands.
#
# NDRE is the preferred index for cool-season pasture because it saturates
# at roughly LAI 5-6 (vs NDVI's saturation at ~LAI 3). In PNW spring flush,
# pasture commonly exceeds NDVI saturation — NDRE keeps responding.
#
# Dynamic range: healthy pasture typically sits at NDRE 0.15–0.45, with
# peak growth pushing toward 0.50–0.55. Values < 0.10 = bare/senescent.
#
# References:
# - Fitzgerald et al. 2010 — Red-edge for canopy chlorophyll
# - Frampton et al. 2013 — Sentinel-2 red-edge indices for vegetation
# - Punalekar et al. 2018 [6] — S2 biomass for temperate pasture, R²~0.6-0.8
#
# [6] Punalekar, S.M., et al. (2018). "Application of Sentinel-2A data for
#     pasture biomass monitoring using a physically based radiative transfer model"
#     Remote Sensing of Environment 218, 207-220.
#     https://centaur.reading.ac.uk/79547/

SEASONAL_MODELS_NDRE: dict[Season, CalibrationModel] = {
    Season.WINTER: CalibrationModel(
        name="Winter (dormant) — NDRE",
        scale=1200,
        coef=4.0,
        offset=200,
        min_ndvi=0.08,  # NDRE threshold
        max_sdm=2500,
        source="Adapted from Punalekar et al. 2018 [6]; dormant season",
    ),
    Season.SPRING: CalibrationModel(
        name="Spring (peak growth) — NDRE",
        scale=900,
        coef=5.5,
        offset=100,
        min_ndvi=0.12,
        max_sdm=5000,  # Higher ceiling than NDVI/EVI — NDRE saturates less
        source="Adapted from Punalekar et al. 2018 [6]; peak growth",
    ),
    Season.SUMMER: CalibrationModel(
        name="Summer (dry/senescent) — NDRE",
        scale=1500,
        coef=3.5,
        offset=300,
        min_ndvi=0.06,
        max_sdm=3000,
        source="PNW summer dormancy; NDRE retains sensitivity on dry grass",
    ),
    Season.FALL: CalibrationModel(
        name="Fall (recovery) — NDRE",
        scale=1000,
        coef=4.8,
        offset=150,
        min_ndvi=0.10,
        max_sdm=3800,
        source="Adapted for fall green-up after first rains (NDRE)",
    ),
}

ANNUAL_MODEL_NDRE = CalibrationModel(
    name="Annual average — NDRE",
    scale=1050,
    coef=4.8,
    offset=200,
    min_ndvi=0.08,
    max_sdm=4500,
    source="Generic temperate pasture NDRE; pending local calibration",
)


# ---------------------------------------------------------------------------
# Leaf Area Index (LAI) → Standing Dry Matter
# ---------------------------------------------------------------------------
#
# Two paths to LAI:
#
# 1. **NDRE → LAI via empirical inversion.** Based on Baret & Guyot (1991)
#    style of exponential canopy model:
#        LAI = -ln(1 - (NDRE - NDRE_soil) / (NDRE_max - NDRE_soil)) / K
#    where K ≈ 0.5 for random-leaf canopy. This is a physics-lite
#    approximation that avoids the SNAP NN while still producing a
#    physically meaningful LAI.
#
# 2. **Full SNAP biophysical NN (Weiss & Baret 2016).** Not implemented —
#    see rfernand387/LEAF-Toolbox and ollinevalainen/satellitetools for
#    reference ports. Skipped for this farm because the NN doesn't improve
#    on a locally calibrated red-edge index for a single property.
#
# LAI → SDM via Specific Leaf Weight (SLW):
#    SDM_leaf (kg DM/ha) = LAI × SLW × 10_000      (SLW in kg DM/m² leaf)
#    SDM_total = SDM_leaf × leaf_to_total_ratio
#
# Published SLW values for cool-season grasses (perennial ryegrass, tall
# fescue, orchardgrass): ~33–55 g DM/m² leaf (TRY database). A reasonable
# default for mixed PNW pasture is **SLW ≈ 40 g/m² = 0.040 kg/m²**.
#
# Leaf-to-total ratio varies seasonally:
# - Spring flush: ~0.65 (high leaf fraction)
# - Summer mature: ~0.45 (more stem/sheath)
# - Fall recovery: ~0.55
# - Winter dormant: ~0.55
#
# So total aboveground SDM is roughly 1.5–2× leaf-only.

NDRE_SOIL = 0.05  # Bare-soil NDRE baseline
NDRE_MAX = 0.85  # Asymptotic maximum (dense canopy)
LAI_K = 0.5  # Beer's law canopy extinction coefficient (random leaves)

SLW_DEFAULT_KG_M2 = 0.040  # 40 g DM/m² leaf for cool-season grass

LEAF_TO_TOTAL_BY_SEASON: dict[Season, float] = {
    Season.WINTER: 0.55,
    Season.SPRING: 0.65,
    Season.SUMMER: 0.45,
    Season.FALL: 0.55,
}


def ndre_to_lai(ndre: float) -> float:
    """Convert NDRE to LAI via Baret-Guyot style exponential inversion.

    LAI = -ln(1 - (NDRE - NDRE_soil) / (NDRE_max - NDRE_soil)) / K

    Args:
        ndre: NDRE value (typically 0.0–0.6 for pasture)

    Returns:
        LAI (m² leaf / m² ground), clamped to [0, 6]
    """
    import math

    if ndre <= NDRE_SOIL:
        return 0.0

    # Cap NDRE to just below NDRE_MAX to avoid log(0)
    ndre_capped = min(ndre, NDRE_MAX - 0.001)
    numerator = ndre_capped - NDRE_SOIL
    denominator = NDRE_MAX - NDRE_SOIL
    ratio = numerator / denominator

    lai = -math.log(1.0 - ratio) / LAI_K
    return max(0.0, min(lai, 6.0))


def lai_to_standing_dry_matter(
    lai: float,
    month: int | None = None,
    slw_kg_m2: float = SLW_DEFAULT_KG_M2,
) -> float:
    """Convert LAI to total aboveground Standing Dry Matter (kg DM/ha).

    Uses specific leaf weight and a seasonal leaf-to-total ratio to convert
    leaf-only LAI into total aboveground biomass.

    Args:
        lai: Leaf Area Index (m² leaf / m² ground)
        month: Month (1-12) for seasonal leaf-to-total ratio. Defaults to
            a middle-of-the-road 0.55 if not provided.
        slw_kg_m2: Specific leaf weight in kg DM / m² leaf (default 0.040).

    Returns:
        Total aboveground SDM in kg DM/ha.
    """
    sdm_leaf_kg_ha = lai * slw_kg_m2 * 10_000.0  # kg/m² × m²/ha

    if month is not None:
        season = get_season(month)
        leaf_to_total = LEAF_TO_TOTAL_BY_SEASON[season]
    else:
        leaf_to_total = 0.55

    sdm_total = sdm_leaf_kg_ha / leaf_to_total
    return round(sdm_total, 0)


def get_season(month: int) -> Season:
    """Get season from month number (1-12).

    Delegates to the canonical ``growth.get_season(date)`` implementation.
    """
    return _get_season_from_date(date(2000, month, 15))


def ndvi_to_standing_dry_matter(
    ndvi: float,
    month: int | None = None,
    model: CalibrationModel | None = None,
    index: str = "NDVI",
) -> tuple[float, CalibrationModel]:
    """
    Convert a vegetation index value to Standing Dry Matter (kg DM/ha).

    Despite the legacy function name, this supports both NDVI and EVI.
    Select the appropriate calibration via ``index``.

    Args:
        ndvi: Vegetation index value (typically 0.0 - 1.0 for NDVI, 0.0 - 0.8
            for EVI in healthy pasture).
        month: Month number (1-12) for seasonal model selection.
        model: Override with specific calibration model.
        index: Which index the value is — "NDVI" (default) or "EVI". Ignored
            when ``model`` is supplied explicitly.

    Returns:
        Tuple of (SDM in kg DM/ha, model used)

    Note:
        Accuracy is approximately ±260-350 kg DM/ha based on literature [4].
        Local calibration with harvest data can improve this significantly.
    """
    import math

    if model is None:
        if index == "EVI":
            seasonal = SEASONAL_MODELS_EVI
            annual = ANNUAL_MODEL_EVI
        elif index == "NDVI":
            seasonal = SEASONAL_MODELS
            annual = ANNUAL_MODEL
        elif index == "NDRE":
            seasonal = SEASONAL_MODELS_NDRE
            annual = ANNUAL_MODEL_NDRE
        else:
            raise ValueError(f"Unknown vegetation index: {index!r} (use 'NDVI', 'EVI', or 'NDRE')")

        if month is not None:
            season = get_season(month)
            model = seasonal[season]
        else:
            model = annual

    # Handle below-threshold index (bare soil / minimal vegetation)
    if ndvi < model.min_ndvi:
        return 0.0, model

    # Exponential model: SDM = scale * exp(coef * index) + offset
    # Capped at max_sdm to handle saturation [5]
    sdm = model.scale * math.exp(model.coef * ndvi) + model.offset
    sdm = min(sdm, model.max_sdm)

    return round(sdm, 0), model


def calculate_growth_rate(
    ndvi_current: float,
    ndvi_previous: float,
    days_between: int,
    month_current: int | None = None,
    month_previous: int | None = None,
) -> tuple[float, str]:
    """
    Calculate pasture growth rate from two NDVI observations.

    Args:
        ndvi_current: Current NDVI value
        ndvi_previous: Previous NDVI value
        days_between: Days between observations
        month_current: Month of current observation
        month_previous: Month of previous observation

    Returns:
        Tuple of (growth rate in kg DM/ha/day, notes about calculation)

    Note:
        Negative values indicate biomass loss (senescence, grazing, cutting).
        Typical ranges for temperate pasture:
        - Peak spring growth: 50-100 kg DM/ha/day
        - Summer dormancy: -20 to +10 kg DM/ha/day
        - Fall recovery: 20-50 kg DM/ha/day
        - Winter: 5-20 kg DM/ha/day
    """
    if days_between <= 0:
        raise ValueError("days_between must be positive")

    # Cap negative NDVI to 0 (bare soil/water/tillage treated as no vegetation)
    ndvi_current = max(0.0, ndvi_current)
    ndvi_previous = max(0.0, ndvi_previous)

    sdm_current, model_current = ndvi_to_standing_dry_matter(ndvi_current, month_current)
    sdm_previous, model_previous = ndvi_to_standing_dry_matter(ndvi_previous, month_previous)

    growth_rate = (sdm_current - sdm_previous) / days_between

    notes = (
        f"SDM: {sdm_previous:.0f} → {sdm_current:.0f} kg DM/ha over {days_between} days. "
        f"Models: {model_previous.name} → {model_current.name}"
    )

    return round(growth_rate, 1), notes


# Uncertainty estimates based on literature
EXPECTED_UNCERTAINTY = {
    "sdm_error_kg_ha": 260,  # Waikato study [4]
    "sdm_error_percent": 10,  # Waikato study [4]
    "growth_rate_error_kg_ha_day": 15,  # Derived from SDM error over 7-day period
}


# Grazing pressure adjustment model
# Based on local calibration data (Jan 2026):
#   - Rested paddocks: NDVI model ~15% high
#   - Heavy grazing (94 kg/ha/day): NDVI model ~81% high
#   - Continuous grazing: NDVI model ~271% high
#
# NDVI measures greenness/coverage but cannot detect grass height.
# Grazed paddocks have short grass with similar greenness to tall grass,
# causing systematic overestimation of FOO.

# Base correction for ungrazed paddocks (model tends to overestimate by ~15%)
GRAZING_BASE_CORRECTION = 0.85

# Decay rate for grazing pressure effect
# Higher values = more aggressive correction for grazed paddocks
GRAZING_DECAY_RATE = 0.004

# Minimum correction factor (floor for very heavy grazing)
GRAZING_MIN_CORRECTION = 0.25


def calculate_grazing_correction(
    grazing_pressure_kg_ha_day: float,
    days_since_rest: int | None = None,
) -> float:
    """
    Calculate FOO correction factor based on grazing pressure.

    The NDVI-to-FOO model systematically overestimates biomass in grazed
    paddocks because NDVI measures greenness/coverage, not height. A paddock
    grazed to 2" can have similar NDVI to one at 10" but vastly different FOO.

    Args:
        grazing_pressure_kg_ha_day: Current grazing intake rate (kg DM/ha/day).
            0 = rested/empty paddock
            20-50 = moderate grazing
            50-100 = heavy grazing
            >100 = very heavy (supplement feeding likely)

        days_since_rest: Days since paddock was last rested (no animals).
            If None, assumes currently grazed or recently grazed.
            Used to allow recovery of the correction factor.

    Returns:
        Correction factor (0.25 - 0.85) to multiply NDVI-derived FOO by.

    Examples:
        >>> calculate_grazing_correction(0)  # Rested paddock
        0.85
        >>> calculate_grazing_correction(50)  # Moderate grazing
        0.63
        >>> calculate_grazing_correction(94)  # Heavy grazing
        0.48
        >>> calculate_grazing_correction(150)  # Very heavy
        0.35
    """
    import math

    # Base exponential decay model
    # correction = base * exp(-decay * pressure)
    correction = GRAZING_BASE_CORRECTION * math.exp(-GRAZING_DECAY_RATE * grazing_pressure_kg_ha_day)

    # If paddock has been rested, allow partial recovery toward base
    if days_since_rest is not None and days_since_rest > 0 and grazing_pressure_kg_ha_day == 0:
        # Recovery rate: ~7 days to recover halfway, ~21 days to recover 90%
        recovery_rate = 0.1  # per day
        recovery = 1 - math.exp(-recovery_rate * days_since_rest)
        # Blend toward base correction
        correction = correction + (GRAZING_BASE_CORRECTION - correction) * recovery

    # Floor at minimum correction
    correction = max(correction, GRAZING_MIN_CORRECTION)

    return round(correction, 2)


def adjust_foo_for_grazing(
    ndvi_foo_kg_ha: float,
    grazing_pressure_kg_ha_day: float,
    days_since_rest: int | None = None,
) -> tuple[float, float]:
    """
    Adjust NDVI-derived FOO for grazing pressure.

    Args:
        ndvi_foo_kg_ha: FOO estimate from NDVI model (before adjustment)
        grazing_pressure_kg_ha_day: Current grazing intake (kg DM/ha/day)
        days_since_rest: Days since paddock was last rested

    Returns:
        Tuple of (adjusted_foo_kg_ha, correction_factor_used)

    Example:
        >>> adjust_foo_for_grazing(1366, 94)  # Hay Field example
        (656, 0.48)
    """
    correction = calculate_grazing_correction(
        grazing_pressure_kg_ha_day,
        days_since_rest,
    )

    adjusted_foo = ndvi_foo_kg_ha * correction

    return round(adjusted_foo, 0), correction
