"""Candidate generation, ranking, attempt history, progress and resume state."""

from __future__ import annotations

import json

import pytest

from pcb_world.agent.observations import NetPair
from pcb_world.agent.scheduler import (
    RUN_STATE_VERSION,
    AttemptHistory,
    AttemptRecord,
    ProgressTracker,
    ProvenanceMismatchError,
    RunState,
    attempt_plan_key,
    build_provenance,
    generate_candidates,
    rank_candidates,
    sha256_file,
)


def _pair(**overrides) -> NetPair:
    base = dict(
        net_code=1, net_name="NET1",
        start=(10.0, 10.0, 1), target=(40.0, 10.0, 1), gap_mm=30.0,
        start_layers=(1,), target_layers=(1,), pad_groups=2,
    )
    base.update(overrides)
    return NetPair(**base)


# ---------------------------------------------------------------------------
# Candidates
# ---------------------------------------------------------------------------


def test_candidates_lead_with_the_cheap_deterministic_plans():
    candidates = generate_candidates(_pair(), copper_layers=2)
    names = [candidate.name for candidate in candidates]
    assert names[0] == "direct_walkaround"
    assert names[1] == "direct_shove"
    assert all(candidate.mode in ("walkaround", "shove") for candidate in candidates)
    # Bounded: direct x2, up to max_detours detours, at most one layer change.
    assert len(candidates) <= 2 + 3 + 1


def test_detour_waypoints_sit_off_the_straight_line_and_inside_the_board():
    candidates = generate_candidates(
        _pair(), board_bbox=[0.0, 0.0, 50.0, 30.0], copper_layers=2, max_detours=2,
    )
    detours = [c for c in candidates if c.kind == "detour"]
    assert len(detours) == 2
    for candidate in detours:
        (x, y, layer), = candidate.waypoints
        assert layer == 1
        assert 0.5 <= x <= 49.5 and 0.5 <= y <= 29.5
        assert abs(y - 10.0) > 0.1          # off the y=10 straight line
    sides = sorted(c.waypoints[0][1] - 10.0 for c in detours)
    assert sides[0] < 0 < sides[1]          # one detour each side of the line

    wider = [c for c in generate_candidates(
        _pair(), board_bbox=[0.0, 0.0, 50.0, 30.0], copper_layers=2, max_detours=3,
    ) if c.kind == "detour"]
    magnitudes = sorted(abs(c.waypoints[0][1] - 10.0) for c in wider)
    assert magnitudes[0] == magnitudes[1] < magnitudes[2]


def test_layer_detour_only_when_it_makes_sense():
    same_layer = generate_candidates(_pair(), copper_layers=2)
    assert any(c.kind == "layer" for c in same_layer)
    single_layer = generate_candidates(_pair(), copper_layers=1)
    assert not any(c.kind == "layer" for c in single_layer)
    already_crossing = generate_candidates(
        _pair(target=(40.0, 10.0, 2)), copper_layers=2
    )
    assert any(c.kind == "layer" for c in already_crossing)


def test_candidate_key_is_stable_and_rounded():
    candidate = generate_candidates(_pair(), copper_layers=2)[2]
    again = generate_candidates(_pair(), copper_layers=2)[2]
    assert candidate.key() == again.key()
    assert candidate.to_probe_dict()["waypoints"]


# ---------------------------------------------------------------------------
# Obstacle-derived candidates
# ---------------------------------------------------------------------------


def test_obstacle_detours_clear_the_observed_wall_on_the_nearer_side():
    """A wall across the straight line gets a waypoint just past its edge."""
    # The pair runs west->east at y=10; a foreign track lies across it at x=25.
    wall = {
        "kind": "track", "net_code": 2, "net_name": "OTHER", "layer": 1,
        "x1_mm": 25.0, "y1_mm": 9.0, "x2_mm": 25.0, "y2_mm": 11.5,
        "width_mm": 0.25,
    }
    candidates = generate_candidates(
        _pair(), board_bbox=[0.0, 0.0, 50.0, 30.0], copper_layers=2,
        obstacles=[wall],
    )
    obstacles = [c for c in candidates if c.kind == "obstacle"]
    assert obstacles, [c.name for c in candidates]
    # The informed plans come before the generic sideways detours.
    assert candidates.index(obstacles[0]) < min(
        index for index, c in enumerate(candidates) if c.kind == "detour"
    )
    first = obstacles[0]
    (wx, wy, layer), = first.waypoints
    assert layer == 1
    assert abs(wy - 10.0) >= 1.0          # clears the wall's 1.5 mm extent
    assert abs(wx - 25.0) < 0.001         # placed at the obstacle, not a blind offset


