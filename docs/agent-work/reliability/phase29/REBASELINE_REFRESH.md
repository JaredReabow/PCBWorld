# Phase 29 (T29C) refresh - the replay re-measured under the accepted strict consumer

Status: **re-measured and corrected; read-only; pending review.** The six
historical phase-26 restorations were re-measured through the accepted T29G
strict evidence consumer and retain six of six; the final candidate's gates
were re-measured and refuse; and a three-way common-fill study was run for both
reference boards. Nothing was routed, refilled in place, promoted or written
back to a frozen input, and the accepted pointer did not move.

This revision answers the frozen [phase 29
REBASELINE_REVIEW](REBASELINE_REVIEW.md) (T29CV), which asked for an
architectural change rather than another item-specific patch. The
representation comparison is now a **whole-board structural contract**: it
parses the complete tree, accounts for every node on both sides, and folds only
two exact, named writer re-spellings at exact structural positions. A relation's
retention is the whole conjunction - staging, the fill-only proof, validated
native capture envelopes, a complete CLI gate with its pinned envelope
enforced, the board, project, rule, CLI, provider and terminal-proof bindings,
the native comparison and terminal preservation - with the rebuild failing
closed on any integrity failure. The measured verdicts are unchanged: the same
two relations complete-retain and the same four refuse.

This page supersedes the measurement path, not the verdicts, of
[phase 29 REBASELINE_REPLAY](REBASELINE_REPLAY.md). That page is left byte-for-
byte as it was measured, because its evidence was produced before T29G changed
the reader; every verdict it records reproduced here under the corrected
consumer. Private evidence lives in the board workspace; this page carries
aggregates only.

Owner: task T29C (refresh). Predecessors: [phase 29
REBASELINE_REPLAY](REBASELINE_REPLAY.md), [phase 29
EVIDENCE_VALIDATION](EVIDENCE_VALIDATION.md), [phase 29
EVIDENCE_VALIDATION_REVIEW](EVIDENCE_VALIDATION_REVIEW.md), [phase 29
PROJECT_POLICY](PROJECT_POLICY.md).

## 1. Why a refresh, and what it is not

T29G made the serialized native evidence fail closed: the reader now requires
the inventory accounting the capture writes and refuses a payload that omits
it. Every stored native multiset in the board workspace - including the twelve
the rebaseline tree held - predates that field set and is refused on purpose.
A verdict that rests on a stored multiset therefore has to be re-derived from a
fresh capture; re-reading the old payloads is not an option, by design.

This refresh re-runs the same measurements under the corrected consumer. It is
not a routing campaign, it does not close the remaining joins, it does not
refill a frozen input, and it does not move the accepted pointer. The twelve
incompatible stored payloads are a count of *incompatible* records, not the
number of captures a refresh needs: the six step comparisons need three fresh
instances on each side, and the candidate gates need four roles times three
passes.

## 2. The private scripts this task changed

No public gate, verifier, engine, rule or frozen board was edited. The private
changes are:

| script | change |
|---|---|
| `p29c_dupid.py` | synthetic rows now name the item pair their `uuid` key names, and the binding is a real `BoardInventory` summary merged the way the production capture merges one, so the regression travels through the real serializer and loader again |
| `p29c_common.py` | the verifier invocation accepts a scratch directory and sets `TMPDIR` for it, so the fresh native captures are kept instead of being discarded with the process |
| `p29c_freeze.py` | the drift snapshot also covers the refresh drivers |
| `p29c_manifest.py` | bulk rerun scratch (`work`, `cli_work`, `__pycache__`) is skipped by path component anywhere in the tree, while payloads and reports are still hashed |
| `refresh/scripts/p29c_fill_repr_diff.py` | the representation comparison, narrowed to two exact fold positions with a congruence check, duplicate and missing identities refusing, and every fold named and counted |
| `refresh/scripts/p29c_fill_repr_diff.py` (rewritten again) | the whole-board structural contract: every top-level kind and every nested occurrence is compared, generated zone fill is the only exclusion, multiplicity is preserved through occurrence groups, and coverage classifies every node as exact, normalized, generated fill or difference |
| `refresh/scripts/p29c_native_envelope.py` (new) | validates a role's capture envelopes with the accepted verifier's own worker validator, plus the pinned instance count, distinct processes, pinned engine and pinned consumer digests, and the staged board, project and rule bytes |
| `refresh/scripts/p29c_step_envelopes.py` (new) | re-checks the six steps' retained payloads against the same envelope contract |
| `refresh/scripts/p29c_relation_verdict.py` (new) | the complete relation verdict, reusing the accepted verifier's own evidence loader and CLI binding validator |
| `refresh/scripts/p29c_common_fill.py`, `refresh/scripts/p29c_common_fill_rebuild.py` | both relation builders now publish components only; the retention verdict is the shared conjunction and the rebuild fails closed on an integrity failure |
| `refresh/scripts/p29c_common_fill_native.py`, `refresh/scripts/p29c_common_fill_counts.py`, `refresh/scripts/p29c_refresh_hashes.py`, `refresh/scripts/p29c_refresh_native.py` (new) | the per-reference native capture, the partition counts, the consumer/native hash separation and the retained-payload re-measurement |

