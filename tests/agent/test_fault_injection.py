"""Fault-injection regressions for the second correction cycle.

Each test reproduces a failure a targeted independent check found, and asserts the
invariant rather than the implementation: no unverified copper survives, the DRC
side-channel cannot be bypassed, the rule context cannot be downgraded, the
adapter cannot be talked out of its checks, and "we could not put it back" is
reported differently from "nothing was kept".
"""

from __future__ import annotations

import json
import pathlib

import pytest

from pcb_world.agent import (
    AgentSession,
    Outcome,
    RuleContext,
    StructuredAction,
    canonical_rows,
)
from pcb_world.agent.drc_gate import DrcContextError, DrcGate, take_violations
from pcb_world.agent.state import rows_digest
from pcb_world.agent.state import StateProbe
from pcb_world.agent.tool_api import handle_request

from tests.agent.fake_engine import FakeEngine, FakeViolation, PadInfo

START = (10.0, 10.0, 1)
TARGET = (40.0, 10.0, 1)


def _engine(**kwargs) -> FakeEngine:
    kwargs.setdefault("clusters", {"A": {(10.0, 10.0, 1)}, "B": {(40.0, 10.0, 1)}})
    kwargs.setdefault(
        "pads", [PadInfo(10.0, 10.0, net_code=1), PadInfo(40.0, 10.0, net_code=1)]
    )
    return FakeEngine(**kwargs)


def _session(engine, **kwargs) -> AgentSession:
    kwargs.setdefault("board_path", "/tmp/fault_injection.kicad_pcb")
    kwargs.setdefault("require_tokens", False)
    return AgentSession(engine, **kwargs)


def _digest(engine) -> str:
    return rows_digest(canonical_rows(engine.get_tracks(), engine.get_vias()))


def test_connectivity_failure_after_start_route_rolls_the_action_back():
    """Root's reproduction: the copper used to survive an unverified path."""
    engine = _engine()
    session = _session(engine)
    snapshot = session.snapshot()
    started = session.act(StructuredAction.start_route(10.0, 10.0, 1), token=snapshot.token)
    assert started.outcome is Outcome.OK

    engine.build_connectivity_failure = "connectivity exploded"
    failed = session.act(
        StructuredAction.make_line(40.0, 10.0, "walkaround"), token=started.token
    )

    assert failed.outcome is Outcome.UNVERIFIED
    assert failed.evidence["reason"] == "unverified_state"
    assert failed.evidence["rollback"]["copper_digest_matches"] is True
    assert engine.get_tracks() == []
    assert engine.get_vias() == []
    assert engine.live_checkpoints == 0


def test_connectivity_failure_with_unverifiable_restore_quarantines():
    engine = _engine(restore_silently_wrong=True)
    session = _session(engine)
    snapshot = session.snapshot()
    started = session.act(StructuredAction.start_route(10.0, 10.0, 1), token=snapshot.token)
    assert started.outcome is Outcome.OK

    engine.build_connectivity_failure = "connectivity exploded"
    failed = session.act(
        StructuredAction.make_line(40.0, 10.0, "walkaround"), token=started.token
    )
    assert failed.outcome is Outcome.UNSUPPORTED
    assert failed.dirty is True
    assert session.dirty is True
    refused = session.act(StructuredAction.start_route(10.0, 10.0, 1))
    assert refused.evidence["reason"] == "session_quarantined"


