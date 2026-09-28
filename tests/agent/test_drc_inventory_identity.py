"""Identity proof for DRC findings whose item UUIDs the board reuses.

The gate used to key a violation on its UUID pair alone. Two different physical
pairs that happen to share a UUID pair then looked like one identity, so a
candidate that replaced pair A with pair B at an equal count was "no change".
These tests pin the resolution rule that closes that: a UUID carried by exactly
one item is that item, a reused UUID is resolved to the item the finding was
reported on only when one candidate is clearly nearest, and anything else falls
back to condition-refined identity, where a swap is an addition.
"""

from __future__ import annotations

import types

import pytest

from pcb_world.agent.drc_gate import (
    DrcGate,
    ambiguous_pair_keys,
    capture_inventory,
    diff_sets,
    resolve_item_identity,
    take_violations,
    violation_key,
)
from tests.agent.fake_engine import FakeViolation


def _track(uuid, x1, y1, x2, y2, width=0.25, layer=0):
    return types.SimpleNamespace(
        x1_mm=x1, y1_mm=y1, x2_mm=x2, y2_mm=y2, width_mm=width, layer=layer,
        uuid=uuid, net_code=1, net_name="NET",
    )


def _pad(uuid, x, y, ref="P1", name="1", width=1.0, height=1.0):
    return types.SimpleNamespace(
        x_mm=x, y_mm=y, width_mm=width, height_mm=height, layer=0,
        footprint_ref=ref, pad_name=name, uuid=uuid, net_code=1, net_name="NET",
    )


def _item(uuid, kind, *, source="board.tracks", parent_kind="board",
          parent_ref="", layer=0, net_code=1, physical_id=None):
    """One row of the complete inventory, shaped like ``BoardItemInfo``."""
    return types.SimpleNamespace(
        uuid=uuid, kind=kind, source=source, parent_kind=parent_kind,
        parent_ref=parent_ref, layer=layer, net_code=net_code,
        physical_id=physical_id or f"{kind}|{uuid}",
    )


class Engine:
    """Engine double whose DRC answer and item inventory are both explicit.

    The inventory is the engine's complete accessor - the same one the real
    engine exposes - so a double cannot quietly fall back to a copper-only
    inventory that would treat a zone or footprint UUID as unique.
    """

    def __init__(self, violations, *, tracks=(), pads=(), extra_items=(),
                 with_inventory=True):
        self._violations = list(violations)
        self._tracks = list(tracks)
        self._pads = list(pads)
        self._extra_items = list(extra_items)
        self.with_inventory = with_inventory

    def run_drc(self, _rules):
        return list(self._violations)

    def get_last_drc_rules_load_error(self):
        return ""

    def get_project_path(self):
        return ""

    def get_board_items(self):
        rows = [_item(track.uuid, "track") for track in self._tracks]
        rows += [
            _item(pad.uuid, "pad", source="board.pads", parent_kind="footprint",
                  parent_ref=pad.footprint_ref, layer=pad.layer,
                  physical_id=f"{pad.uuid}|{pad.x_mm}|{pad.y_mm}|{pad.footprint_ref}")
            for pad in self._pads
        ]
        return rows + list(self._extra_items)

    def get_pads(self):
        return list(self._pads)

    def get_vias(self):
        return []

    def get_tracks(self):
        return list(self._tracks)


def _clearance(item_a, item_b, x, y, layer=0, nets=("NET",)):
    return FakeViolation(
        error_code=5, error_type="Clearance violation", item_a=item_a, item_b=item_b,
        x_mm=x, y_mm=y, layer=layer, net_names=list(nets),
    )


# ---------------------------------------------------------------------------
# Inventory capture
# ---------------------------------------------------------------------------


