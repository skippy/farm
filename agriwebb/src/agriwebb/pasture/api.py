"""AgriWebb API functions for pasture/growth data."""

from datetime import UTC, date, datetime

from agriwebb.core.config import settings

# =============================================================================
# GraphQL Queries
# =============================================================================

PASTURE_GROWTH_RATES_QUERY = """
query GetPastureGrowthRates($farmId: String!) {
  pastureGrowthRates(filter: {
    farmId: { _eq: $farmId }
  }) {
    id
    time
    value
    fieldId
  }
}
"""


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
    feedOnOffers {
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
    from agriwebb.core.client import graphql_with_retry

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
    return await graphql_with_retry(ADD_PASTURE_GROWTH_RATE_MUTATION, variables)


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
    from agriwebb.core.client import graphql_with_retry

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
    return await graphql_with_retry(ADD_FEED_ON_OFFER_MUTATION, variables)


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
    from agriwebb.core.client import graphql_with_retry

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
    return await graphql_with_retry(ADD_STANDING_DRY_MATTER_MUTATION, variables)


async def get_pasture_growth_rates(
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[dict]:
    """Get pasture growth rate records from AgriWebb.

    Args:
        start_date: Optional start date filter (ISO format)
        end_date: Optional end date filter (ISO format)

    Returns:
        List of growth rate records with id, time, value, fieldId
    """
    from agriwebb.core.client import graphql_with_retry

    if start_date or end_date:
        # Build time filter - must combine _gte and _lte in a single time object
        time_conditions = []
        if start_date:
            time_conditions.append(f"_gte: {_to_timestamp_ms(start_date)}")
        if end_date:
            time_conditions.append(f"_lte: {_to_timestamp_ms(end_date)}")
        time_filter = f", time: {{ {', '.join(time_conditions)} }}" if time_conditions else ""

        query = f"""
        {{
          pastureGrowthRates(filter: {{
            farmId: {{ _eq: "{settings.agriwebb_farm_id}" }}
            {time_filter}
          }}) {{
            id
            time
            value
            fieldId
          }}
        }}
        """
        result = await graphql_with_retry(query)
    else:
        variables = {"farmId": settings.agriwebb_farm_id}
        result = await graphql_with_retry(PASTURE_GROWTH_RATES_QUERY, variables)

    return result.get("data", {}).get("pastureGrowthRates", [])
