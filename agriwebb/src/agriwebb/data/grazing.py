"""
Grazing consumption model for pasture growth estimation.

Calculates daily dry matter intake (DMI) per animal based on:
- Body weight (from most recent weigh record)
- Age class (ewe, ram, lamb, wether, etc.)
- Lactation status (nursing lambs increases intake significantly)
- Number of lambs nursing (twins/triplets = higher milk production)

Then aggregates consumption by paddock to estimate grazing pressure.
"""

import json
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, TypedDict

from agriwebb.core import get_cache_dir

if TYPE_CHECKING:
    from pathlib import Path

# Default weights by age class (kg) when no weight record available
DEFAULT_WEIGHTS = {
    "ewe": 140,
    "ram": 160,
    "maiden_ewe": 130,
    "wether": 150,
    "ewe_hogget": 110,
    "ram_hogget": 100,
    "wether_hogget": 110,
    "ewe_weaner": 70,
    "ram_weaner": 65,
    "wether_weaner": 65,
    "ewe_lamb": 35,
    "ram_lamb": 35,
    "wether_lamb": 35,
    "lamb": 30,
}

# Base intake as % of body weight by age class
# Higher for growing animals, lower for mature
BASE_INTAKE_PCT = {
    "ewe": 0.025,  # 2.5% of BW for maintenance
    "ram": 0.025,
    "maiden_ewe": 0.028,  # Still growing
    "wether": 0.023,  # Often mature, lower metabolism
    "ewe_hogget": 0.032,
    "ram_hogget": 0.032,
    "wether_hogget": 0.030,
    "ewe_weaner": 0.040,  # Growing fast
    "ram_weaner": 0.040,
    "wether_weaner": 0.038,
    "ewe_lamb": 0.045,  # Young, high intake relative to BW
    "ram_lamb": 0.045,
    "wether_lamb": 0.045,
    "lamb": 0.045,
}

# Lactation multipliers by number of lambs nursing
# Based on NRC requirements for milk production
LACTATION_MULTIPLIERS = {
    0: 1.0,  # Not lactating
    1: 1.7,  # Single lamb - ~70% increase
    2: 2.3,  # Twins - ~130% increase
    3: 2.9,  # Triplets - ~190% increase
}

# Weaning assumption: 4 months (120 days) after birth if no wean record
DEFAULT_WEANING_DAYS = 120


class AnimalIntake(TypedDict):
    """Intake calculation result for one animal."""

    animal_id: str
    name: str
    age_class: str
    weight_kg: float
    weight_source: str  # 'record' or 'default'
    is_lactating: bool
    lambs_nursing: int
    base_intake_kg: float
    lactation_multiplier: float
    total_intake_kg: float
    paddock_id: str | None
    paddock_name: str | None


class PaddockConsumption(TypedDict):
    """Aggregated consumption for a paddock."""

    paddock_id: str
    paddock_name: str
    area_ha: float
    animal_count: int
    total_intake_kg_day: float
    intake_per_ha_kg_day: float
    animals: list[str]  # Animal names for reference


def load_farm_data(cache_path: Path | None = None) -> dict:
    """Load cached farm data."""
    if cache_path is None:
        cache_path = get_cache_dir() / "animals.json"

    with open(cache_path) as f:
        return json.load(f)


def load_fields(cache_path: Path | None = None) -> dict[str, dict]:
    """Load fields and return as id -> field dict."""
    if cache_path is None:
        cache_path = get_cache_dir() / "animals.json"

    with open(cache_path) as f:
        data = json.load(f)

    fields = data.get("fields", [])
    # Also try field_names for quick lookup
    field_names = data.get("field_names", {})

    result = {}
    for f in fields:
        result[f["id"]] = {
            "id": f["id"],
            "name": f.get("name", "Unknown"),
            "area_ha": f.get("totalArea", 0) or f.get("grazableArea", 0),
        }

    # Add names from field_names if not in fields
    for fid, fname in field_names.items():
        if fid not in result:
            result[fid] = {"id": fid, "name": fname, "area_ha": 0}

    return result


def get_latest_weight(animal: dict) -> tuple[float, str]:
    """
    Get the most recent weight for an animal.

    Returns (weight_kg, source) where source is 'record' or 'default'.
    """
    records = animal.get("records", [])
    weights = [r for r in records if r.get("recordType") == "weigh"]

    if weights:
        # Sort by observation date (epoch ms)
        sorted_weights = sorted(weights, key=lambda w: w.get("observationDate", 0), reverse=True)
        latest = sorted_weights[0]
        weight_data = latest.get("weight") or {}
        weight_val = weight_data.get("value")

        if weight_val and weight_val > 0:
            return float(weight_val), "record"

    # Fall back to default weight for age class
    age_class = (animal.get("characteristics") or {}).get("ageClass", "ewe")
    default = DEFAULT_WEIGHTS.get(age_class, DEFAULT_WEIGHTS["ewe"])
    return float(default), "default"


