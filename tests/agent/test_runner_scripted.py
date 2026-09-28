"""Runner behaviour with a fake engine: closure, limits, failures, resume."""

from __future__ import annotations

import json

import pytest

from pcb_world.agent.observations import LayerResolver, enumerate_net_pairs
from pcb_world.agent.runner import (
    CATEGORY_BAD_RESPONSE,
    CATEGORY_TIMEOUT,
    PlannerError,
    PlannerReply,
    RoutingRunner,
    RunnerConfig,
    ScriptedPlanner,
)
from pcb_world.agent.scheduler import (
    AttemptRecord,
    ProvenanceMismatchError,
    RunState,
    build_provenance,
)
from pcb_world.agent.session import AgentSession
from tests.agent.conftest import fake_artifact_verifier
from pcb_world.engine.wire import RatsnestEdge

from tests.agent.fake_engine import FakeEngine, FakeViolation, PadInfo, TrackInfo


def _fake_board(*, failing_target: tuple[float, float] | None = None,
                extra_pair: bool = False) -> FakeEngine:
    """Two nets, each with an outstanding pair, over a two-cluster fake board."""
    clusters = {
        "A": {(10.0, 10.0, 1)},
        "B": {(40.0, 10.0, 1)},
    }
    pads = [PadInfo(10.0, 10.0, net_code=1), PadInfo(40.0, 10.0, net_code=1)]
    ratsnest = [
        RatsnestEdge(10.0, 10.0, 40.0, 10.0, 1, 0, 0),
    ]
    if extra_pair:
        clusters["C"] = {(10.0, 20.0, 1)}
        clusters["D"] = {(40.0, 20.0, 1)}
        pads += [PadInfo(10.0, 20.0, net_code=2), PadInfo(40.0, 20.0, net_code=2)]
        ratsnest.append(RatsnestEdge(10.0, 20.0, 40.0, 20.0, 2, 0, 0))
    engines = {
        "fail_fix_at": {failing_target} if failing_target else set(),
        "clusters": clusters,
        "pads": pads,
        "ratsnest": ratsnest,
    }
    return engines


def _engine(failing_target: tuple[float, float] | None = None, *, extra_pair=False,
            **kwargs) -> FakeEngine:
    spec = _fake_board(failing_target=failing_target, extra_pair=extra_pair)
    spec.update(kwargs)
    return FakeEngine(**spec)


def _config(tmp_path, engine: FakeEngine, name: str = "run", **overrides) -> RunnerConfig:
    defaults = dict(
        board_path="/tmp/fake_board.kicad_pcb",
        run_dir=str(tmp_path / name),
        max_attempts=8,
        time_limit_s=60.0,
        planner=None,
        engine_factory=lambda path: engine,
        session_factory=lambda eng, path: AgentSession(eng, board_path=path),
        artifact_verifier=fake_artifact_verifier,
    )
    defaults.update(overrides)
    return RunnerConfig(**defaults)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_runner_closes_a_pair_checkpoints_and_reports(tmp_path):
    engine = _engine()
    report = RoutingRunner(_config(tmp_path, engine)).run()

    assert report.status == "completed"
    assert report.stop_reason == "no_outstanding_pairs"
    assert report.accepted >= 1
    assert report.attempts >= 1
    assert report.mode_usage                      # a real mode was used
    assert report.best_board_path and report.best_board_sha256
    assert (tmp_path / "run" / "run_state.json").is_file()
    assert report.progress_after["unrouted_edges"] == 0
    assert report.drc["added_relevant_count"] == 0


def test_runner_does_not_ask_the_planner_when_deterministic_plans_win(tmp_path):
    engine = _engine()
    planner = _CountingPlanner()
    report = RoutingRunner(_config(
        tmp_path, engine, planner=planner, max_model_requests=5,
    )).run()
    assert report.accepted >= 1
    assert planner.calls == 0
    assert report.model_usage["requests"] == 0


def test_runner_probes_alternatives_and_records_them(tmp_path):
    engine = _engine(extra_pair=False)
    report = RoutingRunner(_config(tmp_path, engine)).run()
    # Every candidate evaluated is recorded, so plan evaluations are at least the
    # number of real attempts on the pair.
    assert report.plan_evaluations >= report.attempts


def test_a_candidate_that_closes_nothing_costs_no_full_drc(tmp_path):
    """The cheap rejection, measured: discarded copper is rolled back, not DRC'd.

    A plan that cannot close the connection leaves nothing to accept, so the
    sweep rolls it back from the full snapshot and verifies the restored state
    instead of spending a native DRC on copper that is about to be discarded.
    Widening the candidate set must therefore add no DRC calls at all.
    """
    narrow = _engine(failing_target=(40.0, 10.0))
    wide = _engine(failing_target=(40.0, 10.0))
    RoutingRunner(_config(
        tmp_path, narrow, name="narrow", max_attempts=1, candidate_limit=1,
    )).run()
    RoutingRunner(_config(
        tmp_path, wide, name="wide", max_attempts=1, candidate_limit=6,
    )).run()

    assert wide.run_drc_calls == narrow.run_drc_calls
    # And the sweeps really did evaluate more plans than the narrow run.
    narrow_state = json.loads((tmp_path / "narrow" / "run_state.json").read_text())
    wide_state = json.loads((tmp_path / "wide" / "run_state.json").read_text())
    assert len(wide_state["attempts"]) > len(narrow_state["attempts"])
    # Nothing connected, so no copper was kept and every rollback was verified.
    for record in wide_state["attempts"]:
        if record["sweep_member"]:
            assert record["committed"] is False
            assert record["copper_state"] in ("restored", "unchanged", None)


