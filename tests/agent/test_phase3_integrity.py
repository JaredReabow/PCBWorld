"""Phase-3 review findings: budgets, binding, artifact promotion, fair queue.

These cover defects the phase-2 review reproduced: planner totals that reset on
resume, a request ceiling that could be exceeded inside one call, a promoted
artifact that could be overwritten by a half-written save, and a pair queue that
offered only the first net's shortest connection.
"""

from __future__ import annotations

import json
import urllib.error
from pathlib import Path

import pytest

import pcb_world.agent.runner as runner_module
import pcb_world.engine.router_client as router_client
from pcb_world.agent.observations import LayerResolver, NetPair, PairScan, scan_net_pairs
from pcb_world.agent.runner import (
    CATEGORY_CANCELLED,
    OpenAICompatiblePlanner,
    PlannerError,
    PlannerReply,
    RoutingRunner,
    RunnerConfig,
    resolve_inputs,
)
from pcb_world.agent.scheduler import (
    AttemptHistory,
    AttemptRecord,
    RunState,
    attempt_plan_key,
    build_provenance,
)
from pcb_world.agent.session import AgentSession
from tests.agent.conftest import fake_artifact_verifier
from pcb_world.engine.wire import RatsnestEdge

from tests.agent.fake_engine import FakeEngine, FakeViolation, PadInfo, TrackInfo
from tests.agent.test_runner_scripted import _config, _engine


class _CountingProvider:
    """A planner that counts its own HTTP attempts, like the real client does."""

    name = "counting-provider"

    def __init__(self, replies: list[dict] | None = None) -> None:
        self.request_count = 0
        self.replies = list(replies or [])

    def propose(self, *, observation, tool_schema, previous_attempts):
        self.request_count += 1
        request = self.replies.pop(0) if self.replies else {
            "tool": "connect_targets", "mode": "walkaround",
        }
        return PlannerReply(request=dict(request), model="counting")


class _JsonResponse:
    status = 200

    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.payload


def test_resume_across_planner_instances_keeps_request_totals(tmp_path):
    """A resumed run adds to the checkpoint's totals instead of replacing them."""
    # Deterministic plans must not close this pair, or the planner is never asked.
    engine = _engine(failing_target=(40.0, 10.0))
    first_planner = _CountingProvider()
    # One deterministic candidate per attempt: the contract under test is the
    # planner tally across a resume, not how many deterministic plans exist.
    first = RoutingRunner(_config(
        tmp_path, engine, planner=first_planner, max_model_requests=3,
        max_attempts=1, candidate_limit=1, name="run",
    )).run()
    assert first.model_usage["requests"] == first_planner.request_count == 1

    state = RunState.load(
        str(tmp_path / "run" / "run_state.json"),
        expected_provenance=build_provenance(
            board_path="/tmp/fake_board.kicad_pcb", project_path=None, rules_path=None,
        ),
    )
    second_planner = _CountingProvider()
    second = RoutingRunner(
        _config(tmp_path, engine, planner=second_planner, max_model_requests=5,
                max_attempts=2, candidate_limit=1, name="run"),
        state=state,
    ).run()

    # One from the first run plus this instance's own attempts - not a reset to 1.
    assert second.model_usage["requests"] == 1 + second_planner.request_count
    assert second.model_usage["requests"] >= 2


def test_direct_api_resume_cannot_bypass_provenance(tmp_path):
    engine = _engine(failing_target=(40.0, 10.0))
    RoutingRunner(_config(tmp_path, engine, name="direct", max_attempts=1)).run()
    state = RunState.load(str(tmp_path / "direct" / "run_state.json"))
    state.provenance["engine_cpp_hash"] = "tampered"
    with pytest.raises(ValueError, match="provenance changed"):
        RoutingRunner(_config(
            tmp_path, _engine(), name="direct", max_attempts=2,
        ), state=state).run()


def test_missing_checkpoint_artifact_fails_closed(tmp_path):
    engine = _engine(failing_target=(40.0, 10.0))
    RoutingRunner(_config(tmp_path, engine, name="missing", max_attempts=1)).run()
    run_dir = tmp_path / "missing"
    state = RunState.load(str(run_dir / "run_state.json"))
    # Remove the pointer so a missing legacy/checkpoint path cannot be masked by
    # the authoritative generation reconciliation path.
    import shutil
    shutil.rmtree(run_dir / "artifacts")
    state.best_board_path = str(run_dir / "absent.kicad_pcb")
    with pytest.raises(ValueError, match="names a best artifact that is missing"):
        RoutingRunner(_config(
            tmp_path, _engine(), name="missing", max_attempts=2,
        ), state=state).run()


