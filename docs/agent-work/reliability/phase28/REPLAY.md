# Phase 28 (T28C) - the six-step restoration replay under the repaired provider

Status: **historical restoration replay verified under new provider, pending
Astra review.** The six frozen phase-26 restoration steps were re-measured
against the repaired loaded provider and replayed through the accepted T27A
verifier, and **all six retain**. Nothing was routed, refilled, promoted or
written back to a frozen input; the accepted pointer did not move. This is a
statement about a historical restoration replay, not about the candidate being
manufacturable - the board still carries the refill's whole DRC debt and twelve
joins remain.

Owner: task T28C. Private evidence lives in `phase28_six_step_replay/` under the
private workspace; this page carries aggregates only. Predecessors:
[phase 26](../phase26/RESULT.md), [phase 27](../phase27/VERIFIER.md),
[phase 28 RESULT](RESULT.md).

## 1. The gap this closes

The phase-26 retained steps were accepted on a complete pinned CLI gate whose
`provider_sha256` names the **pre-repair** kiface. T28A rebuilt that kiface and
the RL module, so the accepted verifier refuses every step on the stale provider
digest - all six exit 10 with `provider_hash_matches: false`, while the five
board conditions already hold. That refusing run is kept as the run of record
(`phase27_verifier/evidence/dryrun_live_stale.json`); it is not a board result,
it is a binding result.

The fix is not a waiver and not a friendlier rerun: the gate evidence is
regenerated under the current binaries with the same phase-26 gate code, and the
steps are then replayed through the accepted verifier unchanged.

## 2. What was staged and what was regenerated

For each of the six steps the driver stages a **copy** of the immediate parent,
the post-refill candidate and the pre-refill snapshot beside the rules, checks
each copy against the hash the phase-26 record froze, and only then measures.
Only step 3's refill changes bytes; the other five pre-refill snapshots are
byte-identical to their candidates.

For each step the complete pinned CLI gate is regenerated for **both sides**
(two runs on the parent, two on the candidate) under the repaired CLI and
kiface, from the parent's own rule file. The regenerated gate reports
`ok`, `report_complete` and `staged_inputs_match`, with both staged rule digests
equal to the pinned rule file.

| | value |
|---|---|
| build-tree CLI | `56dd7910af7d190c` |
| loaded kiface | `dfe46690...` (repaired; was `79ac6f6f...`) |
| RL module | `a922eb81...` (repaired) |
| C++ stamp | `2f9e6153`, equal to the live tree hash |
| provenance waiver | unset in every process |

Each step then replays through the accepted verifier with three native DRC
passes per board, its four pinned board hashes, the pinned rule file, the fresh
gate and its bound terminal proof. Every step ran **once**; no run was discarded
or retried for a better sample.

## 3. The result

Six of six steps retained with all six conditions true.

| step | transaction | joins vs accepted, before -> after | native instances | CLI added classes | verdict |
|---:|---|---:|---|---|---|
| 1 | walkaround, direct | 18 -> 17 | 3 + 3, agree | unconnected pairing only (9) | retain |
| 2 | walkaround, direct | 17 -> 16 | 3 + 3, agree | unconnected pairing only (6) | retain |
| 3 | push-and-shove, direct | 16 -> 15 | 3 + 3, agree | unconnected pairing only (8) | retain |
| 6 | walkaround, direct | 15 -> 14 | 3 + 3, agree | unconnected pairing only (13) | retain |
| 9 | walkaround, direct | 14 -> 13 | 3 + 3, agree | unconnected pairing only (6) | retain |
| 17 | walkaround, one dogleg | 13 -> 12 | 3 + 3, agree | unconnected pairing only (8) | retain |

Five of the six conditions were already true in the stale-provider run; exactly
one flipped, `zero_added_cli_identities`, and it flipped because the gate now
binds the repaired kiface - not because any board condition was relaxed. No
class was waived: the CLI's only added class on every step is the unconnected
endpoint pairing, which the rule already admits behind a bound fresh-process
terminal-partition proof, and that proof reports zero vanished terminals, zero
vanished nets and zero split relations on **every** step.

The regenerated gate is the same measurement as phase 26, not a new one, and the
numbers say so. The raw unconnected row counts are identical to the phase-26
record on both sides of every step - 153/152, 152/151, 151/150, 150/149,
149/148, 148/147 parent/candidate - and no class other than the unconnected
pairing adds an identity anywhere. Only the waived pairing itself churns between
measurement campaigns, which is the documented behaviour of that class: the row
count is a property of the copper, the pairing is the reporter's choice, and the
rule judges it on the count plus the fresh native proof.

## 4. Native instances, and the class that used to move

Each board is measured in three separate fresh engine server processes, and the
condition is stated over all nine parent-by-candidate instance pairs, not over
a favourable one. Every server is distinct within its role and none ran under
the provenance waiver. The immediate parent's three instances agree with each
other, and so do the candidate's, in findings **and** multiplicities.

The instability phase 26 recorded is gone under the repair. The "items shorting
two nets" class reports the same 30 rows - and the board the same 313 relevant
findings overall - in every pass of every step on both sides. An addition
cannot be hidden by picking a quiet instance because no instance is quiet.

## 5. Terminal partitions agree across both surfaces

The native read and the CLI gate's bound terminal-partition proof, produced by
different processes, agree exactly on every step: 191 nets and 1060 terminals
throughout, no vanished terminal, no new terminal, no changed net membership,
and the cluster count falling one per step, from 339 to 333. That is the shape
the six joins should have and nothing else.

## 6. Guards

The frozen inputs were re-hashed after the run and are unchanged: the accepted
pointer and generation, the canonical original, the phase-24 and phase-25
boards, the phase-26 start board, all six parents, candidates and pre-refill
snapshots, the pinned rule file, the original EasyEDA project, and the accepted
post-T28A binaries. The public-document leak scan is clean and
`git diff --check` passes.

## 7. Limits

This is a replay, not a routing campaign and not a promotion. It says the six
historical restorations still hold under the repaired reporter and the accepted
verifier; it does not say the candidate is finished. The refilled candidate
still carries the refill's DRC debt - nineteen isolated copper identities, the
starved-thermal deficit and the unconnected churn - and twelve joins remain
before the fragmented clusters are whole again. Phase 26's promotional refusal
stands unchanged.
