"""The installed-CLI gate: measured reproducibility, counts, and honest status.

The gate exists because the installed KiCad CLI reports a stable per-class total
but a non-reproducible item pairing for copper-vs-zone clearance findings. These
tests pin both halves: an exactly reproducible class is judged by identity, a
non-reproducible one is judged by count and *said* to be so, and a missing CLI is
never reported as a pass.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import pytest

from pcb_world.agent.cli_gate import (
    CliGateConfig,
    compare_reports,
    discover_cli,
    not_configured_verdict,
    unavailable_verdict,
    verdict_from_reports,
    run_gate,
    _run_owned,
)
from pcb_world.agent.scheduler import sha256_file


def _finding(kind: str, severity: str, uuid: str, x: float = 1.0, y: float = 2.0) -> dict:
    return {
        "type": kind, "severity": severity, "description": f"{kind} finding",
        "items": [{"uuid": uuid, "pos": {"x": x, "y": y},
                   "description": f"item {uuid}"}],
    }


def _report(violations: list[dict], unconnected: int = 0,
            version: str = "9.0.8") -> dict:
    """A report from the *vetted* reporter by default.

    Completeness is a property of a vetted reporter, so the gate refuses any
    other version outright; tests that want to exercise the comparison logic use
    9.0.8 (the pinned build-tree CLI) unless they are testing the policy itself.
    """
    return {
        "kicad_version": version,
        "included_severities": ["error", "warning", "exclusion"],
        "violations": violations,
        "unconnected_items": [
            _finding("unconnected", "error", f"u{i}") for i in range(unconnected)
        ],
    }


def _verdict(source: list[dict], candidate: list[dict], runs: int = 1, **kwargs):
    return verdict_from_reports(
        [json.loads(json.dumps(source)) for _ in range(runs)],
        [json.loads(json.dumps(candidate)) for _ in range(runs)],
        cli_path="/nope/kicad-cli", cli_version_text="9.0.8",
        source_board=kwargs.pop("source_board", __file__),
        candidate_board=kwargs.pop("candidate_board", __file__),
        **kwargs,
    )


def test_a_reproducible_class_may_not_gain_an_identity():
    shared = [_finding("clearance", "error", "a"), _finding("clearance", "error", "b")]
    extra = shared + [_finding("clearance", "error", "new")]
    verdict = _verdict(_report(shared), _report(extra), runs=2)
    assert verdict.status == "regressed"
    assert verdict.ok is False
    assert any("does not have" in reason for reason in verdict.reasons)


def test_a_churned_relevant_class_is_refused_not_judged_by_union():
    """A relevant class that churns is refused, and the union is diagnostic only.

    With an intermittent source finding X and a persistent candidate X, a union
    comparison would hide a resolved Y: union(source) = {X, Y} and
    union(candidate) = {X} shows no addition. Refusing the class is the only
    sound answer; the union numbers stay available as diagnostics.
    """
    runs = [
        _report([_finding("clearance", "error", "x")]),
        _report([_finding("clearance", "error", "y")]),
        _report([_finding("clearance", "error", "z")]),
    ]
    classes, problems = compare_reports(runs, runs)
    assert problems == []
    clearance = next(row for row in classes if row.kind == "clearance")
    assert clearance.identity_reproducible is False
    assert clearance.source_churned_identities == 3   # x, y, z each seen once
    assert clearance.source_identities == 1
    assert clearance.source_seen_across_runs == 3

    # The hiding case: source run 1 saw X and run 2 saw Y; the candidate's runs
    # both show X, so Y is gone and the union difference is empty.
    candidate = [
        _report([_finding("clearance", "error", "x")]),
        _report([_finding("clearance", "error", "x")]),
    ]
    verdict = verdict_from_reports(
        runs[:2], candidate, cli_path="cli", cli_version_text="9.0.8",
        source_board=__file__, candidate_board=__file__,
    )
    assert verdict.status == "unverified"
    assert verdict.ok is False
    assert any("no union substitute is an acceptance argument" in reason
               for reason in verdict.reasons)


def test_a_new_finding_replacing_a_resolved_one_at_equal_count_is_refused():
    """Equal totals are not acceptance: a swap is an added identity.

    This is the regression the architecture hold demanded. The candidate has the
    same number of clearance findings as the source, but one of them is a
    *different* violation - the source's must be resolved and the candidate's is
    new. No count comparison may call that verified.
    """
    source = [
        _report([_finding("clearance", "error", "old1"),
                 _finding("clearance", "error", "keep")]),
    ] * 2
    candidate = [
        _report([_finding("clearance", "error", "new1"),
                 _finding("clearance", "error", "keep")]),
    ] * 2
    verdict = verdict_from_reports(
        source, candidate, cli_path="cli", cli_version_text="9.0.8",
        source_board=__file__, candidate_board=__file__,
    )
    assert verdict.status == "regressed"
    assert verdict.ok is False
    assert any("does not have" in reason for reason in verdict.reasons)
    # Both boards report the same total, so the refusal cannot have come from a
    # count comparison.
    assert [len(r["violations"]) for r in source] == \
           [len(r["violations"]) for r in candidate]


def test_a_report_at_a_known_per_class_cap_is_refused_as_possibly_truncated():
    """199 and 499 are KiCad's per-class report caps, not measurements."""
    findings = [_finding("clearance", "error", f"c{i}") for i in range(499)]
    capped = [_report(findings)] * 2
    verdict = verdict_from_reports(
        capped, capped, cli_path="cli", cli_version_text="9.0.8",
        source_board=__file__, candidate_board=__file__,
    )
    assert verdict.status == "unverified"
    assert verdict.report_complete is False
    assert "truncation" in verdict.reasons[0]
    assert verdict.suspected_truncation == ("clearance",)


def test_a_class_whose_total_varies_between_runs_is_unverified():
    """A tool that cannot repeat its own total is not evidence about a candidate."""
    a = [_report([_finding("clearance", "error", "a")])] * 2
    b = [_report([_finding("clearance", "error", "a"),
                  _finding("clearance", "error", "b")]),
         _report([_finding("clearance", "error", "a")])]
    _, problems = compare_reports(a, b)
    assert problems and "varies between runs" in problems[0]
    verdict = verdict_from_reports(
        a, b, cli_path="cli", cli_version_text="9.0.8",
        source_board=__file__, candidate_board=__file__,
    )
    assert verdict.status == "unverified"
    assert verdict.ok is False


