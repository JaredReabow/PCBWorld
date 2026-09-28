"""A serialized DRC baseline is only proof when the payload says so completely.

The loader and the serializer are the same module, so nothing stops them being
consistent with each other; what these tests stop is a payload that is
*incomplete* and still compares "clean". The concrete exploit: deleting the
``inventory_ambiguous`` list from both sides of a comparison left the ambiguity
union empty, so a same-count physical substitution under a reused UUID pair was
reported as no change. It survived because every missing field defaulted (no
ambiguity, no condition, x/y = 0) instead of refusing.

Each guard below is a negative case from the phase-29 evidence-validation task;
the positive cases pin that honest evidence - an unchanged set, proven movement,
an ambiguous replacement, a raised multiplicity - keeps its intended verdict.
"""

from __future__ import annotations

import json

import pytest

from pcb_world.agent.drc_gate import (
    EVIDENCE_POLICY,
    EvidenceSchemaError,
    diff_sets,
    evidence_schema_problems,
    take_violations,
    validate_evidence_payload,
    violations_evidence,
    violations_from_evidence,
)
from tests.agent.test_drc_inventory_identity import (  # noqa: E402
    Engine,
    _clearance,
    _pad,
    _track,
)


# ---------------------------------------------------------------------------
# Fixtures: real captures, so the payload under test is the real serialization
# ---------------------------------------------------------------------------


def ambiguous_engine(*, second_pad_x: float = 40.0):
    """A reused pad UUID plus a track: the finding's UUID pair is not proof."""
    return Engine(
        [_clearance("x", "y", second_pad_x, 10.5)],
        pads=[_pad("x", 10.0, 10.0, ref="P1", name="1"),
              _pad("x", second_pad_x, 10.0, ref="P2", name="2")],
        tracks=[_track("y", 10.0, 11.0, 40.0, 11.0)],
    )


def unique_engine(x: float = 10.0, y: float = 0.1):
    """Two distinct, uniquely identified tracks: the pair *is* an identity."""
    return Engine(
        [_clearance("a", "b", x, y)],
        tracks=[_track("a", 0.0, 0.0, 20.0, 0.0),
                _track("b", 0.0, 0.2, 20.0, 0.2)],
    )


def captured(engine) -> dict:
    """The payload a real capture writes, after a JSON round trip."""
    return json.loads(json.dumps(violations_evidence(take_violations(engine, ""))))


def _rows(payload) -> list:
    return payload["violations"]


def _recount(payload) -> None:
    """Re-derive the declared accounting from the payload's own rows."""
    counts: dict = {}
    for row in _rows(payload):
        key = json.dumps(row["key"])
        counts[key] = counts.get(key, 0) + 1
    relevant = sum(1 for row in _rows(payload) if row["relevant"])
    payload["total"] = len(_rows(payload))
    payload["relevant"] = relevant
    payload["connectivity"] = len(_rows(payload)) - relevant
    payload["counts"] = [
        {"key": json.loads(key), "count": count}
        for key, count in counts.items()
    ]
    ambiguous = payload["inventory_ambiguous"]
    payload["keys_without_inventory_proof"] = len(ambiguous)
    payload["violations_without_inventory_proof"] = sum(
        counts[json.dumps(key)] for key in ambiguous
    ) if ambiguous else 0


def refusal(payload) -> str:
    """Assert the payload refuses, and return the joined reasons."""
    with pytest.raises(EvidenceSchemaError) as caught:
        violations_from_evidence(payload)
    return "; ".join(caught.value.reasons)


# ---------------------------------------------------------------------------
# Honest evidence keeps its verdict
# ---------------------------------------------------------------------------


def test_a_valid_payload_round_trips_unchanged():
    payload = captured(unique_engine())
    assert evidence_schema_problems(payload) == ()
    replayed = violations_from_evidence(payload)
    original = take_violations(unique_engine(), "")
    assert replayed.keys == original.keys
    assert replayed.counts == original.counts
    assert replayed.inventory_ambiguous == original.inventory_ambiguous
    # ... and the replay serialises back to the same complete payload.
    assert violations_evidence(replayed) == payload


def test_an_empty_multiset_is_valid_and_not_an_error():
    payload = captured(Engine([]))
    assert payload["total"] == 0
    validate_evidence_payload(payload)
    assert violations_from_evidence(payload).total == 0


