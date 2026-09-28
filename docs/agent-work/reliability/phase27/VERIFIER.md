# Phase 27 - the tightened retention verifier (T27A)

Status: **implementation complete and dry-run against the frozen phase-26
candidates.** This phase changes the *verifier*, not a board: it pins the four
boards a retention decision reads, decides every board-level condition on the
reopened post-refill bytes, refuses on an incomplete or unpinned input, removes
the single-pass reading of the native condition, and replaces the campaign's
component-keyed attempted set with a stable route-offer identity. The accepted
pointer did not move, no board or rule changed, and nothing was promoted.

Predecessors: [phase 26](../phase26/RESULT.md),
[phase 25](../phase25/RESULT.md). Requirements R1 and R2 of the ledger.

## 1. Why the verifier needed tightening

Phase 26's campaign took its decision on the retained bytes, but the code path
that read them did not insist on them. Three weaknesses were reachable by a
future campaign:

* a board could be read without an expected hash, so a caller could measure
  "whatever is on disk" and call it the pinned input;
* an incomplete native terminal capture was usable - the comparator would
  report a smaller delta rather than refuse - so a partial capture could read as
  a clean result;
* the native DRC condition was one pass in one engine instance. Phase 26
  measured the reporter moving up to three "items shorting two nets" identities
  between instances on byte-identical boards, so a single favourable pass was
  not evidence, and neither was a run whose own instances disagreed with each
  other: counting the pairs alone lets a favourable instance hide an addition.

Separately, the campaign keyed its attempted set on the *component* pair, and a
component is a digest of terminal membership. A merge changes that digest, so
three of the eighteen links were offered again after refusing. The receipt was
in the campaign's own record, not in a hypothetical.

## 2. The four-board input contract

Every decision names four boards by role:

| role | what it is |
|---|---|
| accepted generation | the board the restoration target is measured against |
| refilled baseline | the frozen board the campaign started from |
| immediate parent | the board the attempt was built from |
| post-refill candidate | the saved, legally refilled, reopened candidate |

Each role must supply an **explicit expected board hash**. The loader refuses,
with a stable code in a JSON packet, when the directory, the board, or the
`board.kicad_pro` sidecar is absent, when the hash does not match, when no
expected hash was supplied, when the engine cannot open the board, or when the
native terminal capture comes back incomplete. All four inputs are validated
before any engine is opened, and all four are re-hashed after the measurement: a
board that moves while the verifier runs refuses as `prepost_divergence` rather
than being half-measured.

An optional fifth input pins the pre-refill snapshot, so a decision bound to the
pre-refill bytes instead of the refilled bytes is detectable rather than silent.
On the frozen phase-26 steps the refill changed the bytes on one step and left
the other five byte-identical - both are legal, and the contract records which
happened instead of assuming.

The board hashes pin the copper, but the DRC verdict also depends on the rule
file and on each project sidecar, both of which the engine reads. The verifier
therefore hashes the rule file and all four `board.kicad_pro` sidecars before it
measures and again after, and refuses as `policy_input_changed` if any of them
moved. A `--expected-rules-sha256` argument optionally pins the rule file up
front as well. This matters because the engine's own context identity records
the rule path's size and mtime per pass, but nothing in the comparison path
compares those identities against each other - a rule file swapped between the
parent's instances and the candidate's would otherwise change the decision while
every board hash stayed stable.

## 3. What is decided, and on which bytes

The five board-level conditions - anchor closure, terminal identity and net
membership, no new fragmentation, strict restoration-debt reduction, and the
native DRC delta - are computed from a separate engine that opens the
**reopened post-refill candidate** and the immediate parent. Nothing is read
from an in-session snapshot, and the anchor closure is re-derived from the
candidate's own cluster rows.

The sixth condition is the complete pinned CLI. It is a *binding*, and it binds
everything the gate report is evidence about:

* the gate's candidate hash must equal the post-refill candidate hash and its
  source hash must equal the immediate parent hash;