def test_a_single_run_cannot_be_verified():
    """Reproducibility has to be demonstrated, not assumed."""
    report = _report([])
    verdict = verdict_from_reports(
        [report], [report], cli_path="cli", cli_version_text="9.0.8",
        source_board=__file__, candidate_board=__file__,
    )
    assert verdict.status == "unverified"
    assert any("at least two runs" in reason for reason in verdict.reasons)


def test_a_rise_in_unconnected_items_is_refused():
    verdict = _verdict(_report([], unconnected=2), _report([], unconnected=3), runs=2)
    assert verdict.status == "regressed"
    assert any("unconnected items rose" in reason for reason in verdict.reasons)


def test_fewer_findings_and_unconnected_items_is_verified():
    source = _report([_finding("track_dangling", "warning", "d1"),
                      _finding("track_dangling", "warning", "d2")], unconnected=3)
    candidate = _report([_finding("track_dangling", "warning", "d1")], unconnected=1)
    verdict = _verdict(source, candidate, runs=2)
    assert verdict.status == "verified", verdict.reasons
    assert verdict.ok is True


def test_a_missing_cli_is_unavailable_never_a_pass():
    assert discover_cli("/definitely/not/here/kicad-cli") is None
    required = unavailable_verdict(cli_path=None, required=True)
    assert required.status == "unavailable"
    assert required.ok is False
    optional = unavailable_verdict(cli_path=None, required=False)
    assert optional.ok is True
    assert optional.status == "unavailable"
    assert "no CLI acceptance is claimed" in optional.reasons[0]


def test_empty_objects_are_not_verified_as_complete_empty_reports():
    verdict = _verdict([{}, {}], [{}, {}], runs=2)
    assert verdict.status == "unverified"
    assert not verdict.ok
    assert any("version" in reason or "missing" in reason for reason in verdict.reasons)


def test_an_itemless_finding_is_identified_by_its_own_description():
    """The pinned reporter emits copper_sliver with an empty items list.

    Such a finding is still judged - by class, severity and its own description -
    and every occurrence is kept, so a candidate that grows the count is caught
    instead of the report being refused outright or the rows being collapsed.
    """
    sliver = {"type": "copper_sliver", "severity": "warning",
              "description": "Copper sliver (Top Layer)", "items": []}
    source = _report([json.loads(json.dumps(sliver)) for _ in range(3)])
    candidate = _report([json.loads(json.dumps(sliver)) for _ in range(3)])
    verdict = _verdict(source, candidate, runs=2)
    assert verdict.status == "verified", verdict.reasons
    slivers = next(row for row in verdict.classes if row.kind == "copper_sliver")
    assert slivers.source_counts == (3, 3)    # multiplicity kept, not collapsed
    assert slivers.duplicate_rows_dropped_source == 0

    grew = _report([json.loads(json.dumps(sliver)) for _ in range(4)])
    verdict = _verdict(source, grew, runs=2)
    assert verdict.status == "regressed"
    assert any("more finding" in reason for reason in verdict.reasons)


def test_an_itemless_finding_without_a_description_is_refused():
    row = {"type": "copper_sliver", "severity": "warning", "items": []}
    verdict = _verdict(_report([row]), _report([row]), runs=2)
    assert verdict.status == "unverified"
    assert any("no items and no description" in reason for reason in verdict.reasons)


def test_uuidless_items_use_stable_geometry_identity():
    source = _report([{"type": "clearance", "severity": "error", "items": [
        {"reference": "U1", "pos": {"x": 1.0, "y": 2.0}},
    ]}])
    candidate = _report([{"type": "clearance", "severity": "error", "items": [
        {"reference": "U2", "pos": {"x": 1.0, "y": 2.0}},
    ]}])
    verdict = _verdict(source, candidate, runs=2)
    assert verdict.status == "regressed"
    assert any("does not have" in reason for reason in verdict.reasons)


def test_equal_unconnected_total_cannot_hide_a_new_item():
    source = _report([], 0)
    candidate = _report([], 0)
    source["unconnected_items"] = [_finding("unconnected", "error", "old")]
    candidate["unconnected_items"] = [_finding("unconnected", "error", "new")]
    verdict = _verdict(source, candidate, runs=2)
    assert verdict.status == "regressed"
    unconnected = next(row for row in verdict.classes if row.kind == "__unconnected_items__")
    assert unconnected.added_identities == 1


def _board_pair(tmp_path) -> tuple[str, str]:
    source = tmp_path / "reference.kicad_pcb"
    candidate = tmp_path / "candidate.kicad_pcb"
    source.write_text("frozen reference board", encoding="utf-8")
    candidate.write_text("candidate board", encoding="utf-8")
    return str(source), str(candidate)


def _bound_terminal_proof(source: str, candidate: str) -> dict:
    return {
        "fresh_process": True,
        "ok": True,
        "complete": True,
        "original_reference_sha256": sha256_file(source),
        "candidate_board_sha256": sha256_file(candidate),
    }


