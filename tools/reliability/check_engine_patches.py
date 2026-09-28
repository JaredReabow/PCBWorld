#!/usr/bin/env python3
"""Prove the engine patches reproduce this checkout's engine tree, from the pin.

The engine is a separate repository pinned here as the ``engine/`` submodule, and
the changes this workspace needs from it live in ``patches/engine/`` because the
submodule's working tree is not tracked by this repository. This script is the
proof that the patch set is complete and clean:

  1. every patched file is read from the engine's *pinned* commit
     (``git show HEAD:<path>``), never from the modified working tree;
  2. the patches are applied in their documented order to that pristine copy;
  3. every resulting file is compared byte for byte with this checkout's engine
     working tree - the tree the router was built from;
  4. the engine-side ``engine_server/wire.py`` is compared with the environment's
     ``pcb_world/engine/wire.py`` (the protocol module must be identical on both
     sides, which ``tools/check_separation.py`` also checks).

Exit 0 means "apply the patches to the pin and you get exactly this tree".

    python tools/reliability/check_engine_patches.py [--allow-other-base]
"""

from __future__ import annotations

import argparse
import filecmp
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ENGINE = Path(os.environ.get("PCBWORLD_ENGINE_HOME") or (REPO / "engine"))
PATCH_DIR = REPO / "patches" / "engine"

#: The engine commit the patch set is cut against (see patches/engine/README.md).
PINNED_ENGINE_COMMIT = "7a31e0c982a84fcda75be3ce69966027135bd7c1"

_DIFF_PATH = re.compile(r"^diff --git a/(?P<a>\S+) b/(?P<b>\S+)$", re.MULTILINE)


def engine_git(*args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(ENGINE), *args], capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git -C {ENGINE} {' '.join(args)} failed: {result.stderr.strip()}"
        )
    return result.stdout


def patch_order() -> list[Path]:
    return sorted(PATCH_DIR.glob("*.patch"))


def touched_paths(patch: Path) -> list[str]:
    text = patch.read_text(encoding="utf-8")
    paths: list[str] = []
    for match in _DIFF_PATH.finditer(text):
        if match.group("a") != match.group("b"):
            raise RuntimeError(f"{patch.name}: rename patches are not supported here")
        paths.append(match.group("b"))
    if not paths:
        raise RuntimeError(f"{patch.name}: no file paths found")
    return paths


def sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--allow-other-base", action="store_true",
                        help="continue when the engine HEAD is not the pinned commit")
    args = parser.parse_args(argv)

    if not (ENGINE / ".git").exists() and not (ENGINE / "HEAD").exists():
        print(f"FAIL: {ENGINE} is not a git checkout")
        return 1
    head = engine_git("rev-parse", "HEAD").strip()
    print(f"engine       : {ENGINE}")
    print(f"engine HEAD  : {head}")
    if head != PINNED_ENGINE_COMMIT:
        message = (f"engine HEAD is {head}, not the pinned {PINNED_ENGINE_COMMIT} "
                   "the patches were cut against")
        if not args.allow_other_base:
            print(f"FAIL: {message}")
            return 1
        print(f"WARN: {message} (--allow-other-base)")
    patches = patch_order()
    print("patch order  : " + ", ".join(patch.name for patch in patches))

    with tempfile.TemporaryDirectory(prefix="pcbworld-engine-pin-") as temp:
        work = Path(temp)
        files: list[str] = []
        for patch in patches:
            for relative in touched_paths(patch):
                if relative not in files:
                    files.append(relative)
        added: list[str] = []
        for relative in files:
            try:
                content = engine_git("show", f"{PINNED_ENGINE_COMMIT}:{relative}")
            except RuntimeError:
                # A patch may *add* a file the pin does not carry - the engine
                # keeps an overlay copy of each stock KiCad source it patches,
                # and a new overlay file is created by its own patch. There is
                # nothing to pre-write from the pin; `git apply` creates it, and
                # the byte-for-byte comparison below still has to hold.
                added.append(relative)
                continue
            target = work / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        print(f"files        : {len(files)} read from {PINNED_ENGINE_COMMIT[:7]}")
        if added:
            print("added by patch: " + ", ".join(added))

        for patch in patches:
            for flag in ("--check", None):
                command = ["git", "apply"] + ([flag] if flag else []) + [str(patch)]
                result = subprocess.run(
                    command, cwd=work, capture_output=True, text=True,
                )
                if result.returncode != 0:
                    print(f"FAIL: {' '.join(command)}: {result.stderr.strip()}")
                    return 1
            print(f"applied      : {patch.name}")

        mismatches = 0
        for relative in files:
            produced = work / relative
            actual = ENGINE / relative
            if sha256(produced) != sha256(actual):
                mismatches += 1
                print(f"DIFFERS: {relative}")
        if mismatches:
            print(f"FAIL: {mismatches} file(s) differ from this checkout's engine tree")
            return 1
        print(f"byte-equal   : {len(files)} file(s) match {ENGINE}")

        wire_engine = ENGINE / "engine_server" / "wire.py"
        wire_env = REPO / "pcb_world" / "engine" / "wire.py"
        if not filecmp.cmp(wire_engine, wire_env, shallow=False):
            print(f"FAIL: {wire_engine} and {wire_env} differ")
            return 1
        print(f"wire copies  : identical ({sha256(wire_env)})")
    print("OK: the engine patches apply cleanly to the pin and reproduce this tree")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
