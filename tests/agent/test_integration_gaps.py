"""Integration-facing gap fixes: pair kinds, advisory target, unsupported refusal."""

from __future__ import annotations

import json
import pytest

from pcb_world.agent import AgentSession
from pcb_world.agent.observations import LayerResolver, NetPair, enumerate_net_pairs
from pcb_world.agent.runner import RoutingRunner, RunnerConfig
from pcb_world.agent.state import unverifiable_properties
from pcb_world.engine.wire import RatsnestEdge

from tests.agent.fake_engine import FakeEngine, PadInfo
from tests.agent.conftest import fake_artifact_verifier


def _engine_with_pairs(specs) -> FakeEngine:
    """A fake board with one pad per anchor plus two clean layer samples.

    ``specs`` are dicts: ``net``, ``start``/``target`` = (x, y, human_layer),
    ``start_mirror``/``target_mirror`` = the engine's copper-layer id for the pad.
    The two far-away pads exist so the layer resolver has single-layer samples on
    each copper layer (exactly as a real board's copper does).
    """
    clusters: dict[str, set] = {}
    pads: list[PadInfo] = [
        PadInfo(2.0, 2.0, layer=0, net_code=1, net_name="NET1"),
        PadInfo(4.0, 2.0, layer=2, net_code=1, net_name="NET1"),
    ]
    clusters["SAMPLE_F"] = {(2.0, 2.0, 1)}
    clusters["SAMPLE_B"] = {(4.0, 2.0, 2)}
    edges = []
    for index, spec in enumerate(specs):
        sx, sy, sh = spec["start"]
        tx, ty, th = spec["target"]
        clusters[f"S{index}"] = {(sx, sy, sh)}
        clusters[f"T{index}"] = {(tx, ty, th)}
        pads.append(PadInfo(sx, sy, layer=spec.get("start_mirror", 0),
                            net_code=spec["net"], net_name=f"NET{spec['net']}"))
        pads.append(PadInfo(tx, ty, layer=spec.get("target_mirror", 0),
                            net_code=spec["net"], net_name=f"NET{spec['net']}"))
        edges.append(RatsnestEdge(
            sx, sy, tx, ty, spec["net"],
            spec.get("start_mirror", 0), spec.get("target_mirror", 0),
        ))
    return FakeEngine(clusters=clusters, pads=pads, ratsnest=edges)


# ---------------------------------------------------------------------------
# Pair kinds
# ---------------------------------------------------------------------------


def test_pair_kind_separates_line_pairs_from_coincident_ones():
    engine = _engine_with_pairs([
        # routable line, both anchors on F.Cu
        {"net": 1, "start": (10.0, 10.0, 1), "target": (40.0, 10.0, 1)},
        # coincident anchors, different layers -> a via bridge, still routable
        {"net": 2, "start": (20.0, 20.0, 1), "target": (20.0, 20.0, 2),
         "target_mirror": 2},
    ])
    session = AgentSession(engine, board_path="/tmp/x.kicad_pcb", require_tokens=False)
    resolver = LayerResolver(copper_layers=2, mapping={0: 1, 2: 2}, samples={0: 1, 2: 1})
    pairs = enumerate_net_pairs(session, resolver=resolver, max_pairs=5)
    kinds = {pair.net_code: pair.pair_kind for pair in pairs}
    assert kinds == {1: "line", 2: "coincident_cross_layer"}
    # Both are routable: a coincident cross-layer pair is a via bridge, which the
    # plan builder executes with a via plus a layer switch.
    assert all(pair.routable for pair in pairs)


def test_a_short_cross_layer_pair_is_a_line_not_a_capability_claim():
    """A 0.048 mm cross-layer gap is a short line; the engine bridges it.

    An earlier 0.25 mm threshold labelled every short cross-layer pair
    "unsupported" without trying it, which hid real connections on the V3 board.
    Distance now only decides whether the two anchors are the *same point*.
    """
    engine = _engine_with_pairs([
        {"net": 1, "start": (10.0, 10.0, 1), "target": (40.0, 10.0, 1)},
        {"net": 2, "start": (20.0, 20.0, 1), "target": (20.048, 20.0, 2),
         "target_mirror": 2},
    ])
    session = AgentSession(engine, board_path="/tmp/x.kicad_pcb", require_tokens=False)
    resolver = LayerResolver(copper_layers=2, mapping={0: 1, 2: 2}, samples={0: 1, 2: 1})
    pairs = enumerate_net_pairs(session, resolver=resolver, max_pairs=5)
    kinds = {pair.net_code: pair.pair_kind for pair in pairs}
    assert kinds[2] == "line"
    assert kinds[1] == "line"
    # The truly coincident case is still classified as a bridge, not a line.
    engine = _engine_with_pairs([
        {"net": 3, "start": (20.0, 20.0, 1), "target": (20.0, 20.0, 2),
         "target_mirror": 2},
    ])
    session = AgentSession(engine, board_path="/tmp/x.kicad_pcb", require_tokens=False)
    kinds = {pair.net_code: pair.pair_kind
             for pair in enumerate_net_pairs(session, resolver=resolver, max_pairs=5)}
    assert kinds == {3: "coincident_cross_layer"}