def test_a_churned_unconnected_pairing_needs_a_bound_terminal_proof(tmp_path):
    """Endpoint churn is a visualisation choice, and only that class may use it.

    Measured on the frozen V3 source: 4 of 106 unconnected pairings differ between
    two runs of one unchanged board. The class may be accepted on a fresh native
    terminal-partition proof for *this* candidate and reference - and without one
    the verdict stays unverified rather than passing on union arithmetic.
    """
    source_board, candidate_board = _board_pair(tmp_path)
    source_runs = [_report([], 0), _report([], 0)]
    candidate_runs = [_report([], 0), _report([], 0)]
    source_runs[0]["unconnected_items"] = [_finding("unconnected", "error", "u-a")]
    source_runs[1]["unconnected_items"] = [_finding("unconnected", "error", "u-b")]
    candidate_runs[0]["unconnected_items"] = [_finding("unconnected", "error", "u-a")]
    candidate_runs[1]["unconnected_items"] = [_finding("unconnected", "error", "u-b")]

    def verdict(**kwargs):
        return verdict_from_reports(
            source_runs, candidate_runs, cli_path="/nope/kicad-cli",
            cli_version_text="9.0.8", source_board=source_board,
            candidate_board=candidate_board, **kwargs,
        )

    without = verdict()
    assert without.status == "unverified"
    assert not without.ok
    assert any("churned its endpoint pairing" in reason
               and "stays unverified" in reason for reason in without.reasons)

    unbound = _bound_terminal_proof(source_board, candidate_board)
    unbound["candidate_board_sha256"] = "0" * 64
    stale = verdict(terminal_partition_proof=unbound)
    assert stale.status == "unverified"
    assert any("not bound to this candidate" in reason for reason in stale.reasons)

    replayed = _bound_terminal_proof(source_board, candidate_board)
    replayed["fresh_process"] = False
    assert verdict(terminal_partition_proof=replayed).status == "unverified"

    accepted = verdict(
        terminal_partition_proof=_bound_terminal_proof(source_board, candidate_board),
    )
    assert accepted.status == "verified", accepted.reasons
    assert any("bound native terminal-partition proof" in reason
               for reason in accepted.reasons)
    # The exemption covers the class's *identities* (a visualisation choice), not
    # its counts: they are still compared and a rise is still refused.
    assert not any("__unconnected_items__" in reason for reason in accepted.reasons)
    grown = [_report([], 0), _report([], 0)]
    for index, row in enumerate(grown):
        row["unconnected_items"] = [
            _finding("unconnected", "error", "u-a"),
            _finding("unconnected", "error", f"extra-{index}"),
        ]
    risen = verdict_from_reports(
        source_runs, grown, cli_path="/nope/kicad-cli", cli_version_text="9.0.8",
        source_board=source_board, candidate_board=candidate_board,
        terminal_partition_proof=_bound_terminal_proof(source_board, candidate_board),
    )
    assert risen.status == "regressed"
    assert any("unconnected items rose" in reason for reason in risen.reasons)


def _unconnected_row(index: int) -> dict:
    return _finding("unconnected", "error", f"u{index}")


def _raw_report(rows: list[dict]) -> dict:
    return {
        "kicad_version": "9.0.8",
        "included_severities": ["error", "warning", "exclusion"],
        "violations": [],
        "unconnected_items": rows,
    }


def _measured_v3_unconnected_runs():
    """The measured shape: raw 143 rows on both boards, repeats shifting.

    On the frozen V3 pair the 9.0.8 reporter emits 143 ``unconnected_items`` rows
    for both boards, but the number of *byte-identical repeats* varies between
    runs of one unchanged board, so the collapsed total churns (131/131 on the
    source, 125/126 on the candidate) while the physical quantity does not.
    """
    source = [_unconnected_row(index) for index in range(131)]
    source_report = _raw_report(source + [source[0]] * 12)              # raw 143
    candidate_runs = [
        _raw_report([_unconnected_row(index) for index in range(126)]
                    + [_unconnected_row(0)] * 17),                      # raw 143
        _raw_report([_unconnected_row(index) for index in range(125)]
                    + [_unconnected_row(0)] * 18),                      # raw 143
    ]
    return source_report, candidate_runs


def test_raw_unconnected_rows_are_the_count_when_repeat_rows_shift(tmp_path):
    """A stable raw total with churned identities passes on a bound proof only."""
    source_board, candidate_board = _board_pair(tmp_path)
    source_report, candidate_runs = _measured_v3_unconnected_runs()
    source_runs = [json.loads(json.dumps(source_report)) for _ in range(2)]

    def verdict(**kwargs):
        return verdict_from_reports(
            source_runs, candidate_runs, cli_path="/nope/kicad-cli",
            cli_version_text="9.0.8", source_board=source_board,
            candidate_board=candidate_board, **kwargs,
        )

    without = verdict()
    assert without.status == "unverified"
    assert any("churned its endpoint pairing" in reason
               and "stays unverified" in reason for reason in without.reasons)

    accepted = verdict(
        terminal_partition_proof=_bound_terminal_proof(source_board, candidate_board),
    )
    assert accepted.status == "verified", accepted.reasons
    assert accepted.ok
    # The judgement is on raw rows, and the churn is recorded as a diagnostic.
    assert accepted.unconnected_source == (143, 143)
    assert accepted.unconnected_candidate == (143, 143)
    unconnected = next(row for row in accepted.classes
                       if row.kind == "__unconnected_items__")
    assert unconnected.source_rows == (143, 143)
    assert unconnected.candidate_rows == (143, 143)
    assert unconnected.candidate_counts == (126, 125)     # collapsed, diagnostic
    assert unconnected.count_delta == 0
    assert not any("total varies" in reason for reason in accepted.reasons)
    assert any("bound native terminal-partition proof" in reason
               for reason in accepted.reasons)


def test_an_unstable_raw_unconnected_count_is_unverified(tmp_path):
    """A raw total that moves between runs of one board is not a count."""
    source_board, candidate_board = _board_pair(tmp_path)
    source_report, candidate_runs = _measured_v3_unconnected_runs()
    source_runs = [json.loads(json.dumps(source_report)) for _ in range(2)]
    candidate_runs[1] = _raw_report(
        [_unconnected_row(index) for index in range(126)]
        + [_unconnected_row(0)] * 18,
    )                                                        # raw 144

    verdict = verdict_from_reports(
        source_runs, candidate_runs, cli_path="/nope/kicad-cli",
        cli_version_text="9.0.8", source_board=source_board,
        candidate_board=candidate_board,
        terminal_partition_proof=_bound_terminal_proof(source_board, candidate_board),
    )
    assert verdict.status == "unverified"
    assert any("__unconnected_items__" in reason and "raw total varies" in reason
               for reason in verdict.reasons)


