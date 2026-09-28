"""Violation classification and the engine-enum binding (pure Python)."""

from __future__ import annotations

import pytest

from pcb_world.agent.drc_gate import (
    CONNECTIVITY_ERROR_CODES,
    DRCE_DANGLING_TRACK,
    DRCE_DANGLING_VIA,
    DRCE_DRILLED_HOLES_COLOCATED,
    DRCE_DRILLED_HOLES_TOO_CLOSE,
    DRCE_UNCONNECTED_ITEMS,
    ENUM_EXPECTATIONS,
    is_connectivity_finding,
    parse_drc_enum,
    violation_key,
)

from tests.agent.fake_engine import FakeViolation


_ENUM_SNIPPET = """
enum PCB_DRC_CODE {
    DRCE_FIRST = 1,
    DRCE_UNCONNECTED_ITEMS = DRCE_FIRST, // items are unconnected
    DRCE_SHORTING_ITEMS,
    DRCE_ALLOWED_ITEMS,
    DRCE_TEXT_ON_EDGECUTS,
    DRCE_CLEARANCE,
    DRCE_CREEPAGE,
    DRCE_TRACKS_CROSSING,
    DRCE_EDGE_CLEARANCE,
    DRCE_ZONES_INTERSECT,
    DRCE_ISOLATED_COPPER,
    DRCE_STARVED_THERMAL,
    DRCE_DANGLING_VIA,
    DRCE_DANGLING_TRACK,
    DRCE_DRILLED_HOLES_TOO_CLOSE,
    DRCE_DRILLED_HOLES_COLOCATED,
};
"""


def test_parse_drc_enum_reads_the_positional_values():
    values = parse_drc_enum(_ENUM_SNIPPET)
    assert values["DRCE_UNCONNECTED_ITEMS"] == 1
    assert values["DRCE_DANGLING_VIA"] == 12
    assert values["DRCE_DANGLING_TRACK"] == 13
    assert values["DRCE_DRILLED_HOLES_TOO_CLOSE"] == 14
    assert values["DRCE_DRILLED_HOLES_COLOCATED"] == 15


def test_parse_drc_enum_raises_on_a_foreign_source():
    with pytest.raises(ValueError):
        parse_drc_enum("int main() { return 0; }")


def test_the_constants_match_the_parsed_snippet():
    values = parse_drc_enum(_ENUM_SNIPPET)
    for name, expected in ENUM_EXPECTATIONS.items():
        assert values[name] == expected, (
            f"{name}: module says {expected}, the engine enum says {values[name]}"
        )


def test_classification_matches_the_corrected_enum():
    """The defect this guards: 14 is hole spacing (relevant), 12 is a dangling via."""
    assert DRCE_UNCONNECTED_ITEMS == 1
    assert DRCE_DANGLING_VIA == 12
    assert DRCE_DANGLING_TRACK == 13
    assert {1, 12, 13} == set(CONNECTIVITY_ERROR_CODES)
    assert DRCE_DRILLED_HOLES_TOO_CLOSE not in CONNECTIVITY_ERROR_CODES
    assert DRCE_DRILLED_HOLES_COLOCATED not in CONNECTIVITY_ERROR_CODES

    dangling_via = FakeViolation(error_code=12, error_type="Via is not connected")
    dangling_track = FakeViolation(error_code=13, error_type="Track has unconnected end")
    hole_spacing = FakeViolation(
        error_code=14, error_type="Drilled hole too close to other hole",
        message="min 0.2mm", severity=0x20,
    )
    holes_colocated = FakeViolation(error_code=15, error_type="Drilled holes co-located")
    clearance = FakeViolation(error_code=5, error_type="Clearance violation")

    assert is_connectivity_finding(dangling_via) is True
    assert is_connectivity_finding(dangling_track) is True
    assert is_connectivity_finding(hole_spacing) is False       # the fixed defect
    assert is_connectivity_finding(holes_colocated) is False
    assert is_connectivity_finding(clearance) is False