def test_legacy_checkpoint_is_migrated_before_resume(tmp_path):
    engine = _engine(failing_target=(40.0, 10.0))
    RoutingRunner(_config(tmp_path, engine, name="legacy", max_attempts=1)).run()
    run_dir = tmp_path / "legacy"
    state_path = run_dir / "run_state.json"
    state = RunState.load(str(state_path))
    old_generation = Path(state.best_board_path).parent
    legacy_board = run_dir / "best_board.kicad_pcb"
    legacy_board.write_bytes((old_generation / "board.kicad_pcb").read_bytes())
    import shutil
    for old_name, legacy_name in (
        ("board.kicad_pro", "best_board.kicad_pro"),
        ("board.kicad_dru", "best_board.kicad_dru"),
    ):
        source = old_generation / old_name
        if source.exists():
            shutil.copyfile(source, run_dir / legacy_name)
    from pcb_world.agent.scheduler import sha256_file
    state.best_board_path = str(legacy_board)
    state.best_board_sha256 = sha256_file(str(legacy_board))
    shutil.rmtree(run_dir / "artifacts")
    state.save(str(state_path))

    resumed = RoutingRunner(
        _config(tmp_path, engine, name="legacy", max_attempts=2), state=state,
    ).run()
    assert resumed.best_board_path
    assert Path(resumed.best_board_path).parent.parent.name == "artifacts"
    pointer = json.loads((run_dir / "artifacts" / "accepted_artifact.json").read_text())
    assert pointer["manifest"]["saved_artifact_verification"]["test_double"] is True


def test_planner_budget_is_enforced_inside_the_retry_loop(tmp_path):
    """One propose() may not spend more HTTP attempts than the run has left."""
    calls: list[dict] = []

    class _ServerErrorOpener:
        def open(self, request, timeout=None):  # noqa: A003 - mirrors urlopen
            calls.append(json.loads(request.data.decode()))
            raise urllib.error.HTTPError(
                request.full_url, 503, "server error", {}, None  # type: ignore[arg-type]
            )

    key_file = tmp_path / "key"
    key_file.write_text("test-key-not-real")
    planner = OpenAICompatiblePlanner(
        base_url="https://api.deepseek.com", model="stub",
        api_key_file=str(key_file), max_retries=5, opener=_ServerErrorOpener(),
    )
    planner.set_budget(requests_remaining=2)
    with pytest.raises(PlannerError) as exc:
        planner.propose(observation={}, tool_schema={}, previous_attempts=[])

    assert len(calls) == 2, "the request budget must stop the retry loop"
    assert planner.request_count == 2
    assert exc.value.retryable is False


@pytest.mark.parametrize("terminal", ["503", "timeout"])
def test_malformed_response_usage_survives_terminal_retry_failure(tmp_path, terminal):
    key_file = tmp_path / "planner.key"
    key_file.write_text("local-test-key")
    calls = 0

    class _Opener:
        def open(self, request, timeout=None):  # noqa: A003
            nonlocal calls
            calls += 1
            if calls == 1:
                return _JsonResponse({"choices": [{"message": {"content": "not JSON"}}],
                                      "usage": {"prompt_tokens": 12,
                                                "completion_tokens": 7}})
            if terminal == "503":
                raise urllib.error.HTTPError(
                    request.full_url, 503, "retry failed", {}, None
                )
            raise TimeoutError("terminal timeout")

    planner = OpenAICompatiblePlanner(
        base_url="https://api.deepseek.com", model="stub",
        api_key_file=str(key_file), max_retries=2, opener=_Opener(),
    )
    with pytest.raises(PlannerError) as exc:
        planner.propose(observation={}, tool_schema={}, previous_attempts=[])
    assert exc.value.detail["usage"]["prompt_tokens"] == 12
    assert exc.value.detail["usage"]["completion_tokens"] == 7
    assert exc.value.detail["usage_known"] is False