def test_proven_movement_still_keeps_its_identity_through_the_payload():
    before = captured(unique_engine(y=0.1))
    after = captured(unique_engine(y=0.1001))
    delta = diff_sets(violations_from_evidence(before),
                      violations_from_evidence(after))
    assert delta.acceptable


def test_an_ambiguous_replacement_is_still_refused_through_the_payload():
    """The honest payload refuses the same-count pad swap. The ambiguity list is
    what carries that: the loader must never let it be dropped."""
    before = captured(ambiguous_engine(second_pad_x=10.0))
    after = captured(ambiguous_engine(second_pad_x=40.0))
    assert before["inventory_ambiguous"], "the capture must be ambiguous"
    assert _rows(before)[0]["key"] == _rows(after)[0]["key"]
    delta = diff_sets(violations_from_evidence(before),
                      violations_from_evidence(after))
    assert not delta.acceptable
    assert delta.added_occurrences == 1 and delta.resolved_occurrences == 1


def test_a_raised_multiplicity_is_still_an_addition_through_the_payload():
    base = ambiguous_engine(second_pad_x=10.0)
    extra = Engine(
        [_clearance("x", "y", 10.0, 10.5), _clearance("x", "y", 10.0, 10.5)],
        pads=[_pad("x", 10.0, 10.0, ref="P1", name="1"),
              _pad("x", 40.0, 10.0, ref="P2", name="2")],
        tracks=[_track("y", 10.0, 11.0, 40.0, 11.0)],
    )
    delta = diff_sets(violations_from_evidence(captured(base)),
                      violations_from_evidence(captured(extra)))
    assert not delta.acceptable


# ---------------------------------------------------------------------------
# The exploit: a deleted ambiguity list used to compare clean
# ---------------------------------------------------------------------------


def test_deleting_the_ambiguity_list_is_no_longer_a_clean_comparison():
    """Both sides lose ``inventory_ambiguous`` - the reported exploit.

    The loader refuses the payload outright. The second half of the test shows
    the hazard the refusal exists for: if a reader *did* accept the edited
    payloads, the union of a now-empty ambiguity set makes the same physical
    substitution look like no change at all.
    """
    before = captured(ambiguous_engine(second_pad_x=10.0))
    after = captured(ambiguous_engine(second_pad_x=40.0))
    for payload in (before, after):
        del payload["inventory_ambiguous"]
        payload["keys_without_inventory_proof"] = 0
        payload["violations_without_inventory_proof"] = 0
    assert "inventory_ambiguous" in refusal(before)
    assert "inventory_ambiguous" in refusal(after)

    # The hazard, measured on the honest payloads: with the ambiguity list
    # cleared on both sides - which is exactly what the edit achieved - the same
    # physical substitution compares clean, because nothing refines the shared
    # pair key by condition any more.
    honest_before = violations_from_evidence(captured(ambiguous_engine(second_pad_x=10.0)))
    honest_after = violations_from_evidence(captured(ambiguous_engine(second_pad_x=40.0)))
    assert not diff_sets(honest_before, honest_after).acceptable
    cleared_before = _with_ambiguity(honest_before, frozenset())
    cleared_after = _with_ambiguity(honest_after, frozenset())
    assert diff_sets(cleared_before, cleared_after).acceptable


def test_deleting_the_ambiguity_list_from_one_side_is_refused():
    """Integrity is per payload: one side keeping its ambiguity is not enough."""
    before = captured(ambiguous_engine(second_pad_x=10.0))
    after = captured(ambiguous_engine(second_pad_x=40.0))
    del after["inventory_ambiguous"]
    assert "inventory_ambiguous" in refusal(after)
    # The untouched side still refuses the swap on its own.
    assert not diff_sets(violations_from_evidence(before),
                         violations_from_evidence(captured(ambiguous_engine(
                             second_pad_x=40.0)))).acceptable


def test_deleting_the_binding_is_refused():
    payload = captured(unique_engine())
    del payload["binding"]
    assert "binding" in refusal(payload)


def test_deleting_ambiguity_and_binding_together_is_refused():
    payload = captured(unique_engine())
    del payload["inventory_ambiguous"]
    del payload["binding"]
    reasons = refusal(payload)
    assert "inventory_ambiguous" in reasons and "binding" in reasons


