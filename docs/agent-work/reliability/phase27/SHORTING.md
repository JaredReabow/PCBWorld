# Phase 27 (T27B) - the "items shorting two nets" hold

Status: **diagnosis complete, no board promoted, no rule, engine or gate
changed.** On byte-identical retained phase-26 boards the pinned native reporter
reports the shorting class as 30, 33 or 36 rows across runs. This phase measured
what actually moves: **the set of item pairs is invariant (30 pairs, in every
one of 193 passes and in the pinned CLI's own 20 frozen reports), and only the
number of layers each pair is reported on moves, for four pairs, between one and
the board's four copper layers.** The row count is therefore not a reproducible
quantity for this class; the pair set is.

Owner: task T27B (read-only diagnosis). Private evidence lives in
`phase27_shorting/` under `/Users/leo/Documents/Helix_Control_V3_pcbworld_improved`;
this document carries aggregates only.

## 1. What was measured, and how

Three retained phase-26 boards were copied into private scratch (source bytes
re-hashed before and after every capture, all unchanged) and measured with the
repository's own pinned engine under the pinned rule file:

| board | mode | passes | observed row counts |
|---|---|---:|---|
| phase-26 step 2's board | fresh process per pass | 60 | 30 x51, 33 x8, 36 x1 |
| phase-26 step 9's board | fresh process per pass | 60 | 30 x52, 33 x8 |
| phase-26 step 1's board | fresh process per pass | 25 | 30 x20, 33 x5 |
| phase-26 step 2's board | one server process, 8 loads | 24 | 30 x24 |
| phase-26 step 9's board | one server process, 8 loads | 24 | 30 x24 |

The reuse policy is a measured property of the harness, not an assumption:
`KICAD_ENGINE_REUSE=0` gives every capture its own engine-server pid, and parked
servers give one pid for the whole invocation. Both were checked before the
matrix was run. The phase-26 stability and probe runs used the fresh-process
shape, which is the shape that shows the movement.

The rule context is the pinned `.kicad_dru` (still exactly the sha256 the
phase-26 campaign baselined), and the engine is the pinned build. One private
artifact (`evidence/provenance.json`) records the build directory, the engine
version, the C++ stamp hash and the checked module hash, plus the pinned
`kicad-cli` and `_pcbnew.kiface` hashes and the reporter version; those two
binary hashes are identical to the ones the phase-26 CLI attribution recorded.

## 2. What is invariant, and what moves

**Invariant.** Collapsing the class to *item pairs* - two source items, layer
ignored - gives the same 30 pairs in all 193 passes, across both modes and all
three boards. The same 30 pairs are also what the pinned `kicad-cli` reports:
the engine's core set and the CLI's set are equal pair-for-pair, and the CLI's
own 20 frozen reports (from the phase-26 CLI attribution) agree with each other
and with the engine.

**What moves.** The reporter sometimes emits a pair once and sometimes once per
copper layer the pair's pads share. On this board that is 1 or 4 rows per pair,
and the multiplicity of an affected pair is all-or-nothing: a pair is either
reported once or on all four copper layers. Across the fresh-process matrix the
per-pair multiplicity took only the values 1 and 4 (never 2 or 3), which is a
prediction of the mechanism in section 3 rather than a coincidence.

**Scoped.** Both facts above are about the affected pairs. The always-present row
of an affected pair is on F.Cu because the pad test walks the layers in
enumeration order, top first, while the other 26 pairs are single rows on
whichever copper layer they sit on: on the step-2 board the 30 core rows split 20
on F.Cu, 9 on B.Cu and 1 on In2.Cu. Nothing here claims a generic pair lives on
F.Cu.

Four of the invariant 30 pairs can carry the extra rows, and they are the same
four on every board: each is a pad pair inside one footprint where the pads share
a pad number, one pad is on a net and the other carries no net, and the smaller
pads sit geometrically inside a larger pad of the same footprint. Which of the
four flips in a given process is not fixed - over the fresh-process matrix the
number of affected pairs was four, three and two on the three boards, in 60, 60
and 25 passes respectively. All of them are *already* in the invariant 30, so
the movement is extra rows for pairs the reporter always reports, never a pair
appearing or disappearing.

Two other facts bound the effect. Only two classes moved in any pass: this one
(count) and the unconnected-items class (identity churn at a constant count, the
same re-pairing phase 25/26 already recorded). No other class's count or
identity set changed in any pass of the 193.

## 3. Why: the reporter's pair cache, keyed by address

The rows come from the copper-clearance provider's *same-logical-pad* branch
(two pads of one footprint with the same pad number and different nets), not
from a copper collision
(`build_rl/kicad_src/pcbnew/drc/drc_test_provider_copper_clearance.cpp:868-894`,
`testPadClearances` at `:1002`; the same body is in the RL fork at `:1055`).
Whether that branch fires once or once per layer is decided by the provider's
`checkedPairs` cache:

* the R-tree *filter* canonicalises the `(pad, other)` pair by pointer address
  before consulting and updating `checkedPairs` (`:1041-1045`);
* the *visitor* that sets `has_error` on that cache entry looks the pair up
  **without** the same swap (`:1063-1070`), and `PTR_PTR_CACHE_KEY` equality and
  hash are order-sensitive (`build_rl/kicad_src/pcbnew/board.h:91-99`, `:128-136`);
* so when the outer pad's address is above the other pad's, the flag lands
  nowhere, `has_error` is never set, and the pair is reported once per copper
  layer instead of once.

The address order is process state, not board state: the same bytes give one or
four rows depending on it, and that is why the row count is not reproducible
while the pair set is. The same code shape is in the **unpatched upstream** file
in the pinned tree - this is inherited KiCad behaviour, not something the RL fork
introduced - and it is exercised by both front ends: 2 of the 20 frozen CLI
reports from the phase-26 attribution carry the extra rows, and 1 of this
phase's own 24 fresh CLI runs does too - 3 of the 44 pinned-CLI invocations on
record, so the CLI shows the same effect at a lower rate. That also corrects an
earlier reading of the phase-26 probe: its four instances did not reproduce the
extra rows because all four happened to land on the one-row side, not because
the CLI or the probe is immune.

Classified against the four candidates: this is **reporting**, not connectivity
(the connectivity class's count is constant and only its pairing churns), not a
change in geometry (identical bytes, invariant pair set, and the same pairs are
already reported in the flat runs), and not memory corruption (nothing is lost:
the multiplicity is exactly one or the copper-layer count, and no pair ever
appears or disappears).

## 4. What that does to gating, measured

Phase 26's native condition compares one parent pass against one candidate pass
and counts added identities - row level, layer included. Replaying exactly that
estimator over every combination of the captured passes of the retained
step-1 -> step-2 boards (25 x 60 = 1500 combinations, real boards, no rerun
selection):

| estimator | added identities | resolved identities |
|---|---|---|
| row level (current) | 0 in 1281, +3 in 198, +6 in 21 | 0 in 1210, -3 in 290 |
| pair level | 0 in **1500** | 0 in **1500** |

Roughly one combination in six fabricates an addition of three or six shorting
identities that is not there, and one in five fabricates a resolution. Gating
this class at row level is not reproducible evidence; gating it at pair level is.

## 5. Fail-closed diagnostic

The class should be judged on the pair set, and the multiplicity kept only as
diagnostics:

1. **Gate on the pair set.** Collapse rows for this class to `{item_a, item_b}`
   with the layer dropped; that is the reproducible object, and it is the object
   the pinned CLI agrees with. Never gate this class on its row count.
2. **Record the multiplicity.** For each pair, keep the number of rows and the
   layers. A pair reported at a multiplicity other than 1 or the board's copper
   count is not the known artefact and must be treated as unexplained.
3. **Refuse on any pair added or removed.** A pair appearing or disappearing is
   a real change (or a new failure mode), and no rerun may be used to make it
   disappear: the estimator is asymmetric, so re-running until the class looks
   clean is not evidence.
4. **Refuse when the class cannot be collapsed.** A row with no item UUIDs (a
   geometry-keyed identity) cannot be pair-matched and is refused rather than
   counted.
5. **Keep the rest of the board untouched.** This hold is specific to one class;
   it must not soften any other class's condition.

## 6. Preconditions for a pair-set gate (documented, not implemented)

Collapsing this class to item pairs is only sound if a pair is an *identity*, and
that is a property of the board, not of the class. Measured this phase with the
repository's own inventory policy (`drc_gate.capture_inventory` and
`resolve_item_identity`, i.e. the gate's own notion of a provable key):

| board | distinct UUIDs | reused UUIDs (items they cover) | shorting rows whose key is unprovable |
|---|---:|---:|---:|
| phase-26 candidates, three measured | 16,859 / 16,861 / 16,875 | 0 (0) | 0 of 30 |
| canonical original | 16,819 | 0 (0) | 0 of 30 |
| accepted generation | 16,854 | 0 (0) | 0 of 30 |
| imported baseline | 9,577 | 922 (8,164) | 26 of 30 |

So a future gate must, per board and before any comparison:

1. read a **complete inventory** - the engine's whole item accessor, not the
   copper lists - and refuse when it is incomplete;
2. require every UUID in a compared pair to name exactly **one** physical item,
   on both sides; the imported baseline fails this (922 reused UUIDs, 26 of its
   30 shorting rows unprovable) and must be refused rather than compared;
3. require the **same physical signature on both sides**: the identity used for
   the parent and for the candidate has to be built the same way (UUID plus
   position, reference/pad/library id and description, as the repository's own
   CLI gate does), and the comparison has to be refused when collapsing to bare
   UUIDs would merge two distinct pairs - which is why this phase's CLI
   comparison is computed twice, once on bare UUID pairs and once on
   collision-safe pairs, and refused when the two disagree;
4. stay **class-scoped**: the relaxation applies to this one class only, and
   every other class keeps its existing row-level condition;
5. **fail closed**: refusal, never a waiver, and never a rerun until the class
   looks clean.

No gate is implemented in this phase. This is the precondition list an
implementation has to satisfy, and the table above is the evidence that makes it
necessary. The pinned CLI carries no inventory, so on the CLI side the same rule
can only be applied to what the report itself gives.

## 7. Concrete next experiment

Re-measure the whole retained phase-26 chain at pair level, with the CLI as an
independent reporter, and with the multiplicity recorded:

* for each retained step, K bounded fresh-process passes of the parent and of the
  candidate (the phase-26 stability harness plus this phase's pair collapse);
* assert the pair-level added set is empty for every parent-pass x candidate-pass
  combination, not for one sample;
* assert each pair's multiplicity is 1 or the copper-layer count, and record the
  distribution;
* stage the same bytes for the pinned CLI and assert its pair set equals the
  engine's for each board.

That turns "no added identity in the instance that decided" into a reproducible
statement about pairs, and it is the smallest experiment that also tests whether
any *other* pair can ever enter the class on these boards.

## 8. Evidence, guards and limits

| | |
|---|---|
| input bytes | every capture re-hashed its staged copy against the frozen source; all frozen sources unchanged |
| boards opened | three phase-26 candidate boards (capture matrix) plus the canonical original, the accepted generation and the imported baseline (read-only identity check); all hash-bound and unchanged |
| accepted pointer and generation, canonical original, phase-24/25 artifacts, original EasyEDA project, pinned rule file | re-hashed, unchanged |
| engine | pinned `build_rl` build, version and C++ stamp recorded privately; no engine change |
| rules | pinned `.kicad_dru`, sha256 unchanged; no rule change |
| identity | complete inventory and 0 reused UUIDs on every board measured except the imported baseline, which is refused (section 6) |
| diagnostics | the row-level and pair-level estimators, the collision-safe CLI extraction and the set-vs-multiset comparison are covered by the phase's 132-check harness |
| public documents | leak-scanned for every private net name, footprint reference, coordinate, UUID and rule value |
| board/rule/engine/gate changes | none |
| paid API calls | 0 |
| staging, commit, push | none |

Limits. This is a read-only diagnosis: it promotes no board and the accepted
pointer has not moved; the board it was measured on still carries the refill
debt phase 24 recorded, and nothing here is manufacturing-ready. The mechanism
in section 3 is read from the pinned source and supported by the multiplicity
distribution and the per-process behaviour; it was not proved by instrumenting
the allocator, so the assignment of a *particular* pass to an address order is
inference, not measurement. The pair-level invariance is measured on this board
family and three of its retained boards, and the CLI-side rate is a 44-invocation
sample (3 carrying extra rows), so the rate itself is not characterised. The
duplication finding is measured on one imported-baseline artifact at the hash in
section 6; how often other imports reuse UUIDs is not characterised, which is
exactly why the precondition is a per-board verification and not a constant. No
gate is implemented.
family and three of its retained boards; a board whose footprints have no
duplicate pad numbers would not exercise the same branch at all.
