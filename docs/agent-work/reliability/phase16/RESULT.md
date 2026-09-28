# Phase 16 - result

Status: **ready for review.** The through-via screen now asks every copper layer
a via spans and fails closed when it cannot know them; the re-screen drops from
four openings to one; the one remaining candidate was executed and its plan did
**not** close the connection, so nothing was accepted and the pointer is
unchanged.

Contract: [PLAN.md](PLAN.md).

## What changed

| file | change |
|---|---|
| `pcb_world/agent/zone_coverage.py` | `via_layer_span()`; `find_openings()` probes every spanned layer and deduplicates positions; `screen_openings()` refuses an edge whose span is unreadable; `EdgeScreen.layers_checked` |
| `tests/agent/test_zone_point_query.py` | interior-layer disqualification on a four-layer board, two-layer compatibility, no-zone board, bounded query count, no mutation |
| `tests/agent/test_zone_prefilter_unit.py` | span fails closed: no layer map, self-inconsistent map, board order |
| `tools/reliability/check_phase.py` | native floor 135 -> 138 |
| `docs/agent-work/reliability/phase16/{PLAN,RESULT}.md`, `HISTORY.md`, `CHANGELOG.md` | this phase |

No engine or wire file was touched, no footprint moved, no zone was deleted, no
clearance was relaxed and no gate was substituted.

## The fix

`via_layer_span(coverage)` reads `layer_map.board_layer_order` and its length from
the **engine**, and returns the human copper layers 1..N in that order; `None`
when the map is missing or disagrees with itself. `find_openings()` asks every
layer in that span for every corridor sample, and a sample is offered only when
every one of them resolved and none is foreign or mixed. The span is the whole
stack because the harness places its via with the router's own through-via action
— which the phase-14 replay confirmed when its findings appeared on a layer
outside even the endpoints' interval — and a full-stack check is a superset of
any partial via pair, so it can only refuse more openings.

Unknown stays fatal by design: an unreadable layer map, an inconsistent map, or
an unresolved zone on any spanned layer refuses the opening rather than skipping
the layer that might have blocked it. A new `layers_checked` field on the screen
records how many layers were actually asked about (0 when the span was unknown).

**The map is checked, not trusted** (review hardening). `max_layer` is converted
inside the same guard as the order — a missing, non-numeric or otherwise
unconvertible value is a refusal rather than a default — and length agreement
alone is not enough: a repeated layer, a non-copper id (the engine's ids are even
and non-negative), a negative id, or a map whose own `human_to_board` /
`board_to_human` converters disagree each return `None`. Every failure mode is a
refusal, never a smaller span. `find_openings()` additionally refuses a start or
target **human layer that is not in the proven span**, so a caller's impossible
layer cannot be reported as a measured opening; the screen reports such an edge
as `unknown` with the span it did prove.

The hardening is covered by unit tests for malformed `max_layer`, missing map
metadata, duplicate layer ids, non-copper and negative ids, disagreeing
converters, endpoints outside the span (finder and screen), and known-good two-
and four-layer maps. Re-running the screen over the same sixteen edges after the
hardening reproduces the pre-hardening result exactly (one opening,
`layers_checked 4`, board SHA256 equal to the accepted hash), so valid-board
behaviour is unchanged.

## Re-screen: four openings become one

Exact sixteen cross-layer refusal edges, accepted generation, the **same
lower-bound margin as before** (this phase changes coverage, not the clearance
numbers), via the tool — which also reports the board's SHA256 and the layer
count per edge:

| | before (two endpoint layers) | after (every spanned layer) |
|---|---:|---:|
| edges screened | 16 | 16 |
| edges with an opening | **4** | **1** |
| openings total | 4 | 1 |
| edges with none | 12 | 15 |
| layers asked per edge | 2 | 4 |

The predicted one is the one that survived: the three that disappeared were not
marginally clear, they were clear only on the layers the screen had asked about.

## The remaining candidate, executed

The surviving edge has no same-generation DRC-refusal evidence, so the generic
refusal-aware policy correctly does not promote its family candidate. It was
executed the honest way instead — the same generator and budgets with a wider
candidate window (`candidate_limit 3`) — from the exact accepted board, with a
fresh engine and session and zero planner requests. The authoritative fields were
read (`drc_delta`, `connected_before_refusal`), and every item identity was
resolved against the board's inventory.

| candidate | kind | outcome | reason | closed before refusal | copper | rollback |
|---|---|---|---|---|---|---|
| `direct_walkaround` | direct | routing_failed | connection_not_verified | n/a (never closed) | restored | verified |
| `direct_shove` | shove | routing_failed | connection_not_verified | n/a | restored | verified |
| **`zone_gap_via_*`** | zone_gap_via | routing_failed | connection_not_verified | n/a | restored | verified |

**The previously unexecuted opening is now genuinely executed**, and its plan did
not close the connection at all: there is no `drc_delta` because the gate was
never reached, `connected_before_refusal` is absent, and every attempt restored
its copper with the rollback verified. Nothing was accepted, so no promotion
ladder ran and the accepted pointer and hash are untouched (re-hashed after the
trial and identical).

## Would the deferred rule-query API help?

**Not for these sixteen edges, and this phase's evidence is the reason.** The
three edges whose plans *did* close and were refused on clearance and
hole-clearance findings no longer have a layer-complete opening at all, so a
rule-complete margin would have nothing to measure; and the one edge that does
have an opening fails on **connectivity**, not on any clearance — no rule value
can change that. The deferred `probeRules()` contract remains a correctness and
evidence improvement for any future opening screen, but it is not the binding
constraint on this edge set.

## Verification

| command | exit | result |
|---|---:|---|
| `bash tools/reliability/check_phase.sh --strict` | 0 | 488 unit + 138 native, no skips (after the review hardening) |
| `python tools/check_separation.py` | 0 | 4/4 checks; wire copies identical |
| `python tools/reliability/check_engine_patches.py` | 0 | 3 patches, 6 files byte-equal to the pin |
| `git diff --check` | 0 | clean |
| private re-screen via the tool | 0 | 1 of 16 edges with an opening, `layers_checked` 4, board SHA256 present |
| private trial, fresh session | 0 | 3 candidates executed, 0 accepted, all restored |
| accepted-generation sha256 | - | `6c4f8ab81b83...f593477cf1b`, unchanged |

## Limits and risks

* **The span is the whole stack**, which is right for this harness (its via is a
  through via) and deliberately conservative for any future blind/buried via: it
  can refuse an opening a partial via could have used, never the reverse.
* **The margin is still the old lower bound.** This phase fixed coverage, not the
  rule numbers, so an opening that survives is still "clear of foreign filled
  copper on every layer by the board's minimum plus the adopted copper radius" —
  not a rule-complete statement. Astra deferred the interface that would make it
  one.
* **The screen remains a measurement of loaded fills** (`loaded_unverified`), and
  a point result is not a path proof.
* **One edge is not a trend.** Sixteen refusal edges now screen to one opening,
  and that one fails on connectivity; the family remains an opt-in advisory with
  no demonstrated closure.
