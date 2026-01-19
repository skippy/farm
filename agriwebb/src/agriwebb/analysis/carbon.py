"""
Carbon Flux and Sequestration Estimation from Satellite Data.

This module estimates carbon-related metrics for pastures using NDVI
and weather data, based on established ecosystem models.

Key Metrics:
- GPP (Gross Primary Production): Total carbon fixed by photosynthesis
- NPP (Net Primary Production): GPP minus plant respiration
- Carbon stock: Carbon stored in above-ground biomass
- NEE (Net Ecosystem Exchange): Net carbon flux (negative = sequestration)

References:
-----------
[1] Running, S.W., et al. (2004). "A Continuous Satellite-Derived Measure
    of Global Terrestrial Primary Production." BioScience 54(6):547-560.
    MOD17 GPP/NPP algorithm.

[2] Potter, C.S., et al. (1993). "Terrestrial ecosystem production:
    A process model based on global satellite and surface data."
    Global Biogeochemical Cycles 7(4):811-841. CASA model.

[3] Gilmanov, T.G., et al. (2010). "Productivity, Respiration, and
    Light-Response Parameters of World Grassland and Agroecosystems."
    Rangeland Ecology & Management 63(1):16-39.

[4] Soussana, J.F., et al. (2007). "Full accounting of the greenhouse
    gas (CO2, N2O, CH4) budget of nine European grassland sites."
    Agriculture, Ecosystems & Environment 121:121-134.

Typical Values for Temperate Pastures:
- GPP: 800-2000 g C/m²/year (8-20 t C/ha/year)
- NPP: 400-1000 g C/m²/year (4-10 t C/ha/year)
- Soil C sequestration: 0.3-0.8 t C/ha/year (well-managed grassland)
- Livestock CH4: 80-120 kg CH4/head/year (beef cattle)
"""

import math
from dataclasses import dataclass
from datetime import date
from enum import Enum

# Carbon content of dry matter (typical for grasses)
CARBON_FRACTION = 0.45  # 45% of dry matter is carbon

# CO2 equivalent factors
CO2_PER_C = 44 / 12  # 3.67 kg CO2 per kg C
CH4_GWP = 28  # Global Warming Potential of CH4 (100-year, AR5)


# =============================================================================
# Methane Adjustment Factors
# Based on IPCC Tier 2/3 methodologies and recent research
# =============================================================================


@dataclass
class DietAdjustments:
    """
    Diet-based methane adjustment factors.

    These multiply the base emission factor to account for diet composition.
    Reference: IPCC 2019 Refinement, Vol 4, Ch 10 + recent research.
    """

    # Base forage types (relative to standard pasture = 1.0)
    forage_factors: dict = None

    # Feed additives/supplements
    additive_factors: dict = None

    def __post_init__(self):
        # Forage type affects methane yield (Ym - % of gross energy lost as CH4)
        # Higher fiber = more methane; higher digestibility = less methane
        self.forage_factors = {
            "pasture_fresh": 1.0,  # Base case - fresh grass
            "pasture_dry": 1.05,  # Dry/dormant pasture - slightly more
            "hay": 1.08,  # Dried hay - higher fiber, harder to digest
            "haylage": 0.95,  # Fermented, more digestible
            "silage": 0.92,  # Well-fermented silage
            "grain_supplement": 0.85,  # Grain reduces CH4 per unit feed
            "legume_pasture": 0.90,  # Clover/alfalfa - more digestible
        }

        # Additives that modify enteric fermentation
        # Research sources noted in comments
        self.additive_factors = {
            "none": 1.0,
            # Seaweeds - bromoform inhibits methanogenic archaea
            "kelp": 0.88,  # Brown seaweed: 10-15% reduction (Kinley et al. 2016)
            "asparagopsis": 0.35,  # Red seaweed: 50-80% reduction! (Roque et al. 2021)
            "seaweed_meal": 0.85,  # Generic seaweed supplement
            # Other additives
            "tannins": 0.90,  # Condensed tannins: ~10% reduction
            "lipids_oils": 0.85,  # Fat supplementation: ~15% reduction
            "nitrate": 0.80,  # Nitrate: ~20% reduction (requires careful management)
            "3nop": 0.70,  # 3-nitrooxypropanol (Bovaer): ~30% reduction
        }


