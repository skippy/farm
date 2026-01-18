"""Data modules - livestock, fields, grazing, historical data."""

from agriwebb.data import livestock
from agriwebb.data.grazing import (
    calculate_paddock_consumption,
    load_farm_data,
    load_fields,
)
from agriwebb.data.livestock import (
    find_animal,
    format_lineage_tree,
    get_animal,
    get_animal_lineage,
    get_animals,
    get_mobs,
    get_offspring,
    get_pregnancies,
    get_treatments,
    get_weights,
    summarize_animals,
)

__all__ = [
    "livestock",
    "get_animals",
    "get_animal",
    "find_animal",
    "get_animal_lineage",
    "get_offspring",
    "get_mobs",
    "get_weights",
    "get_treatments",
    "get_pregnancies",
    "format_lineage_tree",
    "summarize_animals",
    "load_farm_data",
    "load_fields",
    "calculate_paddock_consumption",
]
