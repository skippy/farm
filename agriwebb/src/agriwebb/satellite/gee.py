"""
Satellite imagery pipeline using Harmonized Landsat Sentinel-2 (HLS) via Google Earth Engine.

Provides cloud-free NDVI composites for paddock-level pasture monitoring.
"""

from datetime import date, timedelta
from typing import TypedDict

import ee


class PaddockNDVI(TypedDict):
    """NDVI result for a single paddock."""

    paddock_id: str
    paddock_name: str
    date_start: str
    date_end: str
    ndvi_mean: float | None
    ndvi_min: float | None
    ndvi_max: float | None
    ndvi_stddev: float | None
    pixel_count: int
    cloud_free_pct: float
    tree_cover_pct: float | None  # Percentage of paddock masked as trees


# HLS collections in GEE
HLS_L30 = "NASA/HLS/HLSL30/v002"  # Landsat 8/9 harmonized
HLS_S30 = "NASA/HLS/HLSS30/v002"  # Sentinel-2 harmonized
# NOTE: HLS harmonization drops the native Sentinel-2 red-edge bands
# (original S2 B5/B6/B7). For red-edge work (NDRE, LAI), use the native
# S2 SR collection below.

# Sentinel-2 Surface Reflectance (L2A) — native band set including red-edge
S2_SR_HARMONIZED = "COPERNICUS/S2_SR_HARMONIZED"

# NLCD for tree masking (2021 release covers CONUS)
# Land cover classes 41, 42, 43 are forest types
NLCD_LANDCOVER = "USGS/NLCD_RELEASES/2021_REL/NLCD"


def initialize(project: str | None = None) -> None:
    """
    Initialize Earth Engine using service account.

    Looks for credentials in order:
    1. GEE_SERVICE_ACCOUNT_KEY env var (JSON string, for CI)
    2. service-account.json file in project root (for local dev)

    Project ID is determined by:
    1. Explicit project parameter
    2. project_id from service account JSON

    Args:
        project: GEE project ID (optional if service account has project_id).
    """
    import json
    import os
    from pathlib import Path

    # Option 1: Environment variable (CI/CD)
    key_json = os.environ.get("GEE_SERVICE_ACCOUNT_KEY")

    # Option 2: Local file (agriwebb/service-account.json)
    if not key_json:
        # __file__ is src/agriwebb/satellite/gee.py
        # .parent.parent.parent.parent = agriwebb/
        key_file = Path(__file__).parent.parent.parent.parent / "service-account.json"
        if key_file.exists():
            key_json = key_file.read_text()

    if not key_json:
        raise RuntimeError(
            "No GEE credentials found. Either:\n"
            "  1. Set GEE_SERVICE_ACCOUNT_KEY env var, or\n"
            "  2. Place service-account.json in agriwebb/ directory"
        )

    key_data = json.loads(key_json)

    # Use project from param, or fall back to project_id in service account
    effective_project = project
    if not effective_project or effective_project == "your-gcp-project-id":
        effective_project = key_data.get("project_id")

    if not effective_project:
        raise RuntimeError(
            "No GEE project ID found. Either:\n"
            "  1. Set GEE_PROJECT_ID in .env, or\n"
            "  2. Ensure service-account.json contains project_id"
        )

    credentials = ee.ServiceAccountCredentials(
        email=key_data["client_email"],
        key_data=key_json,
    )
    ee.Initialize(credentials=credentials, project=effective_project)


def _agriwebb_to_ee_geometry(geometry: dict) -> ee.Geometry:
    """Convert AgriWebb GeoJSON geometry to Earth Engine geometry."""
    geom_type = geometry.get("type", "Polygon")
    coords = geometry.get("coordinates", [])

    if geom_type == "Polygon":
        return ee.Geometry.Polygon(coords)
    elif geom_type == "MultiPolygon":
        return ee.Geometry.MultiPolygon(coords)
    else:
        raise ValueError(f"Unsupported geometry type: {geom_type}")


def _mask_clouds_hls(image: ee.Image) -> ee.Image:
    """
    Apply cloud mask to HLS image using Fmask band.

    HLS v2.0 Fmask is a bitmask:
        Bit 0: Cirrus (reserved, not used)
        Bit 1: Cloud
        Bit 2: Adjacent to cloud/shadow
        Bit 3: Cloud shadow
        Bit 4: Snow/ice
        Bit 5: Water
        Bits 6-7: Aerosol level (00=climatology, 01=low, 10=moderate, 11=high)

    We mask out: cloud (bit 1), cloud shadow (bit 3)
    """
    fmask = image.select("Fmask")

    # Create bitmask for cloud (bit 1) and cloud shadow (bit 3)
    cloud_bit = 1 << 1  # 2
    shadow_bit = 1 << 3  # 8

    # Mask where neither cloud nor shadow bits are set
    clear_mask = fmask.bitwiseAnd(cloud_bit).eq(0).And(fmask.bitwiseAnd(shadow_bit).eq(0))

    return image.updateMask(clear_mask)