def test_a_raw_rise_in_unconnected_rows_is_regressed_even_with_a_proof(tmp_path):
    """A real connection loss is a rise in raw rows, proof or no proof."""
    source_board, candidate_board = _board_pair(tmp_path)
    source_report, _candidate = _measured_v3_unconnected_runs()
    source_runs = [json.loads(json.dumps(source_report)) for _ in range(2)]
    grown = _raw_report([_unconnected_row(index) for index in range(150)])
    candidate_runs = [json.loads(json.dumps(grown)) for _ in range(2)]

    verdict = verdict_from_reports(
        source_runs, candidate_runs, cli_path="/nope/kicad-cli",
        cli_version_text="9.0.8", source_board=source_board,
        candidate_board=candidate_board,
        terminal_partition_proof=_bound_terminal_proof(source_board, candidate_board),
    )
    assert verdict.status == "regressed"
    assert not verdict.ok
    assert any("unconnected items rose" in reason for reason in verdict.reasons)


def test_a_report_without_the_unconnected_section_is_refused(tmp_path):
    """The raw section is required; a missing one is never an empty one."""
    source_board, candidate_board = _board_pair(tmp_path)
    source_runs = [_report([]), _report([])]
    candidate_runs = [_report([]), _report([])]
    for report in candidate_runs:
        del report["unconnected_items"]

    verdict = verdict_from_reports(
        source_runs, candidate_runs, cli_path="/nope/kicad-cli",
        cli_version_text="9.0.8", source_board=source_board,
        candidate_board=candidate_board,
        terminal_partition_proof=_bound_terminal_proof(source_board, candidate_board),
    )
    assert verdict.status == "unverified"
    assert any("missing violations or unconnected_items arrays" in reason
               for reason in verdict.reasons)


def test_a_churned_relevant_class_is_refused_even_with_a_terminal_proof(tmp_path):
    """The proof is bound to the unconnected section only, never to a real class."""
    source_board, candidate_board = _board_pair(tmp_path)
    source_runs = [_report([]), _report([])]
    candidate_runs = [_report([]), _report([])]
    source_runs[0]["violations"] = [_finding("clearance", "error", "c-a")]
    source_runs[1]["violations"] = [_finding("clearance", "error", "c-b")]
    candidate_runs[0]["violations"] = [_finding("clearance", "error", "c-a")]
    candidate_runs[1]["violations"] = [_finding("clearance", "error", "c-b")]
    verdict = verdict_from_reports(
        source_runs, candidate_runs, cli_path="/nope/kicad-cli",
        cli_version_text="9.0.8", source_board=source_board,
        candidate_board=candidate_board,
        terminal_partition_proof=_bound_terminal_proof(source_board, candidate_board),
    )
    assert verdict.status == "unverified"
    assert any("clearance" in reason and "union substitute" in reason
               for reason in verdict.reasons)


def test_an_unproven_unconnected_churn_still_names_the_class_regressions(tmp_path):
    """A refusal has to say everything it saw, not just what it could not judge."""
    source_board, candidate_board = _board_pair(tmp_path)
    source_runs = [_report([]), _report([])]
    candidate_runs = [_report([]), _report([])]
    source_runs[0]["unconnected_items"] = [_finding("unconnected", "error", "u-a")]
    source_runs[1]["unconnected_items"] = [_finding("unconnected", "error", "u-b")]
    candidate_runs[0]["unconnected_items"] = [_finding("unconnected", "error", "u-a")]
    candidate_runs[1]["unconnected_items"] = [_finding("unconnected", "error", "u-b")]
    for runs in (source_runs, candidate_runs):
        runs[0]["violations"] = [_finding("isolated_copper", "warning", "island")]
        runs[1]["violations"] = [_finding("isolated_copper", "warning", "island")]
    candidate_runs[0]["violations"] = source_runs[0]["violations"] + [
        _finding("isolated_copper", "warning", "new-island"),
    ]
    candidate_runs[1]["violations"] = candidate_runs[0]["violations"]

    verdict = verdict_from_reports(
        source_runs, candidate_runs, cli_path="/nope/kicad-cli",
        cli_version_text="9.0.8", source_board=source_board,
        candidate_board=candidate_board,
    )
    assert verdict.status == "unverified"
    assert any("churned its endpoint pairing" in reason for reason in verdict.reasons)
    # The class-level regression is still named alongside the unproven exemption.
    assert any("isolated_copper" in reason and "does not have" in reason
               for reason in verdict.reasons)


def test_run_gate_passes_the_terminal_proof_into_the_verdict(tmp_path):
    """The production path must actually carry the proof, not just accept one."""
    source = tmp_path / "source.kicad_pcb"
    candidate = tmp_path / "candidate.kicad_pcb"
    source.write_text("source board", encoding="utf-8")
    candidate.write_text("candidate board", encoding="utf-8")
    for stem in ("source", "candidate"):
        (tmp_path / f"{stem}.kicad_pro").write_text("{}\n", encoding="utf-8")
        (tmp_path / f"{stem}.kicad_dru").write_text("(version 1)\n", encoding="utf-8")
    counter = tmp_path / "counter"
    cli = tmp_path / "kicad-cli"
    cli.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"--version\" ]; then echo 'KiCad 9.0.8'; exit 0; fi\n"
        "for arg in \"$@\"; do prev=\"${prev:-}\"; if [ \"$prev\" = \"--output\" ]; then out=\"$arg\"; fi; prev=\"$arg\"; done\n"
        f"n=$(cat {counter} 2>/dev/null || echo 0)\n"
        f"echo $((n+1)) > {counter}\n"
        "if [ $((n % 4)) -lt 2 ]; then u=u-a; else u=u-b; fi\n"
        "printf '%s' \"{\\\"kicad_version\\\":\\\"9.0.8\\\","
        "\\\"included_severities\\\":[\\\"error\\\",\\\"warning\\\",\\\"exclusion\\\"],"
        "\\\"violations\\\":[],\\\"unconnected_items\\\":[{"
        "\\\"type\\\":\\\"unconnected\\\",\\\"severity\\\":\\\"error\\\","
        "\\\"description\\\":\\\"unconnected $u\\\",\\\"items\\\":[{"
        "\\\"uuid\\\":\\\"$u\\\",\\\"pos\\\":{\\\"x\\\":1,\\\"y\\\":2},"
        "\\\"description\\\":\\\"item $u\\\"}]}]}\" > \"$out\"\n",
        encoding="utf-8",
    )
    cli.chmod(0o755)
    kwargs = dict(
        source_board=str(source), candidate_board=str(candidate),
        source_rules=str(tmp_path / "source.kicad_dru"),
        candidate_rules=str(tmp_path / "candidate.kicad_dru"),
    )

    def gate(**extra):
        name = extra.pop("n", 0)
        return run_gate(
            CliGateConfig(cli_path=str(cli), runs=2),
            work_dir=str(tmp_path / f"reports-{name}"), **kwargs, **extra,
        )

    without = gate()
    assert without.status == "unverified", without.reasons
    accepted = gate(
        n=1,
        terminal_partition_proof=_bound_terminal_proof(str(source), str(candidate)),
    )
    assert accepted.status == "verified", accepted.reasons