def test_unreadable_post_probe_rolls_back():
    """An engine that goes unreadable right after a mutation is not trusted.

    The failure is one-shot so the rollback itself can still be verified: the
    result is ``unverified`` (the action was undone, not kept) and the session
    stays usable.
    """
    engine = _engine()
    session = _session(engine)
    snapshot = session.snapshot()
    started = session.act(StructuredAction.start_route(10.0, 10.0, 1), token=snapshot.token)
    assert started.outcome is Outcome.OK

    original = engine.get_routing_session_state
    state = {"calls": 0}

    def flaky():
        state["calls"] += 1
        # Call 1 is the pre-action probe, call 2 the one right after the mutation.
        if state["calls"] == 2:
            raise RuntimeError("probe died")
        return original()

    engine.get_routing_session_state = flaky
    failed = session.act(
        StructuredAction.make_line(40.0, 10.0, "walkaround"), token=started.token
    )
    assert failed.outcome is Outcome.UNVERIFIED
    assert failed.evidence["stage"] == "post_action_state"
    assert failed.evidence["rollback"]["copper_digest_matches"] is True
    assert engine.get_tracks() == []
    assert failed.dirty is False


def test_a_persistently_unreadable_engine_quarantines_after_the_attempt():
    engine = _engine()
    session = _session(engine)
    snapshot = session.snapshot()
    started = session.act(StructuredAction.start_route(10.0, 10.0, 1), token=snapshot.token)
    assert started.outcome is Outcome.OK

    original = engine.build_connectivity

    def flip():
        original()
        engine.breaking_reads = True          # dies right after the mutation

    engine.build_connectivity = flip
    failed = session.act(
        StructuredAction.make_line(40.0, 10.0, "walkaround"), token=started.token
    )
    # The engine could not be read, so the snapshot refuses to claim a definite
    # outcome (UNVERIFIED); the quarantine and copper_state carry the rest.
    assert failed.outcome in (Outcome.UNVERIFIED, Outcome.UNSUPPORTED)
    assert session.dirty is True
    assert failed.evidence["copper_state"] == "retained_unknown"
    # The rollback was attempted even though it could not be verified; with the
    # engine readable again, the copper is in fact gone.
    engine.breaking_reads = False
    engine.build_connectivity = original
    assert engine.get_tracks() == []


def test_drc_regression_rollback_re_arms_the_baseline_for_the_restored_board():
    """A verified rollback leaves the board back on a known baseline.

    Re-running that DRC would cost tens of seconds on a real board for an answer
    that is still exactly valid, so the gate is re-seeded with the *verified*
    baseline instead of being left empty.
    """
    engine = _engine(
        violation_on_fix=FakeViolation(
            error_code=5, error_type="Clearance violation", message="nope",
        )
    )
    session = _session(engine)
    runs_before = engine.run_drc_calls
    result = session.connect_targets(START, TARGET, "walkaround")
    assert result.outcome is Outcome.ROUTING_FAILED
    assert result.evidence["copper_state"] == "restored"
    assert engine.get_tracks() == []
    cached = session._gate._cached
    assert cached is not None                      # re-armed, not thrown away
    assert cached.rules_path == ""
    # The next attempt's baseline is a cache hit: one verification run, no new
    # baseline run.
    runs_after_attempt = engine.run_drc_calls
    session.connect_targets(START, TARGET, "walkaround")
    assert engine.run_drc_calls - runs_after_attempt <= 1


def test_a_seed_from_another_rule_context_is_refused():
    """A baseline measured under one context must not answer for another.

    Re-seeding after a verified rollback is a shortcut for a *valid* answer; if
    the project or rule file changed since the baseline was taken, the seeded
    result would attach an old answer to a new cache key.
    """
    engine = _engine()
    session = _session(engine)
    baseline = take_violations(engine, "")
    assert session._gate._cached is None

    # Same copper, different project: the context identity no longer matches.
    engine.project_path = "/tmp/some-other-board.kicad_pro"
    session._gate.seed(baseline, "digest-after-restore")
    assert session._gate._cached is None
    assert session._gate._cached_key is None

    # With a matching context the seed is kept (it is the cheap path).
    fresh = take_violations(engine, "")
    session._gate.seed(fresh, "digest-after-restore")
    assert session._gate._cached is not None


