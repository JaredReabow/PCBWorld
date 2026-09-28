"""Installed-KiCad-CLI acceptance gate: complete report, exact identities.

Two things had to be established before this gate could be sound, and both are
measured rather than assumed.

**1. The stock CLI truncates its report.** KiCad's ``DRC_ENGINE`` caps findings
per error code (``ERROR_LIMIT = 199``, ``EXTENDED_ERROR_LIMIT = 499`` for
clearance and unconnected items). On the V3 board the installed 10.0.6 CLI
reported exactly 499 ``clearance`` findings and exactly 199 in five other
classes, every run — the caps, not the board. Comparing such a report by item
identity compares *which* findings made the cut: two runs of one unchanged file
disagreed on ~18 identities. The earlier "23 added clearance errors" was that
artefact. A capped report is therefore never acceptable evidence here.

**2. A complete report is deterministic and exact.** Built from the pinned source
(same tree as the engine, caps removed by the existing patch, stock copper
clearance provider), the CLI reports 9758 findings on the source board, repeats
that report identity-for-identity across runs, and reproduces the source exactly
for a physically identical load/save copy. Against it, a candidate either has an
identity the source does not have or it does not.

Where that does *not* hold - the reporter picks a different representative item
for the same physical condition between runs (measured: `clearance` on the source
board, and `unconnected_items`, where 4 of 106 pairings differ between two runs of
one unchanged board) - the class is judged over the **union of its runs**: an
identity no source run ever reported is an addition, and an identity no candidate
run ever reported is a removal. Judging the union is strictly stronger than
judging one run (a finding reported once still counts) and it does not punish the
reporter for churning which item it names.

The gate therefore refuses to judge a report that may be truncated, refuses to
judge a class whose identity set is not reproducible between runs of the same
board, and otherwise requires **no added identity in any class** plus a
non-increasing count. There is no count-only acceptance path and no tolerance:
equal totals with a swapped finding are a regression, because the swapped
finding is an added identity.

**3. Completeness is a property of a vetted reporter, not of a count.** "No
class sits at one of the two known caps" is not evidence that an arbitrary
binary reported everything: another build may cap at a different number, drop a
class, or ship a provider the gate never measured. The gate therefore has an
explicit supported-reporter policy (version, options, required severities) and
an expected provider location, and refuses any reporter outside it. The installed
10.0.6 application stays diagnostic-only.

**4. Identity is collision-safe, and duplicate rows are dropped.** The board
reuses UUIDs (769 of them; one on 73 different footprint graphics), so a UUID is
not a physical item: two different violations can collapse onto one key. Every
item identity therefore carries the UUID *plus* the item's own position and
semantics, and the pinned reporter's byte-identical repeated rows are collapsed
once (measured: 1151 of 9758 rows on the frozen source, and three extra identical
rows between two runs of the same refilled board). Rows that differ in any field
are kept, so the multiplicity of *distinct* findings is preserved, and a finding
identity that vanishes without the total dropping blocks the verdict.
"""

from __future__ import annotations

import collections
import json
import math
import os
import re
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from pcb_world.agent.scheduler import sha256_file

#: Absolute locations the installed CLI is known to use. PATH is not consulted
#: first: a missing PATH entry is not evidence that KiCad is not installed.
CLI_CANDIDATES = (
    "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli",
    "/usr/local/bin/kicad-cli",
    "/opt/homebrew/bin/kicad-cli",
    "/usr/bin/kicad-cli",
)

CLI_OPTIONS = ("pcb", "drc", "--format", "json", "--severity-all",
               "--all-track-errors")

#: Per-error-code report caps in stock KiCad's DRC engine. A class reported at
#: exactly one of these may be truncated, so the gate refuses to judge it.
KNOWN_REPORT_CAPS = (199, 499)

#: Reporters whose *completeness* has actually been established here: the CLI
#: built from the pinned source tree with the report caps removed (9.0.8). A
#: reporter outside this set cannot support an identity verdict - "not at a known
#: cap" is not evidence that some other binary reported everything - so the
#: installed 10.0.6 application (capped) and any unknown build are refused.
#: Extend this tuple only with a measurement, never to make a run pass.
SUPPORTED_REPORTER_VERSIONS = ("9.0.8",)

#: Severities a report must declare it included before its classes can be judged.
REQUIRED_REPORT_SEVERITIES = ("error", "warning")

#: The vetted command line. A report produced with different options is not the
#: report this gate was measured on, so the gate refuses to judge it.
REQUIRED_REPORT_OPTIONS = CLI_OPTIONS


class CliUnavailableError(RuntimeError):
    """No usable installed KiCad CLI was found."""


def discover_cli(explicit: str | None = None) -> str | None:
    """Absolute path to an installed ``kicad-cli``, or ``None``.

    ``explicit`` wins when it names an executable file. Otherwise the known
    install locations are probed, then ``PATH`` as a last resort.
    """
    if explicit:
        return explicit if os.path.isfile(explicit) and os.access(explicit, os.X_OK) \
            else None
    for candidate in CLI_CANDIDATES:
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        if not directory:
            continue
        candidate = os.path.join(directory, "kicad-cli")
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def cli_version(cli_path: str, *, timeout_s: float = 60.0,
                env: Mapping[str, str] | None = None) -> str | None:
    try:
        proc = _run_owned([cli_path, "--version"], timeout_s=timeout_s, env=env)
    except (OSError, subprocess.SubprocessError):
        return None
    text = (proc.stdout or proc.stderr or "").strip()
    version = text.splitlines()[0].strip() if text else None
    return version if version and re.search(r"\b\d+\.\d+(?:\.\d+)?\b", version) else None


