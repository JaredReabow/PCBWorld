"""Free-position via search: a bounded prefilter, not a clearance proof.

The engine's ``pad_block_reason(for_via=True)`` answers a narrow question - is
this point on a through-hole pad (or grazing a same-net one) - and says nothing
about tracks, zones, holes or clearance across the via's span. So the search uses
it to *choose candidates*, and the transaction's native DRC keeps the authority.
What these tests pin is the bounding and the honesty: probes and candidates are
capped, the search stops when the run is stopping or the deadline passes, the
positions it returns are the ones the prefilter did not refuse, and it is not even
attempted for a pair where a via cannot help.
"""

from __future__ import annotations

import json

import pytest

from pcb_world.agent.observations import NetPair
from pcb_world.agent.runner import RunnerConfig, RoutingRunner
from pcb_world.agent.scheduler import AttemptRecord, RunState


class _StubEngine:
    """Records every prefilter probe; refuses exactly the points told to."""

    def __init__(self, blocked=()) -> None:
        self.blocked = {(round(x, 3), round(y, 3)) for x, y in blocked}
        self.calls: list[tuple[float, float]] = []
        self.radius_calls = 0

    def route_item_radius_mm(self, *, for_via: bool) -> float:
        assert for_via is True
        self.radius_calls += 1
        return 0.3

    def pad_block_reason(self, x_mm: float, y_mm: float, *, item_radius_mm: float,
                         for_via: bool) -> str | None:
        assert for_via is True
        self.calls.append((round(x_mm, 3), round(y_mm, 3)))
        if (round(x_mm, 3), round(y_mm, 3)) in self.blocked:
            return "via_on_thru_pad"
        return None


class _StubSession:
    def __init__(self, engine, *, target_cluster=()) -> None:
        self._engine = engine
        self._target = frozenset(target_cluster)

    def cluster(self, x_mm: float, y_mm: float, layer: int) -> frozenset:
        # The target's own anchor is on its cluster; everything else is empty
        # unless a test says otherwise.
        if (round(float(x_mm), 3), round(float(y_mm), 3)) == (30.0, 15.0):
            return self._target
        return frozenset()


def _pair(start, target, *, kind: str = "line", net: int = 1) -> NetPair:
    return NetPair(
        net_code=net, net_name=f"NET{net}", start=start, target=target,
        gap_mm=abs(target[0] - start[0]) + abs(target[1] - start[1]),
        pair_kind=kind,
    )


def _runner(tmp_path, *, hints=(), name: str = "run") -> RoutingRunner:
    config = RunnerConfig(board_path="/tmp/b.kicad_pcb",
                          run_dir=str(tmp_path / name))
    runner = RoutingRunner(config)
    runner._pending_digest = "digest"
    runner.state = RunState(
        board_path="/tmp/b.kicad_pcb", project_path=None, rules_path=None,
        provenance={}, run_dir=str(tmp_path / name),
    )
    if hints:
        runner.state.attempts.note(AttemptRecord(
            pair_key=_pair((10.0, 15.0, 1), (40.0, 15.0, 1)).key, plan_key=(),
            candidate="direct_walkaround", mode="walkaround", kind="direct",
            source="deterministic", outcome="routing_failed", accepted=False,
            connected=True, reason="drc_regression", board_digest="digest",
            drc_hints=tuple(hints),
        ))
    return runner


def test_the_search_is_not_attempted_where_a_via_cannot_help(tmp_path):
    engine = _StubEngine()
    runner = _runner(tmp_path)
    same_layer = _pair((10.0, 15.0, 1), (40.0, 15.0, 1))
    assert runner._via_free_positions(
        _StubSession(engine), same_layer, runner.config,
    ) == []
    assert engine.calls == [], "a same-layer pair with no hole hint needs no search"


def test_a_cross_layer_pair_searches_and_returns_unrefused_points(tmp_path):
    engine = _StubEngine()
    runner = _runner(tmp_path)
    pair = _pair((10.0, 15.0, 1), (10.0, 15.0, 4),
                 kind="coincident_cross_layer")
    spots = runner._via_free_positions(_StubSession(engine), pair, runner.config)
    assert spots, "an empty board must offer free positions"
    assert len(spots) <= runner.config.via_search_candidates
    for spot in spots:
        assert (round(spot["x_mm"], 3), round(spot["y_mm"], 3)) not in engine.blocked
        assert spot["distance_mm"] <= runner.config.via_search_radius_mm + 1e-6
    # The nearest positions come first.
    distances = [spot["distance_mm"] for spot in spots]
    assert distances == sorted(distances)


