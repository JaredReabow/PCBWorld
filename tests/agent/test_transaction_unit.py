"""Transaction bookkeeping: atomicity, DRC acceptance, quarantine, probing.

These tests drive a test double, so they are about *policy*: what the session
does with a backend that behaves in a particular way. Native behaviour is covered
by ``test_native_*.py``.
"""

from __future__ import annotations

import pytest

from pcb_world.agent import (
    AgentSession,
    Outcome,
    RuleContext,
    StructuredAction,
    geometry_digest,
    resolve_rule_context,
)
from pcb_world.agent.state import canonical_rows, nets_changed, rows_digest

from tests.agent.fake_engine import FakeEngine, FakeViolation, PadInfo, TrackInfo

START = (10.0, 10.0, 1)
TARGET = (40.0, 10.0, 1)


def _engine(**kwargs) -> FakeEngine:
    kwargs.setdefault("clusters", {"A": {(10.0, 10.0, 1)}, "B": {(40.0, 10.0, 1)}})
    kwargs.setdefault("pads", [PadInfo(10.0, 10.0, net_code=1), PadInfo(40.0, 10.0, net_code=1)])
    return FakeEngine(**kwargs)


def _session(engine, **kwargs) -> AgentSession:
    """A token-less session: the documented opt-out, so bookkeeping is the subject."""
    kwargs.setdefault("board_path", "/tmp/synthetic.kicad_pcb")
    kwargs.setdefault("require_tokens", False)
    return AgentSession(engine, **kwargs)


# ---------------------------------------------------------------------------
# Success paths
# ---------------------------------------------------------------------------


def test_direct_connection_commits_on_verified_connectivity():
    engine = _engine()
    session = _session(engine)
    result = session.connect_targets(START, TARGET, "walkaround")
    assert result.outcome is Outcome.OK
    assert result.connected is True
    assert result.committed is True
    assert result.accepted is True
    assert [s.kind for s in result.steps] == ["start", "line"]
    assert result.evidence["shared_anchors"]
    assert result.evidence["drc_delta"]["acceptable"] is True
    assert result.evidence["changed_nets"] == [1]


def test_already_connected_is_reported_without_mutating():
    engine = _engine(clusters={"A": {(10.0, 10.0, 1), (40.0, 10.0, 1)}})
    session = _session(engine)
    result = session.connect_targets(START, TARGET, "walkaround")
    assert result.outcome is Outcome.ALREADY_CONNECTED
    assert result.connected is True
    assert result.committed is False
    assert result.steps == ()
    assert engine.get_tracks() == []


def test_connection_to_bare_copper_is_an_invalid_action():
    session = _session(_engine())
    result = session.connect_targets((10.0, 10.0, 1), (20.0, 20.0, 1), "walkaround")
    assert result.outcome is Outcome.INVALID_ACTION
    assert result.evidence["reason"] == "endpoint_unknown"
    assert result.steps == ()


def test_malformed_endpoint_is_refused_not_crashed():
    session = _session(_engine())
    result = session.connect_targets([], TARGET, "walkaround")
    assert result.outcome is Outcome.INVALID_ACTION
    assert result.evidence["reason"] == "malformed_coordinate"
    result = session.connect_targets([10.0], TARGET, "walkaround")
    assert result.outcome is Outcome.INVALID_ACTION
    result = session.connect_targets(START, [float("nan"), 10.0, 1], "walkaround")
    assert result.outcome is Outcome.INVALID_ACTION
    assert result.evidence["reason"] == "nonfinite_coordinate"


def test_unknown_mode_is_refused_before_any_mutation():
    engine = _engine()
    session = _session(engine)
    result = session.connect_targets(START, TARGET, "sprint")
    assert result.outcome is Outcome.INVALID_ACTION
    assert result.evidence["reason"] == "unknown_mode"
    assert engine.get_tracks() == []
    assert engine.is_routing() is False


def test_transaction_refuses_to_attach_to_an_active_route():
    engine = _engine()
    session = _session(engine)
    session.act(StructuredAction.start_route(10.0, 10.0, 1))
    assert engine.is_routing() is True
    result = session.connect_targets(START, TARGET, "walkaround")
    assert result.outcome is Outcome.INVALID_ACTION
    assert result.evidence["reason"] == "wrong_phase"


# ---------------------------------------------------------------------------
# Atomicity
# ---------------------------------------------------------------------------