# ---------------------------------------------------------------------------
# Types, nulls and unsupported policies
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field, value", [
    ("policy", None),
    ("policy", "collision-aware-inventory-v0"),
    ("policy", "inventory-identity-v2"),
    ("policy", 7),
    ("rules_path", None),
    ("rules_path", 5),
    ("context", None),
    ("context", "not-a-list"),
    ("binding", None),
    ("binding", ["complete"]),
    ("binding", {"complete": "yes"}),
    ("binding", {"complete": 1}),
    ("inventory_ambiguous", None),
    ("inventory_ambiguous", {}),
    ("inventory_ambiguous", "abc"),
    ("violations", None),
    ("violations", {}),
    ("violations", "rows"),
    ("counts", None),
    ("counts", {}),
    ("total", None),
    ("total", "1"),
    ("relevant", None),
    ("connectivity", True),
    ("keys_without_inventory_proof", None),
    ("violations_without_inventory_proof", None),
])
def test_null_and_wrong_typed_fields_refuse(field, value):
    payload = captured(unique_engine())
    payload[field] = value
    assert refusal(payload)


@pytest.mark.parametrize("field", [
    "policy", "rules_path", "context", "binding", "inventory_ambiguous",
    "violations", "total", "relevant", "connectivity",
    "keys_without_inventory_proof", "violations_without_inventory_proof",
    "counts",
])
def test_a_missing_payload_field_is_named_in_the_refusal(field):
    payload = captured(unique_engine())
    del payload[field]
    assert field in refusal(payload)


def test_an_unsupported_policy_is_named():
    payload = captured(unique_engine())
    payload["policy"] = "collision-aware-inventory-v2"
    assert "unsupported evidence policy" in refusal(payload)


def test_the_serializer_policy_is_the_only_supported_one():
    payload = captured(unique_engine())
    assert payload["policy"] == EVIDENCE_POLICY


def test_the_binding_must_carry_the_whole_inventory_summary():
    """The summary beside the payload is the second witness to the ambiguity list.

    An empty or partial binding used to validate, which is how clearing the
    ambiguity list could sail past a summary that still said one key was
    unproven. Every field ``take_violations`` writes is now required and typed.
    """
    payload = captured(unique_engine())
    assert payload["binding"]["policy"] == "inventory-identity-v2"

    payload["binding"] = {}
    reasons = refusal(payload)
    assert "inventory summary field 'complete'" in reasons
    assert "inventory summary field 'keys_without_inventory_proof'" in reasons

    payload["binding"] = {"policy": "inventory-identity-v2"}
    assert "inventory summary field" in refusal(payload)


@pytest.mark.parametrize("field, value", [
    ("policy", ""),
    ("policy", "inventory-identity-v1"),
    ("policy", None),
    ("complete", "yes"),
    ("complete", None),
    ("problems", None),
    ("problems", [1]),
    ("items", -1),
    ("items", "4"),
    ("distinct_uuids", None),
    ("duplicated_uuids", -2),
    ("duplicated_items", None),
    ("nil_uuids", 1.0),
    ("kinds", None),
    ("kinds", [["track"]]),
    ("kinds", [["track", "4"]]),
    ("unknown_kinds", {}),
    ("unresolved_reasons", "none"),
    ("unresolved_reasons", [1]),
])
def test_a_wrong_typed_inventory_summary_field_refuses(field, value):
    payload = captured(unique_engine())
    payload["binding"][field] = value
    reasons = refusal(payload)
    assert "binding" in reasons or "inventory policy" in reasons


def test_an_inventory_summary_that_disagrees_with_the_payload_refuses():
    payload = captured(ambiguous_engine(second_pad_x=10.0))
    assert payload["binding"]["keys_without_inventory_proof"] == 1

    payload["binding"]["violations"] = payload["total"] + 1
    assert "binding violations" in refusal(payload)

    payload = captured(ambiguous_engine(second_pad_x=10.0))
    payload["binding"]["keys_without_inventory_proof"] = 0
    assert "binding keys_without_inventory_proof" in refusal(payload)

    payload = captured(ambiguous_engine(second_pad_x=10.0))
    payload["binding"]["violations_without_inventory_proof"] = 0
    assert "binding violations_without_inventory_proof" in refusal(payload)