Two of these were the defects this revision repairs. The duplicate-identifier
regression had to be repaired before it could run at all under the strict
reader; the relation verdict and the representation rule were repaired because
the independent review showed a retention decision that would not change if the
gate beside it were broken.

Measured after the repair, through the verifier's own comparison path: the
unchanged ambiguous inventory still accepts; one more occurrence of the same
key and condition still refuses; a same-count replacement under a different
condition still refuses; an equal-count movement between two ambiguous keys
still refuses; and the replayed ambiguity survives the round trip. The
serializer-boundary cases (no native provenance, no server identity, the reuse
pool active, diverged board, stamp mismatch) each still refuse with their own
code, and a real-board run with a single native instance still cannot retain.

## 3. The six steps under the strict consumer

Each step stages copies of its immediate parent, its post-refill candidate and
its pre-refill snapshot, regenerates the complete pinned CLI gate for both
sides, and replays the accepted verifier with three fresh native instances per
side. Every board is opened in its own engine process and no process ran under
the provenance waiver.

| step | transaction | clusters | native instances | CLI added class | verdict |
|---:|---|---|---|---|---|
| 1 | walkaround, direct | 339 -> 338 | 3 + 3, agree | unconnected pairing only (7 identities) | retain |
| 2 | walkaround, direct | 338 -> 337 | 3 + 3, agree | unconnected pairing only (9) | retain |
| 3 | push-and-shove, direct | 337 -> 336 | 3 + 3, agree | unconnected pairing only (9) | retain |
| 6 | walkaround, direct | 336 -> 335 | 3 + 3, agree | unconnected pairing only (8) | retain |
| 9 | walkaround, direct | 335 -> 334 | 3 + 3, agree | unconnected pairing only (10) | retain |
| 17 | walkaround, one dogleg | 334 -> 333 | 3 + 3, agree | unconnected pairing only (8) | retain |

All six hold every one of the six conditions, over all nine parent-by-candidate
instance pairs per step: the relevant union is 317 identities on both sides of
every step, no pair and no instance shows an added relevant identity, the
anchor closure re-derives from the candidate's own reopened cluster rows, and
the CLI's only added class is the unconnected endpoint pairing, admitted behind
a bound fresh-process terminal proof reporting zero vanished terminals, zero
vanished nets and zero split relations on every step.

The fresh captures are kept, not merely their verdicts: each of the six steps'
two roles has three native payloads on disk, every one carrying the inventory
accounting the strict reader requires. A second, independent native campaign
was then run over the same staged boards and the same CLI evidence and
reproduced all six verdicts; a campaign that had disagreed with the first would
have been reported as a finding rather than replaced.

## 4. The final candidate still refuses

Four pinned boards (accepted generation, canonical original, phase-25 refilled
start, final experimental candidate) were each measured with three fresh
instances, and the complete pinned CLI was run twice per side. All gates are
complete - staged inputs match, both runs per side, report complete.

| relation | native added identities | terminal comparator | fragmented clusters / minimum joins | CLI | verdict |
|---|---:|---|---|---|---|
| candidate vs phase-25 refilled start | 0 | ok | 0 / 0 | verified | retain |
| candidate vs accepted generation | 19, all isolated copper | 21 split relations | 5 / 12 | refuses | refuse |
| candidate vs canonical original | 19, all isolated copper | 21 split relations | 5 / 12 | refuses | refuse |

This is the same picture the historical page recorded, now carried by fresh
evidence the strict reader accepts. It is also the picture section 5 explains.

