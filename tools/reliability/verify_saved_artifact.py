#!/usr/bin/env python3
"""Fresh-process native gates for one staged board generation.

Three modes, all read-only, each run in an owned child process so a native
answer never comes from the caller's own engine:

``capture_baseline``
    Open the frozen reference board, run its DRC, and return the complete
    violation multiset as evidence (keys *and* conditions), so a later process
    can replay the exact identities it was measured with.

``verify_generation`` (default)
    Reopen the saved candidate, prove the rule context, replay the captured
    baseline, and refuse if the fresh DRC added a relevant *identity* under the
    same collision-aware policy the local transactions use. Geometry digest and
    connectivity progress are checked against the saved candidate too.

``terminal_partition``
    Open the frozen reference *and* the candidate in this one fresh process,
    capture both terminal partitions from the native connectivity, and compare
    them: every terminal pair joined on the reference must still be joined. The
    evidence names both boards' hashes and is what the CLI gate's
    unconnected-endpoint exemption is bound to.

A missing baseline is an explicit recapture request, never a set fallback: set
comparison silently turns one more violation under an existing key into "no
change", which is the defect this gate exists to catch.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Mapping

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pcb_world.agent.drc_gate import (  # noqa: E402
    EvidenceSchemaError,
    diff_sets,
    take_violations,
    violations_from_evidence,
)
from pcb_world.agent.observations import progress_summary  # noqa: E402
from pcb_world.agent.reference_baseline import (  # noqa: E402
    POLICY,
    capture_envelope,
    engine_provenance,
    envelope_problems,
)
from pcb_world.agent.rules import engine_rule_status  # noqa: E402
from pcb_world.agent.scheduler import sha256_file  # noqa: E402
from pcb_world.agent.session import AgentSession  # noqa: E402
from pcb_world.agent.terminals import (  # noqa: E402
    capture_terminals,
    compare_terminal_partitions,
)
from pcb_world.engine import KiCadEngine  # noqa: E402
from pcb_world.engine.kicad_engine import allow_router_coexistence  # noqa: E402

class GateRefusal(RuntimeError):
    """A measured refusal that already carries its full evidence payload."""


def _replayable_baseline(envelope) -> object:
    """The baseline multiset, or a refusal naming why it cannot be replayed.

    The stored payload is schema-checked by the module that wrote it, and a
    failure is an explicit refusal: an incomplete payload (a deleted ambiguity
    list, a truncated row, a row whose fields contradict its key) must never be
    replayed as if the missing parts were empty, because that is how a physical
    substitution reads as "nothing changed".
    """
    if not isinstance(envelope, Mapping):
        raise RuntimeError(
            "the captured baseline is a "
            f"{type(envelope).__name__}, not the envelope the verifier wrote"
        )
    payload = envelope.get("baseline")
    try:
        return violations_from_evidence(payload)
    except EvidenceSchemaError as exc:
        raise RuntimeError(
            "the captured baseline cannot be replayed: " + "; ".join(exc.reasons)
        ) from exc
    except Exception as exc:  # noqa: BLE001 - any unreadable payload is a refusal
        # A shape this reader cannot interpret is a refusal, not a crash that
        # some caller might treat as "no baseline" and continue without one.
        raise RuntimeError(
            "the captured baseline cannot be replayed "
            f"({type(exc).__name__}: {exc})"
        ) from exc


def _sha256(path) -> str | None:
    """Hash a file this gate is about to open; ``None`` when it is absent."""
    if path is None:
        return None
    text = str(path)
    return sha256_file(text) if os.path.isfile(text) else None


def _sidecar(board: str, explicit, suffix: str) -> str | None:
    candidate = explicit or str(Path(board).with_suffix(suffix))
    return candidate if candidate and os.path.isfile(str(candidate)) else None


def _engine_provenance() -> dict:
    """Which router build answered, proved before anything is opened.

    The C++ content hash is checked against this tree by the engine's own guard,
    so evidence cannot attribute a measurement to sources that did not produce
    the module.
    """
    provenance, problem = engine_provenance()
    if provenance is None:
        raise RuntimeError(f"this process cannot identify its router build: {problem}")
    return provenance


def _hash_binding(request) -> dict[str, str | None]:
    """Candidate + frozen-reference hashes, measured from the files on disk."""
    generation = Path(request["generation_dir"]) if request.get("generation_dir") else None
    candidate = {
        "candidate_board_sha256":
            _sha256(generation / "board.kicad_pcb") if generation else None,
        "candidate_project_sha256":
            _sha256(generation / "board.kicad_pro") if generation else None,
        "candidate_rules_sha256":
            _sha256(generation / "board.kicad_dru") if generation else None,
    }
    reference = {
        "reference_board_sha256": _sha256(request.get("reference_board_path")),
        "reference_project_sha256": _sha256(request.get("reference_project_path")),
        "reference_rules_sha256": _sha256(request.get("reference_rules_path")),
    }
    if request.get("candidate_board_path"):
        candidate["candidate_board_sha256"] = _sha256(request["candidate_board_path"])
    return {**candidate, **reference}


def _check_expected(request, binding: dict) -> None:
    """Refuse when the bytes on disk are not the bytes the caller named."""
    for field, digest in binding.items():
        expected = request.get(f"expected_{field}")
        if expected and digest != expected:
            raise RuntimeError(
                f"{field} does not match the expected hash ({digest} vs {expected})"
            )


def _prove_rules(engine, rules_path: str | None) -> dict:
    """The engine really loaded the rule file this gate names."""
    status = engine_rule_status(engine)
    evidence = {
        "rules_path": str(rules_path) if rules_path else "",
        "rules_sha256": _sha256(rules_path),
        "routing_rules_path": str(status.get("routing_rules_path") or ""),
        "routing_rules_loaded_from_file": bool(status.get("routing_rules_loaded_from_file")),
        "last_drc_rules_load_error": str(status.get("last_drc_rules_load_error") or ""),
    }
    if evidence["last_drc_rules_load_error"]:
        raise RuntimeError(
            "the engine reported a rule-load failure: "
            f"{evidence['last_drc_rules_load_error']}"
        )
    if rules_path:
        loaded = evidence["routing_rules_path"]
        if (not evidence["routing_rules_loaded_from_file"] or not loaded
                or not os.path.isfile(loaded)
                or Path(loaded).read_bytes() != Path(rules_path).read_bytes()):
            raise RuntimeError("fresh engine did not prove the generation rule file")
    return evidence


def _counts(violations) -> dict:
    return {
        "total": int(violations.total),
        "relevant": int(violations.relevant),
        "connectivity": int(violations.connectivity),
        "distinct_identities": len(violations.counts),
        "keys_with_more_than_one_violation": violations.ambiguous_keys,
        "keys_without_inventory_proof": violations.unproven_keys,
    }


def mode_capture_baseline(request: dict) -> dict:
    """Capture the frozen reference's DRC multiset, with provenance."""
    board = request["board_path"]
    project = _sidecar(board, request.get("project_path"), ".kicad_pro")
    rules = _sidecar(board, request.get("rules_path"), ".kicad_dru")
    engine = KiCadEngine(
        str(board), project_path=project, call_timeout_s=request.get("call_timeout_s"),
    )
    try:
        rules_evidence = _prove_rules(engine, rules)
        drc = take_violations(engine, rules or "")
        # The envelope, not just the violations: a verifier re-checks the bytes and
        # the build it names before it replays anything.
        return capture_envelope(
            drc, board_path=board, project_path=project, rules_path=rules,
            rules_evidence=rules_evidence, provenance=_engine_provenance(),
        )
    finally:
        engine.close()


