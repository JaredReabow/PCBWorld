"""The copper-clearance reporter must report each reached pair exactly once.

Two contracts are pinned here, on **both** providers - the RL module the engine
server loads and the ``_pcbnew`` kiface the pinned ``kicad-cli`` loads - over the
synthetic board in :mod:`reporter_fixture`:

* **multiplicity** - a pair that shares copper on more than one layer is
  reported once, not once per layer. The provider's per-run ``checkedPairs``
  cache is keyed on a pointer pair whose equality and hash are order-sensitive,
  so the filter that inserts and the visitor that records "already reported"
  have to canonicalise the pair identically (T28's repair). The multilayer
  families share all four copper layers, so a regression re-reports them four
  times and fails on multiplicity;
* **order independence** - a same-logical-pad pair with one netless and one
  netted member reports the same single finding whichever member the footprint
  declares first. A visit from the netless member returns without reporting; if
  it leaves the layer claimed, the netted member's later visit is filtered out
  and the pair is never reported at all (T29's repair). The *flip* families are
  emitted netted-first on the default board and netless-first on the reversed
  one, and both boards must report the identical pair set.

The exact **pair identities**, not just row counts, are asserted: every expected
pair from the fixture's own UUIDs, each exactly once, no pair outside that set,
and none of the fixture's exempt or control pairs. Row counting alone cannot
distinguish a substituted pair from the pair the fixture meant to pin.

Nothing is skipped: a missing engine build, a missing CLI, a missing kiface or an
engine that only loads under the provenance waiver fails the test. The waiver
(``PCBWORLD_ENGINE_ALLOW_MISMATCH``) is what a pre-repair router needs, and these
tests are exactly the ones it must not pass.
"""

from __future__ import annotations

import collections
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import reporter_fixture as fixture                                          # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

SHORTING_ERROR_CODE = 2


def build_dir() -> Path:
    return Path(os.environ.get("PCBWORLD_KICAD_RL_BUILD_DIR",
                               REPO_ROOT / "build_rl"))


def cli_path() -> Path:
    return build_dir() / "kicad" / "KiCad.app" / "Contents" / "MacOS" / "kicad-cli"


def provider_path() -> Path:
    return (build_dir() / "kicad" / "KiCad.app" / "Contents" / "PlugIns"
            / "_pcbnew.kiface")


def _require(path: Path, what: str) -> None:
    if not path.exists():
        pytest.fail(f"{what} is missing at {path}: these regressions are "
                    "required coverage and may not be skipped")


def shorting_pairs(rows) -> collections.Counter:
    """Per-pair row multiplicity of the shorting class, from a native run."""
    pairs: collections.Counter = collections.Counter()
    for violation in rows:
        if int(violation.error_code) == SHORTING_ERROR_CODE:
            pairs[tuple(sorted((str(violation.item_a),
                                str(violation.item_b))))] += 1
    return pairs


def native_pairs(board: Path, project: Path, rules: Path,
                 passes: int) -> list[collections.Counter]:
    """Run the engine's own DRC in its own process, ``passes`` times."""
    from pcb_world.agent.drc_gate import take_violations
    from pcb_world.engine import KiCadEngine, router_client

    # One engine-server process per pass: the provider's cache order is process
    # state, so a shared server would measure one arrangement of it.
    router_client.set_server_reuse(0)

    captures = []
    for _index in range(passes):
        engine = KiCadEngine(str(board), project_path=str(project))
        try:
            found = take_violations(engine, str(rules))
            captures.append(shorting_pairs(found.violations))
        finally:
            engine.close()
    return captures


def native_capture(directory: Path, passes: int) -> list[collections.Counter]:
    return native_pairs(directory / "board.kicad_pcb",
                        directory / "board.kicad_pro",
                        directory / "board.kicad_dru", passes)


def cli_capture(directory: Path, runs: int) -> list[collections.Counter]:
    """Run the pinned CLI's DRC, once per fresh process."""
    from pcb_world.agent.cli_gate import CLI_OPTIONS, run_drc, _stable_item_identity

    cli = cli_path()
    env = dict(os.environ)
    env["KICAD_RUN_FROM_BUILD_DIR"] = "1"
    frameworks = build_dir() / "kicad" / "KiCad.app" / "Contents" / "Frameworks"
    env["DYLD_LIBRARY_PATH"] = (f"{frameworks}:{env['DYLD_LIBRARY_PATH']}"
                                if env.get("DYLD_LIBRARY_PATH") else str(frameworks))

    captures = []
    for index in range(runs):
        out = directory / f"cli_report_{index}.json"
        report = run_drc(str(cli), str(directory / "board.kicad_pcb"), str(out),
                         timeout_s=600.0, env=env, options=tuple(CLI_OPTIONS))
        pairs: collections.Counter = collections.Counter()
        for row in report.get("violations", []):
            if row.get("type") != "shorting_items":
                continue
            # The CLI's identity is a JSON list whose first element is the item
            # UUID, so the reported pair can be compared with the fixture's own
            # expected UUID pairs rather than with a self-consistent row count.
            uuids = []
            for item in row.get("items", []):
                identity = _stable_item_identity(item)
                try:
                    parsed = json.loads(identity) if identity else None
                except ValueError:
                    parsed = None
                uuids.append(str(parsed[0]) if isinstance(parsed, list) and parsed
                             else "")
            pairs[tuple(sorted(uuids))] += 1
        captures.append(pairs)
        out.unlink(missing_ok=True)
    return captures