## 5. The common-fill study

Two comparisons of this lineage are not on one fill basis. The accepted
generation and the canonical original carry the *stored* zone fills they were
built with; the candidate carries a legally refilled board. T29P showed the
project sidecars are not the difference and that the stored fill is, so this
study puts both sides on one basis:

* stage disposable copies of the reference `R` and the candidate `C` under one
  pinned rule file and one pinned project sidecar;
* refill each copy with the engine's own zone filler, save it to a separately
  named file, and reopen it for every measurement, giving `F(R)` and `F(C)`;
* prove the saved and reopened bytes differ from their own source only in the
  generated fill and two documented writer re-spellings;
* measure `R -> F(R)`, `F(R) -> F(C)` and the mandatory `R -> F(C)` with three
  fresh native instances per side, a complete pinned CLI gate and the terminal
  comparator - and report what was measured.

### 5.1 The whole-board structural contract

The comparison parses the complete board on both sides and accounts for every
node of every kind. It is not a list of item types to visit: nets, setup,
graphics lines and arcs, text, segments, vias, standalone arcs, footprints,
pads, and any node kind the reader has never seen are all compared by default,
because the default is comparison. Exactly one thing is excluded - the
`filled_polygon` children of a `zone`, the derived copper a refill rewrites -
and zone declarations, occurrence counts and every other zone child are
compared like anything else.

Top-level items are grouped by identity key into ordered **occurrence groups**,
never into a dictionary that would drop a repeat. A group whose sizes differ, a
key present on one side only, and an ambiguous match (more than one candidate
for a leftover occurrence) are all differences. Within a matched pair the walk
classifies each node as exact, normalized, generated fill or difference, and
the totals must add up on both sides; acceptance requires zero unexplained
nodes.

Two re-spellings may be folded out, each at one exact position, and every fold
is recorded with its path and before/after text: a rotation congruent modulo a
full turn, which may only be folded at the fourth token of an `at` node that
actually carries an angle or at the third token of a `render_cache` node; and
the writer spelling out a fill mode the source left to the reader's default,
which may only be folded for a `fill` node inside a `zone` when one side carries
exactly one extra token immediately after the head, that token is the observed
default spelling, and every other child is identical. A moved coordinate, a
changed angle, two explicit modes that differ, an undeclared extra token, an
item added or removed, or a duplicated or missing identity makes the verdict
*substantive*.

Equality is **lexical**. Two atoms are equal only when their raw source text is
equal; there is no numeric transform anywhere in the comparison, so a
millimetre spelling and its quantised integer spelling are different tokens, and
a quoted numeric token is not the bare one. Both of those were reproductions
against an earlier version, and both now read *substantive*. An angle fold also
preserves the token's own kind: the fold requires two bare integer tokens, so a
bare zero is not equivalent to a quoted full turn however the digits read, and a
quoted angle that is identical on both sides is simply equal rather than folded.

The folds are a **finite contract with one decision point**: a difference is
permitted only when a named rule recognises the full structural path, the exact
shape, the lexical token kinds and the allowed value relation, and every
granted difference is recorded. Anything else is compared exactly. There are
two rules. The rotation rule folds two *bare* integer tokens that are congruent
modulo a full turn, at the angle position of an `at` node that carries one or of
a `render_cache` node, inside a known board item, with every ancestor between
the node and the board root an approved container. The fill rule folds one
  extra token at index 1 of a `fill` node that is a direct child of a top-level
  zone, where that token is the bare spelling `yes` and every other child is
  identical by raw text. Generated fill is excluded only directly inside a
  top-level zone - the exclusion is ancestry-exact, so its only ancestor is that
  zone - and the root tag is validated. A quoted token never folds
  against a bare one - including a quoted `"yes"` where the writer emits bare
  `yes` - an undeclared extra token, a changed body, a changed coordinate, a wrong
  arity, a nested context and a changed child head are all differences, and a
  fold keyed on a name alone cannot fire under an unknown intermediate node. A
  `filled_polygon` inside a nested zone, or under a wrapper inside a zone, is
  not the derived copper of the top-level pour and is compared exactly.

On these boards the folds actually used are exactly the two named ones:

| difference | measured shape |
|---|---|
| stored zone fill | 95 of 177 zones rewrite their fill polygons; the declarations are otherwise identical |
| rotation spelling | 183 `at` angles and 1 `render_cache` angle on the canonical lineage, none on the accepted one, each congruent modulo a full turn |
| fill-mode token | 3 zone declarations, where the source left the mode to the reader's default and the writer spells it out explicitly |

