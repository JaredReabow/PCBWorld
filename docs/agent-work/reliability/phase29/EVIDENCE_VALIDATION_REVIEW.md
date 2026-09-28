# Phase 29 independent verification (T29GV)

Status: **independent verification complete; recommendation ACCEPT.** A second
check of the T29G fail-closed evidence contract and of the frozen T29P
sidecar-policy study, run against the artifacts themselves rather than their
reports. No board was opened, nothing was routed, refilled or promoted, the
accepted pointer did not move, and no waiver was used. Raw payloads, paths,
board bytes and per-payload detail stay private; this page carries aggregates
only.

Owner: task T29GV. Verified: [phase 29 EVIDENCE_VALIDATION](EVIDENCE_VALIDATION.md)
(T29G) and [phase 29 PROJECT_POLICY](PROJECT_POLICY.md) (T29P, frozen).

## 1. What was verified, and how

Three independent tools were written for this task: an attack probe that drives
the real serializer and loader plus both accepted consumers, a stored-payload
audit, and a re-derivation of the sidecar-policy study from the raw captures.
Each artifact's content hash was re-measured rather than taken from the report,
and every claim below was reproduced from the evidence.

| artifact | content hash matches its T29G record |
|---|---|
| the DRC gate module (serializer, validators, strict loader) | yes |
| the saved-artifact verifier | yes |
| the one-large-harness entry point | yes |
| the new evidence-validation suite, and the rewritten inventory-identity suite | yes |
| the private four-board verifier module and its suite | yes |
| the public T29G report | yes |

## 2. The fail-closed contract under attack

Twenty-one attack payloads were rebuilt from the real serializer and run through
four surfaces: the schema check itself, the loader, the public saved-artifact
consumer, and the private four-board verifier. **Every case was refused by every
consumer with the correct structured error** - schema reasons returned, loader
`EvidenceSchemaError`, saved-artifact consumer `RuntimeError`, private verifier
`evidence_schema_invalid` - and **no case raised an unexpected exception or
returned an empty comparison**.

The cases include the original fail-open (the ambiguity list deleted from both
compared payloads), the contradictory summary reported in review (ambiguity and
both payload-listed counts cleared while the inventory summary still declared one
unproven key), an empty summary, a summary missing one required field, a
summary of the wrong type, malformed keys in all four key positions including a
nested object inside a list, an absent row list, an absent evidence field
entirely, truncated rows with the accounting left alone, truncated rows with the
totals edited to match, an unsupported policy string, and an incomplete
inventory that certified an identifier anyway.

Reading the replay path's own source confirms the repair rather than describing
it: the replay reads no payload or row field with a default, it calls the
validator before it builds anything, and the schema check converts an unexpected
failure to check into a refusal reason.

The positive verdicts survive. Honest evidence round-trips byte for byte, an
empty multiset is valid, a proven move keeps its tolerance, an ambiguous
same-count substitution is still refused, a raised multiplicity under one key is
still an addition, and an intact payload still measures end to end through the
private verifier. The hazard itself is pinned as a measurement: with the
ambiguity union emptied in memory, the substitution that the honest capture
refuses compares clean - the pre-repair behaviour, demonstrated instead of
asserted.

## 3. Stored evidence against the new schema

The audit finds every embedded violation multiset in the stored evidence trees.
For the rebaseline tree under review, twelve payloads exist - four roles, three
DRC passes each - and all twelve are refused, each naming a missing accounting
field. No other file in that tree carries a replayable multiset: the retained
per-step verdicts and the campaign summary are self-contained records, not
payloads, and nothing re-reads a stored multiset to produce them.

A wider walk of the whole board workspace finds **259 embedded payloads in 134
files, and every one is refused**. All 259 carry the single pre-change field-set
under the same policy string, so the change invalidates every stored native
multiset in the workspace, not only the rebaseline's. The consequence the parent
owns is that anything which wants to *re-derive* a comparison from stored
evidence must re-capture natively; the retained verdicts remain readable.

