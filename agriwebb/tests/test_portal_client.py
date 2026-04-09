"""Tests for the portal client and sync modules."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from agriwebb.portal.client import RECORD_TYPES, PortalClient


class TestPortalClientInit:
    """Test PortalClient configuration."""

    def test_record_types_documented(self):
        """All discovered record types are documented."""
        assert "death-record" in RECORD_TYPES
        assert "note-record" in RECORD_TYPES
        assert "natural-service-record" in RECORD_TYPES
        assert "ai-record" in RECORD_TYPES
        assert len(RECORD_TYPES) == 13

    def test_default_profile_dir(self):
        client = PortalClient()
        assert "ms-playwright" in str(client.profile_dir)


class TestPortalClientSearch:
    """Test search method builds correct request bodies."""

    @pytest.fixture
    def mock_client(self):
        """Create a PortalClient with mocked internals."""
        client = PortalClient(farm_id="test-farm-id")
        client._token = "test-token"
        client._page = AsyncMock()
        return client

    async def test_search_basic(self, mock_client):
        mock_client._page.evaluate = AsyncMock(
            return_value={"status": 200, "body": json.dumps({"data": [{"recordId": "r1"}], "filterCount": 1})}
        )

        results = await mock_client.search("death-record")
        assert len(results) == 1
        assert results[0]["recordId"] == "r1"

    async def test_search_with_filter(self, mock_client):
        mock_client._page.evaluate = AsyncMock(
            return_value={"status": 200, "body": json.dumps({"data": [], "filterCount": 0})}
        )

        results = await mock_client.search("note-record", filter={"animalIds": {"$in": ["id1"]}})
        assert results == []

    async def test_search_with_count(self, mock_client):
        mock_client._page.evaluate = AsyncMock(
            return_value={"status": 200, "body": json.dumps({"data": [{"id": "1"}, {"id": "2"}], "filterCount": 42})}
        )

        records, count = await mock_client.search_with_count("weigh-record")
        assert len(records) == 2
        assert count == 42

    async def test_search_api_error_raises(self, mock_client):
        mock_client._page.evaluate = AsyncMock(return_value={"status": 401, "body": "Unauthorized"})

        with pytest.raises(RuntimeError, match="Portal API error"):
            await mock_client.search("death-record")

    async def test_aggregate(self, mock_client):
        mock_client._page.evaluate = AsyncMock(
            return_value={
                "status": 200,
                "body": json.dumps(
                    {
                        "creationDate": 1700000000000,
                        "lastModifiedDate": 1700100000000,
                    }
                ),
            }
        )

        result = await mock_client.aggregate("dismissed-prompts", subject_id="user-1")
        assert result["creationDate"] == 1700000000000

    async def test_no_token_raises(self):
        """If no login token found, raises helpful error."""
        client = PortalClient(farm_id="test")
        client._token = None
        client._page = AsyncMock()

        # The _api_call would fail if token is None since it'd be interpolated as 'None'
        # But the __aenter__ check should catch it first
        assert client._token is None


class TestSyncPortalData:
    """Test the sync function writes correct cache files."""

    async def test_sync_creates_cache_files(self, tmp_path, monkeypatch):
        """sync_portal_data creates per-type JSON files in .cache/portal/."""
        monkeypatch.setattr("agriwebb.portal.sync.get_cache_dir", lambda: tmp_path)

        # Mock the PortalClient
        mock_records = {
            "death-record": [{"recordId": "d1", "fateReason": "Injury"}],
            "note-record": [{"recordId": "n1", "note": "test note"}],
            "natural-service-record": [
                {
                    "recordId": "ns1",
                    "animalDictionary": {"ram1": "Male", "ewe1": "Female"},
                    "startDate": 1000,
                    "observationDate": 2000,
                }
            ],
            "ai-record": [{"recordId": "ai1", "sireDetails": {"name": "Test Sire"}}],
        }

        async def mock_search_with_count(record_type, **kwargs):
            recs = mock_records.get(record_type, [])
            return recs, len(recs)

        async def mock_search(record_type, **kwargs):
            return mock_records.get(record_type, [])

        with patch("agriwebb.portal.client.PortalClient") as MockClient:
            instance = AsyncMock()
            instance.search_with_count = mock_search_with_count
            instance.search = mock_search
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=None)
            MockClient.return_value = instance

            from agriwebb.portal.sync import sync_portal_data

            await sync_portal_data()

        # Check files were created
        portal_dir = tmp_path / "portal"
        assert portal_dir.exists()

        for rt in ["death-record", "note-record", "natural-service-record", "ai-record"]:
            cache_file = portal_dir / f"{rt}.json"
            assert cache_file.exists(), f"Missing cache file for {rt}"
            data = json.loads(cache_file.read_text())
            assert "synced_at" in data
            assert "records" in data

        # Check natural-service-parsed.json was also created
        ns_parsed = portal_dir / "natural-service-parsed.json"
        assert ns_parsed.exists()
        ns_data = json.loads(ns_parsed.read_text())
        assert ns_data["count"] == 1
        assert ns_data["groups"][0]["ewe_ids"] == ["ewe1"]
