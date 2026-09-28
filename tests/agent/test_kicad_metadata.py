"""Deterministic duplicate-UUID normalization, on synthetic boards only.

Imported boards reuse UUIDs; a reused UUID is not an identity, and the DRC gate
refuses to guess one. These tests pin the repair: only identity tokens change,
duplicates get a deterministic per-object identifier, reused identifiers that
cannot be told apart (or are referenced) are refused, and the whole thing is
idempotent. Everything here is a string literal - no board file is read.
"""

from __future__ import annotations

import json
import uuid

import pytest

import pcb_world.agent.kicad_metadata as kicad_metadata
from pcb_world.agent.kicad_metadata import (
    IDENTITY_POLICY,
    analyze,
    collect_occurrences,
    main,
    normalize_board,
    normalize_text,
    parse,
    semantic_digest,
    verify_equivalence,
)


def _item(uuid_value: str, start: int, end: int, tag: str = "gr_line") -> str:
    return (
        f"  ({tag}\n"
        f"    (start {start} {end})\n"
        f"    (end {start + 1} {end + 1})\n"
        f"    (stroke (width 0.15) (type solid))\n"
        f'    (layer "F.SilkS")\n'
        f'    (uuid "{uuid_value}")\n'
        f"  )\n"
    )


def _board(*items: str) -> str:
    return (
        "(kicad_pcb\n"
        "  (version 20241229)\n"
        '  (generator "unit-test")\n'
        "  (layers\n"
        '    (0 "F.Cu" signal)\n'
        "  )\n"
        + "".join(items)
        + ")\n"
    )


def _footprint(ref: str, x: float, child_uuid: str, pad_uuid: str) -> str:
    return (
        f'  (footprint "Synthetic:F" (layer "F.Cu")\n'
        f"    (at {x} 10)\n"
        f'    (uuid "{uuid.uuid5(uuid.NAMESPACE_DNS, ref)}")\n'
        f'    (property "Reference" "{ref}"\n'
        f"      (at 0 -1)\n"
        f'      (layer "F.SilkS")\n'
        f"      (uuid \"{uuid.uuid5(uuid.NAMESPACE_DNS, ref + '-ref')}\")\n"
        f"    )\n"
        f"    (fp_line\n"
        f"      (start 0 0)\n"
        f"      (end 1 0)\n"
        f'      (layer "F.SilkS")\n'
        f'      (uuid "{child_uuid}")\n'
        f"    )\n"
        f'    (pad "1" smd rect\n'
        f"      (at 0 0)\n"
        f"      (size 1 1)\n"
        f'      (layers "F.Cu")\n'
        f'      (uuid "{pad_uuid}")\n'
        f"    )\n"
        f"  )\n"
    )


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def test_the_parser_tracks_source_spans_and_survives_strings_and_comments():
    text = (
        "(kicad_pcb\n"
        "  ; a comment with a ( and a \" in it\n"
        '  (title_block (title "a ) paren and \\" quote"))\n'
        "  # a hash comment (also skipped)\n"
        '  (gr_line (start 1 1) (end 2 2) (uuid "u1"))\n'
        ")\n"
    )
    root = parse(text)
    assert root.head == "kicad_pcb"
    atoms = [occurrence.uuid for occurrence in collect_occurrences(root)]
    assert atoms == ["u1"]
    # Every atom's raw text is the exact source slice it came from.
    title = next(node for node in root.items
                 if getattr(node, "head", None) == "title_block")
    value = next(item for item in title.items[1].items
                 if getattr(item, "quoted", False))
    assert text[value.start:value.end] == value.raw
    assert "paren" in value.text
    assert value.text.endswith('" quote')      # the escaped quote survived


def test_a_unique_board_is_clean_and_comes_back_byte_for_byte():
    text = _board(_item("11111111-1111-1111-1111-111111111111", 1, 1),
                  _item("22222222-2222-2222-2222-222222222222", 5, 5))
    analysis = analyze(text)
    assert analysis.clean
    assert analysis.distinct_identifiers == 2
    assert analysis.duplicate_identifiers == 0

    result = normalize_text(text)
    assert result.ok
    assert result.text == text
    assert result.mapping == []
    assert result.normalized_sha256 == result.source_sha256


# ---------------------------------------------------------------------------
# Duplicate repair
# ---------------------------------------------------------------------------


