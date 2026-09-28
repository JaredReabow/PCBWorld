# RESULT — phase 3: corrected integration and real V3 progress

Phase 3 closes the phase-2 review findings and the measured V3 bottlenecks in one
bundle. Phase 1 remains accepted; phase 2's documents are unchanged except where
this phase corrects a claim they made.

Contract: [PLAN.md](PLAN.md). Handover: [CHECKPOINT.md](CHECKPOINT.md).

| | |
|---|---|
| Workspace | `/Users/leo/Documents/PCBWorld-reliability` |
| Branch | `feat/agent-reliability-actions` (base `b3d62f5`) |
| Public gate | `bash tools/reliability/check_phase.sh --strict` → exit 0 (unit 237, native 37, no skips) |
| Private pilot | `/Users/leo/Documents/Helix_Control_V3_pcbworld_improved` |
| Git state | nothing committed, pushed or staged |

**Historical pilot record, acceptance pending:** the run report recorded six
deterministic closures (149 → 143 unrouted edges, 335 → 329 pad groups) and a
reopened DRC total of 8139 against a 8149 baseline. The former mutable-checkpoint
workflow did not prove immutable saved-artifact integrity or detailed reopened DRC
identity. These are recorded measurements, not acceptance claims; the candidate
must pass the hardened generation verifier before it can be called accepted. The
model closed no connections in that pilot.

## 1. What changed, and why

### 1.1 Layer and endpoint identity (review finding 1)

Phase 2 derived the mirror-id → human-layer mapping by sampling every track, via
and pad on the board and taking a majority — ~12 s on V3, and wrong in principle
(a board whose copper sits mostly on one layer would map an unrelated id onto it).
`KiCadEngine.layer_map` already exposed the mapping, so `LayerResolver.build` now
reads it and *verifies* it: up to two single-layer pads per layer are looked up
through the engine's own connectivity query, and a contradiction raises instead of
routing on an invented number.

Measured on V3: **resolver 0.01 s** (was ~12-14 s), mapping `{0:1, 4:2, 6:3, 2:4}`,
verified.

Two related defects went with it:

* `_choose_layer` fell back to "any layer with copper at this point", which can be
  another net's layer. `_anchor_layer` now takes the layer from the ratsnest
  anchor's own board id through the engine map, and for a spans-copper anchor it
  accepts the other anchor's layer **only** where the connectivity query confirms
  copper, falling back to a layer whose *net* the engine reports as this pair's
  net. Pairs silently skipped for an unresolvable layer: 2 → 0 on V3.
* `AgentSession.endpoint` matched pads by X/Y alone, so a pad on another layer —
  and therefore another net — was credited to the cluster under the probe. It now
  converts the pad's `PCB_LAYER_ID` to a human layer and matches on it (a negative
  layer means the pad spans copper). `tests/agent/test_native_layers.py` pins this
  on 2- and 4-layer boards, with overlapping XY on unrelated nets, through-hole
  pads and an inner-layer anchor.

The 0.25 mm `COINCIDENT_GAP_MM` heuristic — which declared every short cross-layer
pair "unsupported" without trying it — is gone. `COINCIDENT_EPS_MM = 0.02` now says
only "these two anchors are the same point", and every pair is attempted.

### 1.2 The queue (review finding 2)

`enumerate_net_pairs` capped per net and per scan *before* filtering, so a net
whose shortest pair was dead could hide its other endpoints, and a run could report
"no outstanding pairs" with 149 edges on the board. It is replaced by
`scan_net_pairs`, which returns a `PairScan`:

* every edge is classified (offered, below `min_gap`, unresolvable layer, caller-skipped, unroutable, over the per-net cap, over the global cap, with the layer error text);
* the queue is **round-robin across nets**, each net's pairs shortest-first, so no net can monopolise the budget;
* ordinary connections are offered before degenerate same-point cross-layer bridges (which are still attempted, just later);
* a pair whose plans are exhausted, or whose endpoint the engine refuses outright, is retired with that reason instead of being re-selected until the stall timer fires;
* **completion is the engine's own unrouted count.** An empty offered list with edges outstanding is `pairs_exhausted`, not `completed`.

Two measurement-driven consequences: a failed attempt is filed against the board
digest it was measured on, so a pair may be re-tried once after neighbouring copper
changes; and `attempts_for_pair` (not probe count) drives the per-net budget, so
probing a six-candidate set no longer looks like six attempts.

### 1.3 Probing cost

