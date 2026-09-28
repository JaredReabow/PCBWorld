"""Tool schema, prompt rendering and request execution for LLM agents.

The tool definitions come from :func:`pcb_world.agent.tool_api.tool_schemas` —
the same source the runner and the demo use — so a model, the CLI and the tests
cannot drift apart. Execution goes through
:func:`pcb_world.agent.tool_api.handle_request`, which enforces a non-empty
token, the structured schema, endpoint identity, the rule gate and native DRC
acceptance before any copper is kept.
"""

from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from pcb_world.agent.actions import INVALID_REASONS
from pcb_world.agent.observations import render_observation
from pcb_world.agent.session import AgentSession
from pcb_world.agent.tool_api import handle_request, tool_schemas


#: Refusal categories a planner may branch on. The first group comes from action
#: validation, the second from the transaction/session layer; both are stable
#: strings rather than prose.
REFUSAL_CATEGORIES: tuple[str, ...] = tuple(INVALID_REASONS) + (
    "token_required",
    "malformed_request",
    "malformed_json",
    "unknown_tool",
    "malformed_field",
    "missing_field",
    "missing_action",
    "drc_unavailable",
    "drc_regression",
    "rules_unavailable",
    "session_quarantined",
    "endpoint_unknown",
    "endpoint_ambiguous",
    "connection_not_verified",
)


def describe_tools() -> dict[str, Any]:
    """The tool schema handed to a model (single source of truth)."""
    return tool_schemas()


def build_connection_prompt(
    observation: Mapping[str, Any],
    *,
    tool_schema: Mapping[str, Any] | None = None,
    refusals: Sequence[str] | None = None,
) -> tuple[str, str]:
    """System + user prompt for one connection decision.

    Kept deliberately small: the schema once, the observation once, and the list
    of refusal categories a reply may encounter. No board dump, no file paths.
    """
    schema = tool_schema or describe_tools()
    categories = list(refusals or REFUSAL_CATEGORIES)
    system = (
        "You are a PCB routing planner. You choose ONE plan for ONE outstanding "
        "connection and reply with a single JSON object, nothing else. Mechanics "
        "are executed by deterministic code; an invalid or unsafe plan is refused "
        "with a machine-readable reason, so prefer a legal, conservative plan. "
        "Coordinates are millimetres, layers are human 1..N, modes are "
        "mark_obstacles|shove|walkaround. Never invent fields."
    )
    user = (
        "Available tools:\n"
        + json.dumps(schema, sort_keys=True, separators=(",", ":"))
        + "\n\nRefusal categories you may see: "
        + ", ".join(sorted(categories))
        + "\n\nConnection observation:\n"
        + render_observation(observation)
        + '\n\nReply with one JSON object, e.g. {"tool":"connect_targets",'
          '"start":[x,y,layer],"target":[x,y,layer],"mode":"walkaround"}.'
    )
    return system, user


def execute_request(session: AgentSession, request: Mapping[str, Any] | str) -> dict[str, Any]:
    """Execute one tool request through the validated surface."""
    return handle_request(session, request)
