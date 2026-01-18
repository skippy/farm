"""Explore AgriWebb fields/paddocks schema and data."""

import asyncio

from agriwebb.client import graphql
from agriwebb.config import settings


async def introspect_field_type():
    """Get the schema for Field type."""
    query = """
    {
      __type(name: "Field") {
        name
        description
        fields {
          name
          description
          type {
            name
            kind
            ofType {
              name
              kind
            }
          }
        }
      }
    }
    """
    result = await graphql(query)
    return result


async def get_fields():
    """Fetch all fields for the farm."""
    query = f"""
    {{
      fields(filter: {{ farmId: "{settings.agriwebb_farm_id}" }}) {{
        id
        name
        area {{
          value
          unit
        }}
        boundary {{
          coordinates
        }}
        fieldType
        currentUse
      }}
    }}
    """
    result = await graphql(query)
    return result


async def main():
    print("=== Introspecting Field type ===")
    schema = await introspect_field_type()

    if "errors" in schema:
        print("Schema introspection errors:", schema["errors"])
    else:
        field_type = schema.get("data", {}).get("__type", {})
        print(f"Type: {field_type.get('name')}")
        print(f"Description: {field_type.get('description')}")
        print("\nFields:")
        for f in field_type.get("fields", []):
            type_info = f.get("type", {})
            type_name = type_info.get("name") or (type_info.get("ofType", {}) or {}).get("name")
            print(f"  - {f['name']}: {type_name} - {f.get('description', '')}")

    print("\n=== Fetching fields for farm ===")
    fields = await get_fields()

    if "errors" in fields:
        print("Query errors:", fields["errors"])
    else:
        field_list = fields.get("data", {}).get("fields", [])
        print(f"Found {len(field_list)} fields\n")

        for field in field_list:
            area = field.get("area", {})
            area_val = area.get("value", 0) if area else 0
            area_unit = area.get("unit", "?") if area else "?"

            boundary = field.get("boundary", {})
            coords = boundary.get("coordinates", []) if boundary else []
            coord_count = len(coords) if coords else 0

            print(f"  {field.get('name', 'Unnamed')}")
            print(f"    ID: {field.get('id')}")
            print(f"    Area: {area_val} {area_unit}")
            print(f"    Type: {field.get('fieldType')}")
            print(f"    Use: {field.get('currentUse')}")
            print(f"    Boundary points: {coord_count}")
            print()


if __name__ == "__main__":
    asyncio.run(main())