* its `cli_sha256` must equal the digest of the pinned CLI binary, and its
  `provider_sha256` the digest of the kiface that binary loads, both re-derived
  from the pinned paths on this host - or from an explicit
  `--expected-cli-sha256` / `--expected-provider-sha256` envelope when a caller
  must judge a report against a recorded provenance instead;
* its staged candidate and source rule digests must both equal the rule file the
  verifier itself measured under, with `staging.identical` true;
* the project-sidecar digests the gate recorded - `candidate_project_sha256`
  and `reference_project_sha256`, which the frozen runs carry in the terminal
  proof's `binding` block - must equal the project sidecars of the post-refill
  candidate and the immediate parent that the verifier hashed. The board hashes
  alone would let a gate that ran with a different project file be replayed;
* and it must report `ok`, `report_complete` and `staged_inputs_match`.

The project pins are read from that nested schema only: `staging` in the gate
summary, or `proof.binding` / a top-level `binding` in the terminal proof (also
reachable through `--proof-evidence`). They are never taken from a flat key
somewhere else in the file, and the two sides are not interchangeable -
`candidate_project_sha256` binds the candidate sidecar and
`reference_project_sha256`/`source_project_sha256` the immediate parent's.

Every binding that is supplied has to agree. The verifier gathers each family -
candidate board, parent board, candidate project, parent project - from the gate,
its `staging`, its `proof` and `proof.binding`, and from any external
`--proof-evidence`; two different values for one family refuse as
`<family>_binding_conflict` rather than the first one silently winning. An
external proof that carries project digests must also name **both** boards it
measured, and those hashes must equal the decision's pinned candidate and parent:
without them it refuses as `proof_board_binding_missing`, and with different ones
as `proof_board_binding_mismatch`. Matching project sidecars are not evidence
about this candidate if the proof is about some other board pair.

Any of those missing or mismatched fails closed, so a report produced by a
different CLI, a different provider or a different rule file cannot be replayed
as this candidate's verdict. Without bound CLI evidence the sixth condition is
false, so the verdict refuses while still reporting the board conditions. The
unconnected-endpoint waiver the phase-26 rule carries survives only inside that
bound gate; the verifier itself adds no new waiver.

The local binary digests are authoritative and always reported next to the
evidential ones (`local_*` versus `evidence_*`). An unreadable or missing local
CLI or provider is a refusal (`cli_unavailable`, `provider_unavailable`), never
an assumption. `--expected-cli-sha256` and `--expected-provider-sha256` are
**additional assertions about the local files**, checked against them and never
substituted for them: an assertion that agrees with the local binary is
harmless, and one that does not is a request to replay a recorded provenance -
which refuses (`provenance_replay_requires_diagnostic`) unless the caller
declares `--diagnostic-replay`, and is diagnostic-only when it does.

## 4. No waiver for unstable shorting

Both the immediate parent and the post-refill candidate are now measured in
several native engine instances - one instance per pass, because the instability
lives between instances - and the condition is stated over all of them.

The instances have to be *actually* distinct, which is not automatic. The engine
client parks its IPC server for reuse: `router_client._MAX_IDLE` is 2 unless
`KICAD_ENGINE_REUSE` says otherwise, and it is read once at import time. A
process that has already imported the client - or that inherited
`KICAD_ENGINE_REUSE=1` - cannot be made fresh by setting the environment
afterwards, and a second open in that process can be answered by the same
server. Each measurement therefore runs in its own worker process that disables
parking before it imports the client and then *verifies* the client really has
no pool, and each worker reports the server pid it used. The verifier records
those pids, requires every DRC pass to name a distinct server, and refuses with
`engine_reuse_active`, `server_identity_unavailable` or `server_not_fresh` when
it cannot show that. It changes no global pool state of its caller.

The condition itself:

* **every instance pair must add nothing.** Any parent-by-candidate instance
  pair that reports an added relevant identity refuses the step, whatever any
  other pair showed. The full matrix is kept in the evidence, including the
  pairs where a single-instance rule would have passed;