def test_an_incomplete_inventory_cannot_certify_a_uuid():
    """`complete: false` says the inventory could not name every item, so no
    identifier it returned is proven: every uuid key must be declared unproven."""
    payload = captured(unique_engine())
    assert payload["inventory_ambiguous"] == []
    payload["binding"]["complete"] = False
    payload["binding"]["problems"] = ["get_board_items() failed"]
    reasons = refusal(payload)
    assert "certifies no identifier" in reasons


def test_a_summary_that_is_both_complete_and_problematic_refuses():
    payload = captured(unique_engine())
    payload["binding"]["problems"] = ["an unexplained problem"]
    assert "differs" in refusal(payload) or "complete and problems" in refusal(payload)

    payload = captured(unique_engine())
    payload["binding"]["complete"] = False
    payload["binding"]["problems"] = []
    assert "certifies no identifier" in refusal(payload)


def test_a_summary_that_is_smaller_than_its_own_counts_refuses():
    payload = captured(unique_engine())
    payload["binding"]["items"] = 1
    payload["binding"]["distinct_uuids"] = 2
    assert "smaller than distinct_uuids" in refusal(payload)

    payload = captured(unique_engine())
    payload["binding"]["nil_uuids"] = payload["binding"]["items"] + 1
    assert "larger than items" in refusal(payload)


# ---------------------------------------------------------------------------
# Contradictory rows and row fields
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field", [
    "key", "condition", "relevant", "error_code", "error_type", "message",
    "layer", "x_nm", "y_nm", "net_names", "item_a", "item_b",
])
def test_a_missing_row_field_is_named_in_the_refusal(field):
    payload = captured(unique_engine())
    del _rows(payload)[0][field]
    assert field in refusal(payload)


@pytest.mark.parametrize("field, value", [
    ("error_code", None),
    ("error_code", "5"),
    ("error_code", True),
    ("layer", None),
    ("layer", 1.0),
    ("x_nm", None),
    ("y_nm", None),
    ("net_names", None),
    ("net_names", "AB"),
    ("net_names", [1, 2]),
    ("item_a", 1),
    ("item_b", None),
    ("message", 3),
    ("error_type", None),
    ("relevant", None),
    ("relevant", "yes"),
    ("condition", None),
    ("condition", [1, 2, 3]),
    ("condition", [1, 2, 3, [1]]),
    ("key", None),
    ("key", []),
    ("key", ["uuid"]),
    ("key", ["unknown", 5, 0, ["a", "b"]]),
    ("key", ["uuid", 5, 0, ["a"]]),
])
def test_a_wrong_typed_row_field_refuses(field, value):
    """Includes the geometry case: an absent ``x_nm``/``y_nm`` is never 0."""
    payload = captured(unique_engine())
    _rows(payload)[0][field] = value
    _recount(payload)
    assert refusal(payload)


def test_a_condition_that_contradicts_its_row_refuses():
    payload = captured(unique_engine())
    row = _rows(payload)[0]
    row["condition"] = [row["x_nm"] + 1_000_000, row["y_nm"], row["layer"],
                        sorted(row["net_names"])]
    assert "condition disagrees" in refusal(payload)


def test_a_key_that_contradicts_its_row_refuses():
    payload = captured(unique_engine())
    row = _rows(payload)[0]
    row["key"] = ["uuid", int(row["error_code"]), 9, row["key"][3]]
    assert "key disagrees" in refusal(payload)


def test_a_uuid_key_that_contradicts_its_item_pair_refuses():
    payload = captured(unique_engine())
    row = _rows(payload)[0]
    row["key"] = ["uuid", int(row["error_code"]), int(row["layer"]),
                  [row["item_a"], "somebody-else"]]
    assert "item pair" in refusal(payload)


def test_a_geometry_key_that_still_names_items_refuses():
    payload = captured(unique_engine())
    row = _rows(payload)[0]
    row["key"] = ["geom", int(row["error_code"]), row["error_type"],
                  int(row["layer"]), int(row["x_nm"]), int(row["y_nm"]),
                  sorted(row["net_names"])]
    assert "still names an item" in refusal(payload)


def test_a_row_whose_relevance_flag_contradicts_its_class_refuses():
    payload = captured(unique_engine())
    row = _rows(payload)[0]
    row["relevant"] = not row["relevant"]
    assert "relevant disagrees" in refusal(payload)


# ---------------------------------------------------------------------------
# Ambiguity accounting
# ---------------------------------------------------------------------------