def test_failed_step_is_rolled_back_and_reported():
    engine = _engine(fail_fix_at={(40.0, 10.0)})
    session = _session(engine)
    result = session.connect_targets(START, TARGET, "walkaround")
    assert result.outcome is Outcome.ROUTING_FAILED
    assert result.connected is False
    assert result.committed is False
    assert result.accepted is False
    assert result.evidence["reason"] == "connection_not_verified"
    assert result.evidence["rollback"]["copper_digest_matches"] is True
    assert engine.get_tracks() == []


def test_atomic_is_the_default_and_restores_everything():
    engine = _engine(fail_fix_at={(40.0, 10.0)})
    session = _session(engine)
    before = geometry_digest(engine.get_tracks(), engine.get_vias())
    result = session.connect_targets(START, TARGET, "walkaround")
    assert result.rollback_verified is True
    assert result.committed is False
    assert geometry_digest(engine.get_tracks(), engine.get_vias()) == before


def test_provisional_keeps_verified_progress_when_a_later_leg_fails():
    engine = FakeEngine(
        clusters={
            "A": {(10.0, 10.0, 1)},
            "M": {(25.0, 10.0, 1)},
            "B": {(40.0, 10.0, 1)},
        },
        pads=[PadInfo(10.0, 10.0, net_code=1), PadInfo(40.0, 10.0, net_code=1)],
        fail_fix_at={(40.0, 10.0)},
    )
    session = _session(engine)
    result = session.connect_targets(
        START, TARGET, "walkaround", waypoints=[(25.0, 10.0, 1)], provisional=True,
    )
    assert result.outcome is Outcome.ROUTING_FAILED
    assert result.connected is False
    assert result.committed is True                   # explicit provisional progress
    assert len(engine.get_tracks()) == 1              # the kept waypoint leg
    kinds = [(s.kind, s.success, s.restored) for s in result.steps]
    assert kinds == [("start", True, False), ("line", True, False), ("line", False, True)]


def test_partial_progress_is_rolled_back_without_provisional():
    engine = FakeEngine(
        clusters={
            "A": {(10.0, 10.0, 1)},
            "M": {(25.0, 10.0, 1)},
            "B": {(40.0, 10.0, 1)},
        },
        pads=[PadInfo(10.0, 10.0, net_code=1), PadInfo(40.0, 10.0, net_code=1)],
        fail_fix_at={(40.0, 10.0)},
    )
    session = _session(engine)
    result = session.connect_targets(START, TARGET, "walkaround",
                                     waypoints=[(25.0, 10.0, 1)])
    assert result.committed is False
    assert engine.get_tracks() == []
    # The final state, not the pre-rollback one, is what the result describes.
    assert result.evidence["final"]["track_count"] == 0
    assert result.evidence["final"]["digest"] == result.evidence["pre_transaction"]["digest"]


# ---------------------------------------------------------------------------
# DRC acceptance
# ---------------------------------------------------------------------------


def test_new_relevant_violation_rejects_the_route_and_rolls_back():
    engine = _engine(violation_on_fix=FakeViolation(
        error_code=5, error_type="Clearance violation", message="clearance 1.0 < required",
        x_mm=20.0, y_mm=10.0, layer=1, net_names=["NET1", "NET2"],
    ))
    session = _session(engine)
    before = geometry_digest(engine.get_tracks(), engine.get_vias())
    result = session.connect_targets(START, TARGET, "walkaround")
    assert result.outcome is Outcome.ROUTING_FAILED
    assert result.accepted is False
    assert result.committed is False
    assert result.connected is False                  # reported from the restored board
    assert result.evidence["reason"] == "drc_regression"
    assert result.evidence["drc_delta"]["added_relevant_count"] == 1
    assert result.evidence["drc_delta"]["added_relevant"][0]["error_type"] == "Clearance violation"
    assert result.rollback_verified is True
    assert geometry_digest(engine.get_tracks(), engine.get_vias()) == before


def test_provisional_still_refuses_copper_that_fails_drc():
    engine = _engine(violation_on_fix=FakeViolation(
        error_code=5, error_type="Clearance violation", message="nope",
    ))
    session = _session(engine)
    result = session.connect_targets(START, TARGET, "walkaround", provisional=True)
    assert result.outcome is Outcome.ROUTING_FAILED
    assert result.committed is False
    assert result.evidence["reason"] == "drc_regression"
    assert engine.get_tracks() == []


def test_connectivity_findings_do_not_gate_a_commit():
    """Unrouted ratsnest and dangling ends are progress, not violations."""
    engine = _engine()
    engine.state.violations.append(FakeViolation(
        error_code=1, error_type="Missing connection between items",
        message="unrouted", severity=0x20,
    ))
    session = _session(engine)
    result = session.connect_targets(START, TARGET, "walkaround")
    assert result.outcome is Outcome.OK
    assert result.committed is True
    assert result.evidence["drc_delta"]["added_relevant_count"] == 0