* **each board's own instances must agree.** Every instance of the parent must
  report the same relevant findings, and so must every instance of the
  candidate. The comparison is over the findings *and* their multiplicities, so
  an extra row for an identity that was already present - the "1 or 4" shape -
  is a disagreement. A board whose instances disagree is an unstable
  measurement, and no comparison drawn from it can be trusted, so the step
  refuses. This is the case a favourable-rerun rule would have hidden;
* a relevant identity some candidate instance reports and no parent instance
  reports is reported as an unexplained addition; an identity a pair called
  added that the parent also reported in one of its own instances is reported as
  movement. Both are diagnostics on top of the refusal - neither is a waiver,
  and neither is what makes the step pass;
* fewer than two instances per board cannot separate an addition from the
  reporter's own movement, so that is a refusal too. A diagnostic-only run can
  override it explicitly with `--allow-single-instance`, and such a run **never
  retains**: the conditions are still measured and reported, but the verdict is
  `diagnostic`, `retain` is false, and the process exits 15 rather than 0. That
  holds even when every condition reads true, which is the case the flag exists
  for - measuring, not deciding.

The refusal packet names its cause: an instance disagreement on either side, an
unexplained added identity, or a pair-level added identity. No class is waived
and no instance is dropped, and no pair-set gate is introduced: the refusal is
stated in terms of the rows the reporter actually emitted.

## 5. Stable route-offer identity

An offer is now identified by the copper the router would actually join:
`(anchor_a, anchor_b, layer, strategy)`, with the two anchor pads canonically
ordered and the strategy named from the declared ladder. The identity
deliberately does not carry a component label, because a label that changes with
a merge cannot suppress a repeat.

Two properties follow, and both are tested:

* a repeat of an identical offer - same anchors, same layer, same strategy - is
  suppressed, whatever the component labels did in between;
* a different offer is never suppressed: a different anchor pair after a merge,
  a different layer, or a strategy that has not run yet is lawful and stays
  available.

Link repeats and strategy repeats are reported separately, because they mean
different things. A step can re-offer a link whose anchors were already offered
and still carry a strategy that has never been run for it; that is a new
strategy offer, not a repeat, and the report says so rather than labelling the
whole step as refused-before.

Re-judged against the frozen phase-26 record, the eighteen link records carry
fifteen distinct identities. The three link repeats sit at steps 7, 8 and 18,
first offered at steps 4, 5 and 4, and all three are exactly the re-offers the
component-keyed set missed - it would have suppressed none of them. Every one of
those three re-runs the same five strategies it had already run, so they carry
no new strategy offers at all; no other step repeats a strategy it had already
tried, and no distinct offer is suppressed.

## 6. Dry run against the frozen phase-26 candidates

The six frozen phase-26 retained candidates were re-verified with the tightened
verifier, three native engine instances per board and nine instance pairs per
step, with the frozen CLI gates bound to the same bytes. The accepted generation
and the refilled baseline were re-hashed and match the phase-26 record.

The result is the honest one, and it varies between runs against the *same
frozen boards*. The measurement was repeated; some runs held all six steps, and
others refused. The captured refusing run is kept in the private evidence and
refused one step, in this shape:

| what the verifier reported | the step it reported it for |
|---|---|
| the parent's instances disagreed with each other | the unstable instance reported the extra triple |
| the candidate's instances disagreed with each other | the extra triple appeared on the other side too |
| three instance pairs added an identity | and the added identity was not explained by any parent instance |

The five other steps in that run held. An earlier run of the same measurement,
before the consistency test compared multiplicities rather than membership,
refused four steps - the disagreement moves between instances and between runs,
which is the point. The visible signature is always the same: per-instance
relevant totals of 313 or 316, an exact extra triple in the "items shorting two
nets" class, and never a change to any other class.

That is exactly the shape the fail-closed rule exists for, and it is why no
favourable run is selected: on the current reporter some historical retained
steps cannot be re-confirmed at all, and the verifier says so instead of
averaging, rerunning or dropping the instance that disagrees.

