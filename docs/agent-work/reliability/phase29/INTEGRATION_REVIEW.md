# Phase 29 (T29IV) - independent verification of the T29I public integration

Verdict: **PASS** on every enumerated requirement that committed objects can
settle, with four recorded observations (O1-O4). Check 4 - no force-push or
history rewrite - is bounded to local evidence and is not asserted here as a
fact about the server; section 5 states the exact bound. No tree or hash
mismatch, no secret, no coordinate-like geometry and no board identifier beyond
the already-accepted project-name convention. The observations are
documentation-accuracy items, not leaks.

Owner: task T29IV (verification). This page is the only file this task wrote.
Everything else was read through `git show HEAD:<path>` or `git ls-remote`, so
the checks are against committed objects, not the working tree. Nothing was
committed, staged, reverted or pushed.

## 1. Repository under test

| field | value |
|---|---|
| repository | `/Users/leo/Documents/PCBWorld-reliability` |
| branch | `feat/agent-reliability-actions` |
| commit | `da6eee538c5d56a53eab8fa5dd861d67e1ff254f` |
| parent | `b3d62f5c37e7528670d112e03d9a90029f23f4f3` |
| `origin` | `https://github.com/JaredReabow/PCBWorld.git` |
| `upstream` | `https://github.com/LGAI-Research/PCBWorld.git` |

## 2. Pre-write working tree state (recorded before this page existed)

`git status --porcelain=v1 --untracked-files=all` printed exactly one line:

```
 M docs/agent-work/reliability/PARALLELISM_LEDGER.json
```

No untracked files and no staged entries. That single modification is
post-commit bookkeeping outside this task's ownership: it registers T29I's
post-commit state (`ready_for_review` plus an evidence row) and adds the T29IV
task record. Working-tree SHA-256 of the file was
`d2a41bcdb1ea4feabfc947bf2f7ee888baed0ca6af7f0fba7f4ecca1bfb9fa04`; the
committed blob is `ff1d8979f7d27646c37263a66e0fa8b14057638b38c53fe5ff33ec57fa70fdab`
(114692 bytes), which is also the value the report's manifest carries at
`docs/agent-work/reliability/phase29/INTEGRATION.md:147`. This task did not
touch that file. This page's own creation is the second, expected working-tree
change; the state above is the clean baseline for any later comparison.

## 3. Requirement R1 / R2, check by check

| # | what was asked | result |
|---:|---|---|
| 1 | commit `%T` equals the Astra-accepted tree `85e79f68ce2562760b09b09c9ce4576799e75d67` | **PASS** - exact |
| 2 | parent is the `b3d62f5` baseline | **PASS** - single parent, `b3d62f5c37e7528670d112e03d9a90029f23f4f3` |
| 3 | remote tip equals the commit, read fresh | **PASS** - `git ls-remote` exit 0, `refs/heads/feat/agent-reliability-actions` = `da6eee5...` |
| 4 | no force-push / history rewrite | **NOT PROVABLE from here** - the local objects are consistent with a direct descendant of `main` and show no local rewrite; server-side history is unreadable, so no non-forced push is claimed. See section 5 |
| 5 | integration report manifest rows match committed blobs | **PASS** - 170 of 170 rows match size and SHA-256 |
| 6 | all 26 ledger artifact hashes match commit versions | **PASS** - 26 of 26 |
| 7 | `README.md` and `pyproject.toml` both say 1.1.0 | **PASS** - `README.md:7` marker `v1.1.0`, `pyproject.toml:5` `version = "1.1.0"` |
| 8 | two phase-5 documents redacted, aggregates preserved | **PASS on structure** - see section 6 |
| 9 | no real-board identifiers, secrets or coordinate-like decimals | **PASS under the stated patterns** - see section 7 |
| 10 | 49 accepted whitespace findings | **PASS** - exactly 49: 6/28/9/3/2 in the five engine patches, 1 in `pcb_world/agent/zone_coverage.py` |
| 11 | patch byte bindings | **PASS** - see section 8 |
| 12 | no submodule pointer changes | **PASS** - zero mode-`160000` entries in the commit diff; `.gitmodules` untouched; all three gitlinks byte-identical to the parent |

Commit shape: 3 commits total on the branch, no merges,
`da6eee5 -> b3d62f5 -> ed41174`. The commit is **not** an ancestor of `main`,
and `main` still points at `b3d62f5` both locally and on the remote.