def test_duplicate_identifiers_are_renumbered_and_nothing_else_changes():
    text = _board(_item("dup", 1, 1), _item("dup", 9, 9),
                  _item("keep", 20, 20))
    result = normalize_text(text)
    assert result.ok, result.problems
    assert len(result.mapping) == 2
    assert {entry["original_uuid"] for entry in result.mapping} == {"dup"}
    minted = {entry["new_uuid"] for entry in result.mapping}
    assert len(minted) == 2
    for value in minted:
        uuid.UUID(value)                       # well-formed, and distinct
    # The already-unique identifier is untouched, and the file is otherwise the
    # same bytes: only the two identity tokens differ.
    assert '"keep"' in result.text
    assert '"dup"' not in result.text
    assert verify_equivalence(text, result.text, result.mapping) == []
    assert semantic_digest(text) == semantic_digest(result.text)


def test_renumbering_is_deterministic_and_independent_of_file_order():
    first = _item("dup", 1, 1)
    second = _item("dup", 9, 9)
    forward = normalize_text(_board(first, second))
    backward = normalize_text(_board(second, first))
    assert forward.ok and backward.ok

    def by_signature(result):
        return {entry["occurrence_signature"]: entry["new_uuid"]
                for entry in result.mapping}

    assert by_signature(forward) == by_signature(backward)


def test_normalization_is_idempotent():
    text = _board(_item("dup", 1, 1), _item("dup", 9, 9))
    once = normalize_text(text)
    twice = normalize_text(once.text)
    assert once.ok and twice.ok
    assert twice.text == once.text
    assert twice.mapping == []
    assert analyze(once.text).clean


def test_a_minted_identifier_that_collides_with_a_kept_one_is_refused(monkeypatch):
    """The output is re-read and checked; a UUIDv5 collision is detected, not trusted."""
    kept = "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa"
    duplicated = "bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb"
    monkeypatch.setattr(kicad_metadata, "mint_uuid", lambda signature: kept)
    result = normalize_text(
        _board(_item(duplicated, 1, 1), _item(duplicated, 9, 9),
               _item(kept, 20, 20))
    )
    assert not result.ok
    assert any("collides with an identifier the board already keeps" in problem
               for problem in result.problems)


def test_two_occurrences_minted_to_one_identifier_are_refused(monkeypatch):
    duplicated = "bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb"
    monkeypatch.setattr(kicad_metadata, "mint_uuid",
                        lambda signature: "cccccccc-3333-4333-8333-cccccccccccc")
    result = normalize_text(_board(_item(duplicated, 1, 1),
                                   _item(duplicated, 9, 9)))
    assert not result.ok
    assert any("assigned the same new identifier" in problem
               for problem in result.problems)


def test_structurally_identical_duplicates_are_refused():
    """No rule can tell two identical occurrences apart; so none is applied."""
    identical = _item("dup", 1, 1)
    result = normalize_text(_board(identical, identical))
    assert not result.ok
    assert any("structurally identical" in problem for problem in result.problems)
    assert result.mapping == []


def test_a_reference_to_a_duplicated_identifier_is_refused_not_guessed():
    group = (
        "  (group \"rails\"\n"
        "    (uuid \"33333333-3333-3333-3333-333333333333\")\n"
        "    (members \"dup\" \"keep\")\n"
        "  )\n"
    )
    text = _board(_item("dup", 1, 1), _item("dup", 9, 9),
                  _item("keep", 20, 20), group)
    result = normalize_text(text)
    assert not result.ok
    assert any("referenced" in problem for problem in result.problems)
    assert result.text == ""


def test_a_reference_to_unique_identifiers_does_not_block_normalization():
    group = (
        "  (group \"rails\"\n"
        "    (uuid \"33333333-3333-3333-3333-333333333333\")\n"
        "    (members \"keep\")\n"
        "  )\n"
    )
    text = _board(_item("dup", 1, 1), _item("dup", 9, 9),
                  _item("keep", 20, 20), group)
    result = normalize_text(text)
    assert result.ok, result.problems
    assert '"keep"' in result.text                     # the reference survives
    assert verify_equivalence(text, result.text, result.mapping) == []


def test_nested_items_are_distinguished_by_their_parent():
    """Two library instances share every child UUID; the parent separates them."""
    text = _board(
        _footprint("R1", 10.0, "child-dup", "pad-dup"),
        _footprint("R2", 30.0, "child-dup", "pad-dup"),
    )
    result = normalize_text(text)
    assert result.ok, result.problems
    by_original = {}
    for entry in result.mapping:
        by_original.setdefault(entry["original_uuid"], set()).add(entry["new_uuid"])
    assert set(by_original) == {"child-dup", "pad-dup"}
    assert all(len(values) == 2 for values in by_original.values())
    assert verify_equivalence(text, result.text, result.mapping) == []


