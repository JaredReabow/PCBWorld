# Phase 28 (T28CV) - independent verification of the six-step replay

Status: **independently verified, with two qualified observations.** This is a
read-only recomputation of the T28C six-step restoration replay from the raw
packets it left behind, not a rerun. It re-derives every retain verdict and
every condition from the recorded measurements, re-hashes the frozen inputs, and
re-checks the public account against the raw numbers. It does not execute the
provider, the CLI, the native module or the verifier.

Reviewed document: [REPLAY.md](REPLAY.md),
`sha256 cce3372c1a8241cc72fada47a7359e42e2437b753ed44afa6d2daf403d6ca647`.
Predecessors: [phase 26](../phase26/RESULT.md),
[phase 27](../phase27/VERIFIER.md), [phase 28 RESULT](RESULT.md).

## 1. What was recomputed, and how

For each of the six steps the raw per-step `verify.json`, the regenerated CLI
`gate.json` and its bound `gate.proof.json` were read directly. The six
conditions were rebuilt from the packets' own raw blocks rather than from the
recorded booleans: the parent/candidate comparator, the three fragmentation
summaries, the nine-pair native delta and the CLI binding flags. The anchor
closure is recomputed from the packet's own anchor-cluster indices using the
producing helper's semantics - merged iff the first pad's cluster index is not
null and equals the second's - and the terminal-identity condition includes
both partitions' completeness as T27A defines it. Every class row in the
regenerated gate was diffed against the frozen phase-26 gate for the same step,
and the frozen phase-26 board hashes were compared to the hashes of the copies
the replay actually measured. A small set of negative controls drives the same
recomputation code with unequal, missing, out-of-range and non-integer anchor
indices and with incomplete partitions, and all of them behave as required.

Recomputed result: **six of six steps retain, and all six conditions are true on
every step**, matching the recorded verdicts.

| step | conditions (recomputed) | retain | native pairs / added / consistent | joins before -> after |
|---:|---|---|---:|---:|---:|
| 1 | all six true | yes | 9 / 0 / yes | 18 -> 17 |
| 2 | all six true | yes | 9 / 0 / yes | 17 -> 16 |
| 3 | all six true | yes | 9 / 0 / yes | 16 -> 15 |
| 6 | all six true | yes | 9 / 0 / yes | 15 -> 14 |
| 9 | all six true | yes | 9 / 0 / yes | 14 -> 13 |
| 17 | all six true | yes | 9 / 0 / yes | 13 -> 12 |

## 2. Provenance, waiver and the CLI binding

The CLI gate binds the same binaries on both sides of every step. The gate's
`cli_sha256` and `provider_sha256` equal the digests re-derived from the pinned
paths and the asserted/evidential/local digest families agree with each other;
`staging.identical` is true and both staged rule digests equal the pinned rule
file. Every gate reports `ok`, `report_complete` and `staged_inputs_match`, with
two runs per side. The router build-provenance waiver is unset in every process:
the recorded `allow_mismatch_env` lists are empty and `waiver_enabled` is false
for all four roles on all six steps. No step was a recorded-provenance replay.

The gap the replay closes is real and independently confirmed: the refusing run
of record refuses **all six** frozen steps with exit 10 on the stale pre-repair
provider digest, with the five board conditions already true.

## 3. Native distinct-process evidence and terminal partitions

Each board is measured in three native passes plus a separate capture, and the
replay records the server pid of every one. The three DRC server pids are
distinct within each role on every step, the immediate parent and the candidate
use disjoint pid sets, and no measurement ran under the waiver. Every one of the
nine parent-by-candidate instance pairs reports zero added relevant identities,
and each board's own instances agree with each other, on every step.

The native read and the CLI gate's bound terminal-partition proof agree exactly
on every step: 191 nets and 1060 terminals across all four roles on the native
side, and the same 191/1060 in both partitions of the CLI proof, with zero
vanished terminals, zero vanished nets and zero split relations. The cluster
count falls one per step, 339 -> 333 across the ladder.

## 4. Aggregates re-derived from the raw counts

The public "items shorting two nets" and relevant-finding totals hold in **every
pass of every step on both sides**: 30 shorting rows and 313 relevant findings.
The unconnected row counts the public account quotes are reproduced exactly from
the regenerated gates - 153/152, 152/151, 151/150, 150/149, 149/148, 148/147
(parent/candidate) - and are identical to the frozen phase-26 record on both
sides of every step. On every step the only class that **adds** an identity is
the unconnected endpoint pairing; no other class adds one in either campaign.

## 5. Guards

**Twenty-nine in-scope frozen inputs** re-hash to their recorded values: the
accepted pointer and generation, the canonical original, the phase-24 and
phase-25 boards, the phase-26 start board, all eighteen frozen
parent/candidate/pre-refill snapshots, the pinned rule file, the original
EasyEDA project, the accepted CLI, and the reporter cache patch. The eighteen
frozen snapshots additionally match the phase-26 record's own hashes, so the
replay measured the bytes that record froze.

The remaining four protected names - the kiface, the native module and the two
repaired provider sources - have changed **since this replay ran**, because the
concurrent provider task rebuilt them; their live digests no longer equal the
ones the replay is bound to. That drift is out of scope for this review and is
attributed, not excused: the expected digests are corroborated by the T28A
freeze record that predates the replay, and the live files carry a later
modification time than the replay itself. Nothing about the *current* provider
state is asserted here. The public document is leak-clean.

## 6. Two qualified observations

Neither changes a verdict; both are stated so a reader is not misled by the
"same measurement" comparison.

1. **A pre-existing removal, not an addition.** On step 9 the CLI gate shows the
   `starved_thermal` class losing one row (72 -> 71). This is a *removal*, not an
   added identity, so it does not bear on the "zero added CLI identities"
   condition; and it is reproduced **identically** from the frozen phase-26 gate
   for the same step, so it is a property of the fenced copper, not of the
   repaired provider. Every step's set of movement classes matches between the
   two campaigns.
2. **One intended row-count change.** On step 9 the frozen phase-26 candidate
   recorded an unstable shorting-class row count between its two CLI runs
   (30 and 33); the regenerated gate reports 30 on both runs. This is the one
   and only class whose parent/candidate rows differ from the phase-26 record,
   and it is the repair's own effect - the instability T27B diagnosed and T28A
   removed. Every other class on every step reports identical parent/candidate
   rows.

## 7. Limits

The anchor closure is now independently established from the packet's own
cluster indices on all six steps (equal, in-range indices on every step), not
copied from the recorded boolean. An earlier draft of this review claimed a
cross-check against the CLI proof's merged-pair field; that claim was
unsupported and has been removed - the CLI proof's merged-pair count is not a
measurement of the anchor closure, and no such cross-check exists. The four
provider/toolchain artifacts listed in section 5 were rebuilt by the concurrent
task after the replay, so no claim is made about the provider state after that
rebuild; the replay's own evidence is bound to the pre-rebuild digests, which
the T28A freeze record corroborates. No live execution was performed, by design,
so as not to contend with that rebuild.

## 8. Verdict

The T28C historical six-step restoration replay is **independently verified**:
six of six retain, all six conditions recompute true on every step (anchor
closure from the recorded cluster indices, terminal identity with partition
completeness), the CLI gate binds both sides under the repaired provider with
the waiver unset, the native condition holds over nine fresh distinct-server
instance pairs per step, the terminal partitions agree across both surfaces, and
all twenty-nine in-scope frozen inputs are unchanged, with the four
provider/toolchain artifacts noted as rebuilt by the concurrent task. No
promotion, routing or write-back is implied by this review.
