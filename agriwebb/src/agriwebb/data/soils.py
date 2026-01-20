"""
Fetch soil data from USDA Web Soil Survey.

Uses paddock boundaries from AgriWebb and queries USDA's Soil Data Access
service to get soil map unit data for each paddock's location.
"""

import asyncio
import json
import re
from collections.abc import Callable
from datetime import UTC, datetime

import httpx

from agriwebb.core import get_cache_dir, get_fields

USDA_SOIL_URL = "https://SDMDataAccess.sc.egov.usda.gov/TABULAR/post.rest"


def calculate_centroid(geometry: dict) -> tuple[float, float] | None:
    """Calculate centroid of a polygon geometry."""
    geom_type = geometry.get("type")
    coords = geometry.get("coordinates", [])

    if not coords:
        return None

    points = []

    if geom_type == "Polygon":
        # First ring is the exterior
        if coords and coords[0]:
            points = coords[0]
    elif geom_type == "MultiPolygon":
        # Flatten all exterior rings
        for polygon in coords:
            if polygon and polygon[0]:
                points.extend(polygon[0])

    if not points:
        return None

    # Calculate centroid (simple average)
    lon_sum = sum(p[0] for p in points)
    lat_sum = sum(p[1] for p in points)
    n = len(points)

    return (lat_sum / n, lon_sum / n)


async def get_mukey_at_point(lat: float, lon: float) -> str | None:
    """Get the map unit key (mukey) at a point using USDA's geometry service."""
    url = "https://SDMDataAccess.sc.egov.usda.gov/Spatial/SDMNAD83Geographic.wfs"
    params = {
        "Service": "WFS",
        "Version": "1.1.0",
        "Request": "GetFeature",
        "TypeName": "MapunitPoly",
        "Filter": f"""<Filter xmlns="http://www.opengis.net/ogc" xmlns:gml="http://www.opengis.net/gml">
            <Contains>
                <PropertyName>Geometry</PropertyName>
                <gml:Point srsName="EPSG:4326">
                    <gml:pos>{lat} {lon}</gml:pos>
                </gml:Point>
            </Contains>
        </Filter>""",
        "outputFormat": "application/json",
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=params, timeout=30)
            if response.status_code == 200:
                data = response.json()
                features = data.get("features", [])
                if features:
                    return features[0].get("properties", {}).get("mukey")
    except Exception:
        pass
    return None


async def query_soil_by_mukey(
    mukey: str,
    on_progress: Callable[[str], None] | None = None,
) -> dict | None:
    """Query soil properties for a given map unit key.

    Args:
        mukey: USDA map unit key
        on_progress: Optional callback for progress/error messages

    Returns:
        Soil data dict or None if not found
    """
    columns = [
        "mukey",
        "muname",
        "mukind",
        "compname",
        "comppct",
        "taxorder",
        "drainage",
        "hydgrp",
        "sand_pct",
        "silt_pct",
        "clay_pct",
        "organic_matter_pct",
        "ksat",
        "awc",
    ]

    query = f"""
    SELECT
        mu.mukey,
        mu.muname,
        mu.mukind,
        c.compname,
        c.comppct_r,
        c.taxorder,
        c.drainagecl,
        c.hydgrp,
        ch.sandtotal_r,
        ch.silttotal_r,
        ch.claytotal_r,
        ch.om_r,
        ch.ksat_r,
        ch.awc_r
    FROM mapunit mu
    INNER JOIN component c ON c.mukey = mu.mukey
    LEFT JOIN chorizon ch ON ch.cokey = c.cokey AND ch.hzdept_r = 0
    WHERE mu.mukey = '{mukey}'
        AND c.comppct_r >= 15
    ORDER BY c.comppct_r DESC
    """

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                USDA_SOIL_URL,
                data={"query": query, "format": "JSON"},
                timeout=30,
            )
            response.raise_for_status()
            result = response.json()

            if "Table" in result and result["Table"]:
                rows = result["Table"]
                if rows:
                    first_row = rows[0]
                    if isinstance(first_row[0], str) and first_row[0].lower() in ["mukey", "mu.mukey"]:
                        data_rows = rows[1:]
                    else:
                        data_rows = rows

                    components = []
                    for row in data_rows:
                        comp = dict(zip(columns, row, strict=False))
                        components.append(comp)

                    if components:
                        dominant = components[0]
                        return {
                            "mukey": dominant.get("mukey"),
                            "muname": dominant.get("muname"),
                            "mukind": dominant.get("mukind"),
                            "dominant_component": dominant.get("compname"),
                            "comppct": dominant.get("comppct"),
                            "taxorder": dominant.get("taxorder"),
                            "drainage": dominant.get("drainage"),
                            "hydgrp": dominant.get("hydgrp"),
                            "sand_pct": dominant.get("sand_pct"),
                            "silt_pct": dominant.get("silt_pct"),
                            "clay_pct": dominant.get("clay_pct"),
                            "organic_matter_pct": dominant.get("organic_matter_pct"),
                            "ksat_mm_hr": dominant.get("ksat"),
                            "awc_cm_cm": dominant.get("awc"),
                            "all_components": components,
                        }
    except Exception as e:
        if on_progress:
            on_progress(f"    Error querying mukey {mukey}: {e}")
    return None