def test_planner_deadline_clamps_transport_and_reserves_before_dispatch(tmp_path):
    key_file = tmp_path / "planner.key"
    key_file.write_text("local-test-key")
    observed = {}
    reservations = []

    class _Opener:
        def open(self, request, timeout=None):  # noqa: A003
            observed["timeout"] = timeout
            observed["max_tokens"] = json.loads(request.data)["max_tokens"]
            raise TimeoutError("deterministic timeout")

    planner = OpenAICompatiblePlanner(
        base_url="https://api.deepseek.com", model="stub", api_key_file=str(key_file),
        timeout_s=30, max_tokens=500, max_retries=1, opener=_Opener(),
    )
    planner.set_budget(
        requests_remaining=1, tokens_remaining=40, deadline=0.02,
        clock=lambda: 0.0, on_request_reserved=lambda: reservations.append("reserved"),
    )
    with pytest.raises(PlannerError):
        planner.propose(observation={}, tool_schema={}, previous_attempts=[])
    assert observed["timeout"] <= 0.02
    assert observed["max_tokens"] == 40
    assert reservations == ["reserved"]


def test_planner_reply_for_another_connection_is_refused(tmp_path):
    """A reply that plans a different pair is not this connection's attempt."""
    engine = _engine(failing_target=(40.0, 10.0))
    planner = _CountingProvider([
        {"tool": "connect_targets", "start": [0.0, 0.0, 1],
         "target": [1.0, 1.0, 1], "mode": "walkaround"},
    ])
    RoutingRunner(_config(
        tmp_path, engine, planner=planner, max_model_requests=2, max_attempts=2,
        stall_patience=10,
    )).run()

    state = json.loads((tmp_path / "run" / "run_state.json").read_text())
    out_of_scope = [r for r in state["attempts"] if r["outcome"] == "planner_out_of_scope"]
    assert out_of_scope, "an off-target planner reply must be recorded as out of scope"
    assert "not the requested anchor" in out_of_scope[0]["reason"]


def test_two_coordinate_waypoints_are_recorded_without_crashing():
    """A planner may emit (x, y) waypoints; recording them must not raise."""
    key = attempt_plan_key("walkaround", [[1.0, 2.0], [3.0, 4.0, 1]])
    assert key == ("walkaround", ((1.0, 2.0, None), (3.0, 4.0, 1)))
    record = AttemptRecord(
        pair_key=(1,), plan_key=key, candidate="planner", mode="walkaround",
        kind="planner", source="planner", outcome="ok", accepted=False,
        connected=False,
    )
    assert record.to_dict()["plan_key"][1] == [[1.0, 2.0, None], [3.0, 4.0, 1]]


def test_artifact_sidecars_are_copied_from_the_inputs(tmp_path):
    """The promoted artifact ships the input sidecars, byte-for-byte."""
    board = tmp_path / "board.kicad_pcb"
    board.write_text("(kicad_pcb)")
    project = tmp_path / "board.kicad_pro"
    project.write_text('{"meta": {"filename": "board.kicad_pro"}}')
    rules = tmp_path / "board.kicad_dru"
    rules.write_text('(rule "custom" (constraint clearance (min 0.31mm)))')

    inputs = resolve_inputs(str(board))
    assert inputs.project_path == str(project)
    assert inputs.rules_path == str(rules)

    engine = _engine()
    report = RoutingRunner(RunnerConfig(
        board_path=str(board), run_dir=str(tmp_path / "run"),
        project_path=str(project), rules_path=str(rules),
        max_attempts=1, engine_factory=lambda path: engine,
        session_factory=lambda eng, path: AgentSession(eng, board_path=path),
        verify_artifacts=False,
        artifact_verifier=fake_artifact_verifier,
    )).run()

    assert report.best_board_path
    generation = Path(report.best_board_path).parent
    assert (generation / "board.kicad_pro").read_text() == project.read_text()
    assert (generation / "board.kicad_dru").read_text() == rules.read_text()
    manifest = json.loads((generation / "manifest.json").read_text())
    assert manifest["files"]["board.kicad_pro"]
    assert manifest["files"]["board.kicad_dru"]
    assert manifest["verification"]["project_matches_input"] is True
    assert manifest["verification"]["rules_matches_input"] is True


