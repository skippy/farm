"""Characterization tests for agriwebb.core.units.

Captures current behavior of unit conversion utilities before refactoring.
"""

import pytest

from agriwebb.core.config import settings
from agriwebb.core.units import (
    celsius_to_fahrenheit,
    fahrenheit_to_celsius,
    format_precip,
    format_precip_summary,
    format_temp,
    format_temp_range,
    get_precip_description,
    get_precip_unit,
    get_temp_unit,
    is_imperial,
    precip_mm_to_display,
    temp_c_to_display,
)

# =============================================================================
# Temperature Conversions
# =============================================================================


class TestCelsiusToFahrenheit:
    """Characterize celsius_to_fahrenheit behavior."""

    def test_freezing_point(self):
        assert celsius_to_fahrenheit(0) == 32.0

    def test_boiling_point(self):
        assert celsius_to_fahrenheit(100) == 212.0

    def test_body_temperature(self):
        assert celsius_to_fahrenheit(37) == pytest.approx(98.6)

    def test_negative_temperature(self):
        assert celsius_to_fahrenheit(-40) == pytest.approx(-40.0)

    def test_typical_winter_day(self):
        """7C is a typical PNW winter day."""
        assert celsius_to_fahrenheit(7) == pytest.approx(44.6)


class TestFahrenheitToCelsius:
    """Characterize fahrenheit_to_celsius behavior."""

    def test_freezing_point(self):
        assert fahrenheit_to_celsius(32) == 0.0

    def test_boiling_point(self):
        assert fahrenheit_to_celsius(212) == 100.0

    def test_negative_temperature(self):
        assert fahrenheit_to_celsius(-40) == pytest.approx(-40.0)

    def test_roundtrip(self):
        """C -> F -> C should return original value."""
        original = 15.5
        assert fahrenheit_to_celsius(celsius_to_fahrenheit(original)) == pytest.approx(original)


# =============================================================================
# Display Unit Switching - Temperature
# =============================================================================


class TestTempCToDisplay:
    """Characterize temp_c_to_display behavior with display_units setting."""

    def test_imperial_returns_fahrenheit(self, monkeypatch):
        monkeypatch.setattr(settings, "display_units", "imperial")
        value, unit = temp_c_to_display(0)
        assert value == 32.0
        assert unit == "°F"

    def test_metric_returns_celsius(self, monkeypatch):
        monkeypatch.setattr(settings, "display_units", "metric")
        value, unit = temp_c_to_display(0)
        assert value == 0
        assert unit == "°C"

    def test_negative_temp_imperial(self, monkeypatch):
        monkeypatch.setattr(settings, "display_units", "imperial")
        value, unit = temp_c_to_display(-10)
        assert value == pytest.approx(14.0)
        assert unit == "°F"

    def test_negative_temp_metric(self, monkeypatch):
        monkeypatch.setattr(settings, "display_units", "metric")
        value, unit = temp_c_to_display(-10)
        assert value == -10
        assert unit == "°C"


class TestFormatTemp:
    """Characterize format_temp behavior."""

    def test_imperial_no_decimals(self, monkeypatch):
        monkeypatch.setattr(settings, "display_units", "imperial")
        result = format_temp(0)
        assert result == "32°F"

    def test_metric_no_decimals(self, monkeypatch):
        monkeypatch.setattr(settings, "display_units", "metric")
        result = format_temp(9.7)
        assert result == "10°C"

    def test_imperial_with_decimals(self, monkeypatch):
        monkeypatch.setattr(settings, "display_units", "imperial")
        result = format_temp(20, decimals=1)
        assert result == "68.0°F"

    def test_metric_with_decimals(self, monkeypatch):
        monkeypatch.setattr(settings, "display_units", "metric")
        result = format_temp(20.55, decimals=1)
        assert result == "20.6°C"

    def test_zero_celsius_metric(self, monkeypatch):
        monkeypatch.setattr(settings, "display_units", "metric")
        result = format_temp(0)
        assert result == "0°C"

    def test_negative_temp_format(self, monkeypatch):
        monkeypatch.setattr(settings, "display_units", "metric")
        result = format_temp(-5)
        assert result == "-5°C"


class TestFormatTempRange:
    """Characterize format_temp_range behavior."""

    def test_imperial_range(self, monkeypatch):
        monkeypatch.setattr(settings, "display_units", "imperial")
        result = format_temp_range(7, 11)
        assert result == "45-52°F"

    def test_metric_range(self, monkeypatch):
        monkeypatch.setattr(settings, "display_units", "metric")
        result = format_temp_range(7, 11)
        assert result == "7-11°C"

    def test_range_with_negative_low(self, monkeypatch):
        monkeypatch.setattr(settings, "display_units", "metric")
        result = format_temp_range(-3, 5)
        assert result == "-3-5°C"


# =============================================================================
# Precipitation Conversions
# =============================================================================