DIET_ADJUSTMENTS = DietAdjustments()


@dataclass
class BreedAdjustments:
    """
    Breed-based methane adjustment factors for sheep.

    Smaller breeds and more efficient converters produce less methane.
    Based on body weight and feed efficiency relationships.
    """

    # Relative to medium-sized meat breed = 1.0
    breed_factors: dict = None

    def __post_init__(self):
        self.breed_factors = {
            # Large meat breeds
            "suffolk": 1.05,
            "hampshire": 1.05,
            "texel": 1.00,
            "dorset": 1.00,
            # Medium breeds
            "corriedale": 1.00,
            "columbia": 1.00,
            "north_country_cheviot": 0.95,  # Efficient hill breed
            "cheviot": 0.95,
            # Smaller/primitive breeds
            "shetland": 0.80,
            "soay": 0.75,
            "icelandic": 0.85,
            # Hair sheep (no wool = different metabolism)
            "katahdin": 0.90,
            "dorper": 0.95,
            "st_croix": 0.85,
            # Wool breeds (different feed partitioning)
            "merino": 0.90,
            "rambouillet": 0.92,
            # Prolific/dairy breeds
            "finnish_landrace": 0.85,  # Small, efficient, prolific
            "finnsheep": 0.85,
            "finn": 0.85,
            "east_friesian": 1.10,  # Large dairy breed, high intake
            "lacaune": 1.05,  # Dairy breed
            "awassi": 1.05,  # Dairy breed
            # Longwool breeds
            "bluefaced_leicester": 1.00,
            "bfl": 1.00,
            "leicester_longwool": 1.02,
            "lincoln": 1.05,  # Very large
            "cotswold": 1.03,
            # Crosses
            "1st_cross": 1.00,
            "crossbred": 1.00,
            "mule": 0.95,  # BFL x hill breed - efficient
            # Default
            "unknown": 1.00,
        }


BREED_ADJUSTMENTS = BreedAdjustments()


@dataclass
class ClimateAdjustments:
    """
    Climate-based adjustments to methane production.

    Cold climates require more feed for maintenance = more methane.
    Hot climates reduce feed intake = less methane (but heat stress issues).
    """

    # Relative to temperate (10-20°C avg) = 1.0
    climate_factors: dict = None

    def __post_init__(self):
        self.climate_factors = {
            "cold": 1.10,  # < 5°C average - more feed needed
            "cool": 1.05,  # 5-10°C average
            "temperate": 1.00,  # 10-20°C average (base case)
            "warm": 0.95,  # 20-25°C average
            "hot": 0.90,  # > 25°C average - reduced intake
        }


CLIMATE_ADJUSTMENTS = ClimateAdjustments()


class PastureType(Enum):
    """Pasture management types affecting carbon dynamics."""

    INTENSIVE = "intensive"  # High input, frequent grazing
    MODERATE = "moderate"  # Rotational grazing
    EXTENSIVE = "extensive"  # Low input, infrequent grazing
    SILVOPASTURE = "silvopasture"  # Trees + pasture


@dataclass
class CarbonFluxResult:
    """Results from carbon flux calculations."""

    # Gross Primary Production (kg C/ha/day)
    gpp: float
    # Net Primary Production (kg C/ha/day)
    npp: float
    # Ecosystem respiration estimate (kg C/ha/day)
    respiration: float
    # Net Ecosystem Exchange (kg C/ha/day, negative = sequestration)
    nee: float
    # Above-ground carbon stock (kg C/ha)
    carbon_stock: float
    # Model notes
    notes: str


@dataclass
class LUEParams:
    """Light Use Efficiency parameters for GPP calculation."""

    # Maximum LUE under optimal conditions (g C / MJ PAR)
    lue_max: float
    # Temperature optimum (°C)
    t_opt: float
    # Temperature range for growth (°C)
    t_min: float
    t_max: float
    # Source citation
    source: str


