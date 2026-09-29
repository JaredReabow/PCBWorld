# Phase 32 (T32P) - the bounded-transaction ledger and the interruption guard, promoted to the public harness

Status: **correction 2 applied (rework round 2 of 2); focused and strict harness
green; ready for independent re-verification and acceptance review by the
appointed reviewer.** Sol high reviewed the first revision and returned
REQUEST_CHANGES on the publication API (section 4a), then reviewed correction 1
and returned REQUEST_CHANGES on one remaining gap in the interrupted-finalization
path (section 4b). The task was dispatched for Astra's acceptance; the workflow
ledger records the user replacing Astra with Sol high on 2026-09-29, so
acceptance belongs to whoever the ledger then names. Nothing here accepts its own
work, and nothing here is committed or pushed.

Owner: task T32P, requirements R1/R2/R3. Owned write paths:
`pcb_world/agent/ledger.py`, `pcb_world/agent/interruption_guard.py`,
`pcb_world/agent/__init__.py`, `tests/agent/test_ledger_unit.py`,
`tests/agent/test_interruption_guard.py`, `tools/reliability/check_phase.py`,
`README.md`, `pyproject.toml`, appended entries in `HISTORY.md` and
`CHANGELOG.md`, and this page. Everything else in the tree, including the
uncommitted work of earlier phases and the task's own workflow ledger, stands
exactly as it was left.

## 1. What this task is

Two libraries that were accepted in the work that preceded this phase - a budget
for native routing transactions, and a fail-closed guard for a campaign that was
interrupted - existed only inside private, board-specific campaign code. This
task promotes them into the public harness as **generic, engine-free modules**
that carry no board path, no campaign layout and no task identifier.

It does **not** copy the private campaign. The campaign driver, its integration
harness, its CLI and its test suite all carry a real workspace layout and
campaign-specific routing, and they stay private; only the two libraries cross
over, with their logic preserved. The symbol-level mapping and the frozen source
digests live in this task's private evidence packet, not here.

No board, rule, project, accepted pointer, frozen input or routing budget was
read for routing, reset or re-bound, and no routing was run.

## 2. The ledger (`pcb_world.agent.ledger`)

A routing campaign spends *native transactions* - one engine call that moves
copper - and the budget is a rule, not a target. The module is the book that
makes the rule hold across process boundaries.

* **Four hard ceilings**: five native transactions per stable logical link,
  twelve distinct anchor pairs per link, twelve links per campaign and sixty
  native transactions per campaign. A caller may ask for a *tighter* value and
  never a looser one, and every ceiling is re-validated at construction, when a
  ledger is restored from disk, and again on every reservation and pair offer -
  so a raised ceiling in a persisted file or in a mutated object is refused
  rather than honoured.
* **Charged before the call, cumulative across resumes.** The charge is written
  through to disk *before* the caller issues its native call, so a crash inside a
  transaction still charges that attempt on the next resume. Charges are never
  reset, and a resumed run finds the same budget because a link's identity is the
  canonical, order-independent pair of component identities with the engine's net
  renumbering normalised away.
* **Symmetric aliases, merged conservatively.** Declaring two component names
  equivalent unions their budgets - counters add, anchor sets union, nothing is
  reset - and a merge that would break a ceiling or combine two different
  retained joins is refused with the equivalence itself rolled back, so a
  swallowed refusal cannot leave one logical link able to spend twice.
* **Reconciliation is per link, not per total.** A record that charges a link the
  ledger does not hold, that accounts for a different count on a link, that
  leaves charges parked on a link no record visited, or whose declared totals
  disagree with the ledger is refused. A total-only check accepts 5+1 rewritten
  as 1+5; this does not.
* **The persisted schema tags are carried unchanged** (`t30l-link-ledger/2`, with
  `/1` still refused by name). A rename would silently re-interpret a spent
  counter, so a ledger written by the accepted private limiter still loads here.

## 3. The guard (`pcb_world.agent.interruption_guard`)

The limiter bounds a *count*. It does not decide whether a ledger it is handed is
a legitimate continuation. The guard closes that gap, in two clearly separated
layers.

