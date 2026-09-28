"""Structured JSON tool surface over an :class:`AgentSession`.

This is the model-facing contract: a request is a JSON object, a response is a
JSON object, and nothing needs to be parsed out of prose. It is deliberately
small — one introspection tool, one single-action tool, one transactional tool —
and deliberately strict:

* **a token is required** on every mutating tool (the adapter never constructs a
  token-less session, and it refuses a missing ``token`` field before dispatch);
* ``schema_version`` is required in an action payload and must be supported;
* unknown tool names, unknown fields and malformed values are refused with
  ``ok: false`` and a stable ``reason``, never silently ignored.

The full shape (also emitted by :func:`tool_schemas`) is:

    {"tool": "snapshot"}
    {"tool": "act", "token": "<from snapshot>", "action": {
        "name": "make_line", "schema_version": "1.0",
        "params": {"x_mm": 40.0, "y_mm": 10.0, "mode": "walkaround"}}}
    {"tool": "connect_targets", "token": "<from snapshot>",
     "start": [10.0, 10.0, 1], "target": [40.0, 10.0, 1],
     "mode": "shove", "waypoints": [[25.0, 6.0, 1]], "provisional": false}

See ``tools/reliability/demo_structured_actions.py`` for an executable,
model-free transcript of a full session.
"""

from __future__ import annotations

import json
import math
from typing import Any, Mapping, Sequence

from pcb_world.agent.actions import (
    REASON_UNKNOWN_FIELD,
    InvalidActionError,
    SCHEMA_VERSION,
    coerce_action,
    mode_to_int,
)
from pcb_world.agent.rules import RulesUnavailableError
from pcb_world.agent.session import AgentSession


TOOL_NAMES = ("snapshot", "act", "connect_targets")


def tool_schemas() -> dict[str, Any]:
    """Machine-readable description of the three tools (for a prompt or a schema file)."""
    return {
        "schema_version": SCHEMA_VERSION,
        "tools": [
            {
                "name": "snapshot",
                "description": (
                    "Read the authoritative engine state. Returns the token the "
                    "next mutating call must carry."
                ),
                "input": {"tool": "snapshot"},
                "output": "AgentSnapshot",
            },
            {
                "name": "act",
                "description": (
                    "Validate and run one routing action. Rejected before mutation "
                    "when malformed, out of phase, or when the resulting copper "
                    "fails native DRC acceptance (in which case it is rolled back)."
                ),
                "input": {
                    "tool": "act",
                    "token": "<token from the last snapshot>",
                    "action": {
                        "name": "one of: " + ", ".join(
                            ("net_select", "start_route", "make_line", "make_via", "finish", "net_end")
                        ),
                        "schema_version": SCHEMA_VERSION,
                        "params": {
                            "start_route": {"x_mm": 0.0, "y_mm": 0.0, "layer": 1},
                            "make_line": {"x_mm": 0.0, "y_mm": 0.0, "mode": "walkaround"},
                            "make_via": {"x_mm": 0.0, "y_mm": 0.0, "mode": "shove"},
                            "finish": {"mode": "walkaround"},
                            "net_select": {"net_id": 1},
                            "net_end": {},
                        },
                    },
                },
                "output": "AgentSnapshot",
            },
            {
                "name": "connect_targets",
                "description": (
                    "Connect two copper groups as one transaction. Atomic by "
                    "default: unless the connection verifies and the copper passes "
                    "native DRC acceptance, everything is rolled back."
                ),
                "input": {
                    "tool": "connect_targets",
                    "token": "<token from the last snapshot>",
                    "start": [10.0, 10.0, 1],
                    "target": [40.0, 10.0, 1],
                    "mode": "walkaround | shove | mark_obstacles",
                    "waypoints": [[25.0, 6.0, 1]],
                    "provisional": False,
                },
                "output": "ConnectionResult",
            },
        ],
    }


def _refuse(tool: str | None, reason: str, message: str, **extra: Any) -> dict[str, Any]:
    return {
        "ok": False,
        "tool": tool,
        "reason": reason,
        "message": message,
        **extra,
    }


def _check_fields(request: Mapping[str, Any], allowed: Sequence[str]) -> str | None:
    unknown = sorted(set(request) - set(allowed))
    if unknown:
        return f"unknown field(s) {unknown}; allowed: {sorted(allowed)}"
    return None


def _is_point(value: Any) -> bool:
    return (
        isinstance(value, (list, tuple))
        and len(value) in (2, 3)
        and all(
            isinstance(item, (int, float))
            and not isinstance(item, bool)
            and math.isfinite(float(item))
            for item in value
        )
    )