def test_inventory_capture_reports_duplicates_and_incompleteness():
    engine = Engine([], tracks=[_track("shared", 0.0, 0.0, 5.0, 0.0),
                                _track("unique", 0.0, 0.0, 1.0, 0.0)],
                    pads=[_pad("shared", 1.0, 1.0, name="1"),
                          _pad("shared", 20.0, 20.0, name="2")])
    inventory = capture_inventory(engine)
    assert inventory.complete and not inventory.problems
    assert inventory.item_count == 4
    assert inventory.distinct_uuids == 2
    assert inventory.duplicated_uuids == 1
    assert inventory.duplicated_items == 3
    assert len(inventory.candidates("shared")) == 3
    assert len(inventory.candidates("unique")) == 1
    assert inventory.to_evidence()["policy"] == "inventory-identity-v2"
    assert dict(inventory.kinds) == {"pad": 2, "track": 2}

    class Broken(Engine):
        def get_board_items(self):
            raise RuntimeError("engine read failed")

    broken = capture_inventory(Broken([], pads=[_pad("only", 0.0, 0.0)]))
    assert broken.complete is False
    assert "get_board_items() failed" in broken.problems[0]
    assert "no complete board inventory" in resolve_item_identity("only", broken)[1]

    class Missing(Engine):
        def __init__(self):                       # no inventory accessors at all
            super().__init__([])

        def get_board_items(self):
            raise AttributeError("get_board_items")

    absent = capture_inventory(Missing())
    assert absent.complete is False
    assert absent.problems


def test_a_uuid_carried_by_one_item_resolves_to_itself():
    inventory = capture_inventory(Engine([], tracks=[_track("u1", 0.0, 0.0, 5.0, 0.0)]))
    identity, why = resolve_item_identity("u1", inventory)
    assert identity == "u1" and why == "unique item"
    # A UUID the inventory does not carry cannot be resolved at all.
    assert resolve_item_identity("ghost", inventory)[0] is None
    assert resolve_item_identity("", inventory)[0] is None
    assert resolve_item_identity(
        "00000000-0000-0000-0000-000000000000", inventory)[0] is None


def test_a_reused_uuid_is_never_resolved_by_proximity():
    """Nearest-candidate geometry is a heuristic, not proof of identity.

    Root review: a 2 mm / 0.1 mm resolution rule would label a guessed physical
    identity "proven". A reused UUID stays unverified however close one candidate
    happens to be, and the ambiguity is repaired on a disposable copy instead.
    """
    inventory = capture_inventory(Engine([], pads=[
        _pad("x", 10.0, 10.0, ref="P1", name="1"),
        _pad("x", 30.0, 10.0, ref="P2", name="2"),
    ]))
    for point in ((10.2, 10.0), (10.0, 10.0), (90.0, 60.0)):
        identity, why = resolve_item_identity("x", inventory)
        assert identity is None
        assert "duplicate identifier" in why


# ---------------------------------------------------------------------------
# The defect: an equal-count swap under a reused UUID
# ---------------------------------------------------------------------------


def test_a_swap_between_two_pairs_under_one_uuid_pair_is_rejected():
    """The reported hole: one finding per report, same key, different physics.

    ``x`` is reused by two pads; ``y`` is a single track. The baseline reports the
    violation on pad 1 and the candidate reports it on pad 2 - one finding each,
    the same UUID pair, the same count. The reused UUID cannot prove which pad the
    finding is about, so the key stays condition-refined and the swap is one
    removal plus one addition instead of "moved".
    """
    pads = [_pad("x", 10.0, 10.0, ref="P1", name="1"),
            _pad("x", 40.0, 10.0, ref="P2", name="2")]
    tracks = [_track("y", 10.0, 11.0, 40.0, 11.0)]
    baseline = take_violations(Engine([_clearance("x", "y", 10.0, 10.5)],
                                      pads=pads, tracks=tracks), "")
    candidate = take_violations(Engine([_clearance("x", "y", 40.0, 10.5)],
                                       pads=pads, tracks=tracks), "")

    # The raw UUID pair is identical in both reports, so a pair-only key cannot
    # see the replacement, and neither report is ambiguous *within itself*.
    assert violation_key(_clearance("x", "y", 10.0, 10.5)) == \
        violation_key(_clearance("x", "y", 40.0, 10.5))
    assert baseline.ambiguous_keys == 0 and candidate.ambiguous_keys == 0
    assert baseline.keys == candidate.keys
    assert baseline.inventory_ambiguous == baseline.keys
    delta = diff_sets(baseline, candidate)
    assert not delta.acceptable
    assert delta.added_occurrences == 1 and delta.resolved_occurrences == 1


