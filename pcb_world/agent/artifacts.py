"""Immutable on-disk generations and one atomic accepted-artifact pointer."""

from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Mapping

from pcb_world.agent.scheduler import sha256_file


class ArtifactStore:
    """Store complete board generations; the pointer is the commit record.

    A crash while writing a generation leaves the previous pointer untouched.
    A crash after pointer replacement but before run_state save is reconciled by
    reading this pointer on resume.
    """

    POINTER = "accepted_artifact.json"

    def __init__(self, run_dir: str) -> None:
        self.root = Path(run_dir) / "artifacts"
        self.root.mkdir(parents=True, exist_ok=True)
        self.last_checkpoint: dict[str, Any] | None = None
        self._pointer_bytes: bytes | None = None

    @staticmethod
    def _fsync_dir(path: Path) -> None:
        """Persist directory entries where the host supports directory fsync."""
        try:
            fd = os.open(path, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(fd)
        except OSError:
            pass
        finally:
            os.close(fd)

    def stage(self, board: str, project: str | None, rules: str | None,
              manifest: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
        """Copy exact candidate inputs into a new immutable generation directory."""
        board_hash = sha256_file(board)
        if not board_hash or os.path.getsize(board) <= 0:
            raise RuntimeError("candidate board is missing or empty")
        generation = f"{time.time_ns()}-{board_hash[:12]}-{uuid.uuid4().hex[:8]}"
        folder = self.root / generation
        folder.mkdir()
        files: dict[str, str] = {}
        sources = (("board.kicad_pcb", board), ("board.kicad_pro", project),
                   ("board.kicad_dru", rules))
        try:
            for name, source in sources:
                if source and os.path.isfile(source):
                    shutil.copyfile(source, folder / name)
                    with (folder / name).open("rb") as handle:
                        os.fsync(handle.fileno())
                    files[name] = sha256_file(str(folder / name)) or ""
            complete = {**dict(manifest), "generation": generation,
                        "files": files, "board_sha256": files["board.kicad_pcb"]}
            manifest_path = folder / "manifest.json"
            manifest_path.write_text(json.dumps(complete, indent=2, sort_keys=True),
                                     encoding="utf-8")
            # Flush file data before making the generation addressable.
            with manifest_path.open("rb") as handle:
                os.fsync(handle.fileno())
            self._fsync_dir(folder)
            self._fsync_dir(self.root)
            return str(folder), complete
        except BaseException:
            shutil.rmtree(folder, ignore_errors=True)
            raise

    def promote(self, folder: str, manifest: Mapping[str, Any],
                checkpoint: Mapping[str, Any] | None = None) -> None:
        """Atomically change the only resume-authoritative pointer."""
        payload = {"generation": os.path.basename(folder),
                   "manifest_sha256": sha256_file(os.path.join(folder, "manifest.json")),
                   "manifest": dict(manifest),
                   "checkpoint": dict(checkpoint) if checkpoint is not None else None}
        target = self.root / self.POINTER
        temp = self.root / f".{self.POINTER}.{uuid.uuid4().hex}.tmp"
        with temp.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, target)
        self._fsync_dir(self.root)
        self._pointer_bytes = target.read_bytes()

    def snapshot_pointer(self) -> bytes | None:
        path = self.root / self.POINTER
        self._pointer_bytes = path.read_bytes() if path.is_file() else None
        return self._pointer_bytes

    def restore_pointer(self, snapshot: bytes | None) -> None:
        """Restore the last accepted commit record after a later gate rejects."""
        target = self.root / self.POINTER
        if snapshot is None:
            try:
                target.unlink()
            except FileNotFoundError:
                pass
            self._pointer_bytes = None
            return
        temp = self.root / f".{self.POINTER}.{uuid.uuid4().hex}.restore"
        with temp.open("wb") as handle:
            handle.write(snapshot)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, target)
        self._fsync_dir(self.root)
        self._pointer_bytes = snapshot

    def current(self) -> tuple[str, dict[str, Any]] | None:
        pointer = self.root / self.POINTER
        if not pointer.is_file():
            return None
        payload = json.loads(pointer.read_text(encoding="utf-8"))
        self.last_checkpoint = payload.get("checkpoint")
        generation = str(payload["generation"])
        if Path(generation).name != generation or generation in {".", ".."}:
            raise ValueError("accepted artifact pointer contains an unsafe generation path")
        folder = self.root / generation
        manifest_path = folder / "manifest.json"
        if sha256_file(str(manifest_path)) != payload.get("manifest_sha256"):
            raise ValueError("accepted artifact pointer manifest hash mismatch")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest != payload.get("manifest"):
            raise ValueError("accepted artifact pointer and generation manifest disagree")
        for name, expected in manifest.get("files", {}).items():
            actual = sha256_file(str(folder / name))
            if actual != expected:
                raise ValueError(f"accepted artifact file hash mismatch: {name}")
        return str(folder), manifest


