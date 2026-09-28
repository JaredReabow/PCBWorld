# Phase 29 (T29G) - serialized native evidence now fails closed

Status: **implemented and verified against the public harness and the private
verifier suite; pending independent review.** The change is inside the DRC gate
and its two consumers. No board was opened, routed, refilled, saved over or
promoted; the frozen boards, rules and projects, the comparison policy, the
accepted pointer, the ledger, HISTORY/CHANGELOG and the version are untouched,
and no waiver was used. Raw reports and per-payload detail live in
`phase29_evidence_validation/` in the board workspace; this page carries
aggregates only.

## 1. The defect

Native evidence travels between processes as a serialized violation multiset:
the captured baseline is written by `violations_evidence` and replayed by
`violations_from_evidence`, and a comparison of the two multisets is what accepts
or refuses a copper change. The reader defaulted whatever it did not find: a
missing ambiguity list became empty, a row's condition was never cross-checked
against its own fields, and absent geometry became zero.

An ambiguity list is not decoration. A same-count physical substitution under a
reused item identifier is refused *only* because the shared key is refined by the
violation's own condition, and the set of refined keys is the union of the
ambiguity the two compared payloads declare. Deleting the ambiguity list from
**both** payloads emptied that union, and the substitution then compared as
`clean` with nothing added. Deleting it from one side only still refused, because
the union kept the other side's ambiguity - which is exactly why the defect was
easy to miss.

## 2. The repair

The payload contract now lives in the module that writes it.
`violations_evidence` stamps a single supported policy and writes its own
accounting - the row total, the relevant/connectivity split, the count of keys
and rows that lost their inventory proof, and a per-key count list.
`validate_evidence_payload` then requires, before any `ViolationSet` is built:

* the supported evidence policy, and correctly typed rules path, context and
  ambiguity list - each *present*, so "absent" is never read as "empty";
* the whole inventory summary the capture wrote - a supported inventory policy,
  `complete`, `problems`, the item and identifier counts, `kinds`,
  `unknown_kinds`, `unresolved_reasons` and the violation/ambiguity accounting -
  each required and typed, then cross-checked against the payload: the violation
  count against the rows, the unproven-key and unproven-row counts against the
  ambiguity list and the payload's own counts, `complete` against `problems`, and
  a summary that says the inventory was incomplete against every `uuid` key,
  which it must declare unproven;
* explicit and correctly typed rows: key, condition, class, message, layer, both
  geometry coordinates and net names, all named in the refusal when missing;
* keys restricted to the two identity shapes the gate can compare, with the row's
  key, condition, geometry, layer, net names and relevance classification all
  agreeing with each other - contradictory rows are refused;
* every ambiguous key present in the payload's own rows, with the declared key
  and occurrence counts equal to what the rows carry;
* the declared total and per-key counts equal to what the rows add up to, which
  is what turns a truncated or omitted row list into a refusal.

`violations_from_evidence` validates first and then reads every field
explicitly; the replay path has no defaults left. Both consumers convert a
failure into an explicit refusal: the saved-artifact verifier refuses the
envelope before it opens a board, and the private four-board verifier refuses the
measurement with a structured `evidence_schema_invalid` code, so no retention can
rest on an incomplete pass. The private worker case where the whole evidence
field is absent refuses too, rather than reading as "no violations". Malformed
key shapes - an object, `None` or a float anywhere a key is expected - are
reported as schema problems instead of raising out of the checker, and any
unexpected failure to check is itself a refusal. The diagnostic route cannot
retain and now also cannot consume a malformed payload. The outer envelope check
- board, project, rules bytes and engine build - is unchanged and still runs
first.

## 3. Verification

Measured, not asserted:

| check | result |
|---|---|
| exploit through the real serializer, loader and accepted comparison | honest capture refuses the substitution (`clean` false, one unexplained added identity); ambiguity cleared on both sides reports `clean` true and nothing added; both edited payloads are refused by the loader with the missing field named |
| the T29CV fail-open variants re-run against the repaired loader | every stripped variant refused (ambiguity removed, ambiguity emptied with non-zero declared counts, binding removed, both removed); the intact variant still loads and still refuses the substitution |
| the two reported reproductions, re-measured on real captures | clearing the ambiguity list and the payload's two counts now refuses against the inventory summary; a key carrying an object, `None` or a float refuses in all four key positions; `binding={}` refuses listing every missing summary field; a missing whole `violations` field refuses; an incomplete summary certifying a `uuid` refuses |
| focused public suite (`test_evidence_validation.py`, inventory identity, reference baseline) | 163 passed |
| whole public agent tree | 775 passed |
| private phase-27 suite (accepted verifier, rewritten payload builders) | 97 tests, OK |
| integrated harness, `check_phase.sh --strict` | unit 640 passed, native 146 passed with 0 skipped, engine patch reconstruction OK |

The negative matrix covers ambiguity deleted from both sides and from one side,
binding deleted alone and together with ambiguity, every payload and row field
deleted, `null` and wrong-typed replacements (including a policy the reader does
not support), empty ambiguity with non-zero declared counts, an ambiguous key the
payload does not carry, repeated or null ambiguity entries, key and occurrence
counts that disagree with the rows, contradictory key/condition/layer/item pair,
a geometry key that still names items, an inverted relevance flag, truncated rows
and omitted rows with edited totals, and mismatched per-key counts - on both
consumers. Positive cases pin that a valid payload round-trips byte for byte, an
empty multiset is valid, proven movement keeps its tolerance, an ambiguous
same-count replacement is still refused, and a raised multiplicity is still an
addition.

## 4. Limits

The schema proves self-consistency, not authenticity. A payload rewritten
coherently end to end - rows, accounting and ambiguity list all edited to agree -
passes; nothing in it is signed and the envelope does not hash its own baseline
block. Truncation is detectable only because the payload declares its own
accounting, and edited accounting that no remaining row field contradicts is not
detectable. Payloads captured before this change lack the accounting fields and
are refused on purpose, including a run resumed from an older checkpoint: the
refusal names the missing field and the recovery is a fresh baseline capture
rather than re-deriving the missing accounting from the rows.

That limit is measured, not implied: with the ambiguity list, both payload counts
and the inventory summary all edited into agreement, the payload loads and two
such rewrites compare clean. Requiring the full summary raises the edit from one
deleted field to a complete, deliberate rewrite; closing the rewrite itself needs
a signature over the payload, which this task does not add.

Two synthetic payload builders in the board workspace (the phase-29 duplicate-ID
regression driver and the independent fail-open probe) write rows with a `uuid`
key and empty item fields, which a real capture cannot produce; they now fail
loudly at the serializer/loader boundary and need their rows to carry the item
pair their key names. They are owned by other tasks and were not edited here. An
independent re-run of the fail-open variants with real captures is recorded in
the board workspace and shows every stripped variant refused.

The change also moves the content hash of `pcb_world/agent/drc_gate.py`, which
the reliability ledger registers as an artifact, so the ledger's acceptance
records for the tasks that depend on it are stale until the parent re-records
them; the ledger itself was not edited here. The frozen phase-29 rebaseline
evidence pinned the previous hash of this gate, and the independent audit that
reads the loader's source to detect drift will now report the intended shape
change.

## 5. Files

`pcb_world/agent/drc_gate.py` (policy constants, accounting, validators, strict
loader), `tools/reliability/verify_saved_artifact.py`
(`_replayable_baseline`, called before the engine opens),
`tools/reliability/check_phase.py` (the new suite added to the unit group),
`tests/agent/test_evidence_validation.py` (new), and
`tests/agent/test_drc_inventory_identity.py` (the previous permissive-loader case
rewritten). Private: `phase27_verifier/phase27_verify.py` and
`phase27_verifier/phase27_tests.py`. No commit, version bump or
HISTORY/CHANGELOG entry was made; the parent integrates those.