# LUE parameters for temperate C3 grasslands
# Based on MOD17 and CASA literature
PASTURE_LUE = LUEParams(
    lue_max=1.2,  # g C / MJ PAR (Gilmanov et al. 2010)
    t_opt=20.0,  # Optimal temperature for C3 grasses
    t_min=0.0,  # Minimum temperature for growth
    t_max=35.0,  # Maximum temperature for growth
    source="Gilmanov et al. 2010 [3], C3 temperate grasslands",
)


def ndvi_to_fpar(ndvi: float) -> float:
    """
    Convert NDVI to fPAR (fraction of absorbed PAR).

    Uses linear relationship from MOD17 algorithm [1].
    fPAR = (NDVI - NDVImin) / (NDVImax - NDVImin) * (fPARmax - fPARmin) + fPARmin

    Args:
        ndvi: NDVI value (0-1)

    Returns:
        fPAR value (0-0.95)
    """
    # Clamp NDVI
    ndvi = max(0.0, min(1.0, ndvi))

    # Parameters from MOD17
    ndvi_min = 0.08
    ndvi_max = 0.86
    fpar_min = 0.01
    fpar_max = 0.95

    if ndvi <= ndvi_min:
        return fpar_min
    if ndvi >= ndvi_max:
        return fpar_max

    fpar = (ndvi - ndvi_min) / (ndvi_max - ndvi_min) * (fpar_max - fpar_min) + fpar_min
    return round(fpar, 3)


def estimate_par(latitude: float, day_of_year: int) -> float:
    """
    Estimate daily PAR (Photosynthetically Active Radiation) in MJ/m²/day.

    Simplified model based on latitude and day of year.
    PAR is approximately 45-50% of total solar radiation.

    Args:
        latitude: Latitude in degrees (negative for southern hemisphere)
        day_of_year: Day of year (1-365)

    Returns:
        PAR in MJ/m²/day
    """
    # Solar constant and PAR fraction
    solar_constant = 1361  # W/m²
    par_fraction = 0.48  # PAR is ~48% of total solar

    # Calculate solar declination
    declination = 23.45 * math.sin(math.radians((284 + day_of_year) * 360 / 365))

    # Calculate day length (hours)
    lat_rad = math.radians(latitude)
    dec_rad = math.radians(declination)

    # Hour angle at sunrise/sunset
    cos_hour_angle = -math.tan(lat_rad) * math.tan(dec_rad)
    cos_hour_angle = max(-1, min(1, cos_hour_angle))  # Clamp for polar regions
    hour_angle = math.degrees(math.acos(cos_hour_angle))
    _day_length = 2 * hour_angle / 15  # hours (unused but kept for reference)

    # Extraterrestrial radiation
    dr = 1 + 0.033 * math.cos(2 * math.pi * day_of_year / 365)

    # Daily extraterrestrial radiation (MJ/m²/day)
    ra = (
        (24 * 60 / math.pi)
        * solar_constant
        * dr
        * (
            math.radians(hour_angle) * math.sin(lat_rad) * math.sin(dec_rad)
            + math.cos(lat_rad) * math.cos(dec_rad) * math.sin(math.radians(hour_angle))
        )
        / 1e6
    )  # Convert to MJ

    # Apply atmospheric transmission (~75% on clear day, ~40% on cloudy)
    # Use average for PNW (~55% due to frequent clouds)
    atmospheric_transmission = 0.55

    # PAR
    par = ra * atmospheric_transmission * par_fraction

    return round(max(0, par), 2)


def temperature_scalar(temp: float, params: LUEParams = PASTURE_LUE) -> float:
    """
    Calculate temperature stress scalar for LUE.

    Uses a simple ramp function based on optimal temperature range.

    Args:
        temp: Air temperature in °C
        params: LUE parameters

    Returns:
        Temperature scalar (0-1)
    """
    if temp <= params.t_min or temp >= params.t_max:
        return 0.0

    if temp <= params.t_opt:
        return (temp - params.t_min) / (params.t_opt - params.t_min)
    else:
        return (params.t_max - temp) / (params.t_max - params.t_opt)