def test_obstacle_detours_are_deterministic_and_bounded():
    wall = {
        "kind": "via", "net_code": 2, "net_name": "OTHER", "x_mm": 25.0,
        "y_mm": 10.4, "diameter_mm": 0.6,
    }
    first = generate_candidates(_pair(), obstacles=[wall], board_bbox=[0, 0, 50, 30])
    second = generate_candidates(_pair(), obstacles=[wall], board_bbox=[0, 0, 50, 30])
    assert [c.name for c in first] == [c.name for c in second]
    assert len([c for c in first if c.kind == "obstacle"]) <= 3


def test_an_obstacle_clear_of_the_line_produces_no_detour():
    far = {
        "kind": "pad", "net_code": 2, "net_name": "OTHER", "x_mm": 25.0,
        "y_mm": 16.0, "size_mm": [0.5, 0.5],
    }
    candidates = generate_candidates(_pair(), obstacles=[far],
                                     board_bbox=[0, 0, 50, 30])
    assert not [c for c in candidates if c.kind == "obstacle"]


def test_a_waypoint_is_clamped_inside_the_board():
    wall = {
        "kind": "track", "net_code": 2, "net_name": "OTHER",
        "x1_mm": 25.0, "y1_mm": 10.0, "x2_mm": 25.0, "y2_mm": 10.5,
        "width_mm": 0.25,
    }
    candidates = generate_candidates(
        _pair(gap_mm=2.0), board_bbox=[0.0, 0.0, 50.0, 10.2], copper_layers=2,
        obstacles=[wall],
    )
    for candidate in candidates:
        if candidate.kind != "obstacle":
            continue
        (x, y, _layer), = candidate.waypoints
        assert 0.5 <= x <= 49.5 and 0.5 <= y <= 9.7


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------


def _result(*, accepted=True, connected=True, added_relevant=0, vias=0, length=1.0,
            changed=(1,), steps=2) -> dict:
    return {
        "accepted": accepted,
        "connected": connected,
        "steps": [{}] * steps,
        "evidence": {
            "added_relevant_count": added_relevant,
            "vias_added": vias,
            "added_length_mm": length,
            "changed_nets": list(changed),
            "drc_delta": {"added_relevant_count": added_relevant},
            "pre_transaction": {"via_count": 0},
            "final": {"via_count": vias},
        },
    }


def test_ranking_prefers_acceptance_then_quality():
    direct = generate_candidates(_pair(), copper_layers=2)[0]
    shove = generate_candidates(_pair(), copper_layers=2)[1]
    ranked = rank_candidates([
        (shove, _result(accepted=False, connected=False)),
        (direct, _result(accepted=True, vias=0, length=2.0, changed=(1,))),
    ])
    assert ranked[0][0].name == "direct_walkaround"

    # Two accepted plans: fewer vias wins.
    a = generate_candidates(_pair(), copper_layers=2)[0]
    b = generate_candidates(_pair(), copper_layers=2)[1]
    ranked = rank_candidates([
        (b, _result(accepted=True, vias=1)),
        (a, _result(accepted=True, vias=0)),
    ])
    assert ranked[0][0].key() == a.key()


def test_ranking_prefers_less_added_copper_and_less_disturbance():
    a = generate_candidates(_pair(), copper_layers=2)[0]
    b = generate_candidates(_pair(), copper_layers=2)[1]
    ranked = rank_candidates([
        (a, _result(length=9.0, changed=(1, 2, 3))),
        (b, _result(length=3.0, changed=(1,))),
    ])
    assert ranked[0][0].key() == b.key()