def test_checkpoint_failure_before_mutation_is_reported_without_change():
    engine = _engine(checkpoint_failure="no checkpoints available")
    session = _session(engine)
    snapshot = session.snapshot()
    result = session.act(StructuredAction.start_route(10.0, 10.0, 1), token=snapshot.token)
    assert result.outcome is Outcome.UNSUPPORTED
    assert result.evidence["reason"] == "checkpoint_unavailable"
    assert engine.get_tracks() == []
    assert engine.live_checkpoints == 0


def test_provisional_does_not_override_unverified_connectivity():
    engine = FakeEngine(
        clusters={
            "A": {(10.0, 10.0, 1)},
            "M": {(25.0, 10.0, 1)},
            "B": {(40.0, 10.0, 1)},
        },
        pads=[PadInfo(10.0, 10.0, net_code=1), PadInfo(40.0, 10.0, net_code=1)],
    )
    session = _session(engine)
    snapshot = session.snapshot()

    original = engine.build_connectivity
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] > 2:
            raise RuntimeError("connectivity unreadable")
        return original()

    engine.build_connectivity = flaky
    result = session.connect_targets(
        START, TARGET, "walkaround", waypoints=[(25.0, 10.0, 1)],
        token=snapshot.token, provisional=True,
    )
    assert result.outcome in (Outcome.UNVERIFIED, Outcome.UNSUPPORTED)
    assert result.committed in (False, None)
    assert result.accepted is False
    engine.build_connectivity = original
    assert engine.get_tracks() == []


def test_atomic_false_without_provisional_is_refused():
    engine = _engine()
    session = _session(engine)
    result = session.connect_targets(START, TARGET, "walkaround", atomic=False)
    assert result.outcome is Outcome.INVALID_ACTION
    assert result.evidence["reason"] == "underspecified"
    assert engine.get_tracks() == []


def test_step_handles_are_released_on_an_exception_path():
    engine = _engine(fail_fix_at={(40.0, 10.0)})
    session = _session(engine)
    session.connect_targets(START, TARGET, "walkaround")
    assert engine.live_checkpoints == 0


def test_validation_failure_never_reaches_a_checkpoint():
    engine = _engine()
    session = _session(engine)
    snapshot = session.snapshot()
    snap = session.act(
        StructuredAction.make_line(40.0, 10.0, "walkaround"), token=snapshot.token
    )
    assert snap.outcome is Outcome.NO_ACTIVE_ROUTE
    assert engine.get_tracks() == []
    assert engine.live_checkpoints == 0
    assert engine.run_drc_calls == 0


# ---------------------------------------------------------------------------
# B. DRC side-channel and rule-context binding
# ---------------------------------------------------------------------------


def test_a_reported_rule_load_failure_refuses_take_violations():
    engine = _engine(drc_sets_rules_error="requested rule load failure")
    with pytest.raises(DrcContextError) as exc:
        take_violations(engine, "/tmp/whatever.kicad_dru")
    assert "requested rule load failure" in str(exc.value)


def test_connect_reports_unsupported_when_drc_silently_falls_back():
    """Root's reproduction: run_drc returning [] while the load failed."""
    engine = _engine(drc_sets_rules_error="requested rule load failure")
    session = _session(engine)
    result = session.connect_targets(START, TARGET, "walkaround")
    assert result.outcome is Outcome.UNSUPPORTED
    assert result.evidence["reason"] == "drc_unavailable"
    assert result.accepted is False
    assert engine.get_tracks() == []


def test_rule_file_content_change_between_runs_is_refused(tmp_path):
    rules = tmp_path / "board.kicad_dru"
    rules.write_text("(version 1)\n", encoding="utf-8")
    engine = _engine()
    session = _session(engine)
    gate = DrcGate(session)
    baseline = gate.baseline(str(rules), "digest-a")

    rules.write_text('(version 1)\n(rule "changed")\n', encoding="utf-8")
    with pytest.raises(DrcContextError) as exc:
        gate.verify(baseline, str(rules), "digest-b")
    assert "context changed" in str(exc.value)