def test_violation_key_prefers_item_identity():
    a = FakeViolation(error_code=14, error_type="hole", item_a="aaaa", item_b="bbbb",
                      x_mm=1.0, y_mm=1.0)
    moved = FakeViolation(error_code=14, error_type="hole", item_a="aaaa", item_b="bbbb",
                          x_mm=9.0, y_mm=9.0)
    other = FakeViolation(error_code=14, error_type="hole", item_a="aaaa", item_b="cccc")
    assert violation_key(a) == violation_key(moved)
    assert violation_key(a) != violation_key(other)

    # Without UUIDs the geometry and nets carry the identity.
    geom_a = FakeViolation(error_code=14, error_type="hole", x_mm=1.0, y_mm=1.0)
    geom_b = FakeViolation(error_code=14, error_type="hole", x_mm=1.0, y_mm=1.0)
    geom_c = FakeViolation(error_code=14, error_type="hole", x_mm=1.0, y_mm=2.0)
    assert violation_key(geom_a) == violation_key(geom_b)
    assert violation_key(geom_a) != violation_key(geom_c)


def test_a_shared_violation_key_keeps_every_violation_and_its_multiplicity():
    """A pair-based key is not unique; the set must stay a multiset.

    Measured on the frozen source: 226 keys carry more than one violation (998
    relevant ones). Keyed as a dict, the set dropped 835 violations and could not
    see one added violation under a key that already existed.
    """
    from pcb_world.agent.drc_gate import diff_sets, take_violations

    class Engine:
        def __init__(self, violations):
            self._violations = violations

        def run_drc(self, _rules):
            return list(self._violations)

        def get_last_drc_rules_load_error(self):
            return ""

        def get_project_path(self):
            return ""

        def get_pads(self):
            return []

    shared = dict(error_code=14, error_type="Drilled hole too close to other hole",
                  item_a="aaaa", item_b="bbbb")
    baseline_violations = [FakeViolation(**shared, x_mm=1.0, y_mm=1.0)]
    candidate_violations = [
        FakeViolation(**shared, x_mm=1.0, y_mm=1.0),
        FakeViolation(**shared, x_mm=2.0, y_mm=2.0),   # a second hole pair, same key
    ]
    baseline = take_violations(Engine(baseline_violations), "")
    candidate = take_violations(Engine(candidate_violations), "")

    assert baseline.total == 1 and candidate.total == 2
    assert baseline.keys == candidate.keys                  # the key is unchanged
    assert baseline.counts == {violation_key(baseline_violations[0]): 1}
    assert candidate.counts == {violation_key(baseline_violations[0]): 2}
    assert baseline.ambiguous_keys == 0
    assert candidate.ambiguous_keys == 1
    assert len(candidate.members[violation_key(baseline_violations[0])]) == 2

    delta = diff_sets(baseline, candidate)
    assert not delta.acceptable                             # a set diff would pass this
    assert delta.added_occurrences == 1
    ambiguity = delta.to_evidence()["shared_key_ambiguity"]
    assert ambiguity["baseline_keys_with_more_than_one_violation"] == 0
    assert ambiguity["candidate_keys_with_more_than_one_violation"] == 1
    assert ambiguity["keys_disambiguated_by_condition"] == 1
    # This engine double exposes no item inventory, so no UUID is proven and the
    # key is refined by condition as well (the conservative side).
    assert ambiguity["baseline_keys_without_inventory_proof"] == 1
    assert ambiguity["baseline_inventory"]["complete"] is False