def test_mismatched_report_and_binary_versions_are_refused():
    source = _report([])
    candidate = _report([])
    source["kicad_version"] = "9.0.8"
    candidate["kicad_version"] = "10.0.6"
    verdict = _verdict(source, candidate, runs=2)
    assert verdict.status == "unverified"
    assert any("version/report mismatch" in reason for reason in verdict.reasons)


def test_owned_cli_timeout_kills_only_its_process_group(tmp_path):
    child_pid = tmp_path / "child.pid"
    script = tmp_path / "hang.py"
    script.write_text(
        "import os, subprocess, sys, time\n"
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'])\n"
        "open(sys.argv[1],'w').write(str(child.pid))\n"
        "time.sleep(30)\n", encoding="utf-8",
    )
    import signal
    import subprocess
    import sys

    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            _run_owned([sys.executable, str(script), str(child_pid)], timeout_s=0.2)
        assert child_pid.exists()
        os.kill(unrelated.pid, 0)
        child = int(child_pid.read_text())
        with pytest.raises(ProcessLookupError):
            os.kill(child, 0)
    finally:
        unrelated.terminate()
        unrelated.wait(timeout=2)


def test_an_explicit_cli_env_is_added_to_the_inherited_one():
    """A build-tree CLI needs its marker *and* the inherited PATH/HOME.

    Replacing the environment instead of extending it makes the CLI fail to start
    at all, which used to read as "the reporter reported no version" rather than
    as a caller mistake.
    """
    proc = _run_owned(
        ["/bin/sh", "-c", "printf '%s|%s' \"$PATH\" \"$PCBWORLD_TEST_MARKER\""],
        timeout_s=10.0, env={"PCBWORLD_TEST_MARKER": "isolated"},
    )
    path, _, marker = proc.stdout.partition("|")
    assert path == os.environ.get("PATH", "")
    assert marker == "isolated"


def test_run_gate_uses_exact_isolated_sidecars_and_keeps_generation_reports(tmp_path):
    source = tmp_path / "source.kicad_pcb"
    candidate = tmp_path / "candidate.kicad_pcb"
    project = "{}\n"
    rules = "(version 1)\n"
    source.write_text("source board", encoding="utf-8")
    candidate.write_text("candidate board", encoding="utf-8")
    (tmp_path / "source.kicad_pro").write_text(project, encoding="utf-8")
    (tmp_path / "candidate.kicad_pro").write_text(project, encoding="utf-8")
    source_rules = tmp_path / "source.kicad_dru"
    candidate_rules = tmp_path / "candidate.kicad_dru"
    source_rules.write_text(rules, encoding="utf-8")
    candidate_rules.write_text(rules, encoding="utf-8")
    cli = tmp_path / "kicad-cli"
    cli.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"--version\" ]; then echo 'KiCad 9.0.8'; exit 0; fi\n"
        "for arg in \"$@\"; do prev=\"${prev:-}\"; if [ \"$prev\" = \"--output\" ]; then out=\"$arg\"; fi; prev=\"$arg\"; done\n"
        "printf '%s' '{\"kicad_version\":\"9.0.8\","
        "\"included_severities\":[\"error\",\"warning\",\"exclusion\"],"
        "\"violations\":[],\"unconnected_items\":[]}' > \"$out\"\n",
        encoding="utf-8",
    )
    cli.chmod(0o755)
    work_dir = tmp_path / "generation" / "cli_reports"
    verdict = run_gate(
        CliGateConfig(cli_path=str(cli), runs=2),
        source_board=str(source), candidate_board=str(candidate),
        source_rules=str(source_rules), candidate_rules=str(candidate_rules),
        work_dir=str(work_dir),
    )
    assert verdict.status == "verified", verdict.reasons
    assert verdict.cli_sha256
    assert verdict.source_project_sha256 == verdict.candidate_project_sha256
    assert len(verdict.report_paths) == 4
    assert all(Path(path).is_file() and str(work_dir) in path for path in verdict.report_paths)
    assert (work_dir / Path(verdict.report_paths[0]).parent.name / "inputs" / "source"
            / "board.kicad_pro").read_text() == project
    assert source.read_text() == "source board"
    assert candidate.read_text() == "candidate board"


def test_cli_gate_refuses_sidecar_drift(tmp_path):
    source = tmp_path / "source.kicad_pcb"
    candidate = tmp_path / "candidate.kicad_pcb"
    source.write_text("source", encoding="utf-8")
    candidate.write_text("candidate", encoding="utf-8")
    (tmp_path / "source.kicad_pro").write_text("{}", encoding="utf-8")
    (tmp_path / "candidate.kicad_pro").write_text("{\"changed\": true}", encoding="utf-8")
    for stem in ("source", "candidate"):
        (tmp_path / f"{stem}.kicad_dru").write_text("(version 1)", encoding="utf-8")
    cli = tmp_path / "kicad-cli"
    cli.write_text("#!/bin/sh\necho 'KiCad 9.0.8'\n", encoding="utf-8")
    cli.chmod(0o755)
    verdict = run_gate(
        CliGateConfig(cli_path=str(cli), runs=2), source_board=str(source),
        candidate_board=str(candidate), source_rules=str(tmp_path / "source.kicad_dru"),
        candidate_rules=str(tmp_path / "candidate.kicad_dru"), work_dir=str(tmp_path / "reports"),
    )
    assert verdict.status == "unverified"
    assert any(".kicad_pro files differ" in reason for reason in verdict.reasons)