def test_rule_file_disappearance_between_runs_is_refused(tmp_path):
    rules = tmp_path / "board.kicad_dru"
    rules.write_text("(version 1)\n", encoding="utf-8")
    engine = _engine()
    session = _session(engine)
    gate = DrcGate(session)
    baseline = gate.baseline(str(rules), "digest-a")

    pathlib.Path(rules).unlink()
    with pytest.raises(DrcContextError):
        gate.verify(baseline, str(rules), "digest-b")


def test_rules_cache_identity_includes_the_rule_content(tmp_path):
    rules = tmp_path / "board.kicad_dru"
    rules.write_text("(version 1)\n", encoding="utf-8")
    engine = _engine()
    session = _session(engine)
    gate = DrcGate(session)
    first = gate.baseline(str(rules), "same-digest")
    assert gate.baseline(str(rules), "same-digest") is first       # cache hit
    rules.write_text('(version 1)\n(rule "different")\n', encoding="utf-8")
    second = gate.baseline(str(rules), "same-digest")
    assert second is not first                                     # context invalidated


def test_a_context_pointing_at_a_missing_file_cannot_erase_loaded_rules(tmp_path):
    """Root's regression, at the session level: no silent downgrade to implicit."""
    rules = tmp_path / "actual.kicad_dru"
    rules.write_text("(version 1)\n", encoding="utf-8")
    board = tmp_path / "actual.kicad_pcb"
    board.write_text("(kicad_pcb)")
    engine = _engine(rules_path=str(rules))
    session = AgentSession(
        engine,
        board_path=str(board),
        rule_context=RuleContext(
            board_path=str(board), rules_path=str(tmp_path / "missing.kicad_dru"),
            project_path=str(tmp_path / "actual.kicad_pro"), source="explicit",
        ),
        require_tokens=False,
    )
    result = session.connect_targets(START, TARGET, "walkaround")
    message = str(result.evidence.get("message", ""))
    assert result.outcome is Outcome.UNSUPPORTED
    assert "missing.kicad_dru" in message or "but the routing engine loaded" in message
    assert engine.get_tracks() == []


# ---------------------------------------------------------------------------
# C. Adapter strictness
# ---------------------------------------------------------------------------


def test_adapter_requires_a_token_even_when_the_session_opts_out():
    engine = _engine()
    session = _session(engine, require_tokens=False)
    response = handle_request(session, {
        "tool": "connect_targets", "token": None,
        "start": list(START), "target": list(TARGET),
    })
    assert response["ok"] is False
    assert response["reason"] == "token_required"
    assert engine.get_tracks() == []


def test_adapter_rejects_a_non_string_token():
    session = _session(_engine(), require_tokens=False)
    response = handle_request(session, {
        "tool": "connect_targets", "token": 12345,
        "start": list(START), "target": list(TARGET),
    })
    assert response["ok"] is False and response["reason"] == "token_required"


def test_adapter_validates_shapes_and_booleans_before_mutating():
    session = _session(_engine(), require_tokens=False)
    token = handle_request(session, {"tool": "snapshot"})["snapshot"]["token"]

    response = handle_request(session, {
        "tool": "connect_targets", "token": token, "start": list(START),
        "target": list(TARGET), "waypoints": 5,
    })
    assert response["ok"] is False and response["reason"] == "malformed_coordinate"

    response = handle_request(session, {
        "tool": "connect_targets", "token": token, "start": list(START),
        "target": list(TARGET), "provisional": "false",
    })
    assert response["ok"] is False and response["reason"] == "malformed_field"

    response = handle_request(session, {
        "tool": "connect_targets", "token": token, "start": [10.0, float("inf"), 1],
        "target": list(TARGET),
    })
    assert response["ok"] is False and response["reason"] == "malformed_coordinate"