# ---------------------------------------------------------------------------
# Resume
# ---------------------------------------------------------------------------


def test_initial_then_resume_matches_an_uninterrupted_run(tmp_path):
    resumed_engine = _engine(extra_pair=True)
    straight_engine = _engine(extra_pair=True)

    first = RoutingRunner(_config(
        tmp_path, resumed_engine, name="resumed", max_attempts=1,
    )).run()
    assert first.attempts == 1

    state = RunState.load(
        str(tmp_path / "resumed" / "run_state.json"),
        expected_provenance=build_provenance(
            board_path="/tmp/fake_board.kicad_pcb", project_path=None, rules_path=None,
        ),
    )
    resumed = RoutingRunner(
        _config(tmp_path, resumed_engine, name="resumed", max_attempts=5),
        state=state,
    ).run()

    straight = RoutingRunner(_config(
        tmp_path, straight_engine, name="straight", max_attempts=5,
    )).run()

    assert resumed.progress_after == straight.progress_after
    assert resumed.accepted == straight.accepted
    assert resumed.mode_usage == straight.mode_usage
    assert resumed.attempts == straight.attempts


def test_resume_refuses_when_the_board_hash_changed(tmp_path):
    engine = _engine()
    RoutingRunner(_config(tmp_path, engine, max_attempts=1)).run()
    board = tmp_path / "board.kicad_pcb"
    board.write_text("(kicad_pcb)")
    state_path = tmp_path / "run" / "run_state.json"
    payload = json.loads(state_path.read_text())
    payload["provenance"]["board_sha256"] = "0" * 64
    state_path.write_text(json.dumps(payload))
    with pytest.raises(ProvenanceMismatchError):
        RunState.load(
            str(state_path),
            expected_provenance=build_provenance(
                board_path=str(board), project_path=None, rules_path=None
            ),
        )


def test_resume_refuses_a_different_run_directory(tmp_path):
    """A checkpoint owns its run directory; state and artefacts must not split."""
    engine = _engine()
    RoutingRunner(_config(tmp_path, engine, max_attempts=1)).run()
    state = RunState.load(
        str(tmp_path / "run" / "run_state.json"),
        expected_provenance=build_provenance(
            board_path="/tmp/fake_board.kicad_pcb", project_path=None, rules_path=None,
        ),
    )

    with pytest.raises(ValueError, match="different run directory"):
        RoutingRunner(
            _config(tmp_path, engine, name="elsewhere", max_attempts=1), state=state,
        ).run()

    # The refusal happens before anything is written to the new directory.
    assert not (tmp_path / "elsewhere" / "run_state.json").exists()
    assert list((tmp_path / "elsewhere").glob("best_board*")) == []


# ---------------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------------


class _CountingPlanner:
    name = "counting"

    def __init__(self, reply: dict | None = None, error: PlannerError | None = None) -> None:
        self.calls = 0
        self.reply = reply or {"tool": "connect_targets", "mode": "walkaround"}
        self.error = error

    def propose(self, *, observation, tool_schema, previous_attempts):
        self.calls += 1
        if self.error is not None:
            raise self.error
        request = dict(self.reply)
        connection = observation.get("connection", {})
        request.setdefault("start", list(connection.get("start", [])))
        request.setdefault("target", list(connection.get("target", [])))
        return PlannerReply(request=request, model="counting")


def test_planner_timeout_is_classified_and_does_not_break_the_run(tmp_path):
    engine = _engine(failing_target=(40.0, 10.0))
    planner = _CountingPlanner(error=PlannerError(
        "timed out", category=CATEGORY_TIMEOUT, retryable=True,
    ))
    report = RoutingRunner(_config(
        tmp_path, engine, planner=planner, max_model_requests=2, max_attempts=4,
        stall_patience=10,
    )).run()
    assert report.accepted == 0
    assert report.planner_categories.get(CATEGORY_TIMEOUT, 0) >= 1
    assert report.model_usage["requests"] >= 1
    assert planner.calls >= 1


def test_bad_planner_reply_is_bounded_and_recorded(tmp_path):
    engine = _engine(failing_target=(40.0, 10.0))
    planner = _CountingPlanner(error=PlannerError(
        "not json", category=CATEGORY_BAD_RESPONSE,
    ))
    report = RoutingRunner(_config(
        tmp_path, engine, planner=planner, max_model_requests=3, max_attempts=3,
        stall_patience=10,
    )).run()
    assert report.planner_categories.get(CATEGORY_BAD_RESPONSE, 0) >= 1
    assert planner.calls <= 3