def mode_verify_generation(request: dict) -> dict:
    """Reopen the saved candidate and compare it to the captured baseline."""
    generation = Path(request["generation_dir"])
    board = generation / "board.kicad_pcb"
    project = generation / "board.kicad_pro"
    rules = generation / "board.kicad_dru"
    if not isinstance(request.get("baseline"), dict):
        raise RuntimeError(
            "no captured baseline evidence in this request; the source DRC "
            "baseline must be recaptured under the current policy "
            f"({POLICY}) - a key-set fallback is not a comparison"
        )
    binding = _hash_binding(request)
    _check_expected(request, binding)
    # The baseline is only this reference's baseline if it says so itself: the
    # envelope's board/project/rules hashes and engine build are re-measured here,
    # before a single violation is replayed. A substituted baseline, a baseline
    # captured from another board, or one measured by a stale engine is refused.
    envelope = request["baseline"]
    provenance = _engine_provenance()
    baseline_problems = envelope_problems(
        envelope, policy=POLICY,
        board_sha256=binding["reference_board_sha256"],
        project_sha256=binding["reference_project_sha256"],
        rules_sha256=binding["reference_rules_sha256"],
        engine=provenance,
    )
    if baseline_problems:
        raise RuntimeError(
            "the captured baseline is not this reference's baseline: "
            + "; ".join(baseline_problems)
        )
    # Replay the multiset before the engine is opened: an incomplete payload is a
    # refusal, not a comparison against whatever the missing fields defaulted to.
    baseline = _replayable_baseline(envelope)
    engine = KiCadEngine(
        str(board), project_path=str(project) if project.exists() else None,
        call_timeout_s=request.get("call_timeout_s"),
    )
    try:
        rules_evidence = _prove_rules(engine, str(rules) if rules.exists() else None)
        session = AgentSession(engine, board_path=str(board))
        progress = progress_summary(session)
        geometry = session.board_digest()
        if not geometry or geometry != request.get("geometry_digest"):
            raise RuntimeError("reopened board geometry digest differs from saved candidate")
        drc = take_violations(engine, str(rules) if rules.exists() else "")
        delta = diff_sets(baseline, drc)
        problems: list[str] = []
        if not delta.acceptable:
            problems.append(
                f"fresh native DRC found {len(delta.added_relevant)} added relevant "
                f"identity groups ({delta.added_occurrences} occurrence(s))"
            )
        if progress != request.get("expected_progress"):
            problems.append("reopened connectivity/geometry counts differ from the candidate")
        evidence = {
            "ok": not problems,
            "mode": "verify_generation",
            "policy": POLICY,
            "problems": problems,
            "reopened": True,
            "generation_dir": str(generation),
            "geometry_digest": geometry,
            "progress": progress,
            "rules": rules_evidence,
            "engine": _engine_provenance(),
            "binding": binding,
            "baseline_policy": str(envelope.get("policy") or ""),
            "baseline_binding": {
                "board_sha256": envelope.get("board_sha256"),
                "project_sha256": envelope.get("project_sha256"),
                "rules_sha256": envelope.get("rules_sha256"),
                "engine": dict(envelope.get("engine") or {}),
            },
            "baseline": _counts(baseline),
            "candidate": _counts(drc),
            "drc_delta": delta.to_evidence(),
            **binding,
        }
        if problems:
            raise GateRefusal(json.dumps(evidence, sort_keys=True))
        return evidence
    finally:
        engine.close()


