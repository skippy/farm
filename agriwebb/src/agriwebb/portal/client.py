"""Client for AgriWebb's internal event-sourcing API.

This client accesses the loopback-cdn.agriwebb.io API through a Playwright
browser session, which provides the auth context the API requires.
"""

import json
from pathlib import Path

# Playwright MCP profile directory
_PLAYWRIGHT_CACHE = Path.home() / "Library" / "Caches" / "ms-playwright"
PORTAL_URL = "https://portal.agriwebb.com"
LOOPBACK_BASE = "https://loopback-cdn.agriwebb.io/event-sourcing/api/EventSourcingService"


def _find_profile_dir() -> Path:
    """Auto-detect the Playwright MCP browser profile directory.

    The MCP server creates profiles named mcp-chrome-* in the playwright cache.
    Find the most recently modified one (the active session).
    """
    cache = _PLAYWRIGHT_CACHE
    if not cache.exists():
        return cache / "mcp-chrome-profile"  # fallback

    candidates = sorted(
        cache.glob("mcp-chrome-*"),
        key=lambda p: (p / "Default" / "Local Storage").stat().st_mtime
        if (p / "Default" / "Local Storage").exists()
        else 0,
        reverse=True,
    )
    return candidates[0] if candidates else cache / "mcp-chrome-profile"


class PortalClient:
    """Async context manager for the AgriWebb portal API.

    Usage:
        async with PortalClient() as client:
            results = await client.search("death-record", limit=50)
            notes = await client.search("note-record", filter={"animalIds": {"$in": [some_id]}})
    """

    def __init__(self, profile_dir: Path | None = None, farm_id: str | None = None):
        self.profile_dir = profile_dir or _find_profile_dir()
        self.farm_id = farm_id  # loaded from settings if None
        self._playwright = None
        self._browser = None
        self._page = None
        self._token = None

    async def __aenter__(self):
        from playwright.async_api import async_playwright

        if self.farm_id is None:
            from agriwebb.core.config import settings

            self.farm_id = settings.agriwebb_farm_id

        self._playwright = await async_playwright().__aenter__()
        self._browser = await self._playwright.chromium.launch_persistent_context(
            str(self.profile_dir),
            headless=True,
        )
        self._page = self._browser.pages[0] if self._browser.pages else await self._browser.new_page()

        # Navigate to portal to establish Origin context
        await self._page.goto(f"{PORTAL_URL}/f/{self.farm_id}/dashboard", wait_until="domcontentloaded")

        # Read auth token from localStorage
        self._token = await self._page.evaluate(
            "() => { const lr = localStorage.getItem('loginResponse'); return lr ? JSON.parse(lr).id : null; }"
        )
        if not self._token:
            raise RuntimeError(
                "No login token found in browser profile. "
                "Log in via Playwright MCP first: navigate to portal.agriwebb.com and sign in."
            )
        return self

    async def __aexit__(self, *args):
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.__aexit__(*args)

    async def _api_call(self, endpoint: str, body: dict) -> dict:
        """Make an API call to the loopback event-sourcing service."""
        url = f"{LOOPBACK_BASE}/{endpoint}"
        body_json = json.dumps(body)
        result = await self._page.evaluate(
            """async ([url, token, bodyJson]) => {
                const r = await fetch(url, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'Authorization': token
                    },
                    body: bodyJson
                });
                const text = await r.text();
                return { status: r.status, body: text };
            }""",
            [url, self._token, body_json],
        )
        if result["status"] != 200:
            raise RuntimeError(f"Portal API error ({result['status']}): {result['body'][:200]}")
        return json.loads(result["body"])

    async def search(
        self,
        record_type: str,
        *,
        filter: dict | None = None,
        limit: int = 1000,
        skip: int = 0,
    ) -> list[dict]:
        """Search for records of a given type.

        Args:
            record_type: e.g., "death-record", "note-record", "natural-service-record"
            filter: MongoDB-style filter dict (optional)
            limit: Max records to return
            skip: Offset for pagination
        """
        body = {
            "tenantId": self.farm_id,
            "type": {"type": record_type, "version": "v1"},
            "capabilities": {"include-async": True},
            "limit": limit,
            "skip": skip,
        }
        if filter:
            body["filter"] = filter
        result = await self._api_call("search-data-and-count", body)
        return result.get("data", [])

    async def search_with_count(
        self,
        record_type: str,
        *,
        filter: dict | None = None,
        limit: int = 1000,
        skip: int = 0,
    ) -> tuple[list[dict], int]:
        """Search and return (records, total_count)."""
        body = {
            "tenantId": self.farm_id,
            "type": {"type": record_type, "version": "v1"},
            "capabilities": {"include-async": True},
            "limit": limit,
            "skip": skip,
        }
        if filter:
            body["filter"] = filter
        result = await self._api_call("search-data-and-count", body)
        return result.get("data", []), result.get("filterCount", 0)

    async def aggregate(self, aggregate_type: str, subject_id: str | None = None) -> dict:
        """Fetch an aggregate (includes creationDate, lastModifiedDate)."""
        body = {
            "type": {"type": aggregate_type, "version": "v1"},
            "tenantId": self.farm_id,
            "capabilities": {"include-async": True},
        }
        if subject_id:
            body["subjectId"] = subject_id
        return await self._api_call("aggregate", body)

    async def query(self, query_type: str, input_data: dict | None = None) -> dict:
        """Execute a named query."""
        body = {
            "definition": {"type": query_type, "version": "v1"},
            "input": {"tenantId": self.farm_id, **(input_data or {})},
        }
        return await self._api_call("query", body)


# Available record types (discovered via exploration)
RECORD_TYPES = {
    "natural-service-record": "Breeding groups -- ram/ewe assignments, start dates",
    "death-record": "Death details -- fateReason, fateDetails, disposalMethod",
    "note-record": "Free-text notes per animal",
    "ai-record": "Artificial insemination -- donor sire details, straw batch",
    "sale-record": "Sales -- buyer, transport, income",
    "wean-record": "Weaning -- method, pre-weaning group",
    "castrate-record": "Castration -- method",
    "tag-record": "Tag assignments -- VID, color, type",
    "observation-record": "Custom observations",
    "weigh-record": "Weights -- individual values with units",
    "score-record": "Body condition scores",
    "wool-harvest-record": "Wool harvest -- details, income",
    "feed-record": "Feed -- allocation, type, locations",
}
