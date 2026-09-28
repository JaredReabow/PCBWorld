"""Native layer/endpoint identity: the engine's map is the authority.

The resolver used to majority-vote the mirror-id convention from every track,
via and pad on the board (slow, and wrong on a board where most copper sits on
one layer), and the endpoint query matched pads by X/Y alone, which credits a pad
on another layer - and therefore another net - to the cluster under the probe.
These tests pin the corrected contract against the real engine, on synthetic
2- and 4-layer boards.
"""

from __future__ import annotations

from pcb_world.agent import AgentSession
from pcb_world.agent.observations import (
    LayerResolver,
    enumerate_net_pairs,
    human_layers_at,
)
from tests.agent import synthetic_boards as sb


def _session(engine, board: str) -> AgentSession:
    return AgentSession(engine, board_path=board)


def test_two_layer_resolver_reads_the_engine_map(engine_factory, board_dir):
    board = sb.write_board(
        board_dir / "layers2.kicad_pcb",
        pads=[sb.Pad("PA1", 10.0, 10.0, 1, kind="smd_top"),
              sb.Pad("PB1", 40.0, 10.0, 1, kind="smd_bottom")],
    )
    engine = engine_factory(board)
    resolver = LayerResolver.build(_session(engine, board))

    assert resolver.mapping == {0: 1, 2: 2}
    evidence = resolver.to_evidence()
    assert evidence["source"] == "engine.layer_map"
    assert evidence["validated"] is True
    assert engine.layer_map.board_layer_order == [0, 2]


def test_four_layer_resolver_reads_the_engine_map(engine_factory, board_dir):
    """A 4-layer board: mirror ids 0/4/6/2 are human 1/2/3/4, not 1/2/3/4 in order."""
    board = sb.write_board(
        board_dir / "layers4.kicad_pcb",
        pads=[sb.Pad("PA1", 10.0, 10.0, 1, kind="smd_top"),
              sb.Pad("PB1", 40.0, 10.0, 1, kind="smd_bottom")],
        copper_layers=4,
    )
    engine = engine_factory(board)
    resolver = LayerResolver.build(_session(engine, board))

    assert engine.get_copper_layer_count() == 4
    assert engine.layer_map.board_layer_order == [0, 4, 6, 2]
    assert resolver.mapping == {0: 1, 4: 2, 6: 3, 2: 4}
    assert resolver.require_human(6) == 3          # an inner layer, not "5"


def test_overlapping_xy_on_unrelated_nets_resolves_by_layer(engine_factory, board_dir):
    """Two pads at one point on different layers are two endpoints, not one clash."""
    board = sb.write_board(
        board_dir / "overlap.kicad_pcb",
        pads=[sb.Pad("PA1", 20.0, 10.0, 1, kind="smd_top"),
              sb.Pad("PC1", 20.0, 10.0, 2, kind="smd_bottom"),
              sb.Pad("PB1", 40.0, 10.0, 1, kind="smd_top"),
              sb.Pad("PD1", 40.0, 10.0, 2, kind="smd_bottom")],
        nets=(1, 2),
    )
    engine = engine_factory(board)
    session = _session(engine, board)

    top = session.endpoint(20.0, 10.0, 1)
    bottom = session.endpoint(20.0, 10.0, 2)
    assert top.net_code == 1
    assert bottom.net_code == 2
    # Matching on X/Y alone was what collapsed these into `endpoint_ambiguous`.
    assert "PA1" in top.pads[0]
    assert "PC1" in bottom.pads[0]


def test_a_through_hole_pad_is_one_endpoint_on_both_layers(engine_factory, board_dir):
    board = sb.write_board(
        board_dir / "thru.kicad_pcb",
        pads=[sb.Pad("PA1", 20.0, 10.0, 1, kind="thru"),
              sb.Pad("PB1", 40.0, 10.0, 1, kind="smd_top")],
    )
    engine = engine_factory(board)
    session = _session(engine, board)

    for layer in (1, 2):
        endpoint = session.endpoint(20.0, 10.0, layer)
        assert endpoint.net_code == 1
        assert endpoint.pads and "PA1" in endpoint.pads[0]


def test_an_inner_layer_track_endpoint_is_reported_on_its_own_layer(
    engine_factory, board_dir
):
    board = sb.write_board(
        board_dir / "inner.kicad_pcb",
        pads=[sb.Pad("PA1", 10.0, 10.0, 1, kind="smd_top"),
              sb.Pad("PB1", 40.0, 20.0, 1, kind="smd_bottom")],
        copper_layers=4,
    )
    engine = engine_factory(board)
    session = _session(engine, board)
    resolver = LayerResolver.build(session)

    assert human_layers_at(session, 10.0, 10.0) == (1,)
    assert human_layers_at(session, 40.0, 20.0) == (4,)
    # The pair's anchors carry the engine's own layer ids, so a layer "probe"
    # that happens to find other copper can never substitute another layer.
    pairs = enumerate_net_pairs(session, resolver=resolver)
    assert pairs
    assert pairs[0].start[2] in (1, 4)
    assert pairs[0].target[2] in (1, 4)


def test_a_through_hole_anchor_without_copper_is_refused_not_guessed(
    engine_factory, board_dir
):
    """A spans-copper anchor is only usable where the other layer really has copper."""
    from pcb_world.agent.observations import LayerConventionError, _anchor_layer

    board = sb.write_board(
        board_dir / "span.kicad_pcb",
        pads=[sb.Pad("PA1", 10.0, 10.0, 1, kind="smd_top"),
              sb.Pad("PB1", 40.0, 20.0, 1, kind="smd_bottom")],
    )
    engine = engine_factory(board)
    session = _session(engine, board)
    resolver = LayerResolver.build(session)

    # Empty space: no copper on the suggested layer, so there is no answer.
    try:
        _anchor_layer(session, 25.0, 25.0, LayerResolver.SPANS_COPPER, resolver,
                      other_layer=1, net_code=1)
    except LayerConventionError as exc:
        assert "no copper there" in str(exc)
    else:  # pragma: no cover - guessing here would route on the wrong layer
        raise AssertionError("a through-hole anchor was resolved without copper")

    # A through-hole pad, on the other hand, is reachable from the other anchor.
    assert _anchor_layer(
        session, 10.0, 10.0, LayerResolver.SPANS_COPPER, resolver, other_layer=1,
        net_code=1,
    ) == 1
    try:
        _anchor_layer(
            session, 10.0, 10.0, LayerResolver.SPANS_COPPER, resolver,
            other_layer=1, net_code=2,
        )
    except LayerConventionError:
        pass
    else:  # pragma: no cover - the wrong-net endpoint must never be scheduled
        raise AssertionError("spans-copper anchor resolved to unrelated net")
