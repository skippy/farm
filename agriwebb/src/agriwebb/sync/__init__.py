"""Sync modules - push data to AgriWebb."""

from agriwebb.sync.animals import cli as sync_animals_cli
from agriwebb.sync.feed import cli as sync_foo_cli
from agriwebb.sync.growth_rates import cli as sync_growth_cli

__all__ = [
    "sync_animals_cli",
    "sync_growth_cli",
    "sync_foo_cli",
]