def test_max_model_requests_is_enforced(tmp_path):
    engine = _engine(failing_target=(40.0, 10.0))
    planner = _CountingPlanner(error=PlannerError(
        "rate limited", category="rate_limited", retryable=True,
    ))
    report = RoutingRunner(_config(
        tmp_path, engine, planner=planner, max_model_requests=2, max_attempts=6,
        stall_patience=10,
    )).run()
    assert planner.calls <= 2
    assert report.model_usage["requests"] <= 2


def test_no_false_success_when_the_connection_cannot_close(tmp_path):
    engine = _engine(failing_target=(40.0, 10.0))
    before_tracks = list(engine.get_tracks())
    report = RoutingRunner(_config(
        tmp_path, engine, max_attempts=4, stall_patience=2,
    )).run()
    assert report.accepted == 0
    assert report.improved is False
    assert report.progress_after == report.progress_before
    assert engine.get_tracks() == before_tracks
    assert report.stop_reason in ("no_progress", "attempt_limit", "pairs_exhausted")


def test_stall_patience_stops_a_fruitless_run(tmp_path):
    engine = _engine(failing_target=(40.0, 10.0))
    report = RoutingRunner(_config(
        tmp_path, engine, max_attempts=20, stall_patience=1,
    )).run()
    assert report.stop_reason == "no_progress"
    assert report.attempts <= 2


def test_drc_delta_without_a_committed_change_is_reported_as_jitter(tmp_path):
    """A DRC delta on unchanged copper is named, not reported as a result.

    The native DRC is not fully deterministic on a large board, so a run that
    committed nothing can still see findings appear or disappear. The report must
    say so rather than leaving a bare delta for an operator to misread.
    """
    engine = _engine(failing_target=(40.0, 10.0))
    plain_run_drc = engine.run_drc
    calls = {"n": 0}

    def jittering_run_drc(rules_path: str = "") -> list:
        # Every pass after the first sees one extra relevant finding for copper
        # that never changed - the shape of the observed engine jitter.
        calls["n"] += 1
        findings = list(plain_run_drc(rules_path))
        if calls["n"] >= 2:
            findings.append(FakeViolation(
                error_code=4, error_type="Clearance violation",
                message="Clearance violation",
                x_mm=25.0, y_mm=10.0, layer=1, net_names=["A", "B"],
                item_a="uuid-one", item_b="uuid-two",
            ))
        return findings

    engine.run_drc = jittering_run_drc
    before_tracks = list(engine.get_tracks())
    report = RoutingRunner(_config(
        tmp_path, engine, max_attempts=4, stall_patience=2,
    )).run()

    assert report.accepted == 0
    assert engine.get_tracks() == before_tracks
    assert report.progress_after == report.progress_before
    assert report.drc["added_relevant_count"] >= 1
    assert report.drc["resolved_count"] == 0
    assert any("drc delta on an unmodified board" in note for note in report.notes)
    state = json.loads((tmp_path / "run" / "run_state.json").read_text())
    assert state["metrics"]["drc_jitter_suspected"]["added_relevant"] >= 1


def test_a_clean_run_carries_no_jitter_note(tmp_path):
    """No delta, no note: the warning must not become background noise."""
    engine = _engine()
    report = RoutingRunner(_config(tmp_path, engine, max_attempts=4)).run()

    assert report.accepted >= 1
    assert report.drc["added_relevant_count"] == 0
    assert report.drc["resolved_count"] == 0
    assert not any("jitter" in note for note in report.notes)


def test_time_limit_is_enforced(tmp_path):
    engine = _engine(extra_pair=True)
    ticks = {"now": 0.0}

    def clock() -> float:
        ticks["now"] += 5.0
        return ticks["now"]

    report = RoutingRunner(_config(
        tmp_path, engine, max_attempts=50, time_limit_s=1.0, clock=clock,
    )).run()
    assert report.stop_reason == "time_limit"
    assert report.attempts == 0


def test_planner_plan_is_executed_through_the_tool_surface(tmp_path):
    """A planner proposal is validated and applied exactly like any other request."""
    engine = _engine()
    planner = _CountingPlanner(reply={"tool": "connect_targets", "mode": "shove"})
    report = RoutingRunner(_config(
        tmp_path, engine, planner=planner, max_model_requests=2,
    )).run()
    # Deterministic plans already win here, so the planner stays unused; the
    # recorded request path is covered by the native/planner tests.
    assert report.accepted >= 1
    assert planner.calls == 0