def test_two_writers_spelling_one_rotation_the_same_way_map_to_one_identifier():
    """``-180`` and ``180`` are one rotation; they must not split one object."""
    def board_with(angle: str, zero: str) -> str:
        return _board(
            '  (footprint "Synthetic:F" (layer "F.Cu")\n'
            f"    (at {zero} 10)\n"
            '    (uuid "aa111111-1111-1111-1111-111111111111")\n'
            '    (property "Reference" "R1"\n'
            f"      (at 0 -1 {angle})\n"
            '      (layer "F.SilkS")\n'
            '      (uuid "bb222222-2222-2222-2222-222222222222")\n'
            "    )\n"
            '    (fp_line (start 0 0) (end 1 0) (layer "F.SilkS")\n'
            '      (uuid "child-dup"))\n'
            "  )\n",
            _item("child-dup", 1, 1),
        )

    left = normalize_text(board_with("-180", "-0"))
    right = normalize_text(board_with("180", "0"))
    assert left.ok and right.ok
    assert (left.mapping[0]["occurrence_signature"]
            == right.mapping[0]["occurrence_signature"])
    assert left.mapping[0]["new_uuid"] == right.mapping[0]["new_uuid"]


# ---------------------------------------------------------------------------
# Sidecars and the evidence a caller keeps
# ---------------------------------------------------------------------------


def test_a_sidecar_naming_a_duplicated_identifier_refuses_the_board(tmp_path):
    duplicated = "44444444-4444-4444-4444-444444444444"
    board = tmp_path / "board.kicad_pcb"
    board.write_text(
        _board(_item(duplicated, 1, 1), _item(duplicated, 9, 9)),
        encoding="utf-8",
    )
    rules = tmp_path / "board.kicad_dru"
    rules.write_text(
        '(version 1)\n(rule "x" (constraint clearance (min 0.2mm))\n'
        f'  (condition "A.Uuid == \'{duplicated}\'"))\n',
        encoding="utf-8",
    )
    output = tmp_path / "canonical.kicad_pcb"
    result = normalize_board(board, output, sidecars=[rules])
    assert not result.ok
    assert any("board.kicad_dru" in problem for problem in result.problems)
    assert not output.exists()

    # The same board normalizes when the sidecar names nothing it rewrites.
    clean_rules = tmp_path / "clean.kicad_dru"
    clean_rules.write_text('(version 1)\n', encoding="utf-8")
    assert normalize_board(board, output, sidecars=[clean_rules]).ok
    assert output.exists()


def test_evidence_carries_the_mapping_hashes_and_policy(tmp_path):
    board = tmp_path / "board.kicad_pcb"
    board.write_text(_board(_item("dup", 1, 1), _item("dup", 9, 9)),
                     encoding="utf-8")
    output = tmp_path / "canonical.kicad_pcb"
    report = tmp_path / "report.json"
    assert main(["normalize", str(board), "--output", str(output),
                 "--report", str(report)]) == 0

    evidence = json.loads(report.read_text(encoding="utf-8"))
    assert evidence["ok"] is True
    assert evidence["identity_policy"] == IDENTITY_POLICY
    assert evidence["source_sha256"] != evidence["normalized_sha256"]
    assert len(evidence["mapping"]) == 2
    assert evidence["analysis"]["duplicate_identifiers"] == 1
    assert evidence["semantic_digest"] == semantic_digest(output.read_text(
        encoding="utf-8"))


def test_the_cli_refuses_an_ambiguous_board_with_a_distinct_status(tmp_path):
    identical = _item("dup", 1, 1)
    board = tmp_path / "ambiguous.kicad_pcb"
    board.write_text(_board(identical, identical), encoding="utf-8")
    assert main(["normalize", str(board),
                 "--output", str(tmp_path / "out.kicad_pcb")]) == 2
    assert not (tmp_path / "out.kicad_pcb").exists()


def test_analyze_reports_a_parse_error_instead_of_raising():
    analysis = analyze("(kicad_pcb (version 1)")
    assert analysis.parse_error
    assert analysis.clean is False
    result = normalize_text("(kicad_pcb (version 1)")
    assert not result.ok
    assert "not parseable" in result.problems[0]
