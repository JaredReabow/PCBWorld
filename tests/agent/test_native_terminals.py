"""Native terminal membership: the partition acceptance is built on."""

from __future__ import annotations

from pcb_world.agent.terminals import (
    capture_terminals,
    compare_terminal_partitions,
)
from tests.agent import synthetic_boards as sb


def test_native_cluster_membership_names_stable_terminals(
    native_engine_checked, board_dir
):
    """Membership comes from native connectivity, named ``REF.PAD``."""
    board = sb.write_board(
        board_dir / "terminals.kicad_pcb",
        pads=[sb.Pad("PA1", 10.0, 10.0, 1), sb.Pad("PB1", 40.0, 10.0, 1),
              sb.Pad("PC1", 10.0, 20.0, 2), sb.Pad("PD1", 40.0, 20.0, 2)],
        nets=(1, 2),
    )
    from pcb_world.engine import KiCadEngine

    engine = KiCadEngine(board)
    try:
        rows = engine.get_pad_cluster_members()
        terminals = {t for _net, members in rows for t in members}
        pad_ids = {pad.physical_id for pad in engine.get_pads() if pad.net_code > 0}
        assert pad_ids <= terminals
        # The count and the membership describe the same grouping.
        assert len(rows) == sum(engine.get_pad_groups().values())

        partition = capture_terminals(engine)
        assert len(partition.cluster_of) == len(terminals)
        assert partition.complete
        assert any(t.startswith("PA1.1@") for t in partition.cluster_of)
        assert any(t.startswith("PB1.1@") for t in partition.cluster_of)
        # Each net's two pads start apart, so each is its own cluster.
        pa = next(t for t in partition.cluster_of if t.startswith("PA1.1@"))
        pb = next(t for t in partition.cluster_of if t.startswith("PB1.1@"))
        assert partition.cluster_of[pa] != partition.cluster_of[pb]
        assert compare_terminal_partitions(partition, partition).ok is True
    finally:
        engine.close()


def test_a_closed_connection_merges_terminals_natively(
    native_engine_checked, board_dir
):
    """Routing one pair must show up as two terminals sharing a cluster."""
    board = sb.write_board(
        board_dir / "terminals_closed.kicad_pcb",
        pads=[sb.Pad("PA1", 10.0, 10.0, 1), sb.Pad("PB1", 40.0, 10.0, 1)],
        nets=(1,),
    )
    from pcb_world.agent.session import AgentSession
    from pcb_world.engine import KiCadEngine

    engine = KiCadEngine(board)
    try:
        session = AgentSession(engine, board_path=board)
        before = capture_terminals(engine)
        pa = next(t for t in before.cluster_of if t.startswith("PA1.1@"))
        pb = next(t for t in before.cluster_of if t.startswith("PB1.1@"))
        assert before.cluster_of[pa] != before.cluster_of[pb]

        result = session.connect_targets(
            (10.0, 10.0, 1), (40.0, 10.0, 1), "walkaround",
            token=session.snapshot().token,
        )
        assert result.connected and result.accepted

        after = capture_terminals(engine)
        assert after.cluster_of[pa] == after.cluster_of[pb]
        # Closing is a merge: nothing the reference had may be lost.
        assert compare_terminal_partitions(before, after).ok is True
    finally:
        engine.close()