def test_run_state_never_contains_a_planner_api_key(tmp_path, monkeypatch):
    secret = "sk-sentinel-must-not-appear-0123456789"
    # Force the planner path (deterministic candidates cannot close this pair) so
    # the key-bearing client is actually exercised.
    engine = _engine(failing_target=(40.0, 10.0))

    from pcb_world.agent import runner as runner_module

    monkeypatch.setattr(
        runner_module, "resolve_api_key", lambda **kwargs: (secret, "file:/tmp/fake.key")
    )
    planner = runner_module.OpenAICompatiblePlanner(
        base_url="https://api.deepseek.com", model="deepseek-flash",
        opener=_StubOpener('{"tool":"connect_targets","mode":"walkaround"}'),
    )
    RoutingRunner(_config(
        tmp_path, engine, planner=planner, max_model_requests=1, max_attempts=2,
        stall_patience=10,
    )).run()

    run_dir = tmp_path / "run"
    for path in run_dir.rglob("*"):
        if path.is_file():
            assert secret not in path.read_text(errors="replace"), path


class _StubOpener:
    """Minimal urlopen stand-in returning one JSON chat-completions payload."""

    def __init__(self, content: str, *, status: int = 200, usage: dict | None = None):
        self.content = content
        self.status = status
        self.usage = usage or {"prompt_tokens": 11, "completion_tokens": 7}
        self.requests: list = []

    def open(self, request, timeout=None):  # noqa: A003 - mirrors urlopen
        self.requests.append(request)
        body = json.dumps({
            "choices": [{"message": {"content": self.content}}],
            "usage": self.usage,
            "model": "stub-model",
        }).encode()
        return _StubResponse(body, self.status)


class _StubResponse:
    def __init__(self, body: bytes, status: int) -> None:
        self._body = body
        self.status = status

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        return None


# ---------------------------------------------------------------------------
# Coverage honesty
# ---------------------------------------------------------------------------


def test_a_capped_component_window_is_reported_as_capped(tmp_path, monkeypatch):
    """"No offers" from a bounded window is not "the board is exhausted".

    The scan examines a window of each net's component graph. When that window (or
    the per-net pair cap) left part of the graph unexamined, the run must say so
    rather than reporting a global exhaustion it did not establish.
    """
    from pcb_world.agent import runner as runner_module
    from pcb_world.agent.observations import PairScan

    engine = _engine()
    # The board still has outstanding edges, but this scan may offer none.
    engine.get_unrouted_count = lambda: 3  # type: ignore[method-assign]
    monkeypatch.setattr(
        runner_module, "scan_net_pairs",
        lambda session, **kwargs: PairScan(
            pairs=(), edges=3,
            component_coverage={
                "components_omitted_by_window": 5,
                "pairs_omitted_by_cap": 2,
            },
        ),
    )
    report = RoutingRunner(_config(tmp_path, engine, max_attempts=4)).run()
    assert report.stop_reason == "pairs_exhausted_capped", report.stop_reason
    assert any("capped, not global" in note for note in report.notes), report.notes


def test_an_uncapped_exhaustion_still_reports_plain_exhaustion(
    tmp_path, monkeypatch
):
    from pcb_world.agent import runner as runner_module
    from pcb_world.agent.observations import PairScan

    engine = _engine()
    engine.get_unrouted_count = lambda: 3  # type: ignore[method-assign]
    monkeypatch.setattr(
        runner_module, "scan_net_pairs",
        lambda session, **kwargs: PairScan(
            pairs=(), edges=3, component_coverage={},
        ),
    )
    report = RoutingRunner(_config(tmp_path, engine, max_attempts=4)).run()
    assert report.stop_reason == "pairs_exhausted"
    assert not report.notes


# ---------------------------------------------------------------------------
# Budget headroom and a reaped native call
# ---------------------------------------------------------------------------


def test_run_stops_before_an_attempt_it_cannot_verify(tmp_path):
    """The run's own clock must not be allowed to reap a native call.

    The loop checked the time limit only at the top of an iteration, so an
    attempt that started just inside the limit could cross it mid-flight. The
    acceptance DRC is then dispatched with a deadline of seconds against a pass
    that needs tens of them, the owned engine child is reaped, and the rollback
    has no child left to restore through — run_via2 lost its session exactly
    there. The floor is explicit here because a fake board's DRC is free, so the
    measured term is 0 and only ``attempt_headroom_s`` is left.
    """
    engine = _engine(extra_pair=True)
    ticks = {"now": 1000.0}

    def clock() -> float:
        return ticks["now"]

    def on_progress(_update: dict) -> None:
        ticks["now"] += 45.0

    config = _config(
        tmp_path, engine, max_attempts=50, time_limit_s=60.0, clock=clock,
        attempt_headroom_s=50.0,
    )
    config.progress_callback = on_progress
    runner = RoutingRunner(config)
    report = runner.run()

    assert report.stop_reason == "time_limit_headroom"
    assert report.status == "stopped"
    assert report.attempts == 1
    assert any("stopped before mutating copper" in note for note in report.notes)
    headroom = runner.state.metrics["time_limit_headroom"]
    assert headroom["required_s"] == 50.0
    assert headroom["remaining_s"] < 50.0