def test_native_only_acceptance_is_distinguishable_from_a_cli_pass():
    verdict = not_configured_verdict()
    assert verdict.status == "not_configured"
    assert "native engine gate only" in verdict.reasons[0]


def test_cli_reports_are_recorded_in_the_verdict_evidence():
    verdict = _verdict(_report([]), _report([], unconnected=0), runs=2)
    evidence = verdict.to_evidence()
    assert evidence["cli_version"] == "9.0.8"
    assert evidence["options"] == [
        "pcb", "drc", "--format", "json", "--severity-all", "--all-track-errors",
    ]
    assert evidence["status"] == "verified"
    assert "source_board_sha256" in evidence
    # A reviewer must be able to see which binary answered and that a truncated
    # report would have been refused rather than judged by counts.
    assert evidence["selected_cli"]["version"] == "9.0.8"
    assert evidence["selected_cli"]["report_complete"] is True
    assert evidence["cap_policy"]["known_per_class_report_caps"] == [199, 499]
    assert evidence["cap_policy"]["truncated_reports_refused"] is True
    assert evidence["cap_policy"]["count_only_acceptance"] is False


def test_gate_config_defaults_are_explicit():
    config = CliGateConfig()
    assert config.runs == 3
    assert config.required is True
    assert config.extra_options == (
        "pcb", "drc", "--format", "json", "--severity-all", "--all-track-errors",
    )


# ---------------------------------------------------------------------------
# Runner integration: the CLI verdict is recorded and gates promotion
# ---------------------------------------------------------------------------


def _run_with_cli_verifier(tmp_path, verifier, **overrides):
    from pcb_world.agent.runner import RoutingRunner

    from tests.agent.conftest import fake_artifact_verifier
    from tests.agent.test_runner_scripted import _config, _engine

    engine = _engine()
    options = dict(
        name="run", max_attempts=2,
        artifact_verifier=fake_artifact_verifier,
        cli_gate=CliGateConfig(cli_path="/nonexistent"),
        cli_verifier=verifier,
    )
    options.update(overrides)
    return RoutingRunner(_config(tmp_path, engine, **options)).run()


def test_a_verified_cli_verdict_is_recorded_in_the_manifest(tmp_path):
    def verifier(**kwargs):
        assert "source_board" in kwargs and "candidate_board" in kwargs
        return {"status": "verified", "ok": True, "required": True,
                "cli_version": "9.0.8", "reasons": ["counts did not rise"]}

    report = _run_with_cli_verifier(tmp_path, verifier)
    assert report.accepted >= 1
    pointer = json.loads(
        (tmp_path / "run" / "artifacts" / "accepted_artifact.json").read_text())
    evidence = pointer["manifest"]["installed_cli_verification"]
    assert evidence["status"] == "verified"
    assert evidence["cli_version"] == "9.0.8"


def test_a_regressed_cli_verdict_blocks_promotion(tmp_path):
    calls = {"n": 0}

    def verifier(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"status": "verified", "ok": True, "required": True}
        return {"status": "regressed", "ok": False, "required": True,
                "reasons": ["silk_over_copper: 3 more finding(s) than the source"]}

    report = _run_with_cli_verifier(tmp_path, verifier, max_attempts=6)
    # The first candidate promoted; a later one was refused by the CLI gate, so
    # the pointer still names the earlier generation.
    assert calls["n"] >= 2
    pointer = json.loads(
        (tmp_path / "run" / "artifacts" / "accepted_artifact.json").read_text())
    manifest = pointer["manifest"]
    assert manifest["installed_cli_verification"]["status"] == "verified"
    assert report.status in ("stopped", "blocked", "completed")


def test_an_unavailable_required_cli_blocks_promotion(tmp_path):
    def verifier(**kwargs):  # pragma: no cover - promotion never completes
        return {"status": "unavailable", "ok": False, "required": True,
                "reasons": ["no installed kicad-cli was found"]}

    report = _run_with_cli_verifier(tmp_path, verifier)
    assert report.accepted == 0
    assert not (tmp_path / "run" / "artifacts" / "accepted_artifact.json").exists()


# ---------------------------------------------------------------------------
# Reporter policy: completeness belongs to a vetted reporter, not to a count
# ---------------------------------------------------------------------------

def test_an_unvetted_reporter_is_refused_even_without_cap_like_counts():
    """No class sits on a known cap, and the report is still not evidence.

    The installed 10.0.6 application is capped at a *different* count on other
    boards; "not at 199/499" is not a completeness proof for an arbitrary
    binary, so a report from one is refused before any comparison happens.
    """
    report = _report([_finding("clearance", "error", "a")], version="10.0.6")
    verdict = verdict_from_reports(
        [report] * 2, [report] * 2, cli_path="cli", cli_version_text="10.0.6",
        source_board=__file__, candidate_board=__file__,
    )
    assert verdict.status == "unverified"
    assert any("outside the vetted set" in reason for reason in verdict.reasons)
    assert verdict.report_complete is False
    assert verdict.to_evidence()["reporter_policy"]["supported_versions"] == ["9.0.8"]


def test_a_report_that_does_not_declare_required_severities_is_refused():
    report = _report([_finding("clearance", "error", "a")])
    del report["included_severities"]
    verdict = verdict_from_reports(
        [report] * 2, [report] * 2, cli_path="cli", cli_version_text="9.0.8",
        source_board=__file__, candidate_board=__file__,
    )
    assert verdict.status == "unverified"
    assert any("does not declare" in reason for reason in verdict.reasons)