def _compute_ndvi(image: ee.Image) -> ee.Image:
    """
    Compute NDVI from HLS image.

    HLS bands:
        B4 = Red (both L30 and S30)
        B5 = NIR for S30 (Sentinel-2)
        B5 = NIR for L30 (Landsat)

    Note: HLS harmonizes band names, so B5 is NIR for both.
    """
    nir = image.select("B5")
    red = image.select("B4")

    ndvi = nir.subtract(red).divide(nir.add(red)).rename("NDVI")

    return image.addBands(ndvi)


def _compute_evi(image: ee.Image) -> ee.Image:
    """
    Compute EVI (Enhanced Vegetation Index) from HLS image.

    EVI = G * (NIR - Red) / (NIR + C1*Red - C2*Blue + L)

    Standard MODIS coefficients: G=2.5, C1=6, C2=7.5, L=1.

    EVI is preferred over NDVI for biomass work because:
    - Saturates at much higher biomass (NDVI saturates at LAI ~3, EVI at ~5)
    - Less sensitive to atmospheric/aerosol effects (the blue-band correction)
    - More responsive in dense canopy, which is where NDVI loses sensitivity

    HLS bands:
        B2 = Blue
        B4 = Red
        B5 = NIR (harmonized)
    """
    nir = image.select("B5")
    red = image.select("B4")
    blue = image.select("B2")

    evi = image.expression(
        "2.5 * ((NIR - RED) / (NIR + 6 * RED - 7.5 * BLUE + 1))",
        {"NIR": nir, "RED": red, "BLUE": blue},
    ).rename("EVI")

    return image.addBands(evi)


def _compute_indices(image: ee.Image) -> ee.Image:
    """Compute both NDVI and EVI on an image.

    Used by the ``get_ndvi_composite``/``extract_paddock_ndvi`` pipeline so
    either index can be selected downstream without re-processing the
    collection.
    """
    return _compute_evi(_compute_ndvi(image))


# ---------------------------------------------------------------------------
# Sentinel-2 native (with red-edge bands) — for NDRE / LAI work
# ---------------------------------------------------------------------------


def _mask_clouds_s2(image: ee.Image) -> ee.Image:
    """Cloud-mask a Sentinel-2 SR image using the SCL scene classification.

    SCL values (Sen2Cor L2A):
        0  NO_DATA
        1  SATURATED_OR_DEFECTIVE
        2  DARK_AREA_PIXELS
        3  CLOUD_SHADOWS
        4  VEGETATION
        5  NOT_VEGETATED
        6  WATER
        7  UNCLASSIFIED
        8  CLOUD_MEDIUM_PROBABILITY
        9  CLOUD_HIGH_PROBABILITY
        10 THIN_CIRRUS
        11 SNOW

    Keep classes 4, 5, 6, 7 (valid land + water + unclassified); drop clouds,
    shadows, saturation, snow, cirrus.
    """
    scl = image.select("SCL")
    keep = (
        scl.eq(4)  # vegetation
        .Or(scl.eq(5))  # not-vegetated
        .Or(scl.eq(6))  # water
        .Or(scl.eq(7))  # unclassified
    )
    return image.updateMask(keep)


def _compute_ndre_s2(image: ee.Image) -> ee.Image:
    """Compute NDRE (Normalized Difference Red Edge) from Sentinel-2 L2A.

    NDRE = (B8A - B5) / (B8A + B5)

    Uses the native S2 red-edge 1 band (B5, ~705nm) and narrow NIR (B8A,
    ~865nm) — both 20m resolution, matching scales.

    NDRE saturates at much higher biomass than NDVI (~LAI 5-6 vs ~LAI 3)
    because the red-edge region is more sensitive to chlorophyll in dense
    canopies. It's the preferred index for cool-season pasture where
    biomass commonly exceeds NDVI's saturation point in spring flush.

    Reference: Gitelson et al. 2003; Fitzgerald et al. 2010; Frampton et
    al. 2013 (S2 red-edge indices).
    """
    b5 = image.select("B5")
    b8a = image.select("B8A")
    ndre = b8a.subtract(b5).divide(b8a.add(b5)).rename("NDRE")
    return image.addBands(ndre)