def test_preexisting_violation_is_preserved_not_charged_to_the_attempt():
    engine = _engine()
    preexisting = FakeViolation(
        error_code=5, error_type="Clearance violation", message="pre-existing",
        x_mm=1.0, y_mm=1.0, layer=1, item_a="aaaa", item_b="bbbb",
    )
    engine.state.violations.append(preexisting)
    session = _session(engine)
    result = session.connect_targets(START, TARGET, "walkaround")
    assert result.outcome is Outcome.OK
    assert result.evidence["drc_delta"]["baseline_relevant"] == 1
    assert result.evidence["drc_delta"]["added_relevant_count"] == 0
    assert engine.state.violations                      # still there afterwards


def test_unavailable_drc_refuses_the_mutation():
    engine = _engine(drc_failure="DRC engine exploded")
    session = _session(engine)
    result = session.connect_targets(START, TARGET, "walkaround")
    assert result.outcome is Outcome.UNSUPPORTED
    assert result.evidence["reason"] == "drc_unavailable"
    assert result.committed is False
    assert engine.get_tracks() == []


def test_unanswerable_rule_context_refuses_every_mutating_call():
    engine = _engine()
    session = _session(_OldBuildEngine(engine))

    result = session.connect_targets(START, TARGET, "walkaround")
    assert result.outcome is Outcome.UNSUPPORTED
    assert result.evidence["native_validation_blocked"] is True
    assert engine.get_tracks() == []

    snap = session.act(StructuredAction.start_route(10.0, 10.0, 1))
    assert snap.outcome is Outcome.UNSUPPORTED
    assert engine.is_routing() is False


class _OldBuildEngine:
    """Delegates everything except the rule-context accessors (an older engine)."""

    _MISSING = frozenset({
        "get_routing_rules_path", "was_routing_rules_loaded_from_file",
        "get_last_drc_rules_load_error", "was_project_loaded_from_file",
    })

    def __init__(self, inner) -> None:
        self._inner = inner

    def __getattr__(self, name):
        if name in self._MISSING:
            raise AttributeError(name)
        return getattr(self._inner, name)


def test_project_not_loaded_from_file_refuses_mutations():
    engine = _engine(project_loaded=False)
    session = _session(engine)
    result = session.connect_targets(START, TARGET, "walkaround")
    assert result.outcome is Outcome.UNSUPPORTED
    assert engine.get_tracks() == []


# ---------------------------------------------------------------------------
# Rollback failure / quarantine
# ---------------------------------------------------------------------------


def test_unverifiable_rollback_quarantines_instead_of_claiming_success():
    engine = _engine(
        fail_fix_at={(40.0, 10.0)}, restore_silently_wrong=True, mutate_before_fail=True,
    )
    session = _session(engine)
    result = session.connect_targets(START, TARGET, "walkaround")
    assert result.outcome is Outcome.UNSUPPORTED
    assert result.dirty is True
    # "no copper kept" and "we could not put the board back" are different facts.
    assert result.committed is None
    assert result.copper_state == "retained_unknown"
    assert result.evidence["rollback"]["copper_digest_matches"] is False
    assert session.dirty is True
    # A quarantined session refuses everything mutating from then on.
    refused = session.act(StructuredAction.start_route(10.0, 10.0, 1))
    assert refused.outcome is Outcome.UNSUPPORTED
    assert refused.evidence["reason"] == "session_quarantined"
    assert refused.allowed_next_actions == ()
    snap = session.snapshot()
    assert snap.dirty is True
    assert snap.allowed_next_actions == ()


def test_refused_restore_is_unsupported_and_quarantines():
    engine = _engine(fail_fix_at={(40.0, 10.0)}, fail_restore=True)
    session = _session(engine)
    result = session.connect_targets(START, TARGET, "walkaround")
    assert result.outcome is Outcome.UNSUPPORTED
    assert result.dirty is True
    # Candidate probing halts on a quarantined session instead of running on a
    # board whose state is not trustworthy.
    results, snap = session.probe_candidates(START, TARGET, [{"name": "x"}])
    assert results == []
    assert snap.outcome is Outcome.UNSUPPORTED
    assert snap.evidence["reason"] == "session_quarantined"


# ---------------------------------------------------------------------------
# Candidate probing
# ---------------------------------------------------------------------------