def test_ranking_falls_back_to_the_drc_delta_count():
    a = generate_candidates(_pair(), copper_layers=2)[0]
    b = generate_candidates(_pair(), copper_layers=2)[1]
    result_a = _result()
    result_a["evidence"].pop("added_relevant_count")
    result_a["evidence"]["drc_delta"] = {"added_relevant_count": 2}
    result_b = _result()
    result_b["evidence"].pop("added_relevant_count")
    result_b["evidence"]["drc_delta"] = {"added_relevant_count": 0}
    ranked = rank_candidates([(a, result_a), (b, result_b)])
    assert ranked[0][0].key() == b.key()


# ---------------------------------------------------------------------------
# Attempt history
# ---------------------------------------------------------------------------


def _record(pair: NetPair, candidate, *, accepted=False, probe_only=False,
            board_digest=None, sweep_member=False) -> AttemptRecord:
    return AttemptRecord(
        pair_key=pair.key, plan_key=candidate.key(), candidate=candidate.name,
        mode=candidate.mode, kind=candidate.kind, source="deterministic",
        outcome="ok" if accepted else "routing_failed", accepted=accepted,
        connected=accepted, probe_only=probe_only,
        sweep_member=sweep_member,
        committed=accepted and not probe_only,
        board_digest=board_digest,
    )


def test_history_blocks_identical_retries_until_something_commits():
    pair = _pair()
    candidate = generate_candidates(pair, copper_layers=2)[0]
    history = AttemptHistory()
    history.note(_record(pair, candidate, accepted=False))
    assert candidate.key() in history.tried_keys(pair)
    assert history.count_for_pair(pair) == 1

    # A committed success clears the pair's "do not retry" pressure...
    history.note(_record(pair, candidate, accepted=True))
    assert candidate.key() not in history.tried_keys(pair)


def test_probe_only_records_still_block_a_retry():
    pair = _pair()
    candidate = generate_candidates(pair, copper_layers=2)[1]
    history = AttemptHistory()
    history.note(_record(pair, candidate, accepted=True, probe_only=True))
    assert candidate.key() in history.tried_keys(pair)
    assert history.records[0].committed is False


def test_a_swept_candidate_does_not_spend_the_pairs_attempt_budget():
    """A six-candidate sweep is one attempt on the pair, not six.

    The sweep now *applies* its candidates (and rolls the losers back), so its
    records are not ``probe_only``; if they counted as attempts, a single
    six-candidate pair would burn the per-net budget and starve the queue before
    the pair ever reached the planner.
    """
    pair = _pair()
    candidates = generate_candidates(pair, copper_layers=2)
    history = AttemptHistory()
    for candidate in candidates[:4]:
        history.note(_record(pair, candidate, accepted=False, sweep_member=True))
    assert history.attempts_for_pair(pair) == 0
    assert history.for_pair(pair)[0].sweep_member is True
    # Each swept candidate is still a plan that must not be retried unchanged.
    for candidate in candidates[:4]:
        assert candidate.key() in history.tried_keys(pair)

    # A committed sweep member is a real attempt and clears its plan.
    history.note(_record(pair, candidates[0], accepted=True, sweep_member=True))
    assert history.attempts_for_pair(pair) == 1
    assert candidates[0].key() not in history.tried_keys(pair)


def test_sweep_member_survives_a_checkpoint_round_trip():
    pair = _pair()
    candidate = generate_candidates(pair, copper_layers=2)[0]
    history = AttemptHistory()
    history.note(_record(pair, candidate, accepted=False, sweep_member=True))
    restored = AttemptHistory.from_dicts(
        json.loads(json.dumps(history.as_dicts()))
    )
    assert restored.records[0].sweep_member is True
    assert restored.attempts_for_pair(pair) == 0


def test_history_round_trips_through_json():
    pair = _pair()
    candidate = generate_candidates(pair, copper_layers=2)[0]
    history = AttemptHistory()
    history.note(_record(pair, candidate, accepted=False))
    payload = json.loads(json.dumps(history.as_dicts()))
    restored = AttemptHistory.from_dicts(payload)
    assert restored.tried_keys(pair) == history.tried_keys(pair)
    assert restored.records[0].plan_key == candidate.key()