def test_adapter_waypoints_none_is_a_no_op_and_bad_geometry_stays_stable():
    session = _session(_engine(), require_tokens=False)
    token = handle_request(session, {"tool": "snapshot"})["snapshot"]["token"]
    response = handle_request(session, {
        "tool": "connect_targets", "token": token, "start": list(START),
        "target": list(TARGET), "waypoints": None,
    })
    assert response["ok"] is True

    response = handle_request(session, {
        "tool": "connect_targets", "token": token, "start": [10.0, 10.0, float("inf")],
        "target": list(TARGET),
    })
    assert response["ok"] is False
    json.dumps(response, allow_nan=False)


def test_act_tool_refusals_are_json_serialisable_without_nan():
    session = _session(_engine(), require_tokens=False)
    token = handle_request(session, {"tool": "snapshot"})["snapshot"]["token"]
    for action in (
        {"name": "make_line", "schema_version": "1.0",
         "params": {"x_mm": float("nan"), "y_mm": 1.0, "mode": "walkaround"}},
        {"name": "start_route", "schema_version": "99",
         "params": {"x_mm": 1.0, "y_mm": 1.0, "layer": 1}},
        {"name": "teleport", "schema_version": "1.0", "params": {}},
    ):
        response = handle_request(session, {"tool": "act", "token": token, "action": action})
        json.dumps(response, allow_nan=False)


def test_already_connected_is_a_successful_tool_call():
    engine = _engine(clusters={"A": {(10.0, 10.0, 1), (40.0, 10.0, 1)}})
    session = _session(engine, require_tokens=False)
    token = handle_request(session, {"tool": "snapshot"})["snapshot"]["token"]
    response = handle_request(session, {
        "tool": "connect_targets", "token": token,
        "start": list(START), "target": list(TARGET),
    })
    assert response["ok"] is True
    assert response["result"]["outcome"] == "already_connected"
    assert response["result"]["copper_state"] == "unchanged"


# ---------------------------------------------------------------------------
# D. Rollback verification and result semantics
# ---------------------------------------------------------------------------


def test_rollback_verification_notices_unrestored_session_state():
    """Copper comes back but the router session does not: the restore is unproven."""
    engine = _engine(restore_keeps_session=True)
    session = _session(engine)
    snapshot = session.snapshot()
    started = session.act(StructuredAction.start_route(10.0, 10.0, 1), token=snapshot.token)
    assert started.outcome is Outcome.OK

    engine.build_connectivity_failure = "connectivity exploded"
    failed = session.act(
        StructuredAction.make_line(40.0, 10.0, "walkaround"), token=started.token
    )
    assert failed.outcome is Outcome.UNSUPPORTED
    assert failed.evidence["rollback"]["copper_digest_matches"] is True
    assert failed.evidence["rollback"]["session_state_matches"] is False
    assert "expected_state" in failed.evidence["rollback"]
    assert session.dirty is True


def test_rollback_evidence_reports_the_state_it_compared():
    engine = _engine(fail_fix_at={(40.0, 10.0)})
    session = _session(engine)
    result = session.connect_targets(START, TARGET, "walkaround")
    rollback = result.evidence["rollback"]
    assert set(rollback["expected_state"]) == {
        "route_active", "head", "target", "net_code", "layer",
    }
    assert rollback["expected_layer"] == rollback["actual_layer"]


def test_probe_results_separate_would_commit_from_the_restored_board():
    engine = _engine()
    session = _session(engine)
    results, _snap = session.probe_candidates(START, TARGET, [{"name": "direct"}])
    probe = results[0].result
    assert probe.evaluated is True
    assert probe.would_commit is True          # what the candidate would have kept
    assert probe.committed is False            # the board was restored
    assert probe.copper_state == "restored"
    assert engine.get_tracks() == []


def test_unknown_copper_state_is_not_reported_as_no_change():
    engine = _engine(fail_fix_at={(40.0, 10.0)}, fail_restore=True)
    session = _session(engine)
    result = session.connect_targets(START, TARGET, "walkaround")
    assert result.committed is None
    assert result.copper_state == "retained_unknown"
    assert result.dirty is True