def test_an_empty_ambiguity_list_with_nonzero_declared_counts_refuses():
    payload = captured(ambiguous_engine(second_pad_x=10.0))
    payload["inventory_ambiguous"] = []
    assert "keys_without_inventory_proof" in refusal(payload)


def test_an_ambiguous_key_the_payload_does_not_carry_refuses():
    payload = captured(unique_engine())
    payload["inventory_ambiguous"] = [["uuid", 5, 0, ["ghost", "a"]]]
    payload["keys_without_inventory_proof"] = 1
    payload["violations_without_inventory_proof"] = 1
    assert "does not carry" in refusal(payload)


def test_an_ambiguity_occurrence_count_that_does_not_match_the_rows_refuses():
    payload = captured(ambiguous_engine(second_pad_x=10.0))
    payload["violations_without_inventory_proof"] += 1
    assert "occurrence counts" in refusal(payload)


def test_an_ambiguity_key_count_that_does_not_match_the_list_refuses():
    payload = captured(ambiguous_engine(second_pad_x=10.0))
    payload["keys_without_inventory_proof"] = len(
        payload["inventory_ambiguous"]) + 1
    assert "keys_without_inventory_proof" in refusal(payload)


def test_a_repeated_ambiguity_entry_refuses():
    payload = captured(ambiguous_engine(second_pad_x=10.0))
    payload["inventory_ambiguous"].append(payload["inventory_ambiguous"][0])
    payload["keys_without_inventory_proof"] = 2
    assert "repeats" in refusal(payload)


def test_a_null_ambiguity_entry_refuses():
    payload = captured(ambiguous_engine(second_pad_x=10.0))
    assert payload["inventory_ambiguous"]
    payload["inventory_ambiguous"] = [None]
    assert refusal(payload)


def test_clearing_the_ambiguity_list_and_both_counts_is_refused_by_the_summary():
    """The Astra reproduction: empty the list and the payload's own two counts
    while the inventory summary still says one key was unproven.

    The rows are untouched, so the summary is the only witness left - and it now
    has to agree with the payload, which it no longer does.
    """
    payload = captured(ambiguous_engine(second_pad_x=10.0))
    assert payload["binding"]["keys_without_inventory_proof"] == 1
    payload["inventory_ambiguous"] = []
    payload["keys_without_inventory_proof"] = 0
    payload["violations_without_inventory_proof"] = 0
    reasons = refusal(payload)
    assert "binding keys_without_inventory_proof" in reasons


def test_clearing_the_summary_too_leaves_only_a_coherent_rewrite():
    """What the cross-checks can and cannot catch, pinned.

    Editing the ambiguity list, the payload's counts *and* the inventory summary
    into agreement produces a payload that is internally consistent, so it
    loads - and the rows then say the shared key is proven. That is the
    documented limit: the schema proves self-consistency, not authenticity, and
    closing it needs a signature, which this task does not add. The point of the
    required summary is that the edit has to be complete and deliberate rather
    than one deleted field.
    """
    payload = captured(ambiguous_engine(second_pad_x=10.0))
    payload["inventory_ambiguous"] = []
    payload["keys_without_inventory_proof"] = 0
    payload["violations_without_inventory_proof"] = 0
    payload["binding"]["keys_without_inventory_proof"] = 0
    payload["binding"]["violations_without_inventory_proof"] = 0
    assert evidence_schema_problems(payload) == ()


# ---------------------------------------------------------------------------
# Malformed shapes refuse; they never raise out of the checker
# ---------------------------------------------------------------------------


NESTED_OBJECT = {"key": "not-a-primitive"}