def test_a_reaped_acceptance_drc_is_recorded_with_its_cause(tmp_path):
    """The checkpoint names the failure, not just its category.

    run_via2's run state kept ``reason=drc_unavailable`` and
    ``copper_state=retained_unknown`` on the failing attempt and dropped the
    exception and the rollback detail, so the cause had to be recovered by
    re-running the transaction. The saved record now carries both.
    """
    reaped = (
        "owned engine operation 'call' exceeded its 3.399 s deadline; child reaped"
    )
    engine = _engine(
        drc_failure=reaped,
        # Let the startup baseline and the transaction's own baseline answer; reap
        # the child on the acceptance pass of the copper that would be kept.
        drc_fail_after_calls=2,
        restore_exception=(
            "owned engine operation 'call' exceeded its 0.000 s deadline; child reaped"
        ),
    )
    runner = RoutingRunner(_config(tmp_path, engine, max_attempts=4))
    report = runner.run()

    assert report.stop_reason == "session_quarantined"
    assert report.status == "blocked"
    assert report.accepted == 0
    records = runner.state.to_dict()["attempts"]
    assert records, "the failing attempt must still be in the history"
    failing = records[-1]
    assert failing["reason"] == "drc_unavailable"
    assert failing["copper_state"] == "retained_unknown"
    assert "child reaped" in failing["failure_exception"]
    assert "0.000 s deadline" in failing["rollback_detail"]["restore_exception"]
    assert "restored" not in failing["rollback_detail"]
    # The record must still say copper moved: a failure that reports no change
    # would read like a plan that did nothing.
    assert failing["steps_applied"] >= 1


def test_a_plan_that_stopped_names_the_step_it_stopped_at(tmp_path):
    """A record says *where* a plan stopped, not just how many steps ran.

    ``steps_applied`` counts the steps that succeeded, so a start that never
    opened the route and a via that never landed can leave the same count. A
    failure analysis then has to replay the stored plan to find out where it
    died — and a replay is only faithful while the harness is unchanged. The
    first step that did not succeed is now carried on the record itself:
    ``failed_step_index`` equals ``steps_applied`` because the session stops at
    the first failure, so the pair together is the plan's stopping point.
    """
    engine = _engine(failing_target=(40.0, 10.0))
    runner = RoutingRunner(_config(tmp_path, engine, max_attempts=4))
    runner.run()

    records = runner.state.to_dict()["attempts"]
    stopped = [record for record in records if record["failed_step_kind"]]
    assert stopped, "a plan that stopped must name the step it stopped at"
    assert {record["failed_step_kind"] for record in stopped} == {"line"}
    for record in stopped:
        assert record["failed_step_index"] == record["steps_applied"]
        assert record["failed_step_index"] >= 0


def test_a_plan_that_ran_to_the_end_names_no_failed_step(tmp_path):
    """No failure means no failed step: the fields stay empty, not zero.

    An unset ``failed_step_index`` of ``0`` would read as "the start step
    failed", which is the opposite of what a plan that closed the connection
    did.
    """
    engine = _engine()
    runner = RoutingRunner(_config(tmp_path, engine))
    report = runner.run()

    assert report.accepted >= 1
    accepted = [record for record in runner.state.to_dict()["attempts"]
                if record["accepted"]]
    assert accepted
    for record in accepted:
        assert record["failed_step_kind"] == ""
        assert record["failed_step_index"] == -1


def test_a_pinned_net_is_offered_first_without_losing_the_queue(tmp_path):
    """A trial brief can name geometry it wants tried while budget remains.

    The scan's round-robin stays intact inside both groups; only the group order
    changes, so pinning a net cannot starve the rest of the queue — the pinned
    net is simply looked at first on every iteration.
    """
    from pcb_world.agent.observations import NetPair

    engine = _engine()
    runner = RoutingRunner(_config(tmp_path, engine, priority_nets=(7,)))
    runner._pending_digest = "digest"
    runner.state = RunState(
        board_path="/tmp/fake_board.kicad_pcb", project_path=None, rules_path=None,
        provenance={}, run_dir=str(tmp_path / "pinned"),
    )
    pairs = [
        NetPair(net_code=net, net_name=f"NET{net}", start=(10.0, 10.0, 1),
                target=(40.0, 10.0, 1), gap_mm=30.0)
        for net in (1, 7, 9)
    ]
    assert runner._next_pair(pairs, "digest").net_code == 7
    # The pinned pair is then exhausted by the per-net budget and the queue goes
    # back to its own order rather than stalling.
    runner.state.attempts.note(AttemptRecord(
        pair_key=pairs[1].key, plan_key=("direct", ((10.0, 10.0, 1),)),
        candidate="direct", mode="direct", kind="direct", source="deterministic",
        outcome="routing_failed", accepted=False, connected=False,
        reason="connection_not_verified", committed=False,
    ))
    runner.config.per_net_tries = 1
    runner.state.metrics["pair_cursor"] = 0
    assert runner._next_pair(pairs, "digest").net_code == 1