def assert_exact_pair_set(captures: list[collections.Counter], *, provider: str,
                          order: str) -> None:
    """Every run reports exactly the fixture's expected pairs, once each.

    Counting rows is not enough: a swapped or substituted pair at the same count
    has to fail, the ordinary collisions have to be present, and every exempt or
    control pair has to be absent.
    """
    expected = set(fixture.expected_pair_ids())
    forbidden = set(fixture.forbidden_pair_ids())
    clean = fixture.clean_pair_id()
    genuine = fixture.genuine_pair_ids()
    assert captures, f"{provider}/{order}: no capture"
    for counter in captures:
        observed = set(counter)
        assert observed == expected, (
            f"{provider}/{order}: reported pairs differ from the fixture's "
            f"expected pairs\n"
            f"  unexpected: {sorted(observed - expected)}\n"
            f"  missing:    {sorted(expected - observed)}")
        assert not (observed & forbidden), (
            f"{provider}/{order}: an exempt or control pair was reported: "
            f"{sorted(observed & forbidden)}")
        assert clean not in observed, (
            f"{provider}/{order}: the same-net control pair {clean} was reported")
        assert max(counter.values()) == 1, (
            f"{provider}/{order}: a multilayer pair was reported more than once: "
            f"{dict(counter)}")
        for pair in genuine:
            assert counter[pair] == 1, (
                f"{provider}/{order}: the genuine collision {pair} was reported "
                f"{counter[pair]} time(s)")


def test_fixture_is_deterministic_and_four_copper_layers(tmp_path):
    from pcb_world.engine import KiCadEngine

    first = fixture.write_fixture(tmp_path / "one")
    second = fixture.write_fixture(tmp_path / "two")
    assert first.read_bytes() == second.read_bytes()
    assert fixture.build_board(True) != fixture.build_board(False), (
        "the reversed board must actually differ from the default one")

    engine = KiCadEngine(str(first), project_path=str(first.parent / "board.kicad_pro"))
    try:
        layers = int(engine.get_copper_layer_count())
    finally:
        engine.close()
    assert layers == 4, "the fixture must exercise the multi-layer multiplicity"
    assert fixture.expected_same_logical_pad_pairs() == 14
    assert fixture.expected_genuine_pairs() == 2
    assert fixture.expected_shorting_pairs() == 16


def test_fixture_covers_every_required_case():
    """The fixture's own case tables, not the provider, define the coverage."""
    expected = fixture.expected_pair_ids()
    forbidden = fixture.forbidden_pair_ids()
    assert not (expected & forbidden), "a pair is both expected and forbidden"
    assert expected, "the fixture expects no findings at all"
    assert forbidden, "the fixture carries no exemption or control case"

    # Single copper layer: every pad in GS1 is surface mount on F.Cu only.
    assert fixture.SINGLE_LAYER_FAMILIES == ("GS1",)
    single = next(family for family in fixture.FIXED_FAMILIES
                  if family.reference == "GS1")
    assert all(pad.kind == "smd" and "F.Cu" in pad.layers for pad in single.pads)

    # Multilayer multiplicity: the flip families are through-hole.
    assert set(fixture.MULTILAYER_FAMILIES) >= {"FM4", "FR4"}
    assert all(pad.kind == "thru_hole"
               for family in fixture.FLIP_FAMILIES for pad in family.pads())

    # Exemptions and controls are each represented by name, not only by count.
    exempt_references = {"EQ1", "AL1", "UC1"}
    fixed_by_reference = {family.reference: family
                          for family in fixture.FIXED_FAMILIES}
    for reference in exempt_references:
        assert reference in fixed_by_reference, f"{reference} case is missing"
    for family in fixture.FIXED_FAMILIES:
        if family.reference in exempt_references:
            pairs = {tuple(sorted((fixture.uid(a.token), fixture.uid(b.token))))
                     for a in family.pads for b in family.pads if a is not b}
            assert pairs <= forbidden, (
                f"{family.reference} is meant to be exempt but is not forbidden")
    assert fixture.clean_pair_id() in forbidden
    assert len(fixture.NEARBY) == 2 and len(fixture.DISTANT) == 2, (
        "the nearby and distant controls are each one pair of footprints")