def _get_s2_sr_collection(
    geometry: ee.Geometry,
    start_date: str,
    end_date: str,
) -> ee.ImageCollection:
    """Get cloud-masked Sentinel-2 SR collection with NDRE computed.

    Reflectance is divided by 10000 to get physical [0, 1] values.
    """
    raw = (
        ee.ImageCollection(S2_SR_HARMONIZED)
        .filterBounds(geometry)
        .filterDate(start_date, end_date)
        .map(_mask_clouds_s2)
    )

    def _scale(image: ee.Image) -> ee.Image:
        # Scale reflectance bands from int16 [0, 10000] to float [0, 1]
        refl = image.select(["B2", "B3", "B4", "B5", "B6", "B7", "B8", "B8A", "B11", "B12"]).divide(10000)
        return image.addBands(refl, overwrite=True)

    return raw.map(_scale).map(_compute_ndre_s2)


def get_s2_ndre_composite(
    geometry: ee.Geometry,
    start_date: str,
    end_date: str,
    reducer: str = "median",
    mask_trees: bool = True,
) -> ee.Image:
    """Create a cloud-free NDRE composite for a date range using S2 L2A."""
    collection = _get_s2_sr_collection(geometry, start_date, end_date)
    ndre_collection = collection.select("NDRE")

    if reducer == "median":
        composite = ndre_collection.median()
    elif reducer == "mean":
        composite = ndre_collection.mean()
    elif reducer == "max":
        composite = ndre_collection.max()
    else:
        raise ValueError(f"Unknown reducer: {reducer}")

    if mask_trees:
        composite = composite.updateMask(_get_tree_mask(geometry))

    return composite


def extract_paddock_ndre(
    paddock: dict,
    start_date: str,
    end_date: str,
    scale: int = 20,
    mask_trees: bool = True,
) -> PaddockNDVI:
    """Extract NDRE statistics for a paddock using native Sentinel-2 L2A.

    Returns a ``PaddockNDVI``-shaped dict with NDRE in the ``ndvi_*`` fields
    (the field names are kept generic since the validation gate and SDM
    conversion treat the value as an opaque vegetation index).

    Uses 20m resolution by default because NDRE's inputs (B5, B8A) are
    native 20m bands.
    """
    geometry = _agriwebb_to_ee_geometry(paddock["geometry"])

    tree_cover_pct = _calculate_tree_cover_pct(geometry) if mask_trees else None

    composite = get_s2_ndre_composite(geometry, start_date, end_date, mask_trees=mask_trees)

    stats = composite.reduceRegion(
        reducer=ee.Reducer.mean()
        .combine(ee.Reducer.minMax(), sharedInputs=True)
        .combine(ee.Reducer.stdDev(), sharedInputs=True)
        .combine(ee.Reducer.count(), sharedInputs=True),
        geometry=geometry,
        scale=scale,
        maxPixels=int(1e8),
    )

    stats_dict = stats.getInfo() or {}

    area_ha = paddock.get("totalArea", 0)
    expected_pixels = (area_ha * 10000) / (scale * scale)
    actual_pixels = stats_dict.get("NDRE_count", 0) or 0
    cloud_free_pct = (actual_pixels / expected_pixels * 100) if expected_pixels > 0 else 0

    return PaddockNDVI(
        paddock_id=paddock["id"],
        paddock_name=paddock.get("name", "Unknown"),
        date_start=start_date,
        date_end=end_date,
        ndvi_mean=stats_dict.get("NDRE_mean"),
        ndvi_min=stats_dict.get("NDRE_min"),
        ndvi_max=stats_dict.get("NDRE_max"),
        ndvi_stddev=stats_dict.get("NDRE_stdDev"),
        pixel_count=actual_pixels,
        cloud_free_pct=round(cloud_free_pct, 1),
        tree_cover_pct=tree_cover_pct,
    )