def test_inferred_sidecars_are_resolved_hashed_and_copied(tmp_path):
    """Omitting --project/--rules must not mean hashing nothing."""
    board = tmp_path / "inferred.kicad_pcb"
    board.write_text("(kicad_pcb)")
    (tmp_path / "inferred.kicad_pro").write_text("{}")
    (tmp_path / "inferred.kicad_dru").write_text(
        '(rule "x" (constraint clearance (min 0.2mm)))')

    engine = _engine()
    RoutingRunner(RunnerConfig(
        board_path=str(board), run_dir=str(tmp_path / "run"), max_attempts=1,
        engine_factory=lambda path: engine,
            session_factory=lambda eng, path: AgentSession(eng, board_path=path),
            verify_artifacts=False,
            artifact_verifier=fake_artifact_verifier,
    )).run()

    state = json.loads((tmp_path / "run" / "run_state.json").read_text())
    assert state["provenance"]["project_sha256"]
    assert state["provenance"]["rules_sha256"]
    pointer = json.loads((tmp_path / "run" / "artifacts" / "accepted_artifact.json").read_text())
    assert (tmp_path / "run" / "artifacts" / pointer["generation"] / "board.kicad_dru").exists()


def test_final_drc_regression_refuses_promotion(tmp_path):
    """A board that gained relevant findings is not promoted as the best board."""
    engine = _engine()
    plain = engine.run_drc
    calls = {"n": 0}

    def late_regression(rules_path: str = "") -> list:
        calls["n"] += 1
        findings = list(plain(rules_path))
        if calls["n"] >= 4:          # baseline + acceptance are clean; final is not
            findings.append(FakeViolation(
                error_code=5, error_type="Clearance violation",
                message="Clearance violation", x_mm=1.0, y_mm=1.0, layer=1,
                net_names=["A", "B"], item_a="one", item_b="two",
            ))
        return findings

    engine.run_drc = late_regression
    report = RoutingRunner(_config(
        tmp_path, engine, max_attempts=2, stall_patience=4,
    )).run()

    assert report.stop_reason == "final_drc_regression"
    assert report.status == "blocked"
    assert report.accepted == 0
    assert any("not promoted" in note for note in report.notes)
    run_dir = tmp_path / "run"
    pointer = json.loads((run_dir / "artifacts" / "accepted_artifact.json").read_text())
    assert pointer["manifest"]["progress"] == report.progress_before
    assert report.progress_after == report.progress_before
    assert report.drc["subject"] == "accepted_artifact"
    assert report.drc["artifact_generation"] == pointer["generation"]
    state = RunState.load(str(run_dir / "run_state.json"))
    assert state.best_progress == pointer["manifest"]["progress"]
    assert state.progress.best == pointer["manifest"]["progress"]
    assert state.metrics["progress_final"] == pointer["manifest"]["progress"]
    assert report.artifact_verification["active_artifact_progress"] == pointer["manifest"]["progress"]
    rejected = [r for r in state.attempts.records if r.final_disposition]
    assert rejected and all(r.final_disposition == "rejected_final_drc_regression" for r in rejected)
    assert state.metrics["rejected_candidate_drc"]["final_relevant"] > report.drc["final_relevant"]

    # Resume from the preserved generation: its progress tracker must compare
    # against that board so a genuine later improvement is still accepted.
    resume_engine = _engine()
    resumed = RoutingRunner(_config(
        tmp_path, resume_engine, max_attempts=4, stall_patience=4,
    ), state=state).run()
    assert resumed.status == "completed"
    assert resumed.progress_after["unrouted_edges"] == 0
    resumed_pointer = json.loads((run_dir / "artifacts" / "accepted_artifact.json").read_text())
    assert resumed_pointer["manifest"]["progress"] == resumed.progress_after
    resumed_state = RunState.load(str(run_dir / "run_state.json"))
    assert resumed_state.best_progress == resumed.progress_after
    assert resumed_state.progress.best["unrouted_edges"] == resumed.progress_after["unrouted_edges"]


