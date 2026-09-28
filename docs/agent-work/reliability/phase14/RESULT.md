# Phase 14 - result

Status: **ready for review.** The refusal-aware ordering policy and the screen's
board-generation stamp are implemented and tested; the matched trial executed
three of the four offered openings and the native DRC refused all three, so
nothing closed and the accepted pointer is unchanged.

Contract: [PLAN.md](PLAN.md).

## What changed

| file | change |
|---|---|
| `pcb_world/agent/runner.py` | `RunnerConfig.zone_gap_via_priority` (off by default), `_refusal_evidence()` and `_refusal_ranked()`; applied after the foreign-pour ranking and before the `candidate_limit` truncation |
| `tools/reliability/screen_zone_openings.py` | `sha256_file()` and real `board_sha256` / `project_sha256` / `rules_sha256` in the output (was `null`) |
| `tests/agent/test_zone_prefilter_unit.py` | the ordering contract: exact edge, stale generation, no refusal, unseen plan, substitution binding, truncation, other families, default off |
| `tests/agent/test_zone_point_query.py` | the screen tool's provenance end to end, including a missing file returning no hash |
| `tools/reliability/check_phase.py` | native floor 134 -> 135 |
| `docs/agent-work/reliability/phase14/{PLAN,RESULT}.md`, `HISTORY.md`, `CHANGELOG.md` | this phase |

No engine or wire file was touched, no footprint moved, no zone was deleted, no
clearance was relaxed and no gate was substituted.

## The ordering policy

With `zone_gap_via_priority` on, a pair is reordered only when all four hold: the
pair offers a `zone_gap_via` candidate, its **exact** edge key carries
same-generation DRC-refusal evidence, and that evidence names at least one direct
candidate by plan key. Then each opening that sits after the first already-refused
direct candidate moves to just in front of it — one at a time, so the
non-refused direct plan, every other family and the original order inside each
group keep their positions, and nothing is removed. With no evidence about the
edge, or no opening to place, the generated order is returned untouched, which is
why a clean connection never enters this path.

The evidence is read per record with two filters: the record's reason must be a
DRC refusal, and its `board_digest` must equal the current generation's. The edge
is matched by its canonical key through both the geometry actually attempted and
the connection the attempt stood for, so a substitution attempt still counts as
evidence about the edge it was made for.

## Screen provenance

`screen_zone_openings.py` now reports the board's SHA256 (plus the project's and,
when supplied, the rule file's) instead of `null`. A screen is evidence about one
generation; without the hash, two screens of different boards, or a re-run of an
old screen, are indistinguishable. A missing file returns no hash rather than an
invented one, and the test asserts both halves.

## The matched trial

Four edges with measured openings, matched ON/OFF arms, **fresh engine and
session per arm**, the same board file, rules and budgets, `candidate_limit` 2,
each candidate with its own wall-clock allowance, zero planner requests. Every
transaction is atomic, so a refused arm leaves the board byte-identical for the
next one.

| metric | value |
|---|---:|
| edges trialled | 4 of 4 with openings |
| arm ON / OFF candidates executed | 8 / 8 |
| **`zone_gap_via` openings executed** | **3 of 4** |
| edges where the policy reordered | 3 (two refused direct plans demoted behind the opening each) |
| arm ON / OFF accepted | **0 / 0** |
| `zone_gap_via` outcomes | `routing_failed` / `drc_regression` x3 |
| rollback | `copper_state=restored`, `rollback_verified=true` on all 16 executions |
| wall clock | 737.9 s |
| accepted generation | unchanged |

**Correction (post-review).** The trial script read `evidence["violations"]`,
which does not exist — the session records the gate's verdict under
`evidence["drc_delta"]` — and it read `result.connected`, which is recomputed
*after* the rollback, instead of `evidence["connected_before_refusal"]`. Its
"empty DRC classes" and any reading of "closed" from that output are therefore
invalid. The script is fixed, `DrcDelta`'s evidence rows now carry the item
identities that caused each finding (a small, tested evidence-retrieval fix, not
a behaviour change), and the three executed attempts were re-run in isolation;
the corrected result is in **Diagnosis** below. Nothing else about the trial —
the counts, the ordering, the rollbacks, the unchanged generation — is affected.