# ---------------------------------------------------------------------------
# E. Snapshot token honesty
# ---------------------------------------------------------------------------


def test_a_partial_snapshot_mints_no_usable_token():
    engine = _engine()
    session = _session(engine)
    partial = session.snapshot(include_geometry=False)
    assert partial.token == ""
    assert partial.allowed_next_actions == ()
    assert partial.evidence["token"] == "unavailable"
    refused = session.act(StructuredAction.start_route(10.0, 10.0, 1), token=partial.token)
    assert refused.outcome is Outcome.INVALID_ACTION
    assert engine.get_tracks() == []


# ---------------------------------------------------------------------------
# Transactions: an unreadable read is never evidence of success
# ---------------------------------------------------------------------------


def test_transaction_unreadable_post_route_read_rolls_back():
    """Root's reproduction, verbatim: reads fail as soon as copper exists."""
    engine = _engine()
    session = _session(engine)
    original = session._probe

    def unreadable(*args, **kwargs):
        if engine.get_track_count():
            return StateProbe(error="injected post-route read failure")
        return original(*args, **kwargs)

    session._probe = unreadable
    result = session.connect_targets(START, TARGET, "walkaround")

    assert result.outcome is Outcome.UNVERIFIED
    assert result.accepted is False
    assert result.committed is False
    assert result.copper_state == "restored"
    assert result.snapshot.outcome is Outcome.UNVERIFIED
    assert result.snapshot.dirty is False
    session._probe = original
    assert engine.get_tracks() == []
    assert engine.get_vias() == []


def test_transaction_late_post_drc_unreadability_cannot_report_success():
    """A one-shot read failure after the gate's verification run.

    The rollback probe then succeeds, so the attempt is reported ``unverified``
    with the copper provably gone, and the session stays usable.
    """
    engine = _engine()
    session = _session(engine)
    original = session._probe
    state = {"failed": False}

    def unreadable_after_drc(*args, **kwargs):
        if engine.run_drc_calls >= 2 and not state["failed"]:
            state["failed"] = True
            return StateProbe(error="late post-DRC read failure")
        return original(*args, **kwargs)

    session._probe = unreadable_after_drc
    result = session.connect_targets(START, TARGET, "walkaround")

    assert result.outcome is Outcome.UNVERIFIED
    assert result.accepted is False
    assert result.committed is False
    assert result.copper_state == "restored"
    assert result.snapshot.outcome is Outcome.UNVERIFIED
    assert result.snapshot.dirty is False
    session._probe = original
    assert engine.get_tracks() == []


def test_transaction_persistent_late_unreadability_quarantines():
    """Every read after the gate's verification run fails: no claim is available."""
    engine = _engine()
    session = _session(engine)
    original = session._probe

    def unreadable_after_drc(*args, **kwargs):
        if engine.run_drc_calls >= 2:
            return StateProbe(error="persistent late post-DRC read failure")
        return original(*args, **kwargs)

    session._probe = unreadable_after_drc
    result = session.connect_targets(START, TARGET, "walkaround")

    assert result.outcome in (Outcome.UNVERIFIED, Outcome.UNSUPPORTED)
    assert result.accepted is False
    assert result.committed is None
    assert result.copper_state == "retained_unknown"
    assert result.dirty is True
    assert result.snapshot.outcome is Outcome.UNVERIFIED
    session._probe = original
    assert engine.get_tracks() == []            # the rollback was still attempted