def calculate_gpp(
    ndvi: float,
    latitude: float = 48.5,  # San Juan Islands default
    day_of_year: int | None = None,
    temperature: float | None = None,
    par_override: float | None = None,
) -> tuple[float, str]:
    """
    Calculate Gross Primary Production (GPP) from NDVI.

    Uses simplified MOD17/CASA approach:
    GPP = LUE × fPAR × PAR × temperature_scalar

    Args:
        ndvi: NDVI value
        latitude: Latitude for PAR estimation
        day_of_year: Day of year (1-365), defaults to today
        temperature: Air temperature in °C (optional, improves accuracy)
        par_override: Override PAR value (MJ/m²/day)

    Returns:
        Tuple of (GPP in kg C/ha/day, notes)
    """
    if day_of_year is None:
        day_of_year = date.today().timetuple().tm_yday

    # Calculate fPAR from NDVI
    fpar = ndvi_to_fpar(ndvi)

    # Get PAR
    if par_override is not None:
        par = par_override
    else:
        par = estimate_par(latitude, day_of_year)

    # Temperature scalar
    if temperature is not None:
        t_scalar = temperature_scalar(temperature)
    else:
        # Assume moderate temperature if not provided
        t_scalar = 0.7

    # Calculate GPP (g C/m²/day)
    # LUE in g C / MJ PAR
    gpp_g_m2 = PASTURE_LUE.lue_max * fpar * par * t_scalar

    # Convert to kg C/ha/day
    gpp_kg_ha = gpp_g_m2 * 10  # 1 g/m² = 10 kg/ha

    notes = f"fPAR={fpar:.2f}, PAR={par:.1f} MJ/m²/day, T_scalar={t_scalar:.2f}, LUE={PASTURE_LUE.lue_max} g C/MJ"

    return round(gpp_kg_ha, 1), notes


def calculate_carbon_flux(
    ndvi: float,
    standing_dry_matter: float,  # kg DM/ha
    latitude: float = 48.5,
    day_of_year: int | None = None,
    temperature: float | None = None,
    soil_respiration_factor: float = 0.6,
) -> CarbonFluxResult:
    """
    Calculate full carbon flux for a paddock.

    Args:
        ndvi: NDVI value
        standing_dry_matter: Standing dry matter in kg DM/ha
        latitude: Latitude for PAR estimation
        day_of_year: Day of year (1-365)
        temperature: Air temperature in °C
        soil_respiration_factor: Ratio of soil respiration to NPP (default 0.6)

    Returns:
        CarbonFluxResult with all carbon metrics
    """
    # Calculate GPP
    gpp, gpp_notes = calculate_gpp(ndvi, latitude, day_of_year, temperature)

    # NPP is approximately 50% of GPP for grasslands
    # (other 50% is plant respiration)
    npp_ratio = 0.50
    npp = gpp * npp_ratio

    # Ecosystem respiration = plant respiration + soil respiration
    # Plant respiration = GPP - NPP = 0.5 * GPP
    plant_respiration = gpp - npp
    # Soil respiration estimated as fraction of NPP
    soil_respiration = npp * soil_respiration_factor
    total_respiration = plant_respiration + soil_respiration

    # NEE = Respiration - GPP (negative = net uptake/sequestration)
    nee = total_respiration - gpp

    # Above-ground carbon stock
    carbon_stock = standing_dry_matter * CARBON_FRACTION

    notes = (
        f"GPP model: {gpp_notes}. "
        f"NPP ratio: {npp_ratio}, Soil resp factor: {soil_respiration_factor}. "
        f"Based on MOD17/CASA approach [1,2]."
    )

    return CarbonFluxResult(
        gpp=round(gpp, 1),
        npp=round(npp, 1),
        respiration=round(total_respiration, 1),
        nee=round(nee, 1),
        carbon_stock=round(carbon_stock, 0),
        notes=notes,
    )