The verifier does **not** add a pair-set gate. The refusal is at the level of
the rows the reporter emits - an identity, or a higher multiplicity of an
identity, that a pair or an instance comparison shows as new - so it fails
closed on the current row-level instability without inventing a second,
unreviewed rule about what the pair set should have been.

Re-run after the provider rebuild, with no waiver in the environment, all six
frozen steps refuse on the CLI condition rather than on the native one: their
gates name the pre-rebuild provider digest, this host now has the rebuilt one,
and a report produced by a different provider is not evidence about this host.
Judged as a diagnostic replay of the recorded provenance, the same six steps
report five of six conditions and exit 15 on every step - no retention, by
design. That is the honest state of the frozen campaign: its CLI evidence is
stale with respect to the repaired binaries, and the gate says so instead of
waiving it.

Superseded evidence: `phase27_verifier/evidence/dryrun_envelope.json` is the
earlier run that bound against the *then-current* provider, before that rebuild.
It is kept for provenance only, it is **superseded**, and it is not a retention
proof for the present binaries. The live post-rebuild results are
`dryrun_live_stale.json` (no waiver, all six refuse) and
`dryrun_envelope_diagnostic.json` (diagnostic replay, exit 15).

The verdict always records which measurements ran with the build-provenance
guard waived by the caller's environment (`engine_measurement.*.allow_mismatch_env`),
so a reader can tell a measurement of the current tree from one of the same
router binary under edited sources.

## 6a. What may and may not retain

Three things make a run **ineligible to retain**, and each is decided by the
process that measured, not by what the caller does afterwards:

1. **The router build-provenance waiver.** If `PCBWORLD_ENGINE_ALLOW_MISMATCH=1`
   was in force when the native module loaded - captured in the worker before
   any `pcb_world` import and re-read at load - the run is diagnostic. Clearing
   the variable later changes nothing, and it does not matter that the stamp
   happens to match; a waived process cannot certify a promotion
   (`engine_allow_mismatch_enabled`).
2. **The stamp itself.** The worker re-derives the comparison the engine's guard
   makes - the build's `ENGINE_CPP_HASH` against this tree's C++ content hash -
   so the guard's own downgrade-to-warning cannot be the only thing standing
   between a stale router and a retained board. A missing stamp refuses
   (`native_stamp_missing`); a mismatched stamp refuses
   (`router_provenance_stamp_mismatch`) unless the waiver above already made the
   run diagnostic.
3. **A recorded-provenance replay.** Asserted CLI/provider digests that are not
   this host's binaries are a diagnostic act (`recorded_envelope_replay`), and
   they require `--diagnostic-replay` explicitly.

A diagnostic run reports its conditions honestly and never retains: `verdict`
is `diagnostic`, `retain` and `retention_eligible` are false, and the process
exits 15 even when every condition reads true. `--allow-single-instance` and
`--diagnostic-replay` are the only ways in, and both are recorded in
`diagnostic_reasons`.

Refusal codes are specific, and `engine_error` is reserved for a real engine
fault - an argument or an evidence file never borrows it. Inputs refuse as
`dir_missing`, `board_missing`, `project_missing`, `expected_hash_missing`,
`hash_mismatch`, `unknown_role`, `input_missing`, `prepost_divergence`,
`rules_missing`, `capture_incomplete`, `native_passes_insufficient`, and - for
the two evidence files - `cli_evidence_missing`, `cli_evidence_malformed`,
`proof_evidence_missing`, `proof_evidence_malformed`. Provenance refuses as
`cli_unavailable`, `provider_unavailable`, `rules_hash_mismatch`,
`provenance_replay_requires_diagnostic`, `native_stamp_missing`,
`router_provenance_stamp_mismatch`, `candidate_board_binding_conflict`,
`parent_board_binding_conflict`, `candidate_project_binding_conflict`,
`parent_project_binding_conflict`, `proof_board_binding_missing`,
`proof_board_binding_mismatch`; sampling refuses as `engine_reuse_active`,
`server_identity_unavailable`, `server_not_fresh`, `worker_failed`; policy drift
refuses as `policy_input_changed`; anything genuinely unexpected refuses as
`unexpected_error`. Every refusal packet carries `retain: false`,
`retention_eligible: false`, `verdict: "refuse"` and its `refusal_codes`.

