"""The captured reference DRC baseline, bound to the bytes and build it came from.

A saved-artifact gate replays a baseline another process captured. Replaying the
violations alone is not evidence: a substituted, more permissive baseline would
be replayed happily while the verdict names the reference board's hashes. The
baseline therefore travels as an *envelope* - the violation multiset plus the
board/project/rules hashes and the engine build it was measured with - and the
verifying process re-measures all of it before replaying anything.

Both halves live here so the producer (the runner's own capture, or the verifier's
``capture_baseline`` mode) and the consumer (``verify_generation``) cannot drift
apart.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping

from pcb_world.agent.drc_gate import ViolationSet, violations_evidence
from pcb_world.agent.scheduler import sha256_file

#: The identity policy a baseline was measured under. Bumping it invalidates
#: stored baselines on purpose; they have to be recaptured.
POLICY = "collision-aware-inventory-v1"


def file_hash(path: str | os.PathLike | None) -> str | None:
    """Hash a file the envelope names; ``None`` when it is absent."""
    if path is None:
        return None
    text = str(path)
    return sha256_file(text) if os.path.isfile(text) else None


def engine_provenance() -> tuple[dict | None, str | None]:
    """This process's router build, or ``(None, why)`` rather than an exception.

    The provenance guard hashes the engine C++ against the loaded module's stamp;
    a mismatch means the build does not match the sources, which is exactly the
    "stale engine" the envelope has to be able to name. A caller that cannot
    identify its build gets a ``None`` provenance and a reason, and the verifying
    side refuses to replay a baseline without one.
    """
    try:
        from pcb_world.engine import ensure_router_provenance, router_build_dir
        from pcb_world.engine.provenance import STAMP_NAME, lib_dir_of

        ensure_router_provenance()
        build_dir = str(router_build_dir())
        lib_dir = Path(lib_dir_of(build_dir))
        modules = sorted(lib_dir.glob("kicad_rl_router*"))
        stamp = lib_dir / STAMP_NAME
        version = lib_dir / "ENGINE_VERSION"
        return {
            "build_dir": build_dir,
            "module_dir": str(lib_dir),
            "cpp_hash": stamp.read_text(encoding="utf-8").strip()
                         if stamp.is_file() else None,
            "version": version.read_text(encoding="utf-8").strip()
                       if version.is_file() else None,
            "module_sha256": file_hash(modules[0]) if modules else None,
            "provenance_checked": True,
        }, None
    except Exception as exc:  # noqa: BLE001 - an unidentifiable build is reported, not raised
        return None, f"{type(exc).__name__}: {exc}"


def capture_envelope(
    violations: ViolationSet | Mapping[str, Any], *,
    board_path: str | os.PathLike | None,
    project_path: str | os.PathLike | None,
    rules_path: str | os.PathLike | None,
    rules_evidence: Mapping[str, Any] | None = None,
    provenance: Mapping[str, Any] | None = None,
    provenance_problem: str | None = None,
    policy: str = POLICY,
) -> dict[str, Any]:
    """The self-describing baseline a verifier will re-check before replaying."""
    baseline = (violations_evidence(violations)
                if isinstance(violations, ViolationSet) else dict(violations))
    if provenance is None and provenance_problem is None:
        provenance, provenance_problem = engine_provenance()
    return {
        "ok": True,
        "mode": "capture_baseline",
        "policy": policy,
        "board_path": str(board_path) if board_path else None,
        "project_path": str(project_path) if project_path else None,
        "rules_path": str(rules_path) if rules_path else None,
        "board_sha256": file_hash(board_path),
        "project_sha256": file_hash(project_path),
        "rules_sha256": file_hash(rules_path),
        "rules": dict(rules_evidence or {}),
        "engine": dict(provenance) if provenance else None,
        "engine_problem": provenance_problem,
        "baseline": baseline,
    }


def envelope_problems(
    envelope: Any, *,
    policy: str = POLICY,
    board_sha256: str | None,
    project_sha256: str | None,
    rules_sha256: str | None,
    engine: Mapping[str, Any] | None,
) -> list[str]:
    """Why a captured envelope is not the baseline for *these* bytes and build.

    Empty means the envelope may be replayed: it was captured from the same board,
    project and rules bytes, under the same identity policy, using the same engine
    build that is about to replay it.
    """
    if not isinstance(envelope, Mapping):
        return ["no captured baseline envelope was supplied"]
    problems: list[str] = []
    if not isinstance(envelope.get("baseline"), Mapping):
        problems.append("the captured envelope carries no violation multiset")
    if str(envelope.get("policy") or "") != policy:
        problems.append(
            f"the captured baseline was measured under policy "
            f"{envelope.get('policy')!r}, not {policy!r}; recapture it"
        )
    for label, expected in (("board", board_sha256), ("project", project_sha256),
                            ("rules", rules_sha256)):
        actual = envelope.get(f"{label}_sha256")
        if str(actual or "") != str(expected or ""):
            problems.append(
                f"the captured baseline names a different {label} "
                f"({actual} vs {expected})"
            )
    captured_engine = envelope.get("engine")
    if not isinstance(captured_engine, Mapping):
        problems.append(
            "the captured baseline does not name the engine that measured it "
            f"({envelope.get('engine_problem') or 'no provenance recorded'})"
        )
    elif not isinstance(engine, Mapping):
        problems.append("this process cannot name the engine replaying the baseline")
    else:
        for field in ("cpp_hash", "module_sha256"):
            if not str(captured_engine.get(field) or "") or not str(
                engine.get(field) or ""
            ):
                problems.append(
                    f"the captured baseline and this process must both name the "
                    f"router {field} before a replay counts as proven"
                )
        for field in ("cpp_hash", "module_sha256", "version"):
            if str(captured_engine.get(field) or "") != str(engine.get(field) or ""):
                problems.append(
                    f"the captured baseline was measured by a different engine "
                    f"({field}: {captured_engine.get(field)} vs {engine.get(field)})"
                )
        if not captured_engine.get("provenance_checked"):
            problems.append("the captured baseline's engine was never provenance-checked")
    return problems
