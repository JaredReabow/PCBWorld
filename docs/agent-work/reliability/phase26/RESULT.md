# Phase 26 - the reconnection campaign

Status: **ready for review.** The campaign restored **six** of the eighteen
component joins phase 24's zone refill cost, each one placed by the pinned native
router under the pinned project and rules and each one surviving a legal refill,
save and reopen of its own zones. It is **not promotable**: twelve joins remain,
the candidate still carries the refill's whole DRC debt, and the complete pinned
CLI refuses it against both the accepted generation and the canonical original.
The accepted pointer did not move.

Contract: [PLAN.md](PLAN.md). Predecessors:
[phase 23](../phase23/RESULT.md), [phase 24](../phase24/RESULT.md),
[phase 25](../phase25/RESULT.md).

## The answer

Phase 25 restored one join and left eighteen. This phase attempted all eighteen
within its declared bounds - eighteen links, at most five native transactions
each - and retained **six**. The minimum number of joins needed to make the
refill's fragmented reference clusters whole again fell from **18 to 12**, and
the fragmented clusters themselves from **6 to 5**: the large multi-component
cluster went from fourteen pieces to nine, and one of the five two-component
clusters is now whole. Whole-board unrouted edges fell from 153 to 147 and the
copper pad-group count from 339 to 333, both by exactly the six joins.

Every retained step satisfied all six declared conditions, measured against the
board it was built from: the two anchors share one native cluster afterwards; the
terminal capture is complete with no vanished terminal, no new terminal and no
changed net membership; no cluster of the parent or of the refilled baseline is
split; the join count against the accepted generation strictly falls; the
whole-board native DRC adds no relevant identity in the instance measured; and
the complete pinned CLI, run on the candidate after a legal refill, save and
reopen, adds **zero unwaived** finding identities against the immediate parent.
The unconnected endpoint pairing did move by three to nine identities per
retained step, and that class was waived only by the bound terminal proof below.
The candidate routes added no via: all six joins are copper on one layer each.

The conditions are decided on the **retained bytes**, not on a snapshot taken
in session: for an attempt that commits, the step saves the route, performs a
legal refill and save in its own process, and a separate verifier reopens the
files and evaluates the board-level conditions there, bound to four board
hashes; the CLI gate must then be bound to the same candidate hash. Review
asked for exactly this, because a decision taken before the refill is a decision
about a different board.

The native condition is a one-pass, one-engine-instance measurement and is
reported as one. It is not a cross-instance guarantee: the reporter moves three
identities of the "items shorting two nets" class between engine instances on
byte-identical boards (section 7 measures this), so the honest statement is "no
added identity in the deciding instance, and no added identity in any class
other than that one".

The CLI's unconnected endpoint pairing is the only class the retention rule can
waive, and it is waived only where a fresh-process native terminal proof - bound
to both boards' hashes, complete, with no vanished terminal and no split relation
- shows every parent connection preserved. The campaign never needed the waiver
to hide a lost connection: on every retained step the proof reported zero
vanished terminals and zero split relations.

What that does not buy is a promotable board. The candidate is the refilled board
plus six joins, so it still carries nineteen isolated copper identities, the
thermal-fill deficit and the unconnected churn the refill created. The complete
pinned CLI refuses it against the accepted generation and against the canonical
original, in the same shape phase 25 recorded - the campaign adds none of it, and
this phase does not promote anything.

## 1. Starting point and target

The campaign started from a frozen copy of phase 25's candidate - the refilled
board plus its single join - byte-hashed before the run and re-checked after. Its
fragmentation was re-derived from the boards rather than read out of phase 25's
evidence, and it reproduces: **18** minimum joins across **6** fragmented
reference clusters, five of them split in two and one into fourteen.

The restoration target is component joins, not recorded split-relation pairs, and
the components and anchors are re-derived from the live board after every
retained merge. A link that is already satisfied cannot be selected again.

## 2. The contested design point, and what the dry run changed

The declared link set was corrected **before** the campaign ran, and the reason
matters more than the correction.

The reconstruction manifest expresses restoration as one chain per fragmented
cluster. A chain is a spanning set, not the set of available joins: for a cluster
split into `k` pieces any pair of components can be joined, and the chain's first
pair is not necessarily the shortest - on the large cluster it was hopeless. A
bounded dry run, used to prove the pipeline end to end, selected that hopeless
pair, could not route it, and exposed the flaw.

Two properties fell out of the correction. The link set is now **every pair of
components inside a fragmented cluster**, ordered by span, then layer, then net
code, then component identity. And because
`get_pad_cluster_members` row indices are process-local, the ordering keys on
component membership digests and anchor geometry rather than on a row index, so
the choice is reproducible from the boards. Before the correction, one dry run
and one campaign attempt disagreed about which of two equal-span links came
first. The dry run's candidate is not promoted and its record is not carried into
these results; the campaign was re-run from the frozen input.