The exact fresh native recapture scope for the rebaseline is the twelve
per-role-per-pass DRC payloads (4 roles x 3 passes). The terminal-partition
captures are a different payload and are unaffected. Missing accounting is not
re-derived and not invented.

## 4. The sidecar-policy study, re-derived from raw captures

**Frozen page.** The report's content hash was re-measured and matches the frozen
value.

**Manifest.** Recomputed independently from the live tree, excluding the two
self-referential files by path: 62 declared, 62 live, none mismatched, none
undeclared, none missing, no self-entries. The content digest, re-implemented
from its stated contract, reproduces the declared value, and the public-artifact
hash inside the manifest equals the frozen page.

**Sidecar equivalence on identical bytes.** For each of the three switched
boards, the two sides are byte-identical and match their pins, the sidecar
digests are the two known files, and the resolved policy is equal: global
minima, the Default and named classes, the class of every populated net, the
project-loaded and legacy-settings flags, and the DRC class table. The second
native path that avoids the repository's classification module reproduces the
same numbers. All eight one-block hybrids return the accepted class table and
A's resolved rules, so no single block is load-bearing.

**The CLI agrees, and the control matters.** Four raw reports (three under one
policy, one under the other) carry identical raw row counts - 9757 rows and 135
unconnected rows in every one - and identical counts in all twenty classes. The
gate's collapsed-identity view moves in that class across all four
(116/117/118/117), including between two runs of the *same* policy, which is the
known reporter instability rather than a rule difference. A rule difference
would move the raw rows, and nothing does.

**The fill basis, separated from the policy.** The 7934-versus-317 observation
belongs to the board, not the project file. Re-derived independently from the
four CLI reports, every one of the accepted board's 7488 clearance rows involves
a zone (5462 track/zone, 696 via/zone, 691 through-hole-pad/zone, 573 pad/zone,
66 zone/zone), identical in all four runs and with none carrying no items. Those
pours were filled at a finer clearance than the pinned rules ask for, and a
supported refill removes them. A comparison across the un-refilled and refilled
bases is therefore dominated by stored fills; the sidecar contributes none of
it.

**Scope is honest.** The bisected block set is exactly the eight the report
lists, and the teardrop and tuning-pattern settings sit only in the non-bisected
group, so the report's statement that they are not exercised and therefore not
measured is accurate.

**No leak.** The frozen page carries no private path, board digest, item
identifier, board net name or unexpected digest. The pointer counts it quotes
(2 only in A, 142 only in B, 63 differing) match the structural diff's declared
counts; the saved lists are capped samples, which is why one of them is shorter
than its count.

## 5. Drift, gates and waiver

The pinned build-tree CLI, the pcbnew kiface and the router module all hash to
their pinned values, and the accepted pointer still resolves to the accepted
board (verified from the artifact manifest as well as the file). The accepted,
canonical, refilled-baseline and final-candidate boards each hash to their pins.

| gate | result |
|---|---|
| focused public suite (evidence validation, inventory identity, reference baseline) | 163 passed |
| private four-board verifier suite | 97 tests, OK |
| strict combined harness | unit 640 passed, native 146 passed with 0 skipped, engine patch reconstruction OK |
| public working tree | `git diff --check` clean |
| reliability ledger | valid, 0 warnings |

No waiver was enabled or consulted.

## 6. Limits

The schema proves self-consistency, not authenticity: a payload rewritten
coherently end to end still loads, and closing that needs a signature this work
does not add. The attack cases are built from the real serializer over a
multiset the verifier constructs, so this verification adds no new measurement
of a frozen board. The workspace-wide payload scan ran while the policy study
was still being corrected, so that tree is excluded from it and was validated
separately against its frozen manifest. This review does not re-run the
rebaseline candidate gates, does not re-measure the refill, and does not
authorise promotion.

## 7. Verdict

**ACCEPT.** Every attack is refused with the correct structured error on both
consumers, the intended verdicts survive, the stored-payload blast radius and
the exact recapture scope are characterised, and the frozen sidecar-policy claim
re-derives from the raw captures on every count. The remaining limits are the
ones both reports already state, and neither is a defect in the work under
review.