def test_run_gate_refuses_cli_options_that_are_not_the_vetted_set(tmp_path):
    cli = tmp_path / "kicad-cli"
    cli.write_text("#!/bin/sh\necho 'KiCad 9.0.8'\n", encoding="utf-8")
    cli.chmod(0o755)
    board = tmp_path / "b.kicad_pcb"
    board.write_text("board", encoding="utf-8")
    (tmp_path / "b.kicad_pro").write_text("{}", encoding="utf-8")
    (tmp_path / "b.kicad_dru").write_text("(version 1)", encoding="utf-8")
    verdict = run_gate(
        CliGateConfig(cli_path=str(cli), runs=2,
                      extra_options=("pcb", "drc", "--format", "json")),
        source_board=str(board), candidate_board=str(board),
        source_rules=str(tmp_path / "b.kicad_dru"),
        candidate_rules=str(tmp_path / "b.kicad_dru"),
        work_dir=str(tmp_path / "reports"),
    )
    assert verdict.status == "unverified"
    assert any("not the vetted set" in reason for reason in verdict.reasons)


# ---------------------------------------------------------------------------
# Identity: a UUID is not a physical item, and multiplicity is preserved
# ---------------------------------------------------------------------------

def test_a_reused_uuid_with_different_geometry_is_a_different_identity():
    """The board reuses UUIDs; identity must survive that.

    Two items named by the same UUID at different positions are different items,
    so a candidate that reports the second one where the source reported the
    first is a swap - not "the same finding".
    """
    def item(x: float) -> dict:
        return {"uuid": "8e8e3aa6-aaaa-4c0c-9648-b22837b6d7f9",
                "pos": {"x": x, "y": 5.0},
                "description": "Pad 2 [+3V3] of C99 on Bottom Layer"}

    source = _report([{"type": "clearance", "severity": "error",
                       "description": "Clearance violation", "items": [item(10.0)]}])
    candidate = _report([{"type": "clearance", "severity": "error",
                          "description": "Clearance violation", "items": [item(20.0)]}])
    verdict = _verdict(source, candidate, runs=2)
    assert verdict.status == "regressed"
    clearance = next(row for row in verdict.classes if row.kind == "clearance")
    assert clearance.added_identities == 1
    assert clearance.removed_identities == 1


def test_an_identity_that_vanishes_at_an_equal_total_is_refused():
    """Equal totals, no new identity, and a source finding is still gone.

    The candidate kept the same number of rows but lost one identity and
    duplicated another. A set-only comparison of added identities sees nothing
    new and would pass it; multiplicity plus the removed-identity rule refuses.
    """
    source = _report([
        {"type": "clearance", "severity": "error", "description": "rule A; actual 0.10",
         "items": [{"uuid": "keep", "pos": {"x": 1.0, "y": 1.0}, "description": "Track"}]},
        {"type": "clearance", "severity": "error", "description": "rule A; actual 0.20",
         "items": [{"uuid": "gone", "pos": {"x": 2.0, "y": 2.0}, "description": "Track"}]},
    ])
    candidate = _report([
        {"type": "clearance", "severity": "error", "description": "rule A; actual 0.10",
         "items": [{"uuid": "keep", "pos": {"x": 1.0, "y": 1.0}, "description": "Track"}]},
        {"type": "clearance", "severity": "error", "description": "rule A; actual 0.30",
         "items": [{"uuid": "keep", "pos": {"x": 1.0, "y": 1.0}, "description": "Track"}]},
    ])
    verdict = _verdict(source, candidate, runs=2)
    assert verdict.status == "regressed", verdict.reasons
    assert verdict.removed_identities == 1
    assert any("missing from the candidate" in reason for reason in verdict.reasons)


def test_byte_identical_repeat_rows_are_counted_once_but_distinct_rows_are_kept():
    """The reporter repeats itself; that is not extra information.

    The pinned 9.0.8 reporter emits one row per item pair, so UUID-shared items
    produce literally identical rows and the raw total varies between runs of one
    unchanged board (measured: 2173 vs 2176 on the refilled board). Identical
    rows are dropped once; rows that differ in any field keep their multiplicity.
    """
    repeated = {"type": "shorting_items", "severity": "error",
                "description": "Items shorting two nets (nets GND and )",
                "items": [{"uuid": "a46b35b8", "pos": {"x": 214.889, "y": 66.909},
                           "description": "PTH pad 1 [GND] of MH4"},
                          {"uuid": "c49b9105", "pos": {"x": 216.9465, "y": 66.909},
                           "description": "PTH pad 1 [GND] of MH4"}]}
    distinct_same_identity = json.loads(json.dumps(repeated))
    distinct_same_identity["description"] = "Items shorting two nets (nets GND and 7)"
    run_one = _report([json.loads(json.dumps(repeated)) for _ in range(3)])
    run_two = _report([json.loads(json.dumps(repeated)) for _ in range(3)]
                      + [distinct_same_identity])
    classes, problems = compare_reports([run_one], [run_two])
    shorting = next(row for row in classes if row.kind == "shorting_items")
    assert shorting.source_counts == (1,)          # three identical rows, one finding
    assert shorting.source_rows == (3,)
    assert shorting.candidate_counts == (2,)       # the distinct row survives
    assert shorting.candidate_max_multiplicity == 2
    assert shorting.duplicate_rows_dropped_source == 2
    assert problems == []


# ---------------------------------------------------------------------------
# Input provenance: hashes are captured before staging, and the copies are checked
# ---------------------------------------------------------------------------

def _write_fake_cli(path: Path) -> Path:
    path.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"--version\" ]; then echo 'KiCad 9.0.8'; exit 0; fi\n"
        "for arg in \"$@\"; do prev=\"${prev:-}\"; "
        "if [ \"$prev\" = \"--output\" ]; then out=\"$arg\"; fi; prev=\"$arg\"; done\n"
        "printf '%s' '{\"kicad_version\":\"9.0.8\","
        "\"included_severities\":[\"error\",\"warning\",\"exclusion\"],"
        "\"violations\":[],\"unconnected_items\":[]}' > \"$out\"\n",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def _board_triplet(tmp_path: Path, stem: str, body: str = "board") -> tuple[Path, Path, Path]:
    board = tmp_path / f"{stem}.kicad_pcb"
    board.write_text(body, encoding="utf-8")
    project = tmp_path / f"{stem}.kicad_pro"
    project.write_text("{}\n", encoding="utf-8")
    rules = tmp_path / f"{stem}.kicad_dru"
    rules.write_text("(version 1)\n", encoding="utf-8")
    return board, project, rules


