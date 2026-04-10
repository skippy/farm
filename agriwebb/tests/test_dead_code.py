"""Dead code verification tests.

Verify that the three legacy top-level modules have been deleted and that
no active code paths depend on them:

- agriwebb/src/agriwebb/client.py   (deleted, replaced by core/client.py)
- agriwebb/src/agriwebb/config.py   (deleted, replaced by core/config.py)
- agriwebb/src/agriwebb/weather.py  (deleted, replaced by weather/ package)

These files were confirmed as dead code and removed. These tests verify
they stay deleted and that all active imports continue to work.
"""

import sys
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _clean_agriwebb_modules():
    """Remove agriwebb modules before each test for isolation."""
    pre_loaded = {k for k in sys.modules if k.startswith("agriwebb")}
    yield
    added = {k for k in sys.modules if k.startswith("agriwebb")} - pre_loaded
    for mod in added:
        del sys.modules[mod]


# =============================================================================
# Legacy files deleted from disk
# =============================================================================


class TestLegacyFilesDeleted:
    """Confirm the legacy files have been deleted from disk."""

    _SRC_ROOT = Path(__file__).parent.parent / "src" / "agriwebb"

    def test_legacy_client_deleted(self):
        assert not (self._SRC_ROOT / "client.py").exists(), "Legacy client.py should have been deleted"

    def test_legacy_config_deleted(self):
        assert not (self._SRC_ROOT / "config.py").exists(), "Legacy config.py should have been deleted"

    def test_legacy_weather_deleted(self):
        assert not (self._SRC_ROOT / "weather.py").exists(), "Legacy weather.py should have been deleted"


# =============================================================================
# Active imports work without legacy code
# =============================================================================


class TestActiveImportsWork:
    """All active submodule imports succeed and don't load legacy config."""

    def test_main_package(self):
        import agriwebb

        assert hasattr(agriwebb, "__version__")

    def test_core_settings(self):
        from agriwebb.core import settings

        assert settings is not None
        assert "agriwebb.config" not in sys.modules

    def test_core_get_cache_dir(self):
        from agriwebb.core import get_cache_dir

        assert callable(get_cache_dir)
        assert "agriwebb.config" not in sys.modules

    def test_core_client_has_graphql_error(self):
        from agriwebb.core.client import GraphQLError

        assert issubclass(GraphQLError, Exception)
        assert "agriwebb.config" not in sys.modules

    def test_core_error_classes(self):
        from agriwebb.core.client import (
            AgriWebbAPIError,
            ExternalAPIError,
            RetryableError,
        )

        assert issubclass(AgriWebbAPIError, Exception)
        assert issubclass(RetryableError, Exception)
        assert issubclass(ExternalAPIError, Exception)

    def test_core_units(self):
        from agriwebb.core.units import format_precip, format_temp

        assert callable(format_temp)
        assert callable(format_precip)

    def test_data_livestock(self):
        from agriwebb.data.livestock import cli

        assert callable(cli)
        assert "agriwebb.config" not in sys.modules

    def test_data_functions(self):
        from agriwebb.data import find_animal, get_animals, summarize_animals

        assert callable(find_animal)
        assert callable(get_animals)
        assert callable(summarize_animals)

    def test_weather_package(self):
        from agriwebb.weather import cli

        assert callable(cli)
        assert "agriwebb.config" not in sys.modules

    def test_weather_ncei(self):
        from agriwebb.weather.ncei import fetch_ncei_precipitation

        assert callable(fetch_ncei_precipitation)

    def test_weather_openmeteo(self):
        from agriwebb.weather.openmeteo import fetch_historical

        assert callable(fetch_historical)

    def test_weather_is_package_not_file(self):
        """agriwebb.weather resolves to the package directory, not legacy .py."""
        import agriwebb.weather

        assert hasattr(agriwebb.weather, "__path__"), (
            "agriwebb.weather should be a package (directory), not a single .py file"
        )


# =============================================================================
# Legacy config never loads
# =============================================================================


class TestLegacyConfigNeverLoads:
    """After importing active modules, legacy agriwebb.config must not be in sys.modules."""

    def test_client_resolves_to_core(self):
        """If agriwebb.client is in sys.modules, it must be the core client."""
        if "agriwebb.client" in sys.modules:
            mod = sys.modules["agriwebb.client"]
            assert hasattr(mod, "GraphQLError"), "agriwebb.client resolved to legacy client.py instead of core.client"

    def test_config_not_loaded_after_imports(self):
        """Legacy agriwebb.config must not be loaded by any active import chain."""
        sys.modules.pop("agriwebb.config", None)
        # Force a fresh import chain through the most common entry points
        from agriwebb.core import settings  # noqa: F401
        from agriwebb.data import livestock  # noqa: F401
        from agriwebb.weather import ncei  # noqa: F401

        assert "agriwebb.config" not in sys.modules

    def test_bulk_imports_avoid_legacy(self):
        """Importing all active submodules at once doesn't load legacy config."""
        # Use __import__ to avoid CodeQL import-style warnings
        for mod_name in [
            "agriwebb",
            "agriwebb.core",
            "agriwebb.core.client",
            "agriwebb.core.config",
            "agriwebb.core.units",
            "agriwebb.data",
            "agriwebb.data.livestock",
            "agriwebb.weather",
            "agriwebb.weather.ncei",
            "agriwebb.weather.openmeteo",
        ]:
            __import__(mod_name)
        assert "agriwebb.config" not in sys.modules
