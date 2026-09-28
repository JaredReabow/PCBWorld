# Phase 13 - exact-edge priority, a read-only screen, then a matched trial

Status: in progress (2026-09-28). Phase 12 is the accepted predecessor
([CHECKPOINT.md](../phase12/CHECKPOINT.md)); this phase's outcome is in
[RESULT.md](RESULT.md).

## What phase 12 left on the table

Phase 12 landed the measured via-opening family and found, in its own trial, that
a **net** pinned selection is not enough: the sweep spent its budget on
same-layer pairs of the pinned nets, so the family never even produced a
candidate. Its second limitation was budget: a matched pair of arms costs about
five minutes per edge on this board, so screening must come first and cost almost
nothing.

## Contract

1. **Select by exact edge identity.** A generic `RunnerConfig.priority_edges`
   pins the canonical six-tuple `observations.edge_key()` produces — direction
   tolerant, rounded to 3 decimals — ahead of net pins and ahead of the scan
   order. Nothing about which pairs exist changes; only which one is offered
   first.
2. **Screen before spending.** A generic, bounded, **read-only**
   `zone_coverage.screen_openings()` reports, per edge, whether the measured
   corridor has an opening, whether the edge is same-layer, whether the query
   could not answer, or whether the budget ran out before the edge was reached.
   A screening that ran out of room must never look like a screening that found
   nothing. No route is started, no checkpoint taken, nothing written back.
3. **Trial only what the screen found.** Transactional ON/OFF arms run only for
   edges with openings, with identical generators, sessions, budgets and
   ordering, and each arm on its own wall-clock budget so a slow arm cannot
   starve its control. Zero planner requests.
4. **Keep `zone_gap_via` off by default** until a controlled trial shows value.
5. **Promotion only through the immutable store** and only if something closes:
   fresh child, whole-board native DRC, terminal partition, complete pinned CLI
   against the canonical original. Otherwise the accepted pointer stays put.
6. **No engine or wire changes.** The phase-11 query is sufficient; anything that
   needed a new native accessor would come back to Astra as a proposal first.
7. **Public/private boundary.** Coordinates were redacted in phase 12. This phase
   redacts the board's own design-rule values from older public reports and
   history — narrowly, only the values that are unambiguously this board's rule
   file — keeping every original in private evidence, documenting the exception,
   and making no gratuitous edits to accepted reports.
8. **One report**: coverage, closures, pointer/hash, and exact limits.

## Evidence

* Selector: `pcb_world/agent/runner.py` (`priority_edges`,
  `_priority_edge_keys`, `_pair_priority_edge`), `observations.edge_key`.
* Screen: `pcb_world/agent/zone_coverage.py` (`EdgeScreen`, `screen_openings`,
  `screen_summary`) and the CLI
  `tools/reliability/screen_zone_openings.py`.
* Trial: private `phase13_routing/phase13_trial.py`.
* Phase gate: `bash tools/reliability/check_phase.sh --strict`; patch proof:
  `python tools/reliability/check_engine_patches.py`.