def _get_tree_mask(geometry: ee.Geometry) -> ee.Image:
    """
    Get a binary mask where trees are masked out (0 = tree, 1 = non-tree).

    Uses NLCD 2021 land cover classification:
        41 = Deciduous Forest
        42 = Evergreen Forest
        43 = Mixed Forest

    Args:
        geometry: Earth Engine geometry to get mask for

    Returns:
        Binary image: 1 = pasture/non-tree, 0 = tree (masked)
    """
    # Get the most recent NLCD image (2021)
    nlcd = ee.ImageCollection(NLCD_LANDCOVER).sort("system:time_start", False).first()

    # Select land cover band
    landcover = nlcd.select("landcover")

    # Create mask: 1 where NOT forest (41, 42, 43), 0 where forest
    # Forest classes: 41=Deciduous, 42=Evergreen, 43=Mixed
    is_forest = landcover.eq(41).Or(landcover.eq(42)).Or(landcover.eq(43))
    non_tree_mask = is_forest.Not()

    return non_tree_mask


def _calculate_tree_cover_pct(geometry: ee.Geometry, scale: int = 30) -> float | None:
    """
    Calculate the percentage of a geometry covered by trees.

    Args:
        geometry: Earth Engine geometry
        scale: Resolution in meters (30m matches NLCD native resolution)

    Returns:
        Percentage of area covered by trees (0-100), or None if calculation fails
    """
    tree_mask = _get_tree_mask(geometry)

    # tree_mask is 1 for non-tree, 0 for tree
    # So we need to invert: (1 - tree_mask) gives us tree pixels
    tree_pixels = tree_mask.Not()

    # Calculate mean of tree pixels (gives fraction that is trees)
    stats = tree_pixels.reduceRegion(
        reducer=ee.Reducer.mean(),
        geometry=geometry,
        scale=scale,
        maxPixels=int(1e8),
    )

    try:
        result = stats.getInfo()
        if result:
            tree_fraction = result.get("landcover")
            if tree_fraction is not None:
                return round(tree_fraction * 100, 1)
    except Exception:
        pass

    return None


def _get_hls_collection(
    geometry: ee.Geometry,
    start_date: str,
    end_date: str,
) -> ee.ImageCollection:
    """
    Get merged HLS collection (Landsat + Sentinel-2) for a geometry and date range.

    Args:
        geometry: Earth Engine geometry to filter by
        start_date: Start date (YYYY-MM-DD)
        end_date: End date (YYYY-MM-DD)

    Returns:
        Merged, cloud-masked, NDVI-computed image collection
    """
    # Load both HLS collections
    hls_l30 = ee.ImageCollection(HLS_L30).filterBounds(geometry).filterDate(start_date, end_date)

    hls_s30 = ee.ImageCollection(HLS_S30).filterBounds(geometry).filterDate(start_date, end_date)

    # Merge collections
    merged = hls_l30.merge(hls_s30)

    # Apply cloud mask and compute both NDVI + EVI
    processed = merged.map(_mask_clouds_hls).map(_compute_indices)

    return processed


def get_ndvi_composite(
    geometry: ee.Geometry,
    start_date: str,
    end_date: str,
    reducer: str = "median",
    mask_trees: bool = True,
    index: str = "NDVI",
) -> ee.Image:
    """
    Create a cloud-free vegetation-index composite for a date range.

    Args:
        geometry: Earth Engine geometry
        start_date: Start date (YYYY-MM-DD)
        end_date: End date (YYYY-MM-DD)
        reducer: Aggregation method ('median', 'mean', 'max')
        mask_trees: If True, mask out tree-covered pixels using NLCD
        index: Vegetation index to composite — "NDVI" (default) or "EVI".
            EVI is less susceptible to saturation at high biomass.

    Returns:
        Single composite image with the selected index band.
    """
    if index not in ("NDVI", "EVI"):
        raise ValueError(f"Unknown vegetation index: {index!r} (use 'NDVI' or 'EVI')")

    collection = _get_hls_collection(geometry, start_date, end_date)
    index_collection = collection.select(index)

    if reducer == "median":
        composite = index_collection.median()
    elif reducer == "mean":
        composite = index_collection.mean()
    elif reducer == "max":
        composite = index_collection.max()
    else:
        raise ValueError(f"Unknown reducer: {reducer}")

    # Apply tree mask if requested
    if mask_trees:
        tree_mask = _get_tree_mask(geometry)
        composite = composite.updateMask(tree_mask)

    return composite