Candidates are probed **one at a time, best-first, stopping at the first accepted
plan** instead of evaluating the whole set. On a board where every connected probe
costs a full native DRC (~41 s on V3), that is the difference between one and six
DRCs per pair in the common case. Candidates the loop never reached are not marked
as tried, so they remain available later.

### 1.4 Planner budget and replies (review finding 3)

* The request offset is now **per planner instance**: a resumed run adds to the checkpoint's totals. (Reading the offset back out of the state made the first run persist `0` and every later run report only its own calls — the reproduced `10 → 1` bug.)
* `OpenAICompatiblePlanner.set_budget` is enforced **before every HTTP attempt**, so one `propose()` cannot exceed the run's remaining requests, tokens or time. A budget stop is `cancelled`, and the tokens already spent are reported.
* `reasoning_content` is never executed. Only an explicit `content` JSON object is an answer.
* A reply truncated by `max_tokens` escalates the output allowance **once** instead of re-sending the identical under-budget request, then fails non-retryably with its usage attached.
* `--max-new-tokens` defaults to the measured-working 8000 (1600 was measured returning an empty answer).
* The reply is bound to the request: another tool (`snapshot`), another pair, or a malformed waypoint is recorded as `planner_out_of_scope` and not counted as an attempt on this connection. The board digest captured with the observation is compared after inference; a plan for copper that has since changed is refused as `planner_stale_state` rather than executed with a fresh token.

### 1.5 Artifact and rule integrity (review finding 5)

* The former `_save_best` path staged and hashed files but overwrote fixed filenames individually; it did not provide immutable generations or one atomic resume-authoritative pointer. That earlier claim of atomic promotion is withdrawn.
* `resolve_inputs` resolves `<board>.kicad_pro` / `<board>.kicad_dru` when the CLI arguments are omitted, so they are hashed into the provenance and copied next to the artifact. A resumed run proofs the rules the engine loaded against the run's resolved rules **by content**, and aligns its rule context to the engine's own path — the pairing that made every resumed mutation fail closed with `rules_unavailable` is fixed and covered by a native test.
* The former post-run reopen was diagnostic only: it compared aggregate counts and recorded DRC total after the promotion decision. It did not gate promotion on detailed DRC identities.
* A final in-memory DRC regression changed the report status, but the earlier board file could already have replaced the previous checkpoint. The promotion-safety claim is withdrawn pending the generation pointer gate.
* A `--run-dir` that does not match the checkpoint is refused (exit 3) — and a *copied* checkpoint directory resumes in its new location, which is what the refusal message tells the operator to do.

### 1.6 Safety stops (review finding 4)

An unverified probe snapshot, or a session that quarantined itself, stops the run
(`session_unverified` / `session_quarantined`) instead of continuing to apply
plans. `DrcGate.seed` re-checks the rule context and drops the seed on a mismatch.
The earlier runner refused to save a dirty session, but that check alone did not establish durable promotion safety.

**Corrected:** the engine IPC transport had no per-operation deadline. This phase adds an owned-child deadline; the native strict gate must still prove the actual child is reaped and unrelated processes survive.

## 2. Verification

```bash
cd /Users/leo/Documents/PCBWorld-reliability
bash tools/reliability/check_phase.sh --strict      # exit 0: 237 unit + 37 native, no skips
PYTHONPATH=$PWD .venv/bin/python -m pytest tests/agent -q -o addopts=   # 274 passed
git diff --check                                    # clean
```

New coverage this phase: `tests/agent/test_native_layers.py` (2/4-layer maps,
overlapping XY across unrelated nets, through-hole pads, inner layer, spans-copper
anchors), `tests/agent/test_phase3_integrity.py` (resume totals across planner
instances, budget inside the retry loop, off-target replies, 2-value waypoints,
artifact sidecars, inferred sidecars, final-DRC refusal, fair queue, copied
checkpoint, operator stall patience), plus a native resume-can-still-mutate test
and the advisory-target regression carried from phase 2.

## 3. The V3 pass

Private artefacts: `/Users/leo/Documents/Helix_Control_V3_pcbworld_improved`
(`report_phase3.md`, `run_p3_*`, `provenance_phase3.json`). Board, project and rules
are the frozen baseline; only the sanctioned engine build is used.

