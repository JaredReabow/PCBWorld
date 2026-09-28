"""Fixtures for the agent tests: synthetic boards and a real engine.

The native tests must never turn a missing build into a pass. When the router
extension is not present they skip with an explicit reason, and the phase
harness reports that as "native coverage skipped", not as acceptance.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BUILD_DIR = Path(os.environ.get("PCBWORLD_KICAD_RL_BUILD_DIR", REPO_ROOT / "build_rl"))
FRAMEWORKS = BUILD_DIR / "kicad" / "KiCad.app" / "Contents" / "Frameworks"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _prepare_runtime() -> None:
    """Point the loader at this checkout's build before anything imports the router."""
    if FRAMEWORKS.is_dir():
        existing = os.environ.get("DYLD_LIBRARY_PATH", "")
        if str(FRAMEWORKS) not in existing.split(":"):
            os.environ["DYLD_LIBRARY_PATH"] = (
                f"{FRAMEWORKS}:{existing}" if existing else str(FRAMEWORKS)
            )
    os.environ.setdefault("PCBWORLD_KICAD_RL_BUILD_DIR", str(BUILD_DIR))
    # One C++ router per process; the engine's own guard enforces it.
    os.environ.setdefault("KICAD_ENGINE_REUSE", "0")


_prepare_runtime()


def engine_available() -> bool:
    try:
        from pcb_world.engine import engine_available as _avail

        return bool(_avail())
    except Exception:  # noqa: BLE001 - no engine, no native coverage
        return False


def fake_artifact_verifier(folder, request):
    """Explicit verifier injection for fake-engine runner tests only.

    The injected verifier owns the whole native acceptance for a run that uses a
    fake engine, so it carries the terminal-partition verdict the runner now
    requires as well.
    """
    assert Path(folder).is_dir()
    assert request.get("geometry_digest")
    binding = {
        field: request.get(f"expected_{field}")
        for field in (
            "candidate_board_sha256", "candidate_project_sha256",
            "candidate_rules_sha256", "reference_board_sha256",
            "reference_project_sha256", "reference_rules_sha256",
        )
    }
    return {
        "ok": True, "reopened": True,
        "progress": request["expected_progress"],
        "geometry_digest": request["geometry_digest"],
        "test_double": True,
        "terminal_partition": {
            "ok": True, "complete": True, "test_double": True,
            "reasons": [], "split_relations": [],
            **{key: value for key, value in binding.items() if value},
        },
    }


@pytest.fixture(scope="session")
def native_engine_checked() -> None:
    if not engine_available():
        pytest.skip(
            "native coverage skipped: no kicad_rl_router build "
            f"(looked in {BUILD_DIR}/pcbnew/python/rl). "
            "Build it with `bash engine/build_rl_router.sh` — no native "
            "acceptance is claimed without it."
        )
    # A build that exists but cannot be verified is an error, not a skip: the
    # phase gate must not go green because every native test quietly opted out.
    from pcb_world.engine import ensure_router_provenance

    try:
        ensure_router_provenance()
    except Exception as exc:  # noqa: BLE001 - surfaced as a hard failure
        pytest.fail(
            "native coverage cannot run: a kicad_rl_router build is present but "
            f"cannot be verified ({type(exc).__name__}: {exc}). Rebuild it with "
            "`bash engine/build_rl_router.sh`.",
            pytrace=False,
        )


@pytest.fixture
def engine_factory(native_engine_checked, tmp_path):
    """Build engines over synthetic boards and always close them.

    A few tests need two engines at once (one with a rule file, one without).
    That is the documented ``allow_router_coexistence`` case: each engine talks
    to its own engine-server process, and the fixture closes them all at
    teardown.
    """
    from pcb_world.engine.kicad_engine import KiCadEngine, allow_router_coexistence

    created = []

    def _make(board_path: str):
        with allow_router_coexistence("agent tests own their engines and close them"):
            engine = KiCadEngine(str(board_path))
        created.append(engine)
        return engine

    yield _make

    for engine in created:
        try:
            engine.close()
        except Exception:  # noqa: BLE001 - teardown best effort
            pass


@pytest.fixture
def board_dir(tmp_path) -> Path:
    directory = tmp_path / "boards"
    directory.mkdir()
    return directory