def estimate_annual_sequestration(
    monthly_ndvi: list[float],
    latitude: float = 48.5,
    pasture_type: PastureType = PastureType.MODERATE,
) -> dict:
    """
    Estimate annual carbon sequestration from monthly NDVI values.

    Args:
        monthly_ndvi: List of 12 monthly NDVI values (Jan-Dec)
        latitude: Latitude for PAR estimation
        pasture_type: Management type affecting sequestration

    Returns:
        Dict with annual carbon metrics
    """
    if len(monthly_ndvi) != 12:
        raise ValueError("Need exactly 12 monthly NDVI values")

    # Management factor for soil carbon sequestration
    management_factors = {
        PastureType.INTENSIVE: 0.3,  # Lower due to soil disturbance
        PastureType.MODERATE: 0.5,  # Rotational grazing benefits
        PastureType.EXTENSIVE: 0.4,  # Low input, moderate benefit
        PastureType.SILVOPASTURE: 0.7,  # Trees add significant C storage
    }

    annual_gpp = 0.0
    annual_npp = 0.0

    for month, ndvi in enumerate(monthly_ndvi, 1):
        if ndvi is None or ndvi < 0:
            continue

        # Mid-month day of year
        day_of_year = (month - 1) * 30 + 15

        # Days in month (approximate)
        days_in_month = 30

        gpp, _ = calculate_gpp(ndvi, latitude, day_of_year)
        npp = gpp * 0.5

        annual_gpp += gpp * days_in_month
        annual_npp += npp * days_in_month

    # Soil carbon sequestration potential
    # Based on literature: well-managed pastures can sequester 0.3-0.8 t C/ha/year
    # This is the portion of NPP that gets incorporated into stable soil organic matter
    management_factor = management_factors[pasture_type]
    soil_sequestration = annual_npp * 0.1 * management_factor  # ~5-10% of NPP

    return {
        "annual_gpp_kg_c_ha": round(annual_gpp, 0),
        "annual_npp_kg_c_ha": round(annual_npp, 0),
        "annual_gpp_t_c_ha": round(annual_gpp / 1000, 2),
        "annual_npp_t_c_ha": round(annual_npp / 1000, 2),
        "soil_sequestration_t_c_ha": round(soil_sequestration / 1000, 2),
        "soil_sequestration_t_co2_ha": round(soil_sequestration / 1000 * CO2_PER_C, 2),
        "pasture_type": pasture_type.value,
        "notes": (
            f"GPP/NPP from NDVI using MOD17 approach. "
            f"Soil sequestration assumes {management_factor * 100:.0f}% management efficiency "
            f"for {pasture_type.value} grazing. Literature range: 0.3-0.8 t C/ha/year [4]."
        ),
    }


@dataclass
class SheepEmissionFactors:
    """
    IPCC Tier 2 emission factors for sheep by category.

    Based on IPCC 2019 Refinement, Vol 4, Ch 10, Table 10.10
    Values for "Developed countries" with temperate climate.

    Enteric fermentation is primary source (~97% of sheep CH4).
    Manure adds small amount (~3%).
    """

    # Enteric fermentation (kg CH4/head/year)
    lamb: float = 4.0  # < 1 year, smaller rumen
    ewe: float = 8.0  # Adult female
    ram: float = 8.0  # Adult male (similar to ewe)
    wether: float = 8.0  # Castrated male

    # Lactating ewes produce more due to higher feed intake
    ewe_lactating: float = 10.0

    # Manure management (kg CH4/head/year) - small addition
    manure_factor: float = 0.28  # Pasture systems, cool climate

    source: str = "IPCC 2019 Refinement, Vol 4, Ch 10"


SHEEP_EF = SheepEmissionFactors()