def test_one_reused_uuid_pair_reported_twice_is_disambiguated_by_condition():
    """Real duplicated-ID ambiguity: one board, one key, two different pairs.

    Counts alone cannot see this (2 vs 2 of the same key); splitting the key by
    condition makes a replacement visible, and a candidate that keeps only one of
    the two conditions is an addition plus a removal.
    """
    from pcb_world.agent.drc_gate import diff_sets, take_violations

    class Engine:
        """Two-item board whose only items carry the pair's UUIDs."""

        def __init__(self, violations, *, inventory=True):
            self._violations = violations
            self._inventory = inventory

        def run_drc(self, _rules):
            return list(self._violations)

        def get_last_drc_rules_load_error(self):
            return ""

        def get_project_path(self):
            return ""

        def get_pads(self):
            return []

        def get_vias(self):
            return []

        def get_board_items(self):
            if not self._inventory:
                return []
            import types

            def track(uuid, x):
                return types.SimpleNamespace(
                    uuid=uuid, kind="track", source="board.tracks",
                    parent_kind="board", parent_ref="", layer=0, net_code=1,
                    physical_id=f"track|{uuid}|{x}",
                )

            return [track("shared-uuid-1", 10.0), track("shared-uuid-2", 11.0)]

    pair = dict(error_code=5, error_type="Clearance violation",
                item_a="shared-uuid-1", item_b="shared-uuid-2")
    baseline = take_violations(
        Engine([FakeViolation(**pair, x_mm=10.0, y_mm=10.0),
                FakeViolation(**pair, x_mm=11.0, y_mm=11.0)]), "")
    # The candidate keeps the same *count* under the key but at a third place:
    # one of the two original conditions is gone and a new one appeared.
    candidate = take_violations(
        Engine([FakeViolation(**pair, x_mm=10.0, y_mm=10.0),
                FakeViolation(**pair, x_mm=40.0, y_mm=40.0)]), "")
    assert baseline.keys == candidate.keys          # identical pair key
    assert baseline.counts == candidate.counts == {next(iter(baseline.counts)): 2}
    delta = diff_sets(baseline, candidate)
    assert not delta.acceptable
    assert delta.added_occurrences == 1 and delta.resolved_occurrences == 1

    # A violation that merely moved is still the same identity when the board
    # inventory proves the pair names one physical violation: proven pair-keyed
    # identity is what keeps a re-route from looking like a new violation.
    single_baseline = take_violations(
        Engine([FakeViolation(**pair, x_mm=10.0, y_mm=10.0)]), "")
    moved = take_violations(
        Engine([FakeViolation(**pair, x_mm=10.0, y_mm=10.0001)]), "")
    assert single_baseline.inventory_ambiguous == frozenset()
    assert diff_sets(single_baseline, moved).acceptable

    # The same two reports without the inventory *cannot* prove that identity, so
    # the movement is refused instead of being waved through (fail closed).
    unproven_baseline = take_violations(
        Engine([FakeViolation(**pair, x_mm=10.0, y_mm=10.0)], inventory=False), "")
    unproven_moved = take_violations(
        Engine([FakeViolation(**pair, x_mm=10.0, y_mm=10.0001)], inventory=False), "")
    assert unproven_baseline.inventory_ambiguous == unproven_baseline.keys
    assert not diff_sets(unproven_baseline, unproven_moved).acceptable


def test_the_added_class_histogram_is_not_capped_by_the_row_sample():
    """A refusal's class counts must not stop at the row sample's ``limit``.

    ``to_evidence`` carries a few ``added_relevant`` rows so a caller can see
    *which* findings were added; the list is capped because a checkpoint is not a
    place to put an unbounded block. Counting that capped list as a histogram is
    the defect this pins: a delta that added more class instances than the cap
    would be recorded as if it had added only ``limit`` of them - on the phase-17
    trial, 50 recorded as eight.
    """
    from pcb_world.agent.drc_gate import diff_sets, take_violations

    class Engine:
        def __init__(self, violations):
            self._violations = violations

        def run_drc(self, _rules):
            return list(self._violations)

        def get_last_drc_rules_load_error(self):
            return ""

        def get_project_path(self):
            return ""

        def get_pads(self):
            return []

    added = [
        FakeViolation(error_code=5, error_type="Clearance violation",
                      x_mm=10.0 + index, y_mm=10.0, layer=0)
        for index in range(6)
    ] + [
        FakeViolation(error_code=16, error_type="Hole clearance violation",
                      x_mm=20.0 + index, y_mm=20.0, layer=0)
        for index in range(4)
    ]
    baseline = take_violations(Engine([]), "")
    candidate = take_violations(Engine(added), "")
    delta = diff_sets(baseline, candidate)
    evidence = delta.to_evidence()

    assert delta.added_relevant and len(delta.added_relevant) == 10
    assert evidence["added_relevant_count"] == 10
    # The row sample stays capped...
    assert len(evidence["added_relevant"]) == 8
    # ...and the histogram is not derived from it.
    assert evidence["added_relevant_class_counts"] == {
        "Clearance violation": 6,
        "Hole clearance violation": 4,
    }
    assert (sum(evidence["added_relevant_class_counts"].values())
            == evidence["added_relevant_count"])
    # What the capped rows alone would have said. The rows are ordered by identity
    # text, not by class, so the sample is not even a proportional one - which is
    # the second reason to count identities rather than rows.
    rows_only: dict[str, int] = {}
    for row in evidence["added_relevant"]:
        rows_only[row["error_type"]] = rows_only.get(row["error_type"], 0) + 1
    assert sum(rows_only.values()) == len(evidence["added_relevant"]) == 8
    assert rows_only != evidence["added_relevant_class_counts"]