| Stage | Attempts (cumulative) | Accepted | Unrouted | Pad groups | Tracks | Vias | Added relevant | Reopened DRC |
|---|---|---|---|---|---|---|---|---|
| Baseline | — | — | 149 | 335 | 6175 | 209 | — | 8149 |
| Deterministic pass (`run_p3_det3`) | 34 | 2 | 147 | 333 | 6178 | 209 | 0 | 8143 |
| Model pass (`run_p3_model`, 20 requests) | 98 | 3 | 146 | 332 | 6180 | 209 | 0 | 8142 |
| Broader deterministic pass | 160 | 5 | 144 | 330 | 6190 | 209 | 0 | 8140 |
| Continued deterministic pass | 230 | 6 | 143 | 329 | 6195 | 209 | 0 | 8139 |

All six closures:

Private route-level identifiers and coordinates remain only in the local V3 evidence archive. The public phase record retains aggregate counts and states that all six reported closures were deterministic walkaround candidates.

The historical report records that each closure passed the phase-1 in-memory transaction gate. The aggregate reopened total and zero in-memory delta do not prove detailed saved-artifact acceptance; that remains pending the hardened verifier.

### 3.1 Model usage in the pass (exact)

20 requests in total, which is the phase-3 ceiling for the first board pass:
49,902 prompt tokens, 59,778 completion tokens (58,904 of that reasoning), 24,192
cached. Outcome categories: `bad_response` ×1 (a truncation, escalated then
refused), `cancelled` ×9 (the request budget enforced inside the retry loop), and
the rest executed through the tool surface with `connection_not_verified`. Four
replies were `planner_out_of_scope` (the model answered with a `snapshot` call
instead of a plan for the requested connection) — refused, not counted as an
attempt on that connection.

Three requests were wasted before the rules-context fix (a resumed run refused
every mutation with `rules_unavailable`); that is included in the 20 and reported
rather than hidden.

**No model plan closed a connection.** Every executed model plan was refused by the
router's own connectivity check (`connection_not_verified`), and the six closures
above all came from the deterministic candidate set. That is the honest state of
the model contribution: the plumbing works end to end (observations, binding,
budget, execution through the tool surface, and the gate), and the plans it
produced for these near-field pairs did not route. Six deterministic closures
against zero model closures is the measurement, not a preference.

### 3.2 Where the pass stopped, and why

143 edges remain unclosed. The measured reasons, in order of frequency:

1. **The engine probes do not close the remaining pairs** (`connection_not_verified`) — the deterministic plans (direct/walkaround/shove/detours/layer change) and the model's plans were all evaluated and refused by the router itself under the transaction's own connectivity check.
2. **Engine endpoint refusals** (`endpoint_unknown`): the anchor's cluster holds no pad or via, so the transaction cannot establish the net. These are now retired in one probe instead of six.
3. **`drc_regression`** — the copper closed the connection but added relevant findings (typically `source-fill-clearance` against the board's existing zone fills, which are filled at a finer clearance than the recovered rule file requires
(`[rule values redacted]`); the baseline already carries 7488 such violations). This remains the largest structural obstacle to accepting short routes near planes, and the honest fix is a zone refill under the current rules — an engine capability that does not exist yet (see CHECKPOINT).

## 4. Cost model (measured, V3)

| Operation | Cost |
|---|---|
| Engine load | 0.7 s |
| Layer map + resolver build | 0.01 s (was ~12-14 s) |
| Ratnest scan, 149 edges | 0.12 s |
| Full native DRC | ~41 s |
| One accepted attempt | ~1-2 DRCs (early-stop probing) |
| One refused attempt | 0.1 s (structural) to ~4 min (six connected probes) |

## 5. Limits and corrections to earlier reporting

* Phase 2 stated that V3's near-field pairs "cannot be bridged by the engine". **That was wrong** — it was a threshold in the agent, not a property of the engine. Corrected here, with the closures as evidence.
* Phase 2's "+3 relevant findings on the artifact" was attributed to repeated-run variation. The former promotion gate did not compare detailed identities on the saved artifact; phase 4 now requires that exact check and defines no jitter tolerance.
* **No zone refill.** The `source-fill-clearance` mismatch is documented, not worked around: no rule was relaxed, no violation class ignored, and no count tolerance introduced.
* **No complete-coverage claim for incremental DRC.** `KiCadEngine.run_drc_incremental` exists in the engine, but its scoped pass skips the pad/graphic/zone sub-tests (documented in the engine's own provider); it was therefore not used as an acceptance gate. Full native DRC remains the only acceptance.
* One board and one rule file were exercised; the native gate's own coverage is synthetic.
* The former native call path had no hard operation deadline; phase 4 adds an owned-child deadline and awaits native strict validation.

## 6. Not claimed

No version tag, release, commit or push. No whole-board completion, no
routing-quality claim, no manufacturing-readiness claim. No private board, rules,
prompts, logs or credentials entered this repository.