Staged-set arithmetic reconciles: 171 changed paths = the 170 manifest rows plus
`INTEGRATION.md` itself; `git diff --numstat` gives `171 files, 63642
insertions(+), 31 deletions(-)` with no binary files, and 63642 - 384 (this
report's sibling, `INTEGRATION.md`) = 63258, the figure recorded at
`docs/agent-work/reliability/phase29/INTEGRATION.md:326`. The manifest byte
column sums to 3187243, matching `INTEGRATION.md:329`.

## 4. Exact commands and exits

| command | exit | observed |
|---|---:|---|
| `git rev-parse HEAD^{tree}` | 0 | `85e79f68ce2562760b09b09c9ce4576799e75d67` |
| `git rev-list --parents -n 1 HEAD` | 0 | `da6eee5... b3d62f5...` |
| `git rev-list --count HEAD` / `--merges --count HEAD` | 0 | `3` / `0` |
| `git ls-remote origin` | 0 | branch tip `da6eee5...`; `main` and both tags unchanged at `b3d62f5` |
| manifest re-derivation (170 rows vs `git show HEAD:<path>`) | 0 | 170 ok, 0 bad, 0 missing |
| 26 ledger artifact hashes vs committed blobs | 0 | 26 ok, 0 bad |
| `git diff --check b3d62f5 da6eee5` (message lines only) | - | 49 findings: 47 `trailing whitespace`, 2 `new blank line at EOF` |
| `python tools/reliability/check_engine_patches.py` | 0 | five patches apply to pin `7a31e0c9`, 9 files byte-equal, wire copies identical `f787f5be...` |
| `git diff --raw b3d62f5 da6eee5 \| grep -c '^:160000'` | 1 | `0` (grep found nothing) |
| `git merge-base --is-ancestor da6eee5 main` | 1 | not an ancestor - expected |

`tools/reliability/check_engine_patches.py` writes only to a temporary
directory; it does not modify the repository. Its wire-copy digest
`f787f5be4e1f7ff7f1280466bcec4e4db7a69aac796c70521c6ac4a1c50907f6` is the same
value the manifest records for `pcb_world/engine/wire.py`, which ties the live
patch reproduction to the staged bytes independently of the report's own table.

The full `bash tools/reliability/check_phase.sh --strict --expected-native 146`
run was **not** repeated: it is a build-and-test command, not a read-only check,
and the frozen evidence file records its last result. That file is
`docs/agent-work/reliability/evidence/phase_check.json` (`strict: true`,
`expected_native_tests: 146`, three groups at exit 0, `native_engine_available:
true`, `generated_at` 2026-09-29T03:06:57+1000). The report states at
`INTEGRATION.md:334-341` that every staged source, test and patch path still
carries the SHA-256 it had when that run exited 0 (36 source, 50 test, 6 patch
paths, 0 changed) and that only documentation and metadata changed afterwards;
section 8 below cross-checks that claim by hash rather than by assertion.

## 5. What local Git evidence does and does not settle about rewriting

What the local objects show:

* the commit has exactly one parent, the previously published `main` tip
  `b3d62f5`, and `main` is unchanged locally and on the remote;
* the branch reflog shows only `branch: Created from HEAD` at `b3d62f5`
  (2026-09-26 11:55:58 +1000) and the single commit at 03:19:39 +1000 - no
  amend, rebase or reset entry;
* `refs/remotes/origin/feat/agent-reliability-actions` has exactly one reflog
  entry, `update by push` at 03:19:47 +1000, i.e. the local remote-tracking ref
  had no earlier observed value;
* `git stash list` is empty.

This page does not claim a non-forced push. Server-side history - the receiving
side's push log and any server reflogs - is not readable from here, and the
remote answers only for the ref values it advertises now, so "no force-push ever
occurred" is not established by anything this task could read. What is
established is narrower: the branch tip is a direct descendant of the published
`main` tip, and no local evidence of a rewrite exists.

## 6. Phase-5 redaction, verified structurally

Every replacement the report lists is present at the stated line of the
committed blob:

| document:line | now reads |
|---|---|
| `docs/agent-work/reliability/phase5-kicad/RESULT.md:251` | `one pad` |
| `docs/agent-work/reliability/phase5-kicad/RESULT.md:260` | `the pad attribution` |
| `docs/agent-work/reliability/phase5-kicad/CHECKPOINT.md:511` | `3 on one supply net, 1 on another` |
| `docs/agent-work/reliability/phase5-kicad/CHECKPOINT.md:512` | `one pad`, `three supply nets`, `one further net` |
| `docs/agent-work/reliability/phase5-kicad/CHECKPOINT.md:685-686` | `the last three are supply nets` |
| `docs/agent-work/reliability/phase5-kicad/CHECKPOINT.md:703` | `one pad` |

The aggregates are intact at those same locations: the 4-way
`isolated_copper` split with its 3 + 1 shape (`CHECKPOINT.md:511`), the 33 split
relations with the 20 + two nets at 4 + 1 breakdown (`CHECKPOINT.md:512`), the
14 relations lost across five nets at the same relation count and per-net split
(`CHECKPOINT.md:684-686`) - recorded in this page as that aggregate shape and
net count, not as net indices - and the GND 34 -> 44 measurement
(`CHECKPOINT.md:684`).

Limit: the pre-redaction text is not in Git history, so no independent diff
against it is possible. What is verifiable - and verified - is that the
committed lines carry the replacement phrasing and the same aggregate counts,
and that the referenced lines exist. The claim that line numbers "did not move"
cannot be reproduced from history.

## 7. Privacy scan (aggregate only)

Scans ran over the 171 committed blobs of the commit.

* **Secrets:** zero matches for `sk-...`, `AIza...`, `ghp_...`, `xox[baprs]-...`,
  PEM private-key headers, or `api_key`/`api-key` assignments. The only tracked
  environment file is `.env.example`, which the report already names.
* **Coordinate-like decimals:** zero tokens with five or more fractional digits
  other than the deliberately pinned values `0.000001` (a coincidence epsilon in
  `pcb_world/agent/observations.py:270` and `tools/reliability/failure_taxonomy.py:388`)
  and `0.30000000004` (a float-floor regression pin in
  `tests/agent/test_native_via_sizes.py:340`, described in
  `HISTORY.md:1014` and `CHANGELOG.md:1221`). Four-fractional-digit tokens do
  exist and are all either the arXiv identifier `2607.05915`
  (`README.md:111`), measured error magnitudes such as `0.0000 mm` and a
  `0.7446 mm` repair figure, or values inside test fixtures. None is
  high-precision board geometry.
* **UUID-shaped tokens:** nine files, all clearly synthetic naming - the nil
  UUID at `pcb_world/agent/drc_gate.py:72`, the normalization namespace at
  `pcb_world/agent/kicad_metadata.py:51`, and obvious test patterns
  (`tests/agent/test_kicad_metadata.py:111-112` etc.). No board-derived item
  UUID appears.
* **Pad references:** the tight `U1.1`-style pattern occurs only in
  `tests/agent/test_terminals.py` (first at line 28) and
  `tests/agent/test_native_terminals.py` (first at line 36), which matches
  `INTEGRATION.md:65`.
* **Private workspace path:** 9 documents, 16 occurrences - exactly the count at
  `INTEGRATION.md:349-352`. The path names the workspace only; no file inside it
  is named.
* **Board project name:** the string appears *without* the private path in four
  committed files (see O2).

## 8. Patch byte bindings

The five patch digests in the manifest are the digests of the committed blobs
(rows 222-226 of `INTEGRATION.md`, re-derived independently). Independent
cross-registration exists for one of them: `0004-reporter-cache-canonical-order.patch`
is `c8a319719928161f0d4bd1646ba23677099aaf9c9a54f1124e57c3d68ae34a63` in the
ledger artifact `art-phase28a-patch`, in the report table and in the commit blob.
The other four (`0001`, `0002`, `0003`, `0005`) are registered **only** in this
report - `git grep` over the commit tree finds each digest in
`INTEGRATION.md` and nowhere else - so their "binding" is the manifest row plus
the live reproduction, not a separately recorded earlier reference. The live
patch group closes that gap for all five at once: applied in the documented
order to pin `7a31e0c9`, they reproduce nine files byte-for-byte and yield a wire
module whose digest equals the manifest row for `pcb_world/engine/wire.py`.

## 9. Observations (documentation accuracy, no leakage)

* **O1 - the committed packet describes itself pre-commit.** `INTEGRATION.md:3`
  says the packet is "stopped before commit" and `INTEGRATION.md:13` says
  "Nothing is committed or pushed", while the committed ledger still carries
  `"state": "running"` and `"evidence": []` for T29I
  (`PARALLELISM_LEDGER.json:2704` and `:2751`). That is consistent with the
  process - the accepted tree *is* the pre-commit packet, and the commit
  reproduces it byte-for-byte - but the published record now contains a sentence
  that is false as of the commit that carries it, and T29I's completion state is
  registered only in the uncommitted working tree.
* **O2 - the "two further documents" count is low.** `INTEGRATION.md:350-352`
  says two documents name the board project without the private path
  (`PLAN.md` and the ledger). The scan finds four: additionally
  `docs/agent-work/reliability/phase18/PLAN.md:8` and
  `pcb_world/agent/runner.py:261` (a default key path under `~/.config`). Same
  class as the two already reported - project name only, no geometry, net name,
  identifier or measurement - but the inventory is incomplete.
* **O3 - the pad-reference bucket has a fourth document.** The report restricts
  "component pad references" to the two terminal test files
  (`INTEGRATION.md:65`), which is correct for the `U1.1` reference form. A
  synthetic CLI-report fixture at `tests/agent/test_cli_gate.py:861` also carries
  a pad-plus-component description string. The report already acknowledges that
  fixture for its supply-net token, so this is a classification boundary rather
  than a missed file.
* **O4 - four of five patch digests are first registered here.** See section 8.
  Not a defect in the bytes, but the earlier phases left no independently
  published digest for `0001`, `0002`, `0003` and `0005`.

## 10. Limits and residual risk

This review is against committed objects and one remote read. It does not open
the private board workspace, does not re-run the native harness, and cannot
re-derive a board fact from a redacted line. The privacy audit is a pattern
scan: it can show that no token of a given class is present, and it cannot prove
the absence of a private fact that has no distinctive token. No tree, hash,
manifest or whitespace claim failed, and I found no secret, geometry or board
identifier in the committed public set beyond the conventions the report already
declares.