class TestPrecipMmToDisplay:
    """Characterize precip_mm_to_display behavior."""

    def test_imperial_converts_mm_to_inches(self, monkeypatch):
        monkeypatch.setattr(settings, "display_units", "imperial")
        value, unit = precip_mm_to_display(25.4)  # 1 inch
        assert value == pytest.approx(1.0)
        assert unit == '"'

    def test_metric_returns_mm(self, monkeypatch):
        monkeypatch.setattr(settings, "display_units", "metric")
        value, unit = precip_mm_to_display(10.0)
        assert value == 10.0
        assert unit == "mm"

    def test_zero_mm(self, monkeypatch):
        monkeypatch.setattr(settings, "display_units", "metric")
        value, unit = precip_mm_to_display(0)
        assert value == 0
        assert unit == "mm"

    def test_zero_mm_imperial(self, monkeypatch):
        monkeypatch.setattr(settings, "display_units", "imperial")
        value, unit = precip_mm_to_display(0)
        assert value == pytest.approx(0.0)
        assert unit == '"'


class TestFormatPrecip:
    """Characterize format_precip behavior including dash threshold."""

    def test_imperial_default_decimals(self, monkeypatch):
        monkeypatch.setattr(settings, "display_units", "imperial")
        result = format_precip(25.4)
        assert result == '1.00"'

    def test_metric_default_decimals(self, monkeypatch):
        monkeypatch.setattr(settings, "display_units", "metric")
        result = format_precip(8.4)
        assert result == "8.4mm"

    def test_imperial_trace_returns_dash(self, monkeypatch):
        """Values below 0.01 inches return em-dash in imperial."""
        monkeypatch.setattr(settings, "display_units", "imperial")
        # 0.01 inches = 0.254 mm; use something smaller
        result = format_precip(0.1)  # ~0.004 inches
        assert result == "\u2014"

    def test_metric_trace_returns_dash(self, monkeypatch):
        """Values below 0.1mm return em-dash in metric."""
        monkeypatch.setattr(settings, "display_units", "metric")
        result = format_precip(0.05)
        assert result == "\u2014"

    def test_custom_decimals(self, monkeypatch):
        monkeypatch.setattr(settings, "display_units", "metric")
        result = format_precip(12.345, decimals=2)
        assert result == "12.35mm"

    def test_zero_metric(self, monkeypatch):
        """Zero precipitation should return dash."""
        monkeypatch.setattr(settings, "display_units", "metric")
        result = format_precip(0)
        assert result == "\u2014"

    def test_zero_imperial(self, monkeypatch):
        """Zero precipitation should return dash."""
        monkeypatch.setattr(settings, "display_units", "imperial")
        result = format_precip(0)
        assert result == "\u2014"


class TestFormatPrecipSummary:
    """Characterize format_precip_summary behavior."""

    def test_imperial_summary(self, monkeypatch):
        monkeypatch.setattr(settings, "display_units", "imperial")
        result = format_precip_summary(25.4, 7)
        assert result == '1.0" / 7d'

    def test_metric_summary(self, monkeypatch):
        monkeypatch.setattr(settings, "display_units", "metric")
        result = format_precip_summary(21.0, 7)
        assert result == "21.0mm / 7d"

    def test_zero_precip_summary(self, monkeypatch):
        monkeypatch.setattr(settings, "display_units", "metric")
        result = format_precip_summary(0, 14)
        # Zero triggers the dash for format_precip
        assert result == "\u2014 / 14d"


# =============================================================================
# Precipitation Description
# =============================================================================


class TestGetPrecipDescription:
    """Characterize rain intensity thresholds."""

    def test_dry(self):
        assert get_precip_description(0) == "Dry"

    def test_trace_is_dry(self):
        assert get_precip_description(0.05) == "Dry"

    def test_light_rain_lower_bound(self):
        assert get_precip_description(0.1) == "Light rain"

    def test_light_rain(self):
        assert get_precip_description(2.0) == "Light rain"

    def test_moderate_rain_lower_bound(self):
        assert get_precip_description(2.5) == "Moderate rain"

    def test_moderate_rain(self):
        assert get_precip_description(5.0) == "Moderate rain"

    def test_heavy_rain_lower_bound(self):
        assert get_precip_description(7.5) == "Heavy rain"

    def test_heavy_rain(self):
        assert get_precip_description(10.0) == "Heavy rain"

    def test_very_heavy_lower_bound(self):
        assert get_precip_description(15) == "Very heavy"

    def test_very_heavy(self):
        assert get_precip_description(50) == "Very heavy"


# =============================================================================
# Display Unit Info Helpers
# =============================================================================


class TestUnitInfoHelpers:
    """Characterize get_temp_unit, get_precip_unit, is_imperial."""

    def test_get_temp_unit_imperial(self, monkeypatch):
        monkeypatch.setattr(settings, "display_units", "imperial")
        assert get_temp_unit() == "°F"

    def test_get_temp_unit_metric(self, monkeypatch):
        monkeypatch.setattr(settings, "display_units", "metric")
        assert get_temp_unit() == "°C"

    def test_get_precip_unit_imperial(self, monkeypatch):
        monkeypatch.setattr(settings, "display_units", "imperial")
        assert get_precip_unit() == '"'

    def test_get_precip_unit_metric(self, monkeypatch):
        monkeypatch.setattr(settings, "display_units", "metric")
        assert get_precip_unit() == "mm"

    def test_is_imperial_true(self, monkeypatch):
        monkeypatch.setattr(settings, "display_units", "imperial")
        assert is_imperial() is True

    def test_is_imperial_false(self, monkeypatch):
        monkeypatch.setattr(settings, "display_units", "metric")
        assert is_imperial() is False