def _require_token(request: Mapping[str, Any]) -> str | None:
    """The token must be a non-empty string, whatever the session's own policy is.

    The adapter is the model-facing surface: it never inherits a session's
    `require_tokens=False` opt-out, and it never forwards a placeholder.
    """
    token = request.get("token")
    if not isinstance(token, str) or not token.strip():
        return None
    return token


def handle_request(
    session: AgentSession, request: Mapping[str, Any] | str
) -> dict[str, Any]:
    """Run one tool request and return a JSON-shaped response dict.

    Never raises for a bad request: every refusal comes back as
    ``{"ok": false, "reason": ...}`` so a caller can key on the reason. Engine
    faults still propagate from the session as structured outcomes.
    """
    if isinstance(request, str):
        try:
            request = json.loads(request)
        except json.JSONDecodeError as exc:
            return _refuse(None, "malformed_json", f"request is not valid JSON: {exc}")
    if not isinstance(request, Mapping):
        return _refuse(None, "malformed_request", "request must be a JSON object")

    tool = request.get("tool")
    if tool not in TOOL_NAMES:
        return _refuse(
            tool if isinstance(tool, str) else None, "unknown_tool",
            f"unknown tool {tool!r}; known: {list(TOOL_NAMES)}",
        )

    if tool == "snapshot":
        problem = _check_fields(request, ("tool",))
        if problem:
            return _refuse(tool, REASON_UNKNOWN_FIELD, problem)
        snap = session.snapshot()
        return {"ok": True, "tool": tool, "snapshot": snap.to_dict()}

    if tool == "act":
        problem = _check_fields(request, ("tool", "token", "action"))
        if problem:
            return _refuse(tool, REASON_UNKNOWN_FIELD, problem)
        token = _require_token(request)
        if token is None:
            return _refuse(
                tool, "token_required",
                "act requires a non-empty 'token' string from the last snapshot",
            )
        if "action" not in request:
            return _refuse(tool, "missing_action", "act requires an 'action' object")
        try:
            action = coerce_action(request["action"])
        except InvalidActionError as exc:
            return _refuse(tool, exc.reason, str(exc), detail=exc.to_evidence())
        snap = session.act(action, token=token)
        return {
            "ok": snap.outcome.value == "ok",
            "tool": tool,
            "snapshot": snap.to_dict(),
        }

    # connect_targets
    problem = _check_fields(
        request,
        ("tool", "token", "start", "target", "mode", "waypoints", "provisional"),
    )
    if problem:
        return _refuse(tool, REASON_UNKNOWN_FIELD, problem)
    token = _require_token(request)
    if token is None:
        return _refuse(
            tool, "token_required",
            "connect_targets requires a non-empty 'token' string from the last snapshot",
        )
    for field_name in ("start", "target"):
        if field_name not in request:
            return _refuse(tool, "missing_field", f"{field_name} is required")
        if not _is_point(request[field_name]):
            return _refuse(
                tool, "malformed_coordinate",
                f"{field_name} must be a [x, y] or [x, y, layer] array of numbers",
            )

    waypoints = request.get("waypoints", ())
    if waypoints is None:
        waypoints = ()
    elif not isinstance(waypoints, (list, tuple)) or not all(_is_point(p) for p in waypoints):
        return _refuse(
            tool, "malformed_coordinate",
            "waypoints must be an array of [x, y] or [x, y, layer] arrays",
        )

    provisional = request.get("provisional", False)
    if not isinstance(provisional, bool):
        return _refuse(
            tool, "malformed_field",
            f"provisional must be a JSON boolean, got {type(provisional).__name__}",
        )

    mode = request.get("mode", "walkaround")
    if not isinstance(mode, (str, int)) or isinstance(mode, bool):
        return _refuse(
            tool, "unknown_mode", f"mode must be a name/letter/int, got {mode!r}",
        )
    try:
        mode_to_int(mode)
    except InvalidActionError as exc:
        return _refuse(tool, exc.reason, str(exc), detail=exc.to_evidence())

    try:
        result = session.connect_targets(
            request["start"], request["target"], mode,
            waypoints=waypoints,
            token=token,
            provisional=provisional,
        )
    except RulesUnavailableError as exc:            # pragma: no cover - session wraps these
        return _refuse(tool, exc.detail.get("reason", "rules_unavailable"), str(exc),
                       detail=exc.detail)
    # A tool call "succeeded" when the board now satisfies the request: an
    # already-verified connection needs nothing further.
    ok = result.outcome.value in ("ok", "already_connected")
    response = {
        "ok": ok,
        "tool": tool,
        "result": result.to_dict(),
    }
    if not ok:
        # Surface the machine-readable reason at the top level too, so a caller
        # does not have to know where in the result it lives.
        response["reason"] = str(result.evidence.get("reason", result.outcome.value))
    return response