def test_attempt_plan_key_normalises_waypoints():
    assert attempt_plan_key("walkaround", ((1.0004, 2.0, 1),)) == (
        "walkaround", ((1.0, 2.0, 1),)
    )
    assert attempt_plan_key("shove", ()) == ("shove", ())
    normalized = attempt_plan_key("walkaround", ((1.0, 2.0, None),))
    assert attempt_plan_key(*normalized) == normalized


def test_failed_plan_is_retried_after_copper_generation_changes():
    pair = _pair()
    candidate = generate_candidates(pair, copper_layers=2)[0]
    history = AttemptHistory()
    history.note(_record(pair, candidate, board_digest="generation-a"))
    assert candidate.key() in history.tried_keys(pair, "generation-a")
    assert candidate.key() not in history.tried_keys(pair, "generation-b")


def test_a_reaped_failure_round_trips_its_cause_through_the_checkpoint():
    """A resumed run must still be able to read why the last attempt stopped.

    The cause is what makes an unverifiable rollback diagnosable instead of a
    category to re-derive by re-running the transaction, so it has to survive the
    same JSON round trip every other attempt field does — and it has to stay
    bounded, because a native crash message carries a stderr tail.
    """
    pair = _pair()
    candidate = generate_candidates(pair, copper_layers=2)[0]
    record = _record(pair, candidate, accepted=False)
    record.reason = "drc_unavailable"
    record.copper_state = "retained_unknown"
    record.failure_exception = (
        "EngineServerCrashed: owned engine operation 'call' exceeded its "
        "3.399 s deadline; child reaped"
    )
    record.rollback_detail = {
        "restore_exception": "EngineServerCrashed: ... 0.000 s deadline; child reaped",
        "expected_digest": "7af2e79c66dd27e8",
        "unverifiable": ["routing_target"],
        # A field outside the compact set is dropped rather than carried.
        "not_kept": "noise",
    }
    history = AttemptHistory()
    history.note(record)

    restored = AttemptHistory.from_dicts(json.loads(json.dumps(history.as_dicts())))
    kept = restored.records[0]
    assert kept.reason == "drc_unavailable"
    assert kept.copper_state == "retained_unknown"
    assert "child reaped" in kept.failure_exception
    # ``rollback_detail`` is written as given; the compaction happens where the
    # record is built from the session's evidence (see the runner).
    assert kept.rollback_detail["expected_digest"] == "7af2e79c66dd27e8"
    assert restored.records == history.records


def test_the_failed_step_round_trips_and_legacy_records_read_as_not_recorded():
    """Where a plan stopped survives the checkpoint; older ones say so plainly.

    The point of the field is that a failure analysis can read a run state and
    tell a plan that never got past its start step from one refused at its via,
    without replaying the plan against a harness that may have changed since.
    A checkpoint written before the field existed must still load: an absent or
    unusable value reads as "not recorded" (empty kind, index -1) and never as a
    failure at step 0, which is a real step.
    """
    pair = _pair()
    candidate = generate_candidates(pair, copper_layers=2)[0]
    record = _record(pair, candidate, accepted=False)

    # A plan that ran every step to the end reports no failed step at all.
    payload = record.to_dict()
    assert payload["failed_step_kind"] == ""
    assert payload["failed_step_index"] == -1

    record.failed_step_kind = "via"
    record.failed_step_index = 2
    history = AttemptHistory([record])
    restored = AttemptHistory.from_dicts(json.loads(json.dumps(history.as_dicts())))
    assert restored.records[0].failed_step_kind == "via"
    assert restored.records[0].failed_step_index == 2

    legacy = {key: value for key, value in payload.items()
              if key not in ("failed_step_kind", "failed_step_index")}
    older = AttemptHistory.from_dicts([legacy]).records[0]
    assert older.failed_step_kind == ""
    assert older.failed_step_index == -1
    # A value the field never should have held is "not recorded", not an error.
    unusable = AttemptHistory.from_dicts([
        {**payload, "failed_step_kind": None, "failed_step_index": "two"},
    ]).records[0]
    assert unusable.failed_step_kind == ""
    assert unusable.failed_step_index == -1