**The pure layer** (`charge_attribution`, `classify_ledger`, `preflight_ledger`,
`completed_record_gate`, `quarantine_note`) reads a ledger object and returns a
verdict, and writes nothing. It refuses a *fresh* invocation whose ledger already
carries charges, links or anchor pairs without a campaign binding (the shape a
killed launch leaves, where a continuation would otherwise take the fresh path
and publish a record its own validator can never resume); it refuses a bound
ledger handed to a fresh run; and it reconciles a bound record against the
ledger's charges cumulatively **and** per canonical link, so a charge the record
cannot account for makes the ledger `quarantined` and explicitly incomplete.

Its flags are deliberately *low-level*. A `resume_safe` or `complete` from these
functions says the amounts add up; it is not an identity check and not permission
to route. They are therefore **not** re-exported from `pcb_world.agent` as an
authorization API, and the module docstring says so.

**The composed, file-facing layer** -
`interruption_guard_preflight_campaign` - is the only function here that reads
real files, and it is the one whose verdict carries an identity claim. It runs
the whole read-and-validate phase ahead of the caller's first write and ahead of
any engine:

1. read the ledger the run would drive, requiring the file to exist for a resume;
2. read, parse and **hash the record's exact bytes**;
3. validate the record through the limiter's own validator;
4. **bind identity**: the record's named ledger must be the ledger being
   preflighted, and the ledger must be bound either to this record
   (`current-record`) or to this record's own declared predecessor at the head of
   the binding chain (`interrupted-finalization`, the crash between the record
   write and the binding);
5. **hash the parent board's actual bytes** and require them to equal the digest
   the record declares;
6. **stage alias declarations in memory** (merging, never writing);
7. **reconcile**, last, so identity is established before any accounting verdict
   is read.

Every refusal raises one type, `CampaignPreflightRefusal`, before the caller's
first `mkdir`, first write and first engine open; the function creates no
directory, writes no file and opens no engine, and staged aliases are left in
memory.

**Publication** is likewise one guarded step. `completed_record_gate` re-validates
the whole cumulative and per-link history before a record is published or bound.
`publish_completed_record` then verifies the two things the gate cannot see - the
record's named `ledger_file` must be *this* ledger's own path, and the parent
board's actual bytes must hash to the digest the record declares, both through
the same helper the preflight uses - and then verifies the lineage against bytes
rather than a claim: a continuation must supply `previous_record_path`, the file
of the record it continues, whose digest must be either the ledger's current
binding or an interrupted finalization the chain can genuinely accept (that
record's own declared predecessor is the binding, and the binding is the head of
the chain). A digest already in the chain at an earlier position is refused as an
old record, and a digest the caller invented has no file to present. `bind=False`
is refused outright, and **every** refusal is decided before the first `mkdir`
and the first write, so a refused publication leaves the filesystem
byte-identical. The one failure that ordering cannot remove - a crash between the
record write and the binding - is not lost: the composed preflight accepts it as
`interrupted-finalization`.

## 4. Verification

| # | command | exit | result |
|---|---|---:|---|
| 1 | `pytest tests/agent/test_ledger_unit.py tests/agent/test_interruption_guard.py -o addopts=` | 0 | **49 passed** (21 ledger, 28 guard) |
| 2 | `pytest --collect-only` over the native group | 0 | **146 tests collected** |
| 3 | `pytest --collect-only` over the unit group | 0 | **689 tests collected** |
| 4 | mutation battery over both modules | 0 | **39/39 mutations caught** (19 ledger, 20 guard) |
| 5 | `bash tools/reliability/check_phase.sh --strict --evidence <private>` | 0 | **689 unit passed** (20.0 s), **146 native passed** (79.8 s), **0 skips**, patch group reproduces the engine tree byte for byte |
| 6 | canary evidence over both refusal families | 0 | 12 refusal scenarios: every one refused for the named reason, with the existing record path, its containing directory, the ledger file and the binding all identical afterwards, 0 process starts and 0 engine opens |
| 7 | the frozen round-2 verifier's own 24 fixtures, re-run against this revision | 0 | **24/24 pass** - their refusal reasons and both lawful paths are unchanged |

