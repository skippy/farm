"""Sync modules - push data to AgriWebb."""

from agriwebb.sync.feed import cli as sync_foo_cli
from agriwebb.sync.growth_rates import cli as sync_growth_cli

__all__ = [
    "sync_growth_cli",
    "sync_foo_cli",
]
