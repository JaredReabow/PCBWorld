"""The model-facing JSON tool surface: token required, refusals are structured."""

from __future__ import annotations

import json

from pcb_world.agent import AgentSession, Outcome, coerce_action
from pcb_world.agent.tool_api import handle_request, tool_schemas

from tests.agent.fake_engine import FakeEngine, PadInfo


def _session(**kwargs) -> AgentSession:
    engine = FakeEngine(
        clusters={"A": {(10.0, 10.0, 1)}, "B": {(40.0, 10.0, 1)}},
        pads=[PadInfo(10.0, 10.0, net_code=1), PadInfo(40.0, 10.0, net_code=1)],
    )
    return AgentSession(engine, board_path="/tmp/tool_api.kicad_pcb")


def test_schemas_advertise_three_tools_with_a_supported_version():
    schemas = tool_schemas()
    assert schemas["schema_version"] == "1.0"
    assert [t["name"] for t in schemas["tools"]] == ["snapshot", "act", "connect_targets"]


def test_snapshot_tool_returns_a_token_the_next_call_can_use():
    session = _session()
    response = handle_request(session, {"tool": "snapshot"})
    assert response["ok"] is True
    token = response["snapshot"]["token"]
    assert token

    step = handle_request(session, {
        "tool": "act",
        "token": token,
        "action": {"name": "start_route", "schema_version": "1.0",
                   "params": {"x_mm": 10.0, "y_mm": 10.0, "layer": 1}},
    })
    assert step["ok"] is True
    assert step["snapshot"]["route_active"] is True


def test_act_tool_requires_a_token():
    session = _session()
    response = handle_request(session, {
        "tool": "act",
        "action": {"name": "start_route", "schema_version": "1.0",
                   "params": {"x_mm": 10.0, "y_mm": 10.0, "layer": 1}},
    })
    assert response["ok"] is False
    assert response["reason"] == "token_required"


def test_connect_targets_tool_runs_the_transaction():
    session = _session()
    token = handle_request(session, {"tool": "snapshot"})["snapshot"]["token"]
    response = handle_request(session, {
        "tool": "connect_targets", "token": token,
        "start": [10.0, 10.0, 1], "target": [40.0, 10.0, 1], "mode": "walkaround",
    })
    assert response["ok"] is True
    assert response["result"]["connected"] is True
    assert response["result"]["accepted"] is True


def test_connect_targets_tool_refuses_an_empty_endpoint():
    session = _session()
    token = handle_request(session, {"tool": "snapshot"})["snapshot"]["token"]
    response = handle_request(session, {
        "tool": "connect_targets", "token": token,
        "start": [], "target": [40.0, 10.0, 1], "mode": "walkaround",
    })
    assert response["ok"] is False
    assert response["reason"] == "malformed_coordinate"


def test_unknown_tool_and_unknown_fields_are_refused():
    session = _session()
    response = handle_request(session, {"tool": "teleport"})
    assert response["ok"] is False and response["reason"] == "unknown_tool"

    response = handle_request(session, {"tool": "snapshot", "extra": 1})
    assert response["ok"] is False and response["reason"] == "unknown_field"

    token = handle_request(session, {"tool": "snapshot"})["snapshot"]["token"]
    response = handle_request(session, {
        "tool": "connect_targets", "token": token, "start": [10.0, 10.0, 1],
        "target": [40.0, 10.0, 1], "surprise": True,
    })
    assert response["ok"] is False and response["reason"] == "unknown_field"


def test_malformed_json_string_is_refused_not_raised():
    session = _session()
    response = handle_request(session, "{not json")
    assert response["ok"] is False
    assert response["reason"] == "malformed_json"


def test_json_round_trip_of_a_structured_action():
    """The advertised schema must survive a real JSON encode/decode."""
    payload = json.loads(json.dumps({
        "tool": "act",
        "token": "x.r0.y",
        "action": {"name": "make_line", "schema_version": "1.0",
                   "params": {"x_mm": 1.0, "y_mm": 2.0, "mode": "shove"}},
    }))
    action = coerce_action(payload["action"])
    assert action.to_env_action() == {
        "action_type": 3, "x_mm": 1.0, "y_mm": 2.0, "routing_mode": 1,
    }


def test_bad_mode_in_the_tool_request_is_refused_with_a_reason():
    session = _session()
    token = handle_request(session, {"tool": "snapshot"})["snapshot"]["token"]
    response = handle_request(session, {
        "tool": "connect_targets", "token": token, "start": [10.0, 10.0, 1],
        "target": [40.0, 10.0, 1], "mode": "sprint",
    })
    assert response["ok"] is False
    assert response["reason"] == "unknown_mode"


def test_stale_token_through_the_tool_surface_is_refused():
    session = _session()
    first = handle_request(session, {"tool": "snapshot"})["snapshot"]["token"]
    second = handle_request(session, {"tool": "snapshot"})["snapshot"]["token"]
    assert first != second
    response = handle_request(session, {
        "tool": "act", "token": first,
        "action": {"name": "start_route", "schema_version": "1.0",
                   "params": {"x_mm": 10.0, "y_mm": 10.0, "layer": 1}},
    })
    assert response["ok"] is False
    assert response["snapshot"]["outcome"] == Outcome.STALE_STATE.value
