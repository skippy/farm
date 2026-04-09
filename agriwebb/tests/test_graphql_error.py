"""Tests for GraphQLError and AgriWebbAPIError.

Verifies that GraphQLError redacts sensitive content from query strings
(UUIDs replaced with [REDACTED], truncated to 50 chars).
"""

import pytest

from agriwebb.core.client import (
    AgriWebbAPIError,
    ExternalAPIError,
    GraphQLError,
    RetryableError,
)

# =============================================================================
# GraphQLError
# =============================================================================


class TestGraphQLErrorInit:
    """Characterize GraphQLError construction and attribute storage."""

    def test_stores_errors_list(self):
        errors = [{"message": "Field not found"}]
        exc = GraphQLError(errors)
        assert exc.errors is errors
        assert exc.errors == [{"message": "Field not found"}]

    def test_short_query_without_uuids_stored_as_is(self):
        query = "{ farms { id name } }"
        exc = GraphQLError([{"message": "error"}], query=query)
        # Short query with no UUIDs is stored unchanged
        assert exc.query == query

    def test_query_defaults_to_none(self):
        exc = GraphQLError([{"message": "error"}])
        assert exc.query is None

    def test_long_query_is_truncated(self):
        query = "query GetFields($farmId: String!) { fields(filter: { farmId: { _eq: $farmId } }) { id name totalArea } }"
        exc = GraphQLError([{"message": "error"}], query=query)
        assert len(exc.query) == 53  # 50 chars + "..."
        assert exc.query.endswith("...")

    def test_uuid_in_query_is_redacted(self):
        query = 'farmId: "550e8400-e29b-41d4-a716-446655440000"'
        exc = GraphQLError([{"message": "error"}], query=query)
        assert "550e8400" not in exc.query
        assert "[REDACTED]" in exc.query

    def test_stores_multiple_errors(self):
        errors = [
            {"message": "Error 1", "path": ["farms"]},
            {"message": "Error 2", "extensions": {"code": "FORBIDDEN"}},
        ]
        exc = GraphQLError(errors)
        assert len(exc.errors) == 2
        assert exc.errors[0]["message"] == "Error 1"
        assert exc.errors[1]["extensions"]["code"] == "FORBIDDEN"


class TestGraphQLErrorQueryRedaction:
    """Verify that queries with sensitive data are redacted."""

    def test_query_with_embedded_uuid_farm_id_is_redacted(self):
        """When f-string interpolation embeds a UUID farm ID, it's redacted."""
        farm_id = "550e8400-e29b-41d4-a716-446655440000"
        query = f'farmId: "{farm_id}" name: "test"'
        exc = GraphQLError([{"message": "update failed"}], query=query)

        # The raw farm ID must NOT be accessible
        assert farm_id not in exc.query
        assert "550e8400" not in exc.query
        assert "[REDACTED]" in exc.query

    def test_long_query_is_truncated(self):
        """Long queries are truncated to 50 chars + '...'."""
        query = "query GetFields($farmId: String!) { fields(filter: { farmId: { _eq: $farmId } }) { id name totalArea grazableArea } }"
        exc = GraphQLError([{"message": "error"}], query=query)

        assert len(exc.query) <= 53
        assert exc.query.endswith("...")

    def test_query_with_variables_placeholder_is_safe(self):
        """Parameterized queries don't contain farm IDs in the query string."""
        query = "{ fields($farmId: String!) { id } }"
        exc = GraphQLError([{"message": "error"}], query=query)

        # No real ID in the query - just the variable placeholder
        assert "$farmId" in exc.query
        assert "abc123" not in exc.query

    def test_multiple_uuids_all_redacted(self):
        """All UUID patterns in a query are replaced."""
        query = 'farmId: "550e8400-e29b-41d4-a716-446655440000" id: "660e8400-e29b-41d4-a716-446655440001"'
        exc = GraphQLError([{"message": "error"}], query=query)

        assert "550e8400" not in exc.query
        assert "660e8400" not in exc.query


class TestGraphQLErrorStr:
    """Characterize str() representation."""

    def test_str_contains_error_message(self):
        exc = GraphQLError([{"message": "Field 'foo' not found"}])
        result = str(exc)
        assert "Field 'foo' not found" in result

    def test_str_contains_graphql_errors_prefix(self):
        exc = GraphQLError([{"message": "error"}])
        result = str(exc)
        assert "GraphQL errors:" in result

    def test_str_joins_multiple_messages_with_semicolons(self):
        errors = [{"message": "Error A"}, {"message": "Error B"}]
        exc = GraphQLError(errors)
        result = str(exc)
        assert "Error A" in result
        assert "Error B" in result
        assert ";" in result

    def test_str_handles_error_without_message_key(self):
        """Errors missing 'message' key fall back to str(error_dict)."""
        errors = [{"code": "UNAUTHENTICATED"}]
        exc = GraphQLError(errors)
        result = str(exc)
        assert "UNAUTHENTICATED" in result

    def test_is_exception_subclass(self):
        exc = GraphQLError([{"message": "test"}])
        assert isinstance(exc, Exception)

    def test_can_be_raised_and_caught(self):
        err = GraphQLError([{"message": "boom"}], query="{ test }")
        with pytest.raises(GraphQLError):
            raise err
        # Short query without UUIDs is stored as-is (under 50 chars)
        assert err.query == "{ test }"
        assert err.errors == [{"message": "boom"}]


# =============================================================================
# AgriWebbAPIError
# =============================================================================


class TestAgriWebbAPIError:
    """Characterize AgriWebbAPIError behavior."""

    def test_is_exception_subclass(self):
        exc = AgriWebbAPIError("something went wrong")
        assert isinstance(exc, Exception)

    def test_str_contains_message(self):
        exc = AgriWebbAPIError("HTTP 401: Unauthorized")
        assert str(exc) == "HTTP 401: Unauthorized"

    def test_can_be_raised_and_caught(self):
        with pytest.raises(AgriWebbAPIError, match="bad request"):
            raise AgriWebbAPIError("bad request")

    def test_distinct_from_graphql_error(self):
        """AgriWebbAPIError and GraphQLError are separate hierarchies."""
        api_err = AgriWebbAPIError("api error")
        gql_err = GraphQLError([{"message": "gql error"}])
        assert not isinstance(api_err, GraphQLError)
        assert not isinstance(gql_err, AgriWebbAPIError)


# =============================================================================
# RetryableError
# =============================================================================


class TestRetryableError:
    """Characterize RetryableError behavior."""

    def test_is_exception_subclass(self):
        exc = RetryableError("timeout")
        assert isinstance(exc, Exception)

    def test_str_contains_message(self):
        exc = RetryableError("Request timed out")
        assert str(exc) == "Request timed out"

    def test_distinct_from_api_errors(self):
        exc = RetryableError("transient")
        assert not isinstance(exc, AgriWebbAPIError)
        assert not isinstance(exc, GraphQLError)


# =============================================================================
# ExternalAPIError
# =============================================================================


class TestExternalAPIError:
    """Characterize ExternalAPIError behavior."""

    def test_is_exception_subclass(self):
        exc = ExternalAPIError("Open-Meteo down")
        assert isinstance(exc, Exception)

    def test_str_contains_message(self):
        exc = ExternalAPIError("HTTP 503: Service Unavailable")
        assert str(exc) == "HTTP 503: Service Unavailable"

    def test_distinct_from_agriwebb_error(self):
        exc = ExternalAPIError("external")
        assert not isinstance(exc, AgriWebbAPIError)
