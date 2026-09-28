# Phase 26 - the reconnection campaign (plan)

Status: **declared before the campaign runs.** This file fixes the selection
order, the attempt ladder and the retention rule, so the run cannot choose its
own criteria after seeing a result. Predecessors:
[phase 23](../phase23/RESULT.md), [phase 24](../phase24/RESULT.md),
[phase 25](../phase25/RESULT.md).

## Starting point

Phase 25 restored one of the nineteen component joins the phase-24 zone refill
cost, on a disposable copy of the refilled board, and proved the closure
survives a legal refill of its own zones. Its candidate - the refilled board plus
one join - is **not** promotable: eighteen joins remain and the refill's whole
DRC debt is still carried. Phase 26 starts from a frozen copy of that candidate,
byte-hashed before the campaign and re-checked afterwards. Nothing in this phase
promotes a board.

## The restoration target

The target is component joins, not recorded split-relation pairs. A reference
cluster the refill split into `k` pieces needs `k - 1` joins, so the eighteen
remaining joins are the sum of `k - 1` over the currently fragmented reference
clusters. The exact components and the anchor pads are **re-derived from the
boards themselves after every retained merge**; a link that is already satisfied
is skipped, and anchors are never reused from an earlier state.

The candidate links are **every pair of components inside a fragmented cluster**,
not one chain per cluster: any pair is a lawful join, and a chain's first pair is
not necessarily the shortest. `k - 1` joins are still what makes a cluster whole,
so the campaign's budget is unchanged; enumerating every pair means each attempt
starts from the shortest lawful span rather than from an ordering artefact of
`get_pad_cluster_members`.

## Selection order (declared)

Links are ordered, after each retained merge, by:

1. shortest anchor span, then
2. lowest shared copper layer, then
3. lowest net code, then
4. lexicographic component identity.

A link whose component pair has already been attempted is not attempted again.
At most eighteen joins are attempted, so the campaign terminates even if joins
keep succeeding.

## Attempt ladder (declared, at most five transactions per selected link)

Each attempt starts from the same checkpoint, so no attempt inherits another's
copper:

1. `walkaround` direct - the placer's own strategy;
2. `shove` direct - push-and-shove;
3. `mark_obstacles` direct - the straight-line control;
4. `walkaround` with one dogleg waypoint off the span midpoint;
5. `shove` with the same dogleg waypoint.

Every transaction is checkpointed, and every attempt that is not retained is
restored and proven back to its starting geometry and terminal state before the
next attempt runs. An attempt that cannot be rolled back puts the campaign into
quarantine rather than continuing.

## Retention rule (declared)

An attempt is retained only when **all** of the following hold, each recorded
separately rather than folded into one boolean:

1. **closure** - the two chosen anchors share one native cluster after the
   commit;
2. **terminal identity and net membership** - the terminal capture is complete,
   no terminal vanished, no terminal is new, and no terminal changed net;
3. **no new fragmentation** - the candidate splits no cluster of the board it
   was built from, and splits no cluster of the refilled baseline;
4. **strict debt reduction** - the number of remaining joins against the
   accepted generation falls below the parent's;
5. **zero added relevant native identities** - the whole-board native DRC delta
   against the parent adds no relevant identity;
6. **zero added complete CLI identities** - the pinned complete CLI, run on the
   candidate after a legal refill, save and reopen, reports no added identity in
   any class against the immediate parent.

The CLI's unconnected endpoint pairing is the one class whose reported pairing
is a visualisation choice rather than a fixed terminal relation. Its churn is
accepted **only** when a fresh-process native terminal-partition proof, bound to
both boards' hashes, shows every parent connection preserved. No other class is
waived, and no rule, engine, gate or footprint geometry changes as part of this
campaign.

### Amendment after review

Two strengthenings were applied after review and are recorded here rather than
slipped into the results. The declared conditions are unchanged; how they are
evaluated is not.

1. **The decision is taken on the retained bytes.** An attempt that commits is
   saved, legally refilled in its own process, and then reopened by a separate
   verifier that evaluates the board-level conditions there, bound to the four
   boards' hashes; the CLI gate must be bound to the same candidate hash. The
   in-session snapshot taken immediately after the transaction stays in the
   evidence as a diagnostic and is never the decision.
2. **The rollback proof covers connectivity.** Restoring a checkpoint now
   requires exact equality of the native terminal partition - every net's
   terminal set and the partition as a canonical set of terminal groups - in
   addition to the copper digest and the counters, and quarantines otherwise.

## What the phase must produce

Every step carries its own attempt records, its retention decision with the six
conditions above, and - for a refused link - the exact refusal packet, so a
reviewer can re-judge each decision without re-running it. The final
experimental candidate is compared with the accepted generation and the
canonical original on all three surfaces the earlier phases used: the native
whole-board DRC, the native terminal partition, and the complete pinned CLI.

Evidence stays in the private artifact directory. This public account carries
aggregates only: no net names, coordinates, item geometry or rule values. The
accepted pointer, the source EasyEDA project and the phase 24 and 25 artifacts
are untouched, and nothing is staged, committed or pushed.