def get_wean_date(animal: dict) -> date | None:
    """
    Get the wean date for a lamb.

    Checks for wean record first, then falls back to birth_date + 4 months.
    Returns None if animal is not a lamb or has no birth info.
    """
    records = animal.get("records", [])

    # Look for wean record
    wean_records = [r for r in records if r.get("recordType") == "wean"]
    if wean_records:
        # Use most recent wean record
        sorted_weans = sorted(wean_records, key=lambda w: w.get("observationDate", 0), reverse=True)
        wean_date_ms = sorted_weans[0].get("observationDate")
        if wean_date_ms:
            return datetime.fromtimestamp(wean_date_ms / 1000).date()

    # Fall back to birth date + 4 months
    characteristics = animal.get("characteristics") or {}
    birth_date_str = characteristics.get("birthDate")

    if birth_date_str:
        try:
            # Birth date might be epoch ms or ISO string
            if isinstance(birth_date_str, (int, float)):
                birth = datetime.fromtimestamp(birth_date_str / 1000).date()
            else:
                birth = datetime.fromisoformat(birth_date_str.replace("Z", "")).date()
            return birth + timedelta(days=DEFAULT_WEANING_DAYS)
        except (ValueError, TypeError):
            pass

    return None


def find_nursing_lambs(
    animals: list[dict],
    reference_date: date | None = None,
) -> dict[str, list[dict]]:
    """
    Find which lambs are currently nursing, grouped by dam.

    Returns dict of dam_id -> list of nursing lamb dicts.
    """
    if reference_date is None:
        reference_date = date.today()

    # First, identify all lambs (by age class containing 'lamb' or 'weaner')
    lambs = []
    for a in animals:
        if not (a.get("state") or {}).get("onFarm"):
            continue

        age_class = (a.get("characteristics") or {}).get("ageClass", "")
        if "lamb" in age_class.lower() or "weaner" in age_class.lower():
            lambs.append(a)

    # Group lambs by dam, checking if still nursing
    nursing_by_dam: dict[str, list[dict]] = {}

    for lamb in lambs:
        # Get dam
        parentage = lamb.get("parentage") or {}
        dams = parentage.get("dams") or []

        if not dams:
            continue

        dam_id = dams[0].get("parentAnimalId")
        if not dam_id:
            continue

        # Check if lamb is still nursing
        wean_date = get_wean_date(lamb)

        if wean_date is None:
            # No birth info - assume not nursing (can't determine)
            continue

        if reference_date < wean_date:
            # Still nursing
            if dam_id not in nursing_by_dam:
                nursing_by_dam[dam_id] = []
            nursing_by_dam[dam_id].append(lamb)

    return nursing_by_dam


def calculate_animal_intake(
    animal: dict,
    nursing_lambs: int = 0,
    fields: dict[str, dict] | None = None,
) -> AnimalIntake:
    """
    Calculate daily dry matter intake for a single animal.
    """
    identity = animal.get("identity") or {}
    characteristics = animal.get("characteristics") or {}
    state = animal.get("state") or {}

    animal_id = animal.get("animalId", "")
    name = identity.get("name") or identity.get("vid") or animal_id[:8]
    age_class = characteristics.get("ageClass", "ewe")

    # Get weight
    weight_kg, weight_source = get_latest_weight(animal)

    # Base intake
    intake_pct = BASE_INTAKE_PCT.get(age_class, 0.025)
    base_intake = weight_kg * intake_pct

    # Lactation adjustment
    is_lactating = nursing_lambs > 0
    lactation_mult = LACTATION_MULTIPLIERS.get(min(nursing_lambs, 3), LACTATION_MULTIPLIERS[3])

    total_intake = base_intake * lactation_mult

    # Location
    paddock_id = state.get("currentLocationId")
    paddock_name = None
    if paddock_id and fields:
        field_info = fields.get(paddock_id, {})
        paddock_name = field_info.get("name")

    return AnimalIntake(
        animal_id=animal_id,
        name=name,
        age_class=age_class,
        weight_kg=weight_kg,
        weight_source=weight_source,
        is_lactating=is_lactating,
        lambs_nursing=nursing_lambs,
        base_intake_kg=round(base_intake, 2),
        lactation_multiplier=lactation_mult,
        total_intake_kg=round(total_intake, 2),
        paddock_id=paddock_id,
        paddock_name=paddock_name,
    )