def test_finish_refuses_to_claim_success_on_an_unreadable_probe():
    """The central invariant, exercised directly."""
    engine = _engine()
    session = _session(engine)
    result = session._finish(
        START, TARGET, "walkaround", (), StateProbe(error="blind"),
        {"reason": "x"}, outcome=Outcome.OK, committed=True, connected=True,
        accepted=True, rollback_verified=None, copper_state="kept",
    )
    assert result.outcome is Outcome.UNVERIFIED
    assert result.accepted is False
    assert result.committed is None
    assert result.copper_state == "retained_unknown"
    assert result.snapshot.outcome is Outcome.UNVERIFIED
    assert session.dirty is True
    assert result.evidence["final_state_unreadable"] is True


# ---------------------------------------------------------------------------
# F. A native call reaped at its deadline
# ---------------------------------------------------------------------------

#: The shape of the message ``_ServerConn.request`` raises when the owned engine
#: child is killed because a native operation passed the absolute deadline the
#: caller gave it. run_via2 lost a session to exactly this message — the run's own
#: time limit had clamped the acceptance DRC to a few seconds against a ~41 s
#: whole-board pass — and the checkpoint the rollback needed died with the child.
REAPED_AT_ACCEPTANCE = (
    "owned engine operation 'call' exceeded its 3.399 s deadline; child reaped"
)
REAPED_AT_RESTORE = (
    "owned engine operation 'call' exceeded its 0.000 s deadline; child reaped"
)


def test_a_reaped_acceptance_drc_keeps_the_cause_and_the_copper_state():
    """The failure's own words survive, and the copper is not claimed back.

    Root's reproduction: ``run_state.json`` kept ``reason=drc_unavailable`` and
    ``copper_state=retained_unknown`` and nothing else, so the cause — a native
    call reaped at its deadline, taking the checkpoint-holding child with it —
    could only be recovered by re-running the transaction. The session must
    report it, and it must report it *without* claiming the copper came back.
    """
    engine = _engine(restore_exception=REAPED_AT_RESTORE)
    session = _session(engine)
    # The baseline was already taken and cached (the long-run case), so the only
    # DRC the transaction dispatches is the acceptance pass on the copper.
    snapshot = session.snapshot()
    session._gate.baseline("", snapshot.geometry_digest)
    engine.drc_failure = REAPED_AT_ACCEPTANCE

    result = session.connect_targets(START, TARGET, "walkaround")

    assert result.outcome is Outcome.UNSUPPORTED
    assert result.accepted is False
    assert result.committed is None
    assert result.evidence["reason"] == "drc_unavailable"
    assert REAPED_AT_ACCEPTANCE in result.evidence["exception"]
    assert REAPED_AT_RESTORE in result.evidence["rollback"]["restore_exception"]
    # No claim that the board was put back: the restore could not even be asked,
    # so there is no ``restored`` answer to misread as a rollback.
    assert "restored" not in result.evidence["rollback"]
    assert result.copper_state == "retained_unknown"
    assert result.evidence["copper_state"] == "retained_unknown"
    assert session.dirty is True
    assert "could not verify the restore" in (session.dirty_reason or "")
    # Copper *was* applied before the DRC refused to answer: the record must not
    # read like a plan that did nothing.
    assert result.evidence["added_length_mm"] > 0
    assert result.evidence["steps_count"] >= 1


def test_a_reaped_child_is_reported_differently_from_a_refusing_child():
    """A live child that answers "no" is not the same evidence as no child.

    ``fail_restore`` (a live engine refusing) and a reaped child both end in
    ``retained_unknown``, but only the second means the checkpoint no longer
    exists — which is what decides whether retrying the restore is even an
    option.
    """
    engine = _engine(fail_restore=True)
    session = _session(engine)
    snapshot = session.snapshot()
    session._gate.baseline("", snapshot.geometry_digest)
    engine.drc_failure = REAPED_AT_ACCEPTANCE
    result = session.connect_targets(START, TARGET, "walkaround")

    assert result.evidence["reason"] == "drc_unavailable"
    rollback = result.evidence["rollback"]
    assert rollback["restored"] is False
    assert "restore_exception" not in rollback
    assert result.copper_state == "retained_unknown"