def test_resumed_final_drc_rejection_keeps_prior_accepted_count(tmp_path):
    """A rejected resumed candidate does not erase accepted checkpoint history."""
    initial_engine = _engine(extra_pair=True)
    initial = RoutingRunner(_config(
        tmp_path, initial_engine, name="resumed-rejection", max_attempts=1,
    )).run()
    assert initial.accepted == 1
    run_dir = tmp_path / "resumed-rejection"
    state = RunState.load(str(run_dir / "run_state.json"))
    accepted_pointer = json.loads(
        (run_dir / "artifacts" / "accepted_artifact.json").read_text()
    )

    engine = _engine(extra_pair=True)
    plain = engine.run_drc
    calls = {"n": 0}

    def late_regression(rules_path: str = "") -> list:
        calls["n"] += 1
        findings = list(plain(rules_path))
        if calls["n"] >= 4:
            findings.append(FakeViolation(
                error_code=5, error_type="Clearance violation",
                message="Clearance violation", x_mm=1.0, y_mm=1.0, layer=1,
                net_names=["A", "B"], item_a="one", item_b="two",
            ))
        return findings

    engine.run_drc = late_regression
    report = RoutingRunner(_config(
        tmp_path, engine, name="resumed-rejection", max_attempts=4,
    ), state=state).run()

    assert report.stop_reason == "final_drc_regression"
    assert report.accepted == 1
    pointer = json.loads((run_dir / "artifacts" / "accepted_artifact.json").read_text())
    assert pointer["generation"] == accepted_pointer["generation"]
    assert pointer["manifest"]["progress"] == report.progress_after
    final_state = RunState.load(str(run_dir / "run_state.json"))
    assert final_state.best_progress == pointer["manifest"]["progress"]
    assert final_state.progress.best == pointer["manifest"]["progress"]
    assert final_state.metrics["progress_final"] == pointer["manifest"]["progress"]
    prior_records = [record for record in final_state.attempts.records if record.committed]
    assert len(prior_records) >= 2
    assert sum(
        record.accepted and not record.final_disposition for record in prior_records
    ) == 1
    assert any(
        record.final_disposition == "rejected_final_drc_regression"
        for record in final_state.attempts.records
    )


def test_saved_artifact_rejection_keeps_progress_bound_to_pointer(tmp_path):
    engine = _engine()
    calls = {"count": 0}

    def fail_candidate_verification(folder, request):
        calls["count"] += 1
        if calls["count"] == 1:
            return fake_artifact_verifier(folder, request)
        return {"ok": False, "reason": "injected candidate verification failure"}

    report = RoutingRunner(_config(
        tmp_path, engine, max_attempts=2,
        artifact_verifier=fail_candidate_verification,
    )).run()
    assert report.stop_reason == "artifact_verification_failed"
    run_dir = tmp_path / "run"
    pointer = json.loads((run_dir / "artifacts" / "accepted_artifact.json").read_text())
    accepted_progress = pointer["manifest"]["progress"]
    assert report.progress_after == accepted_progress
    assert report.best_board_path == str(
        run_dir / "artifacts" / pointer["generation"] / "board.kicad_pcb"
    )
    assert report.artifact_verification["active_artifact_progress"] == accepted_progress
    state = RunState.load(str(run_dir / "run_state.json"))
    assert state.best_progress == accepted_progress
    assert {key: state.progress.best[key] for key in accepted_progress} == accepted_progress
    rejected = [r for r in state.attempts.records if r.final_disposition]
    assert rejected and all(r.final_disposition == "rejected_artifact_verification" for r in rejected)