The same dry run answered the other design question offline: whether "zero added
complete CLI identities against the immediate parent" is reachable at all.
Re-reading phase 25's own gate reports, the parent and the phase-25 candidate
differ by zero added identities in every class except the reporter's unconnected
endpoint pairing, whose raw row count did not rise and whose identities are not
reproducible between two runs of one board - the exact shape the terminal-proof
waiver exists for. The condition is therefore satisfiable, and the campaign's six
retained steps are the measurement of that.

## 3. The campaign

One process per step. Each step copied its parent into its own scratch directory,
opened it in a fresh engine, re-derived the fragmentation from the live board,
and ran the declared five-attempt ladder from one checkpoint per attempt: the
placer's walkaround, push-and-shove, the straight-line control, and each of the
two again with one dogleg waypoint off the span midpoint. An attempt that met all
six conditions was kept; every other attempt was restored and **proven** back to
its starting copper digest and counters. A rollback that could not be proven
would have quarantined the campaign; none was needed.

The expensive half of retention - the complete pinned CLI on the candidate after a
legal refill, save and reopen - was paid in the historical run only for attempts
that had already passed the in-session conditions; the corrected pipeline pays it
for every committed attempt, because that is what deciding on the retained bytes
costs.

| step | cluster size before | transaction that satisfied the rule | transactions spent |
|---:|---:|---|---:|
| 1 | 14 | walkaround, direct | 1 |
| 2 | 13 | walkaround, direct | 1 |
| 3 | 12 | shove, direct | 2 |
| 6 | 11 | walkaround, direct | 1 |
| 9 | two-piece cluster | walkaround, direct | 1 |
| 17 | 10 | walkaround, one dogleg | 4 |

Step 3 is the first place push-and-shove is what carried a step in this phase
(phase 25 measured it but did not need it), and step 17 is the first place the
dogleg waypoint did. The other four retained on the placer's own direct
walkaround.

The refill is not a formality in that record: on five of the six steps the
refilled board came back byte-identical to the saved route, and on **step 3** the
refill rewrote the fills, so the retained bytes are the refilled board and the
closure is copper rather than a stale fill polygon. Step 3 is also the step the
independent verifier re-checked directly, and the bound recapture confirms it on
those bytes. Every retained step's refill returned, its input board was unchanged
by the refill, and its save restored the serialized metadata without a problem.

Running the corrected pipeline on a separate scratch copy retained the same
first join - same anchors, same two added segments, the same pad-group and
unrouted counts - but produced **different copper bytes**. The pinned placer is
not byte-deterministic run to run, so re-running the campaign would restore the
same joins without necessarily producing the same file, and a hash is
evidence only for the run that produced it.

## 4. Refusals

Twelve of the eighteen links were refused, and the refusal packets separate
cleanly into three shapes. Every refused link used its full five transactions,
and every one of those transactions was restored and proven back to baseline
before the next ran.

| shape | links | transactions | added relevant identities |
|---|---:|---:|---|
| the router could not reach the target in any transaction | 8 | 40 | 0 |
| the router committed twice and each commit added clearance identities | 1 | 5 | 2 per commit |
| the router committed every time and each commit added one clearance identity | 3 | 15 | 1 per commit |

Nothing here is a rule change, a footprint move or a gate change: the three
two-component links of the third shape add exactly one clearance identity per
commit under the pinned rules, which is also what phase 25 measured for the same
three cases. They are refused, not waived.

One honest inefficiency: because the link identity is the pair of *components*,
a pair that had already refused was offered again after a different merge changed
the component it belonged to. Three of the eighteen links were such re-offers,
and they refused again. The declared rule allowed it; a future phase could key
the attempted set on the anchor pair itself.

Bound: eighteen links attempted is the whole declared budget, so the campaign
stopped there rather than continue into unplanned work. The two-component cluster
on the net whose span is the longest of the remaining ones was never reached
inside the budget.

## 5. The final comparison

The final candidate was compared with the accepted generation, the canonical
original and the refilled baseline it started from, on all three surfaces.

| surface | vs accepted generation | vs canonical original | vs refilled baseline |
|---|---|---|---|
| native relevant identities, added | 19 | 19 | **0** |
| native relevant identities, resolved | 7 669 | 7 687 | 17 |
| fragmented reference clusters | 5 | 5 | **0** |
| minimum joins still missing | **12** | **12** | **0** |
| vanished terminals / changed net membership | 0 / 0 | 0 / 0 | 0 / 0 |
| complete pinned CLI | `unverified`, `ok: false` | `unverified`, `ok: false` | not run |

Read plainly. Against the board it was built from the candidate adds **no**
relevant identity, splits **no** cluster, loses **no** terminal and changes **no**
net membership - the seventeen resolved identities are connectivity findings the
joins closed. Against the accepted generation and the canonical original it adds
exactly nineteen relevant identities, all of them the refill's isolated copper
fills that phase 24 already measured, and it still breaks twelve relations those
boards have.