def test_a_swap_on_an_unresolvable_uuid_is_refused_by_condition():
    """No inventory: the pair key stays, and a moved condition is an addition.

    Without a board inventory in copper items there is no proof that the pair
    names one physical violation, so the fail-closed branch applies: the key is
    refined by the violation's own condition, and the same-count swap is refused.
    """
    baseline = take_violations(Engine([_clearance("x", "y", 10.0, 10.5)]), "")
    candidate = take_violations(Engine([_clearance("x", "y", 40.0, 10.5)]), "")
    assert baseline.keys == candidate.keys            # the pair key is unchanged
    assert baseline.inventory_ambiguous == baseline.keys
    assert ambiguous_pair_keys(baseline, candidate) == baseline.keys
    delta = diff_sets(baseline, candidate)
    assert not delta.acceptable
    assert delta.added_occurrences == 1 and delta.resolved_occurrences == 1
    assert delta.to_evidence()["added_relevant_occurrences"] == 1


def test_a_uuid_absent_from_the_inventory_cannot_grant_movement_tolerance():
    """A graphic/zone UUID is not in the copper inventory, so it is not proven."""
    baseline = take_violations(Engine([_clearance("graphic", "y", 10.0, 10.0)],
                                      tracks=[_track("y", 0.0, 10.0, 20.0, 10.0)]), "")
    moved = take_violations(Engine([_clearance("graphic", "y", 10.0, 10.004)],
                                   tracks=[_track("y", 0.0, 10.0, 20.0, 10.0)]), "")
    assert baseline.inventory_ambiguous == baseline.keys
    assert not diff_sets(baseline, moved).acceptable


# ---------------------------------------------------------------------------
# Movement tolerance that is still proven
# ---------------------------------------------------------------------------


def test_a_proven_unique_pair_keeps_movement_tolerance():
    tracks = [_track("a", 0.0, 0.0, 20.0, 0.0), _track("b", 0.0, 0.2, 20.0, 0.2)]
    baseline = take_violations(Engine([_clearance("a", "b", 10.0, 0.1)],
                                      tracks=tracks), "")
    moved = take_violations(Engine([_clearance("a", "b", 10.0, 0.1001)],
                                   tracks=tracks), "")
    assert baseline.inventory_ambiguous == frozenset()
    assert diff_sets(baseline, moved).acceptable


def test_a_reused_uuid_finding_is_condition_refined_and_never_movement_tolerant():
    """The reused UUID cannot prove which pad the finding is about, so it does not.

    Both reports name the same UUID pair at the same count; with the identity
    unproven, any change of reported condition is an addition (fail closed), and
    that is the conservative side of a genuinely ambiguous identifier.
    """
    pads = [_pad("x", 10.0, 10.0, ref="P1", name="1"),
            _pad("x", 40.0, 10.0, ref="P2", name="2")]
    tracks = [_track("y", 10.0, 11.0, 40.0, 11.0)]
    baseline = take_violations(Engine([_clearance("x", "y", 10.0, 10.4)],
                                      pads=pads, tracks=tracks), "")
    moved = take_violations(Engine([_clearance("x", "y", 10.05, 10.42)],
                                   pads=pads, tracks=tracks), "")
    assert baseline.keys == moved.keys
    assert baseline.inventory_ambiguous == baseline.keys
    assert not diff_sets(baseline, moved).acceptable
    assert diff_sets(baseline, baseline).acceptable


def test_take_violations_records_the_inventory_it_used():
    engine = Engine([_clearance("x", "y", 10.0, 10.4)],
                    pads=[_pad("x", 10.0, 10.0), _pad("x", 40.0, 10.0)],
                    tracks=[_track("y", 10.0, 11.0, 40.0, 11.0)])
    violations = take_violations(engine, "")
    evidence = violations.to_evidence()
    assert evidence["inventory"]["complete"] is True
    assert evidence["inventory"]["duplicated_uuids"] == 1
    # The finding names the reused pad UUID, so its key has no inventory proof and
    # is refined by condition (the conservative side of an ambiguous identifier).
    assert evidence["keys_without_inventory_proof"] == 1
    assert evidence["inventory"]["policy"] == "inventory-identity-v2"