Two consequences for the next campaign. A step whose four boards include an
unstable instance cannot be certified at all until the instability is
understood; and a campaign that wants to retain a join in that condition needs
either a repaired reporter or a reviewed policy decision - not a client-side
waiver. Neither the board nor the rule changed here, and nothing was promoted:
the dry run read the frozen boards and wrote only its own verdicts.

## 7. Guards

| | |
|---|---|
| accepted pointer, accepted generation, canonical original | unchanged |
| phase-24 refilled board, both phase-25 boards | unchanged |
| all six frozen phase-26 candidate boards | unchanged |
| original EasyEDA project | unchanged |
| public document | leak-scanned for every private token, coordinate-like decimal and UUID |
| planner calls / paid API calls | 0 |
| staging, commit, push | none |

## 8. Verification

| command | exit | result |
|---|---:|---|
| `phase27_tests.py` | 0 | 92 tests; the engine-backed ones run in fresh processes and are skipped when another task's edits trip the router build guard |
| `phase27_dryrun.py --passes 3` (no waiver, live provenance) | 0 | six frozen steps re-verified, nine fresh server processes each; all six refuse on the stale provider digest |
| `phase27_dryrun.py ... --diagnostic-replay` | 0 | the same six as a diagnostic replay: five of six conditions, exit 15, no retention |
| `phase27_guards.py` | 0 | every frozen hash unchanged, public document clean, `git diff --check` clean |

A one-instance diagnostic was also run against a frozen step whose six
conditions read true: it reported `all_conditions: true`, `verdict: diagnostic`,
`retain: false` and exit 15, which is the contract the flag is supposed to have.

## 9. Integration contract for T27C

The next routing campaign must call the tightened verifier rather than
re-implementing it:

1. `phase27_verify.py` with `--accepted-generation-dir`,
   `--refilled-baseline-dir`, `--parent-dir` and `--candidate-dir`, each with its
   `--expected-*-sha256`; `--rules` (and optionally `--expected-rules-sha256`),
   `--anchor-a`, `--anchor-b`, `--layer`, `--drc-passes`, `--cli-evidence`,
   `--out`. Pass `--proof-evidence` with the gate's terminal proof JSON so the
   project-sidecar binding can be checked. `--expected-cli-sha256` and
   `--expected-provider-sha256` are assertions about the local files; when they
   do not match them the run needs `--diagnostic-replay` and can never retain.
   Exit 0 means all six conditions hold, the run was eligible, and neither the
   waiver nor a diagnostic mode applied;
   exit 10 means a board condition refused; exit 12 means an input, an engine,
   the CLI binding or a policy-input drift refused; exit 15 means the run was a
   diagnostic one and can never back retention. Always branch on `retain` in
   the verdict, never on `all_conditions`.
2. A committed attempt is saved, legally refilled and reopened **before** the
   verifier is called; the candidate hash handed to the verifier is the refilled
   board's, and the same hash must be the one the CLI gate reports.
3. The attempted set is the stable offer identity, not the component pair. A
   campaign that keeps a component-keyed set will keep re-offering refused
   links, and its record should be re-judged with the identity report before
   another budget is spent.
4. The campaign must keep its evidence private: the offer identities carry pad
   identifiers and geometry, so only aggregates belong in a public account.
5. The native condition needs at least two engine instances per board, and it
   refuses both an added identity in any instance pair and any disagreement
   between a board's own instances. A campaign that meets an unstable board
   must record the refusal and escalate; rerunning until a favourable instance
   appears is exactly what this rule removes, and no client-side waiver should
   be added to get past it.