Two counts describe that same remaining debt and should not be confused. The
**minimum joins** are twelve: the sum of `k - 1` over the fragmented clusters,
four two-piece clusters plus one of nine pieces, which is the number of
restoration routes that would make the partition whole again. The comparator's
**split-relation witnesses** are twenty-one: its representatives of the same
five fragments, several to a fragment, and routing all of them would place
redundant copper. Phase 25 drew the same distinction between nineteen joins and
thirty-three witnesses; this phase reduced both, and the join count is the
restoration target.

The complete pinned CLI refuses it against both, with `report_complete: true`,
`staged_inputs_match: true` and two runs per board, in phase 25's shape: four
isolated-copper identities against one, thirty-six added starved-thermal
identities, one added dangling track, and unconnected endpoint churn. The
fresh-process terminal proof behind those verdicts is bound to both boards and
reports `complete: true` but `ok: false`, because the candidate genuinely is less
connected than those references - which is precisely why the gate's
unconnected-class waiver does **not** apply at this level. The refusals are
inherited refill debt, and the campaign is the measurement showing it did not add
to it.

## 6. Rollback proof: the historical record, and the strengthened replay

The campaign's own evidence is what it was: 64 rollbacks, each proven by the
engine's own ``restore`` return value, the copper digest and the progress
counters. That is **not** a terminal proof, and those records are not upgraded
here - they stay labelled as geometry-and-counter measurements.

Review asked for the rollback check itself to be strengthened, and it now
re-captures the native terminal partition after the restore and requires exact
equality of every net's terminal set and of the partition as a canonical set of
terminal groups, quarantining on any mismatch. Cluster *labels* are deliberately
excluded from that comparison: they are opaque per capture, and comparing them
literally would quarantine a correct rollback.

To show the strengthened path rather than describe it, a bounded replay re-ran
**every one of the campaign's 70 transactions** from the recorded parents - 23
that committed copper and 47 that refused - with the strengthened check. All 70
restored with matching copper, counters and terminal partition, no mismatch was
quarantined, and each replay agreed with the recorded commit-or-refuse outcome.

## 7. Stability of the native condition

The native condition is one pass in one engine instance, so it was measured
again: three DRC passes on each retained parent and candidate, giving nine
parent-candidate pass pairs per step. Two of the six steps showed up to **three
added identities**, all in the "items shorting two nets" class; the other four
showed zero added identities in every pair. On byte-identical boards the
relevant total moved between 313 and 316 - the same three-identity movement
phase 25 measured cross-process on the accepted generation.

That movement is reported, not smoothed over: it means the per-step native
condition is a statement about the instance that decided, and a re-run could see
three added shorting identities where this run saw none. A follow-up probe opened
each affected board in four further engine instances and measured the same 30
identities every time, so the trigger is intermittent rather than deterministic,
and no other class moved in any pass pair.

## 8. Guards

| | |
|---|---|
| accepted pointer | unchanged, `57b8db6121ee…` |
| accepted generation board | unchanged, `6c4f8ab81b83…` |
| canonical original board | unchanged, `a6232800646a…` |
| phase 24 refilled board | unchanged, `c8648ff5853f…` |
| phase 25 candidate and its refilled input | unchanged, `0bb7155167a9…` and `6c8e6e394368…` |
| original EasyEDA project | unchanged, `8f8ddaed…`, mtime predates this phase |
| journaled candidate chain | own directory, never the accepted board |
| public documents | leak-scanned for every private token, coordinate-like decimal and UUID |
| planner calls / paid API calls | 0 |
| staging, commit, push | none |
| disk | 14.41 GiB free at campaign start, 13.9 GiB free at its end |

The public documents this phase owns - `PLAN.md` and `RESULT.md` - are
leak-scanned for every private token in the evidence, for coordinate-like
decimals and for identifiers. The scan also covers the sibling
`CLI_ATTRIBUTION.md`: that file belongs to the separate, already-accepted CLI
attribution task and is scanned because it sits in the same directory, not
because this phase wrote it.

"Read-only" here means the bytes are unchanged: the guards re-hash each frozen
artifact after the campaign. Running the campaign against scratch copies means the
frozen directories are only ever read.

## 9. Verification

| command | exit | result |
|---|---:|---|
| `phase26_campaign.py` | 0 | 18 links attempted, 6 joins retained, 12 refused, no quarantine, start board unchanged |
| `phase26_final.py --stage measure` (x4) | 0 | four boards measured in their own engines, two DRC passes each, all passes within a board identical |
| `phase26_final.py --stage compare` | 0 | three relations plus the two final pinned-CLI gates |
| `phase26_guards.py` | 0 | every frozen hash unchanged, public documents clean, `git diff --check` clean |
| `phase26_recapture.py` | 0 | all six retained candidates confirm on their frozen bytes, hashes bound, zero unwaived CLI additions |
| `phase26_replay.py` | 0 | 70/70 transactions restored with a terminal-partition proof, outcomes agree with the record |
| `phase26_stability.py` | 0 | 9 pass pairs per retained step; movement in the shorting class reported |
| `phase26_shorting_probe.py` | 0 | four further instances per board; the three-identity variant did not reproduce |
| `phase26_tests.py` | 0 | 30 unit and consistency tests |