def test_the_gate_only_tolerates_movement_when_identity_is_proven():
    """A proven (unique-UUID) pair keeps its tolerance; a reused one does not."""
    engine = Engine([_clearance("x", "y", 10.0, 10.4)],
                    pads=[_pad("x", 10.0, 10.0), _pad("x", 40.0, 10.0)],
                    tracks=[_track("y", 10.0, 11.0, 40.0, 11.0)])
    session = types.SimpleNamespace(_engine=engine)
    gate = DrcGate(session)
    baseline = gate.baseline("", "digest-1")
    assert baseline.inventory_ambiguous == baseline.keys
    # The same condition is the same finding.
    assert gate.verify(baseline, "", "digest-1b").acceptable

    unique = Engine([_clearance("a", "b", 10.0, 10.4)],
                    tracks=[_track("a", 0.0, 0.0, 20.0, 0.0),
                            _track("b", 0.0, 0.2, 20.0, 0.2)])
    unique_session = types.SimpleNamespace(_engine=unique)
    unique_gate = DrcGate(unique_session)
    unique_baseline = unique_gate.baseline("", "digest-2")
    assert unique_baseline.inventory_ambiguous == frozenset()
    unique._violations = [_clearance("a", "b", 10.0, 10.4001)]
    assert unique_gate.verify(unique_baseline, "", "digest-2b").acceptable


def test_evidence_round_trip_replays_the_measured_identities():
    """A saved-artifact gate in another process compares the same identities."""
    from pcb_world.agent.drc_gate import (
        violations_evidence,
        violations_from_evidence,
    )

    pads = [_pad("x", 10.0, 10.0, ref="P1", name="1"),
            _pad("x", 40.0, 10.0, ref="P2", name="2")]
    tracks = [_track("y", 10.0, 11.0, 40.0, 11.0)]
    engine = Engine([_clearance("x", "y", 10.0, 10.4)], pads=pads, tracks=tracks)
    captured = take_violations(engine, "")
    replayed = violations_from_evidence(violations_evidence(captured))

    assert replayed.keys == captured.keys
    assert replayed.counts == captured.counts
    assert replayed.violation_keys == captured.violation_keys
    assert replayed.inventory_ambiguous == captured.inventory_ambiguous
    assert replayed.binding == captured.binding
    for left, right in zip(captured.violations, replayed.violations):
        assert int(left.error_code) == int(right.error_code)
        assert str(left.error_type) == str(right.error_type)
        assert (round(float(left.x_mm), 6), round(float(left.y_mm), 6)) == \
            (round(float(right.x_mm), 6), round(float(right.y_mm), 6))

    # The replay is a real comparison input: the swapped candidate is refused.
    swapped = take_violations(Engine([_clearance("x", "y", 40.0, 10.4)],
                                     pads=pads, tracks=tracks), "")
    assert not diff_sets(replayed, swapped).acceptable


def test_evidence_replay_refuses_a_payload_without_its_required_fields():
    """An abbreviated payload is refused, never defaulted into a usable set.

    The full negative matrix lives in ``test_evidence_validation.py``; this pins
    the shape the loader used to accept - a row list and nothing else - and the
    row-level case the old message named.
    """
    from pcb_world.agent.drc_gate import (
        EVIDENCE_POLICY,
        EVIDENCE_PAYLOAD_FIELDS,
        EvidenceSchemaError,
        violations_from_evidence,
    )

    with pytest.raises(EvidenceSchemaError) as caught:
        violations_from_evidence({"violations": [{"error_code": 5}]})
    assert "policy" in "; ".join(caught.value.reasons)

    # Every payload field present, the row itself incomplete: the row's key is
    # named instead of being defaulted.
    payload = {
        field: None for field in EVIDENCE_PAYLOAD_FIELDS
    }
    payload.update({
        "violations": [{"error_code": 5}],
        "inventory_ambiguous": [],
        "context": [],
        "counts": [],
        "total": 1,
        "relevant": 1,
        "connectivity": 0,
        "keys_without_inventory_proof": 0,
        "violations_without_inventory_proof": 0,
        "binding": {"complete": True},
        "policy": EVIDENCE_POLICY,
        "rules_path": "",
    })
    with pytest.raises(EvidenceSchemaError) as caught:
        violations_from_evidence(payload)
    assert "'key'" in "; ".join(caught.value.reasons)