def test_late_engine_requests_use_remaining_runner_budget(monkeypatch, tmp_path):
    now = {"value": 100.0}
    config = RunnerConfig(
        board_path="/tmp/fake_board.kicad_pcb", run_dir=str(tmp_path / "run"),
        engine_factory=None, session_factory=None,
        engine_call_timeout_s=8.0, time_limit_s=10.0,
        clock=lambda: now["value"],
    )
    runner = RoutingRunner(config)
    runner._run_started = 100.0
    captured = {}

    class EngineStub:
        def __init__(self, board_path, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(router_client, "ipc_enabled", lambda: True)
    import pcb_world.engine as engine_module
    monkeypatch.setattr(engine_module, "KiCadEngine", EngineStub)
    runner._open_engine(config.board_path)
    timeout_provider = captured["call_timeout_s"]
    assert callable(timeout_provider)
    assert timeout_provider() == pytest.approx(8.0)
    now["value"] = 109.8
    assert timeout_provider() == pytest.approx(0.2)
    now["value"] = 110.0
    assert timeout_provider() == 0.0


def test_candidate_verifier_uses_remaining_run_deadline(monkeypatch, tmp_path):
    now = {"value": 109.8}
    runner = RoutingRunner(_config(
        tmp_path, _engine(), engine_call_timeout_s=8.0,
        time_limit_s=10.0, clock=lambda: now["value"],
    ))
    runner._run_started = 100.0
    captured = {}

    def fake_owned_process(command, *, timeout_s):
        captured["timeout_s"] = timeout_s
        request = json.loads(Path(command[-1]).read_text())
        captured["call_timeout_s"] = request["call_timeout_s"]
        return runner_module.subprocess.CompletedProcess(
            command, 0, json.dumps({"ok": True}), "",
        )

    monkeypatch.setattr(runner_module, "runner_run_owned_process", fake_owned_process)
    folder = tmp_path / "generation"
    folder.mkdir()
    assert runner._verify_generation_in_child(str(folder), {"test": True}) == {"ok": True}
    assert captured["timeout_s"] == pytest.approx(0.2)
    assert captured["call_timeout_s"] == pytest.approx(0.2)

    now["value"] = 110.0
    result = runner._verify_generation_in_child(str(folder), {"test": True})
    assert result["ok"] is False
    assert "run deadline expired" in result["reason"]


def test_quarantined_failed_planner_stops_before_next_iteration(monkeypatch, tmp_path):
    engine = _engine(failing_target=(40.0, 10.0))
    planner_calls = []

    class Planner:
        request_count = 0

        def propose(self, **_kwargs):
            self.request_count += 1
            planner_calls.append(self.request_count)
            return PlannerReply(request={"tool": "connect_targets", "mode": "walkaround"})

    planner = Planner()
    session_holder = {}

    def make_session(eng, path):
        session = AgentSession(eng, board_path=path)
        session_holder["session"] = session
        return session

    actual_handler = runner_module.handle_request

    def quarantine_failed_tool(session, request):
        session._dirty = True
        session._dirty_reason = "test rollback could not be verified"
        return {"ok": False, "reason": "tool refused after quarantine"}

    monkeypatch.setattr(runner_module, "handle_request", quarantine_failed_tool)
    runner = RoutingRunner(_config(
        tmp_path, engine, planner=planner, max_model_requests=3,
        max_attempts=4, session_factory=make_session,
    ))
    report = runner.run()
    session = session_holder["session"]
    assert report.stop_reason == "session_quarantined"
    assert planner_calls == [1]

    # Simulate the loop offering another pair after the tool's failed return.
    pair = NetPair(
        net_code=1, net_name="NET1", start=(10.0, 10.0, 1),
        target=(40.0, 10.0, 1), gap_mm=30.0,
    )
    calls_after_quarantine = []
    original = engine.get_unrouted_count

    def no_native_after_quarantine():
        if session.dirty:
            calls_after_quarantine.append("native")
            raise AssertionError("quarantined engine was queried")
        return original()

    engine.get_unrouted_count = no_native_after_quarantine
    runner._attempt_pair(session, pair)
    runner._try_planner(session, pair)
    assert not calls_after_quarantine
    assert planner_calls == [1]
    monkeypatch.setattr(runner_module, "handle_request", actual_handler)


def test_unknown_track_only_pair_does_not_block_another_net(tmp_path):
    engine = FakeEngine(
        clusters={
            "track_start": {(0.0, 0.0, 1)},
            "track_end": {(1.0, 0.0, 1)},
            "pad_start": {(10.0, 0.0, 1)},
            "pad_end": {(12.0, 0.0, 1)},
        },
        pads=[PadInfo(10.0, 0.0, net_code=2), PadInfo(12.0, 0.0, net_code=2)],
        tracks=[TrackInfo(0.0, 0.0, 1.0, 0.0, net_code=1)],
        ratsnest=[
            RatsnestEdge(0.0, 0.0, 1.0, 0.0, 1, 0, 0),
            RatsnestEdge(10.0, 0.0, 12.0, 0.0, 2, 0, 0),
        ],
    )
    report = RoutingRunner(_config(
        tmp_path, engine, max_attempts=4, candidate_limit=1, max_per_net=2,
    )).run()
    state = RunState.load(str(tmp_path / "run" / "run_state.json"))
    # The track-only pair is either re-anchored on its own copper (the new,
    # preferred outcome) or retired with a diagnosis that names the geometry. What
    # must never happen is a forced net or a blocked queue.
    unknown = [r for r in state.attempts.records if r.candidate == "endpoint_identity"]
    assert all(r.pair_key[0] == 1 for r in unknown)
    assert all(r.outcome == AttemptHistory.EXHAUSTED_OUTCOME for r in unknown)
    assert all(("unsupported" in r.reason) or ("carries no copper" in r.reason)
               for r in unknown)
    reanchored = [r for r in state.attempts.records
                  if r.pair_key[0] == 1 and r.candidate != "endpoint_identity"]
    assert unknown or reanchored, "the pair must be attempted or named, never skipped"
    assert report.stop_reason != "endpoint_net_mismatch"
    assert any(r.accepted and r.pair_key[0] == 2 for r in state.attempts.records)
    assert all(edge.net_code == 1 for edge in engine.state.ratsnest)


def test_scan_offers_every_net_a_turn_before_any_net_a_second():
    """The queue is round-robin across nets, not shortest-pair-first globally."""
    edges = []
    pads = []
    clusters = {}
    for net in (1, 2, 3):
        for index in range(3):
            x0, x1 = 10.0 * net, 10.0 * net + 5.0 + index
            clusters[f"n{net}_{index}a"] = {(x0, float(index), 1)}
            clusters[f"n{net}_{index}b"] = {(x1, float(index), 1)}
            pads += [PadInfo(x0, float(index), net_code=net),
                     PadInfo(x1, float(index), net_code=net)]
            edges.append(RatsnestEdge(x0, float(index), x1, float(index), net, 0, 0))
    engine = FakeEngine(clusters=clusters, pads=pads, ratsnest=edges)
    session = AgentSession(engine, board_path="/tmp/x.kicad_pcb", require_tokens=False)
    resolver = LayerResolver(copper_layers=2, mapping={0: 1, 2: 2}, samples={0: 1})

    scan = scan_net_pairs(session, resolver=resolver, max_per_net=2)
    assert isinstance(scan, PairScan)
    # Depth-first per net would be [1, 1, 2, 2, 3, 3].
    assert [pair.net_code for pair in scan.pairs] == [1, 2, 3, 1, 2, 3]
    assert scan.skipped_net_cap == 3          # each net's third pair is deferred


def test_an_empty_scan_is_not_an_empty_board():
    """An empty offered list is "no pair left to try", not "completed"."""
    engine = _engine()
    scan = scan_net_pairs(
        AgentSession(engine, board_path="/tmp/x.kicad_pcb", require_tokens=False),
        skip=lambda pair: True,
    )
    assert scan.pairs == ()
    assert scan.skipped_by_caller == scan.edges
    assert scan.edges > 0


def test_a_resumed_run_uses_the_operators_stall_patience(tmp_path):
    """The checkpoint's stall count is evidence; this session's patience governs."""
    engine = _engine(failing_target=(40.0, 10.0))
    RoutingRunner(_config(
        tmp_path, engine, max_attempts=1, stall_patience=1, name="run",
    )).run()

    state = RunState.load(
        str(tmp_path / "run" / "run_state.json"),
        expected_provenance=build_provenance(
            board_path="/tmp/fake_board.kicad_pcb", project_path=None, rules_path=None,
        ),
    )
    assert state.progress.patience == 1
    resumed = RoutingRunner(
        _config(tmp_path, engine, max_attempts=6, stall_patience=4, name="run"),
        state=state,
    ).run()
    assert state.progress.patience == 4
    # The stall count is per session: the checkpoint's fruitless attempts do not
    # consume this session's budget.
    assert resumed.stop_reason != "no_progress"
    assert resumed.attempts > 1, "the resumed run stopped on the checkpoint's patience"


def test_a_copied_checkpoint_resumes_in_its_new_directory(tmp_path):
    """The CLI's "copy the directory and resume the copy" advice must work.

    The checkpoint records the directory it was written in; the *copy* is a
    different directory, so the resumed state is rebound to wherever the state
    file actually lives - and any other ``--run-dir`` is still refused.
    """
    import shutil
    from tools.reliability.route_agent import rebind_resume_run_dir

    engine = _engine()
    RoutingRunner(_config(tmp_path, engine, max_attempts=1, name="original")).run()
    shutil.copytree(tmp_path / "original", tmp_path / "copy")

    state = RunState.load(str(tmp_path / "copy" / "run_state.json"))
    assert state.run_dir == str(tmp_path / "original")
    assert rebind_resume_run_dir(
        state, str(tmp_path / "copy" / "run_state.json"), str(tmp_path / "copy")
    ) is None
    assert state.run_dir == str(tmp_path / "copy")

    elsewhere = rebind_resume_run_dir(
        state, str(tmp_path / "copy" / "run_state.json"), str(tmp_path / "elsewhere")
    )
    assert elsewhere is not None
    assert elsewhere["error"] == "resume_run_dir_mismatch"
    assert elsewhere["checkpoint_run_dir"] == str(tmp_path / "copy")