The 49 tests are engine-free and cover all four ceilings, persistence and
reloads, alias merges and rollback, no-charge resets, interrupted and unbound
evidence, missing records, extra settled and unsettled charges, redistribution at
equal totals, malformed and wrong-identity inputs (wrong ledger, wrong digest,
wrong parent bytes, wrong binding), the positive fresh, resumed, resumed-twice and
interrupted-finalization paths, the four publication-refusal families added in
correction 1, the three predecessor-validation families added in correction 2,
first-run publication without a binding, and byte-for-byte
snapshots proving that every refusal writes nothing and opens no engine. Each
module also runs its invariant battery against deliberately broken copies of its
own source in a fresh interpreter and fails if the battery accepts a mutation;
all 39 were caught. Six initially escaped in the first revision, and each escape
pointed at a real weakness in the tests rather than a weakness in the modules:
the ledger's missing-link and parked-charge checks were being satisfied by a
later cumulative backstop, so the checks now assert the refusal's own reason; one
ledger mutation was behaviourally inert, because the charge write-through happens
on two paths, so it was replaced with an observable one and the direct-budget
persistence path got its own invariant; and the guard's three were a missing
invariant for an inconsistent attribution, a check that accepted either refusal
message, and a fixture that invalidated the record digest so the binding check
refused first.

**`EXPECTED_NATIVE_TESTS` moved from 144 to 146.** The strict gate carried 144
while a fresh collection of the native group reports 146, which would have let two
native tests disappear unnoticed. The number is set from a collection count only,
never from a run's summary line.

### Rework round 1 (Sol high) - publication lineage and identity

The first revision's review returned REQUEST_CHANGES on
`publish_completed_record`, and the finding was correct. The function gated
accounting and then wrote the record before advancing the binding, passing
`head_predecessor` as the current binding. That made the ledger's
interrupted-finalization branch accept *any* predecessor digest that was not
already in the chain - a caller-invented digest was inserted into the chain and
the new record bound - while an older in-chain digest failed the advance only
*after* the record had been written. Publication also did not check the record's
named ledger against the live ledger's path, nor hash the parent board, so a
fully accounted record could be published and bound with an identity the next
resume preflight would refuse.

Correction 1 moves every one of those decisions ahead of the first `mkdir` and
the first write:

* the record's named `ledger_file` and its parent board bytes are now checked by
  `_verify_record_identity`, the *same* helper the composed preflight uses, so
  the two paths can no longer disagree about whether an identity is real;
* a continuation must supply `previous_record_path`, and that file must exist and
  hash to the digest the record declares, so a digest with no bytes behind it
  cannot qualify;
* the predecessor must be either the ledger's current binding (the normal
  advance) or a genuinely verified interrupted finalization - the shared
  `_is_interrupted_finalization` predicate, which requires that record's own
  declared predecessor to be the binding and the binding to be the head of the
  chain;
* an older in-chain digest is refused explicitly as an old record, and a binding
  chain that does not end at its current record is refused as broken, both before
  any write;
* the ledger's `advance_campaign` docstring now states the limit it always had -
  it trusts its caller's predecessor digest, because it cannot open a record
  file - and names the verified publication path as the one that may drive a
  continuation.

The four refusal families are covered by
`test_publication_refuses_an_unrelated_new_predecessor`,
`test_publication_refuses_an_older_in_chain_predecessor`,
`test_publication_refuses_a_wrong_named_ledger` and
`test_publication_refuses_wrong_or_missing_parent_bytes`, each with the
byte-for-byte canary over an existing record path, its containing directory and
the ledger file, plus a process tripwire and an engine sentinel; the lawful
current-record continuation and a genuinely verified interrupted finalization are
both retained and both re-proven
(`test_publication_accepts_a_verified_interrupted_finalization`). Eight
mutations were added for the new checks and all are caught.

