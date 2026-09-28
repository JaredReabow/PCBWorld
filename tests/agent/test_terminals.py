"""Terminal connectivity is a partition, so counts cannot hide a swap.

Every test here is a case a per-net group *count* would pass and the contract
must refuse: same totals with swapped terminals, a lost connection masked by a
gained one, and a terminal that disappeared.
"""

from __future__ import annotations

from types import SimpleNamespace

from pcb_world.agent.terminals import (
    TerminalPartition,
    compare_terminal_partitions,
)


def _partition(net_terminals, cluster_of) -> TerminalPartition:
    return TerminalPartition(
        net_terminals={k: tuple(v) for k, v in net_terminals.items()},
        cluster_of=dict(cluster_of),
    )


def test_same_net_same_group_count_with_swapped_terminals_is_refused():
    """GND has 2 clusters on both boards - but a different pad is joined."""
    reference = _partition(
        {"GND": ("U1.1", "U1.2", "U2.1", "U2.2")},
        {"U1.1": "c1", "U1.2": "c1", "U2.1": "c2", "U2.2": "c2"},
    )
    candidate = _partition(
        {"GND": ("U1.1", "U1.2", "U2.1", "U2.2")},
        {"U1.1": "c1", "U2.1": "c1", "U1.2": "c2", "U2.2": "c2"},
    )
    # Identical group counts, on both nets and in total.
    assert len(set(reference.cluster_of.values())) == len(set(candidate.cluster_of.values()))
    delta = compare_terminal_partitions(reference, candidate)
    assert delta.ok is False
    assert delta.split_relations
    assert any("no longer connected" in reason for reason in delta.reasons)


def test_a_lost_connection_masked_by_a_gained_one_is_refused():
    """Cluster counts match; one relation is lost and another appears."""
    reference = _partition(
        {"A": ("X.1", "X.2", "Y.1", "Y.2")},
        {"X.1": "a1", "X.2": "a1", "Y.1": "a2", "Y.2": "a2"},
    )
    candidate = _partition(
        {"A": ("X.1", "X.2", "Y.1", "Y.2")},
        {"X.1": "b1", "Y.1": "b1", "X.2": "b2", "Y.2": "b2"},
    )
    delta = compare_terminal_partitions(reference, candidate)
    assert delta.ok is False
    assert len(delta.split_relations) >= 1


def test_a_removed_terminal_or_net_is_refused():
    reference = _partition(
        {"A": ("X.1", "X.2"), "B": ("Z.1", "Z.2")},
        {"X.1": "a1", "X.2": "a1", "Z.1": "a2", "Z.2": "a2"},
    )
    missing_terminal = _partition(
        {"A": ("X.1",), "B": ("Z.1", "Z.2")},
        {"X.1": "a1", "Z.1": "a2", "Z.2": "a2"},
    )
    delta = compare_terminal_partitions(reference, missing_terminal)
    assert delta.ok is False
    assert delta.vanished_terminals == ("X.2",)

    missing_net = _partition(
        {"A": ("X.1", "X.2")},
        {"X.1": "a1", "X.2": "a1"},
    )
    delta = compare_terminal_partitions(reference, missing_net)
    assert delta.ok is False
    assert delta.vanished_nets == ("B",)


def test_an_unchanged_partition_passes_even_after_reclustering_names():
    """Cluster names are opaque; only the equivalence matters."""
    reference = _partition(
        {"A": ("X.1", "X.2")}, {"X.1": "left", "X.2": "left"},
    )
    candidate = _partition(
        {"A": ("X.1", "X.2")}, {"X.1": "renamed", "X.2": "renamed"},
    )
    delta = compare_terminal_partitions(reference, candidate)
    assert delta.ok is True


def test_a_gained_connection_is_reported_but_not_a_failure():
    """New closures are progress; only lost relations refuse."""
    reference = _partition(
        {"A": ("X.1", "X.2", "Y.1", "Y.2")},
        {"X.1": "a1", "X.2": "a1", "Y.1": "a2", "Y.2": "a2"},
    )
    candidate = _partition(
        {"A": ("X.1", "X.2", "Y.1", "Y.2")},
        {"X.1": "b1", "X.2": "b1", "Y.1": "b1", "Y.2": "b1"},
    )
    delta = compare_terminal_partitions(reference, candidate)
    assert delta.ok is True


def test_partition_round_trips_through_evidence():
    partition = _partition({"A": ("X.1",)}, {"X.1": "c"})
    restored = TerminalPartition.from_evidence(partition.to_evidence())
    assert restored.net_terminals == partition.net_terminals
    assert restored.cluster_of == partition.cluster_of


def test_net_reassignment_of_same_physical_pad_is_refused():
    reference = _partition({"A": ("pad:one",), "B": ("pad:two",)},
                           {"pad:one": "a", "pad:two": "b"})
    candidate = _partition({"B": ("pad:one", "pad:two")},
                           {"pad:one": "b", "pad:two": "b"})
    delta = compare_terminal_partitions(reference, candidate)
    assert not delta.ok
    assert delta.changed_net_membership == ("pad:one",)


def test_missing_cluster_accessor_fails_closed_instead_of_anchor_fallback():
    from pcb_world.agent.terminals import capture_terminals

    class OldEngine:
        def get_pads(self):
            return [SimpleNamespace(net_code=1, footprint_ref="U1", pad_name="1")]

        def get_net_names(self):
            return {1: "GND"}

    capture = capture_terminals(OldEngine())
    assert not capture.complete
    assert "accessor is unavailable" in capture.reasons[0]
    assert not compare_terminal_partitions(capture, capture).ok


def test_native_capture_checks_complete_uuid_inventory_and_duplicate_pad_labels():
    from pcb_world.agent.terminals import capture_terminals

    class Engine:
        def __init__(self, members):
            self.members = members
            self.pads = [
                SimpleNamespace(uuid="same-uuid", physical_id="pad-one|geom-a",
                                net_code=1, footprint_ref="U1", pad_name="1"),
                # Duplicate reference/pad labels must remain distinct physical pads.
                SimpleNamespace(uuid="same-uuid", physical_id="pad-two|geom-b",
                                net_code=1, footprint_ref="U1", pad_name="1"),
            ]

        def get_net_names(self):
            return {1: "GND"}

        def get_pads(self):
            return self.pads

        def get_pad_cluster_members(self):
            return [(1, members) for members in self.members]

    complete = capture_terminals(Engine([["pad-one|geom-a"], ["pad-two|geom-b"]]))
    assert complete.complete
    assert len(complete.terminals) == 2
    assert len(set(complete.net_terminals["GND"])) == 2
    omitted = capture_terminals(Engine([["pad-one|geom-a"]]))
    assert not omitted.complete
    assert "omitted 1" in omitted.reasons[0]
    duplicate = capture_terminals(Engine([["pad-one|geom-a"],
                                          ["pad-one|geom-a", "pad-two|geom-b"]]))
    assert not duplicate.complete
    assert "multiple clusters" in duplicate.reasons[0]