# ---------------------------------------------------------------------------
# The complete inventory: the item kinds the copper accessors never reached
# ---------------------------------------------------------------------------


def test_a_zone_or_graphic_uuid_is_proven_once_the_inventory_covers_it():
    """The defect this replaces: ~half of DRC references named items no accessor read.

    With only tracks/vias/pads inventoried, a violation naming a zone or a
    courtyard graphic had no inventory proof and was condition-refined. Once the
    engine's complete accessor covers those kinds, a UUID it carries exactly
    once *is* an identity again.
    """
    engine = Engine([_clearance("zone-uuid", "graphic-uuid", 10.0, 10.0)],
                    extra_items=[
                        _item("zone-uuid", "zone", source="board.zones",
                              net_code=1),
                        _item("graphic-uuid", "shape",
                              source="footprint.graphics",
                              parent_kind="footprint", parent_ref="U1"),
                    ])
    violations = take_violations(engine, "")
    assert violations.inventory_ambiguous == frozenset()
    evidence = violations.to_evidence()
    assert evidence["keys_without_inventory_proof"] == 0
    assert evidence["inventory"]["complete"] is True
    assert dict(evidence["inventory"]["kinds"]) == {"shape": 1, "zone": 1}


def test_inventory_reports_kinds_it_could_not_classify():
    """An unknown item class is still inventoried, and named - never dropped."""
    inventory = capture_inventory(Engine([], extra_items=[
        _item("mystery-uuid", "other:PCB_MYSTERY"),
    ]))
    assert inventory.complete is True
    assert inventory.unknown_kinds == (("other:PCB_MYSTERY", 1),)
    assert inventory.candidates("mystery-uuid")


def test_inventory_counts_items_that_name_no_identifier():
    """A nil UUID is a real item with no identity; it is counted and never resolved."""
    from pcb_world.agent.drc_gate import NIL_UUID

    inventory = capture_inventory(Engine([], extra_items=[
        _item(NIL_UUID, "shape", source="board.drawings"),
    ]))
    assert inventory.nil_uuids == 1
    assert inventory.item_count == 1
    identity, why = resolve_item_identity(NIL_UUID, inventory)
    assert identity is None and why == "nil uuid"


def test_added_findings_carry_the_item_identities_that_caused_them():
    """A refusal must name its items, not only a class and a coordinate.

    Regression for a real reporting gap: the delta samples carried the class,
    position and nets but dropped ``item_a``/``item_b``, so a consumer could not
    tell a via landing in a pour from a track crossing a pad without re-running
    the DRC - and the phase-14 trial reported empty classes because it read the
    wrong field entirely.
    """
    baseline = take_violations(Engine([_clearance("keep", "y", 10.0, 10.0)]), "")
    candidate = take_violations(
        Engine([_clearance("keep", "y", 10.0, 10.0),
                _clearance("zone-uuid", "track-uuid", 33.0, 44.0, layer=2)]), "")
    delta = diff_sets(baseline, candidate)
    assert not delta.acceptable

    rows = delta.to_evidence()["added_relevant"]
    added = [row for row in rows if row["item_a"] == "zone-uuid"]
    assert added, rows
    assert added[0]["item_b"] == "track-uuid"
    assert added[0]["error_type"] == "Clearance violation"
    assert (added[0]["x_mm"], added[0]["y_mm"]) == (33.0, 44.0)
    assert added[0]["layer"] == 2
