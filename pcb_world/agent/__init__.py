"""Agent-facing reliability layer (phase 1).

Four pieces, all of them additive — the RL index path, the LLM text projection
and ``PCBWorld.step`` are unchanged:

* :mod:`pcb_world.agent.actions` — versioned structured actions, the human
  routing-mode names (``mark_obstacles`` / ``shove`` / ``walkaround``) mapped
  through the canonical table, and pre-dispatch validation;
* :mod:`pcb_world.agent.state` — authoritative snapshots with a session-bound
  revision/token, canonical nanometre geometry, and a classified outcome;
* :mod:`pcb_world.agent.rules` — proof of which rule context applies, fail closed;
* :mod:`pcb_world.agent.drc_gate` — native-DRC acceptance: no mutation is kept if
  it adds a relevant violation under that context;
* :mod:`pcb_world.agent.session` — :class:`AgentSession`, the transactional
  ``connect_targets`` / ``probe_candidates`` API over one engine.
* :mod:`pcb_world.agent.tool_api` — the JSON tool surface a model can call
  (token required, named modes, structured results).

Typical use::

    from pcb_world.agent import AgentSession, StructuredAction

    session = AgentSession(engine, board_path=str(board_path))
    snap = session.snapshot()
    result = session.connect_targets((x0, y0, 1), (x1, y1, 1), "shove", token=snap.token)
    if result.connected:
        ...
"""

from pcb_world.agent.actions import (
    ActionPhase,
    ActionValidator,
    BoardLimits,
    InvalidActionError,
    MODE_INT_TO_NAME,
    MODE_NAME_TO_INT,
    MODE_NAMES,
    SCHEMA_VERSION,
    SUPPORTED_SCHEMA_VERSIONS,
    StructuredAction,
    canonical_action_names,
    coerce_action,
    mode_to_int,
    mode_to_letter,
)
from pcb_world.agent.drc_gate import (
    CONNECTIVITY_ERROR_CODES,
    CONNECTIVITY_ERROR_TYPES,
    DrcDelta,
    DrcGate,
    ViolationSet,
    is_connectivity_finding,
    take_violations,
    violation_key,
)
from pcb_world.agent.rules import (
    RuleContext,
    RulesUnavailableError,
    assert_rules_applicable,
    engine_rule_status,
    resolve_rule_context,
)
from pcb_world.agent.session import (
    AgentSession,
    CandidateProbe,
    ConnectionResult,
    Endpoint,
    TransactionStep,
    engine_lock,
)
from pcb_world.agent.state import (
    AgentSnapshot,
    Outcome,
    StateProbe,
    StaleStateError,
    allowed_actions,
    canonical_rows,
    geometry_digest,
    nets_changed,
    new_session_id,
    parse_token,
    rows_digest,
    state_fingerprint,
    unverifiable_properties,
)

__all__ = [
    "ActionPhase",
    "ActionValidator",
    "AgentSession",
    "AgentSnapshot",
    "BoardLimits",
    "CandidateProbe",
    "CONNECTIVITY_ERROR_CODES",
    "CONNECTIVITY_ERROR_TYPES",
    "ConnectionResult",
    "DrcDelta",
    "DrcGate",
    "Endpoint",
    "InvalidActionError",
    "MODE_INT_TO_NAME",
    "MODE_NAME_TO_INT",
    "MODE_NAMES",
    "Outcome",
    "RuleContext",
    "RulesUnavailableError",
    "SCHEMA_VERSION",
    "SUPPORTED_SCHEMA_VERSIONS",
    "StaleStateError",
    "StateProbe",
    "StructuredAction",
    "TransactionStep",
    "ViolationSet",
    "allowed_actions",
    "assert_rules_applicable",
    "canonical_action_names",
    "canonical_rows",
    "coerce_action",
    "engine_lock",
    "engine_rule_status",
    "geometry_digest",
    "is_connectivity_finding",
    "mode_to_int",
    "mode_to_letter",
    "nets_changed",
    "new_session_id",
    "parse_token",
    "resolve_rule_context",
    "rows_digest",
    "state_fingerprint",
    "take_violations",
    "unverifiable_properties",
    "violation_key",
]
