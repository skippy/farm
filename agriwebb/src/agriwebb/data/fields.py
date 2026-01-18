"""Fetch and display AgriWebb field/paddock data."""

import asyncio
import json

from agriwebb.core import get_cache_dir, get_fields


async def main():
    print("Fetching fields from AgriWebb (min 0.2 ha / ~0.5 acres)...\n")

    fields = await get_fields(min_area_ha=0.2)

    print(f"Found {len(fields)} fields\n")
    print("-" * 60)

    for field in sorted(fields, key=lambda f: f.get("totalArea", 0), reverse=True):
        name = field.get("name", "Unnamed")
        total_ha = field.get("totalArea", 0)
        grazable_ha = field.get("grazableArea", 0)
        total_acres = total_ha * 2.471
        land_use = field.get("landUse", "Unknown")

        geometry = field.get("geometry", {})
        geom_type = geometry.get("type", "None")
        coords = geometry.get("coordinates", [])

        # Count polygon points
        if geom_type == "Polygon" and coords:
            point_count = len(coords[0]) if coords else 0
        elif geom_type == "MultiPolygon" and coords:
            point_count = sum(len(ring[0]) for ring in coords if ring)
        else:
            point_count = 0

        print(f"{name}")
        print(f"  ID: {field.get('id')}")
        print(f"  Area: {total_ha:.2f} ha ({total_acres:.1f} acres)")
        print(f"  Grazable: {grazable_ha:.2f} ha")
        print(f"  Land use: {land_use}")
        print(f"  Geometry: {geom_type} with {point_count} points")
        print()

    # Save raw data for inspection
    get_cache_dir().mkdir(parents=True, exist_ok=True)
    output_path = get_cache_dir() / "fields.json"
    with open(output_path, "w") as f:
        json.dump(fields, f, indent=2)
    print(f"Raw data saved to {output_path}")


if __name__ == "__main__":
    asyncio.run(main())