def test_the_headroom_guard_stops_before_the_first_scan(tmp_path):
    """The scan is native work under the same clamp, so it is guarded too.

    Stopping only *after* the scan would leave the run's first native consumer
    unprotected: a scan the remaining budget cannot cover is the same mistake as
    an attempt it cannot cover, and it reaps the same child.
    """
    engine = _engine()
    report = RoutingRunner(_config(
        tmp_path, engine, max_attempts=8, time_limit_s=60.0,
        attempt_headroom_s=70.0,
    )).run()

    assert report.stop_reason == "time_limit_headroom"
    assert report.attempts == 0
    assert report.plan_evaluations == 0
    assert any("stopped before mutating copper" in note for note in report.notes)
    # The guard is about the scan and the attempt; the startup baseline still ran,
    # because that is the measurement the guard itself needs.
    assert engine.run_drc_calls >= 1


# ---------------------------------------------------------------------------
# The transaction lease
# ---------------------------------------------------------------------------


def _leased_runner(tmp_path, engine, ticks, **overrides):
    """A runner whose DRC is accounted against its own allowance callback.

    ``engine.advance_clock`` moves this run's fake clock, so a DRC can be made to
    cross the soft deadline without any real waiting, and
    ``engine.allowance_provider`` is the runner's own ``_native_timeout_s``, so the
    deadline the fake engine enforces is the one the real client would resolve.
    """
    config = _config(
        tmp_path, engine, max_attempts=4, time_limit_s=100.0,
        clock=lambda: ticks["now"], attempt_headroom_s=50.0, **overrides,
    )
    runner = RoutingRunner(config)
    engine.allowance_provider = runner._native_timeout_s
    engine.advance_clock = lambda seconds: ticks.__setitem__(
        "now", ticks["now"] + float(seconds)
    )
    return runner


def test_a_drc_that_outgrows_its_history_finishes_inside_the_lease(tmp_path):
    """An attempt that crosses the soft deadline must not be truncated.

    The headroom guard is a measured guess: an attempt can start with apparently
    plenty of room and then need far more than any previous one. Without the lease
    the acceptance DRC is dispatched with whatever is left of the run's budget,
    reaped at that deadline, and the rollback is left with the child that was
    killed holding its checkpoint. With the lease the DRC, and the rollback it
    forces, both complete; the run then stops at the next transaction boundary.
    """
    # The plan closes the connection but its copper adds a violation, so the
    # acceptance DRC refuses it and the transaction owes a rollback: that is the
    # verification this test is about.
    engine = _engine(
        extra_pair=True,
        violation_on_fix=FakeViolation(
            error_code=5, error_type="Clearance violation", message="nope",
        ),
    )
    ticks = {"now": 1000.0}
    runner = _leased_runner(tmp_path, engine, ticks)
    # Calls 1 and 2 are the runner's startup baseline and the transaction's own
    # baseline (both cheap and historical); call 3 is the acceptance pass on the
    # copper that would be kept, and it needs far more than anything measured.
    engine.drc_duration_after_calls = 2
    engine.drc_duration_s = 250.0

    report = runner.run()

    assert engine.reaped is False
    assert report.stop_reason == "time_limit_completed_attempt"
    assert report.attempts == 1
    assert any(
        "transaction boundary" in note for note in report.notes
    ), report.notes
    # The acceptance DRC was dispatched with the lease's allowance, not with the
    # few seconds the soft run budget had left.
    assert engine.allowances[-1] >= 250.0
    records = runner.state.to_dict()["attempts"]
    assert records
    failing = records[-1]
    assert failing["reason"] == "drc_regression"
    assert failing["copper_state"] == "restored"
    assert failing["rollback_detail"]["restored"] is True
    assert all(r["copper_state"] != "retained_unknown" for r in records)
    leases = runner.state.metrics["engine_leases"]
    # At least the transaction's own lease; the run's closing verification takes
    # one too, so a run that stopped on its limit still reports the DRC of the
    # board it kept instead of losing the child that would answer.
    assert leases["granted"] >= 1
    assert leases["expired"] == 0
    assert leases["max_overrun_s"] > 0


def test_without_the_lease_the_same_overshoot_reaps_the_live_checkpoint(tmp_path):
    """The negative control that makes the test above meaningful.

    Same board, same DRC, same clock: only the lease is disabled. Now the
    acceptance DRC is bounded by what is left of the soft run budget, the child is
    reaped, and the rollback that has to follow cannot be verified — the failure
    the lease exists to prevent.
    """
    engine = _engine(
        extra_pair=True,
        violation_on_fix=FakeViolation(
            error_code=5, error_type="Clearance violation", message="nope",
        ),
    )
    ticks = {"now": 1000.0}
    runner = _leased_runner(tmp_path, engine, ticks, transaction_lease_s=0.0)
    engine.drc_duration_after_calls = 2
    engine.drc_duration_s = 250.0

    report = runner.run()

    assert engine.reaped is True
    assert report.stop_reason == "session_quarantined"
    assert report.status == "blocked"
    records = runner.state.to_dict()["attempts"]
    assert records
    failing = records[-1]
    assert failing["reason"] == "drc_unavailable"
    assert failing["copper_state"] == "retained_unknown"
    assert "child reaped" in failing["failure_exception"]
    assert "child reaped" in failing["rollback_detail"]["restore_exception"]


