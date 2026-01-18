"""Introspect AgriWebb API to find pasture growth rate schema."""

import asyncio
import json

from agriwebb.core import graphql


async def main():
    # Full introspection query
    query = """
    {
      __schema {
        types {
          name
          kind
          inputFields {
            name
            description
            type {
              name
              kind
              ofType { name kind ofType { name kind } }
            }
          }
        }
      }
    }
    """
    result = await graphql(query)
    types = result.get("data", {}).get("__schema", {}).get("types", [])

    # Find pasture-related input types
    print("=== Pasture-related types ===\n")
    for t in types:
        name = t.get("name", "")
        if "pasture" in name.lower() or "growth" in name.lower():
            print(f"Type: {name} ({t.get('kind')})")
            for field in t.get("inputFields", []) or []:
                field_type = field.get("type", {})
                type_name = field_type.get("name")
                if not type_name:
                    of = field_type.get("ofType", {})
                    type_name = of.get("name") if of else "?"
                    if not type_name and of:
                        type_name = of.get("ofType", {}).get("name", "?")
                desc = field.get('description', '')
                print(f"  - {field['name']}: {type_name} ({field_type.get('kind')}) - {desc}")
            print()

    # Also look for IOT/reading types since description mentioned "IOT record"
    print("=== IOT/Reading types ===\n")
    for t in types:
        name = t.get("name", "")
        if "iot" in name.lower() or "reading" in name.lower():
            print(f"Type: {name} ({t.get('kind')})")
            for field in t.get("inputFields", []) or []:
                print(f"  - {field['name']}")

    # Try a test mutation to see what error we get
    print("\n=== Testing addPastureGrowthRates with sample data ===\n")
    test_mutation = """
    mutation {
      addPastureGrowthRates(input: [{}]) {
        pastureGrowthRates {
          id
        }
      }
    }
    """
    test_result = await graphql(test_mutation)
    print(json.dumps(test_result, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
