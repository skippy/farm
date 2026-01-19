"""AgriWebb API functions for pasture/growth data."""

from datetime import UTC, date, datetime

from agriwebb.core.config import settings

# =============================================================================
# GraphQL Mutations
# =============================================================================

ADD_PASTURE_GROWTH_RATE_MUTATION = """
mutation AddPastureGrowthRate($input: [AddPastureGrowthRateInput!]!) {
  addPastureGrowthRates(input: $input) {
    pastureGrowthRates {
      id
      time
      value
    }
  }
}
"""

ADD_FEED_ON_OFFER_MUTATION = """
mutation AddFeedOnOffer($input: [AddFeedOnOfferInput!]!) {
  addFeedOnOffers(input: $input) {
    feedOnOffers {
      id
      time
      value
    }
  }
}
"""

ADD_STANDING_DRY_MATTER_MUTATION = """
mutation AddStandingDryMatter($input: [AddTotalStandingDryMatterInput!]!) {
  addTotalStandingDryMatters(input: $input) {
    totalStandingDryMatters {
      id
      time
      value
    }
  }
}
"""


def _to_timestamp_ms(d: str | date) -> int:
    """Convert a date string or date object to milliseconds timestamp (noon UTC)."""
    if isinstance(d, str):
        d = date.fromisoformat(d)
    dt = datetime(d.year, d.month, d.day, hour=12, tzinfo=UTC)
    return int(dt.timestamp() * 1000)


# =============================================================================
# API Functions
# =============================================================================


async def add_pasture_growth_rates_batch(
    records: list[dict],
) -> dict:
    """
    Add multiple pasture growth rate records in a single API call.

    Args:
        records: List of dicts with keys: field_id, growth_rate, record_date

    Returns:
        AgriWebb API response
    """
    from agriwebb.core.client import graphql

    inputs = []
    for rec in records:
        timestamp_ms = _to_timestamp_ms(rec["record_date"])
        inputs.append(
            {
                "value": round(rec["growth_rate"], 1),
                "farmId": settings.agriwebb_farm_id,
                "fieldId": rec["field_id"],
                "time": timestamp_ms,
            }
        )

    variables = {"input": inputs}
    return await graphql(ADD_PASTURE_GROWTH_RATE_MUTATION, variables)


async def add_feed_on_offer_batch(
    records: list[dict],
    source: str = "IOT",
) -> dict:
    """
    Add Feed on Offer (FOO) records to AgriWebb.

    FOO is the total available pasture in kg DM/ha at a point in time.

    Args:
        records: List of dicts with keys: field_id, foo_kg_ha, record_date
        source: Source type - "IOT", "Manual", or "LivestockEstimate"

    Returns:
        AgriWebb API response

    Note:
        API only accepts records within the last 14 days.
    """
    from agriwebb.core.client import graphql

    inputs = []
    for rec in records:
        timestamp_ms = _to_timestamp_ms(rec["record_date"])
        inputs.append(
            {
                "value": round(rec["foo_kg_ha"], 0),
                "farmId": settings.agriwebb_farm_id,
                "fieldId": rec["field_id"],
                "time": timestamp_ms,
                "source": source,
            }
        )

    variables = {"input": inputs}
    return await graphql(ADD_FEED_ON_OFFER_MUTATION, variables)


async def add_standing_dry_matter_batch(
    records: list[dict],
) -> dict:
    """
    Add Total Standing Dry Matter (SDM) records to AgriWebb.

    SDM includes all dry matter (green + dead/senescent material).
    AgriWebb converts SDM to FOO using a utilization factor.

    Args:
        records: List of dicts with keys: field_id, sdm_kg_ha, record_date

    Returns:
        AgriWebb API response

    Note:
        API only accepts records within the last 14 days.
    """
    from agriwebb.core.client import graphql

    inputs = []
    for rec in records:
        timestamp_ms = _to_timestamp_ms(rec["record_date"])
        inputs.append(
            {
                "value": round(rec["sdm_kg_ha"], 0),
                "farmId": settings.agriwebb_farm_id,
                "fieldId": rec["field_id"],
                "time": timestamp_ms,
            }
        )

    variables = {"input": inputs}
    return await graphql(ADD_STANDING_DRY_MATTER_MUTATION, variables)
