# Phase 13 - result

Status: **ready for review.** The exact-edge selector and the read-only screen
are implemented and tested; the screen covered all 16 cross-layer refusal edges
and found openings on 4; the matched ON/OFF trial ran those 4 and closed nothing,
so the accepted pointer is unchanged. The public rule-value boundary is closed
with a documented, narrow exception.

Contract: [PLAN.md](PLAN.md).

## What changed

| file | change |
|---|---|
| `pcb_world/agent/observations.py` | `edge_key()`: the canonical, direction-tolerant identity for one connection |
| `pcb_world/agent/runner.py` | `RunnerConfig.priority_edges` + `_priority_edge_keys` / `_pair_priority_edge`; edge pins outrank net pins in both the rotation and the coverage sort |
| `pcb_world/agent/zone_coverage.py` | `EdgeScreen`, `screen_openings()`, `screen_summary()`: bounded, read-only screening with an explicit budget status |
| `tools/reliability/screen_zone_openings.py` | CLI for the screen, with a counts-only `--public-summary` form |
| `tests/agent/test_zone_prefilter_unit.py` | exact-edge pinning, direction tolerance, malformed pins ignored |
| `tests/agent/test_zone_point_query.py` | the screen over a real engine: per-edge statuses, budget, malformed key, unchanged copper |
| `docs/agent-work/reliability/{RESULT.md,phase3/*,phase5-kicad/*}` | narrow rule-value redaction (see below) |
| `HISTORY.md`, `CHANGELOG.md`, `docs/agent-work/reliability/phase13/*` | this phase |

No engine or wire file was touched, no footprint moved, no zone was deleted, no
clearance was relaxed and no gate was substituted.

## The selector

`RunnerConfig.priority_edges` holds canonical 6-tuples
`(x0, y0, layer0, x1, y1, layer1)`. Matching is direction tolerant and rounded to
3 decimals, so the scan's own float noise and either endpoint order still match,
and a malformed pin matches nothing rather than everything. Edge pins are applied
after the cursor rotation and are ranked ahead of net pins in the coverage sort,
so a trial can name exactly the edges it means to study. A same-layer pair is
still a pair; the selector only decides which one is offered first.

## The screen

`screen_openings()` answers per edge with one of `openings`, `none`,
`same_layer`, `unknown` or `budget`, and `screen_summary()` reduces a set of
screens to counts with no coordinates — the form that belongs in a public
report. It is read-only end to end: the only engine call is the zone query, whose
answers are cached per point, and the test asserts that the via count, track
count and routing state are untouched afterwards. Screening is a measurement,
never a legality claim.

**All 16 cross-layer closed-but-DRC-refused edges were screened**, from the
accepted generation, in **0.78 s**:

| status | edges |
|---|---:|
| `openings` | **4** |
| `none` | 12 |
| `same_layer` / `unknown` / `budget` | 0 |

Four openings in total, one per qualifying edge; each was at least the board's
own clearance + via radius from foreign pour copper on every face the via
touches, and had a clear approach on its start layer. The other twelve had no
corridor sample that satisfied both rules — the physical limit phase 12 recorded
by hand for that hop.

## The matched trial

Only the four edges with openings were trialled. Same generator, same
transactional session and same budgets in both arms; the only difference is
whether the measured opening was offered; each arm had its own wall-clock budget
(90 s) so neither could starve the other; zero planner requests.

| metric | value |
|---|---:|
| edges trialled | 4 of 4 with openings |
| arm ON candidates tried / accepted | 12 / **0** |
| arm OFF candidates tried / accepted | 12 / **0** |
| `zone_gap_via` candidates offered / executed | 4 / **2** |
| outcomes | `drc_regression` 10, `connection_not_verified` 11, per-arm budget 3 |
| wall clock | 505.5 s |
| accepted generation | `6c4f8ab81b83...f593477cf1b`, **unchanged** |

**What the family actually did.** It offered one measured opening on each of the
four edges. Two of those four were executed and both failed — one refused by the
native DRC (`drc_regression`), one did not close the connection
(`connection_not_verified`). The other two were never evaluated: on those edges
the two `direct_*` candidates ahead of the family consumed the whole 90 s arm
budget, so the opening was left in the queue. That is a budget/ordering limit,
not a verdict on the opening.

Nothing closed, so no promotion ladder ran, no artifact was staged and the
pointer is untouched.

## Public/private boundary

Phase 12 redacted the board's coordinates. This phase closes the remaining
exposure the review flagged: the board's **own design-rule values** quoted in
older public reports and history. Eight statements across seven files were
redacted in place with an explicit `[rule values redacted]` marker:

`docs/agent-work/reliability/RESULT.md`, `phase3/RESULT.md`,
`phase3/CHECKPOINT.md`, `phase5-kicad/RESULT.md`, `phase5-kicad/CHECKPOINT.md`,
`phase10/RESULT.md` and `HISTORY.md`.

Every original line is preserved verbatim in the private
`phase13_routing/privacy_redactions.json`, and the exception to the append-only
convention is recorded in the phase-13 `HISTORY.md` entry. No other edits were
made to those documents: measured distances and medians were left alone, and no
conclusion, count or verdict was reworded.

## Verification

| command | exit | result |
|---|---:|---|
| `bash tools/reliability/check_phase.sh --strict` | 0 | **476 unit + 134 native, no skips** |
| `python tools/check_separation.py` | 0 | 4/4 checks; wire copies identical |
| `python tools/reliability/check_engine_patches.py` | 0 | 3 patches, 6 files byte-equal to the pin |
| `git diff --check` | 0 | clean |
| private screen over the 16 edges | 0 | 4 openings, 0.78 s, read-only |
| accepted-generation sha256 | - | unchanged; pointer untouched |

## Limits and risks

* **No closure, and the family is not exonerated.** Only two of the four offered
  openings were ever executed; the other two are unmeasured. A longer per-arm
  budget, or ordering the family ahead of the direct plans when the pair's
  history already records refusals, would actually evaluate them.
* **The screen's `none` is a measurement, not a proof of impossibility.** Twelve
  edges had no *sampled* spot with both a clear approach and clear pour room;
  the sample is a corridor at a fixed pitch, so a spot between samples is
  invisible to it.
* **A measured opening is still only a promise about a point.** The route to it,
  the other endpoint's approach and the DRC all still decide.
* **The rule-value redaction is a documented exception to append-only.** The
  originals live in private evidence; a reviewer who wants them in the public
  record has to say so explicitly.