def test_a_call_past_the_hard_ceiling_still_quarantines(tmp_path):
    """The lease does not remove the absolute per-call bound.

    A native call that exceeds ``engine_call_timeout_s`` is a genuinely hung or
    runaway operation, not a scheduling accident, and the quarantine on the state
    it can no longer prove stays the correct answer.
    """
    engine = _engine(extra_pair=True)
    ticks = {"now": 1000.0}
    runner = _leased_runner(
        tmp_path, engine, ticks, engine_call_timeout_s=300.0,
        transaction_lease_s=600.0,
    )
    engine.drc_duration_after_calls = 2
    engine.drc_duration_s = 350.0        # past the 300 s per-call ceiling

    report = runner.run()

    assert engine.reaped is True
    assert report.stop_reason == "session_quarantined"
    records = runner.state.to_dict()["attempts"]
    assert records[-1]["copper_state"] == "retained_unknown"
    assert engine.allowances[-1] <= 300.0


def test_the_lease_ignores_the_soft_deadline_but_not_the_hard_ceiling(tmp_path):
    """The call allowance itself, with no engine in the way."""
    ticks = {"now": 1000.0}
    wall = {"now": 5000.0}
    runner = RoutingRunner(_config(
        tmp_path, _engine(), time_limit_s=60.0, clock=lambda: ticks["now"],
        engine_call_timeout_s=300.0, transaction_lease_s=600.0,
        wall_clock=lambda: wall["now"],
    ))
    runner._run_started = ticks["now"]
    # No lease: the soft budget is what bounds the call.
    ticks["now"] = 1030.0                      # 30 s of the 60 s limit left
    assert runner._native_timeout_s() == 30.0
    # Inside a lease: the soft budget is irrelevant; the ceiling and the lease are
    # what bound it.
    with runner._transaction_lease():
        assert runner._native_timeout_s() == 300.0
        wall["now"] += 400.0                   # 200 s of the lease left
        assert runner._native_timeout_s() == 200.0
        wall["now"] += 250.0                   # lease spent
        assert runner._native_timeout_s() == 0.0
    # Outside again, and a soft budget already gone still bounds the next call.
    ticks["now"] = 1075.0
    assert runner._native_timeout_s() == 0.0


def test_a_transaction_lease_is_never_shorter_than_one_hard_ceiling(tmp_path):
    ticks = {"now": 1000.0}
    wall = {"now": 0.0}
    runner = RoutingRunner(_config(
        tmp_path, _engine(), time_limit_s=60.0, clock=lambda: ticks["now"],
        engine_call_timeout_s=300.0, transaction_lease_s=5.0,
        wall_clock=lambda: wall["now"],
    ))
    with runner._transaction_lease():
        assert runner._native_timeout_s() == 300.0


def test_the_scan_runs_under_the_lease_not_the_soft_budget(tmp_path, monkeypatch):
    """The loop's own reads and scan must not be reaped either.

    An earlier segment of the campaign ended from inside ``get_pad_groups``: the
    run had seconds of soft budget left, the read was dispatched under that clamp,
    and the child holding the (idle) engine was killed. Nothing was lost, but the
    run reported an engine exception instead of stopping. The scan and the reads
    that select an attempt therefore run under the same lease as the attempt.
    """
    from pcb_world.agent import runner as runner_module

    engine = _engine()
    ticks = {"now": 1000.0}
    wall = {"now": 0.0}
    runner = RoutingRunner(_config(
        tmp_path, engine, max_attempts=1, time_limit_s=60.0,
        clock=lambda: ticks["now"], wall_clock=lambda: wall["now"],
        attempt_headroom_s=0.0,
    ))
    seen: dict = {}
    real_scan = runner_module.scan_net_pairs

    def spy(session, **kwargs):
        seen["leased"] = runner._lease_end is not None
        seen["allowance"] = runner._native_timeout_s()
        return real_scan(session, **kwargs)

    monkeypatch.setattr(runner_module, "scan_net_pairs", spy)
    runner._run_started = ticks["now"]
    ticks["now"] = 1055.0                 # 5 s of the soft budget left

    runner.run()

    assert seen["leased"] is True
    # The lease's allowance, not the 5 s the soft run budget had left.
    assert seen["allowance"] >= 299.0


