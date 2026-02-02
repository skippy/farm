"""Unit conversion utilities using pint.

All internal data is stored in metric (SI) units:
- Temperature: Celsius (°C)
- Precipitation/rainfall: millimeters (mm)
- Length: meters (m), kilometers (km)
- Mass: kilograms (kg)
- Area: hectares (ha)

Display units are controlled by settings.display_units:
- "metric": Display as stored (°C, mm)
- "imperial": Convert to °F, inches

Note: Temperature conversions use simple formulas rather than pint's
offset unit handling, which has ambiguity issues with multiplication.
"""

import pint

from agriwebb.core.config import settings

# Create a unit registry (lazily initialized)
_ureg: pint.UnitRegistry | None = None


def get_ureg() -> pint.UnitRegistry:
    """Get the pint unit registry (lazily initialized)."""
    global _ureg
    if _ureg is None:
        _ureg = pint.UnitRegistry()
    return _ureg


# =============================================================================
# Temperature Conversions
# =============================================================================


def celsius_to_fahrenheit(temp_c: float) -> float:
    """Convert Celsius to Fahrenheit."""
    return temp_c * 9 / 5 + 32


def fahrenheit_to_celsius(temp_f: float) -> float:
    """Convert Fahrenheit to Celsius."""
    return (temp_f - 32) * 5 / 9


def temp_c_to_display(temp_c: float) -> tuple[float, str]:
    """Convert Celsius to display units.

    Args:
        temp_c: Temperature in Celsius

    Returns:
        Tuple of (value, unit_symbol) in display units
    """
    if settings.display_units == "imperial":
        return (celsius_to_fahrenheit(temp_c), "°F")
    return (temp_c, "°C")


def format_temp(temp_c: float, decimals: int = 0) -> str:
    """Format temperature for display.

    Args:
        temp_c: Temperature in Celsius
        decimals: Number of decimal places

    Returns:
        Formatted string like "48°F" or "9°C"
    """
    value, unit = temp_c_to_display(temp_c)
    if decimals == 0:
        return f"{value:.0f}{unit}"
    return f"{value:.{decimals}f}{unit}"


def format_temp_range(low_c: float, high_c: float) -> str:
    """Format a temperature range for display.

    Args:
        low_c: Low temperature in Celsius
        high_c: High temperature in Celsius

    Returns:
        Formatted string like "44-52°F" or "7-11°C"
    """
    low_val, unit = temp_c_to_display(low_c)
    high_val, _ = temp_c_to_display(high_c)
    return f"{low_val:.0f}-{high_val:.0f}{unit}"


# =============================================================================
# Precipitation/Length Conversions
# =============================================================================


def precip_mm_to_display(mm: float) -> tuple[float, str]:
    """Convert millimeters to display units.

    Args:
        mm: Precipitation in millimeters

    Returns:
        Tuple of (value, unit_symbol) in display units
    """
    ureg = get_ureg()

    if settings.display_units == "imperial":
        inches = (mm * ureg.mm).to(ureg.inch).magnitude
        return (inches, '"')
    return (mm, "mm")


def format_precip(mm: float, decimals: int | None = None) -> str:
    """Format precipitation for display.

    Args:
        mm: Precipitation in millimeters
        decimals: Number of decimal places (default: 2 for inches, 1 for mm)

    Returns:
        Formatted string like '0.33"' or "8.4mm"
    """
    value, unit = precip_mm_to_display(mm)

    if decimals is None:
        decimals = 2 if settings.display_units == "imperial" else 1

    if value < 0.01 and settings.display_units == "imperial":
        return "—"
    if value < 0.1 and settings.display_units == "metric":
        return "—"

    return f"{value:.{decimals}f}{unit}"


# =============================================================================
# Compound Formatting
# =============================================================================


def format_precip_summary(total_mm: float, days: int) -> str:
    """Format precipitation summary (total over N days).

    Args:
        total_mm: Total precipitation in millimeters
        days: Number of days

    Returns:
        Formatted string like '0.8" / 7d' or "21mm / 7d"
    """
    precip_str = format_precip(total_mm, decimals=1)
    return f"{precip_str} / {days}d"


def get_precip_description(precip_mm: float) -> str:
    """Get a human-readable precipitation description.

    Args:
        precip_mm: Precipitation in millimeters

    Returns:
        Description like "Dry", "Light rain", "Heavy rain"
    """
    if precip_mm < 0.1:
        return "Dry"
    elif precip_mm < 2.5:
        return "Light rain"
    elif precip_mm < 7.5:
        return "Moderate rain"
    elif precip_mm < 15:
        return "Heavy rain"
    else:
        return "Very heavy"


# =============================================================================
# Display Unit Info
# =============================================================================


def get_temp_unit() -> str:
    """Get the temperature unit symbol for current display settings."""
    return "°F" if settings.display_units == "imperial" else "°C"


def get_precip_unit() -> str:
    """Get the precipitation unit symbol for current display settings."""
    return '"' if settings.display_units == "imperial" else "mm"


def is_imperial() -> bool:
    """Check if display units are imperial."""
    return settings.display_units == "imperial"