def mode_terminal_partition(request: dict) -> dict:
    """Compare the frozen reference's terminal partition with the candidate's."""
    generation = Path(request["generation_dir"])
    reference_board = request["reference_board_path"]
    reference_project = _sidecar(
        reference_board, request.get("reference_project_path"), ".kicad_pro",
    )
    reference_rules = _sidecar(
        reference_board, request.get("reference_rules_path"), ".kicad_dru",
    )
    candidate_board = generation / "board.kicad_pcb"
    candidate_project = generation / "board.kicad_pro"
    candidate_rules = generation / "board.kicad_dru"
    binding = _hash_binding(request)
    _check_expected(request, binding)
    timeout_s = request.get("call_timeout_s")
    with allow_router_coexistence(
        "the saved-artifact terminal gate reads both boards in one owned process"
    ):
        reference_engine = KiCadEngine(
            str(reference_board), project_path=reference_project, call_timeout_s=timeout_s,
        )
        try:
            reference_rules_evidence = _prove_rules(reference_engine, reference_rules)
            reference_partition = capture_terminals(reference_engine)
        finally:
            reference_engine.close()
        candidate_engine = KiCadEngine(
            str(candidate_board),
            project_path=str(candidate_project) if candidate_project.exists() else None,
            call_timeout_s=timeout_s,
        )
        try:
            candidate_rules_evidence = _prove_rules(
                candidate_engine,
                str(candidate_rules) if candidate_rules.exists() else None,
            )
            candidate_partition = capture_terminals(candidate_engine)
        finally:
            candidate_engine.close()
    delta = compare_terminal_partitions(reference_partition, candidate_partition)
    complete = bool(reference_partition.complete and candidate_partition.complete)
    include_partitions = bool(request.get("include_partitions"))

    def partition_evidence(partition) -> dict:
        if include_partitions:
            return partition.to_evidence()
        return {
            "nets": len(partition.net_terminals),
            "terminals": len(partition.cluster_of),
            "clusters": len(set(partition.cluster_of.values())),
            "complete": partition.complete,
            "reasons": list(partition.reasons),
        }

    return {
        **delta.to_evidence(),
        "ok": bool(delta.ok and complete),
        "complete": complete,
        "fresh_process": True,
        "mode": "terminal_partition",
        "policy": POLICY,
        "generation_dir": str(generation),
        "reference": {
            "board_sha256": binding["reference_board_sha256"],
            "rules": reference_rules_evidence,
            "partition": partition_evidence(reference_partition),
        },
        "candidate": {
            "board_sha256": binding["candidate_board_sha256"],
            "rules": candidate_rules_evidence,
            "partition": partition_evidence(candidate_partition),
        },
        "engine": _engine_provenance(),
        "binding": binding,
        "original_reference_sha256": binding["reference_board_sha256"],
        "reference_project_sha256": binding["reference_project_sha256"],
        **binding,
    }


def main(path: str) -> int:
    request = json.loads(Path(path).read_text(encoding="utf-8"))
    mode = str(request.get("mode") or "verify_generation")
    handlers = {
        "capture_baseline": mode_capture_baseline,
        "verify_generation": mode_verify_generation,
        "terminal_partition": mode_terminal_partition,
    }
    if mode not in handlers:
        raise ValueError(f"unknown gate mode {mode!r}")
    print(json.dumps(handlers[mode](request), sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1]))
    except GateRefusal as refusal:
        # A refusal is a measurement: the payload carries the evidence, and the
        # exit status says the gate did not pass, so a caller that only reads the
        # status still fails closed.
        print(str(refusal))
        raise SystemExit(1)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}))
        raise SystemExit(1)
