# Phase 9 — a failure taxonomy first, then two generic strategies

Status: in progress (2026-09-27). Phase 8 is the accepted predecessor
([DECISION.md](../phase8/DECISION.md)).

## The problem this phase exists for

The harness reached a frozen board state and stopped improving it. What it did
*not* have was a complete account of why each outstanding connection was still
open. Every routing decision so far had been driven by the plans a sweep happened
to evaluate, and a refused plan on one pair is evidence about that pair only.
Choosing the next strategy from a sample is how a campaign spends a budget
re-working the same few connections.

So this phase builds the account first, then picks from it.

## Contract

1. **A reproducible, hash-bound taxonomy of every outstanding connection** on one
   exact board - not a sample. Each ratsnest edge gets exactly one category, and
   the category totals sum to the edge count. The tool is generic
   ([`tools/reliability/failure_taxonomy.py`](../../../../tools/reliability/failure_taxonomy.py));
   no net name, coordinate or layout file leaves the private workspace, and the
   aggregate view is what is published (see [TAXONOMY.md](TAXONOMY.md)).
2. **Edge count and component-pair count stay apart.** One ratsnest edge is one
   drawing; a net with four proved copper components has six pairs to join. The
   report carries both, because "135 connections" and "135 pieces of work" are
   not the same statement.
3. **Attempted and unattempted stay apart**, and a refusal is never promoted to
   "impossible". History is joined only where it is bound to this board: by the
   canonical pair key or by the native component identities an offer stood for.
   A record measured on other copper is counted as dropped, not inherited.
4. **Two generic strategies, chosen from the taxonomy**, implemented as small
   modular changes with tests. No rule relaxation, no gate substitution, no
   footprint movement, no protected-copper shrink.
5. **One bounded deterministic campaign** over the accepted board, zero planner
   requests, with coverage/fairness and failed-plan dedup intact and rollback
   unchanged.
6. **Promotion only through the immutable store**: fresh child, full native DRC,
   terminal partition, complete pinned-CLI validation against the canonical
   original. If nothing closes, the accepted pointer stays where it is and the
   report says why.

## Categories

Precedence is fixed and each edge takes the first that applies, so the totals
cannot double-count:

| # | Category | What it asserts |
|---|---|---|
| 1 | `already_connected_stale_edge` | the engine still draws it, but the two anchors are one cluster |
| 2 | `unroutable_degenerate_same_point` | both anchors are the same point on one layer |
| 3 | `layer_unresolvable` | an anchor's copper layer cannot be named |
| 4 | `copper_absent_at_offered_anchor` | an anchor has no copper at all |
| 5 | `unnamed_net_at_offered_anchor` | copper is present but the point rule cannot name the net |
| 6 | `insufficient_component_anchors` | the net has ≥2 proved components and no anchor pair could be proved between them |
| 7 | `drc_regression` | a plan closed it and the native gate refused the copper, with class and geometry |
| 8 | `via_no_continuation` | the pair's plans were via hops and the driver's own search proved no continuation |
| 9 | `connection_not_verified` | plans applied, rolled back, connectivity did not show the connection |
| 10 | `plans_exhausted` | every deterministic plan evaluated, no planner configured |
| 11 | `unattempted_cap_observed` | never attempted, and a run reported its scan's own bounds withheld pairs |
| 12 | `unattempted_no_record` | never attempted, no scan bound reported |

Categories 1–6 are board facts and outrank any inference from a refusal.
Categories 7–10 are only assigned from a recorded attempt **on this board**.

## Candidate order follows the evidence

A recorded refusal outranks a proximity heuristic. The runner truncates the
candidate list to `candidate_limit`, and the contextual families (pour waypoints,
obstacle detours) used to precede the refusal-derived ones, so a pair with
recorded violations was offered five or six plans derived from *where copper is*
before any plan derived from *where this pair was refused*. Measured on the V3
campaign's first 1 318 s segment: 36 distinct pairs carried recorded violations,
and **zero** refusal-derived candidates were ever evaluated. The refusal families
now follow the two direct candidates and precede the contextual ones; the
families and their internal order are otherwise unchanged.

## Evidence

* taxonomy tool: `tools/reliability/failure_taxonomy.py`
* unit contract: `tests/agent/test_failure_taxonomy.py`
* strategy unit contract: `tests/agent/test_coverage_and_clearance.py`
* phase gate: `bash tools/reliability/check_phase.sh --strict`