def calculate_paddock_consumption(
    animals: list[dict],
    fields: dict[str, dict] | None = None,
    min_area_ha: float = 0.2,
    reference_date: date | None = None,
) -> dict[str, PaddockConsumption]:
    """
    Calculate total grazing consumption by paddock.

    Args:
        animals: List of animal dicts (from cache)
        fields: Field info dict (id -> {name, area_ha})
        min_area_ha: Minimum paddock size to include (0.5 acres ≈ 0.2 ha)
        reference_date: Date to check nursing status (default: today)

    Returns:
        Dict of paddock_id -> PaddockConsumption
    """
    if reference_date is None:
        reference_date = date.today()

    if fields is None:
        fields = load_fields()

    # Filter to on-farm animals
    on_farm = [a for a in animals if (a.get("state") or {}).get("onFarm")]

    # Find nursing lambs by dam
    nursing_by_dam = find_nursing_lambs(on_farm, reference_date)

    # Calculate intake per animal
    intakes: list[AnimalIntake] = []
    for animal in on_farm:
        animal_id = animal.get("animalId", "")
        nursing_count = len(nursing_by_dam.get(animal_id, []))

        intake = calculate_animal_intake(animal, nursing_count, fields)
        intakes.append(intake)

    # Aggregate by paddock
    paddock_data: dict[str, dict] = {}

    for intake in intakes:
        pid = intake["paddock_id"]
        if not pid:
            continue

        if pid not in paddock_data:
            field_info = fields.get(pid, {})
            paddock_data[pid] = {
                "paddock_id": pid,
                "paddock_name": field_info.get("name", "Unknown"),
                "area_ha": field_info.get("area_ha", 0),
                "animal_count": 0,
                "total_intake_kg_day": 0,
                "animals": [],
            }

        paddock_data[pid]["animal_count"] += 1
        paddock_data[pid]["total_intake_kg_day"] += intake["total_intake_kg"]
        paddock_data[pid]["animals"].append(intake["name"])

    # Calculate per-hectare consumption and filter by min area
    result: dict[str, PaddockConsumption] = {}

    for pid, data in paddock_data.items():
        area = data["area_ha"]

        # Skip small paddocks
        if area < min_area_ha:
            continue

        intake_per_ha = data["total_intake_kg_day"] / area if area > 0 else 0

        result[pid] = PaddockConsumption(
            paddock_id=pid,
            paddock_name=data["paddock_name"],
            area_ha=round(area, 2),
            animal_count=data["animal_count"],
            total_intake_kg_day=round(data["total_intake_kg_day"], 1),
            intake_per_ha_kg_day=round(intake_per_ha, 1),
            animals=data["animals"],
        )

    return result


def get_grazing_summary(cache_path: Path | None = None) -> dict:
    """
    Get a complete grazing summary from cached data.

    Returns dict with:
    - paddock_consumption: consumption by paddock
    - lactating_ewes: list of ewes with nursing lambs
    - total_intake: farm-wide total intake
    - summary stats
    """
    data = load_farm_data(cache_path)
    animals = data.get("animals", [])
    fields = load_fields(cache_path)

    on_farm = [a for a in animals if (a.get("state") or {}).get("onFarm")]
    nursing_by_dam = find_nursing_lambs(on_farm)

    # Calculate consumption
    consumption = calculate_paddock_consumption(animals, fields)

    # Identify lactating ewes
    lactating_ewes = []
    for dam_id, lambs in nursing_by_dam.items():
        # Find the dam
        dam = next((a for a in animals if a.get("animalId") == dam_id), None)
        if dam:
            dam_name = (dam.get("identity") or {}).get("name", dam_id[:8])
            lamb_names = [(lamb.get("identity") or {}).get("name", "?") for lamb in lambs]
            lactating_ewes.append(
                {
                    "dam_id": dam_id,
                    "dam_name": dam_name,
                    "lamb_count": len(lambs),
                    "lamb_names": lamb_names,
                }
            )

    # Total intake across all paddocks
    total_intake = sum(c["total_intake_kg_day"] for c in consumption.values())
    total_animals = sum(c["animal_count"] for c in consumption.values())

    return {
        "date": date.today().isoformat(),
        "paddock_consumption": consumption,
        "lactating_ewes": lactating_ewes,
        "total_animals_in_paddocks": total_animals,
        "total_intake_kg_day": round(total_intake, 1),
        "lactating_ewe_count": len(lactating_ewes),
        "total_lambs_nursing": sum(len(lambs) for lambs in nursing_by_dam.values()),
    }


# CLI for testing
def main():
    """Print grazing summary."""
    print("=" * 70)
    print("Grazing Consumption Summary")
    print("=" * 70)

    summary = get_grazing_summary()

    print(f"\nDate: {summary['date']}")
    print(f"Total animals in paddocks: {summary['total_animals_in_paddocks']}")
    print(f"Total daily intake: {summary['total_intake_kg_day']:.1f} kg DM")
    print(f"Lactating ewes: {summary['lactating_ewe_count']}")
    print(f"Lambs nursing: {summary['total_lambs_nursing']}")

    if summary["lactating_ewes"]:
        print("\nLactating ewes:")
        for ewe in summary["lactating_ewes"]:
            lambs_str = ", ".join(ewe["lamb_names"])
            print(f"  {ewe['dam_name']}: {ewe['lamb_count']} lamb(s) - {lambs_str}")

    print("\nConsumption by paddock:")
    print(f"{'Paddock':<25} {'Animals':<10} {'Intake/day':<15} {'per ha'}")
    print("-" * 65)

    consumption = summary["paddock_consumption"]
    for _pid, data in sorted(consumption.items(), key=lambda x: -x[1]["intake_per_ha_kg_day"]):
        print(
            f"{data['paddock_name']:<25} "
            f"{data['animal_count']:<10} "
            f"{data['total_intake_kg_day']:>8.1f} kg     "
            f"{data['intake_per_ha_kg_day']:>6.1f} kg/ha"
        )


if __name__ == "__main__":
    main()
