"""Native coverage for the engine's derived-copper refill.

``KiCadEngine.fill_zones`` recomputes zone fill polygons under the given rules.
It is a one-shot board operation, not part of the per-candidate transaction
snapshots, so what matters for the contract is that it is confined to fills and
that it says so honestly when there is nothing to fill.
"""

from __future__ import annotations

import pytest
import re
from pathlib import Path

from pcb_world.agent.session import AgentSession
from pcb_world.agent.terminals import capture_terminals, compare_terminal_partitions
from tests.agent import synthetic_boards as sb


def _session(engine, board: str) -> AgentSession:
    return AgentSession(engine, board_path=board)


def _zone_fill_blocks(board_path: str) -> dict[str, str]:
    """Map each zone UUID to its serialized fill block without normalizing it."""
    text = Path(board_path).read_text(encoding="utf-8")
    result: dict[str, str] = {}
    position = 0
    while True:
        start = text.find("(zone", position)
        if start < 0:
            return result
        depth = 0
        quoted = escaped = False
        for end in range(start, len(text)):
            char = text[end]
            if escaped:
                escaped = False
                continue
            if char == "\\" and quoted:
                escaped = True
                continue
            if char == '"':
                quoted = not quoted
                continue
            if quoted:
                continue
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    break
        block = text[start:end + 1]
        layer = re.search(r'\(layer\s+"([^"]+)"\)', block)
        uuid = re.search(r'\(uuid\s+"([^"]+)"\)', block)
        fill_start = block.find("(filled_polygon")
        fill = block[fill_start:] if fill_start >= 0 else ""
        if layer and uuid:
            result[uuid.group(1)] = f"{layer.group(1)}\n{fill}"
        position = end + 1


def test_fill_zones_on_a_zone_free_board_changes_no_copper(
    native_engine_checked, board_dir
):
    """No zones means nothing to refill: every copper count must be untouched."""
    board = sb.write_board(
        board_dir / "fill_none.kicad_pcb",
        pads=[sb.Pad("PA1", 10.0, 10.0, 1), sb.Pad("PB1", 40.0, 10.0, 1)],
        segments=[sb.Segment(25.0, 8.0, 25.0, 12.0, 2)],
    )
    from tests.agent.conftest import BUILD_DIR  # noqa: F401 - keeps the fixture contract
    from pcb_world.engine import KiCadEngine

    engine = KiCadEngine(board)
    try:
        session = _session(engine, board)
        before = (engine.get_track_count(), engine.get_via_count(),
                  engine.get_unrouted_count(), session.board_digest())
        assert engine.fill_zones("") is True
        after = (engine.get_track_count(), engine.get_via_count(),
                 engine.get_unrouted_count(), session.board_digest())
        assert after == before, "a board with no zones must not change"
    finally:
        engine.close()


def test_fill_zones_is_exposed_with_a_rules_argument(
    native_engine_checked, board_dir
):
    """The binding takes an optional rules path (the DRC engine it fills under)."""
    import inspect

    from pcb_world.engine.kicad_engine import KiCadEngine

    signature = inspect.signature(KiCadEngine.fill_zones)
    assert "rules_path" in signature.parameters
    assert signature.parameters["rules_path"].default == ""


def test_real_zone_fill_preserves_zone_connectivity_across_saved_reopen(
    native_engine_checked, board_dir
):
    """The terminal gate captures a pour-mediated connection on both loads."""
    import hashlib

    from pcb_world.engine import KiCadEngine

    board = sb.write_board(
        board_dir / "fill_connected_zone.kicad_pcb",
        pads=[sb.Pad("PA1", 10, 10, 1), sb.Pad("PB1", 20, 10, 1)],
        nets=(1,), width=40, height=24,
        zones=[sb.Zone(1, 5, 5, 25, 18)],
    )
    rules = sb.write_rules(board_dir / "valid.kicad_dru", 0.25)
    before_hash = hashlib.sha256(open(board, "rb").read()).hexdigest()
    engine = KiCadEngine(board)
    try:
        before = capture_terminals(engine)
        assert before.complete
        assert len(set(before.cluster_of.values())) == 2
        assert engine.fill_zones(rules) is True
        engine.build_connectivity()
        after_fill = capture_terminals(engine)
        assert after_fill.complete
        assert len(set(after_fill.cluster_of.values())) == 1
        candidate = str(board_dir / "fill_connected_zone_saved.kicad_pcb")
        engine.save(candidate)
    finally:
        engine.close()
    assert hashlib.sha256(open(board, "rb").read()).hexdigest() == before_hash

    reopened = KiCadEngine(candidate)
    try:
        reopened_partition = capture_terminals(reopened)
        assert reopened_partition.complete
        assert compare_terminal_partitions(after_fill, reopened_partition).ok
    finally:
        reopened.close()


