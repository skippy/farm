"""Tests for weigh sessions (sheet parsing, resolution, push) and get_weights."""

import json
from datetime import datetime

import httpx
import pytest

from agriwebb.data import livestock, weigh


def animal(aid: str, vid: str | None = None, eid: str | None = None, name: str | None = None, on_farm=True, weighs=()):
    return {
        "animalId": aid,
        "identity": {"vid": vid, "eid": eid, "name": name},
        "state": {"onFarm": on_farm},
        "records": [
            {"recordType": "weigh", "observationDate": ts, "weight": {"value": v, "unit": "lb"}} for ts, v in weighs
        ],
    }


SEP3 = int(datetime(2026, 9, 3, 12).timestamp() * 1000)


# --- parse_sheet ------------------------------------------------------------
def test_parse_sheet_tolerates_handwriting_formats():
    text = """
    # 2026-09-25
    6055: 45 lbs
    6057 56
    2848::103 lbs
    Hearts, 120lb
    # 6077: 71 lbs  crossed out
    6071: 71.5 lbs
    """
    assert weigh.parse_sheet(text) == [
        ("6055", 45.0),
        ("6057", 56.0),
        ("2848", 103.0),
        ("Hearts", 120.0),
        ("6071", 71.5),
    ]


def test_parse_sheet_rejects_unreadable_line():
    with pytest.raises(weigh.SheetError, match="line 2"):
        weigh.parse_sheet("6055 45\n6057 fifty\n")


# --- resolve_sheet ----------------------------------------------------------
def test_resolves_vid_last_four_and_previous_weight():
    lamb = animal("L1", vid="26055", eid="840003329270378", name="Bruno Mars", weighs=[(SEP3, 40)])
    [e] = weigh.resolve_sheet([("6055", 45)], [lamb])
    assert e.ok
    assert e.name == "Bruno Mars"
    assert e.prev_weight == 40
    assert e.gain == 5


def test_eid_last_four_wins_over_vid_last_four():
    hogget = animal("H", eid="840000000002866", name="Hearts")
    lamb = animal("L", vid="22866")
    [e] = weigh.resolve_sheet([("2866", 120)], [hogget, lamb])
    assert e.animal["animalId"] == "H"


def test_prefers_single_on_farm_match_over_off_farm():
    gone = animal("OLD", vid="16055", on_farm=False)
    here = animal("NEW", vid="26055")
    [e] = weigh.resolve_sheet([("6055", 45)], [gone, here])
    assert e.ok and e.animal["animalId"] == "NEW"


@pytest.mark.parametrize(
    ("animals", "warning"),
    [
        ([], "not found"),
        ([animal("A", vid="26055"), animal("B", vid="16055")], "ambiguous"),
        ([animal("A", vid="26055", on_farm=False)], "not on-farm"),
    ],
)
def test_flags_doubtful_matches(animals, warning):
    [e] = weigh.resolve_sheet([("6055", 45)], animals)
    assert not e.ok
    assert warning in e.warnings[0]


def test_flags_same_animal_twice():
    a = animal("A", vid="26055", name="Bruno Mars")
    first, second = weigh.resolve_sheet([("6055", 45), ("Bruno Mars", 46)], [a])
    assert first.ok
    assert not second.ok and "earlier in the sheet" in second.warnings[0]


def test_previous_weight_ignores_other_units():
    a = animal("A", vid="26055", weighs=[(SEP3, 40)])
    a["records"].append({"recordType": "weigh", "observationDate": SEP3 + 1, "weight": {"value": 18, "unit": "kg"}})
    [e] = weigh.resolve_sheet([("6055", 45)], [a], unit="lb")
    assert e.prev_weight == 40


# --- build_weigh_input ------------------------------------------------------
def test_build_weigh_input_shape():
    entries = weigh.resolve_sheet([("6055", 45)], [animal("A", vid="26055")])
    when = datetime(2026, 9, 25, 12)
    assert weigh.build_weigh_input(entries, when) == {
        "observationDate": int(when.timestamp() * 1000),
        "weighEvent": "Check",
        "animals": [{"animalIdentity": {"animalId": "A"}, "weight": {"value": 45, "unit": "lb"}}],
    }


def test_build_weigh_input_refuses_flagged_entries():
    entries = weigh.resolve_sheet([("6055", 45)], [])
    with pytest.raises(ValueError, match="6055"):
        weigh.build_weigh_input(entries, datetime(2026, 9, 25))


@pytest.mark.parametrize(("kwargs", "match"), [({"unit": "stone"}, "unit"), ({"weigh_event": "Nope"}, "weigh_event")])
def test_build_weigh_input_validates_vocab(kwargs, match):
    entries = weigh.resolve_sheet([("6055", 45)], [animal("A", vid="26055")])
    with pytest.raises(ValueError, match=match):
        weigh.build_weigh_input(entries, datetime(2026, 9, 25), **kwargs)


# --- API calls --------------------------------------------------------------
async def test_push_returns_session_id(mock_agriwebb):
    route = mock_agriwebb.post("/v2").mock(
        return_value=httpx.Response(200, json={"data": {"addRecords": {"sessionId": "S1", "errors": []}}})
    )
    assert await weigh.push_weigh_session({"animals": []}) == "S1"
    body = json.loads(route.calls[0].request.content)
    assert body["variables"]["input"]["records"] == [{"weigh": {"animals": []}}]


async def test_push_raises_on_record_errors(mock_agriwebb):
    errors = [{"recordType": "weigh", "errorCode": "X", "errorMessage": "bad animal", "animalIdentity": None}]
    mock_agriwebb.post("/v2").mock(
        return_value=httpx.Response(200, json={"data": {"addRecords": {"sessionId": "S1", "errors": errors}}})
    )
    with pytest.raises(RuntimeError, match="bad animal"):
        await weigh.push_weigh_session({"animals": []})


async def test_push_does_not_retry(mock_agriwebb):
    route = mock_agriwebb.post("/v2").mock(return_value=httpx.Response(503))
    with pytest.raises(httpx.HTTPStatusError):
        await weigh.push_weigh_session({"animals": []})
    assert route.call_count == 1


async def test_get_weights_requires_animal_or_session():
    with pytest.raises(ValueError):
        await livestock.get_weights()


async def test_get_weights_filters_by_type_and_date(mock_agriwebb):
    recs = [
        {"recordId": "r2", "observationDate": 2, "sessionId": "S", "animalId": "A", "weight": {"value": 50}},
        {"recordId": "r1", "observationDate": 1, "sessionId": "S", "animalId": "A", "weight": {"value": 45}},
    ]
    route = mock_agriwebb.post("/v2").mock(return_value=httpx.Response(200, json={"data": {"records": recs}}))
    start, end = datetime(2026, 9, 25), datetime(2026, 9, 26)

    result = await livestock.get_weights(animal_id="A", start=start, end=end)

    assert [r["recordId"] for r in result] == ["r1", "r2"]
    options = json.loads(route.calls[0].request.content)["variables"]["options"]
    assert options["animalId"] == "A"
    assert options["filter"] == {
        "recordType": {"_eq": "weigh"},
        "observationDate": {"_gte": int(start.timestamp() * 1000), "_lt": int(end.timestamp() * 1000)},
    }