def estimate_sheep_methane(
    ewes: int = 0,
    rams: int = 0,
    lambs: int = 0,
    wethers: int = 0,
    ewes_lactating: int = 0,
    # Adjustment factors
    forage_type: str = "pasture_fresh",
    additives: list[str] | None = None,
    breed: str = "unknown",
    climate: str = "temperate",
) -> dict:
    """
    Estimate methane emissions from sheep flock with adjustments.

    Uses IPCC Tier 2/3 approach with age/class-specific factors
    and adjustments for diet, breed, and climate.

    Args:
        ewes: Number of adult ewes (non-lactating)
        rams: Number of adult rams
        lambs: Number of lambs (< 1 year)
        wethers: Number of wethers (castrated males)
        ewes_lactating: Number of lactating ewes

        forage_type: Base forage type (pasture_fresh, hay, haylage, silage, etc.)
        additives: List of feed additives (kelp, asparagopsis, tannins, etc.)
        breed: Sheep breed for adjustment (north_country_cheviot, suffolk, etc.)
        climate: Climate zone (cold, cool, temperate, warm, hot)

    Returns:
        Dict with detailed methane emissions including adjustments

    References:
        IPCC 2019 Refinement to 2006 Guidelines
        Volume 4, Chapter 10, Table 10.10
    """
    # Calculate base enteric emissions by class
    enteric_base = {
        "ewes": ewes * SHEEP_EF.ewe,
        "ewes_lactating": ewes_lactating * SHEEP_EF.ewe_lactating,
        "rams": rams * SHEEP_EF.ram,
        "lambs": lambs * SHEEP_EF.lamb,
        "wethers": wethers * SHEEP_EF.wether,
    }

    total_heads = ewes + ewes_lactating + rams + lambs + wethers
    enteric_base_total = sum(enteric_base.values())

    # Calculate adjustment factors
    # 1. Forage type adjustment
    forage_factor = DIET_ADJUSTMENTS.forage_factors.get(forage_type, 1.0)

    # 2. Additive adjustments (multiplicative - can stack)
    additive_factor = 1.0
    applied_additives = []
    if additives:
        for additive in additives:
            add_f = DIET_ADJUSTMENTS.additive_factors.get(additive.lower(), 1.0)
            additive_factor *= add_f
            if add_f != 1.0:
                applied_additives.append(f"{additive}={add_f:.2f}")

    # 3. Breed adjustment
    breed_key = breed.lower().replace(" ", "_").replace("-", "_")
    breed_factor = BREED_ADJUSTMENTS.breed_factors.get(breed_key, 1.0)

    # 4. Climate adjustment
    climate_factor = CLIMATE_ADJUSTMENTS.climate_factors.get(climate.lower(), 1.0)

    # Combined adjustment factor
    total_adjustment = forage_factor * additive_factor * breed_factor * climate_factor

    # Apply adjustments to enteric emissions
    enteric_adjusted = {k: v * total_adjustment for k, v in enteric_base.items()}
    enteric_adjusted_total = sum(enteric_adjusted.values())

    # Manure emissions (small for pasture-based systems) - also adjusted
    manure_total = total_heads * SHEEP_EF.manure_factor * total_adjustment

    total_ch4 = enteric_adjusted_total + manure_total
    co2_eq = total_ch4 * CH4_GWP

    # Calculate comparison to baseline (no adjustments)
    baseline_ch4 = enteric_base_total + (total_heads * SHEEP_EF.manure_factor)
    reduction_pct = (1 - total_ch4 / baseline_ch4) * 100 if baseline_ch4 > 0 else 0

    # Calculate weighted average per head
    avg_per_head = total_ch4 / total_heads if total_heads > 0 else 0

    return {
        "flock_composition": {
            "ewes": ewes,
            "ewes_lactating": ewes_lactating,
            "rams": rams,
            "lambs": lambs,
            "wethers": wethers,
            "total": total_heads,
        },
        "enteric_by_class_base": {k: round(v, 1) for k, v in enteric_base.items()},
        "enteric_by_class_adjusted": {k: round(v, 1) for k, v in enteric_adjusted.items()},
        "adjustments": {
            "forage_type": forage_type,
            "forage_factor": forage_factor,
            "additives": additives or [],
            "additive_factor": round(additive_factor, 3),
            "breed": breed,
            "breed_factor": breed_factor,
            "climate": climate,
            "climate_factor": climate_factor,
            "combined_factor": round(total_adjustment, 3),
        },
        "baseline_ch4_kg": round(baseline_ch4, 1),
        "enteric_ch4_kg": round(enteric_adjusted_total, 1),
        "manure_ch4_kg": round(manure_total, 1),
        "total_ch4_kg": round(total_ch4, 1),
        "reduction_from_baseline_pct": round(reduction_pct, 1),
        "co2eq_kg": round(co2_eq, 0),
        "co2eq_t": round(co2_eq / 1000, 2),
        "avg_ch4_per_head": round(avg_per_head, 1),
        "emission_factors": {
            "ewe": SHEEP_EF.ewe,
            "ewe_lactating": SHEEP_EF.ewe_lactating,
            "ram": SHEEP_EF.ram,
            "lamb": SHEEP_EF.lamb,
            "wether": SHEEP_EF.wether,
            "manure": SHEEP_EF.manure_factor,
        },
        "notes": (
            f"IPCC Tier 2 factors for temperate pasture sheep. "
            f"Lambs ({SHEEP_EF.lamb} kg/yr) emit ~50% less than adults ({SHEEP_EF.ewe} kg/yr). "
            f"Lactating ewes ({SHEEP_EF.ewe_lactating} kg/yr) emit ~25% more due to feed intake. "
            f"Source: {SHEEP_EF.source}. CH4 GWP={CH4_GWP}."
        ),
    }