def _malformed_payloads():
    """Edits whose keys carry an object/None/float instead of primitives."""
    yield "row key item pair", lambda p: p["violations"][0].__setitem__(
        "key", ["uuid", int(p["violations"][0]["error_code"]),
                int(p["violations"][0]["layer"]), [NESTED_OBJECT, "b"]])
    yield "row key head", lambda p: p["violations"][0].__setitem__(
        "key", [NESTED_OBJECT, 5, 0, ["a", "b"]])
    yield "row key nested list object", lambda p: p["violations"][0].__setitem__(
        "key", ["uuid", 5, 0, [[NESTED_OBJECT], "b"]])
    yield "row key None member", lambda p: p["violations"][0].__setitem__(
        "key", ["uuid", 5, 0, [None, "b"]])
    yield "row condition member", lambda p: p["violations"][0].__setitem__(
        "condition", [NESTED_OBJECT, 0, 0, ["A"]])
    yield "ambiguity object entry", lambda p: p.__setitem__(
        "inventory_ambiguous", [NESTED_OBJECT])
    yield "ambiguity nested object key", lambda p: p.__setitem__(
        "inventory_ambiguous", [["uuid", 5, 0, [NESTED_OBJECT, "b"]]])
    yield "ambiguity float key", lambda p: p.__setitem__(
        "inventory_ambiguous", [["uuid", 5, 0, [1.5, "b"]]])
    yield "counts object key", lambda p: p.__setitem__(
        "counts", [{"key": NESTED_OBJECT, "count": 1}])
    yield "counts nested object key", lambda p: p.__setitem__(
        "counts", [{"key": ["uuid", 5, 0, [NESTED_OBJECT, "b"]], "count": 1}])
    yield "counts object entry", lambda p: p.__setitem__("counts", [NESTED_OBJECT])


def test_malformed_keys_are_refused_and_never_raised():
    """A dict inside a key used to reach ``set()``/``dict`` and raise TypeError.

    The checker now reports a structured problem for every one of these, the
    loader raises :class:`EvidenceSchemaError`, and the consumer refuses.
    """
    from tools.reliability.verify_saved_artifact import _replayable_baseline

    for label, edit in _malformed_payloads():
        payload = captured(ambiguous_engine(second_pad_x=10.0))
        edit(payload)
        problems = evidence_schema_problems(payload)   # must not raise
        assert problems, label
        with pytest.raises(EvidenceSchemaError) as caught:
            violations_from_evidence(payload)
        assert caught.value.reasons, label
        with pytest.raises(RuntimeError) as consumer:
            _replayable_baseline({"baseline": payload})
        assert "cannot be replayed" in str(consumer.value), label


def test_an_absent_violations_field_is_refused_not_read_as_empty():
    """The worker case: the whole evidence field missing, not an empty list."""
    payload = captured(unique_engine())
    del payload["violations"]
    assert "the payload does not carry 'violations' at all" in refusal(payload)


def test_an_unexpected_checker_failure_is_a_refusal_not_a_pass(monkeypatch):
    """Fail closed even if the checker itself is broken.

    The checker is wrapped so that anything it cannot interpret becomes a reason
    rather than an exception escaping to a caller that might treat "no answer"
    as "no change".
    """

    import pcb_world.agent.drc_gate as gate

    payload = captured(unique_engine())

    def explode(*_args, **_kwargs):
        raise RuntimeError("checker bug")

    monkeypatch.setattr(gate, "_row_problems", explode)
    problems = gate.evidence_schema_problems(payload)
    assert problems and "could not be validated" in problems[0]
    with pytest.raises(EvidenceSchemaError):
        violations_from_evidence(payload)


# ---------------------------------------------------------------------------
# Omitted and truncated rows
# ---------------------------------------------------------------------------


def test_a_truncated_row_list_refuses_even_with_matching_row_fields():
    """The row list is shortened; the declared accounting is left alone."""
    engine = Engine(
        [_clearance("a", "b", 10.0, 0.1), _clearance("c", "d", 20.0, 0.1)],
        tracks=[_track("a", 0.0, 0.0, 20.0, 0.0),
                _track("b", 0.0, 0.2, 20.0, 0.2),
                _track("c", 0.0, 30.0, 20.0, 30.0),
                _track("d", 0.0, 30.2, 20.0, 30.2)],
    )
    payload = captured(engine)
    assert payload["total"] == 2
    payload["violations"] = payload["violations"][:1]
    reasons = refusal(payload)
    assert "total is 2" in reasons


def test_an_omitted_row_that_also_edits_the_total_still_refuses():
    """Editing the total to hide the omission still leaves the per-key counts
    describing rows the payload no longer carries."""
    engine = Engine(
        [_clearance("a", "b", 10.0, 0.1), _clearance("c", "d", 20.0, 0.1)],
        tracks=[_track("a", 0.0, 0.0, 20.0, 0.0),
                _track("b", 0.0, 0.2, 20.0, 0.2),
                _track("c", 0.0, 30.0, 20.0, 30.0),
                _track("d", 0.0, 30.2, 20.0, 30.2)],
    )
    payload = captured(engine)
    payload["violations"] = payload["violations"][:1]
    payload["total"] = 1
    payload["relevant"] = 1
    payload["connectivity"] = 0
    assert "counts" in refusal(payload)