After folding those two re-spellings out, every node on both sides is accounted
for and the verdict is *representation only* on all four pairs, with zero
unexplained nodes; no item, pad, track, via, net or declaration value changed.
The candidate's own refill is idempotent: zero zones rewrite their fill, no
non-zone item moves, and no fold is used.

The exact non-zone digest - which is exact text over every top-level kind - is
unchanged wherever no re-spelling was needed, and it moves only on the canonical
lineage, where 183 `at` angles and one `render_cache` angle were re-spelled.
Both facts are reported: `nonzone_digest_unchanged` and
`only_fill_changed_exact_digest` sit beside the strict structural flag
`only_fill_changed`, so the reader can see that the digest moved only because a
recorded, approved re-spelling rewrote an angle token.

The coverage totals reconcile with an independent raw node count, node for
node. On the canonical pair the source carries 864556 nodes and the destination
864559, with the generated fill counted separately at 194 and 179; the walk's
bucket totals equal those numbers exactly, and the same holds on the accepted
pair and on both candidate refills. `zero_unexplained` is that reconciliation
*plus* zero differences, so the claim that every node is classified is backed by
counts rather than by a difference count alone. Items with no identifier - the
192 `net` declarations, the setup block, the layer and paper records - are
matched by rank and content and reported as `identifier_free_groups`; they are
not called duplicated identities, and a duplicate is claimed only for an item
that actually carries one.

The review's bypasses are closed and pinned by the module's own regression. A
one-item edit that holds the item count constant - a segment endpoint or width,
a via position, drill or layer, a net name, a graphics stroke width, a setup
value - is now `substantive`; so are an inserted unknown node kind, a removed
top-level item, an inserted item, a duplicate-identifier substitution and a
missing footprint identifier.

### 5.2 The three relations, for both references

A relation is called retained only when its staging, its fill-only proof, the
validated native capture envelopes on both sides (the pinned number of
independent instances, distinct processes, no waiver, pinned engine and
consumers, exact board/project/rule bytes), its complete CLI gate with the
pinned envelope enforced, every binding (boards, projects, rules, CLI, provider
and the fresh-process terminal proof), its native comparison and its terminal
comparator all hold. The rebuild fails closed when any of those is missing or
disagrees, and it separates that from an intact relation that simply refuses.

| reference | clusters R -> F(R) -> F(C) | R -> F(R) | F(R) -> F(C) | R -> F(C) |
|---|---|---|---|---|
| accepted generation | 321 -> 340 -> 333 | refuse: 19 added, 33 split, 7 fragmented / 19 joins | **retain**: 0 added, 0 split, 0 fragmented, CLI verified, all bindings held | refuse: 19 added, 21 split, 5 fragmented / 12 joins |
| canonical original | 335 -> 346 -> 333 | refuse: 19 added, 33 split, 7 fragmented / 19 joins | **retain**: 0 added, 0 split, 0 fragmented, CLI verified, all bindings held | refuse: 19 added, 21 split, 5 fragmented / 12 joins |

All six relations decided with no integrity failure: two complete-retain and the
four un-refilled-relative relations are measured refusals. The nineteen added
identities are the *same class* in every refusing relation, on both references:
isolated copper fill. Refilling a reference and changing nothing else - no route
added, no track moved - produces those same nineteen additions and refuses
every gate, with the same unconnected-pairing churn and a larger split-relation
count than the candidate comparison.

One number is worth naming so it is not read as a copper property. The relevant
identity union on the un-refilled side of a refusing relation is 7934; on the
refilled side it is 317, a factor of about twenty-five, and that contrast is the
same on both references. Both sides are staged under one rule file and, since
the accepted sidecar study, under project files that resolve to one policy, so
the gap belongs to the stored zone fill - the un-refilled board carries the
large union and refilling it moves the union to the small one. The relation is
still a like-for-like test of the refill operation; what the number describes is
the fill state, not the copper the candidate added.

## 6. What this changes for the next restoration campaign

What the study adds is a measurement of the **fill's contribution**, and it is
worth stating what it does not do as carefully as what it does. It does not
erase the original-relative promotion obligation, it does not substitute the
refilled baseline for it, and it does not show that the candidate's routing
contributed nothing. Three measured points bound the reading:

* **Refilling a reference alone already refuses, with the same nineteen
  additions.** `R -> F(R)` moves only stored fill and the two named writer
  re-spellings - no route is added and no track moves - and it refuses every
  gate with the same nineteen isolated-copper identities, the same
  unconnected-pairing churn and a larger split-relation count than the candidate
  comparison. On this lineage, an un-refilled-relative refusal is therefore not
  by itself evidence about the candidate's copper.
* **On one common fill basis the candidate's copper adds no relevant identity
  and splits no refilled-reference cluster.** `F(R) -> F(C)` is clean on both
  references: zero added relevant identities, zero split relations, zero
  fragmented reference clusters, and the complete CLI gate is verified behind a
  bound fresh-process terminal proof showing every original connection
  preserved. That is a statement about violations on that basis, not a finding
  that the routing is complete or that the refusals elsewhere are harmless.
* **The twelve remaining joins are measured against an un-refilled reference.**
  Against the refilled reference the same comparison reports zero. For the
  accepted reference the arithmetic is visible end to end: the refill raises
  the cluster count by nineteen, the candidate's own copper lowers it by seven,
  and the gate reports twelve. The canonical reference reports the same
  nineteen additions and the same twelve joins from a different cluster ladder,
  so the coincidence is measured on both, not assumed from one. The candidate
  does move connectivity, and the two mandatory `R -> F(C)` refusals stay
  exactly where they were.

The practical constraint is narrow and worth keeping: a promotion gate taken
only against an un-refilled reference measures the fill difference first, so a
campaign that wants to gate its own copper should record the refilled basis
beside the original-relative one. Both are reported here and neither replaces
the other.

## 7. Hashes: what moved and what did not

The native and provider artefacts are unchanged from the accepted T29A values,
re-measured after the run:

| artefact | digest prefix | unchanged |
|---|---|---|
| build-tree `kicad-cli` | `56dd7910af7d190c` | yes |
| `_pcbnew.kiface` | `c23f8cb9023e9e8d` | yes |
| `kicad_rl_router.so` | `819a26f1ebe5affe` | yes |
| `ENGINE_CPP_HASH` stamp | `b47d4f0c` | yes, equal to the live tree content hash |

The Python consumers are recorded separately, because these are the artefacts a
fresh capture binds:

| consumer | digest prefix |
|---|---|
| public DRC gate (serializer, validators, strict loader) | `61d085f99e88fa5f` |
| public saved-artifact verifier | `577cbfb5e55b3e5b` |
| public one-large-harness entry point | `146e5db192a7f21b` |
| public evidence-validation suite | `890f1437b6fb8e89` |
| private four-board verifier | `9deeac074991f3e6` |
| private verifier worker | `da2783f22df69484` |
| private verifier common module | `928ea13e71f7fc20` |
| private verifier suite | `dc0c4223f4931174` |

Compared against the frozen snapshot the historical rebaseline took, exactly
two of those moved: the public DRC gate and the private four-board verifier -
the T29G repair set, which is the change this refresh exists to bind. Nothing
else in the tracked set moved, and the engine tree content hash is unchanged.

## 8. Guards and limits

The protected hashes were re-checked after the run: the accepted pointer, the
accepted generation, the canonical original, the phase-24 and phase-25 boards,
the phase-26 start board, all six parents, candidates and pre-refill snapshots,
the pinned rule file, the original EasyEDA project and the accepted post-T29
binaries are unchanged. The public-document leak scan is clean, `git diff
--check` passes, and a freeze snapshot of the verifier, the gate, the comparison
sources, the engine sources, the binaries and this task's own scripts shows no
drift between the snapshot and the guard run.

The correction rounds added a mutation regression: the complete-verdict
predicate is run against a mutated copy of the retained relation's own gate and
proof, and each of a changed candidate board hash, reference board hash, CLI
verdict, CLI completeness, staged-inputs flag, run count, CLI hash, provider
hash, rule hash, project digest, terminal-proof board binding, terminal-proof
freshness, terminal-proof completeness, terminal-proof split count, absent
proof file, absent or null truncation evidence, a pinned-envelope mismatch, a
missing envelope and a one-instance native comparison forces the complete
verdict to refuse - with the binding failures reported as integrity failures and
the measured ones as measured refusals. The private harness carries those cases,
and the rebuild itself is also run end to end against a disposable copy of the
tree with one mutated field: it exits non-zero and names the refusal.

