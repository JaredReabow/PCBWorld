# Phase 4 recovery boundary

## Objective

Make a routing checkpoint safe across native hangs, process crashes, partial
filesystem writes, and restart. A report may call a board accepted only when one
immutable generation containing the PCB and exact effective project/rules has
passed a fresh native reopen, detailed full-DRC identity comparison, connectivity
and geometry checks, and one atomic pointer change.

## Contracts

1. Each candidate is written into a unique generation directory. Its manifest
   hashes every file. `accepted_artifact.json` is the sole resume authority and
   is replaced atomically only after acceptance. It contains the corresponding
   run-state snapshot so a restart can reconcile a crash between pointer and
   `run_state.json` writes. Unpointed generations are harmless evidence.
2. Promotion requires a production verifier process that opens the saved PCB
   with its generation project and rules, proves rule-file identity, matches exact
   geometry digest and progress/connectivity values, and adds no detailed relevant
   DRC identity against the immutable source baseline. Any verifier error, timeout,
   malformed output, missing file, hash mismatch, quarantine, or DRC addition
   leaves the previous pointer active. Test doubles require an explicit injected
   verifier; they do not alter the production gate.
3. KiCad IPC operations use monotonic absolute deadlines shared by send, every
   partial receive, and owned-child cleanup. Startup, connect, and handshake use
   the same run-clamped budget. The runner refreshes the remaining-time allowance
   at every dispatch and candidate verifier; verifier process groups are isolated
   and reaped on timeout or cancellation. The runner refuses in-process KiCad
   mode, persists elapsed time and request spend, and resumes from the accepted
   generation.
4. Resume validates current board, effective project/rules, and engine build at
   the `RoutingRunner` boundary. Generation paths stay relative when a complete
   checkpoint directory is copied. Missing or tampered artifacts fail closed.
5. Attempt identity normalization is idempotent. Failed plans are scoped to the
   copper digest where they were measured while lifetime attempt evidence remains.
   Execution rotates its pair cursor. Endpoint layers, nets, and via spans must
   agree with the scheduled connection. A definite different nonzero endpoint net
   stops the run; an unreadable session also stops globally, while a verified
   track-only endpoint is recorded and retires only that pair. Distinct tiny gaps
   remain routable work.
6. Planner request reservations are persisted before dispatch; known usage is
   retained when a later retry fails, unknown spend is labeled, and transport
   timeouts and retries fit inside the remaining budget.
7. Public evidence contains aggregate counts only. Board-level net identifiers,
   coordinates, prompts, logs, and credentials stay in the private work area.

## Out of scope

No new routing or zone-fill algorithm, no additional routing/API requests, no
firmware/version bump, and no push/commit. The previously authorized zone refill
and route-quality work belongs to the following phase after this boundary passes.

## Acceptance gates

- Regression tests prove immutable pointer safety, complete sidecar hashes,
  generation/pointer/state reconciliation, detailed DRC identity gating, source
  baseline migration, direct-API provenance, missing/tampered checkpoint refusal,
  hung-child reap with unrelated sentinel survival, child crash recovery,
  slow-dribble deadline enforcement, bounded verifier-tree cleanup, usage
  reservations, stale-plan retry, fair execution, pair-local unsupported
  endpoints, planner quarantine exits, and progress/pointer agreement after a
  later final-gate rejection.
- Run `bash tools/reliability/check_phase.sh --strict`; native coverage must run
  without skips.
- Migrate and reverify a copy of the historical pilot checkpoint. Do not modify
  its saved reference or the original EasyEDA source project.
- Independently compare the saved candidate against source DRC identities and
  report whether it qualifies. Then run the installed KiCad check where available.
- Keep CHECKPOINT and RESULT explicit about remaining acceptance blockers.