def test_copper_only_refill_preserves_non_copper_artwork_fill_exactly(
    native_engine_checked, board_dir
):
    """Silk/mask converted artwork must not be regenerated from its outline."""
    from pcb_world.engine import KiCadEngine

    board = sb.write_board(
        board_dir / "fill_mixed_zone_layers.kicad_pcb",
        pads=[sb.Pad("PA1", 10, 10, 1)], nets=(1,), width=40, height=24,
        zones=[
            sb.Zone(1, 5, 5, 25, 18, layer="F.Cu"),
            sb.Zone(0, 5, 5, 25, 18, layer="B.SilkS", fill_points=(
                (8, 8), (12, 8), (12, 10), (10, 10), (10, 12), (8, 12),
            )),
            sb.Zone(0, 5, 5, 25, 18, layer="F.Mask", fill_points=(
                (9, 9), (11, 9), (11, 11), (9, 11),
            )),
        ],
    )
    rules = sb.write_rules(board_dir / "mixed_valid.kicad_dru", 0.25)
    engine = KiCadEngine(board)
    before = str(board_dir / "mixed_before.kicad_pcb")
    after = str(board_dir / "mixed_after.kicad_pcb")
    try:
        engine.save(before)
        before_fills = _zone_fill_blocks(before)
        assert {layer.splitlines()[0] for layer in before_fills.values()} >= {"B.SilkS", "F.Mask"}
        assert engine.fill_zones(rules) is True
        engine.save(after)
    finally:
        engine.close()
    after_fills = _zone_fill_blocks(after)
    for uuid, before_fill in before_fills.items():
        if before_fill.splitlines()[0] in {"B.SilkS", "F.Mask"}:
            assert after_fills[uuid] == before_fill
    assert any(value.startswith("F.Cu\n") for value in after_fills.values())


def test_fill_zones_rejects_missing_and_malformed_rules_before_mutation(
    native_engine_checked, board_dir
):
    """Bad rule inputs fail before changing zone fill or committed connectivity."""
    from pcb_world.engine import KiCadEngine

    board = sb.write_board(
        board_dir / "fill_bad_rules.kicad_pcb",
        pads=[sb.Pad("PA1", 10, 10, 1), sb.Pad("PB1", 20, 10, 1)],
        nets=(1,), width=40, height=24,
        zones=[sb.Zone(1, 5, 5, 25, 18)],
    )
    malformed = board_dir / "malformed.kicad_dru"
    malformed.write_text("(version 1) (rule invalid", encoding="utf-8")
    engine = KiCadEngine(board)
    try:
        original = capture_terminals(engine)
        assert original.complete
        # Two different refusals, both before any mutation: a path that does not
        # exist is reported as such, and a file that exists but cannot be parsed
        # is reported as a load failure. Asserting the precise message keeps the
        # cases distinguishable instead of accepting either for both.
        with pytest.raises(Exception, match="design rules file not found"):
            engine.fill_zones(str(board_dir / "missing.kicad_dru"))
        with pytest.raises(Exception, match="design rules failed to load"):
            engine.fill_zones(str(malformed))
        assert compare_terminal_partitions(original, capture_terminals(engine)).ok
    finally:
        engine.close()


def test_fill_zones_refuses_an_active_router_session(native_engine_checked, board_dir):
    from pcb_world.engine import KiCadEngine

    board = sb.write_board(
        board_dir / "fill_active_route.kicad_pcb",
        pads=[sb.Pad("PA1", 10, 10, 1), sb.Pad("PB1", 30, 10, 1)],
        nets=(1,), width=40, height=24,
        zones=[sb.Zone(1, 5, 5, 35, 18)],
    )
    rules = sb.write_rules(board_dir / "active_valid.kicad_dru", 0.25)
    engine = KiCadEngine(board)
    try:
        assert engine.start_route(10, 10, 1)
        with pytest.raises(Exception, match="routing session is active"):
            engine.fill_zones(rules)
        assert engine.is_routing()
    finally:
        if engine.is_routing():
            engine.cancel_route()
        engine.close()