class ExperimentalArtifactStore:
    """Durable repair checkpoints isolated from the accepted-artifact pointer.

    The store binds once to an immutable original acceptance reference. Local
    routing checkpoints are always labelled staging-only; making one accepted
    requires final native, complete-CLI, and terminal-partition evidence naming
    that exact frozen reference **and the candidate it was produced for**.

    Binding both sides is what stops a stale pass: evidence produced for
    candidate A names A's board/project/rules hashes, so it cannot promote the
    current candidate B (see :meth:`promote_after_final_gates`).
    """

    POINTER = "staging_checkpoint.json"
    #: Every field a final gate's evidence must carry: the exact candidate it
    #: validated and the exact frozen reference it validated against.
    BINDING_FIELDS = (
        "candidate_board_sha256", "candidate_project_sha256", "candidate_rules_sha256",
        "reference_board_sha256", "reference_project_sha256", "reference_rules_sha256",
    )

    def __init__(self, run_dir: str, reference_board: str,
                 reference_project: str | None = None,
                 reference_rules: str | None = None) -> None:
        self.root = Path(run_dir) / "experimental_staging"
        self.root.mkdir(parents=True, exist_ok=True)
        self.reference_dir = self.root / "reference"
        self.generations = self.root / "generations"
        self.generations.mkdir(exist_ok=True)
        self.last_checkpoint: dict[str, Any] | None = None
        self._pointer_bytes: bytes | None = None
        self.reference_files: dict[str, str] = {}
        self.reference_hash = self._bind_reference(
            reference_board, reference_project, reference_rules
        )

    @staticmethod
    def _hash(path: str | Path | None) -> str | None:
        return sha256_file(str(path)) if path and os.path.isfile(path) else None

    @staticmethod
    def _fsync_dir(path: Path) -> None:
        """Persist directory entries where the host supports directory fsync."""
        try:
            fd = os.open(path, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(fd)
        except OSError:
            pass
        finally:
            os.close(fd)

    def _bind_reference(self, board: str, project: str | None,
                        rules: str | None) -> str:
        project = project or (str(Path(board).with_suffix(".kicad_pro"))
                              if Path(board).with_suffix(".kicad_pro").is_file() else None)
        rules = rules or (str(Path(board).with_suffix(".kicad_dru"))
                          if Path(board).with_suffix(".kicad_dru").is_file() else None)
        sources = {
            "board.kicad_pcb": board,
            "board.kicad_pro": project,
            "board.kicad_dru": rules,
        }
        if not os.path.isfile(board):
            raise ValueError("experimental staging requires an existing original board reference")
        expected = {name: self._hash(path) for name, path in sources.items() if path}
        manifest_path = self.reference_dir / "manifest.json"
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest.get("files") != expected:
                raise ValueError("experimental staging reference changed; refusing resume")
            for name, digest in expected.items():
                if self._hash(self.reference_dir / name) != digest:
                    raise ValueError(f"frozen original reference changed: {name}")
            self.reference_files = dict(manifest["files"])
            return str(manifest["reference_sha256"])
        self.reference_dir.mkdir(exist_ok=False)
        try:
            files: dict[str, str] = {}
            for name, source in sources.items():
                if source:
                    shutil.copyfile(source, self.reference_dir / name)
                    files[name] = self._hash(self.reference_dir / name) or ""
            ref_hash = files["board.kicad_pcb"]
            manifest_path.write_text(json.dumps({
                "label": "immutable_original_acceptance_reference",
                "reference_sha256": ref_hash,
                "files": files,
            }, sort_keys=True, indent=2), encoding="utf-8")
            self.reference_files = dict(files)
            return ref_hash
        except BaseException:
            shutil.rmtree(self.reference_dir, ignore_errors=True)
            raise

    def stage(self, board: str, project: str | None, rules: str | None,
              checkpoint: Mapping[str, Any] | Any,
              evidence: Mapping[str, Any] | None = None) -> tuple[str, dict[str, Any]]:
        """Save a new immutable candidate and atomically move the staging pointer.

        Durability matches the accepted store: copied bytes are fsynced, then the
        manifest, then the directory entries, and only then the pointer is
        replaced and fsynced. A crash at any point leaves either the previous
        pointer or a complete generation - never a pointer to a half-written one.

        ``checkpoint`` may be a mapping or a callable ``(folder, manifest) ->
        mapping``. The callable form exists so a checkpoint can name the
        generation it is being written with (its path and board hash) without a
        second staging pass: the pointer is still written exactly once, with
        state that already refers to this generation.
        """
        board_hash = self._hash(board)
        if not board_hash:
            raise ValueError("experimental candidate board is missing or empty")
        generation = f"{time.time_ns()}-{board_hash[:12]}-{uuid.uuid4().hex[:8]}"
        folder = self.generations / generation
        folder.mkdir()
        files: dict[str, str] = {}
        try:
            for name, source in (("board.kicad_pcb", board),
                                 ("board.kicad_pro", project),
                                 ("board.kicad_dru", rules)):
                if source and os.path.isfile(source):
                    shutil.copyfile(source, folder / name)
                    with (folder / name).open("rb") as handle:
                        os.fsync(handle.fileno())
                    files[name] = self._hash(folder / name) or ""
            manifest = {
                "generation": generation,
                "label": "experimental_staging_only",
                "accepted": False,
                "original_reference_sha256": self.reference_hash,
                "files": files,
                "board_sha256": files["board.kicad_pcb"],
                "local_evidence": dict(evidence or {}),
            }
            manifest_path = folder / "manifest.json"
            manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2),
                                     encoding="utf-8")
            with manifest_path.open("rb") as handle:
                os.fsync(handle.fileno())
            checkpoint_state = (checkpoint(str(folder), manifest)
                                if callable(checkpoint) else dict(checkpoint))
            pointer = {
                "generation": generation,
                "manifest_sha256": self._hash(manifest_path),
                "manifest": manifest,
                "checkpoint": checkpoint_state,
                "label": "experimental_staging_only",
                "accepted": False,
                "original_reference_sha256": self.reference_hash,
            }
            temp = self.root / f".{self.POINTER}.{uuid.uuid4().hex}.tmp"
            with temp.open("w", encoding="utf-8") as handle:
                json.dump(pointer, handle, sort_keys=True, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            self._fsync_dir(folder)
            self._fsync_dir(self.generations)
            os.replace(temp, self.root / self.POINTER)
            self._fsync_dir(self.root)
            self._pointer_bytes = (self.root / self.POINTER).read_bytes()
            return str(folder), manifest
        except BaseException:
            shutil.rmtree(folder, ignore_errors=True)
            raise

    def snapshot_pointer(self) -> bytes | None:
        path = self.root / self.POINTER
        self._pointer_bytes = path.read_bytes() if path.is_file() else None
        return self._pointer_bytes

    def restore_pointer(self, snapshot: bytes | None) -> None:
        target = self.root / self.POINTER
        if snapshot is None:
            target.unlink(missing_ok=True)
            self._fsync_dir(self.root)
            self._pointer_bytes = None
            return
        temp = self.root / f".{self.POINTER}.{uuid.uuid4().hex}.restore"
        with temp.open("wb") as handle:
            handle.write(snapshot)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, target)
        self._fsync_dir(self.root)
        self._pointer_bytes = snapshot

    def current(self) -> tuple[str, dict[str, Any]] | None:
        """Validate and return the durable staging generation and run checkpoint."""
        pointer_path = self.root / self.POINTER
        if not pointer_path.is_file():
            self.last_checkpoint = None
            return None
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
        if pointer.get("label") != "experimental_staging_only" or pointer.get("accepted") is not False:
            raise ValueError("experimental staging pointer has an invalid lifecycle label")
        if pointer.get("original_reference_sha256") != self.reference_hash:
            raise ValueError("experimental staging pointer names a different original reference")
        generation = str(pointer.get("generation", ""))
        if Path(generation).name != generation or generation in {"", ".", ".."}:
            raise ValueError("experimental staging pointer contains an unsafe generation")
        folder = self.generations / generation
        manifest_path = folder / "manifest.json"
        if self._hash(manifest_path) != pointer.get("manifest_sha256"):
            raise ValueError("experimental staging manifest hash mismatch")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest != pointer.get("manifest") or manifest.get("accepted") is not False:
            raise ValueError("experimental staging pointer and manifest disagree")
        if manifest.get("original_reference_sha256") != self.reference_hash:
            raise ValueError("experimental generation is not bound to the frozen reference")
        for name, digest in manifest.get("files", {}).items():
            if self._hash(folder / name) != digest:
                raise ValueError(f"experimental generation hash mismatch: {name}")
        self.last_checkpoint = dict(pointer.get("checkpoint") or {})
        self._pointer_bytes = pointer_path.read_bytes()
        return str(folder), manifest

    def promote_after_final_gates(
        self, accepted_store: ArtifactStore, generation_dir: str,
        final_evidence: Mapping[str, Any], checkpoint: Mapping[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        """Promote only after all three gates bind themselves to *this* candidate.

        ``final_evidence`` is what :meth:`run_final_validation` returns: the three
        gate verdicts plus the binding they were produced under. Both sides of
        that binding are re-derived here from the files on disk - the current
        generation's board/project/rules and the frozen reference - and every
        gate has to name all six hashes, so evidence captured for a different
        candidate (or a different reference) is refused rather than accepted
        because it once passed.
        """
        current = self.current()
        if current is None or os.path.realpath(current[0]) != os.path.realpath(generation_dir):
            raise ValueError("only the current durable staging generation can be promoted")
        expected = self.generation_hashes(generation_dir)
        binding = dict(final_evidence.get("binding") or {})
        if binding != expected:
            raise ValueError(
                "final-gate evidence is not bound to the current candidate: "
                f"evidence binding {binding} != current {expected}"
            )
        problems = self.evidence_problems(final_evidence, expected)
        if problems:
            raise ValueError("final-gate evidence refused: " + "; ".join(problems))
        board = os.path.join(generation_dir, "board.kicad_pcb")
        project = os.path.join(generation_dir, "board.kicad_pro")
        rules = os.path.join(generation_dir, "board.kicad_dru")
        folder, manifest = accepted_store.stage(
            board, project if os.path.isfile(project) else None,
            rules if os.path.isfile(rules) else None,
            {"experimental_source_generation": os.path.basename(generation_dir),
             "original_reference_sha256": self.reference_hash,
             "final_validation_binding": expected,
             "native_final_gate": dict(final_evidence.get("native") or {}),
             "complete_cli_final_gate": dict(final_evidence.get("cli") or {}),
             "terminal_final_gate": dict(final_evidence.get("terminals") or {}),
             "accepted": True},
        )
        accepted_store.promote(folder, manifest, checkpoint=checkpoint)
        return folder, manifest

    def generation_hashes(self, generation_dir: str) -> dict[str, str | None]:
        """The exact candidate + frozen-reference hashes a final gate must name.

        The candidate side is re-hashed from the current generation on every
        call, so a generation that changed after validation cannot be promoted on
        the old evidence.

        The reference side is re-hashed *here* too: the cached
        ``reference_hash``/``reference_files`` were taken when the store bound
        itself, and a frozen reference that changed since (or a store resumed
        against a moved file) must be an error, not a stale binding.
        """
        current = self.current()
        if current is None or os.path.realpath(current[0]) != os.path.realpath(generation_dir):
            raise ValueError("only the current durable staging generation can be validated")
        folder = Path(generation_dir)
        manifest_path = self.reference_dir / "manifest.json"
        if not manifest_path.is_file():
            raise ValueError("experimental staging has no frozen-reference manifest")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("files") != self.reference_files:
            raise ValueError("frozen reference manifest changed since the store bound it")
        for name, digest in manifest.get("files", {}).items():
            if self._hash(self.reference_dir / name) != digest:
                raise ValueError(f"frozen reference file changed: {name}")
        if manifest.get("reference_sha256") != self.reference_hash:
            raise ValueError("frozen reference board hash changed since the store bound it")
        return {
            "candidate_board_sha256": self._hash(folder / "board.kicad_pcb"),
            "candidate_project_sha256": self._hash(folder / "board.kicad_pro"),
            "candidate_rules_sha256": self._hash(folder / "board.kicad_dru"),
            "reference_board_sha256": self.reference_hash,
            "reference_project_sha256": self.reference_files.get("board.kicad_pro"),
            "reference_rules_sha256": self.reference_files.get("board.kicad_dru"),
        }

    @classmethod
    def evidence_problems(cls, final_evidence: Mapping[str, Any],
                          binding: Mapping[str, Any]) -> list[str]:
        """Refusals for evidence that does not bind itself to ``binding``.

        Success booleans are read from the gates, never supplied by the caller;
        this only checks that each gate (a) reports success on its own terms and
        (b) names every hash it claims to have validated.
        """
        problems: list[str] = []

        def named(label: str, evidence: Mapping[str, Any],
                  fields: Mapping[str, str]) -> None:
            for field, expected in fields.items():
                actual = evidence.get(field)
                if expected is None:
                    continue
                if str(actual) != str(expected):
                    problems.append(
                        f"{label} does not name {field}={expected} (got {actual})"
                    )

        candidate = {field: binding.get(field) for field in (
            "candidate_board_sha256", "candidate_project_sha256",
            "candidate_rules_sha256")}
        reference = {
            "reference_board_sha256": binding.get("reference_board_sha256"),
            "reference_project_sha256": binding.get("reference_project_sha256"),
            "reference_rules_sha256": binding.get("reference_rules_sha256"),
        }
        cli_reference = {
            "source_board_sha256": binding.get("reference_board_sha256"),
            "source_project_sha256": binding.get("reference_project_sha256"),
            "source_rules_sha256": binding.get("reference_rules_sha256"),
        }
        terminal_reference = {
            "original_reference_sha256": binding.get("reference_board_sha256"),
            "reference_project_sha256": binding.get("reference_project_sha256"),
            "reference_rules_sha256": binding.get("reference_rules_sha256"),
        }

        native = final_evidence.get("native")
        if not isinstance(native, Mapping):
            problems.append("no native final-gate evidence")
        else:
            if not native.get("ok"):
                problems.append("native final gate did not report ok")
            named("native gate", native, {**candidate, **reference})

        cli = final_evidence.get("cli")
        if not isinstance(cli, Mapping):
            problems.append("no complete-CLI final-gate evidence")
        else:
            if cli.get("status") != "verified" or not cli.get("report_complete"):
                problems.append("complete CLI gate did not report verified/complete")
            named("CLI gate", cli, {**candidate, **cli_reference})

        terminals = final_evidence.get("terminals")
        if not isinstance(terminals, Mapping):
            problems.append("no terminal-partition final-gate evidence")
        else:
            if not terminals.get("ok") or not terminals.get("complete"):
                problems.append("terminal partition gate did not report ok/complete")
            named("terminal gate", terminals, {**candidate, **terminal_reference})
        return problems

    def run_final_validation(
        self, generation_dir: str, *,
        native_gate, cli_gate, terminal_gate, raise_on_problems: bool = True,
    ) -> dict[str, Any]:
        """Run the three gates against the current generation and bind them.

        Each callable is given the binding dict - the candidate and reference
        hashes of *this* generation, recomputed from disk - and returns its own
        verdict. This is the only supported way to build ``final_evidence``:
        the binding cannot be supplied by the caller, and a verdict that does not
        name every hash is refused here rather than at promotion time.

        The terminal gate runs *before* the CLI gate: the one exemption the CLI
        gate may grant - the reporter's churned ``unconnected_items`` endpoint
        pairing - is only valid against a fresh native terminal-partition proof
        for this candidate and reference, so the proof has to exist first. Every
        other class is judged by reproducible identity, and no proof can excuse
        it.

        ``raise_on_problems=False`` returns the assembled evidence with a
        ``problems`` list instead of raising, so a caller can record *why* a
        candidate was refused. It never weakens promotion:
        :meth:`promote_after_final_gates` re-derives the binding and re-runs
        :meth:`evidence_problems` on whatever it is handed.
        """
        binding = self.generation_hashes(generation_dir)
        native = dict(native_gate(binding))
        terminals = dict(terminal_gate(binding))
        # Bound to the proof produced immediately above, by the gate that needs it.
        cli = dict(cli_gate(binding))
        evidence = {
            "label": "final_validation_against_frozen_reference",
            "generation": os.path.basename(generation_dir),
            "binding": binding,
            "native": native,
            "cli": cli,
            "terminals": terminals,
        }
        problems = self.evidence_problems(evidence, binding)
        evidence["problems"] = problems
        if problems:
            if raise_on_problems:
                raise ValueError("final validation did not bind its evidence: "
                                 + "; ".join(problems))
            evidence["ok"] = False
            return evidence
        evidence["ok"] = True
        return evidence