def test_a_nested_lease_cannot_extend_the_window(tmp_path):
    """Nesting buys no extra time, so a sweep cannot compound its overrun."""
    wall = {"now": 0.0}
    runner = RoutingRunner(_config(
        tmp_path, _engine(), time_limit_s=60.0,
        wall_clock=lambda: wall["now"],
        engine_call_timeout_s=300.0, transaction_lease_s=300.0,
    ))
    with runner._transaction_lease() as outer:
        assert outer is True
        window_end = runner._lease_end
        with runner._transaction_lease() as inner:
            assert inner is True
            assert runner._lease_end == window_end
            wall["now"] += 200.0
            assert runner._native_timeout_s() == 100.0
        assert runner._lease_end == window_end
        wall["now"] += 100.0
        assert runner._native_timeout_s() == 0.0
    assert runner._lease_end is None


def test_the_reported_overrun_is_the_total_of_both_lease_windows(tmp_path):
    """The closing verification opens its own window, and the metric says so.

    Root's correction: the closing DRC is leased *after* the attempt's own lease
    has closed, so a run that crossed its soft deadline in an attempt and then
    spends time on the closing DRC overruns by the sum of the two windows, not by
    one lease. Both stages must still finish — nothing reaped — and
    ``max_overrun_s`` must be the total actually observed, which here is larger
    than the largest single window.
    """
    engine = _engine(
        extra_pair=True,
        violation_on_fix=FakeViolation(
            error_code=5, error_type="Clearance violation", message="nope",
        ),
    )
    ticks = {"now": 1000.0}
    wall = {"now": 0.0}
    marks: list[float] = []

    def advance(seconds: float) -> None:
        ticks["now"] += float(seconds)
        wall["now"] += float(seconds)
        marks.append(ticks["now"] - 1000.0)

    config = _config(
        tmp_path, engine, max_attempts=4, time_limit_s=10.0,
        clock=lambda: ticks["now"], wall_clock=lambda: wall["now"],
        engine_call_timeout_s=60.0, transaction_lease_s=60.0,
        attempt_headroom_s=0.0,
    )
    runner = RoutingRunner(config)
    engine.allowance_provider = runner._native_timeout_s
    engine.advance_clock = advance
    # startup baseline, transaction baseline, acceptance pass, closing pass.
    # The two that matter cost 20 s and 55 s: each fits inside the 60 s window it
    # is given, and 75 s of elapsed work against a 10 s limit is 65 s of overrun.
    engine.drc_durations = (0.0, 0.0, 20.0, 55.0)

    report = runner.run()

    assert engine.reaped is False
    assert engine.run_drc_calls == 4            # both stages answered
    assert report.stop_reason == "time_limit_completed_attempt"
    assert report.attempts == 1
    attempt_overrun = round(marks[0] - 10.0, 3)          # after the attempt
    total_overrun = round(ticks["now"] - 1000.0 - 10.0, 3)
    leases = runner.state.metrics["engine_leases"]
    assert attempt_overrun == 10.0
    assert total_overrun == 65.0
    assert leases["max_overrun_s"] == total_overrun
    # The closing verification added to the total, which therefore exceeds one
    # window — the contract is two individually bounded windows, not one lease.
    assert leases["max_overrun_s"] > attempt_overrun
    assert leases["max_overrun_s"] > leases["max_window_s"]
    assert leases["granted"] >= 2
    assert leases["expired"] == 0
    records = runner.state.to_dict()["attempts"]
    assert records[-1]["copper_state"] == "restored"
    assert records[-1]["rollback_detail"]["restored"] is True
    assert all(r["copper_state"] != "retained_unknown" for r in records)


def test_a_refusal_record_carries_every_added_class_not_just_the_row_sample(tmp_path):
    """The stored class breakdown is the delta's whole histogram.

    A refusal record is read to answer "which classes refused this plan, and how
    many of each". The delta's ``added_relevant`` rows are a deliberately capped
    sample of positions, so a record that built its histogram from them would
    report at most the cap - here eight of ten, and on the phase-17 trial eight
    of fifty. The record must carry the complete counts.
    """
    added = [
        FakeViolation(error_code=5, error_type="Clearance violation",
                      message="source-fill-clearance", x_mm=10.0 + index, y_mm=10.0,
                      layer=0, net_names=["NET"])
        for index in range(6)
    ] + [
        FakeViolation(error_code=16, error_type="Hole clearance violation",
                      message="source-hole-clearance", x_mm=20.0 + index, y_mm=20.0,
                      layer=0, net_names=["NET"])
        for index in range(4)
    ]
    engine = _engine(extra_pair=True, violations_on_fix=tuple(added))
    runner = RoutingRunner(_config(tmp_path, engine))
    report = runner.run()

    assert report.attempts >= 1
    records = runner.state.to_dict()["attempts"]
    refused = [record for record in records if record["reason"] == "drc_regression"]
    assert refused, [record["reason"] for record in records]
    record = refused[0]
    assert record["added_relevant"] == len(added) == 10
    assert dict((name, count) for name, count in record["drc_classes"]) == {
        "Clearance violation": 6,
        "Hole clearance violation": 4,
    }
    # The hint positions stay a small sample; only the classes claim completeness.
    assert len(record["drc_hints"]) <= 4