**What the policy bought.** On three of the four edges the ordering evidence
existed, the two direct plans were demoted, and the measured opening ran **first**
— taking about 85 s, as long as a whole arm allowance in the previous phase,
which is exactly why it had been skipped there. Executed, it was **refused by the
native DRC on all three edges** (`drc_regression`), and the following direct plan
was refused too. The fourth edge has no DRC-refusal evidence on this generation
(its recorded failures are connectivity, not DRC), so the policy correctly
declined to reorder, the family fell outside `candidate_limit`, and that opening
is reported as **not executed** — a truncation, not a timeout, and not a verdict
on the opening.

Nothing closed, so no promotion ladder ran, no artifact was staged and no pointer
moved.

## Diagnosis

The first version of this section guessed at a corridor cause. The trial could not
support that guess, because it read the wrong evidence fields; a correction pass
re-ran the three executed attempts and read the gate's own delta instead. The
measured cause is not the corridor.

**What the corrected evidence says** (three attempts, one fresh engine and
session each, 263.8 s, all `routing_failed` / `drc_regression`):

* `connected_before_refusal` is **true** on all three: the plan *did* close the
  connection, and the gate refused the copper it closed it with.
* Every added *relevant* finding names a **zone** as one of its two items — a
  foreign plane on the same net in all three cases — paired with copper that the
  pre-attempt inventory could not name because the transaction had just created
  it (the new via and its trace).
* The finding classes are `Clearance violation` and `Hole clearance violation`.
* The findings sit **at or beside the opening**: the farthest is 0.27 mm and
  0.51 mm away on two edges, and on the third every finding is **0.000 mm** from
  the opening point, i.e. on the via itself.

So the opening is not a corridor problem: the via's own copper and hole are
inside the clearance a foreign plane requires. The margin the screen applies is
the board's copper clearance plus **the default netclass's** copper half-width,
and the zone query's distance is measured against the *loaded* fill. Two things
that follow are checkable rather than hypothetical: the router adopts a via size
for the routed net that can be larger than the default the margin assumes, and a
via also has to satisfy a **hole**-to-copper rule that the margin does not model
at all. The next step is therefore to derive the margin from the *adopted* via
geometry for the net being routed and from both applicable rules, and to require
the opening to clear by that full distance — again as a read-only screen before
any transaction. A corridor screen is not indicated by this evidence.

## Verification

| command | exit | result |
|---|---:|---|
| `bash tools/reliability/check_phase.sh --strict` | 0 | **479 unit + 135 native, no skips** |
| `python tools/check_separation.py` | 0 | 4/4 checks; wire copies identical |
| `python tools/reliability/check_engine_patches.py` | 0 | 3 patches, 6 files byte-equal to the pin |
| `git diff --check` | 0 | clean |
| accepted-generation sha256 | - | unchanged; pointer untouched |

## Limits and risks

* **One opening was not executed**, and the reason matters: no same-generation
  DRC-refusal evidence for that edge, so the policy did not promote it and the
  limit cut it. Raising the limit for that edge, or promoting on connectivity
  refusal as well as DRC refusal, would test it — the policy deliberately does
  not, because a connectivity refusal is not evidence that the copper is
  unkeepable.
* **The margin is a lower bound on what the DRC demands.** It uses the board's
  copper clearance and the *default netclass's* copper half-width; the routed
  net's adopted via can be larger, and the hole-to-copper rule is not modelled at
  all. The corrected diagnosis says that is what refused all three executed
  openings, so any future opening test has to use the adopted via geometry and
  both rules.
* **Three refusals are not a general negative.** They say the measured opening
  does not survive the whole-board DRC on those three hops; they say nothing
  about edges where the corridor is clear end to end.
* **The policy is off by default** and stays that way until a controlled trial
  shows value; it reorders only on exact-edge, same-generation, DRC-refusal
  evidence.
* **The screen is still a point-and-corridor measurement**, not a legality
  proof: `distance_mm` is the engine's measurement against loaded fills, and the
  fill's currency is explicitly `loaded_unverified`.