def test_the_compacted_failure_text_stays_bounded():
    from pcb_world.agent.scheduler import (
        EXCEPTION_TEXT_LIMIT,
        compact_exception_text,
        compact_rollback_detail,
    )

    assert compact_exception_text(None) is None
    assert compact_exception_text("short") == "short"
    long_text = "x" * (EXCEPTION_TEXT_LIMIT + 500)
    bounded = compact_exception_text(long_text)
    assert bounded.startswith("x" * EXCEPTION_TEXT_LIMIT)
    assert f"truncated at {EXCEPTION_TEXT_LIMIT}" in bounded
    assert len(bounded) < len(long_text)
    # Only the fields that decide whether a restore was proved are kept.
    compact = compact_rollback_detail({
        "restored": False, "restore_exception": "boom", "unverifiable": ["a"],
        "expected_state": {"layer": 2}, "not_a_field": 1,
    })
    assert set(compact) == {
        "restored", "restore_exception", "unverifiable", "expected_state",
    }
    assert compact_rollback_detail("not a mapping") is None
    assert compact_rollback_detail({}) is None


# ---------------------------------------------------------------------------
# Progress
# ---------------------------------------------------------------------------


def test_progress_tracker_detects_improvement_and_stall():
    tracker = ProgressTracker(patience=2)
    assert tracker.update({"unrouted_edges": 10, "pad_group_total": 12}) is True
    assert tracker.update({"unrouted_edges": 10, "pad_group_total": 12}) is False
    assert tracker.update({"unrouted_edges": 10, "pad_group_total": 12}) is False
    assert tracker.stalled is True
    assert tracker.update({"unrouted_edges": 9, "pad_group_total": 11}) is True
    assert tracker.stalled is False
    assert tracker.best["unrouted_edges"] == 9


def test_progress_tracker_round_trips():
    tracker = ProgressTracker(patience=3)
    tracker.update({"unrouted_edges": 5, "pad_group_total": 6})
    restored = ProgressTracker.from_dict(json.loads(json.dumps(tracker.to_dict())))
    assert restored.best == tracker.best
    assert restored.stagnation == tracker.stagnation


# ---------------------------------------------------------------------------
# Run state / provenance
# ---------------------------------------------------------------------------


def test_run_state_round_trips_and_refuses_mismatched_provenance(tmp_path):
    board = tmp_path / "b.kicad_pcb"
    board.write_text("(kicad_pcb)")
    rules = tmp_path / "b.kicad_dru"
    rules.write_text("(version 1)")
    provenance = build_provenance(
        board_path=str(board), project_path=None, rules_path=str(rules)
    )
    assert provenance["board_sha256"] == sha256_file(str(board))
    assert provenance["rules_sha256"] == sha256_file(str(rules))

    state = RunState(
        board_path=str(board), project_path=None, rules_path=str(rules),
        provenance=provenance, run_dir=str(tmp_path),
    )
    path = state.save()
    loaded = RunState.load(path, expected_provenance=provenance)
    assert loaded.board_path == str(board)
    assert loaded.version == RUN_STATE_VERSION

    board.write_text("(kicad_pcb) (changed)")
    changed = build_provenance(
        board_path=str(board), project_path=None, rules_path=str(rules)
    )
    with pytest.raises(ProvenanceMismatchError) as exc:
        RunState.load(path, expected_provenance=changed)
    assert "board_sha256" in str(exc.value)
    # An explicit override is possible, and the differences are reported.
    loaded = RunState.load(path, expected_provenance=changed, allow_mismatch=True)
    assert loaded.provenance["board_sha256"] != changed["board_sha256"]


def test_run_state_refuses_an_unknown_version(tmp_path):
    payload = {
        "version": RUN_STATE_VERSION + 7, "board_path": "x", "provenance": {},
    }
    path = tmp_path / "state.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ProvenanceMismatchError):
        RunState.load(str(path))


def test_run_state_save_is_atomic_and_creates_the_directory(tmp_path):
    target_dir = tmp_path / "nested" / "run"
    state = RunState(
        board_path="b", project_path=None, rules_path=None, provenance={},
        run_dir=str(target_dir),
    )
    path = state.save()
    assert path.endswith("run_state.json")
    assert not path.endswith(".tmp")
    assert json.loads(open(path, encoding="utf-8").read())["board_path"] == "b"