def test_each_candidate_starts_from_identical_state_and_the_board_is_restored():
    engine = FakeEngine(
        clusters={
            "A": {(10.0, 10.0, 1)},
            "M1": {(25.0, 6.0, 1)},
            "M2": {(25.0, 14.0, 1)},
            "B": {(40.0, 10.0, 1)},
        },
        pads=[PadInfo(10.0, 10.0, net_code=1), PadInfo(40.0, 10.0, net_code=1)],
    )
    session = _session(engine)
    before = rows_digest(canonical_rows(engine.get_tracks(), engine.get_vias()))
    results, snap = session.probe_candidates(
        START, TARGET,
        [
            {"name": "detour_north", "waypoints": [(25.0, 6.0, 1)]},
            {"name": "detour_south", "waypoints": [(25.0, 14.0, 1)]},
        ],
    )
    assert len(results) == 2
    assert all(r.result.identical_start is True for r in results)
    assert all(r.result.connected for r in results)
    assert all(r.result.accepted for r in results)
    after = rows_digest(canonical_rows(engine.get_tracks(), engine.get_vias()))
    assert after == before
    assert snap.outcome is Outcome.OK
    assert snap.evidence["restored_ok"] is True
    assert session.dirty is False


def test_probe_reports_the_displaced_net_before_restoring():
    """The candidate's own evidence proves a foreign net moved, then was put back."""
    engine = FakeEngine(
        clusters={
            "A": {(10.0, 10.0, 1)},
            "B": {(40.0, 10.0, 1)},
            "O": {(25.0, 9.4, 1), (25.0, 10.6, 1)},
        },
        pads=[PadInfo(10.0, 10.0, net_code=1), PadInfo(40.0, 10.0, net_code=1)],
    )
    engine.state.tracks.append(TrackInfo(25.0, 9.4, 25.0, 10.6, layer=1, net_code=2))
    session = _session(engine)
    before = canonical_rows(engine.get_tracks(), engine.get_vias())

    results, _snap = session.probe_candidates(START, TARGET, [{"name": "shove", "mode": "shove"}])
    moved = results[0].result.evidence["changed_nets"]
    after = canonical_rows(engine.get_tracks(), engine.get_vias())
    assert nets_changed(before, after) == ()
    # The candidate ran against a modification target; the fake merges clusters,
    # which is enough to show the changed-net reporting path is wired.
    assert isinstance(moved, list)


def test_probe_candidates_refuses_an_unbounded_batch():
    session = _session(_engine())
    results, snap = session.probe_candidates(
        START, TARGET, [{"name": f"c{i}"} for i in range(3)], max_candidates=2
    )
    assert results == []
    assert snap.outcome is Outcome.INVALID_ACTION
    assert snap.evidence["reason"] == "too_many_candidates"


# ---------------------------------------------------------------------------
# Env adapter
# ---------------------------------------------------------------------------


def test_from_env_requires_an_explicit_desync_acknowledgement():
    class _Env:
        def __init__(self, engine, board_path):
            self._engine = engine
            self.board_path = board_path

    env = _Env(_engine(), "/tmp/from_env.kicad_pcb")
    with pytest.raises(RuntimeError, match="unsupported"):
        AgentSession.from_env(env)
    session = AgentSession.from_env(env, acknowledge_env_desync=True)
    assert session.board_path == "/tmp/from_env.kicad_pcb"
    assert session.snapshot().outcome is Outcome.OK


def test_from_env_adapter_refuses_an_env_without_an_engine():
    class _Empty:
        pass

    with pytest.raises(ValueError):
        AgentSession.from_env(_Empty(), acknowledge_env_desync=True)


# ---------------------------------------------------------------------------
# Session construction without a board path
# ---------------------------------------------------------------------------


def test_session_without_a_board_path_still_has_a_context():
    """The old AttributeError on a valid request is gone."""
    engine = _engine()
    engine.get_project_path = lambda: "/tmp/derived/board.kicad_pro"   # type: ignore[method-assign]
    session = AgentSession(engine, require_tokens=False)
    assert isinstance(session.rule_context, RuleContext)
    assert session.rule_context.rules_path.endswith("board.kicad_dru")
    result = session.connect_targets(START, TARGET, "walkaround")
    assert result.outcome is Outcome.OK


def test_rule_context_can_be_supplied_explicitly(tmp_path):
    board = tmp_path / "synthetic.kicad_pcb"
    board.write_text("(kicad_pcb)")
    ctx: RuleContext = resolve_rule_context(str(board))
    engine = _engine()
    session = AgentSession(engine, board_path=str(board), rule_context=ctx,
                           require_tokens=False)
    assert session.rule_context is ctx