def test_per_key_counts_that_do_not_match_the_rows_refuse():
    payload = captured(unique_engine())
    payload["counts"][0]["count"] = 2
    assert "counts" in refusal(payload)


def test_a_duplicated_count_row_refuses():
    payload = captured(unique_engine())
    payload["counts"].append(dict(payload["counts"][0]))
    assert "repeats" in refusal(payload)


def test_a_relevant_count_that_disagrees_with_the_rows_refuses():
    payload = captured(unique_engine())
    payload["relevant"] += 1
    payload["total"] += 1
    reasons = refusal(payload)
    assert "relevant" in reasons or "total" in reasons


# ---------------------------------------------------------------------------
# The schema checker is usable without building a set
# ---------------------------------------------------------------------------


def test_evidence_schema_problems_reports_rather_than_raises():
    payload = captured(unique_engine())
    problems = evidence_schema_problems({})
    assert problems and "policy" in problems[0]
    assert evidence_schema_problems(payload) == ()
    assert evidence_schema_problems("not a payload")[0].startswith("the payload is")


def test_validate_evidence_payload_returns_the_payload_it_checked():
    payload = captured(unique_engine())
    assert validate_evidence_payload(payload) is payload


def test_the_serializer_policy_is_the_one_the_envelope_policy_names():
    """One policy string, written by the module that serializes the payload.

    ``reference_baseline.POLICY`` is the envelope's policy; a drift between the
    two would let an envelope claim a policy its inner payload was not measured
    under, so it is pinned here instead of being re-declared.
    """
    from pcb_world.agent.reference_baseline import POLICY

    assert POLICY == EVIDENCE_POLICY


# ---------------------------------------------------------------------------
# Consumer: the saved-artifact verifier
# ---------------------------------------------------------------------------


def test_the_saved_artifact_verifier_replays_an_intact_baseline():
    from tools.reliability.verify_saved_artifact import _replayable_baseline

    envelope = {"baseline": captured(unique_engine())}
    assert _replayable_baseline(envelope).total == 1


def test_the_saved_artifact_verifier_refuses_an_edited_baseline():
    """The exploit, through the consumer that actually replays evidence.

    Deleting the ambiguity list on both sides was the reported clean-pass. The
    verifier now refuses the envelope before it opens a board, and says why.
    """
    from tools.reliability.verify_saved_artifact import _replayable_baseline

    for engine_x in (10.0, 40.0):
        payload = captured(ambiguous_engine(second_pad_x=engine_x))
        del payload["inventory_ambiguous"]
        payload["keys_without_inventory_proof"] = 0
        payload["violations_without_inventory_proof"] = 0
        with pytest.raises(RuntimeError) as caught:
            _replayable_baseline({"baseline": payload})
        assert "cannot be replayed" in str(caught.value)
        assert "inventory_ambiguous" in str(caught.value)


@pytest.mark.parametrize("envelope", [
    None,
    "not an envelope",
    {"baseline": None},
    {"baseline": []},
    {"baseline": {"violations": []}},
])
def test_the_saved_artifact_verifier_refuses_a_malformed_envelope(envelope):
    from tools.reliability.verify_saved_artifact import _replayable_baseline

    with pytest.raises(RuntimeError):
        _replayable_baseline(envelope)


def test_a_truncated_baseline_is_refused_by_the_consumer():
    from tools.reliability.verify_saved_artifact import _replayable_baseline

    engine = Engine(
        [_clearance("a", "b", 10.0, 0.1), _clearance("c", "d", 20.0, 0.1)],
        tracks=[_track("a", 0.0, 0.0, 20.0, 0.0),
                _track("b", 0.0, 0.2, 20.0, 0.2),
                _track("c", 0.0, 30.0, 20.0, 30.0),
                _track("d", 0.0, 30.2, 20.0, 30.2)],
    )
    payload = captured(engine)
    payload["violations"] = payload["violations"][:1]
    with pytest.raises(RuntimeError, match="cannot be replayed"):
        _replayable_baseline({"baseline": payload})


def _with_ambiguity(violations, value):
    import dataclasses

    return dataclasses.replace(violations, inventory_ambiguous=value)
