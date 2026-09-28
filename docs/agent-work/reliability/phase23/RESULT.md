# Phase 23 - what refused the 24 real closures

Status: **ready for review.** All 24 closure-then-refusal evaluations of the
phase-17 trial are accounted for, three of them are reproduced reversibly, and
the one provable harness defect is fixed with tests. **Zero promoted, zero
planner calls, accepted pointer and hash unchanged.**

Contract: [PLAN.md](PLAN.md).

## The answer

All 24 refusals are reconciled. Retained rows from three reproductions demonstrate
new copper against existing zone fills; the same cause for the other 21 remains a
supported hypothesis. Those three were replayed from the immutable accepted
board, and their native deltas added **15 distinct relevant identities** between
them and retained **14 sampled rows** (8 of 9, 5 of 5, 1 of 1), and in every one
of those 14 rows one side is an item that did not exist before the transaction
(the plan's track or via) and the other is an existing board zone, at exactly zero
measured clearance in most rows and below the required minimum in the rest. The
fifteenth identity is
counted by the complete histogram but has no retained row, so it carries no
item-level attribution. The remaining 21 of the 24 plans carry class-level
evidence only - a clearance class and a hole-clearance class, no connectivity,
zone-outline or rule-load class, and refusal positions that all sit on the plan's
own route - so reading them as the same shape is a **supported hypothesis, not
item-level proof**.

For the reproduced plans that is not a harness or rule-application defect. The
same two rules already
refuse **7 637** places on the accepted generation itself, including 164 at
exactly zero clearance against copper that was on the board before any routing. The
router does not model zone fills as obstacles, so its walkaround and shove
candidates cross pours, and the native gate then measures the stored fill and
refuses the copper. Resolving it is a board or router decision: refill zones
under the current rules (a one-shot board operation with its own generation and
whole-board verification) or route pour-aware. Ignoring the class or relaxing the
value is not a fix, and neither would be in this repository's gift.

## The defect the phase did prove

A refusal record could not answer "which classes refused this plan, and how
many". `AttemptRecord.drc_classes` was counted from the DRC delta's
`added_relevant` rows, and that list is deliberately capped - so a refusal was
recorded with at most eight findings' worth of classes no matter how many it
added. On this trial **9 of the 24 records under-count**: the worst recorded
eight where the delta added 50. The public taxonomy's refusal-class aggregate
inherits the same cap.

The cap did not need to reach the histogram. `DrcDelta` gains
`class_histogram()`, `to_evidence()` carries a complete
`added_relevant_class_counts`, and the runner records that, keeping the rows as
the fallback and as the source of the (deliberately few) hint positions.

## What changed

| file | change |
|---|---|
| `pcb_world/agent/drc_gate.py` | `DrcDelta.class_histogram()`, and a complete `added_relevant_class_counts` in `to_evidence()` |
| `pcb_world/agent/runner.py` | `_record` records the complete histogram; rows remain the fallback and supply the hint positions |
| `tests/agent/fake_engine.py` | `violations_on_fix`: several findings appended by one fix |
| `tests/agent/test_drc_classification.py` | the added-class histogram is not capped by the row sample |
| `tests/agent/test_runner_scripted.py` | a refusal record carries every added class, not just the row sample |
| `docs/agent-work/reliability/phase23/RESULT.md`, `HISTORY.md`, `CHANGELOG.md` | this phase |

Both new tests fail against the unpatched module and pass with it: the histogram
test sees eight findings where ten were added, and the record test sees the row
sample's split rather than the delta's. No engine or wire file was touched, no
footprint moved, no zone was deleted, no clearance was relaxed, and no violation
class was ignored.

## The 24, accounted

Fresh phase-17 trial only, exact accepted generation, exact plan identity.

| | |
|---|---:|
| trial plan evaluations | 109 |
| closure-then-DRC-refusal evaluations | 24 |
| never closed | 85 |
| distinct edges | 4 |
| distinct (edge, plan) attempts | 24 |
| duplicate attempts | 0 |
| distinct class fingerprints | 5 |

Every one of the 24 closed the connection, was refused, rolled back, and left the
board on the accepted digest with the rollback verified; none recorded a failing
plan step, because every step ran. The recorded classes are a clearance class and
a hole-clearance class and nothing else - no connectivity, zone-outline or
rule-load failure appears in any of the 24.

The 87 refusal positions the records kept all sit within 0.5 mm of the plan's own
route (41 within 0.01 mm) and on a layer that plan's own copper occupies, which is
consistent with the three reproductions and does not by itself attribute a
finding to an item.

## The reproductions

One record per edge, exactly as recorded (same pair, mode and waypoint list),
through the same public `connect_targets` transaction, on a byte-identical
scratch copy, whole-board native DRC, zero model requests. The accepted board file
and the pointer were hashed before and after the run; each transaction's own
in-memory copper digest was compared before and after and came back equal, which
is what "restored" means for a session that never writes the board.

| | net A | net B | net C |
|---|---:|---:|---:|
| stored class fingerprint | clearance 4, hole 4 | clearance 3, hole 2 | clearance 1 |
| complete histogram | **clearance 5, hole 4** | clearance 3, hole 2 | clearance 1 |
| added relevant identities | 9 | 5 | 1 |
| retained delta rows | **8 of 9** | 5 of 5 | 1 of 1 |
| outcome / reason | routing_failed / drc_regression | same | same |
| closed before refusal | true | true | true |
| copper state | restored | restored | restored |

The histogram counts **distinct added relevant identities** - the delta's keys -
and not occurrence multiplicities: the same identity can stand for more than one
reported finding, and that count is carried separately in the payload.

Net A is the record the old capped histogram under-reported: its stored
fingerprint is 4 + 4, its complete histogram is 5 + 4. So the complete counts
matched the stored record exactly for net B and net C and **not** for net A. In
the 14 retained rows, one side of every added finding is copper issued during the
transaction and the other is an existing zone of another net; the rule context is
the project's own recovered rule file, and measured clearance is exactly zero on
most rows and below the required minimum on the rest.

## The board's own numbers

Two whole-board passes over the unmodified accepted generation, in one process,
returned the identical relevant set: **8 130 findings, 7 930 relevant**.

| | |
|---|---:|
| relevant findings on the accepted generation | 7 930 |
| of those, the same fill-clearance rule that refused the new copper | 7 488 |
| of those, the same hole-clearance rule | 149 |
| relevant findings touching a board zone | 7 636 |
| relevant findings at exactly zero measured clearance | 313 |
| fill-clearance findings against pre-existing copper at zero clearance | 164 |

The 7 488 cluster tightly around one value, i.e. the pours were filled at a finer
clearance than the recovered rule file requires - the mismatch phase 3
documented. Those pre-existing fill findings support the same hypothesis for the
refusals that were not reproduced, because they show the board itself already
carries the refusing condition at scale; they do not prove the cause of all 24.

## Native DRC stability

Twenty fresh processes each ran one whole-board pass over the same unmodified
generation.

* 18 returned 8 130 / 7 930 with an identical relevant multiset.
* 2 returned 8 133 / 7 933: the entire difference is one class, `Items shorting
  two nets`, 30 vs 33. In the one captured with positions, the three extra
  findings are one location reported on three copper layers. The other 7 933
  process was captured as counts only and its relevant multiset differs, so
  whether its three extras are the same three stays unknown.
* Four consecutive passes *inside one process* returned the identical relevant
  multiset and the same count for that class.

The gate compares a baseline and a candidate from the same session, and no
baseline/candidate pair in this phase moved that class. The observation is
recorded because it bounds how much of the profile is a property of the board
alone, not because it refused anything here.

## Promotions

**None.** Nothing was accepted, so the promotion ladder did not run. The accepted
board file and the pointer have the same hashes after the run as before it, no
engine call this phase wrote to the generation directory or to the scratch copies,
and the accepted board still hashes to `6c4f8ab81b83...f593477cf1b`.

## Verification

| command | exit | result |
|---|---:|---|
| `bash tools/reliability/check_phase.sh --strict` | 0 | 496 unit + 138 native, no skips |
| `python tools/check_separation.py` | 0 | 4/4 checks; wire copies identical |
| `python tools/reliability/check_engine_patches.py` | 0 | 3 patches, 6 files byte-equal to the pin |
| `git diff --check` | 0 | clean |
| private reconciliation | 0 | 24/24 closure-then-refusal records accounted, 4 edges, 24 distinct plans, 0 duplicates |
| private reproductions | 0 | 3 transactions; class fingerprints reproduced exactly for 2 of 3, and net A's complete histogram (5 + 4) differs from its capped record (4 + 4); all 3 rolled back |
| private baseline passes | 0 | 2 passes, identical relevant multiset, 8 130 / 7 930 |
| private stability passes | 0 | 20 processes; 18 identical, 2 +3 in one class; 4 passes inside one process identical |
| accepted-generation sha256 | - | `6c4f8ab81b83...f593477cf1b`, unchanged |

## Limits and risks

* **Item-level attribution covers 3 of the 24 plans, and 14 of their 15 added
  identities.** The fifteenth identity is counted but not attributed, because the
  delta keeps only a capped row sample. The other 21 plans have class counts and
  geometry-consistent hint positions; the record does not carry the item
  identities, and reading them as the same shape is a supported hypothesis rather
  than proof.
* **A reproduction is the same plan on the same copper, not the same process.**
  The complete histogram matched the stored record for two of the three plans and
  did not for the first (5 + 4 against a capped 4 + 4); the accepted file hash
  bound the board, but the trial's own session is gone.
* **The +3 class is characterised, not explained.** Whether those three findings
  are absent from the DRC run or attributed to different items is not
  established, and the second 7 933 process was not captured with positions.
* **No refill was run.** This phase names the board operation; it does not
  perform it, and it does not show that a refill clears the findings. That needs
  its own generation and whole-board verification.
* **The per-edge spread of the 24 is not published.** The aggregate counts above
  are; net names, coordinates and rule values stay in the private evidence tree.

## Next bounded action

One measurement, then a decision. Measure a zone refill under the current rules
on a disposable copy of the accepted generation, and do not conclude anything
from persistence alone. The refill must be **proved supported under the pinned
rules** before it can say anything about the findings: the refill call must report
success, the refilled board must be saved and reopened, and the reopened file must
show that the fill geometry actually changed. Then re-run the whole-board native
DRC and classify every finding - the two refusing classes and the 7 637
pre-existing ones they share - as resolved, persistent, or new. An unsupported or
incomplete refill (the call refuses, the fill does not change, or the change does
not survive save/reopen) is its own reported outcome and is not evidence about the
findings either way. Only a proved refill can support a statement: if the refusing
classes resolve, the fills must be recomputed against the rule file before this
copper is called unacceptable; if some persist under a proved refill, they are a
candidate genuine clearance that a further measurement, not an assumption, has to
confirm - and the pour-aware question then belongs in the next phase. This phase
does not assert that a persistent finding implies genuine clearance. Neither
branch touches the gate, the rules or the accepted pointer.