The four bypasses the review opened are closed at the integrity boundary:
top-level items of every kind are inside the structural comparison; a false
pinned CLI or provider assertion, or a request to replay a recorded envelope, is
an integrity failure; a relation needs the pinned number of independent native
instances per side, validated through the accepted verifier's own worker
validator with distinct processes, no waiver, the pinned engine stamp and
pinned consumer digests; and `suspected_truncation` must be present and an empty
list, because the accepted gate always writes it and an absent or null field is
a malformed report rather than a clean one.

The structural recheck closed four more. Atom equality is lexical, so a
quantised integer spelling and a quoted token are differences. Every native
`server_pid` and `worker_pid`, DRC and capture alike, must be a positive integer
and the three DRC identities must be distinct - a missing, zero or negative
identity refuses instead of reading as a sentinel. One shared validator covers
both payload modes, so the capture is checked for its board and project bytes
before and after, its rule binding, its provenance source hash and any waiver
exactly as a DRC pass is; the capture declares its rule file by path, and that
path must resolve to the staged rule file and hash to the pin, because the
accepted worker writes the path and load status rather than a capture rule
digest. And the step-envelope tool now globs directories with numeric step
names, so its own output file can no longer be mistaken for a step: it is
idempotent and all six steps re-check true.

A further round closed three more. Angle folds preserve the token's kind, as
described above, and the whole ancestry is inspected. Every rule field a
payload supplies is checked **together**: a capture carrying a matching rule
digest as well as an engine rule status still refuses if the status says the
rules were not loaded from file, carries a load error, reports validation
blocked, names no path or names a path whose file does not hash to the pin, so a
matching digest can never mask a broken rule binding. And a child whose head
changed is reported as a substantive difference rather than raising, with its
whole subtree accounted for on both sides.

The same-permissive-pattern audit that produced this round is now enforced by a
matrix in the private harness: every fold row is applied asymmetrically in
**both directions** and judged through the public comparator and the fill-only
proof, not through a helper. The rows cover the bare full-turn angle positive
and its quoted, non-numeric, moved-coordinate and wrong-arity refusals; the
bare `yes` fill positive and its quoted-token, other-token, changed-body,
extra-token and nested-context refusals; a generated fill permitted only as a
direct child of a top-level zone, permitted to be added or removed there, and
refused as a changed, added or removed child of a nested zone or of a zone under
a wrapper, refused under an unknown context, or with a changed declaration; and
the default lexical refusals - duplicate, missing and unknown edits, a root-tag
change and a changed child head - all substantive without raising. A companion
table names each contract rule's predicate and the controls that pin it, and
fails if any rule is missing from it or any control fails.

Completeness is read from the gate's own fields, strictly: `report_complete`
must be the boolean true, `staged_inputs_match` must be the boolean true, the
recorded run count must be at least two, and the gate's truncation list must be
empty. An absent or non-boolean field refuses. The same predicate is also run
end to end: the rebuild is executed against a disposable copy of the tree whose
only difference is one mutated field, and the record names the exit status and
the refusal code for each case - a mutated candidate board hash, a mutated
completeness flag and an absent terminal proof each fail closed with exit 10,
while a mutated CLI verdict is refused with the integrity checks still intact.

Limits:

* this remains a rebaseline and a basis study. It is not a routing campaign, it
  does not close the twelve joins, and it does not move the accepted pointer;
* the study measures the fill's contribution; it does not allocate the refusal
  between fill and routing, and the original-relative obligation stands as
  measured;
* the duplicate-identifier regression is synthetic by construction: it pins the
  consumer's behaviour on multisets the verifier builds, not a new measurement
  of a frozen board;
* the common-fill study measures two references and one candidate. The
  representation verdict folds out exactly two writer re-spellings, both named
  above, each only at an exact structural position; a third re-spelling, a
  coordinate that moved, a real angle change, a real fill-mode change and a
  missing or duplicated identity are all reported as substantive, and the
  module's regression pins each of them;
* the CLI's unconnected pairing identity counts are not stable across campaigns
  by design; only the raw row counts are, and no claim is made that a stable
  pairing identity exists;
* the schema proves self-consistency, not authenticity. A payload rewritten
  coherently end to end still loads; closing that needs a signature this work
  does not add;
* no paid API calls were made.