async def query_soil_at_point(
    lat: float,
    lon: float,
    on_progress: Callable[[str], None] | None = None,
) -> dict | None:
    """
    Query USDA Soil Data Access for soil info at a point.

    Uses SoilWeb to get mukey, then USDA SDA for properties.

    Args:
        lat: Latitude
        lon: Longitude
        on_progress: Optional callback for progress/error messages

    Returns:
        Soil data dict or None if not found
    """
    # Try SoilWeb's reflector API
    try:
        url = (
            f"https://casoilresource.lawr.ucdavis.edu/soil_web/reflector_api/soils.php?what=mapunit&lat={lat}&lon={lon}"
        )
        async with httpx.AsyncClient() as client:
            response = await client.get(url, timeout=15, follow_redirects=True)
            if response.status_code == 200:
                html = response.text

                # Parse mukey from HTML response
                mukey_match = re.search(r"mukey=(\d{6,7})", html)
                if mukey_match:
                    mukey = mukey_match.group(1)
                    result = await query_soil_by_mukey(mukey, on_progress=on_progress)
                    if result:
                        return result

                # Pattern 2: <td> NNNNNN </td>
                cells = re.findall(r"<td>\s*(\d{6,7})\s*</td>", html)
                for cell in cells:
                    result = await query_soil_by_mukey(cell, on_progress=on_progress)
                    if result:
                        return result

    except Exception as e:
        if on_progress:
            on_progress(f"    SoilWeb error: {e}")

    # Fallback: Try the WFS approach
    mukey = await get_mukey_at_point(lat, lon)
    if mukey:
        return await query_soil_by_mukey(mukey, on_progress=on_progress)

    return None


async def fetch_all_paddock_soils(
    on_progress: Callable[[str], None] | None = None,
) -> dict:
    """Fetch soil data for all paddocks.

    Args:
        on_progress: Optional callback for progress updates, called with (message: str)

    Returns:
        Dict of paddock soil data keyed by paddock name
    """

    def log(msg: str) -> None:
        if on_progress:
            on_progress(msg)

    log("Fetching paddocks from AgriWebb...")
    fields = await get_fields(min_area_ha=0.2)
    log(f"Found {len(fields)} paddocks")

    paddock_soils = {}
    errors = []

    for i, field in enumerate(sorted(fields, key=lambda f: f.get("name", "")), 1):
        name = field.get("name", "Unnamed")
        field_id = field.get("id")
        area_ha = field.get("totalArea", 0)
        geometry = field.get("geometry", {})

        if on_progress:
            on_progress(f"[{i}/{len(fields)}] {name}... ")

        centroid = calculate_centroid(geometry)
        if not centroid:
            log("skipped (no geometry)")
            errors.append({"name": name, "error": "No valid geometry"})
            continue

        lat, lon = centroid
        soil_data = await query_soil_at_point(lat, lon, on_progress=on_progress)

        if soil_data:
            paddock_soils[name] = {
                "paddock_id": field_id,
                "area_ha": area_ha,
                "centroid": {"lat": lat, "lon": lon},
                "soil": soil_data,
            }
            log(f"{soil_data.get('drainage', 'Unknown')}")
        else:
            log("no data")
            errors.append({"name": name, "error": "No soil data returned"})

        # Small delay to be nice to USDA servers
        await asyncio.sleep(0.3)

    # Save results
    output = {
        "fetched_at": datetime.now(UTC).isoformat(),
        "paddock_count": len(paddock_soils),
        "paddocks": paddock_soils,
        "errors": errors,
    }

    output_path = get_cache_dir() / "paddock_soils.json"
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)

    log(f"\nSaved to: {output_path}")
    log(f"Successfully mapped: {len(paddock_soils)} paddocks")
    if errors:
        log(f"Errors: {len(errors)}")

    return paddock_soils
