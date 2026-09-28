# RESULT — phase 4 recovery boundary

Status: **recovery boundary passes its strict native gate; KiCad CLI cross-check
shows a DRC identity mismatch that keeps board-level acceptance pending.** See [PLAN.md](PLAN.md) and
[CHECKPOINT.md](CHECKPOINT.md).

## Implemented

- `pcb_world/agent/artifacts.py` stores complete immutable generations and a
  hash-checked, atomic `accepted_artifact.json` pointer. The pointer carries the
  attempt/accounting snapshot needed to recover if `run_state.json` was not yet
  updated. A final native DRC regression restores the pointer captured at run
  start.
- `tools/reliability/verify_saved_artifact.py` reopens the exact saved board in a
  fresh process, proves its rules, compares its geometry digest and progress, and
  compares detailed relevant DRC finding identities before promotion. A legacy
  checkpoint with no saved source fingerprint rebuilds that fingerprint from the
  immutable source PCB/project/rules.
- `pcb_world/engine/router_client.py` shares one monotonic absolute deadline across
  each request's send, partial header/body reads, and child cleanup. Startup,
  connect, and handshake use the same caller budget; a timeout provider refreshes
  remaining run time on every later native dispatch instead of reusing the budget
  captured when the engine opened. The runner rejects in-process engine mode.
- Fresh saved-board verifiers and legacy source-DRC capture run in isolated process
  groups. Timeout or cancellation kills and reaps the verifier and its engine-server
  descendants without signaling the runner or unrelated processes. Their total
  timeout and native call timeout are both clamped to remaining run time.
- Resume checks provenance at the core API boundary, reconciles the pointer and
  state snapshot, supports relative generation paths in a copied run directory,
  and rejects a missing named artifact. Quarantined sessions are not queried in
  final accounting.
- Final DRC rejection restores the progress tracker as well as the artifact
  pointer. Saved-board progress, report acceptance, and artifact readback refer to
  that active generation; previously committed candidates remain in attempt
  history with an explicit rejected final disposition and separate diagnostic
  progress/DRC evidence. A saved-artifact gate failure follows the same progress
  rule and leaves its candidate generation available for diagnosis.
- A planner tool failure that quarantines the session stops even when the response
  is unsuccessful. Verified track-only endpoints are retired per pair so a single
  unknown cluster does not block other routable pairs; definite net mismatches
  and unverified sessions still stop globally.
- Fixed waypoint key round-tripping, copper-generation-scoped failed-plan
  retries, persisted pair cursor, net-checked spans-copper layer selection,
  scheduled endpoint net validation, blind/buried via span membership, and the
  1 nm numerical coincidence tolerance.
- Planner errors retain cumulative usage, unknown terminal spend is marked,
  request reservations are persisted before dispatch, and network timeouts,
  retry delays, and output allowance are bounded by remaining budget.
- Removed precise private pilot net IDs and anchor coordinates from the public
  phase-3 result. Downgraded its former promotion claims to historical report data
  pending hardened revalidation.

## Validation

`bash tools/reliability/check_phase.sh --strict` passed: **260 unit tests and all
37 native tests, zero skips** (exit 0); `git diff --check` passed. A resumed-run
regression confirms that rejecting a later candidate restores the prior pointer,
progress tracker, report progress, and cumulative accepted count (one prior closure). The native
runner tests exercised the fresh-process saved-artifact gate.

A copy of the historical model-run checkpoint migrated to an immutable
generation with **zero new attempts** (lifetime count remained 230). The copied
candidate board hash matches the preserved pilot reference. The production
verifier reopened its saved PCB, proved the copied project/rules context, matched
the geometry digest and progress, and found **zero added relevant DRC identities**
against the source identity set. The engine report showed 8,139 total / 7,930
relevant findings on the saved candidate. The original reference and EasyEDA
source remained untouched.

An additional read-only run with the installed KiCad CLI reported 2,455 findings
on the source and 2,451 on the candidate. Comparing identities by rule/type,
severity, and item UUIDs found **23 added and 27 resolved findings** on the
candidate. Therefore the candidate passes the hardened PCBWorld engine gate but
does **not** qualify as a no-new-findings board under the installed KiCad CLI.
The count decrease alone is not acceptance. CLI reports remain in the private
recovery copy; no board save or zone refill was requested.

The follow-up comparison found that the reports encode each item position as an
`{x, y}` object in millimetres. After rounding positions to 0.001 mm, 2,367 of
the 2,428 shared UUID identities retained their reported positions and 61
changed position. The 23 additions are clearance errors; the 27 resolutions are
23 clearance errors and four track-dangling warnings. None of those 50 changed
identities has an exact matching position signature under the same rule and
severity. This is not explained by UUID renumbering alone. Because CLI item
positions do not by themselves prove whether geometry or serialization caused
each displacement, board-level acceptance remains blocked. The private
`cli_diagnosis.json` records the exact source/candidate invocations, hashes,
versions, and aggregate comparison without copying coordinates into this
public result. Both boards used byte-identical project and rules sidecars
(SHA-256 prefixes `08dcb693cf8801d5` and `ce92dd7b83822c4f`); the CLI reports
identify KiCad 10.0.6. The engine build provenance hash is `2613fb07`.

## Known limits

The isolated saved-artifact verifier adds one full DRC run per proposed promotion.
The PCBWorld engine and installed KiCad CLI still disagree on detailed identities.
The CLI position comparison rules out UUID churn as the sole explanation, but
the serialized reports cannot settle geometry versus item-serialization effects.
No rule or violation tolerance has been added. The following routing-quality
phase remains separate and is not claimed complete.