def estimate_livestock_methane(
    cattle_count: int = 0,
    cattle_type: str = "beef",
    sheep: dict | int = 0,
) -> dict:
    """
    Estimate methane emissions from mixed livestock.

    Based on IPCC emission factors.

    Args:
        cattle_count: Number of cattle
        cattle_type: "beef" or "dairy"
        sheep: Either total count (int) or dict with categories:
               {"ewes": 10, "rams": 2, "lambs": 15, "wethers": 0, "ewes_lactating": 5}

    Returns:
        Dict with methane emissions
    """
    # IPCC Tier 1 emission factors for cattle (kg CH4/head/year)
    cattle_factors = {
        "beef": 70,  # Range: 47-99 depending on region/feed
        "dairy": 128,  # Higher due to feed intake
    }

    cattle_ef = cattle_factors.get(cattle_type, cattle_factors["beef"])
    cattle_ch4 = cattle_count * cattle_ef

    # Handle sheep - either simple count or detailed breakdown
    if isinstance(sheep, dict):
        sheep_result = estimate_sheep_methane(**sheep)
        sheep_ch4 = sheep_result["total_ch4_kg"]
        sheep_count = sheep_result["flock_composition"]["total"]
        sheep_details = sheep_result
    elif isinstance(sheep, int) and sheep > 0:
        # Simple count - assume mixed flock with average emission
        # Approximate breakdown: 60% ewes, 5% rams, 30% lambs, 5% wethers
        sheep_result = estimate_sheep_methane(
            ewes=int(sheep * 0.6),
            rams=int(sheep * 0.05),
            lambs=int(sheep * 0.30),
            wethers=int(sheep * 0.05),
        )
        sheep_ch4 = sheep_result["total_ch4_kg"]
        sheep_count = sheep
        sheep_details = sheep_result
    else:
        sheep_ch4 = 0
        sheep_count = 0
        sheep_details = None

    total_ch4 = cattle_ch4 + sheep_ch4
    co2_eq = total_ch4 * CH4_GWP

    result = {
        "cattle": {
            "count": cattle_count,
            "type": cattle_type,
            "emission_factor": cattle_ef,
            "ch4_kg": round(cattle_ch4, 1),
        },
        "sheep": {
            "count": sheep_count,
            "ch4_kg": round(sheep_ch4, 1),
            "details": sheep_details,
        },
        "total_ch4_kg": round(total_ch4, 1),
        "total_co2eq_kg": round(co2_eq, 0),
        "total_co2eq_t": round(co2_eq / 1000, 2),
        "notes": (
            f"Cattle: IPCC Tier 1 ({cattle_type}={cattle_ef} kg CH4/head/yr). "
            f"Sheep: IPCC Tier 2 by age class. CH4 GWP={CH4_GWP} (AR5)."
        ),
    }

    return result


# Convenience constants for reporting
CARBON_UNITS = {
    "kg_c_to_kg_co2": CO2_PER_C,
    "t_c_to_t_co2": CO2_PER_C,
    "description": "Multiply carbon by 3.67 to get CO2 equivalent",
}