def test_the_search_is_bounded_by_probes_and_says_so(tmp_path):
    engine = _StubEngine()
    runner = _runner(tmp_path)
    config = RunnerConfig(
        board_path="/tmp/b.kicad_pcb", run_dir=str(tmp_path / "bounded"),
        via_search_radius_mm=2.0, via_search_pitch_mm=0.15,
        via_search_probes=25, via_search_candidates=100,
    )
    runner.config = config
    pair = _pair((10.0, 15.0, 1), (10.0, 15.0, 4),
                 kind="coincident_cross_layer")
    spots = runner._via_free_positions(_StubSession(engine), pair, config)
    assert len(engine.calls) <= 25
    evidence = runner.state.metrics["via_search"][json.dumps(pair.key)]
    assert evidence["probes"] <= 25
    assert evidence["truncated"] == "probe_budget"
    assert len(spots) <= 25


def test_the_full_grid_is_the_size_the_geometry_says(tmp_path):
    """A 2 mm disc at 0.15 mm pitch is ~558 points, and the budget allows it.

    Everything is refused so the search has to walk the whole disc: the probe
    budget (600) has to cover the geometry it claims to cover.
    """
    class _RefuseAll(_StubEngine):
        def pad_block_reason(self, x_mm, y_mm, *, item_radius_mm, for_via):
            self.calls.append((round(x_mm, 3), round(y_mm, 3)))
            return "via_on_thru_pad"

    engine = _RefuseAll()
    runner = _runner(tmp_path)
    config = RunnerConfig(board_path="/tmp/b.kicad_pcb", run_dir=str(tmp_path / "grid"),
                          via_search_radius_mm=2.0, via_search_candidates=1000,
                          via_search_probes=2000)
    runner.config = config
    pair = _pair((10.0, 15.0, 1), (10.0, 15.0, 4),
                 kind="coincident_cross_layer")
    spots = runner._via_free_positions(_StubSession(engine), pair, config)
    assert spots == []
    # Start, target and midpoint coincide here, so the disc is probed once.
    assert 540 <= len(engine.calls) <= 580, len(engine.calls)
    # The default radius is wider (3 mm), which is the geometry the run uses.
    wider = RunnerConfig(board_path="/tmp/b.kicad_pcb",
                         run_dir=str(tmp_path / "grid2"),
                         via_search_candidates=1000, via_search_probes=2000)
    assert wider.via_search_radius_mm == 3.0
    assert wider.via_search_probes >= 1257, wider.via_search_probes


def test_the_search_stops_when_the_run_is_stopping(tmp_path):
    engine = _StubEngine()
    runner = _runner(tmp_path)
    runner._stop_requested = "session_unverified"
    pair = _pair((10.0, 15.0, 1), (10.0, 15.0, 4),
                 kind="coincident_cross_layer")
    spots = runner._via_free_positions(_StubSession(engine), pair, runner.config)
    assert spots == []
    assert engine.calls == []


def test_the_search_is_cached_per_pair_and_generation(tmp_path):
    engine = _StubEngine()
    runner = _runner(tmp_path)
    pair = _pair((10.0, 15.0, 1), (10.0, 15.0, 4),
                 kind="coincident_cross_layer")
    first = runner._via_free_positions(_StubSession(engine), pair, runner.config)
    probes_after_first = len(engine.calls)
    second = runner._via_free_positions(_StubSession(engine), pair, runner.config)
    assert second == first
    assert len(engine.calls) == probes_after_first, "the second read must be cached"


def test_hole_class_refusals_enable_the_search_on_a_same_layer_pair(tmp_path):
    engine = _StubEngine()
    runner = _runner(tmp_path, hints=(("Hole size out of range", 25.0, 15.0, -1),))
    pair = _pair((10.0, 15.0, 1), (40.0, 15.0, 1))
    spots = runner._via_free_positions(_StubSession(engine), pair, runner.config)
    assert spots, "a hole-class refusal makes a via worth placing here"