def extract_paddock_ndvi(
    paddock: dict,
    start_date: str,
    end_date: str,
    scale: int = 10,
    mask_trees: bool = True,
    index: str = "NDVI",
) -> PaddockNDVI:
    """
    Extract vegetation-index statistics for a single paddock.

    Args:
        paddock: AgriWebb paddock dict with 'id', 'name', 'geometry'
        start_date: Start date (YYYY-MM-DD)
        end_date: End date (YYYY-MM-DD)
        scale: Resolution in meters (default 10m for Sentinel-2 native)
        mask_trees: If True, mask out tree-covered pixels using NLCD
        index: Vegetation index — "NDVI" (default) or "EVI". The result's
            ``ndvi_*`` fields hold whichever index was requested; the
            function name is kept for backward compatibility.

    Returns:
        PaddockNDVI with statistics for the selected index.
    """
    if index not in ("NDVI", "EVI"):
        raise ValueError(f"Unknown vegetation index: {index!r} (use 'NDVI' or 'EVI')")

    geometry = _agriwebb_to_ee_geometry(paddock["geometry"])

    # Calculate tree cover percentage before masking
    tree_cover_pct = _calculate_tree_cover_pct(geometry) if mask_trees else None

    # Get composite (with tree masking if enabled)
    composite = get_ndvi_composite(geometry, start_date, end_date, mask_trees=mask_trees, index=index)

    # Calculate statistics over the paddock
    stats = composite.reduceRegion(
        reducer=ee.Reducer.mean()
        .combine(ee.Reducer.minMax(), sharedInputs=True)
        .combine(ee.Reducer.stdDev(), sharedInputs=True)
        .combine(ee.Reducer.count(), sharedInputs=True),
        geometry=geometry,
        scale=scale,
        maxPixels=int(1e8),
    )

    # Get values (returns None if no valid pixels)
    stats_dict = stats.getInfo() or {}

    # Calculate approximate cloud-free percentage
    # (ratio of valid pixels to expected pixels based on area)
    area_ha = paddock.get("totalArea", 0)
    expected_pixels = (area_ha * 10000) / (scale * scale)  # area in m² / pixel area
    actual_pixels = stats_dict.get(f"{index}_count", 0) or 0
    cloud_free_pct = (actual_pixels / expected_pixels * 100) if expected_pixels > 0 else 0

    return PaddockNDVI(
        paddock_id=paddock["id"],
        paddock_name=paddock.get("name", "Unknown"),
        date_start=start_date,
        date_end=end_date,
        ndvi_mean=stats_dict.get(f"{index}_mean"),
        ndvi_min=stats_dict.get(f"{index}_min"),
        ndvi_max=stats_dict.get(f"{index}_max"),
        ndvi_stddev=stats_dict.get(f"{index}_stdDev"),
        pixel_count=actual_pixels,
        cloud_free_pct=round(cloud_free_pct, 1),
        tree_cover_pct=tree_cover_pct,
    )


def extract_all_paddocks_ndvi(
    paddocks: list[dict],
    start_date: str,
    end_date: str,
    scale: int = 10,
    mask_trees: bool = True,
) -> list[PaddockNDVI]:
    """
    Extract NDVI for all paddocks.

    Args:
        paddocks: List of AgriWebb paddock dicts
        start_date: Start date (YYYY-MM-DD)
        end_date: End date (YYYY-MM-DD)
        scale: Resolution in meters
        mask_trees: If True, mask out tree-covered pixels using NLCD

    Returns:
        List of PaddockNDVI results
    """
    results = []
    for paddock in paddocks:
        if not paddock.get("geometry"):
            continue
        try:
            result = extract_paddock_ndvi(paddock, start_date, end_date, scale, mask_trees)
            results.append(result)
        except Exception as e:
            print(f"Error processing {paddock.get('name', 'Unknown')}: {e}")

    return results


def get_weekly_composites(
    paddocks: list[dict],
    weeks_back: int = 4,
    scale: int = 10,
    mask_trees: bool = True,
) -> dict[str, list[PaddockNDVI]]:
    """
    Get weekly NDVI composites for recent weeks.

    Args:
        paddocks: List of AgriWebb paddock dicts
        weeks_back: Number of weeks to look back
        scale: Resolution in meters
        mask_trees: If True, mask out tree-covered pixels using NLCD

    Returns:
        Dict mapping week start date to list of paddock results
    """
    results = {}
    today = date.today()

    for week_offset in range(weeks_back):
        # Calculate week boundaries (Sunday to Saturday)
        week_end = today - timedelta(days=today.weekday() + 1 + (week_offset * 7))
        week_start = week_end - timedelta(days=6)

        start_str = week_start.isoformat()
        end_str = week_end.isoformat()

        print(f"Processing week: {start_str} to {end_str}")
        week_results = extract_all_paddocks_ndvi(paddocks, start_str, end_str, scale, mask_trees)
        results[start_str] = week_results

    return results
