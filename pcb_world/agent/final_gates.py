"""Production adapters for :meth:`ExperimentalArtifactStore.run_final_validation`.

The three acceptances that let an experimental generation become the accepted
one are each produced by their own owner, never by the caller:

* **native** - ``tools/reliability/verify_saved_artifact.py`` in an owned child
  process reopens the saved candidate, replays the captured reference baseline
  under the same collision-aware identity policy the local transactions use, and
  reports its own ok flag plus every hash it read;
* **terminal** - the same verifier in ``terminal_partition`` mode opens the frozen
  reference *and* the candidate in one fresh process, reads both partitions from
  the native connectivity, and compares them;
* **CLI** - :func:`pcb_world.agent.cli_gate.run_gate` judges a complete report
  from the vetted reporter, with the unconnected-endpoint exemption bound to the
  terminal proof the previous gate just produced.

Binding order matters: the CLI gate runs after the terminal gate because its
exemption is only valid against a proof for *this* candidate and reference. The
adapters take the binding from the store, re-derive the hashes they were asked
to validate from the files on disk in the child process, and refuse when the two
disagree - so evidence captured for one generation cannot be replayed for
another.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from pcb_world.agent.cli_gate import CliGateConfig, run_gate


@dataclass(frozen=True)
class FinalGateConfig:
    """How the production adapters run their child processes."""

    repo_root: str = field(
        default_factory=lambda: str(Path(__file__).resolve().parents[2])
    )
    python: str | None = None
    #: Ceiling for one child gate. The caller decides the real budget; a zero
    #: budget refuses rather than running without a bound.
    timeout_s: float = 3600.0
    work_dir: str | None = None
    #: Remaining run budget. When set, each child gets ``min(timeout_s, budget)``
    #: rather than the full per-child ceiling, so three gates cannot outlive the
    #: run that asked for them.
    remaining_budget_s: Callable[[], float] | None = None
    #: Complete-CLI gate configuration; ``None`` means native-only acceptance.
    cli: CliGateConfig | None = None
    #: Injectable process runner ``(command, timeout_s) -> CompletedProcess``.
    process_runner: Callable[..., subprocess.CompletedProcess] | None = None


class GateUnavailableError(RuntimeError):
    """A gate could not be run at all (no budget, no child, no output)."""


def _default_process_runner(command, *, timeout_s: float):
    from pcb_world.agent.runner import runner_run_owned_process

    return runner_run_owned_process(command, timeout_s=timeout_s)


class NativeGateChild:
    """Owned child process running one mode of the saved-artifact verifier."""

    def __init__(self, config: FinalGateConfig) -> None:
        self.config = config
        self.python = config.python or os.sys.executable
        self.script = str(
            Path(config.repo_root) / "tools" / "reliability" / "verify_saved_artifact.py"
        )
        self.process_runner = config.process_runner or _default_process_runner
        self.last_command: tuple[str, ...] = ()

    def __call__(self, request: Mapping[str, Any]) -> dict[str, Any]:
        timeout_s = float(self.config.timeout_s)
        if self.config.remaining_budget_s is not None:
            try:
                remaining = float(self.config.remaining_budget_s())
            except Exception as exc:  # noqa: BLE001 - an unreadable budget is not a pass
                raise GateUnavailableError(
                    f"the run budget could not be read: {type(exc).__name__}: {exc}"
                ) from None
            timeout_s = min(timeout_s, remaining)
        if timeout_s <= 0:
            raise GateUnavailableError("the native gate has no remaining time budget")
        if not os.path.isfile(self.script):
            raise GateUnavailableError(f"saved-artifact verifier is missing: {self.script}")
        work_dir = self.config.work_dir
        if work_dir:
            os.makedirs(work_dir, exist_ok=True)
        fd, request_path = tempfile.mkstemp(
            prefix="pcbworld-gate-", suffix=".json", dir=work_dir,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({**dict(request), "call_timeout_s": timeout_s}, handle,
                          sort_keys=True)
            command = [self.python, self.script, request_path]
            self.last_command = tuple(command)
            try:
                result = self.process_runner(command, timeout_s=timeout_s)
            except subprocess.TimeoutExpired as exc:
                raise GateUnavailableError(f"native gate timed out: {exc}") from None
        finally:
            try:
                os.unlink(request_path)
            except OSError:
                pass
        lines = [line for line in (result.stdout or "").strip().splitlines() if line.strip()]
        payload: dict[str, Any] | None = None
        if lines:
            try:
                decoded = json.loads(lines[-1])
                if isinstance(decoded, dict):
                    payload = decoded
            except json.JSONDecodeError:
                payload = None
        if payload is None:
            raise GateUnavailableError(
                f"native gate returned no machine-readable verdict "
                f"(exit {result.returncode}): {(result.stderr or '')[-400:]}"
            )
        payload["child_command"] = list(command)
        payload["child_returncode"] = int(result.returncode)
        if result.returncode != 0:
            payload["ok"] = False
            payload.setdefault("error", "native gate exited non-zero")
        return payload


def capture_reference_baseline(
    child: "NativeGateChild", *, board_path: str, project_path: str | None = None,
    rules_path: str | None = None,
) -> dict[str, Any]:
    """Capture a reference baseline envelope in an owned child process.

    The envelope names the board/project/rules bytes and the engine build that
    measured it, so the verifier can refuse a substituted or stale baseline before
    replaying anything.
    """
    envelope = child({
        "mode": "capture_baseline",
        "board_path": str(board_path),
        "project_path": str(project_path) if project_path else None,
        "rules_path": str(rules_path) if rules_path else None,
    })
    if not envelope.get("ok") or not isinstance(envelope.get("baseline"), Mapping):
        raise GateUnavailableError(
            f"reference baseline capture did not produce a bound envelope: "
            f"{envelope.get('error') or envelope.get('problems') or envelope}"
        )
    return envelope


class NativeGenerationGate:
    """Fresh native reopen + baseline replay for one staged generation."""

    name = "native"

    def __init__(self, generation_dir: str,
                 reference_paths: Mapping[str, str | None], child: NativeGateChild,
                 *, geometry_digest: str | None, expected_progress: Mapping[str, Any] | None,
                 baseline: Mapping[str, Any] | None,
                 binding_hashes: Callable[[], Mapping[str, Any]] | None = None) -> None:
        self.generation_dir = str(generation_dir)
        self.reference_paths = dict(reference_paths)
        self.child = child
        self.geometry_digest = geometry_digest
        self.expected_progress = expected_progress
        self.baseline = baseline
        #: Re-derives the current candidate+reference hashes after the child ran, so
        #: evidence cannot be produced for bytes that changed mid-gate.
        self.binding_hashes = binding_hashes
        self.last_evidence: dict[str, Any] | None = None

    def __call__(self, binding: Mapping[str, Any]) -> dict[str, Any]:
        if not self.geometry_digest or self.expected_progress is None:
            raise GateUnavailableError(
                "the staging manifest does not record the candidate geometry digest "
                "and progress; re-stage the generation so the native gate can compare "
                "the reopened board with what was saved"
            )
        if not isinstance(self.baseline, dict):
            raise GateUnavailableError(
                "no captured reference DRC baseline: recapture it under the current "
                "collision-aware policy before final validation"
            )
        request = {
            "mode": "verify_generation",
            "generation_dir": self.generation_dir,
            "geometry_digest": self.geometry_digest,
            "expected_progress": dict(self.expected_progress),
            "baseline": dict(self.baseline),
            **self.reference_paths,
            **{f"expected_{key}": value for key, value in binding.items()},
        }
        evidence = self.child(request)
        self._check_no_drift(binding)
        self.last_evidence = evidence
        return evidence

    def _check_no_drift(self, binding: Mapping[str, Any]) -> None:
        """Refuse when an input changed while the child was measuring it."""
        _check_binding_still_holds(self.generation_dir, self.reference_paths,
                                   _expected_binding(self.binding_hashes, binding))


class TerminalPartitionGate:
    """Fresh native two-board terminal-partition comparison."""

    name = "terminals"

    def __init__(self, generation_dir: str, reference_paths: Mapping[str, str | None],
                 child: NativeGateChild,
                 binding_hashes: Callable[[], Mapping[str, Any]] | None = None) -> None:
        self.generation_dir = str(generation_dir)
        self.reference_paths = dict(reference_paths)
        self.child = child
        self.binding_hashes = binding_hashes
        self.last_evidence: dict[str, Any] | None = None

    def __call__(self, binding: Mapping[str, Any]) -> dict[str, Any]:
        request = {
            "mode": "terminal_partition",
            "generation_dir": self.generation_dir,
            **self.reference_paths,
            **{f"expected_{key}": value for key, value in binding.items()},
        }
        evidence = self.child(request)
        _check_binding_still_holds(self.generation_dir, self.reference_paths,
                                   _expected_binding(self.binding_hashes, binding))
        self.last_evidence = evidence
        return evidence


def _expected_binding(
    binding_hashes: Callable[[], Mapping[str, Any]] | None,
    binding: Mapping[str, Any],
) -> Mapping[str, Any]:
    """The hashes the gate was asked to validate, preferring a fresh derivation.

    A store-level re-derivation that *itself* fails (a tampered generation manifest,
    a moved frozen reference) is not swallowed: the direct file re-hash below still
    compares what was read with what was promised, so the mismatch is named as
    drift rather than surfacing as a store error in the middle of a gate.
    """
    if binding_hashes is None:
        return dict(binding)
    try:
        return binding_hashes()
    except Exception:  # noqa: BLE001 - reported as drift against the promised binding
        return dict(binding)


def _check_binding_still_holds(
    generation_dir: str, reference_paths: Mapping[str, str | None],
    expected: Mapping[str, Any],
) -> None:
    """Re-hash the files each gate just read and compare them with the binding.

    The store re-derives the binding again at promotion, so this is not the last
    line of defence - it is what stops a child's verdict from being *recorded*
    against hashes it never read.
    """
    from pcb_world.agent.scheduler import sha256_file

    generation = Path(generation_dir)
    if not expected:
        return
    measured = {
        "candidate_board_sha256": sha256_file(str(generation / "board.kicad_pcb")),
        "candidate_project_sha256": sha256_file(str(generation / "board.kicad_pro")),
        "candidate_rules_sha256": sha256_file(str(generation / "board.kicad_dru")),
        "reference_board_sha256": sha256_file(
            str(reference_paths.get("reference_board_path") or "")),
        "reference_project_sha256": sha256_file(
            str(reference_paths.get("reference_project_path") or "")),
        "reference_rules_sha256": sha256_file(
            str(reference_paths.get("reference_rules_path") or "")),
    }
    drift = [
        f"{field}: {measured[field]} != {expected.get(field)}"
        for field in measured
        if field in expected and str(measured[field] or "") != str(expected.get(field) or "")
    ]
    if drift:
        raise GateUnavailableError(
            "an input changed while the gate was running: " + "; ".join(drift)
        )


class CliFinalGate:
    """The complete-CLI gate, bound to the terminal proof of the same candidate."""

    name = "cli"

    def __init__(self, generation_dir: str, reference_paths: Mapping[str, str | None],
                 terminal_gate: TerminalPartitionGate, config: FinalGateConfig,
                 binding_hashes: Callable[[], Mapping[str, Any]] | None = None) -> None:
        self.generation_dir = str(generation_dir)
        self.reference_paths = dict(reference_paths)
        self.terminal_gate = terminal_gate
        self.config = config
        self.binding_hashes = binding_hashes

    def __call__(self, binding: Mapping[str, Any]) -> dict[str, Any]:
        if self.config.cli is None:
            from pcb_world.agent.cli_gate import not_configured_verdict

            return not_configured_verdict().to_evidence()
        generation = Path(self.generation_dir)
        board = generation / "board.kicad_pcb"
        project = generation / "board.kicad_pro"
        rules = generation / "board.kicad_dru"
        if not board.is_file():
            raise GateUnavailableError(f"the generation has no saved board: {board}")
        work_dir = self.config.work_dir or str(generation / "final_gate_cli_reports")
        proof = self.terminal_gate.last_evidence
        verdict = run_gate(
            self.config.cli,
            source_board=str(self.reference_paths["reference_board_path"]),
            candidate_board=str(board),
            source_rules=self.reference_paths.get("reference_rules_path"),
            candidate_rules=str(rules) if rules.is_file() else None,
            source_project=self.reference_paths.get("reference_project_path"),
            candidate_project=str(project) if project.is_file() else None,
            work_dir=work_dir,
            terminal_partition_proof=proof,
        )
        evidence = verdict.to_evidence()
        evidence["terminal_proof_bound"] = bool(
            isinstance(proof, Mapping) and proof.get("fresh_process") is True
        )
        # The CLI gate re-checks the inputs it hashed itself; this is the same
        # post-child binding check the native and terminal gates do.
        _check_binding_still_holds(
            self.generation_dir, self.reference_paths,
            self.binding_hashes() if self.binding_hashes else dict(binding),
        )
        return evidence


def production_final_gates(
    store, generation_dir: str, *, config: FinalGateConfig | None = None,
) -> dict[str, Callable[[Mapping[str, Any]], dict[str, Any]]]:
    """The three production adapters, ready for ``run_final_validation``.

    Returns ``{"native_gate": ..., "terminal_gate": ..., "cli_gate": ...}``. The
    generation must be the store's current staging generation and must carry the
    progress and geometry digest its staging recorded; the reference side is the
    store's own frozen copy, so all three gates judge the same bytes the store
    bound at creation.
    """
    config = config or FinalGateConfig()
    current = store.current()
    if current is None or os.path.realpath(current[0]) != os.path.realpath(generation_dir):
        raise GateUnavailableError(
            "final gates only run against the store's current staging generation"
        )
    _folder, manifest = current
    local = dict(manifest.get("local_evidence") or {})
    staged_layout = {
        "reference_board_path": str(store.reference_dir / "board.kicad_pcb"),
        "reference_project_path": (
            str(store.reference_dir / "board.kicad_pro")
            if os.path.isfile(store.reference_dir / "board.kicad_pro") else None
        ),
        "reference_rules_path": (
            str(store.reference_dir / "board.kicad_dru")
            if os.path.isfile(store.reference_dir / "board.kicad_dru") else None
        ),
    }
    baseline = local.get("source_drc_baseline")
    child = NativeGateChild(config)
    native = NativeGenerationGate(
        generation_dir, staged_layout, child,
        geometry_digest=local.get("geometry_digest"),
        expected_progress=local.get("progress"),
        baseline=baseline if isinstance(baseline, dict) else None,
        binding_hashes=lambda: store.generation_hashes(generation_dir),
    )
    terminals = TerminalPartitionGate(
        generation_dir, staged_layout, child,
        binding_hashes=lambda: store.generation_hashes(generation_dir),
    )
    cli = CliFinalGate(
        generation_dir, staged_layout, terminals, config,
        binding_hashes=lambda: store.generation_hashes(generation_dir),
    )
    return {
        "native_gate": native,
        "terminal_gate": terminals,
        "cli_gate": cli,
    }
