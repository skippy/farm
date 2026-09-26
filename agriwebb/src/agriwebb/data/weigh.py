"""Weigh sessions: resolve (tag, weight) pairs and push them to AgriWebb.

There is no sheet format or CLI - weigh sheets are rare and handwritten, so
Claude transcribes them and calls these helpers directly:

    entries = resolve_weights([("6055", 45), ...], animals)   # flag doubtful tags
    format_preview(entries)                                   # show prev weight / gain
    await find_existing_weighs([e.animal_id for e in entries], observed_at)  # must be empty
    session = await push_weigh_session(build_weigh_input(entries, observed_at))
    await get_weights(session_id=session)                     # confirm the count

Tags are whatever was written in the yard - last 4 of the EID, a VID, last 4
of the VID, or a name - matched with the WADDL resolver's matcher. Anything
unresolved, ambiguous, off-farm, or repeated blocks the push: AgriWebb's API
cannot delete records, so a wrong weight has to be fixed by hand in the portal.

The push is one ``addRecords`` call (one AgriWebb session) and deliberately does
**not** retry: a timeout after the server committed would duplicate every record.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from agriwebb.analysis.lambing.loader import is_on_farm
from agriwebb.core import settings
from agriwebb.core.client import graphql
from agriwebb.waddl.resolver import match_candidates

WEIGH_EVENTS = (
    "Check", "Wean", "ProgenyBirth", "Birthing", "Purchase", "Sale", "FarmExit", "Death",
    "PostWeaning", "Yearling", "Mature", "Rear", "Mark", "Induction", "Day200", "Day400", "Day600",
)  # fmt: skip
WEIGHT_UNITS = ("lb", "kg")

ADD_RECORDS_MUTATION = """
mutation AddRecords($input: AddRecordsInput!) {
  addRecords(input: $input) {
    sessionId
    errors { recordType errorCode errorMessage animalIdentity { animalId vid eid } }
  }
}
"""


@dataclass
class WeighEntry:
    """One (tag, weight) pair, resolved against the cache."""

    token: str
    weight: float
    animal: dict | None = None
    warnings: list[str] = field(default_factory=list)
    prev_weight: float | None = None
    prev_date: datetime | None = None

    @property
    def name(self) -> str:
        ident = (self.animal or {}).get("identity") or {}
        return ident.get("name") or ident.get("vid") or ident.get("eid") or "?"

    @property
    def animal_id(self) -> str:
        if self.animal is None:
            raise ValueError(f"tag {self.token} is not resolved to an animal")
        return self.animal["animalId"]

    @property
    def gain(self) -> float | None:
        return None if self.prev_weight is None else self.weight - self.prev_weight

    @property
    def ok(self) -> bool:
        return self.animal is not None and not self.warnings


def _last_weigh(animal: dict, unit: str) -> tuple[float | None, datetime | None]:
    weighs = [
        r
        for r in animal.get("records") or []
        if r.get("recordType") == "weigh" and (r.get("weight") or {}).get("unit") == unit
    ]
    if not weighs:
        return None, None
    last = max(weighs, key=lambda r: r["observationDate"])
    return last["weight"]["value"], datetime.fromtimestamp(last["observationDate"] / 1000)


def resolve_weights(rows: list[tuple[str, float]], animals: list[dict], unit: str = "lb") -> list[WeighEntry]:
    """Match each (tag, weight) pair to exactly one on-farm animal, flagging anything doubtful."""
    entries: list[WeighEntry] = []
    seen: dict[str, str] = {}
    for token, weight in rows:
        entry = WeighEntry(token=token, weight=weight)
        candidates, how = match_candidates(token, animals)
        on_farm = [a for a in candidates if is_on_farm(a)]
        if not candidates:
            entry.warnings.append("not found in cache (refresh cache, or use full VID/name)")
        elif len(on_farm) > 1:
            entry.warnings.append(f"ambiguous - {len(on_farm)} on-farm animals share this {how}")
        elif not on_farm:
            entry.animal = candidates[0]
            entry.warnings.append("matched animal is not on-farm (sold/dead?)")
        else:
            entry.animal = on_farm[0]

        if entry.animal:
            aid = entry.animal["animalId"]
            if aid in seen:
                entry.warnings.append(f"same animal as tag {seen[aid]} earlier in this session")
            seen.setdefault(aid, token)
            entry.prev_weight, entry.prev_date = _last_weigh(entry.animal, unit)
        entries.append(entry)
    return entries


def build_weigh_input(
    entries: list[WeighEntry], observed_at: datetime, unit: str = "lb", weigh_event: str = "Check"
) -> dict:
    """Build the ``WeighRecordInput`` for one session."""
    if unit not in WEIGHT_UNITS:
        raise ValueError(f"unit must be one of {WEIGHT_UNITS}")
    if weigh_event not in WEIGH_EVENTS:
        raise ValueError(f"weigh_event must be one of {WEIGH_EVENTS}")
    if bad := [e.token for e in entries if not e.ok]:
        raise ValueError(f"unresolved or flagged entries: {', '.join(bad)}")
    return {
        "observationDate": int(observed_at.timestamp() * 1000),
        "weighEvent": weigh_event,
        "animals": [
            {"animalIdentity": {"animalId": e.animal_id}, "weight": {"value": e.weight, "unit": unit}} for e in entries
        ],
    }


async def find_existing_weighs(animal_ids: list[str], day: datetime) -> dict[str, list[dict]]:
    """Live-check AgriWebb for weigh records already on *day* (local date), per animal."""
    from agriwebb.data.livestock import get_weights

    start = day.replace(hour=0, minute=0, second=0, microsecond=0)
    sem = asyncio.Semaphore(5)

    async def one(aid: str) -> tuple[str, list[dict]]:
        async with sem:
            return aid, await get_weights(animal_id=aid, start=start, end=start + timedelta(days=1))

    results = await asyncio.gather(*(one(a) for a in animal_ids))
    return {aid: recs for aid, recs in results if recs}


async def push_weigh_session(weigh_input: dict) -> str:
    """Send one weigh session to AgriWebb and return its sessionId.

    Single attempt, no retry - see module docstring.
    """
    variables = {"input": {"farmId": settings.agriwebb_farm_id, "records": [{"weigh": weigh_input}]}}
    result = await graphql(ADD_RECORDS_MUTATION, variables)
    payload = result["data"]["addRecords"]
    if payload["errors"]:
        details = "; ".join(f"{e.get('animalIdentity')}: {e.get('errorMessage')}" for e in payload["errors"])
        raise RuntimeError(
            f"AgriWebb rejected {len(payload['errors'])} record(s) (session {payload['sessionId']}): {details}"
        )
    return payload["sessionId"]


def format_preview(entries: list[WeighEntry]) -> str:
    """Human-readable table: tag, animal, weight, previous weight, gain, warnings."""
    lines = [f"{'tag':<10} {'animal':<18} {'weight':>6} {'prev':>6} {'on':<10} {'gain':>5}  notes"]
    for e in entries:
        prev = f"{e.prev_weight:g}" if e.prev_weight is not None else "-"
        on = f"{e.prev_date:%Y-%m-%d}" if e.prev_date else "-"
        gain = f"{e.gain:+g}" if e.gain is not None else "-"
        name = e.name if e.animal else "-"
        lines.append(f"{e.token:<10} {name:<18} {e.weight:>6g} {prev:>6} {on:<10} {gain:>5}  {'; '.join(e.warnings)}")
    return "\n".join(lines)