def test_run_gate_hashes_inputs_before_staging_and_records_them(tmp_path):
    source, source_project, source_rules = _board_triplet(tmp_path, "source")
    candidate, candidate_project, candidate_rules = _board_triplet(tmp_path, "candidate",
                                                                  "candidate board")
    cli = _write_fake_cli(tmp_path / "kicad-cli")
    verdict = run_gate(
        CliGateConfig(cli_path=str(cli), runs=2),
        source_board=str(source), candidate_board=str(candidate),
        source_rules=str(source_rules), candidate_rules=str(candidate_rules),
        work_dir=str(tmp_path / "reports"),
    )
    assert verdict.status == "verified", verdict.reasons
    evidence = verdict.to_evidence()
    assert evidence["hash_capture"] == "before_staging"
    assert evidence["staged_inputs_match"] is True
    hashes = evidence["input_hashes"]
    # The verdict names the inputs it saw, and those hashes are the files' own.
    assert hashes["source_board"] == sha256_file(str(source))
    assert hashes["candidate_board"] == sha256_file(str(candidate))
    assert hashes["source_project"] == sha256_file(str(source_project))
    assert hashes["candidate_rules"] == sha256_file(str(candidate_rules))
    assert hashes["cli"] == sha256_file(str(cli))
    assert verdict.source_board_sha256 == hashes["source_board"]


def test_run_gate_refuses_when_an_input_changes_during_the_run(tmp_path, monkeypatch):
    source, _sp, source_rules = _board_triplet(tmp_path, "source")
    candidate, _cp, candidate_rules = _board_triplet(tmp_path, "candidate", "candidate board")
    cli = _write_fake_cli(tmp_path / "kicad-cli")

    import pcb_world.agent.cli_gate as cli_gate

    real_run_drc = cli_gate.run_drc
    calls = {"n": 0}

    def drifting_run_drc(*args, **kwargs):
        result = real_run_drc(*args, **kwargs)
        calls["n"] += 1
        if calls["n"] == 1:
            source.write_text("board tampered with mid-run", encoding="utf-8")
        return result

    monkeypatch.setattr(cli_gate, "run_drc", drifting_run_drc)
    verdict = run_gate(
        CliGateConfig(cli_path=str(cli), runs=2),
        source_board=str(source), candidate_board=str(candidate),
        source_rules=str(source_rules), candidate_rules=str(candidate_rules),
        work_dir=str(tmp_path / "reports"),
    )
    assert verdict.status == "unavailable"
    assert verdict.ok is False
    assert any("changed during read-only verification" in reason
               for reason in verdict.reasons)


def test_run_gate_honours_explicit_project_paths(tmp_path):
    source, _sib_project, source_rules = _board_triplet(tmp_path, "source")
    candidate, _sib_project, candidate_rules = _board_triplet(tmp_path, "candidate",
                                                              "candidate board")
    explicit = tmp_path / "configured"
    explicit.mkdir()
    source_project = explicit / "source.kicad_pro"
    candidate_project = explicit / "candidate.kicad_pro"
    source_project.write_text('{"configured": true}\n', encoding="utf-8")
    candidate_project.write_text('{"configured": true}\n', encoding="utf-8")
    cli = _write_fake_cli(tmp_path / "kicad-cli")
    verdict = run_gate(
        CliGateConfig(cli_path=str(cli), runs=2),
        source_board=str(source), candidate_board=str(candidate),
        source_rules=str(source_rules), candidate_rules=str(candidate_rules),
        source_project=str(source_project), candidate_project=str(candidate_project),
        work_dir=str(tmp_path / "reports"),
    )
    assert verdict.status == "verified", verdict.reasons
    # The explicit project is what was verified and what the CLI actually read.
    assert verdict.source_project_sha256 == sha256_file(str(source_project))
    staged_project = (Path(verdict.report_paths[0]).parent / "inputs" / "source"
                      / "board.kicad_pro")
    assert staged_project.read_text() == source_project.read_text()


def test_provider_path_comes_only_from_the_documented_location(tmp_path):
    from pcb_world.agent.cli_gate import _provider_path

    bundle = tmp_path / "KiCad.app" / "Contents"
    (bundle / "MacOS").mkdir(parents=True)
    (bundle / "PlugIns").mkdir()
    cli = bundle / "MacOS" / "kicad-cli"
    cli.write_text("#!/bin/sh\n", encoding="utf-8")
    kiface = bundle / "PlugIns" / "_pcbnew.kiface"
    kiface.write_text("kiface", encoding="utf-8")
    path, source = _provider_path(str(cli), None)
    assert path == str(kiface)
    assert source == "app_bundle:Contents/PlugIns/_pcbnew.kiface"

    # A stray kiface somewhere above a non-bundle CLI is NOT the provider: the
    # gate binds a hash only to the location the loader actually uses.
    plain = tmp_path / "elsewhere" / "bin" / "kicad-cli"
    plain.parent.mkdir(parents=True)
    plain.write_text("#!/bin/sh\n", encoding="utf-8")
    (tmp_path / "elsewhere" / "PlugIns").mkdir()
    (tmp_path / "elsewhere" / "PlugIns" / "_pcbnew.kiface").write_text(
        "stray", encoding="utf-8")
    path, source = _provider_path(str(plain), None)
    assert path is None
    assert "no build-tree kiface" in source or "not inside a .app" in source


def test_a_build_tree_cli_without_the_marker_is_refused(tmp_path):
    from pcb_world.agent.cli_gate import _provider_path

    build = tmp_path / "build_rl" / "kicad" / "KiCad.app" / "Contents"
    (build / "MacOS").mkdir(parents=True)
    (build / "PlugIns").mkdir()
    cli = build / "MacOS" / "kicad-cli"
    cli.write_text("#!/bin/sh\n", encoding="utf-8")
    (build / "PlugIns" / "_pcbnew.kiface").write_text("kiface", encoding="utf-8")
    path, source = _provider_path(str(cli), None)
    assert path is None
    assert "KICAD_RUN_FROM_BUILD_DIR" in source
    path, source = _provider_path(str(cli), {"KICAD_RUN_FROM_BUILD_DIR": "1"})
    assert path is not None
