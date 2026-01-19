"""Tests for the setup module."""

import httpx

from agriwebb.cli import setup


class TestFormatStationName:
    """Tests for the format_station_name function."""

    def test_removes_state_and_country(self):
        """Verify state/country suffix is removed."""
        result = setup.format_station_name("FRIDAY HARBOR AIRPORT, WA US")
        assert result == "Friday Harbor Airport"

    def test_converts_to_title_case(self):
        """Verify all caps is converted to title case."""
        result = setup.format_station_name("SEATTLE TACOMA INTERNATIONAL")
        assert result == "Seattle Tacoma International"

    def test_handles_single_word(self):
        """Verify single word names work."""
        result = setup.format_station_name("BELLINGHAM")
        assert result == "Bellingham"

    def test_handles_multiple_commas(self):
        """Verify only first comma splits."""
        result = setup.format_station_name("SOME PLACE, EXTRA, WA US")
        assert result == "Some Place"


class TestCheckMark:
    """Tests for the check_mark helper."""

    def test_returns_ok_on_true(self):
        assert setup.check_mark(True) == "[OK]"

    def test_returns_missing_on_false(self):
        assert setup.check_mark(False) == "[MISSING]"


class TestCheckEnvVars:
    """Tests for checking environment variables."""

    async def test_detects_set_vars(self, monkeypatch):
        """Verify detection of set environment variables."""
        monkeypatch.setenv("AGRIWEBB_API_KEY", "test-key")
        monkeypatch.setenv("AGRIWEBB_FARM_ID", "test-farm")
        monkeypatch.setenv("NCEI_STATION_ID", "USW00094276")

        result = await setup.check_env_vars()

        assert result["AGRIWEBB_API_KEY"] is True
        assert result["AGRIWEBB_FARM_ID"] is True
        assert result["NCEI_STATION_ID"] is True

    async def test_detects_missing_vars(self, monkeypatch):
        """Verify detection of missing environment variables."""
        monkeypatch.delenv("GEE_PROJECT_ID", raising=False)

        result = await setup.check_env_vars()

        assert result["GEE_PROJECT_ID"] is False


class TestNceiConnection:
    """Tests for NCEI connection testing."""

    async def test_returns_station_name_on_success(self, mock_ncei):
        """Verify station name is returned from API."""
        mock_ncei.get("/access/services/data/v1").mock(
            return_value=httpx.Response(200, json=[{"NAME": "FRIDAY HARBOR AIRPORT, WA US", "STATION": "USW00094276"}])
        )

        result = await setup.test_ncei_connection()

        assert result == "Friday Harbor Airport"

    async def test_returns_none_on_empty_response(self, mock_ncei):
        """Verify None returned when no data."""
        mock_ncei.get("/access/services/data/v1").mock(return_value=httpx.Response(200, json=[]))

        result = await setup.test_ncei_connection()

        assert result is None

    async def test_returns_none_on_error(self, mock_ncei):
        """Verify None returned on HTTP error."""
        mock_ncei.get("/access/services/data/v1").mock(return_value=httpx.Response(500))

        result = await setup.test_ncei_connection()

        assert result is None