One cross-packet consequence, recorded rather than fixed here: the independent
verifier's lawful-publication fixture builds its record with
`record_for(ledger_path, root, BOARD, ...)`, so `final_parent_dir` is the case
root while no board is ever written there. Under the corrected contract that
refuses, which is the required behaviour; that packet owns its fixtures and this
task did not edit it.

### Rework round 2 (Sol high) - the predecessor must be a *record*

Correction 1 made publication hash the supplied predecessor's bytes and compare
them to the declared digest, and made it check that predecessor's declared
lineage. It did not check that the file *was* a campaign record: `_read_record`
requires a JSON object, nothing more. A file containing only
`{"previous_record_sha256": "<current binding>"}` therefore hashed to a digest the
caller could declare, satisfied the lineage predicate, and could extend the chain
- while the composed preflight, which runs `validate_campaign_record` and
`_verify_record_identity` before it accepts an interrupted record, would never
have accepted the same bytes as a resume.

Correction 2 closes that by running **the same two helpers the preflight runs**
on the supplied predecessor, before the first `mkdir` or write:
`validate_campaign_record` (it must be a completed campaign record, with its own
totals consistent) and `_verify_record_identity` (it must name *this* ledger and
carry the parent bytes it declares). The checks are applied on both continuation
paths, not only the interrupted one, because the preflight validates whatever
record it resumes from in both cases - publication and the preflight now agree on
what a predecessor is. The digest comparison still runs first, so an unverified
digest keeps naming that reason; the lineage predicate still runs last, so an
unrelated but well-formed predecessor keeps naming its own.

Three predecessor-validation families are covered by
`test_publication_refuses_a_malformed_predecessor` (the reviewer's bare-object
case), `test_publication_refuses_a_wrong_ledger_predecessor` and
`test_publication_refuses_a_wrong_parent_predecessor` (wrong parent bytes and a
missing parent directory), each with the same byte-for-byte canary over an
existing target record path, its containing directory and the ledger file, plus
the binding, the process tripwire and the engine sentinel. The two lawful paths
are re-proven, two guard mutations were added for the new checks (39 caught in
total), and the frozen round-2 verifier's own 24 fixtures were re-run against
this revision: 24/24 still pass, so no round-1 behaviour moved.

## 5. Version

`pyproject.toml` moves from `1.1.0` to `1.2.0`, and the `README.md`
`<!--VERSION-->` marker moves to `v1.2.0` with it, so the marker and the package
version agree. `README.md` carries no other version string.

## 6. Scope

Written by this task: the two new modules, the package export, the two new test
modules, the phase-gate wiring, the version pair, appended entries in
`HISTORY.md` and `CHANGELOG.md`, and this page. Appended, not edited: the two
journals show **zero deleted lines** in `git diff --numstat` against HEAD, which
also covers earlier sessions' appends to the same files. Correction 1 changed
`pcb_world/agent/ledger.py`, `pcb_world/agent/interruption_guard.py`,
`tests/agent/test_interruption_guard.py`, this page, and appended to the two
journals. Correction 2 changed `pcb_world/agent/interruption_guard.py`,
`tests/agent/test_interruption_guard.py`, this page, and the two journals;
nothing else moved in either round.

Not touched: the task's workflow ledger, the phase-30 documents in this tree, the
board, the accepted pointer, the frozen inputs, the engine patches and every other
module. No commit and no push.

## 7. Limits, and what this packet does not claim

* **Single-writer only.** The ledger persists by writing the whole file and does
  not serialise two processes against one path. Durable journaling - a crash-safe
  record of a transaction in flight - remains design-only and unimplemented.
* **The binding advance trusts its caller's predecessor digest.** The composed
  preflight is what makes that legitimate, so a run must not advance a binding it
  did not preflight.
* **The pure helpers are accounting-only**, as above; a caller that treats their
  `complete` as authorization has used them outside their contract.
* **No routing claim.** Every test here is synthetic and engine-free. Nothing in
  this packet says anything about the real router, a real board, or whether a
  campaign with these budgets would finish.
* No self-approval: this is an author packet for independent verification and the
  appointed reviewer's acceptance.