def test_regression_runs_under_the_strict_provenance_guard():
    """The repaired generation is what these tests measure, and it is enforced.

    A pre-repair router is stamped so the guard refuses it; the waiver exists for
    diagnostics and must never be what makes this regression pass.
    """
    assert not os.environ.get("PCBWORLD_ENGINE_ALLOW_MISMATCH"), (
        "the provenance waiver is set; this regression must run under the "
        "strict guard, not around it")
    script = REPO_ROOT / "engine" / "tools" / "cpp_content_hash.sh"
    _require(script, "the engine C++ content-hash script")
    source_hash = subprocess.run(
        ["bash", str(script), str(REPO_ROOT / "engine" / "kicad-patches")],
        capture_output=True, text=True, check=True).stdout.strip()
    stamp = (build_dir() / "pcbnew" / "python" / "rl" / "ENGINE_CPP_HASH")
    _require(stamp, "the RL module's ENGINE_CPP_HASH stamp")
    assert stamp.read_text(encoding="utf-8").strip() == source_hash, (
        "the built router does not match this tree's C++ sources; rebuild it "
        "before trusting this regression")
    _require(cli_path(), "the pinned kicad-cli")
    _require(provider_path(), "the CLI's _pcbnew.kiface provider")


def test_engine_reports_every_pair_exactly_once(tmp_path):
    directory = fixture.write_fixture(tmp_path / "forward").parent
    captures = native_capture(directory, passes=3)
    assert_exact_pair_set(captures, provider="engine", order="netted-first")


def test_engine_reports_every_pair_exactly_once_when_netless_first(tmp_path):
    """The same pairs, declared from the other side, must still all be reported.

    This is the ordering the pre-repair provider dropped every same-logical-pad
    pair in, and the reason the earlier regression deliberately asserted nothing
    about those pairs.
    """
    directory = fixture.write_fixture(
        tmp_path / "reverse", netted_first=False).parent
    captures = native_capture(directory, passes=2)
    assert_exact_pair_set(captures, provider="engine", order="netless-first")


def test_cli_reports_every_pair_exactly_once_in_both_orders(tmp_path):
    for netted_first, order in ((True, "netted-first"), (False, "netless-first")):
        directory = fixture.write_fixture(
            tmp_path / f"cli-{order}", netted_first=netted_first).parent
        captures = cli_capture(directory, runs=2)
        assert_exact_pair_set(captures, provider="cli", order=order)


#: The saved board's writer owns its own whitespace, so pad order is recovered
#: from the deterministic pad UUID offsets rather than from the pad text shape.
def _pad_declaration_order(text: str, family) -> list[str]:
    positions = {pad.token: text.index(fixture.uid(pad.token))
                 for pad in family.pads()}
    return [token for token, _offset in sorted(positions.items(),
                                               key=lambda item: item[1])]


def test_save_and_reopen_normalises_pad_order_and_keeps_the_report(tmp_path):
    """A hand-written netless-first board is normalised by KiCad's own writer.

    The writer re-emits each footprint's pads in its canonical order, so a board
    that never went through KiCad can lose (or gain) this class on its first
    save. What is asserted is that the report does not move across the round
    trip: the hand-written order and the writer-normalised order report the same
    exact pair set, once each.
    """
    from pcb_world.engine import KiCadEngine, router_client

    router_client.set_server_reuse(0)
    directory = tmp_path / "roundtrip"
    board = fixture.write_fixture(directory, netted_first=False)
    source = board.read_text(encoding="utf-8")

    engine = KiCadEngine(str(board), project_path=str(directory / "board.kicad_pro"))
    try:
        engine.save(str(directory / "saved.kicad_pcb"),
                    str(directory / "saved.kicad_pro"))
    finally:
        engine.close()
    saved = (directory / "saved.kicad_pcb").read_text(encoding="utf-8")

    reordered = []
    for family in fixture.FLIP_FAMILIES:
        before = _pad_declaration_order(source, family)
        after = _pad_declaration_order(saved, family)
        assert sorted(before) == sorted(after), (
            f"{family.reference}: the round trip changed which pads exist")
        if before != after:
            reordered.append(family.reference)
    assert reordered, (
        "KiCad's writer no longer re-orders a netless-first footprint's pads; "
        "the save/reload normalisation this test pins is gone")

    assert_exact_pair_set(
        native_pairs(board, directory / "board.kicad_pro",
                     directory / "board.kicad_dru", 2),
        provider="engine", order="source")
    assert_exact_pair_set(
        native_pairs(directory / "saved.kicad_pcb",
                     directory / "saved.kicad_pro",
                     directory / "board.kicad_dru", 2),
        provider="engine", order="saved")


def test_engine_patches_reproduce_the_tree():
    """The repair is reviewable only if the patch set reproduces this checkout."""
    script = REPO_ROOT / "tools" / "reliability" / "check_engine_patches.py"
    _require(script, "the engine patch reproducibility checker")
    result = subprocess.run([sys.executable, str(script)],
                            capture_output=True, text=True, cwd=REPO_ROOT)
    assert result.returncode == 0, (result.stdout + result.stderr)[-3000:]
    assert "reproduce this tree" in result.stdout
