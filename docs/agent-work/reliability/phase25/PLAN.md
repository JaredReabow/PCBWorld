# Phase 25 - bounded reconnection pilot on the refilled board

Status: approved by separate Astra review of phase 24's frozen private evidence.
Phase 23's gate fix is accepted; the accepted generation is unchanged at 135
unrouted edges and its pointer stays where it is.

## Question

Phase 24 refilled the accepted generation's zones under the pinned rule file and
measured the cost: the whole fill-clearance class disappeared board-wide, and the
board's own connectivity came apart - 7 reference clusters fragmented, at least
19 component rejoins. This phase asks one bounded question about that cost: can
one of those fragments be rejoined with the native router, under the pinned
project and rules, without making anything else worse?

## Contract

1. Re-derive the fragmentation privately as a reconstruction manifest: the
   fragmented reference clusters, their candidate components, and the minimum
   number of **component joins** that restores the reference partition -
   expressed as component links, not as one route per recorded witness pair.
2. Identify the exact existing-copper anchors of each two-component case and
   select one with a defensible native routing path. Selection is measured, not
   assumed: both strategies the pinned engine exposes (walkaround and
   mark-obstacles) are run per case through the native router on a shared
   board state, each inside a checkpoint that is restored and verified.
3. Attempt a bounded set of native transactions on a disposable copy only, under
   the pinned project and rule file, including push-and-shove. The attempt
   ladder and the retention rule are declared before it runs. No planner
   campaign, no paid API calls, no rule relaxation, no engine or wire change.
4. Measure the retained candidate against the refilled baseline for added DRC
   and terminal regression, and against the accepted generation for restoration
   of the original connectivity. A candidate that adds a relevant DRC identity
   or fragments a connection is refused, not reported as a success.
5. Re-fill the candidate's zones under the same pinned rules, save it, reopen it
   in a fresh process and re-run the native DRC and terminal checks, so a
   closure that only survives on stale fills is caught rather than banked.
6. Run the complete pinned KiCad CLI against the canonical original for the
   retained candidate, with the fresh-process terminal proof attached, and report
   every remaining failure. Never promote a partial candidate.

## Evidence

Private: `phase25_reconnect/` under the private workspace - the manifest,
selection probes, route attempts, fresh-process measurements, terminal proofs,
CLI gate, guards, the test module and `NOTES.md`. Public: this contract and the
aggregate `RESULT.md`.

Per-item geometry, net names, component references, coordinates and rule values
stay private. The public account carries aggregates, the exact commands run, and
the limits of what was measured.

Astra reviews the result; DeepSeek ran the implementation and the bulk analysis.