def test_pair_dict_reports_the_kind():
    pair = NetPair(
        net_code=1, net_name="NET1", start=(1.0, 2.0, 1), target=(3.0, 4.0, 2),
        gap_mm=2.8, pair_kind="coincident_cross_layer",
    )
    assert pair.to_dict()["pair_kind"] == "coincident_cross_layer"


def test_runner_attempts_a_coincident_cross_layer_pair_instead_of_refusing_it(tmp_path):
    """A same-point cross-layer pair is a via bridge: the runner tries it.

    The runner used to refuse this shape outright on the strength of a distance
    threshold; on the real V3 board such pairs are exactly where the router can
    place a via, so refusing them hid connections. What the runner must not do is
    retry a shape forever.
    """
    engine = _engine_with_pairs([
        {"net": 2, "start": (20.0, 20.0, 1), "target": (20.0, 20.0, 2),
         "target_mirror": 2},
    ])
    report = RoutingRunner(RunnerConfig(
        board_path="/tmp/x.kicad_pcb",
        run_dir=str(tmp_path / "run"),
        max_attempts=4,
        stall_patience=6,
        engine_factory=lambda path: engine,
        session_factory=lambda eng, path: AgentSession(eng, board_path=path),
        artifact_verifier=fake_artifact_verifier,
    )).run()

    state = json.loads((tmp_path / "run" / "run_state.json").read_text())
    assert state["attempts"], "the bridge shape must be attempted, not refused"
    assert not any(record["outcome"] == "unsupported_pair" for record in state["attempts"])
    # ... and it is not retried forever: the per-net budget still ends the run.
    assert report.attempts <= 4
    assert report.stop_reason in ("pairs_exhausted", "no_progress", "attempt_limit")


def test_physically_distinct_tiny_gap_is_not_marked_unroutable():
    engine = _engine_with_pairs([
        {"net": 2, "start": (20.0, 20.0, 1), "target": (20.01, 20.0, 1)},
    ])
    session = AgentSession(engine)
    pair = enumerate_net_pairs(session)[0]
    assert pair.gap_mm == pytest.approx(0.01)
    assert pair.pair_kind == "line"
    assert pair.routable


# ---------------------------------------------------------------------------
# Advisory routing target
# ---------------------------------------------------------------------------


def test_rollback_verification_treats_the_router_target_as_advisory():
    """Copper/head/net/layer decide; the engine's target hint is reported only."""
    from pcb_world.agent.state import StateProbe

    engine = FakeEngine(
        clusters={"A": {(10.0, 10.0, 1)}, "B": {(40.0, 10.0, 1)}},
        pads=[PadInfo(10.0, 10.0, net_code=1), PadInfo(40.0, 10.0, net_code=1)],
        restore_target_override=(99.0, 99.0, 2.0),
    )
    session = AgentSession(engine, board_path="/tmp/x.kicad_pcb", require_tokens=False)

    # An active route is needed for the engine to report a target at all.
    engine.state.session.is_routing = True
    engine.state.session.current_layer = 1
    engine.state.session.current_net_code = 1
    engine.state.session.route_head = (10.0, 10.0, 1.0)
    engine.state.session.routing_target = (10.0, 10.0, 1.0)

    expected = session._probe()
    handle = engine.checkpoint()
    # The fake recomputes the advisory target during the restore (as the engine does).
    restored, detail = session._restore_and_verify(handle, expected)

    assert restored is True
    assert detail["session_state_matches"] is True
    assert detail["target_matches"] is False
    assert detail["target_advisory"] is True
    assert "advisory" in detail["target_advisory_note"]


def test_advisory_target_is_declared_as_unverifiable():
    limits = unverifiable_properties()
    assert any("routing_target" in item for item in limits)


def test_layer_resolver_refuses_contradictory_evidence():
    """The engine's layer map wins; board evidence that contradicts it is refused.

    With both pads sitting on human layer 1, the map's claim that mirror id 2 is
    human layer 2 is contradicted by the connectivity query, so the run stops
    instead of routing on an invented number.
    """
    engine = FakeEngine(
        clusters={"A": {(2.0, 2.0, 1)}, "B": {(4.0, 2.0, 1)}},
        pads=[
            PadInfo(2.0, 2.0, layer=0, net_code=1),
            PadInfo(4.0, 2.0, layer=2, net_code=1),
        ],
    )
    session = AgentSession(engine, board_path="/tmp/x.kicad_pcb", require_tokens=False)
    from pcb_world.agent.observations import LayerConventionError

    try:
        LayerResolver.build(session)
    except LayerConventionError as exc:
        assert "contradictory mapping" in str(exc)
    else:  # pragma: no cover - a guess here would be a real defect
        raise AssertionError("the resolver accepted a mapping the board contradicts")


def test_layer_resolver_derives_the_mapping_from_clean_samples():
    engine = FakeEngine(
        clusters={"A": {(2.0, 2.0, 1)}, "B": {(4.0, 2.0, 2)}},
        pads=[
            PadInfo(2.0, 2.0, layer=0, net_code=1),
            PadInfo(4.0, 2.0, layer=2, net_code=1),
        ],
    )
    session = AgentSession(engine, board_path="/tmp/x.kicad_pcb", require_tokens=False)
    resolver = LayerResolver.build(session)
    assert resolver.mapping == {0: 1, 2: 2}
    assert resolver.to_evidence()["validated"] is True
    assert resolver.to_evidence()["source"] == "engine.layer_map"