def test_free_positions_become_via_free_candidates(tmp_path):
    from pcb_world.agent.scheduler import generate_candidates

    pair = _pair((10.0, 15.0, 1), (10.0, 15.0, 4),
                 kind="coincident_cross_layer")
    candidates = generate_candidates(
        pair, copper_layers=4,
        via_free_positions=({"x_mm": 10.6, "y_mm": 15.3},),
    )
    free = [candidate for candidate in candidates if candidate.kind == "via_free"]
    assert len(free) == 1
    assert free[0].waypoints[0][:2] == (10.6, 15.3)
    assert free[0].waypoints[0][2] == 4, "the via must land on the target layer"
    # Searched positions are offered before the fixed jog ring: they are the
    # result of looking, not of guessing.
    kinds = [candidate.kind for candidate in candidates]
    assert kinds.index("via_free") < kinds.index("via_jog")


def test_every_anchor_gets_a_share_of_the_candidate_budget(tmp_path):
    """The far side of a blocked hop is often near the *target*, not the start."""
    engine = _StubEngine()
    runner = _runner(tmp_path)
    pair = _pair((10.0, 15.0, 1), (30.0, 15.0, 4), kind="line")
    # With no slots reserved for proved continuations, every slot is an unproved
    # one and the per-anchor quota is what shares them out.
    config = RunnerConfig(
        board_path="/tmp/b.kicad_pcb", run_dir=str(tmp_path / "share"),
        via_search_continuations=0,
    )
    spots = runner._via_free_positions(_StubSession(engine), pair, config)
    assert len(spots) == config.via_search_candidates
    anchors = {tuple(spot["anchor"]) for spot in spots}
    # start, target and midpoint: one candidate each at least.
    assert len(anchors) >= 3, spots
    by_anchor: dict[tuple, int] = {}
    for spot in spots:
        key = tuple(spot["anchor"])
        by_anchor[key] = by_anchor.get(key, 0) + 1
    assert min(by_anchor.values()) >= 1
    # The target's own neighbourhood is searched first: it is the one whose via
    # can close the hop by itself.
    assert spots[0]["anchor"] == [30.0, 15.0], spots


class _ContinuationEngine(_StubEngine):
    """Prefilter-clean everywhere; one grid point carries the target's cluster."""

    def __init__(self, hits) -> None:
        super().__init__()
        self.hits = {(round(x, 3), round(y, 3)) for x, y in hits}


class _ContinuationSession(_StubSession):
    def __init__(self, engine, *, hits, target_cluster=(30.0, 15.0, 4),
                 target_point=(30.0, 15.0)) -> None:
        super().__init__(engine)
        self._hits = {(round(x, 3), round(y, 3)) for x, y in hits}
        self._target_cluster = frozenset({target_cluster})
        self._target_point = (round(target_point[0], 3), round(target_point[1], 3))

    def cluster(self, x_mm: float, y_mm: float, layer: int) -> frozenset:
        key = (round(float(x_mm), 3), round(float(y_mm), 3))
        if key == self._target_point:
            return self._target_cluster
        if key in self._hits:
            return self._target_cluster
        return frozenset()


def test_a_proved_continuation_comes_before_an_unproved_one(tmp_path):
    """A spot whose far-layer copper is the target's own component closes the hop."""
    pair = _pair((10.0, 15.0, 1), (30.0, 15.0, 4), kind="line")
    # A grid point that carries the target's cluster, on the escape layer.
    hit = (30.0 + 0.15, 15.0)
    engine = _ContinuationEngine([hit])
    session = _ContinuationSession(engine, hits=[hit])
    runner = _runner(tmp_path)
    spots = runner._via_free_positions(session, pair, runner.config)
    assert spots, "the search must still offer prefilter-clean positions"
    assert spots[0]["continuation_proved"] is True, spots
    assert (spots[0]["x_mm"], spots[0]["y_mm"]) == (hit[0], hit[1])
    assert all("continuation_proved" in spot for spot in spots)
    evidence = runner.state.metrics["via_search"][json.dumps(pair.key)]
    assert evidence["continuation_proved"] >= 1
    assert "sampled" in evidence and "proof" in evidence["sampled"]


def test_refusal_geometry_is_used_as_a_seed(tmp_path):
    """A recorded violation names where the obstacles are; search outward from it."""
    pair = _pair((10.0, 15.0, 1), (40.0, 15.0, 1))
    runner = _runner(tmp_path, hints=(("Hole clearance violation", 25.0, 18.0, 1),))
    engine = _StubEngine()
    spots = runner._via_free_positions(_StubSession(engine), pair, runner.config)
    evidence = runner.state.metrics["via_search"][json.dumps(pair.key)]
    assert [25.0, 18.0] in evidence["anchors"], evidence["anchors"]
    assert spots