def _run_owned(command: Sequence[str], *, timeout_s: float,
               env: Mapping[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """Run a CLI in an owned process group and reap its descendants on timeout.

    ``env`` entries are *added* to this process's environment rather than
    replacing it: a task-isolated build-tree CLI needs its marker and framework
    path, and it also needs the inherited ``PATH``, ``HOME`` and loader variables
    to start at all. A caller's value wins over the inherited one.
    """
    child_env = None
    if env is not None:
        child_env = {**os.environ, **{str(k): str(v) for k, v in env.items()}}
    proc = subprocess.Popen(
        list(command), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env=child_env,
        start_new_session=(os.name == "posix"),
    )
    try:
        stdout, stderr = proc.communicate(timeout=max(0.001, timeout_s))
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        else:
            proc.terminate()
        try:
            proc.communicate(timeout=1.0)
        except subprocess.TimeoutExpired:
            if os.name == "posix":
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                proc.kill()
            proc.communicate()
        raise subprocess.TimeoutExpired(command, timeout_s)
    return subprocess.CompletedProcess(command, proc.returncode, stdout, stderr)


def run_drc(
    cli_path: str, board_path: str, output_path: str, *, timeout_s: float = 600.0,
    env: Mapping[str, str] | None = None, options: Sequence[str] = CLI_OPTIONS,
) -> dict[str, Any]:
    """One read-only CLI DRC run. Never touches the board."""
    command = [cli_path, *options, "--output", output_path, board_path]
    proc = _run_owned(command, timeout_s=timeout_s, env=env)
    if proc.returncode != 0:
        raise RuntimeError(
            f"kicad-cli drc failed (exit {proc.returncode}): "
            f"{(proc.stderr or proc.stdout or '').strip()[:400]}"
        )
    return json.loads(Path(output_path).read_text(encoding="utf-8"))


def _stable_item_identity(item: Mapping[str, Any]) -> str | None:
    """Collision-safe identity for one reported item.

    A UUID alone does not name a physical item on this board (769 UUIDs are
    reused; one of them on 73 different footprint graphics), so an identity built
    from the UUID only lets two different items - and therefore two different
    violations - share one key. The identity carries everything the report
    itself gives about the item: the UUID, its position, the first non-empty
    reference/pad/lib-id field, and the item's description (the CLI's own
    ``<type> [<net>] on <layer>`` rendering). Two distinct items under one UUID
    differ in at least one of those.

    ``None`` means the item carries nothing that identifies it; that makes the
    report identity-incomplete, which :func:`validate_report` refuses.
    """
    item_uuid = str(item.get("uuid") or "").strip()
    description = str(item.get("description") or "").strip()
    semantic = next((str(item.get(key) or "").strip()
                     for key in ("reference", "ref", "pad", "lib_id")
                     if str(item.get(key) or "").strip()), "")
    x = y = None
    pos = item.get("pos")
    if isinstance(pos, Mapping):
        try:
            x, y = float(pos["x"]), float(pos["y"])
        except (KeyError, TypeError, ValueError):
            x = y = None
        else:
            if not (math.isfinite(x) and math.isfinite(y)):
                x = y = None
    if not (item_uuid or description or semantic):
        return None
    return json.dumps([
        item_uuid,
        None if x is None else round(x, 6),
        None if y is None else round(y, 6),
        semantic,
        description,
    ], separators=(",", ":"), ensure_ascii=True)


def _canonical_row(row: Mapping[str, Any]) -> str:
    """Byte-canonical form of one finding row, for exact-duplicate detection."""
    return json.dumps(row, sort_keys=True, separators=(",", ":"), default=str)


def _collapse_rows(rows: Iterable[Any]) -> tuple[list[Any], int]:
    """Drop rows repeated *byte for byte*; return (kept, dropped).

    The pinned 9.0.8 reporter emits the same finding more than once - a violation
    is reported once per item pair, and UUID-shared items produce literally
    identical rows (1151 of 9758 rows on the frozen source; three extra identical
    rows between two runs of the same refilled board). Those repeats carry no
    extra information, and left in place they make a class *count* - and with it
    the whole verdict - vary between runs of one unchanged board. Rows that
    differ in any field are kept, so the multiplicity of distinct findings is
    preserved.
    """
    kept: list[Any] = []
    seen: set[str] = set()
    dropped = 0
    for row in rows:
        if not isinstance(row, Mapping):
            kept.append(row)          # validate_report rejects it later
            continue
        if not row.get("items"):
            # A finding the reporter emits without items (copper_sliver on this
            # board) has no per-item identity to key on, so its multiplicity is
            # the only signal it carries. Keep every occurrence; if the reporter
            # cannot repeat that count, the class is refused as non-reproducible
            # rather than silently collapsed.
            kept.append(row)
            continue
        key = _canonical_row(row)
        if key in seen:
            dropped += 1
            continue
        seen.add(key)
        kept.append(row)
    return kept, dropped


def entity_identity(finding: Mapping[str, Any]) -> tuple:
    """Identity of one finding: class, severity, and its collision-safe items.

    A finding the reporter emits without items is identified by its own
    description instead, so it is still counted and compared rather than either
    waved through or refused outright.
    """
    items = [i for i in (finding.get("items") or []) if i]
    if not items:
        return (
            str(finding.get("type")), str(finding.get("severity")),
            ("finding:" + str(finding.get("description") or "").strip(),),
        )
    return (
        str(finding.get("type")), str(finding.get("severity")),
        tuple(sorted(_stable_item_identity(i) for i in items)),
    )


def validate_report(report: Any) -> tuple[str, ...]:
    """Reject missing, malformed, or identity-incomplete CLI report sections."""
    if not isinstance(report, Mapping):
        return ("report root is not an object",)
    version = report.get("kicad_version")
    if not isinstance(version, str) or not re.search(r"\d+\.\d+(?:\.\d+)?", version):
        return ("report has no parseable KiCad version",)
    violations = report.get("violations")
    unconnected = report.get("unconnected_items")
    if not isinstance(violations, list) or not isinstance(unconnected, list):
        return ("report is missing violations or unconnected_items arrays",)
    errors: list[str] = []
    for section, rows in (("violations", violations), ("unconnected_items", unconnected)):
        for index, row in enumerate(rows):
            if not isinstance(row, Mapping):
                errors.append(f"{section}[{index}] is not an object")
                continue
            if section == "violations":
                if not isinstance(row.get("type"), str) or not row["type"]:
                    errors.append(f"violations[{index}] has no type")
                if not isinstance(row.get("severity"), str) or not row["severity"]:
                    errors.append(f"violations[{index}] has no severity")
                items = row.get("items")
                if not isinstance(items, list):
                    errors.append(f"violations[{index}] has no items array")
                    continue
                if not items:
                    # The pinned reporter emits copper_sliver without items. The
                    # finding is still identified - by its own description - and
                    # an item-less *and* description-less row is not identifiable
                    # at all, so that is refused.
                    if not str(row.get("description") or "").strip():
                        errors.append(
                            f"violations[{index}] has no items and no description"
                        )
                    continue
                for item_index, item in enumerate(items):
                    if not isinstance(item, Mapping) or _stable_item_identity(item) is None:
                        errors.append(
                            f"violations[{index}].items[{item_index}] has no stable identity"
                        )
            else:
                items = row.get("items")
                identities = items if isinstance(items, list) else [row]
                if not identities or any(
                    not isinstance(item, Mapping) or _stable_item_identity(item) is None
                    for item in identities
                ):
                    errors.append(f"unconnected_items[{index}] has no stable identity")
    return tuple(errors)


UNCONNECTED_CLASS = "__unconnected_items__"


def _terminal_proof_problem(proof: Mapping[str, Any] | None, *,
                            candidate_board_sha256: str | None,
                            reference_board_sha256: str | None) -> str | None:
    """Why a native terminal-partition proof cannot stand in for the CLI's
    unconnected endpoint pairing (``None`` means it can).

    The proof must come from a fresh native process, must have read *both* boards
    it names (so it is not a replayed snapshot), must report a complete capture,
    and must name both boards' hashes. Anything less is not evidence and the CLI
    verdict stays unverified.
    """
    if not isinstance(proof, Mapping):
        return "no native terminal-partition proof was supplied"
    if proof.get("fresh_process") is not True:
        return ("the supplied terminal proof does not come from a fresh native "
                "process over both boards")
    if not proof.get("ok") or not proof.get("complete"):
        return "the supplied terminal proof did not report ok/complete"
    if str(proof.get("candidate_board_sha256")) != str(candidate_board_sha256):
        return ("the supplied terminal proof is not bound to this candidate board "
                f"({proof.get('candidate_board_sha256')} vs {candidate_board_sha256})")
    if str(proof.get("original_reference_sha256")) != str(reference_board_sha256):
        return ("the supplied terminal proof is not bound to the reference board "
                f"({proof.get('original_reference_sha256')} vs {reference_board_sha256})")
    return None


def _report_classes(payload: Mapping[str, Any]) -> tuple[dict[str, list], dict[str, int]]:
    """Report sections as class lists, after dropping byte-identical repeat rows."""
    violations, dropped_v = _collapse_rows(payload.get("violations") or [])
    unconnected, dropped_u = _collapse_rows(payload.get("unconnected_items") or [])
    out: dict[str, list] = {}
    for finding in violations:
        out.setdefault(str(finding.get("type")), []).append(finding)
    out[UNCONNECTED_CLASS] = [
        {"type": "unconnected", "severity": "error",
         "items": row.get("items") if isinstance(row, Mapping)
         and isinstance(row.get("items"), list) else [row]}
        for row in unconnected
    ]
    stats = {
        "raw_violations": len(payload.get("violations") or []),
        "raw_unconnected": len(payload.get("unconnected_items") or []),
        "duplicate_rows_dropped": dropped_v + dropped_u,
    }
    return out, stats


def _raw_class_counts(payload: Mapping[str, Any]) -> dict[str, int]:
    """Per-class row counts as reported, before duplicate rows are dropped."""
    counts: dict[str, int] = {}
    for finding in payload.get("violations") or []:
        if isinstance(finding, Mapping):
            kind = str(finding.get("type"))
            counts[kind] = counts.get(kind, 0) + 1
    counts[UNCONNECTED_CLASS] = len(payload.get("unconnected_items") or [])
    return counts


@dataclass(frozen=True)
class ClassComparison:
    """One violation class, compared between two boards."""

    kind: str
    source_counts: tuple[int, ...]
    candidate_counts: tuple[int, ...]
    identity_reproducible: bool
    source_identities: int
    source_seen_across_runs: int
    candidate_identities: int
    candidate_seen_across_runs: int
    added_identities: int
    count_delta: int
    #: Raw rows as reported, before byte-identical repeats were dropped.
    source_rows: tuple[int, ...] = ()
    candidate_rows: tuple[int, ...] = ()
    #: Rows dropped as byte-identical repeats, summed over the runs of each side.
    duplicate_rows_dropped_source: int = 0
    duplicate_rows_dropped_candidate: int = 0
    #: Identities the source reports that the candidate does not. Zero unless the
    #: class is reproducible; a non-zero value with no drop in the total means a
    #: finding was swapped for another rather than resolved.
    removed_identities: int = 0
    #: How many *distinct* rows share one identity, at most, in each board. One
    #: means every identity is unique; higher means the reporter emitted several
    #: distinct findings under one identity, which the comparison keeps.
    source_max_multiplicity: int = 0
    candidate_max_multiplicity: int = 0
    #: Identities that do not appear in every run of that board (the reporter
    #: named a different item for the same condition). Reported, not fatal.
    source_churned_identities: int = 0
    candidate_churned_identities: int = 0

    @property
    def identity_comparison(self) -> str:
        return "exact" if self.identity_reproducible else "unavailable_non_reproducible"

    def to_evidence(self) -> dict[str, Any]:
        return {
            "type": self.kind,
            "source_counts": list(self.source_counts),
            "candidate_counts": list(self.candidate_counts),
            "count_delta": self.count_delta,
            "identity_comparison": self.identity_comparison,
            "source_identities": self.source_identities,
            "source_identities_seen_across_runs": self.source_seen_across_runs,
            "candidate_identities": self.candidate_identities,
            "candidate_identities_seen_across_runs": self.candidate_seen_across_runs,
            "added_identities": self.added_identities,
            "removed_identities": self.removed_identities,
            "rows_as_reported": {"source": list(self.source_rows),
                                 "candidate": list(self.candidate_rows)},
            "duplicate_rows_dropped": {
                "source": self.duplicate_rows_dropped_source,
                "candidate": self.duplicate_rows_dropped_candidate,
            },
            "max_distinct_rows_per_identity": {
                "source": self.source_max_multiplicity,
                "candidate": self.candidate_max_multiplicity,
            },
            "churned_identities": {
                "source": self.source_churned_identities,
                "candidate": self.candidate_churned_identities,
            },
        }


@dataclass(frozen=True)
class CliVerdict:
    """The gate's decision, with everything a reviewer needs to re-judge it."""

    status: str                 # verified | unverified | regressed | unavailable | not_configured
    ok: bool
    required: bool
    reasons: tuple[str, ...] = ()
    cli_path: str | None = None
    cli_version: str | None = None
    options: tuple[str, ...] = CLI_OPTIONS
    runs: int = 0
    source_board_sha256: str | None = None
    candidate_board_sha256: str | None = None
    source_rules_sha256: str | None = None
    candidate_rules_sha256: str | None = None
    source_project_sha256: str | None = None
    candidate_project_sha256: str | None = None
    cli_sha256: str | None = None
    provider_sha256: str | None = None
    source_totals: tuple[int, ...] = ()
    candidate_totals: tuple[int, ...] = ()
    unconnected_source: tuple[int, ...] = ()
    unconnected_candidate: tuple[int, ...] = ()
    #: Classes whose reported count equals a known report cap: the report may be
    #: truncated, so no verdict is drawn from it.
    suspected_truncation: tuple[str, ...] = ()
    report_complete: bool = True
    classes: tuple[ClassComparison, ...] = ()
    report_paths: tuple[str, ...] = ()
    #: The reporter policy the verdict was decided under, and the rows dropped as
    #: byte-identical repeats (source runs, candidate runs).
    supported_reporter_versions: tuple[str, ...] = SUPPORTED_REPORTER_VERSIONS
    required_severities: tuple[str, ...] = REQUIRED_REPORT_SEVERITIES
    duplicate_rows_dropped: tuple[int, int] = (0, 0)
    removed_identities: int = 0
    #: Hashes of every input, captured *before* anything was staged or run, and
    #: whether the staged copies were byte-identical to those inputs.
    input_hashes: tuple[tuple[str, str], ...] = ()
    hash_capture: str = ""
    staged_inputs_match: bool = False
    provider_source: str | None = None
    expected_cli_sha256: str | None = None
    expected_provider_sha256: str | None = None

    def to_evidence(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "ok": self.ok,
            "required": self.required,
            "reasons": list(self.reasons),
            "cli_path": self.cli_path,
            "cli_version": self.cli_version,
            "options": list(self.options),
            # What a reviewer needs to re-judge the verdict without re-running it:
            # which binary and version answered, whether its report was complete,
            # and the policy that refused to judge a truncated one.
            "selected_cli": {
                "path": self.cli_path,
                "version": self.cli_version,
                "report_complete": self.report_complete,
                "suspected_truncation": list(self.suspected_truncation),
            },
            "cap_policy": {
                "known_per_class_report_caps": list(KNOWN_REPORT_CAPS),
                "truncated_reports_refused": True,
                "minimum_runs_per_board": 2,
                "count_only_acceptance": False,
            },
            "reporter_policy": {
                "supported_versions": list(self.supported_reporter_versions),
                "required_severities": list(self.required_severities),
                "required_options": list(REQUIRED_REPORT_OPTIONS),
                "expected_cli_sha256": self.expected_cli_sha256,
                "expected_provider_sha256": self.expected_provider_sha256,
                "outside_policy_refused": True,
            },
            "identity_policy": {
                "definition": "class + severity + per-item (uuid, position, "
                              "semantic field, description)",
                "uuid_alone_is_not_an_identity": True,
                "byte_identical_repeat_rows_dropped": list(self.duplicate_rows_dropped),
                "distinct_finding_multiplicity_preserved": True,
            },
            "runs": self.runs,
            "source_board_sha256": self.source_board_sha256,
            "candidate_board_sha256": self.candidate_board_sha256,
            "source_rules_sha256": self.source_rules_sha256,
            "candidate_rules_sha256": self.candidate_rules_sha256,
            "source_project_sha256": self.source_project_sha256,
            "candidate_project_sha256": self.candidate_project_sha256,
            "cli_sha256": self.cli_sha256,
            "provider_sha256": self.provider_sha256,
            "provider_source": self.provider_source,
            "source_totals": list(self.source_totals),
            "candidate_totals": list(self.candidate_totals),
            "unconnected_source": list(self.unconnected_source),
            "unconnected_candidate": list(self.unconnected_candidate),
            "removed_identities": self.removed_identities,
            "input_hashes": dict(self.input_hashes),
            "hash_capture": self.hash_capture,
            "staged_inputs_match": self.staged_inputs_match,
            "suspected_truncation": list(self.suspected_truncation),
            "report_complete": self.report_complete,
            "classes": [c.to_evidence() for c in self.classes],
            "report_paths": list(self.report_paths),
            "report_sha256": {path: sha256_file(path) for path in self.report_paths},
        }


def compare_reports(
    source_reports: Sequence[Mapping[str, Any]],
    candidate_reports: Sequence[Mapping[str, Any]],
) -> tuple[tuple[ClassComparison, ...], list[str]]:
    """Class-by-class comparison of repeated runs; returns (classes, problems).

    Rows that repeat byte for byte are dropped once (see :func:`_collapse_rows`)
    and the remaining rows are compared by collision-safe identity *and* by how
    many distinct rows carry each identity, so a candidate that swaps a finding
    for another at an equal total cannot hide behind one shared identity.
    """
    if len(source_reports) != len(candidate_reports) or not source_reports:
        raise ValueError("both boards need the same non-zero number of runs")
    problems: list[str] = []
    parsed_source = [_report_classes(r) for r in source_reports]
    parsed_candidate = [_report_classes(r) for r in candidate_reports]
    raw_source = [_raw_class_counts(r) for r in source_reports]
    raw_candidate = [_raw_class_counts(r) for r in candidate_reports]
    source_by_class = [entry[0] for entry in parsed_source]
    candidate_by_class = [entry[0] for entry in parsed_candidate]
    kinds = sorted(set().union(*(set(m) for m in source_by_class)) |
                   set().union(*(set(m) for m in candidate_by_class)))
    comparisons: list[ClassComparison] = []
    for kind in kinds:
        s_lists = [m.get(kind, []) for m in source_by_class]
        c_lists = [m.get(kind, []) for m in candidate_by_class]
        s_counts = tuple(len(items) for items in s_lists)
        c_counts = tuple(len(items) for items in c_lists)
        raw_s = tuple(raw.get(kind, 0) for raw in raw_source)
        raw_c = tuple(raw.get(kind, 0) for raw in raw_candidate)
        if kind == UNCONNECTED_CLASS:
            # ``unconnected_items`` is a list of endpoint row pairs, and the
            # reporter emits a varying number of *byte-identical repeats* on an
            # unchanged board (measured: 143 rows either way, 17 or 18 of them
            # repeats). The physical quantity is the raw row count, so stability
            # and the count delta are judged on raw rows; the collapsed identity
            # sets are diagnostics, exempted behind a fresh terminal proof in
            # ``verdict_from_reports`` and never used to hide a rise.
            if len(set(raw_s)) != 1 or len(set(raw_c)) != 1:
                problems.append(
                    f"class {kind!r} raw total varies between runs of one board: "
                    f"source {list(raw_s)}, candidate {list(raw_c)}"
                )
        elif len(set(s_counts)) != 1 or len(set(c_counts)) != 1:
            problems.append(
                f"class {kind!r} total varies between runs of one board even after "
                f"byte-identical repeat rows are dropped: "
                f"source {s_counts}, candidate {c_counts}"
            )
        s_counters = [collections.Counter(entity_identity(f) for f in items)
                      for items in s_lists]
        c_counters = [collections.Counter(entity_identity(f) for f in items)
                      for items in c_lists]
        s_sets = [set(counter) for counter in s_counters]
        c_sets = [set(counter) for counter in c_counters]
        s_inter, c_inter = set.intersection(*s_sets), set.intersection(*c_sets)
        s_union, c_union = set.union(*s_sets), set.union(*c_sets)
        reproducible = len(s_inter) == len(s_union) and len(c_inter) == len(c_union)
        # Judged over the union, not one arbitrary run: an identity seen in ANY
        # candidate run that no source run ever reported is an addition, and the
        # reverse is a removal. Reproducibility is reported, not required, because
        # the reporter's choice of representative item for the same condition
        # churns on this board.
        added = len(c_union - s_union)
        removed = len(s_union - c_union)
        s_multi = max((max(counter.values()) if counter else 0) for counter in s_counters)
        c_multi = max((max(counter.values()) if counter else 0) for counter in c_counters)
        dropped_source = sum(raw.get(kind, 0) - len(items)
                             for raw, items in zip(raw_source, s_lists))
        dropped_candidate = sum(raw.get(kind, 0) - len(items)
                                for raw, items in zip(raw_candidate, c_lists))
        # The unconnected section's delta is the raw row count (the reporter's
        # endpoint pairing is a visualisation choice); every relevant class keeps
        # the identity-collapsed delta.
        count_delta = (raw_c[0] - raw_s[0] if kind == UNCONNECTED_CLASS
                       else c_counts[0] - s_counts[0])
        comparisons.append(ClassComparison(
            kind=kind, source_counts=s_counts, candidate_counts=c_counts,
            identity_reproducible=reproducible,
            source_identities=len(s_sets[0]), source_seen_across_runs=len(s_union),
            candidate_identities=len(c_sets[0]), candidate_seen_across_runs=len(c_union),
            added_identities=added, count_delta=count_delta,
            source_rows=raw_s, candidate_rows=raw_c,
            duplicate_rows_dropped_source=dropped_source,
            duplicate_rows_dropped_candidate=dropped_candidate,
            removed_identities=removed,
            source_max_multiplicity=s_multi,
            candidate_max_multiplicity=c_multi,
            source_churned_identities=len(s_union) - len(s_inter),
            candidate_churned_identities=len(c_union) - len(c_inter),
        ))
    return tuple(comparisons), problems


def verdict_from_reports(
    source_reports: Sequence[Mapping[str, Any]],
    candidate_reports: Sequence[Mapping[str, Any]],
    *,
    cli_path: str,
    cli_version_text: str | None,
    source_board: str,
    candidate_board: str,
    source_rules: str | None = None,
    candidate_rules: str | None = None,
    source_project: str | None = None,
    candidate_project: str | None = None,
    cli_sha256: str | None = None,
    provider_sha256: str | None = None,
    report_paths: Iterable[str] = (),
    required: bool = True,
    supported_reporter_versions: Sequence[str] = SUPPORTED_REPORTER_VERSIONS,
    required_severities: Sequence[str] = REQUIRED_REPORT_SEVERITIES,
    expected_cli_sha256: str | None = None,
    expected_provider_sha256: str | None = None,
    provider_source: str | None = None,
    input_hashes: Mapping[str, str] | None = None,
    hash_capture: str = "",
    staged_inputs_match: bool = False,
    terminal_partition_proof: Mapping[str, Any] | None = None,
) -> CliVerdict:
    """Decide whether the candidate may be promoted, with reasons.

    Completeness is a property of a *vetted reporter*: the version must be one
    whose reports were measured to be complete, the report must declare the
    severities the gate depends on, and no class may sit on a known cap. An
    unknown binary that merely reports different numbers is refused - "not at a
    known cap" is not evidence that some other build reported everything.
    """
    policy = tuple(supported_reporter_versions)
    severities = tuple(required_severities)
    validation = [
        f"{side} run {index + 1}: {problem}"
        for side, reports in (("source", source_reports), ("candidate", candidate_reports))
        for index, report in enumerate(reports)
        for problem in validate_report(report)
    ]
    versions = {
        re.search(r"\d+\.\d+(?:\.\d+)?", str(report.get("kicad_version"))).group(0)
        for report in [*source_reports, *candidate_reports]
        if isinstance(report, Mapping)
        and re.search(r"\d+\.\d+(?:\.\d+)?", str(report.get("kicad_version")))
    }
    outside_policy = sorted(versions - set(policy))
    if outside_policy:
        validation.append(
            f"reporter version(s) {outside_policy} are outside the vetted set "
            f"{sorted(policy)}: report completeness cannot be claimed for an "
            "arbitrary binary, so no identity verdict is drawn from it"
        )
    for side, reports in (("source", source_reports), ("candidate", candidate_reports)):
        for index, report in enumerate(reports):
            if not isinstance(report, Mapping):
                continue
            declared = {str(s) for s in (report.get("included_severities") or [])}
            missing = [s for s in severities if s not in declared]
            if missing:
                validation.append(
                    f"{side} run {index + 1}: the report does not declare it "
                    f"included these severities: {missing}"
                )
    cli_version_match = re.search(r"\d+\.\d+(?:\.\d+)?", cli_version_text or "")
    if not cli_version_match or versions != {cli_version_match.group(0)}:
        validation.append(
            f"CLI version/report mismatch: executable={cli_version_match.group(0) if cli_version_match else None}, "
            f"reports={sorted(versions)}"
        )
    if expected_cli_sha256 and cli_sha256 != expected_cli_sha256:
        validation.append(
            "the selected CLI does not have the configured expected sha256 "
            f"(expected {expected_cli_sha256}, got {cli_sha256})"
        )
    if expected_provider_sha256 and provider_sha256 != expected_provider_sha256:
        validation.append(
            "the loaded provider does not have the configured expected sha256 "
            f"(expected {expected_provider_sha256}, got {provider_sha256})"
        )
    sidecar_hashes = {
        "source_rules": sha256_file(source_rules) if source_rules else None,
        "candidate_rules": sha256_file(candidate_rules) if candidate_rules else None,
        "source_project": sha256_file(source_project) if source_project else None,
        "candidate_project": sha256_file(candidate_project) if candidate_project else None,
    }
    if (source_rules or candidate_rules) and sidecar_hashes["source_rules"] != sidecar_hashes["candidate_rules"]:
        validation.append("source and candidate .kicad_dru hashes differ")
    if (source_project or candidate_project) and sidecar_hashes["source_project"] != sidecar_hashes["candidate_project"]:
        validation.append("source and candidate .kicad_pro hashes differ")
    if validation:
        return CliVerdict(
            status="unverified", ok=False, required=required, reasons=tuple(validation),
            cli_path=cli_path, cli_version=cli_version_text, runs=min(len(source_reports), len(candidate_reports)),
            source_board_sha256=sha256_file(source_board),
            candidate_board_sha256=sha256_file(candidate_board),
            source_rules_sha256=sidecar_hashes["source_rules"],
            candidate_rules_sha256=sidecar_hashes["candidate_rules"],
            source_project_sha256=sidecar_hashes["source_project"],
            candidate_project_sha256=sidecar_hashes["candidate_project"],
            cli_sha256=cli_sha256, provider_sha256=provider_sha256,
            report_paths=tuple(report_paths), report_complete=False,
            supported_reporter_versions=policy, required_severities=severities,
            input_hashes=tuple(sorted((input_hashes or {}).items())),
            hash_capture=hash_capture, staged_inputs_match=staged_inputs_match,
            provider_source=provider_source,
            expected_cli_sha256=expected_cli_sha256,
            expected_provider_sha256=expected_provider_sha256,
        )
    classes, problems = compare_reports(source_reports, candidate_reports)
    reasons: list[str] = list(problems)

    # A single run cannot demonstrate that the reporter is reproducible, and an
    # unreproducible reporter is exactly what this gate must not trust.
    insufficient_runs = len(source_reports) < 2
    if insufficient_runs:
        reasons.append(
            f"at least two runs per board are required to demonstrate report "
            f"reproducibility (got {len(source_reports)})"
        )

    # Completeness first: a report sitting on a known per-class cap may be
    # truncated, and a truncated report cannot support any identity claim.
    suspected = tuple(sorted({
        comparison.kind
        for comparison in classes
        # Both the raw rows as reported and the identity-collapsed rows are
        # checked: a report that sat on a known cap would show it in either.
        if any(count in KNOWN_REPORT_CAPS for count in (
            *comparison.source_counts, *comparison.candidate_counts,
            *comparison.source_rows, *comparison.candidate_rows))
    }))
    if suspected:
        reasons.append(
            "possible report truncation: "
            + ", ".join(suspected)
            + f" report a count equal to a known KiCad per-class cap "
              f"({sorted(KNOWN_REPORT_CAPS)}); a capped report cannot be judged by "
              "identity or count. Use a CLI built with the report caps removed "
              "(the pinned source already carries that patch) and re-run."
        )

    # A RELEVANT class whose identities are not reproducible is refused, not
    # judged over the union: with an intermittent source finding X and a
    # persistent candidate X, a union comparison would hide a resolved Y. The
    # union stays available as a diagnostic only.
    unstable = tuple(sorted(
        comparison.kind for comparison in classes
        if not comparison.identity_reproducible and comparison.kind != UNCONNECTED_CLASS
    ))
    churned_unconnected = any(
        comparison.kind == UNCONNECTED_CLASS and not comparison.identity_reproducible
        for comparison in classes
    )
    unproven_unconnected: str | None = None
    if unstable:
        reasons.append(
            "identity comparison unavailable for relevant class(es) "
            + ", ".join(unstable)
            + ": the reporter does not name the same items for the same finding "
              "between runs of one board, so an intermittent source finding could "
              "be matched by a persistent candidate one; no union substitute is an "
              "acceptance argument"
        )
    if churned_unconnected:
        problem = _terminal_proof_problem(
            terminal_partition_proof,
            candidate_board_sha256=sha256_file(candidate_board),
            reference_board_sha256=sha256_file(source_board),
        )
        if problem:
            unproven_unconnected = (
                "the CLI's unconnected section churned its endpoint pairing "
                f"(visualisation, not a fixed terminal relation) and {problem}; "
                "without a fresh native terminal-partition proof bound to this "
                "candidate the verdict stays unverified"
            )
            reasons.append(unproven_unconnected)
        else:
            reasons.append(
                "the CLI's unconnected endpoint pairing churned; accepted only "
                "because a bound native terminal-partition proof shows every "
                "original connection preserved"
            )

    # What the candidate is actually worse at, whether or not an earlier refusal
    # already stopped the verdict: a refusal has to name everything it saw, not
    # just the first reason it could not judge a class. Tracked explicitly rather
    # than inferred from wording, so a diagnostic note never changes the verdict.
    # The reporter's ``unconnected_items`` endpoint pairing is the one class whose
    # *identities* are a visualisation choice, and a fresh native terminal proof
    # for this candidate is what makes its churn diagnostic rather than a
    # regression. Its counts are still compared below.
    exempted_unconnected = churned_unconnected and unproven_unconnected is None
    regressions: list[str] = []
    for comparison in classes:
        # The unconnected section's rise is reported once, below, from its raw
        # row counts; every other class is reported here from its
        # identity-collapsed delta.
        if comparison.count_delta > 0 and comparison.kind != UNCONNECTED_CLASS:
            regressions.append(
                f"{comparison.kind}: candidate has {comparison.count_delta} more "
                f"finding(s) than the source ({comparison.candidate_counts[0]} vs "
                f"{comparison.source_counts[0]})"
            )
        identity_reasons_allowed = not (
            exempted_unconnected and comparison.kind == UNCONNECTED_CLASS
        )
        if comparison.added_identities and identity_reasons_allowed:
            regressions.append(
                f"{comparison.kind}: {comparison.added_identities} finding identity/ies "
                "the source does not have in any of its runs (compared over the "
                "union of runs, so a churned pairing cannot hide one)"
            )
        # The other half of an equal-total swap: an identity the source reports
        # that the candidate does not. Resolving a finding normally lowers the
        # total, so a vanishing identity *without* a drop means a finding was
        # replaced by another rather than fixed.
        if (identity_reasons_allowed and comparison.removed_identities
                and comparison.candidate_counts[0] >= comparison.source_counts[0]):
            regressions.append(
                f"{comparison.kind}: {comparison.removed_identities} finding "
                "identity/ies the source reports are missing from the candidate "
                f"while its total did not drop ({comparison.candidate_counts[0]} "
                f"vs {comparison.source_counts[0]})"
            )
    source_totals = tuple(len(r.get("violations") or []) for r in source_reports)
    candidate_totals = tuple(len(r.get("violations") or []) for r in candidate_reports)
    unconnected_source = tuple(len(r.get("unconnected_items") or []) for r in source_reports)
    unconnected_candidate = tuple(len(r.get("unconnected_items") or [])
                                  for r in candidate_reports)
    if candidate_totals[0] > source_totals[0]:
        regressions.append(
            f"total findings rose: {candidate_totals[0]} vs {source_totals[0]}"
        )
    if unconnected_candidate[0] > unconnected_source[0]:
        regressions.append(
            f"unconnected items rose: {unconnected_candidate[0]} vs "
            f"{unconnected_source[0]}"
        )

    if problems or suspected or insufficient_runs or unstable or unproven_unconnected:
        duplicate_rows = (
            sum(c.duplicate_rows_dropped_source for c in classes),
            sum(c.duplicate_rows_dropped_candidate for c in classes),
        )
        return CliVerdict(
            status="unverified", ok=False, required=required,
            reasons=tuple(reasons) + tuple(regressions),
            cli_path=cli_path, cli_version=cli_version_text, runs=len(source_reports),
            source_board_sha256=sha256_file(source_board),
            candidate_board_sha256=sha256_file(candidate_board),
            source_rules_sha256=sha256_file(source_rules) if source_rules else None,
            candidate_rules_sha256=sha256_file(candidate_rules) if candidate_rules else None,
            source_project_sha256=sidecar_hashes["source_project"],
            candidate_project_sha256=sidecar_hashes["candidate_project"],
            cli_sha256=cli_sha256, provider_sha256=provider_sha256,
            classes=classes, report_paths=tuple(report_paths),
            suspected_truncation=suspected, report_complete=not suspected,
            supported_reporter_versions=policy, required_severities=severities,
            duplicate_rows_dropped=duplicate_rows,
            input_hashes=tuple(sorted((input_hashes or {}).items())),
            hash_capture=hash_capture, staged_inputs_match=staged_inputs_match,
            provider_source=provider_source,
            expected_cli_sha256=expected_cli_sha256,
            expected_provider_sha256=expected_provider_sha256,
        )

    reasons.extend(regressions)
    status = "regressed" if regressions else "verified"
    return CliVerdict(
        status=status, ok=status == "verified", required=required,
        reasons=tuple(reasons), cli_path=cli_path, cli_version=cli_version_text,
        runs=len(source_reports),
        source_board_sha256=sha256_file(source_board),
        candidate_board_sha256=sha256_file(candidate_board),
        source_rules_sha256=sha256_file(source_rules) if source_rules else None,
        candidate_rules_sha256=sha256_file(candidate_rules) if candidate_rules else None,
        source_project_sha256=sidecar_hashes["source_project"],
        candidate_project_sha256=sidecar_hashes["candidate_project"],
        cli_sha256=cli_sha256, provider_sha256=provider_sha256,
        source_totals=source_totals, candidate_totals=candidate_totals,
        unconnected_source=unconnected_source,
        unconnected_candidate=unconnected_candidate,
        classes=classes, report_paths=tuple(report_paths),
        suspected_truncation=(), report_complete=True,
        supported_reporter_versions=policy, required_severities=severities,
        duplicate_rows_dropped=(
            sum(c.duplicate_rows_dropped_source for c in classes),
            sum(c.duplicate_rows_dropped_candidate for c in classes),
        ),
        removed_identities=sum(c.removed_identities for c in classes),
        input_hashes=tuple(sorted((input_hashes or {}).items())),
        hash_capture=hash_capture, staged_inputs_match=staged_inputs_match,
        provider_source=provider_source,
        expected_cli_sha256=expected_cli_sha256,
        expected_provider_sha256=expected_provider_sha256,
    )


def unavailable_verdict(*, cli_path: str | None, required: bool,
                        reason: str | None = None) -> CliVerdict:
    """A precise "we could not ask the installed CLI" verdict (never a pass)."""
    return CliVerdict(
        status="unavailable", ok=not required, required=required,
        reasons=(reason or (
            "no installed kicad-cli was found (checked "
            + ", ".join(CLI_CANDIDATES) + " and PATH); no CLI acceptance is claimed"
        ),),
        cli_path=cli_path,
    )


def not_configured_verdict() -> CliVerdict:
    """Native-only acceptance, stated as such."""
    return CliVerdict(
        status="not_configured", ok=True, required=False,
        reasons=(
            "no installed-CLI gate configured for this run: promotion is validated "
            "by the native engine gate only, which is not a CLI acceptance",
        ),
    )


@dataclass
class CliGateConfig:
    """How hard to lean on the installed CLI for one run."""

    cli_path: str | None = None
    runs: int = 3
    required: bool = True
    timeout_s: float = 600.0
    work_dir: str | None = None
    extra_options: tuple[str, ...] = CLI_OPTIONS
    #: Environment added for the CLI process (the inherited environment is kept;
    #: these entries win). A task-isolated build-tree binary needs
    #: ``KICAD_RUN_FROM_BUILD_DIR=1`` and its own framework path; the user's global
    #: KiCad settings are never touched.
    env: Mapping[str, str] | None = None
    #: Current remaining run budget; queried before each owned CLI subprocess.
    remaining_budget_s: Callable[[], float] | None = None
    require_sidecars: bool = True
    #: Reporter policy. Only a version in this set can support a completeness
    #: claim; the installed 10.0.6 application is deliberately not in it.
    supported_reporter_versions: tuple[str, ...] = SUPPORTED_REPORTER_VERSIONS
    required_severities: tuple[str, ...] = REQUIRED_REPORT_SEVERITIES
    #: Optional exact hashes. When set, the selected binary and the provider it
    #: loads must match them; a rebuild changes the hash and the gate refuses
    #: until the operator re-pins, which is the point.
    expected_cli_sha256: str | None = None
    expected_provider_sha256: str | None = None


def _sidecar(board: str, explicit: str | None, suffix: str) -> str | None:
    candidate = explicit or str(Path(board).with_suffix(suffix))
    return candidate if os.path.isfile(candidate) else None


def _provider_path(cli_path: str, env: Mapping[str, str] | None) -> tuple[str | None, str | None]:
    """The kiface this CLI loads, from its documented location only.

    Returns ``(path, source)``. ``path`` is a file that really exists at the
    location the loader uses for this binary; ``source`` names which documented
    layout matched, so the evidence says *why* this file is believed to be the
    provider instead of listing every plausible path that happens to exist
    nearby. A path found by searching ancestors is not proof of what was loaded,
    so no such search happens here.
    """
    resolved = Path(cli_path).resolve()
    if not resolved.is_file():
        return None, "cli path is not a file"
    build_tree = bool(re.search(r"(?:^|/)build(?:_[^/]+)?(?:/|$)", str(resolved)))
    if build_tree and (env or {}).get("KICAD_RUN_FROM_BUILD_DIR") != "1":
        # Without the marker the build-tree CLI cannot find its kiface at all
        # ("Failed to load kiface library"), so there is nothing to hash.
        return None, "build-tree CLI without KICAD_RUN_FROM_BUILD_DIR=1"
    for ancestor in (resolved.parent, *resolved.parents):
        if ancestor.name.endswith(".app"):
            candidate = ancestor / "Contents" / "PlugIns" / "_pcbnew.kiface"
            if candidate.is_file():
                return str(candidate), "app_bundle:Contents/PlugIns/_pcbnew.kiface"
            return None, f"no _pcbnew.kiface in {ancestor}/Contents/PlugIns"
    # Non-bundle (Linux/portable) build trees keep the kiface beside the build.
    for ancestor in (resolved.parent, *resolved.parents[:4]):
        candidate = ancestor / "pcbnew" / "_pcbnew.kiface"
        if candidate.is_file():
            return str(candidate), "build_tree:pcbnew/_pcbnew.kiface"
    return None, "CLI is not inside a .app bundle and no build-tree kiface was found"


def _stage_cli_inputs(
    board: str, project: str | None, rules: str | None, target: str,
    expected: Mapping[str, str] | None = None, labels: Mapping[str, str] | None = None,
) -> tuple[str, str | None, str | None]:
    """Copy the three inputs next to each other and prove the copies are exact.

    ``expected`` carries the hashes captured *before* the copy; each staged file
    is compared against its own input's hash, so a file that changed between the
    hash and the copy (or a copy that is not byte-identical) is an error rather
    than something the later "did anything change during the run" check would
    silently absorb.
    """
    os.makedirs(target, exist_ok=False)
    staged_board = os.path.join(target, "board.kicad_pcb")
    shutil.copyfile(board, staged_board)
    staged_project = staged_rules = None
    if project:
        staged_project = os.path.join(target, "board.kicad_pro")
        shutil.copyfile(project, staged_project)
    if rules:
        staged_rules = os.path.join(target, "board.kicad_dru")
        shutil.copyfile(rules, staged_rules)
    for label, source, staged in (("board", board, staged_board),
                                  ("project", project, staged_project),
                                  ("rules", rules, staged_rules)):
        if not staged:
            continue
        want = (expected or {}).get((labels or {}).get(label, label))
        got = sha256_file(staged)
        if want and got != want:
            raise RuntimeError(
                f"staged {label} is not byte-identical to its source "
                f"(staged {got}, source {want})"
            )
        if sha256_file(source) != got:
            raise RuntimeError(
                f"staged {label} differs from {source}: the input changed while "
                "it was being copied"
            )
    return staged_board, staged_project, staged_rules


def run_gate(
    config: CliGateConfig,
    *,
    source_board: str,
    candidate_board: str,
    source_rules: str | None,
    candidate_rules: str | None,
    work_dir: str,
    source_project: str | None = None,
    candidate_project: str | None = None,
    terminal_partition_proof: Mapping[str, Any] | None = None,
) -> CliVerdict:
    """Run the CLI on both boards ``config.runs`` times and decide.

    ``source_project``/``candidate_project`` name the effective ``.kicad_pro``
    sidecars explicitly; when omitted the sibling of each board is used. They are
    part of the signature because the runner has always passed them - a gate that
    silently ignored the project a run was configured with would verify a
    different board setup than the one under test.

    ``terminal_partition_proof`` is the fresh native terminal-partition evidence
    for this candidate against this reference. The CLI's ``unconnected_items``
    section is the one class whose endpoint pairing is a visualisation choice
    rather than a fixed terminal relation (measured: 4 of 106 pairings differ
    between two runs of one unchanged board), so that class may be judged against
    the proof - and only that class. Without a proof, churned ``unconnected``
    identity keeps the verdict unverified.
    """
    cli = discover_cli(config.cli_path)
    if cli is None:
        return unavailable_verdict(cli_path=config.cli_path, required=config.required)
    options = tuple(config.extra_options)
    if options != tuple(REQUIRED_REPORT_OPTIONS):
        return CliVerdict(
            status="unverified", ok=False, required=config.required,
            reasons=(
                "configured CLI options are not the vetted set "
                f"({list(options)} vs {list(REQUIRED_REPORT_OPTIONS)}); a report "
                "produced with other options is not the report this gate measured",
            ),
            cli_path=cli, report_complete=False,
        )
    start = time.monotonic()
    remaining = (config.remaining_budget_s() if config.remaining_budget_s else float("inf"))
    if remaining <= 0:
        return unavailable_verdict(
            cli_path=cli, required=config.required,
            reason="remaining run budget expired before CLI verification",
        )
    gate_deadline = start + min(
        remaining, config.timeout_s * (1 + 2 * max(1, int(config.runs)))
    )

    def command_budget() -> float:
        local = gate_deadline - time.monotonic()
        if config.remaining_budget_s:
            local = min(local, config.remaining_budget_s())
        if local <= 0:
            raise TimeoutError("remaining run budget expired during CLI verification")
        return min(config.timeout_s, local)

    try:
        version = cli_version(cli, timeout_s=command_budget(), env=config.env)
    except Exception as exc:  # noqa: BLE001
        return unavailable_verdict(
            cli_path=cli, required=config.required,
            reason=f"CLI version check failed: {type(exc).__name__}: {exc}",
        )
    if version is None:
        return unavailable_verdict(
            cli_path=cli, required=config.required,
            reason="CLI did not return a parseable version",
        )
    cli_hash = sha256_file(cli)
    provider, provider_source = _provider_path(cli, config.env)
    build_tree = bool(re.search(r"(?:^|/)build(?:_[^/]+)?(?:/|$)", str(Path(cli).resolve())))
    if build_tree and provider is None:
        return unavailable_verdict(
            cli_path=cli, required=config.required,
            reason=("build-tree CLI provider (_pcbnew.kiface) could not be located "
                    f"and hashed: {provider_source}"),
        )
    provider_hash = sha256_file(provider) if provider else None

    source_project = _sidecar(source_board, source_project, ".kicad_pro")
    candidate_project = _sidecar(candidate_board, candidate_project, ".kicad_pro")
    source_rules = _sidecar(source_board, source_rules, ".kicad_dru")
    candidate_rules = _sidecar(candidate_board, candidate_rules, ".kicad_dru")
    # Hashes are captured HERE, before anything is staged or run: the verdict
    # names the inputs the gate actually saw, not re-hashes taken afterwards.
    inputs_before: dict[str, str] = {}
    for label, path in (("source_board", source_board),
                        ("candidate_board", candidate_board),
                        ("source_project", source_project),
                        ("candidate_project", candidate_project),
                        ("source_rules", source_rules),
                        ("candidate_rules", candidate_rules),
                        ("cli", cli), ("provider", provider)):
        if path:
            digest = sha256_file(path)
            if digest:
                inputs_before[label] = digest
    input_hashes = tuple(sorted(inputs_before.items()))

    def refused(reason: str) -> CliVerdict:
        return CliVerdict(
            status="unverified", ok=False, required=config.required,
            reasons=(reason,), cli_path=cli, cli_version=version,
            cli_sha256=cli_hash, provider_sha256=provider_hash,
            provider_source=provider_source, report_complete=False,
            supported_reporter_versions=tuple(config.supported_reporter_versions),
            required_severities=tuple(config.required_severities),
            input_hashes=input_hashes, hash_capture="before_staging",
            expected_cli_sha256=config.expected_cli_sha256,
            expected_provider_sha256=config.expected_provider_sha256,
        )

    if config.require_sidecars and not all((source_project, candidate_project,
                                            source_rules, candidate_rules)):
        return refused(
            "effective source/candidate .kicad_pro and .kicad_dru sidecars must "
            "all be present and copied to isolated CLI siblings"
        )
    for label, left, right in ((".kicad_pro", source_project, candidate_project),
                               (".kicad_dru", source_rules, candidate_rules)):
        if left and right and sha256_file(left) != sha256_file(right):
            return refused(f"effective source and candidate {label} files differ")
    report_paths: list[str] = []
    source_reports: list[dict] = []
    candidate_reports: list[dict] = []
    try:
        os.makedirs(work_dir, exist_ok=True)
        reports_dir = os.path.join(work_dir, f"cli-{time.time_ns()}-{os.getpid()}")
        os.makedirs(reports_dir, exist_ok=False)
        input_root = os.path.join(reports_dir, "inputs")
        staged_source, staged_source_project, staged_source_rules = _stage_cli_inputs(
            source_board, source_project, source_rules,
            os.path.join(input_root, "source"),
            expected=inputs_before,
            labels={"board": "source_board", "project": "source_project",
                    "rules": "source_rules"},
        )
        staged_candidate, staged_candidate_project, staged_candidate_rules = _stage_cli_inputs(
            candidate_board, candidate_project, candidate_rules,
            os.path.join(input_root, "candidate"),
            expected=inputs_before,
            labels={"board": "candidate_board", "project": "candidate_project",
                    "rules": "candidate_rules"},
        )
        staged_inputs_after_copy = {
            path: sha256_file(path)
            for path in (staged_source, staged_candidate, staged_source_project,
                         staged_candidate_project, staged_source_rules,
                         staged_candidate_rules)
            if path
        }
        for index in range(max(1, int(config.runs))):
            source_out = os.path.join(reports_dir, f"source_cli_run{index + 1}.json")
            candidate_out = os.path.join(reports_dir, f"candidate_cli_run{index + 1}.json")
            source_reports.append(
                run_drc(cli, staged_source, source_out, timeout_s=command_budget(),
                        env=config.env, options=config.extra_options)
            )
            candidate_reports.append(
                run_drc(cli, staged_candidate, candidate_out,
                        timeout_s=command_budget(), env=config.env,
                        options=config.extra_options)
            )
            report_paths += [source_out, candidate_out]
        # Nothing may have moved: neither the inputs the verdict names nor the
        # isolated copies the CLI actually read, nor the binary or its provider.
        for label, path in (("source_board", source_board),
                            ("candidate_board", candidate_board),
                            ("source_project", source_project),
                            ("candidate_project", candidate_project),
                            ("source_rules", source_rules),
                            ("candidate_rules", candidate_rules),
                            ("cli", cli), ("provider", provider)):
            if not path or label not in inputs_before:
                continue
            current = sha256_file(path)
            if current != inputs_before[label]:
                raise RuntimeError(
                    f"CLI input {label} changed during read-only verification "
                    f"({path})"
                )
        if any(sha256_file(path) != digest
               for path, digest in staged_inputs_after_copy.items()):
            raise RuntimeError("isolated CLI input or sidecar changed during verification")
    except Exception as exc:  # noqa: BLE001 - an unusable CLI is not a pass
        return unavailable_verdict(
            cli_path=cli, required=config.required,
            reason=f"installed kicad-cli could not complete the check: "
                   f"{type(exc).__name__}: {exc}",
        )
    return verdict_from_reports(
        source_reports, candidate_reports, cli_path=cli, cli_version_text=version,
        source_board=source_board, candidate_board=candidate_board,
        source_rules=source_rules, candidate_rules=candidate_rules,
        source_project=source_project, candidate_project=candidate_project,
        cli_sha256=cli_hash, provider_sha256=provider_hash,
        report_paths=report_paths, required=config.required,
        supported_reporter_versions=tuple(config.supported_reporter_versions),
        required_severities=tuple(config.required_severities),
        expected_cli_sha256=config.expected_cli_sha256,
        expected_provider_sha256=config.expected_provider_sha256,
        provider_source=provider_source,
        input_hashes=inputs_before, hash_capture="before_staging",
        staged_inputs_match=True,
        terminal_partition_proof=terminal_partition_proof,
    )
