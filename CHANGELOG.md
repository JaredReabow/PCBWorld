# Changelog

Release notes for the PCBWorld environment, newest first. The engine is a separate program
with its own version line — its notes are in the
[PCBWorld-Engine](https://github.com/LGAI-Research/PCBWorld-Engine) repository's `CHANGELOG.md`
— and every environment tag pins exactly one engine commit: check out the tag and run
`git submodule update --init` to get the engine it was tested with.

## v1.2.0 — 2026-09-29 — bounded native-transaction ledger and interrupted-campaign guard

### The ledger (`pcb_world.agent.ledger`) — 2026-09-29
- **Four hard ceilings, not defaults.** Five native transactions per stable logical
  link, twelve distinct anchor pairs per link, twelve links per campaign and sixty
  native transactions per campaign. A caller may ask for a tighter value and never a
  looser one; every ceiling is re-validated at construction, when a ledger is restored
  from disk and again on every reservation and pair offer, so a raised ceiling in a
  file or in a mutated object is refused rather than honoured. The sixth transaction
  on a link cannot be expressed: the only way to obtain a licence for a native call
  is a ticket from a budget that still has one.
- **Charged before the call, and cumulative across resumes.** `reserve` writes the
  charge through to disk before the caller issues its native call, so a crash inside a
  transaction still charges that attempt on the next resume. Charges are never reset;
  a resume finds the same budget for the same link because the identity is the
  canonical, order-independent pair of component identities with the engine's net
  renumbering normalised away.
- **Aliases are symmetric and merge conservatively.** Declaring two component names
  equivalent unions their budgets - counters add, anchor sets union, nothing is reset -
  and a merge that would break a ceiling or combine two different retained joins is
  refused with the equivalence itself rolled back, so a swallowed refusal cannot leave
  one logical link able to spend twice.
- **A resume is reconciled per link, not per total.** A record whose history charges a
  link the ledger does not hold, that accounts for a different count on a link, that
  leaves charges parked on a link no record visited, or whose declared totals disagree
  with the ledger is refused. A total-only check would accept 5+1 rewritten as 1+5.

### The interruption guard (`pcb_world.agent.interruption_guard`) — 2026-09-29
- **A pre-charged, unbound ledger is no longer adoptable.** A fresh invocation whose
  ledger already carries charges, links or anchor pairs and is not bound to a campaign
  record is classified `interrupted` and refused, with no write and no engine open - the
  shape a killed launch leaves behind, where a continuation would otherwise take the
  fresh path and publish a record its own validator can never resume.
- **The file-facing preflight reads the bytes an identity claim is made from.**
  `interruption_guard_preflight_campaign` reads and hashes the campaign record and the
  parent board it declares, requires the record's named ledger to be the ledger being
  preflighted, requires a `current-record` binding or the permitted
  `interrupted-finalization` predecessor binding, stages alias declarations in memory
  and only then reconciles - all before the caller's first `mkdir`, first write or first
  engine open. The pure helpers stay low-level: their `resume_safe`/`complete` flags are
  accounting statements, not identity checks and not permission to route, and they are
  deliberately not re-exported as an authorization API.
- **A completed record is gated, identified and lineage-checked before it is published
  or bound.** The whole cumulative and per-link history must account for every charge
  the ledger holds; then the record's named `ledger_file` must be this ledger's own
  path and the parent board's actual bytes must hash to the digest the record declares
  (the same helper the preflight uses, so the two cannot disagree); then a continuation
  must present the file of the record it continues; that file must itself be a
  completed campaign record for this ledger - validated by the limiter's own
  `validate_campaign_record` and identified by the same helper the preflight uses,
  so a bare JSON object with a `previous_record_sha256` field is refused - and its
  digest must be either the ledger's current binding or an interrupted finalization
  its own bytes verify. An older in-chain digest is refused as an old record and a
  chain that does not end at its binding is refused as broken - each before the
  first `mkdir` and the first write, so a refused publication leaves the filesystem
  byte-identical. Publication and binding are one step, and a crash between the two
  writes is still recoverable because the successor declares the binding as its own
  predecessor.

### Verification — 2026-09-29 (corrections 1 and 2 of the rework applied)
- **49 engine-free unit tests** (`tests/agent/test_ledger_unit.py`,
  `tests/agent/test_interruption_guard.py`), both wired into the combined unit group of
  `tools/reliability/check_phase.py`. They cover all four ceilings, persistence and
  reloads, alias merges and rollback, no-charge resets, interrupted and unbound evidence,
  missing records, extra settled and unsettled charges, redistribution at equal totals,
  malformed and wrong-identity inputs, the positive fresh/resumed/interrupted-finalization
  paths, and byte-for-byte snapshots proving that every refusal writes nothing and opens
  no engine.
- **39 mutation checks, all caught.** Each test module runs its own invariant battery
  against 19 (ledger) and 20 (guard) deliberately broken copies of the source in a fresh
  interpreter; a mutation that the battery accepts fails the test, so a silent regression
  in any invariant cannot pass the suite. Ten of the guard mutations cover the checks
  the two corrections added to the publication API.
- **`EXPECTED_NATIVE_TESTS` corrected to 146.** The strict gate carried 144 while a fresh
  `--collect-only` over the native group collects 146, which would have let two native
  tests disappear unnoticed.

### Limits, stated — 2026-09-29
- The ledger is a **single-writer** store: it persists by writing the whole file, and
  nothing here serialises two processes against one path. Durable journaling - a
  crash-safe record of a transaction in flight - remains design-only and is not
  implemented.
- The ledger's binding advance trusts the predecessor digest its caller supplies, because
  it cannot open a record file. The composed **preflight** and the guarded
  **publication** step are what make that legitimate: publication verifies the supplied
  predecessor file's bytes and its declared lineage before it writes, so a run must not
  advance a binding by any other path.
- Version: `pyproject.toml` moves from `1.1.0` to `1.2.0`, and the `README.md`
  `<!--VERSION-->` marker moves to `v1.2.0` with it.

## v1.1.0 — 2026-09-29 — agent reliability layer

### Phase 30 bounded recovery, refused, and independently verified (T31D) — 2026-09-29
- **The capped campaign held its budget and still refused.** T30R2 re-ran phase
  30's recovery idea under the accepted limiter - at most five charged native
  calls per stable logical link, twelve links, sixty transactions across all
  resumes - and conserved the cap: **55 of 60** charges, **12 links**, no link
  above five. Two joins were retained, taking the restoration debt from twelve
  to nine, but the final candidate is not promotable, the campaign is
  quarantined at 55/60 and the accepted pointer did not move. Account:
  `docs/agent-work/reliability/phase30/BOUNDED_RECOVERY.md`.
- **A killed launch kept the accounting honest and broke the record.** A first
  launch died after charging seven transactions and writing no campaign record;
  the continuation ran from the same ledger, so the ceiling held cumulatively
  (55) while the continuation's own record accounts for 48. The frozen record
  therefore fails the limiter's own `validate_campaign_record` and
  `reconcile_resume_charges`, and cannot serve as a resume base.
- **Rollback coverage is narrowed, append-only.** The campaign's own report said
  every rejected transaction carried a proven rollback; the frozen evidence
  supports that for the 46 resumed rejections only. The seven killed-launch
  charges - six settled records plus one unsettled reservation - carry no
  per-attempt receipt, so restart isolation is all that is proved for them and
  whether the killed attempts changed copper is unknown; the criterion requiring
  a proven rollback for every rejection is unmet and the author campaign stands
  at REQUEST_CHANGES. The phase-31 successor replaces that path with a fail-closed
  refusal and a prepublish history validation; durable journaling stays
  design-only. The clarification is recorded in `HISTORY.md` and the new phase-30
  page; no earlier entry was edited and no broad rollback proof is claimed.
- **Independent verification, and no promotion.** T30R2V recounted the charges,
  re-hashed all 108 frozen artefacts and 24 protected inputs, and re-measured the
  boards with fresh processes and complete pinned CLI gates. The final candidate
  adds nineteen relevant native identities (all isolated-copper) and eighteen
  terminal split relations against the accepted generation and the canonical
  original, with both CLI gates refused; the same deviation is already present in
  the frozen T30R start, and the final candidate is clean against it. Eligibility
  is not met, and the accepted pointer is unchanged.

### Phases 25-29 integrated, and one scoped backup (T29I) — 2026-09-29
- **Journalled, then integrated.** Phases 25-29 shipped their own plan and
  result documents but no `HISTORY.md` or `CHANGELOG.md` entry; T29I owns both,
  so their accepted accounts are recorded here and in `HISTORY.md` from the
  frozen documents. No frozen board, rule, accepted pointer, pinned binary or
  accepted report was edited.
- **The harness is green on the exact staged set.**
  `bash tools/reliability/check_phase.sh --strict --expected-native 146` ->
  exit 0: **640 unit tests, 146 native tests, 0 skips**, and the patch group
  reports that the five-patch engine series applies to the pinned commit and
  reproduces this checkout's engine tree byte for byte. `git diff --check` is
  clean, and the staged set is scanned for secrets, private board identifiers
  and coordinate-like decimals before it is committed.
- **Version.** `pyproject.toml` moves from `1.0.1` to `1.1.0`, and the
  `README.md` `<!--VERSION-->` marker moves to `v1.1.0` with it, so the two
  agree. `README.md` names no other version.
- **Two phase-5 documents redacted for privacy.** Seven lines across
  `docs/agent-work/reliability/phase5-kicad/CHECKPOINT.md` and
  `docs/agent-work/reliability/phase5-kicad/RESULT.md` carried real-board
  identifiers - one component pad reference in four places, five supply net
  names (two named singly and one compact three-name form) and one
  KiCad-generated net name - written before the phases began leak-scanning their
  own reports. Astra approved a narrow correction in the T29I scope, and each
  identifier now reads as the plain description the same documents already use
  for withdrawn material. Every historical finding, withdrawal notice and
  aggregate count is preserved: the four isolated-copper identities and their
  split, the 33 split relations and their 20 + 12 + 1 breakdown, the fourteen
  relations lost on five nets, and the GND 34 -> 44 measurement are unchanged.
  Nothing else in either document, and nothing at all in the board, the rules,
  the engine or the tests, changed.
- **Backup.** One scoped commit on `feat/agent-reliability-actions`, pushed to
  the existing fork `JaredReabow/PCBWorld`. Private board evidence is not in
  this repository and is not staged.

### Phase 29 - the netless-first reporter repaired, evidence fail-closed — 2026-09-29
- **The last ordering dependence, diagnosed read-only (T29N).** A family that
  declares its netless same-logical-pad partners *before* the netted pad reports
  nothing at all: the R-tree filter claims the pair's layer for the netless
  member, and the netted member's later visit is filtered out. Reproduced on
  synthetic copper and on the frozen generations; the accepted pointer never
  moved. Diagnosis and the proposed validation contract are in
  `docs/agent-work/reliability/phase29/NETLESS_ORDER.md`.
- **Repaired in both front ends (T29A, engine patch `0005`).** The pad-clearance
  visitor now releases the `checkedPairs` layer claim when a same-logical-pad
  pair is visited from its netless member and the partner is netted - in the RL
  provider and in the stock 9.0.8 overlay that `kicad-cli` loads from
  `_pcbnew.kiface`. The release is deliberately narrow: an exempt pair keeps its
  single claim and a filed pair keeps its de-duplication. No rule, waiver,
  severity, clearance, proximity or exemption changed. Rebuilt module
  `819a26f1…`, kiface `c23f8cb9…`, stamp `b47d4f0c`; `check_engine_patches.py`
  reports the five-patch series applying to the pin and reproducing nine files
  byte for byte with identical wire copies.
- **Serialized native evidence now fails closed (T29G).** A malformed or
  incomplete violation multiset used to be readable as "no findings". The gate
  now validates the reader's shape, provenance and accounting before any
  comparison, and a consumer that cannot prove its evidence refuses instead of
  returning a verdict. Changed: `pcb_world/agent/drc_gate.py`,
  `tools/reliability/verify_saved_artifact.py` (before the engine is opened),
  `tools/reliability/check_phase.py`, and the new
  `tests/agent/test_evidence_validation.py`.
- **The two project sidecars are policy-equivalent for this board (T29P).** On
  identical board bytes the reduced EasyEDA-export `.kicad_pro` and the full
  KiCad-rewritten one resolve to the same effective routing and DRC policy -
  7934 relevant identities on the accepted generation and 317 on the restoration
  baseline, the same eight netclasses, and the same class on each of the 191
  populated nets - under both the pinned native engine and the pinned build-tree
  CLI. Eight one-block hybrids of the reduced file resolve identically, so no
  single block is load-bearing. This measures the settings this board and this
  generation exercise; it is not a claim that the two files are
  interchangeable, and teardrops and tuned patterns are outside its scope.
- **The six historical restorations re-measured under the accepted strict
  consumer (T29C).** All six retain with complete CLI, terminal and native
  evidence. The representation comparison is now a whole-board structural
  contract that accounts for every node on both sides and folds only two
  re-spellings, each at one exact position, named and counted, with duplicate and
  missing identities refusing. The experimental candidate's mandatory
  original-relative common-fill comparison still refuses: 19 added
  isolated-copper identities and five fragmented clusters needing 12 minimum
  joins. Nothing was promoted and the accepted pointer did not move.
- **Independent verification, then Astra.** T29AV (the repair and its patch
  reconstruction), T29GV (the fail-closed contract under attack, and the sidecar
  study re-derived from raw captures) and T29CV (the refresh, the fold contract,
  the manifests and the protected hashes) each recomputed from raw packets on a
  frozen packet and recommended ACCEPT; Astra accepted T29N's diagnosis and the
  T29A, T29G, T29P, T29GV, T29C and T29CV results.

### Phase 28 - the shorting reporter repaired at the source — 2026-09-28
- **Repaired in both providers (T28A).** Both copper-clearance providers
  canonicalise a `(BOARD_ITEM*, BOARD_ITEM*)` pair by pointer address before
  touching their per-run `checkedPairs` cache, but only the R-tree *filter*
  applied the same swap. The visitor therefore wrote `has_error` to an entry the
  filter never read back, and one pair was reported once per copper layer it
  shared. The visitor now uses the filter's own canonical order - in the RL
  provider and in a **new overlay copy** of stock KiCad 9.0.8's provider, the one
  `kicad-cli` loads from `_pcbnew.kiface` - so both front ends agree on the
  multiplicity of the same finding. Mutex scope, layer tracking, `has_error`,
  cancellation, the genuine-short and clearance branches and severity handling
  are untouched; no pair-set comparator, class waiver or rule change was added.
- **Patch and harness.** New sequential engine patch
  `patches/engine/0004-reporter-cache-canonical-order.patch`, delivered with an
  added `cp -p` in `engine/build_rl_router.sh`.
  `tools/reliability/check_engine_patches.py` now accepts a patch that *adds* a
  file the pin does not carry (the overlay copy) while everything else is
  unchanged. New deterministic fixture `tests/engine/reporter_fixture.py` and
  regression `tests/engine/test_reporter_cache_canonical_order.py` (six tests over
  both providers), plus `tests/engine/test_phase_gate_groups.py` pinning the
  gate's new required `patches` group.
- **Differential.** Three frozen phase-27 boards, twelve fresh native processes
  and six fresh CLI invocations each: the same 30 shorting item pairs with stable
  multiplicity, no lost genuine finding and no unexplained movement in the other
  relevant classes, with terminal partitions and board/rule hashes unchanged.
- **The historical replay holds (T28C).** The six frozen phase-26 restoration
  steps were re-measured against the repaired loaded provider and replayed
  through the accepted T27A verifier: **all six retain**. Nothing was routed,
  refilled or promoted, and the candidate still carries the refill's DRC debt.
- Independent T28BV and T28CV verification; Astra accepted both.

### Phase 27 - a retention verifier that decides on the reopened bytes — 2026-09-28
- **The verifier was tightened (T27A).** It pins the four boards a retention
  decision reads, decides every board-level condition on the **reopened**
  post-refill bytes, refuses on an incomplete or unpinned input, removes the
  single-pass reading of the native condition, and replaces the campaign's
  component-keyed attempted set with a stable route-offer identity. A diagnostic
  single-instance mode may report conditions but can never return a retention
  verdict. No board, rule or pointer moved.
- **The "items shorting two nets" hold, diagnosed (T27B).** On byte-identical
  retained boards the pinned native reporter returns 30, 33 or 36 rows for this
  class across runs. What moves is only the number of copper layers each pair is
  reported on, for four pairs; the item-pair set is invariant at **30 pairs**
  across 193 passes and the pinned CLI's own 20 frozen reports. The row count is
  therefore not a reproducible quantity for this class and the pair set is, but a
  pair-set gate also needs each UUID to name one unchanged physical item, which
  the original import does not guarantee. Diagnosed read-only.
- Independent T27AV and T27BV verification; Astra accepted both.

### Phase 26 - the reconnection campaign, and where the CLI findings come from — 2026-09-28
- **The campaign (T26A).** Selection order, attempt ladder and retention rule
  were declared before the run. All eighteen remaining links were attempted on
  disposable copies; **six joins were retained**, each surviving a legal refill,
  save and reopen of its own zones, and twelve were refused. Every attempt
  restored its copper and every refusal was recorded. The candidate is **not
  promotable**: the refill's whole DRC debt is still carried and the complete
  pinned CLI refuses it against both the accepted generation and the canonical
  original. The accepted pointer did not move.
- **CLI attribution (T26B).** Three frozen boards were re-measured with the
  complete pinned reporter, four fresh processes each, every board staged with
  the canonical original's own project and rule bytes. The account of which
  classes the refill causes, which the reporter's configuration causes, and
  which is not reproducible at a constant count is in
  `docs/agent-work/reliability/phase26/CLI_ATTRIBUTION.md`.
- Independent T26AV and T26BV verification; Astra accepted both.

### Phase 25 - the reconnection pilot — 2026-09-28
- One of the nineteen component joins the phase-24 refill cost was restored on a
  disposable copy of the refilled board, placed by the pinned native router under
  the pinned project and rules, and it **survives a legal refill of its own
  zones**. The candidate adds zero relevant DRC identities and zero fragmentation
  against the refilled board it was built from, and takes the fragmentation
  against the accepted generation from 7 clusters / 19 minimum joins to 6 / 18
  (and the canonical original from 33 broken relations to 32). It is **not
  promotable**: eighteen joins remain and the refill's DRC debt is still carried.
- Independent T25V verification; Astra accepted.

### Phase 24 - what a refill under the current rules does — 2026-09-28
- **The supported refill path is proved, and it changed the fill.** On a
  disposable copy of the accepted generation, `KiCadEngine.fill_zones(rules)`
  over the pinned build's native `fillZones` returned success in 2.6 s and
  changed the stored derived copper of **95 of the board's 177 zones** (stored
  fill vertices 88 596 -> 140 822). The change survives save and reopen: a fresh
  process on the saved candidate reproduces the same fill digest, the same
  non-zone digest and the same DRC result. Everything that is not a zone is
  untouched - the 7 221 top-level non-zone items hash to the same multiset, with
  the track, via and footprint counts identical, and no zone added, removed or
  re-typed. An independent verifier re-derived that from the same two files
  under three separate encodings (decimal-quantised, quote-aware, byte-raw) and
  found 0 items added and 0 removed across all 7 221 in each, plus exactly three
  declaration edits, each one `(fill ...)` -> `(fill yes ...)` and nothing else.
  The source board is byte-identical after the run and the accepted pointer
  never moved.
- **The refusing classes resolve, and connectivity breaks doing it.** Two
  whole-board native passes on the accepted copy reproduced phase 23's own
  relevant multiset exactly (8 130 total / 7 930 relevant,
  `2df69ffe72f5e522`). After one refill under the recovered rule file the same
  board measures **533 total / 313 relevant** (`ca2220dc9235845f`): **7 488
  fill-clearance identities and 147 of 149 hole-clearance identities** resolved.
  Counted in one process around the refill that is 7 665 identities resolved and
  49 added connectivity identities; counted across fresh processes it is 7 666
  and 50, the difference being one connectivity pairing the reporter chose
  differently, not copper. What the refill added is the other half: the
  isolated-fill class goes from a raw `1` to `19` (a net `+18` - nineteen new
  identities and one resolved), while the pad-group count rises by 19, the
  ratsnest by 19, and **7 reference clusters fragment** recorded as 33 witness
  pairs, needing at least 19 rejoins. Those are separate counts of one refill,
  not an item-for-item correspondence - the accepted generation's connectivity
  depends on pours filled finer than the rule file requires. Of the candidate's
  313 relevant identities, 294 persist and are candidates, not conclusions; the
  19 isolated fills are new, not persistent. **This is a board-wide measurement
  of the class the refusals shared: no transaction of the 24 was replayed, and
  phase 23's attribution (3 proved, 21 supported) is neither extended nor
  weakened.**
- **The complete pinned CLI was run against the canonical original and did not
  accept the candidate.** Build-tree reporter 9.0.8 (caps removed), two runs per
  board, both sides staged with the same project and rule bytes: `clearance`
  7 488 -> 0 with zero added identities, `solder_mask_bridge` 90 removed,
  `starved_thermal` 37 added and 100 removed, `isolated_copper` 4 added,
  `track_dangling` 1 added, `shorting_items` 30 -> 30 with nothing moved,
  unconnected items 149 -> 154 with a non-reproducible pairing. Verdict
  **unverified, not accepted** - the reporter sees effects the native severity
  configuration does not. A save also rewrites the project sidecar into the
  writer's own schema: every value the two files share is equal, including all
  eight netclass clearances and the board minimums, but the candidate's `rules`
  block **gains 11 keys** the imported file did not carry, so the block is a
  superset rather than unchanged. Both sides of every comparison were staged
  with the reference's project and rule bytes, and a candidate needs its sidecars
  normalised at staging.
- **The known cross-process variation is not in play and that is measured, not
  assumed.** `Items shorting two nets` is 30 in all six whole-board passes, is
  absent from both deltas' added and resolved sets, and the CLI's own
  `shorting_items` class moved zero identities. The only difference between the
  within-process delta and the cross-process delta is one connectivity pairing.
  No routing campaign, planner call, rule change, footprint move or engine/wire
  change; nothing staged, committed or pushed.

### Phase 23 - what refused the 24 real closures — 2026-09-28
- **The 24 closure-then-refusal evaluations of the phase-17 trial are accounted
  for, with three reproduced and their retained rows attributed.** Phase 17's
  fresh run closed a connection and was refused by the native gate in 24 of its
  109 plan evaluations. This phase reconciles all 24 against the trial's own
  records and the phase-17 taxonomy, reproduces three of them reversibly from the
  immutable accepted board, and fixes the one harness defect the reconciliation
  proved. No engine or wire file, footprint, zone, rule value or violation class
  was changed, and no candidate was promoted: the accepted pointer and its board
  hash are unchanged.
- **The reproduced cause: the plan's own new copper, on top of a stored fill of
  another net.** The 24 span 4 edges and 5 class fingerprints and are 24 distinct
  (edge, plan) attempts with no duplicates; every one closed the connection, was
  refused, rolled back with the rollback verified, and left the board on the
  accepted digest. Their classes are one clearance class and one hole-clearance
  class and nothing else - no connectivity, zone-outline or rule-load failure
  appears in any of them. One record per edge was then replayed exactly (same
  pair, mode and waypoint list) through the same public `connect_targets`
  transaction on a byte-identical scratch copy with whole-board native DRC, zero
  model requests. Those three deltas added **15 distinct relevant identities**
  between them and retained **14 sampled rows** (8 of 9, 5 of 5, 1 of 1); in every
  one of the 14 rows one side is an item that did not exist before the transaction
  - the plan's own track or via - and the other is an existing board zone, at
  exactly zero measured clearance in most rows and below the required minimum in
  the rest. The fifteenth identity is counted but not attributed, and the other 21
  of the 24 plans carry class-level evidence only, so reading them as the same
  shape is a supported hypothesis rather than item-level proof. The accepted board
  file and pointer have the same hashes after the run as before it, and each
  transaction's in-memory copper digest was equal before and after.
- **The same two rules already fire 7 637 times on the accepted generation.** Two
  whole-board passes over the unmodified generation returned the identical
  relevant set: 8 130 findings, 7 930 relevant, of which 7 488 are the same
  fill-clearance rule and 149 the same hole-clearance rule; 7 636 touch a board
  zone and 164 of the fill-clearance findings sit at exactly zero clearance against
  copper that predates any routing. The 7 488 cluster tightly around one value,
  i.e. the pours were filled at a finer clearance than the recovered rule file
  states. Those pre-existing findings support the same hypothesis for the
  refusals that were not reproduced: the board itself already carries the refusing
  condition at scale, and the router does not model pours while the gate correctly
  measures the fill that is stored. They do not prove the cause of all 24. The
  remedy is a board
  or router decision - refill under the current rules with its own generation and
  whole-board verification, or route pour-aware - and neither is a gate change.
- **A real evidence defect, proved and fixed.** A refusal record could not say how
  many findings it added by class. `AttemptRecord.drc_classes` was counted from the
  DRC delta's `added_relevant` rows, and that list is deliberately capped - so a
  refusal was recorded with at most eight findings' worth of classes however many
  it added. On this trial 9 of the 24 records under-count, the worst recording
  eight where the delta added 50, and the public refusal-class aggregate inherited
  the truncation. `DrcDelta` gains `class_histogram()`; `to_evidence()` carries a
  complete `added_relevant_class_counts`; `RoutingRunner._record` records that and
  keeps the rows as the fallback and as the source of the (deliberately few) hint
  positions. The histogram counts **distinct added relevant identities** - the
  delta's keys - and not occurrence multiplicities, which the payload carries
  separately. Additive and evidence-only - nothing reads these fields to decide
  anything - and backwards compatible: a payload without the new field still
  produces the old histogram.
- **Tests.** `tests/agent/test_drc_classification.py` pins that the added-class
  histogram is not capped by the row sample, and
  `tests/agent/test_runner_scripted.py` pins that a refusal record carries every
  added class rather than the row sample; both fail against the unpatched module
  and pass with it. `tests/agent/fake_engine.py` gains `violations_on_fix` so one
  fix can add more findings than the row cap.
- **Native-DRC stability, measured.** Twenty fresh processes each ran one
  whole-board pass over the same unmodified generation: 18 returned 8 130 / 7 930
  with an identical relevant multiset, and 2 returned 8 133 / 7 933, the entire
  difference being one class, `Items shorting two nets`, 30 vs 33. In the one
  captured with positions the three extras are a single location reported on three
  copper layers; the other 7 933 process was captured as counts only, so whether
  its extras are the same three stays unknown. Four consecutive passes inside one
  process were identical, and no baseline/candidate pair in this phase moved that
  class - the gate diffs two passes of one session. Recorded because it bounds how
  much of the profile is a property of the board alone, not because it refused
  anything here.
- **Nothing promoted, nothing staged.** `bash tools/reliability/check_phase.sh
  --strict` -> 496 unit + 138 native, no skips; `tools/check_separation.py` 4/4;
  `check_engine_patches.py` byte-equal to the pin; `git diff --check` clean. The
  accepted generation `6c4f8ab81b83...f593477cf1b` and its pointer have the same
  hashes after the run as before it, and no engine call in this phase wrote to the
  generation directory or to the scratch copies. Per-edge geometry, net names and
  rule values stay in the private `phase23_drc/` evidence tree; the aggregate
  account is `docs/agent-work/reliability/phase23/RESULT.md`.
- **The next step is a measurement with its own validity gate.** Refill a
  disposable copy under the pinned rules only if the engine reports the refill
  supported, then save and reopen the refilled board and prove from the reopened
  file that the fill geometry actually changed, and only then re-run the
  whole-board native DRC and classify every finding as resolved, persistent or new
  - with an unsupported or incomplete refill reported as its own outcome that says
  nothing about the findings. A persistent finding under a proved refill is a
  candidate genuine clearance that a further measurement must confirm; this phase
  does not assert that persistence implies genuine clearance.

### Phase 22 - bounded alternate-anchor doglegs — 2026-09-28
- **A bounded dogleg family over component-proved anchor pairs found no
  candidate, and it moved the bottleneck off the via.** Phase 21 measured every
  proved same-net anchor pair behind the 49 `connection_not_verified` edges with
  a *straight* segment plus one through via and found no candidate among 21 276
  pairs. This phase keeps the anchor proof and the measured proxy and adds the
  smallest geometry that leaves: a short escape from either anchor, one or two
  bends and a main leg, with at most one through via, which must sit on the
  measured polyline. The pool is phase 21's own - 33 092 raw anchor-pair
  product, 10 219 refused as already connected, 1 597 as one component,
  **21 276 eligible pairs**, equal to phase 21's per-edge `evaluated` count under
  the same predicate, re-derived with residual 0 - and the contract caps the
  search at 16 pairs per edge, so **560 pairs** selected by a deterministic
  ranking (smallest total anchor movement, then anchor coordinates), with the
  four coincident cross-layer edges searched first. The subset is bounded on
  purpose and says nothing about the 20 716 eligible pairs it did not select.
- **The declared family and the instrument.** Per anchor, 28 escapes: eight
  compass headings plus the direction toward the peer, at 0.4 mm, 0.8 mm and
  1.2 mm, plus the zero-length escape, so every pair's first variant is phase
  21's straight segment; 784 combinations per pair, at most two bends, one
  through via on a cross-layer variant. Measurement is phase 21's unchanged:
  discrete copper (foreign and netless) exactly by segment-to-shape distance,
  pour fill and rule areas at a 0.125 mm pitch under the 1-Lipschitz allowance
  (0.05 mm inside 0.5 mm of each anchor), unknown and rule areas refused, and
  two prefix walks along the polyline bounding the via's feasible arc-length
  window exactly.
- **0 candidates in 438 563 variants; 86.6 % stopped on the first
  turn.** Pair verdicts, taking each pair's best variant: 230 blocked on copper
  and 156 on pour of the 390 same-layer pairs; 164 whose two clear prefixes
  never met and 5 with a feasible window but no clear column of the 170
  cross-layer pairs; 4 unproved at the declared pitch, which the bounded
  escalation re-measured at 0.05 mm and 0.025 mm and resolved to measured pour
  refusals, leaving **0 unexamined and 0 unproved among all 560**. The stage
  attribution is the finding: 287 936 variants were refused by the escape out of
  the start anchor and 91 764 by the target anchor, against 36 924 on the main
  leg, 21 818 that never met and only 121 that reached a feasible window with no
  clear via column. At the four coincident cross-layer points this family
  reaches, the via is no longer the binding term. A turn does buy length: 429 of
  the 560 selected pairs reached more than 1 mm of proved clear path.
- **Independent check and controls.** A fresh process with a fresh engine, using
  phase 21's independent instrument (its own inventory, cell index, arithmetic
  and dense walk, sharing nothing with this search), re-proved every row's
  endpoints and separation and dense-walked every recorded polyline at 0.05 mm:
  560 rows, 560 comparisons, 0 over-reports (307 agree within a dense sample,
  253 where the checker found more clear path). Positive controls found a clear
  2 mm-escape / 2 mm-run / 90-degree dogleg on the board at the 763rd scanned
  origin of a 2 mm grid and 5 clear through-via columns in a 703-point
  four-layer grid. Private unit tests pass 3 171 assertions, including the
  cached/pruned implementation checked against an uncached, unpruned statement
  of the contract over every variant of four synthetic cases.
- **No trial, no board touch, no promotion.** The contract makes a transaction
  conditional on a credible candidate; with none, 0 trials were run. No route
  was started, no checkpoint taken, no DRC run, no board file written; the
  accepted generation `6c4f8ab81b83...f593477cf1b`, its pointer, its geometry
  digest and every scratch copy are unchanged (`checkpoint_count()` 0 -> 0).
  Nothing committed, staged or pushed, and no public harness file changed.
  Per-edge nets, coordinates, geometry and rule values stay in the private
  `phase22_doglegs/` evidence; the aggregate account is
  `docs/agent-work/reliability/phase22/RESULT.md`.

### Phase 21 - component-proved alternative anchor pairs — 2026-09-28
- **Every unconnected pair of proved same-net anchors behind the 49 edges was
  measured, and not one has a straight candidate path.** Phase 18 showed each of
  the 49 `connection_not_verified` edges has proved substitutes on its own net
  but never joined two of them; phase 19 searched a window around the *offered*
  segment; phase 20 explained the four coincident columns. This phase asks the
  remaining question - with **both** endpoints free to move, is there a straight
  candidate path between any pair whose two sides are still unconnected? The
  whole anchor list of every native component was taken from the harness's own
  component API, each anchor proved independently with the engine's
  point-identity rule and its own cluster, and every pair measured: discrete
  copper exactly by segment-to-shape distance, pour fill and rule areas sampled
  at 0.125 mm under the 1-Lipschitz allowance (0.05 mm inside 0.5 mm of each
  anchor), and cross-layer pairs bounded by two prefix walks whose union is the
  segment before any through-via column is tested. **33 092 pairs enumerated,
  11 816 refused as already connected or one component, 21 276 evaluated,
  0 candidates, 0 pairs left unexamined by the budget.** The 33 092 is the
  search's own pool product, not the component API's raw list: per side,
  `pool = raw - refused during proof - duplicate points + the offered point
  when the API had not listed it`. The API lists 1 414 anchors across the 98
  side-instances, 11 distinct points were refused during proof and the
  offered point was added 29 times, giving pools of 1 432 anchors; the raw
  product is 33 071 and the product of the pools is 33 092. A private
  reconciliation script re-derives that identity edge by edge with a
  residual of 0 and reproduces the published pair count exactly.
- **Within the family this phase measures, the straight leg leaving a pad is
  what binds, not the via.** The clear prefix of the 23 374 measured legs is
  dominated by 0.5-1.0 mm (13 589 legs) and 0.1-0.5 mm (4 335); 11 682 pairs
  fail because the two legs' clear prefixes never meet, 6 447 same-layer pairs
  stop on pour contact inside the requirement and 3 134 on copper. Only
  **five** pairs reached a feasible via interval and then found no clear column -
  the reverse of the phase-20 picture. This is a statement about a straight run
  plus one through via: it does not say a route that leaves a pad and turns is
  impossible, and it makes no redesign necessary.
- **The escalation is what makes the answer about the board rather than about
  the pitch.** Pairs the declared pitch alone refused were re-measured finer:
  0.125 mm left 8 unproved, 0.05 mm left 8, 0.025 mm resolved all 8 to a
  measured copper refusal. **21 276 of 21 276 pairs resolve to a measured
  refusal or a candidate, with 0 unexamined and 0 unproved.** Two defects were
  found by cross-checking and fixed: a 1-Lipschitz prefix bound applied in the
  direction that *overstates* a clear prefix, caught by the independent checker
  on a corridor running almost parallel to foreign copper; and a walk that
  stopped at the first sampling-allowance sample instead of looking for a real
  contact just beyond it, which had mis-labelled 11 272 pairs as instrument
  artefacts. The measured magnitudes of both are in the private evidence, not
  here.
- **Independently checked, and the search is never the optimistic one.** A
  second implementation in a fresh process - own inventory, own index, own
  distance arithmetic - re-derived all 49 edges, re-proved both anchors and the
  peer separation, and dense-walked 4 000 rows at 0.05 mm: 4 206 prefix
  comparisons, 2 769 agreeing within one sample, 1 437 where the checker found
  *more* clear path, and **0 where the search claimed more than the checker
  measured**. Positive controls fire: the open strip phase 19 used is clear end
  to end and comes back as a candidate, and 94 of 11 771 grid points have a
  through-via column clear on all four copper layers.
- **No trial, because no candidate, and no board was touched.** The contract
  makes a transactional trial conditional on a credible candidate; with 0 of
  21 276 there was nothing to trial, so no route was started, no checkpoint was
  taken (`checkpoint_count()` 0 -> 0), no board file was written, and the
  accepted artifact, its pointer and the scratch copy re-hash unchanged at
  `6c4f8ab81b83...f593477cf1b`. No legality finding is claimed: native DRC was
  not run, the margins are a clearance proxy rather than rule authority, and
  pour distances come from the fill the loaded board carries. Nothing was
  committed, staged or pushed, and no public harness file changed, so no public
  test was added or altered. Coordinates, net names, per-edge geometry and rule
  values stay in the private phase-21 evidence tree;
  `docs/agent-work/reliability/phase21/RESULT.md` carries the aggregate account.

### Phase 20 - coincident cross-layer via-column diagnosis — 2026-09-28
- **All four coincident cross-layer edges were diagnosed, and every column is
  stopped by a measured object.** Phase 19 left four offered
  `connection_not_verified` edges whose two proved endpoints coincide in XY on
  different copper layers, whose two proxy-clear sides meet at exactly that
  point, and where no clear through-via column exists. On the unchanged accepted
  board all four were re-measured at the shared point on every copper layer the
  harness's via spans: **three are refused by foreign pour fill** - on at least
  one layer the point lies inside the pour, where a via would sit on that
  pour's copper, and at two of those edges the pour on the endpoint layers is
  also closer than the via's own radius plus the board's clearance, so the
  column fails with the sampling allowance removed - and **one is refused by a
  rule area that disallows vias** on its target layer, with a foreign pad just
  inside the margin on its start layer. Copper items, drill and unknown account
  for none of the four.
- **A bounded probe found no proxy-clear candidate anywhere in its declared
  window.** Each shared point got an axis-aligned square at **0.05 mm** pitch,
  **1.5 mm** half-size escalated to **3.0 mm**, with every point measured on all
  four copper layers through the engine's read-only zone query and the harness's
  own shape-aware item model. Two classes were tested: a **through** column,
  clear on every layer a through via spans (the phase-19 rule, fail closed), and
  a **span** column, clear only on the endpoint layers - the bracket for a
  blind/buried via - each at the allowance-inflated margin and at the
  un-inflated one. Over **293,792 point-layer measurements**: **0 points clear
  for either class in either window at either margin, and 0 the engine could not
  resolve.** Widening the window changed nothing and removing the allowance
  changed nothing, so within this declared window the refusal is the board at
  those points. Column-clear points would then have had both straight legs back
  to the offered point sampled at the same pitch and tested at the track margin;
  there were none to test. These clearances are a proxy, not native DRC
  authority - no DRC was run for this phase - and the pour distances come from
  the loaded fill, which is not verified against the current rules.
- **The harness can place through vias only - read from the pinned engine's own
  source, not from a wrapper field.** A capability check (22/22 checks) pairs
  the loaded extension with its sources by content - the engine's patch-tree
  content hash against the stamp written beside the extension, the build tree
  the CMake cache names, and byte identity of every router file the overlay
  provides - and then reads: the router's size settings initialise the via type
  to `THROUGH` and the headless path never re-types it; the placer derives its
  span from the layer-pair map, which stays empty, so a through via spans top to
  bottom copper; only the interactive GUI tool ever sets a layer pair; the
  headless interface and the harness's structured action schema expose no via
  type, layer pair or span; and the microvia minima in the rule structs are
  board metadata no placement path consumes. A non-through via would need an
  engine change - but that capability question is not what decides these four
  edges: the span-restricted bracket is proxy-clear nowhere either, so a
  blind/buried via of the endpoint span would meet the same measured pour and the
  same rule area. What the measurements show is a **local design constraint** at
  four specific points; whether anything should change, and what, is a decision
  for the plan and the physical design, and this window does not establish that a
  design change is required for every route plan.
- **Independently re-measured, and phase 19's statuses reproduced.** A second
  script in a fresh process with its own engine, session, layer conversion, raw
  zone query (no wrapper) and flat O(items) copper scan re-derives the four edges
  by exact key and re-measures the column verdict on every layer and each
  anchor's clear state at the un-inflated track margin: **16/16 layer verdicts
  and 8/8 anchor verdicts agree, 0 disagreements.** The two edges phase 19
  recorded as `no_path` have both anchors usable at the un-inflated track margin;
  the two it recorded as `target_blocked` have a target layer that is not clear
  even there. The classifier is covered by 41 assertions on synthetic inputs
  where the answer is true by construction, including that an unresolved point
  never hides a measured blocker.
- **No trial, because no candidate under the measured proxy.** The contract makes
  a trial conditional on a specific candidate under the measured proxy; with
  zero clear columns of either class in either window there was nothing to trial,
  and a route on such a point would be an unbounded re-roll rather than a
  measurement. No legality finding is claimed: native DRC was not run, the
  margins are a clearance proxy rather than rule authority, and the loaded fill
  is not verified against the current rules. No route was started, no checkpoint
  was taken (`checkpoint_count()` 0 -> 0), no board file
  was written, and the accepted artifact, its pointer and the scratch copy
  re-hash unchanged at `6c4f8ab81b83...f593477cf1b`. Nothing was committed,
  staged or pushed. Coordinates, net names, per-edge geometry and rule values
  stay in the private phase-20 evidence tree;
  `docs/agent-work/reliability/phase20/RESULT.md` carries the aggregate account.

### Phase 19 - bounded 2D path test — 2026-09-28
- **A read-only 2D clearance-aware search over the same 49 edges, and it found no
  candidate path.** Each phase-11 `connection_not_verified` edge was joined to
  the unchanged accepted board by exact offered-edge identity (49 offered, 49
  joined, 49 searched, 0 missing, 0 ambiguous). Each edge got a lattice over a
  window following its offered segment - 4 mm to each side and 2 mm beyond each
  end, escalated to 6 mm / 4 mm - at 0.25 mm pitch, with every copper layer a
  through via spans in the graph and a via treated as occupying all of them.
  **0 of 49 edges produced a candidate path in any of five runs, in either
  window.** The escalated window reproduced every count exactly, so widening it
  connected nothing.
- **Two item models, because phase 18's pad model overstates this board.** The
  census modelled every pad as the circle of its longer dimension, which for the
  elongated pads this board is full of claims copper far beyond the pad's own
  extents - safe, but loose enough to refuse most anchors before any routing
  question was asked. Both models are reported: the harness's own shape-aware
  extents and the phase-18 apron. The apron runs block 39 and 41 of the 98
  anchors and are the direct explanation of phase 18's "no clear lane" result,
  while under the harness's own extents 9 anchors are still blocked outright - a
  few by **netless** copper overlapping the anchor, the rest by pour just inside
  the margin - and the other 40 edges have both anchors usable with the start and
  target in different proxy-clear regions. A declared 0.025 mm sampling
  allowance is carried so the clearance margin bounds the space between samples
  rather than being a fudge factor, and a separate `endpoint_relief` run holds
  the two offered anchors - exact measured points on their own proved copper -
  to the un-inflated margin.
- **The search does find paths on this board where the board has room.** A
  positive control walks a deterministic 2 mm grid for the first open 5 mm strip
  (its position stays private) and all five runs find it at exactly 5.0 mm -
  including the strict and phase-18-proxy runs, with every pour on the strip
  counted as foreign. The "no path" answers are therefore a measured property of
  the board at those windows, not of a search that cannot find paths. The search
  is also covered by 14 assertions on synthetic geometry where the answer is
  known by construction (open window, wall with a gap, full wall, cross-layer via
  column, determinism).
- **Independently checked, and the checker was exercised.** A second script in a
  fresh process with its own engine, zone cache and raw O(items) clearance scan
  re-derives each offered edge, re-identifies both endpoints with the engine's
  own point-identity rule, checks continuity and layer transitions, and
  dense-walks each piece at 0.05 mm. With no candidate path on the 49 edges there
  was nothing to check there, so its dense walk was exercised on the control's
  path: 101 samples, 0 failures, clear margin at every sample. A cross-source
  inventory check agrees on the pad and via counts, and its track difference is
  accounted for by arc copper the file reader does not count as segments (counts
  private).
- **Measured how wide each blockage is.** A supporting read-only diagnostic
  reports the closest approach between the two sides of each blocked edge: 4
  edges at 0 mm (the sides meet at one point on different layers - the coincident
  cross-layer pairs, stopped only by the absence of a clear through-via column),
  1 edge between 0.5 and 1 mm, 12 between 1 and 2 mm, 3 between 2 and 4 mm and 29
  over 4 mm. The separation is a distance between *proxy-clear regions* inside
  the sampled window: a lower bound on the gap the search saw, not a measure of
  what a real route needs, and not by itself a mandate for a design change.
  Phase 18's open question is narrowed rather than closed - **expanding from the
  1D leg and its lanes to this bounded 2D proxy did not find a path**, which is a
  statement about this window, sampling and proxy.
- **No trial was run, because no edge produced a credible path.** The contract
  makes bounded transactional trials conditional on that; here the trial
  machinery had nothing to test. No route was started, no DRC was run, no
  checkpoint was taken, `checkpoint_count()` went 0 -> 0, and the accepted
  generation re-hashes to `6c4f8ab81b83...f593477cf1b` with the accepted pointer
  unchanged.
- No public harness file changed, so no public test was added or altered and the
  strict harness gate was not re-run for this phase.
  `docs/agent-work/reliability/phase19/RESULT.md` carries the aggregate account;
  coordinates, net names, per-edge geometry and rule values stay in the private
  evidence tree.

### Phase 18 - endpoint and anchor census — 2026-09-28
- **A read-only endpoint and anchor census, not a routing attempt.** Every one of
  the phase-11 `connection_not_verified` edges was joined to the unchanged
  accepted board by **exact offered-edge identity** (net plus both anchors,
  order-independent) rather than by net name: 49 offered, 49 joined, 0 missing,
  0 ambiguous, and the net code agrees on all 49. The accepted artifact re-hashes
  to `6c4f8ab81b83...f593477cf1b` before and after, the board geometry digest is
  identical, `checkpoint_count()` goes 0 -> 0, and no route is started.
- **Anchor identity is answered for every offered anchor.** All 98 anchors (49
  edges x 2) are named as the scheduled net by the engine's own point-identity
  rule; none carries no copper, none is present-but-unnamed, none is ambiguous.
  These edges are therefore not the `copper_absent_at_offered_anchor` or
  `unnamed_net_at_offered_anchor` class. Availability is answered for 97 of 98:
  one anchor of one coincident cross-layer edge sits inside a rule-area keepout
  on its own layer.
- **Every edge has a proved substitute.** 402 proved same-net substitute anchors
  across the 49 edges, 304 of them not already joined to the peer; 4 to 22 per
  edge. The caveat is identity, not quantity: a substitute can stand for a
  different component pair than the ratsnest edge drew.
- **The declared geometry is not margin-clear under the measured proxy; the
  region nearby often is.** The census measures a geometric proxy - no foreign
  copper inside the board's minimum clearance plus the adopted copper radius, at
  the sampled points and layers - and it runs no DRC, so it never calls anything
  rule-clear; conditional custom rules and hole clearance stay unreadable (see
  phase 15). Under that proxy: foreign copper sits within the margin at the start
  anchor itself for 33 edges; no leg is clean; 44 of 49 have no margin-clear
  lane at any lateral offset within 3 mm, and none of the 34 measurable edges
  has a margin-clear interior lane. All 8 cross-layer edges have **no** spot
  that is margin-clear under the same proxy for a through via on the leg, on
  every copper layer the via spans. A supporting read-only diagnostic then found
  that **47 of 49 edges have at least one margin-clear point within 4 mm of the
  leg**. That is free space, not connectivity: an open point is not a continuous
  path and does not show that copper placed there would pass the native DRC.
- **Verdicts:** 48 `geometric_obstruction` (47 with no margin-clear lane within
  3 mm, 1 direct-leg only), 1 `both` (the keepout anchor plus no via spot), 0
  `anchor_identity` alone, 0 `unresolved`. Nothing was inferred from history:
  neither the phase-17 trial records nor the pre-field attempt history were read
  for the classification.
- **Independent validation.** A second script in a fresh process with a fresh
  engine and a raw O(items) scan re-derived the join, all 98 anchor identities
  and every first-contact offset: 49/49, 98/98, 49/49, 0 failures. A census
  re-run reproduces the aggregate exactly; the verdicts and the 3 mm lane counts
  are stable at three sampling pitches, while fine-grained first-contact counts
  are not (33 -> 35 at the start anchor at the finer pitch) and are quoted with
  that limit. A margin-zero diagnostic run removes all contact, which is the
  honest bound on the finding: it is clearance-margin driven.
- **Next, if the operator wants one:** a bounded 2D clearance-aware search per
  edge over a fixed window, then at most one bounded transactional trial per
  edge where a path is found - plan geometry, not another campaign. Two edges
  have no margin-clear room in the sampled window and would need a physical
  design decision.
- No public harness file changed, so no public test was added or altered.
  `docs/agent-work/reliability/phase18/RESULT.md` carries the aggregate account;
  coordinates, net names and rule values stay in the private evidence tree.

### Where a plan stops — 2026-09-28
- **The attempt record now names the step that stopped the plan.** `steps_applied`
  counts the steps that succeeded, so a route that never opened and a via that
  never landed left the same number and a failure analysis had to replay the
  stored plan — against a harness that may since have changed. One bounded replay
  proved the danger: a recorded plan the reconstruction called "failed at the
  restart step" ran every step successfully on the current harness and was then
  refused by the DRC. `AttemptRecord.failed_step_kind` / `.failed_step_index` are
  populated by `runner._record` from the session's own step list, serialized by
  `to_dict`, and read back from a checkpoint as "not recorded" (empty kind, index
  `-1`) for a legacy or unusable value — never as a failure at step 0, which is a
  real step. Additive and behaviour-free: nothing reads them to decide anything.
- **The taxonomy counts them, and publishes only the counts.**
  `EdgeAttempt.failed_step_kinds`, the stopping-point counts on the
  `connection_not_verified` category detail, and a board-wide `failed_step_kinds`
  aggregate in the public summary. The bucket for a record with no stopping point
  is named for the evidence, not a cause, because an empty kind is written both
  by a plan whose every step succeeded and by a checkpoint that predates the
  field. Step kinds are harness vocabulary; no net name, coordinate or rule value
  goes with them.
- **Tests:** a stopped plan names its step and its index (with
  `failed_step_index == steps_applied`, the invariant the session guarantees); a
  completed plan names none; the field round-trips through the checkpoint and a
  legacy or unusable value loads as "not recorded"; the category detail, the
  per-edge aggregation from records, and the public aggregate.
- **Bounded trial.** Exact accepted board, fresh state, zero planner requests,
  priority-netted to the affected nets, 1 500 s soft budget: 19 attempts, 109
  plan evaluations, 0 accepted, 7 of the 49 target edges reached. Recorded
  distribution over all 109 records: 12 `start`, 61 `line`, 11 `via`, 24
  closed-then-refused (`drc_regression`), 1 unanchored endpoint; the private
  reconstruction and the record agree 42/42 where both exist. **Zero accepted,
  zero promoted:** those 24 evaluations did close the connection and were
  rejected by the native DRC (copper rolled back, `copper_state: restored`) and
  the other 85 never closed it, so the accepted generation re-hashes unchanged.
- `bash tools/reliability/check_phase.sh --strict` -> 494 unit + 138 native, no
  skips; `check_separation.py` 4/4; `check_engine_patches.py` byte-equal.

### Phase 16 hardening — 2026-09-28
- **The layer map is checked, not trusted.** `via_layer_span` converted
  `max_layer` outside its guard (malformed metadata could raise instead of
  refusing) and a repeated layer id could pass a mere length agreement. The
  conversion now sits inside the same guard, and the span is refused — `None`,
  which every caller already treats as fail-closed — when `max_layer` is
  missing, non-numeric or unconvertible; when the order repeats a layer; when an
  entry is not a copper layer (the engine's ids are even and non-negative) or is
  negative; or when the map's own `human_to_board`/`board_to_human` converters
  disagree. Every failure is a refusal, never a smaller span.
- **Endpoints outside the proven span are refused.** `find_openings()` now
  requires both endpoint human layers to be in the span, and `screen_openings()`
  reports such an edge as `unknown` with the span it proved instead of as "no
  opening".
- **Tests:** malformed `max_layer`, missing map metadata, duplicate ids,
  non-copper and negative ids, disagreeing converters, endpoints outside the span
  (finder and screen), and known-good two- and four-layer maps. The screen over
  the same sixteen edges reproduces the previous result exactly — one opening,
  four layers asked, board SHA256 equal to the accepted hash — so valid-board
  behaviour is unchanged.
- `bash tools/reliability/check_phase.sh --strict` -> 488 unit + 138 native, no
  skips; `check_separation.py` 4/4; `check_engine_patches.py` byte-equal.

### The via's real layer span — 2026-09-28
- **Fixed: the opening screen asked about two layers, not the via's span.** A
  via's barrel and hole exist on every copper layer it passes, and the screen
  only queried the two a plan changes between — which is how an opening could be
  reported clear while the via sat inside a foreign plane on an interior layer.
  `zone_coverage.via_layer_span()` now reads the span from the engine's own layer
  map and order; `find_openings()` asks every layer in it per corridor sample;
  and the screen reports `layers_checked` per edge.
- **Fail closed on an unknown span.** A missing or self-inconsistent layer map,
  or an unresolved zone on any spanned layer, refuses the opening — and the
  screen reports the edge as `unknown` — rather than skipping the layer that
  might have blocked it. The span is the whole copper stack (the harness places a
  through via), a superset of any partial via pair, so it can only refuse more.
- **Bounded and read-only.** At most one query per (corridor position, spanned
  layer): a layer-changing leg's own duplication is deduplicated before the
  query, and the tests assert the bound, that a two-layer board keeps working,
  that a no-zone board still offers openings, and that no via, track or route is
  touched.
- **Measured: four openings become one.** Re-screening the sixteen cross-layer
  refusal edges with the same lower-bound margin leaves a single opening; the
  three that disappeared were clear only on the layers the old screen asked
  about. The tool's output carries the board's SHA256 and the layer count per
  edge.
- **The one remaining candidate was executed** (`candidate_limit 3`, fresh engine
  and session, exact accepted board, zero planner requests) without weakening the
  generic refusal-aware policy: all three candidates ran and all failed with
  `connection_not_verified`. The plan never closed the connection, every attempt
  restored its copper with the rollback verified, and **nothing was accepted** —
  the pointer is unchanged.
- **The deferred rule-query API would not help these edges**: the three that
  closed and were refused on clearance findings no longer have a layer-complete
  opening, and the one that does fails on connectivity. Recorded as a finding,
  not as a reason to build the interface.
- `bash tools/reliability/check_phase.sh --strict` -> 483 unit + 138 native, no
  skips; `check_separation.py` 4/4; `check_engine_patches.py` byte-equal.

### The via margin is half answerable — and the screen had a blind spot — 2026-09-28
- **Research stop, as contracted.** Making the measured-opening margin
  rule-authoritative needs three things; the engine can answer one. The via a net
  actually adopts is available read-only (`rules.resolve_via_size`, per net,
  with the netclass values, the board floors, a source and a usable flag). The
  applicable **hole-to-copper** clearance is not exposed at all, and the
  applicable copper clearance is not a scalar either: the board's rule file is
  conditional, and the engine has no rule-evaluation accessor. No engine or wire
  change was made; the minimal read-only `probeRules()` contract is returned for
  a decision.
- **Added evidence: hole-to-copper and hole-to-hole are different rules** and
  must not be conflated. KiCad's board settings carry a hole-to-copper clearance,
  the rule language has distinct `hole_clearance` and `hole_to_hole` constraints,
  the clearance provider resolves the first through the rule engine per item and
  layer — and the engine's design-rule mirror stops at the hole-to-hole minimum,
  with no hole-to-copper field.
- **Found: the opening screen only checked two copper layers.** A through via's
  barrel and hole exist on every copper layer it spans. Re-screening the sixteen
  cross-layer refusal edges with every layer checked leaves **1 of 16** with a
  qualifying sample, and only **1 of the four earlier openings** survives; two of
  those points sit inside foreign pour on interior layers the screen never
  queried. That is the defect behind phase 14's refusals. It is read-only and
  needs no new interface — it is reported here rather than changed, because
  disqualifying three quarters of the previously reported openings is an
  orchestrator decision.
- **No transactional trial.** With the clearances unanswerable, no opening can be
  called rule-complete, so the phase ran none. Accepted generation unchanged.
- `bash tools/reliability/check_phase.sh --strict` -> 480 unit + 135 native, no
  skips; `check_separation.py` 4/4; `check_engine_patches.py` byte-equal.

### Phase 14 correction cycle — 2026-09-28
- **Fixed: DRC evidence retrieval.** `DrcDelta.to_evidence()`'s rows now carry the
  `item_a`/`item_b` identities behind each finding, not only its class and
  position — without them a refusal is a class and a coordinate, and a report has
  to guess at the cause. Evidence-only change, no gate or acceptance path, pinned
  by a test that asserts a refused finding names both items.
- **Fixed (private trial): the wrong evidence fields.** The phase-14 trial read
  `evidence["violations"]` (which does not exist — the session records the gate's
  verdict under `drc_delta`) and `result.connected` (recomputed after rollback)
  instead of `evidence["connected_before_refusal"]`, so its "empty classes" and
  its reading of closure were invalid.
- **Corrected: the phase-14 diagnosis.** The three executed openings were re-run
  in isolation and read properly: all three closed the connection
  (`connected_before_refusal` true) and were refused by the gate on
  `Clearance violation` / `Hole clearance violation` findings that name a
  **zone** (a foreign plane) and, on one edge, sit exactly on the via. The
  earlier corridor guess is withdrawn. The margin the screen applies is the
  board's copper clearance plus the *default netclass's* half-width, while the
  routed net's adopted via can be larger and the hole-to-copper rule is not
  modelled — the next step is a read-only test derived from the adopted via
  geometry and both rules.
- `bash tools/reliability/check_phase.sh --strict` -> 480 unit + 135 native, no
  skips; `check_separation.py` 4/4; `check_engine_patches.py` byte-equal.

### Refusal-aware ordering, and openings that finally got their turn — 2026-09-28
- **Added: `RunnerConfig.zone_gap_via_priority` (opt-in, default off).** When a
  pair's *exact* edge carries same-generation prior DRC-refusal evidence, its
  `zone_gap_via` candidates move ahead of the direct candidates that evidence
  names, before the sweep's `candidate_limit` truncation. The move is minimal and
  deterministic — each opening after the first already-refused direct candidate
  moves to just in front of it — so every other family, the non-refused direct
  plan and the order inside each group keep their positions, and nothing is
  removed. Evidence recorded against another board digest is ignored rather than
  inherited; a substitution attempt still binds to the edge it stood for; a
  connection with no evidence about it never enters the path.
- **Added: board-generation provenance in the screen tool.**
  `tools/reliability/screen_zone_openings.py` now reports the board's SHA256 (and
  the project's / rule file's when supplied) instead of `null`. A missing file
  returns no hash rather than an invented one.
- **Measured: the policy did what it was for.** In a matched ON/OFF trial over
  the four edges with measured openings (fresh session per arm, per-candidate
  budget, zero planner requests, 737.9 s), the policy reordered three edges and
  **the opening was executed on all three** — the cases the previous phase could
  not reach because the direct plans consumed the budget. All three were refused
  by the native DRC (`routing_failed` / `drc_regression`), the following direct
  plan was refused too, and **nothing was accepted in either arm** (0 of 8 ON, 0
  of 8 OFF). All sixteen executions rolled back cleanly (`restored`, rollback
  verified), so the accepted generation is byte-identical and no promotion gate
  ran. The fourth edge has no same-generation DRC-refusal evidence, so the policy
  declined to reorder and that opening is reported as truncated rather than
  executed.
- **Diagnosis:** the opening clears the zone question and still fails the whole
  DRC, so the via point is not the binding constraint on these hops — the
  approach or exit crosses copper the zone query does not model. Next avenue: a
  whole-corridor screen against the existing obstacle observation, read-only
  before any transaction.
- `bash tools/reliability/check_phase.sh --strict` -> 479 unit + 135 native, no
  skips; `check_separation.py` 4/4; `check_engine_patches.py` byte-equal.

### Exact-edge selection, a read-only screen, and one matched trial — 2026-09-28
- **Added: exact-edge priority.** `observations.edge_key()` is the canonical,
  direction-tolerant identity for one connection, and `RunnerConfig.priority_edges`
  pins those identities ahead of net pins in both the scan rotation and the
  coverage sort. A net pin is not an edge pin — one net offers several pairs, so
  a net-pinned trial can spend its whole budget on an edge it never meant to
  study. Malformed pins match nothing.
- **Added: `zone_coverage.screen_openings()`, a bounded read-only screen.** Per
  edge it answers `openings`, `none`, `same_layer`, `unknown` or `budget`, and
  `screen_summary()` reduces a set of them to counts with no coordinates.
  `tools/reliability/screen_zone_openings.py` exposes it, with a
  `--public-summary` form for public reports. The screen never starts a route,
  never checkpoints and never writes to the board — a test asserts the via count,
  track count and routing state are unchanged — and a screening that ran out of
  room says `budget` rather than looking like a screening that found nothing.
- **Measured: all 16 cross-layer closed-but-DRC-refused edges screened in 0.78 s.**
  Four have exactly one measured opening each (at least the board's clearance +
  via radius from foreign pour on every face the via touches, with a clear
  approach); twelve have none; none were same-layer, unknown or budget-limited.
- **Matched ON/OFF trial on the four, and its honest result.** Same generator,
  session, budgets and ordering; each arm on its own wall-clock budget; zero
  planner requests. 505.5 s, **0 accepted in either arm**, accepted generation
  byte-identical. The family offered one opening per edge, **two were executed
  and both failed**, and two were never evaluated because the direct candidates
  ahead of them consumed the arm budget — an ordering/budget limit, not a verdict
  on the opening. No promotion; the pointer is untouched.
- **Public/private boundary:** the board's own design-rule values, quoted in older
  public reports and history, are redacted in place with an explicit
  `[rule values redacted]` marker — eight statements across seven files, narrowly
  scoped, with every original preserved verbatim in private
  `phase13_routing/privacy_redactions.json` and the append-only exception
  documented. Measured distances and medians, and every conclusion and count,
  were left alone.
- `bash tools/reliability/check_phase.sh --strict` -> 476 unit + 134 native, no
  skips; `check_separation.py` 4/4; `check_engine_patches.py` byte-equal.

### A measured via opening, and the transaction fix it uncovered — 2026-09-28
- **Fixed: layer-changing waypoints could never work.** `make_via` commits its
  line and *finishes* the session, so the plan's trailing line to the same point
  ran against an idle router and raised a `KeyError` on an unmapped head layer —
  rolled back, and invisible because no test had ever passed a waypoint on a
  different layer. `AgentSession` now emits a `restart` step (re-open the route
  at the via's point on the new layer) after each layer change, and models the
  action phase the executor actually sees. Measured after the fix: the via lands
  at the requested coordinate with 0.0000 mm error across six corridor positions.
- **Added: `zone_gap_via`, a measured via-placement family.** For a pair whose
  endpoint layers differ, `zone_coverage.find_openings()` samples the pair's own
  corridor, queries both faces a through via touches in one batched call, and
  offers points only where every covering zone resolved, no foreign pour is
  closer than the board's clearance + via radius on either face, no observed
  foreign track/via/pad is closer, **and the whole approach on the start layer is
  clear** — the router cannot see pours, so a blocked approach is not fixable by
  choosing another via spot. Openings sort own-pour contact first, stay a band
  apart and are capped; each becomes a `kind="zone_gap_via"` candidate whose
  rationale says the opening is measured, not proved legal. Off by default
  (`RunnerConfig.zone_gap_via = 0`).
- **Fixed (carried forward): a short IPC answer dropped points.** `verdicts`
  zipped unresolved indices with engine rows, so a truncated answer silently
  removed points from `candidate_risk` and its evidence. A row count that is not
  one per query now marks the batch unknown, a `None` row marks that point, and
  the return path guarantees one verdict per point in order. Regression tests
  cover short, long, holed and well-formed answers.
- **Trial result: inconclusive, and reported as such.** A whole-board sweep with
  the family on (275 records, 581.9 s, zero accepted, board byte-identical) never
  fired because the selected pairs were same-layer; a matched per-edge trial on
  the cross-layer edges reached 2 of 16 within its budget and found **0 measured
  openings** on both, with identical arms (3 candidates each, all refused for
  `drc_regression`). Those hops are pour-dense with no clear approach, so the
  family declined rather than guessing. Nothing promoted; the accepted
  generation is byte-identical.
- **Privacy:** the coordinate examples flagged in review (and two more of the
  same kind) are redacted in place with an explicit marker and a note in
  `HISTORY.md`; earlier phases' board-specific *rule values* are flagged to Astra
  rather than silently rewritten.
- `bash tools/reliability/check_phase.sh --strict` -> 475 unit + 132 native, no
  skips; `check_engine_patches.py` byte-equal; `check_separation.py` 4/4.

### Phase 11 correction cycle — 2026-09-28
- **Fixed: the zone verdict cache was net-blind.** `ZoneCoverage` cached the
  *classified* verdict under `(point, margin)`, but a verdict carries
  `state`/`nets`, which depend on the net being routed — so the second pair to ask
  about the same copper got the first pair's own/foreign answer and was
  mis-ranked. The cache now stores the engine's **net-independent row** (with
  `_Unresolved` markers for points the engine cannot answer) and the verdict is
  derived per call. Regression test asks one point at one margin for two nets and
  asserts opposite verdicts across a cache hit with no extra engine row.
- **Changed: the zone prefilter is opt-in.** `RunnerConfig.zone_prefilter`
  defaults to **False** — it changes which plans a sweep spends its budget on, and
  no control run has shown that change to be an improvement, so it must be asked
  for. The campaign driver gained `--zone-prefilter`, kept
  `--no-zone-prefilter` for paired controls, and the two are mutually exclusive.
- **Corrected: "ranking never drops a candidate" is only half true.** The ranker
  removes nothing from the list it is handed, but the sweep truncates to
  `candidate_limit` *after* ranking, so a plan moved late can miss the attempt.
  Every docstring, test name and release note that implied otherwise now states
  the two claims separately, and a test pins both halves: the full list is
  retained *and* a truncated sweep excludes the late plan.
- **Re-checked: no board-specific detail in public text.** The phase-11 docs and
  history carry counts, ratios and the accepted hash only; the margin basis, the
  campaign digest and the per-pair audit stay in the private evidence tree.
- `bash tools/reliability/check_phase.sh --strict` -> 473 unit + 129 native, no
  skips; `check_engine_patches.py` byte-equal across three patches;
  `check_separation.py` 4/4.

### A read-only zone query, and a prefilter that only ever ranks — 2026-09-28
- **New engine patch `0003-zone-point-query.patch`.** A read-only
  `get_zone_point_hits(queries)` answers, per query and copper layer, what zone
  geometry covers a point: every covering zone with its identity and net,
  `in_outline`, `in_fill`, the engine's own distance to that layer's **filled**
  copper, the island flag, rule-area keepout flags, and a fill provenance of
  `loaded_unverified` / `no_fill` / `unknown`. No mutation, no checkpoint, no
  world resync, callable while a session is active. `ZonePointHit` /
  `ZonePointResult` mirror into both `wire.py` copies, byte-identical
  (`check_engine_patches.py` proves pin + `0001` + `0002` + `0003` reproduce the
  engine tree; `check_separation.py` proves the two protocol copies match).
- **A point can be in several zones, and the answer says so.** The result
  carries *all* hits and classifies the copper itself as `no_zone`,
  `outline_only`, `fill_single_net`, `fill_multi_net` or `unknown` - a multi-net
  overlap is reported as a conflict and names no single net, so one zone can
  never hide another.
- **No currency claim.** KiCad clears its refill flag when a board is parsed and
  keeps no fill hash across processes, so the engine cannot prove the stored fill
  matches the loaded rules. `fill_provenance` says `loaded_unverified` rather
  than implying "current", and no boolean is named for a proof that does not
  exist.
- **Real geometry, not just a point.** `distance_mm` is the distance to the
  covering zone's filled copper (0 on copper, the distance to the nearest fill
  edge or void wall otherwise, -1 when the zone has no fill polygons for the
  layer), so a caller tests a copper object of radius R needing clearance C as
  `distance_mm < R + C`. It is still a point answer, not a collision test against
  the whole board, and the docs say so.
- **`pcb_world/agent/zone_coverage.py` — the advisory prefilter.** Margin from
  the board's own clearance plus the wider of track half-width and via radius;
  per-point states `none` / `own` / `foreign` / `mixed` / `unknown` where unknown
  passes through untouched; samples from the candidate's own waypoints plus a
  bounded, deterministic sample along its start→target corridor on both copper
  faces (without which a direct plan, which names no waypoints, could never be
  classified); and a cache that survives the run because routing never re-pours,
  dropped only by an explicit invalidation or a refill-epoch bump. The cache
  holds the engine's **net-independent** row and never the classified verdict:
  the net decides own vs foreign, so a verdict cache would hand one pair's answer
  to the next pair that asked about the same copper.
- **`RoutingRunner._zone_ranked` ranks, and never filters.** A stable partition
  moves candidates whose own geometry touches a foreign pour behind the rest;
  everything else keeps its order, `unknown` is untouched, `suppressed` is False
  by construction, and the transactional native DRC remains the only authority.
  The ranker drops nothing from the list it is handed, but the sweep truncates
  to `candidate_limit` *after* ranking, so a plan moved late can miss the
  attempt — the docs and the trial report say that instead of claiming every
  ranked-late plan still gets a transaction. **Off by default:** no control run
  has yet shown the ranking to be an improvement, so `zone_prefilter` is opt-in
  and the campaign driver needs an explicit `--zone-prefilter` (with
  `--no-zone-prefilter` kept for a paired control). The run records the whole
  audit under `metrics["zone_prefilter"]`.
- **Tests.** `tests/agent/test_zone_point_query.py` (13 native tests:
  overlapping pours and the conflict classification, a void inside a pour with a
  measured distance, a zone spanning three copper layers, keepout flags, the
  island flag, a board with no zones, an unfilled pour, a non-copper layer, the
  cache/refill lifecycle, one point classified oppositely for two nets across a
  cache hit, and a ranked-late candidate falling outside a truncated sweep) and
  `tests/agent/test_zone_prefilter_unit.py`
  (10 engine-free tests for the policy, the margin, and the ordering rule). The
  synthetic board helper gained multi-layer zones, rule-area keepouts and an
  island switch; the strict gate's native floor moved 116 -> 127.
- **One bounded campaign, nothing promoted.** From the exact accepted generation,
  zero planner requests, 704.9 s: 25 pairs ranked, 342 candidates checked / 289
  ranked late, 1 328 point queries against 18 060 cache hits, 284 own records all
  `restored`, zero accepted, accepted generation byte-identical. Measured effect:
  with candidates truncated to `candidate_limit` after ranking, 58 foreign-pour
  plans were ranked out of the window and spent no transaction (none of them
  duplicates) - the ranking changed which plans were tried, without removing any
  and without touching acceptance. The report states plainly that no control run
  was made, so "better" is not claimed.
- `bash tools/reliability/check_phase.sh --strict` -> 472 unit + 127 native, no
  skips; `check_separation.py` 4/4; `check_engine_patches.py` byte-equal.

### The zone question the engine cannot answer yet — 2026-09-28
- **Phase 11 stopped at its contract gate, deliberately.** The phase's whole
  purpose is a candidate prefilter that knows whether a planned via lands on its
  *own* net's pour or somebody else's — the dominant shape in phase 10's refusal
  diagnosis. Research established that the pinned engine has no trustworthy
  read-only point-in-filled-zone query, so nothing was implemented and the
  exact minimal interface proposal went to the orchestrator instead of into the
  code. No engine, wire, patch, test or tool file was touched.
- **Three independent facts settle it.** (1) The PNS world — the engine's own
  obstacle model — admits copper pours only as *rule-area keepouts*, with a null
  net, so pour copper is not a router obstacle at all. (2) The zone row from
  `get_board_items()` carries uuid, source, layer, net and an **outline bounding
  box** — no outline, no fill, no voids, no islands — and its layer is
  `UNDEFINED_LAYER` for any zone spanning several copper layers, which the one
  existing zone consumer silently skips. (3) Staleness cannot be reported at
  load: KiCad clears `NeedRefill()` unconditionally when a board is parsed and
  its own header says a cleared flag "does not imply filled areas are up to
  date"; the hash accessor only returns a hash this process computed. The full
  bound surface was enumerated rather than sampled: 110 entries, of which only
  `get_board_items()` and `get_keepouts()` touch zones.
- **The only available proxy was measured and cannot serve.** A read-only survey
  of the accepted generation through the pinned KiCad build shows the
  bounding-box test flagging 74.8% of sampled points on *every* copper layer
  against true filled coverage of 50.8%-67.0% — 1.12x-1.47x over-flagging with
  no own-net/foreign-net separation. Fills are fractured (0 inner rings, 0 holes
  across 130 fill outlines), so a saved-file parse would need its own
  arc-aware geometry engine to say anything, and would describe the file rather
  than the live board.
- **Proposed contract (awaiting a decision).** A read-only
  `hitTestZones(queries)` on the router returning, per queried point and copper
  layer: `in_outline`, `in_fill`, `fill_net_code`, `outline_net_code`,
  `is_island`, `in_keepout`, `keepout_flags` and `fill_current` — with unknown as
  a first-class answer that the caller passes through, never treats as safe. The
  wire mirror would land in both `wire.py` copies (byte-identical), with the
  wrapper in `kicad_engine.py` and the advisory prefilter plus tests in the agent
  layer. Because that is an interface change, it is the orchestrator's call.
- **Nothing promoted.** The accepted generation was re-hashed and is unchanged;
  no campaign ran (there is nothing to evaluate without the prefilter) and the
  pointer was left where it was.
- `bash tools/reliability/check_phase.sh --strict` → 462 unit + 116 native, no
  skips; `tools/check_separation.py` 4/4; `check_engine_patches.py` byte-equal;
  `git diff --check` clean.

### Attributed refusals, two aimed candidate families, one campaign — 2026-09-28
- **`unattempted_cap_observed` replaces `capped_unattempted`.** The category was
  always run-level evidence — the run state keeps scan counters, not a per-net
  breakdown — so the old name claimed the cap withheld *this* edge, which the
  evidence cannot show. The new name states the checkable claim. Counts
  unchanged (26 as found, 11 after phase 9's campaign) and still summing to the
  edge count.
- **Attempt records carry `offered_pair_key`.** `pair_key` stays the geometry
  actually attempted; the new field is the offered connection the attempt stands
  for. A substitution offer now carries the edge the scan drew
  (`NetPair.offered_key`), and the taxonomy's history join resolves an edge by
  offered key before falling back to native component membership — so
  substitution attempts are attributed to the connection they were made for
  instead of being counted blind. Backwards compatible: an absent key is "not
  recorded", and legacy records are counted, never guessed at.
- **`clearance_extent_probes` and the `drc_extent` family.** Instead of only
  stepping outward by multiples of a clearance the refusal never measured, each
  clearance-class refusal now contributes a waypoint placed past the *observed
  obstacle's own extent* on the pair's own layer, at the board's own minimum
  clearance, under a shared `max_extent_probes` budget and named by its geometry.
- **`RoutingRunner._hole_escape_seeds`.** For a refusal whose class names a hole,
  the free-via search is seeded from the actual drilled geometry near the finding
  (hole radius + via radius + the board's hole-to-hole rule) in the direction of
  the far terminal. `pad_block_reason` stays a prefilter and the native DRC stays
  the only authority; the search's evidence records the seeds, so an unproductive
  sample is reported as a sample.
- **Selection uses the configured budgets.** `_refusal_unanswered` is now
  computed with the run's actual `drc_candidate_limit`, `drc_clearance_probes`,
  `drc_clearance_mm` and `drc_extent_probes`, and accounts for the two families
  the session computes (a free via spot) by the pair's records and by the run's
  own search evidence — raising a limit now changes which pair is selected, which
  it did not before.
- **Campaign result.** From the exact accepted generation, zero planner requests,
  1 379.9 s across two segments: 465 records, 39 distinct pairs, 16 of the 43
  refusal edges reached with refusal-derived plans (327 refusal-derived records
  against phase 9's 39 over 7 pairs), **zero closures**, every copper state
  `restored`, accepted generation byte-identical. The diagnosis behind the two
  families is in the same entry's evidence: the refusals are dominated by the
  plan's own via against a foreign copper zone, which neither family models.

### A failure taxonomy, and two strategies chosen from it — 2026-09-28
- **`tools/reliability/failure_taxonomy.py` — every outstanding connection,
  classified exactly once.** The source of truth is the engine's own ratsnest on
  one board generation: each edge takes the first applicable category, so the
  totals sum to the edge count and nothing is folded into "other". Two counts are
  kept apart on purpose — one entry per ratsnest edge, and `C(components, 2)`
  summed over the nets with more than one proved copper component, which is the
  work an exhaustive router faces. Attempt history is joined only where it is
  bound to *this* board (the canonical pair key, or the native component
  identities an offer stood for); a record measured on other copper is counted as
  dropped, never inherited. Categories 1–6 are board facts and outrank any
  inference from a refusal; 7–10 are only assigned from a recorded attempt on this
  board. `public_summary()` reduces the report to counts, classes and hashes with
  no net name, coordinate or refusal position.
- **`clearance_search_probes` and the `drc_clear` family.** The single
  `drc_avoid` step is unchanged; each recorded refusal additionally contributes
  waypoints searched outward from its own projection at 1x/2x/4x the caller's
  clearance plus one along-line step, under a shared `max_clearance_probes`
  budget, named from their own geometry so a re-scan cannot mint a new plan key
  for the same copper.
- **Evidence outranks proximity in the candidate order.** The runner truncates to
  `candidate_limit`, and the contextual families (pour waypoints, obstacle
  detours) used to precede the refusal-derived ones: on the V3 campaign's first
  1 318 s segment **36 pairs carried recorded violations and zero refusal-derived
  candidates were evaluated**. The refusal families now follow the two direct
  candidates, and `RoutingRunner._refusal_unanswered` makes a pair with an
  unanswered recorded refusal outrank an untouched one in the selector.
- **Fresh-first coverage.** `scan_net_pairs(..., fresh=...)` grants a net its
  first pair with no record bound to the current generation in addition to the
  capped representative, bounded to one extra pair per net
  (`coverage_promoted`); `RunnerConfig.coverage_first` also ranks the selector by
  `AttemptHistory.records_on`. Coverage deliberately counts *records*, not real
  attempts: a candidate a sweep evaluated and rolled back is real work spent on
  the pair, and the narrower measure made such a pair look untouched. The per-pair
  budget, the failed-plan dedup and the transactional rollback are unchanged.
- **Measured on the V3 board** (accepted generation, hash-bound): the same
  1 318 s window attempted 3 distinct pairs before the coverage correction and 116
  after it; 15 previously-withheld connections received their first attempt
  (`capped_unattempted` 26 → 11, attempted 79 → 94). No connection closed and
  nothing was promoted.
- `bash tools/reliability/check_phase.sh --strict` → 451 unit + 116 native, no
  skips.

### A native call reaped at the run's own deadline — 2026-09-27
- **A bounded engine lease, so the soft deadline cannot truncate a transaction.**
  The run has two budgets that buy different things: the soft scheduling budget
  (`time_limit_s`) decides whether to *start* work, and the hard operational
  ceiling (`engine_call_timeout_s`) bounds each individual call. While a lease is
  held, every native call is bounded by `min(engine_call_timeout_s, lease
  remaining)` and not by the soft budget, so a transaction whose DRC or rollback
  turns out bigger than its history still finishes and is verified. A lease covers
  the whole engine-touching part of an iteration — the digest, the scan, the
  outstanding count and the attempt they select — plus the run's closing
  verification of the board it is about to report. One window opens per unit of
  work, nesting cannot extend it (so a sweep cannot compound its overrun), and the
  run's overrun past the soft limit is at most **two** of those windows — the
  iteration's that crossed it, plus the closing verification's own — never one.
  `metrics["engine_leases"]` reports `max_window_s` (the largest single window) and
  `max_overrun_s` (the total actually observed, which can exceed it);
  saved-artifact gates run outside any lease under the remaining soft budget and
  add no overrun. `RunnerConfig.transaction_lease_s`
  (default 300 s) is that window's floor, widened to `engine_call_timeout_s` and to
  twice the worst measured whole-board DRC or whole attempt; `0` disables the lease
  and restores the plain clamp. A lease that *expires* is a hard-bound violation,
  not a scheduling decision, so the fail-closed quarantine still follows.
- **Stopping at a boundary, not being killed at one.** The pre-work guard keeps
  `stop_reason="time_limit_headroom"` (`attempt_headroom_s` is now a 30 s floor
  rather than 0) and the candidate sweep stops starting new transactions once the
  soft deadline passes; an attempt already in flight finishes and the loop then
  stops with `stop_reason="time_limit_completed_attempt"`. The closing DRC runs
  under a lease too, so a run that stopped on its limit still reports the DRC of
  the board it kept instead of ending on an engine exception.
- **Inherited history is labelled.** `run_state.json` keeps
  `metrics["transplant"]` (`inherited_records`, `prior_run`, generation digest and
  a note that only later records came from this run), so a trial's own outcomes
  can be read without mistaking an inherited record for one it produced.
- **The loop stops before it mutates copper it cannot verify.**
  `_native_timeout_s` clamps every native call to the run's remaining budget, so an
  attempt that started just inside the time limit could have its acceptance DRC
  dispatched with seconds of allowance against a whole-board pass measured at ~41 s.
  The call is reaped at its deadline, which kills the owned engine child holding the
  transaction's checkpoint, and the rollback is then left with no child to restore
  through: `drc_unavailable` plus `copper_state=retained_unknown` and a session
  quarantine. `RunnerConfig.attempt_headroom_s` (floor, default 0) and
  `attempt_headroom_factor` (default 1.25) bound one more scan or attempt by the worst
  whole-board DRC or whole attempt this run has measured, and the loop stops with
  `stop_reason=time_limit_headroom` and the arithmetic in
  `metrics["time_limit_headroom"]`. No gate is weakened: the guard only refuses to
  start work whose verification cannot fit, and both measurements are wall-clock so a
  caller's fake run clock cannot inflate them.
- **A failure keeps its own words.** `AttemptRecord.failure_exception` and
  `AttemptRecord.rollback_detail` are written from the session's evidence and
  serialised with the record — bounded by `EXCEPTION_TEXT_LIMIT`, compacted by
  `compact_rollback_detail` to the fields that decide whether a restore was proved.
  An unverifiable rollback is now diagnosable from `run_state.json` instead of only by
  reproducing the transaction; older checkpoints load unchanged.
- **`RunnerConfig.priority_nets`** pins named nets to the front of every scan (the
  scan's round-robin is preserved inside both groups, so it cannot starve the queue),
  for trial briefs that name geometry they want attempted while budget remains.
- Fault-injection and native regressions: a reaped acceptance DRC keeps its cause and
  is distinguishable from a live child answering "no" (`restore_exception` versus
  `restored=false`); the saved run state carries both new fields; the headroom guard
  stops the run before the first scan and after one; a pinned net is offered first
  without losing the queue; the lease regression runs an attempt whose acceptance DRC
  exceeds its history and crosses the soft deadline, and proves the DRC got the
  lease's allowance, the rollback finished, no record reports `retained_unknown` and
  the run stopped at `time_limit_completed_attempt` — with a negative control on the
  same board, DRC and clock at `transaction_lease_s=0`, a case proving a call past
  the hard ceiling still quarantines, and cases pinning the allowance and the
  no-extension property of nesting. `tests/agent/fake_engine.py` gained
  `restore_exception`, `drc_fail_after_calls` and a deadline-accounting double
  (`allowance_provider`, `advance_clock`, `drc_duration_s`,
  `drc_duration_after_calls`, `reaped`).
- Mechanism, reproduction and limits:
  `docs/agent-work/reliability/phase8/DECISION.md`.

### Via-state corrections and the continuation search — 2026-09-27
- **A -> B -> A restores A.** The size is re-resolved at every transaction entry and
  compared with the size actually in force, instead of being answered from a per-net
  cache; the regression test pins the exact setter sequence `[0.6, 0.9, 0.6]`.
- **Invalid constraints are not "no constraint".** An explicit `0.0` floor is kept;
  absent, non-numeric, non-finite or negative floors make the size unusable and are
  named in `invalid_constraints`.
- **Native precision.** Adopted sizes are no longer rounded, so a size can never
  round below a declared minimum; a floor of `0.30000000004` is pinned by test.
- **Any setter failure quarantines**, because the engine exposes no readback for its
  active sizes; a mutate-then-raise setter is tested.
- **An unusable size refuses via mutation** (`via_size_unusable`) rather than routing
  with a stale setting; plans without a via step are unaffected.
- **Continuation-aware via search.** Seeds include the pair's own refusal geometry;
  spot slots are reserved for positions whose far-layer copper is proved to be the
  target's component; budgets are shared per anchor; probes, bands, truncation and
  continuation counts are recorded, with an explicit note that a sampled disc is a
  sample. Component nearest-neighbours are taken over the whole net, not the rotating
  window, with an optional gap ceiling that is off by default.
- **Bounded trial (private, not this repository's acceptance).** 2400 s: 266 attempts,
  1 434 distinct plans, 39 via searches (28 526 probes, 60 continuation-proved
  positions), 270 refusals with hint geometry, 1 391 verified rollbacks - and one
  unverifiable rollback after a `drc_unavailable` failure on a legitimate 86 mm
  connection, which quarantined the session and stopped the run. Nothing was
  promoted; the accepted board `6c4f8ab81b83…` is unchanged and its triple gates were
  re-run and pass.
- Harness: 398 unit + 116 native tests, no skips; `check_engine_patches.py`
  byte-equal; `tools/check_separation.py` 4/4.

### Per-net via sizes, rotating component windows, free-position via search — 2026-09-27
- **The via size is resolved for the net actually being routed.**
  `resolve_via_size(engine, net_code)` asks the engine for that net's own
  effective class and derives diameter/drill from the declared values and the
  board minima; `AgentSession` applies it at transaction entry and re-applies it
  when the net's size differs. Declared and adopted values are reported
  separately, non-finite or non-numeric values are unusable rather than coerced,
  and the two setters are treated as a pair: a failure of the second one
  quarantines the session instead of routing on router state nothing here can read
  back. The adoption is a starting point; the transaction DRC and the fresh
  whole-board saved gate remain the proof.
- **Bounded component windows rotate and report their coverage.** Component
  identity is a digest of the native terminal membership (not the row index), each
  terminal is expanded over the copper layers it occupies, and both the component
  window and the anchor window rotate on an unchanged generation. The scan exposes
  considered vs omitted components and pairs, and a run whose window or cap left
  part of the graph unexamined stops with `pairs_exhausted_capped` and an explicit
  note rather than claiming global exhaustion.
- **Free-position via search and shove-mode layer escape.** A bounded, cancellable
  grid search (2 mm disc at 0.15 mm pitch, ~558 points, 600-probe budget) around a
  pair's anchors and midpoint proposes via positions, spread one per distance band
  and skipping spots the gate has already refused that pair at, with the budget
  shared per anchor. `pad_block_reason(for_via=True)` is documented and used as a
  *prefilter* only - it says nothing about tracks, zones, holes or clearance across
  the via's span - and the transaction's native DRC keeps the authority. Refusal
  hints also offer a shove-mode escape beside the walkaround one.
- **Private validation run (not part of this repository's acceptance).** 2400 s of
  the 12 000 s budget from the 136-unrouted board: 153 attempts, 4 045 distinct
  plans, 47 via searches and 11 654 probes, 573 refusals with hint geometry, 0
  unverified rollbacks, one closure (net 5, component offer) -> **135 unrouted /
  321 pad groups / 6 210 tracks**, board `6c4f8ab81b83…`. All three production
  gates pass: native 0 added relevant (connectivity 219 -> 200), terminal
  335 -> 321 clusters with 0 splits, complete CLI 9.0.8 verified with raw
  unconnected 149 -> 135.
- Harness: 396 unit + 103 native tests, no skips; `check_engine_patches.py`
  byte-equal; `tools/check_separation.py` 4/4.

### Component-graph scheduling, refusal-driven alternatives, router via-size defect — 2026-09-27
- **Native component membership is now a scheduling source.** `native_components`
  reads the engine's own cluster membership and gives each net's components a
  stable identity; `component_pairs` turns the nearest distinct disconnected
  component pairs into offers that share the ratsnest queue (deduplicated by
  geometry, fair round-robin, bounded per net). On the campaign board 60 of the
  component pairs were geometry the ratsnest had not named.
- **Substitution is labelled, never presented as the offered edge.**
  `reanchor_pair_variants` searches the anchor's own cluster
  (`provenance="original"`), the covering same-net zone sample (`proof="pour"`,
  which proves the sample and not the island), and the net's own components at
  unbounded radius (`provenance="candidate"`, `component_id`, `moved_mm`). A
  component already joined to the other endpoint is reported `already_connected`
  and not offered; `classify_offer` is the single original-vs-substitution
  decision; an edge with no provable substitute stays in the queue as drawn and is
  counted in `unresolved_offers`.
- **Refusals are evidence.** `AttemptRecord` carries `drc_classes`, `drc_hints`
  and `closed_before_refusal` (with `AgentSession` setting
  `connected_before_refusal` before the rollback it cannot otherwise be seen
  after), and `generate_candidates` aims one avoidance and one layer escape at each
  refused violation, plus via jogs for cross-layer and hole-class cases. Names and
  selection are geometry-canonical: naming them by history position minted new plan
  keys for unchanged copper and livelocked a run (1525 keys over 1624 records).
- **A real router defect, not a missing clearance.** The PNS size cache was not
  populated from the project, so it placed 0.25 mm drills on a board whose setup and
  netclass say 0.3 mm, and every via it added was refused for
  `Hole size out of range`. `AgentSession._sync_routing_sizes` adopts the project's
  own netclass via diameter/drill, floored by the board's minimum hole and annular
  ring, and records what it adopted. No rule is relaxed.
- **The scoped native DRC pass is differentialled on the shapes it is used for**:
  via insertion and span, layer transition, a discarded via attempt, and an
  intentional clearance regression, all compared against a whole-board pass on
  identical copper, with the exact added-relevant counts matching. A case that
  produces no copper now fails the differential instead of counting as agreement.
  The pass stays opt-in and off by default, and the fresh full native saved-artifact
  gate is unchanged.
- **Private campaign result (not part of this repository's acceptance).** Three
  deterministic segments (8 025 s of a 12 000 s cap) evaluated 3 356 distinct
  plans over 315 pairs on the final generation and closed one more connection:
  **136 unrouted / 322 pad groups / 6 208 tracks**, board `ed5b783945c6…`, with
  zero unverified rollbacks. All three production gates pass: native 0 added
  relevant (connectivity 219 -> 201), terminal 335 -> 322 clusters with 0 splits,
  complete CLI 9.0.8 verified with raw unconnected 149 -> 136.
- Harness: 385 unit + 88 native tests, no skips; `check_engine_patches.py`
  byte-equal; `tools/check_separation.py` 4/4.

### Attempt cost, scoped DRC with a differential, and component-derived endpoints — 2026-09-27
- **Attempt cost is measured, not assumed.** `tools/reliability/
  profile_attempt_cost.py` runs the production runner with every engine call
  timed. On the V3 board a full native DRC is 38.7-40.1 s and eight of them were
  89 % of a 352 s window; a candidate that closes nothing already cost no DRC
  (rolled back from the full snapshot, restore verified against the
  pre-transaction probe), while a *closure* paid three.
- **The deterministic sweep applies candidates instead of probing then
  applying.** `RoutingRunner._apply_candidates` executes the candidate set as
  atomic transactions in ranked order and stops at the first accepted one: a
  candidate that closes nothing is rolled back without a DRC, and the one that
  closes the connection gets exactly one full native DRC on exactly the copper
  that is kept. Its records carry the new `AttemptRecord.sweep_member`, so a
  six-candidate sweep spends one attempt against the per-pair budget while every
  evaluated plan still blocks an identical retry.
- **The scoped native DRC re-check is available as an opt-in verification
  pass** (`AgentSession(incremental_drc=True)`, `RunnerConfig.incremental_drc`),
  off by default: 0.2-0.35 s against 39-40 s for the same verdict on the V3
  board. It is enabled only together with
  `tools/reliability/drc_incremental_differential.py`, which replays direct
  connect, shove-mode connect, shove of an existing track, and rollback from
  checkpoints and compares the scoped pass against a whole-board pass on
  identical copper (equal digests): relevant multisets identical, connectivity
  counts identical, verdicts agree, and a rule file whose bytes changed behind
  the same path is **refused** rather than diffed across rule regimes.
- **An anchor that carries no scheduled-net copper no longer retires the
  connection.** `component_anchor_candidates` searches the net's own inventory
  anchors for the nearest ones the engine proves carry that net, and
  `reanchor_pair` tries them after the same-cluster proof and the covering-zone
  sample. Every alternative endpoint is labelled
  (`provenance="candidate"`, `moved_mm`), including the pour path, so an
  alternative endpoint is never presented as the one the ratsnest offered. When
  the offered anchor is replaced, the offered pair is retired for that copper
  generation and the attempts are filed against the pair actually attempted.
- **Private campaign result (not part of this repository's acceptance).** From
  the accepted 142-unrouted board, a bounded deterministic pass (5 693 s of a
  12 000 s budget, 932 attempts, 4 228 plan evaluations, stop reason
  `pairs_exhausted`) closed five more connections: 137 unrouted / 323 pad groups
  / 6 206 tracks, best board `f353d35c029d…`. All three production gates pass:
  native 0 added relevant, terminal 335 -> 323 clusters with 0 split relations,
  complete CLI 9.0.8 verified with raw unconnected 149 -> 137 and
  track_dangling 70 -> 65.
- Harness: 385 unit + 67 native tests, no skips; `check_engine_patches.py`
  byte-equal; `tools/check_separation.py` 4/4. The engine C++ is unchanged, so
  patch `0002` is unchanged.

### Raw unconnected counts, self-verifying normalization, metadata restore — 2026-09-27
- Verified endpoint re-anchoring and pour-aware targets. An endpoint whose net the
  point-identity rule cannot name is now re-anchored on copper the engine proves
  carries the scheduled net: a pad in the same cluster, the unanimous net of the
  cluster's own tracks/vias, or a sample inside a covering same-net zone (accepted
  only when one connected component is proved; two same-net islands are refused).
  Pour-aware candidates target copper already connected to the far terminal,
  proved by native cluster intersection, so holes, keepouts, clearance and layer
  are enforced by the engine. The engine's `get_connected_points` also walks every
  overlapping item on a layer instead of the single item `itemAt` returns, so a
  track under an unanchorable zone is no longer reported as "no copper". An anchor
  that carries no scheduled-net copper at all is retired with its exact
  coordinates, covering zones and nearest same-net/foreign copper.
- `serialized_metadata` restores the saved net table's code for the same net
  *name* (never the source's numeric code across a renumbering) and restores only
  `net`; `locked` is no longer copied, so a deliberate unlock survives a save.
- Copper-only refill of the canonical accepted board re-measured after the
  metadata migration: 33 split terminal relations, +14 unrouted, +14 pad groups,
  0 added relevant findings - physical, so the branch stays unpromoted.
- Routing: obstacle-derived candidate plans (waypoints placed past an observed
  obstacle's own edge, plus a two-waypoint dodge) now precede the blind sideways
  offsets, and the layer/via strategy is offered before the list can be truncated
  to its candidate limit. The bounded adaptive pass from the canonical
  six-closure board evaluated 116 plans over 18 pairs on four layers with
  push-and-shove, obstacle-derived, detour, dodge, layer/via and planner plans;
  no new connection could be closed under the no-new-violation contract (83
  plans never connected, 22 connected and were refused by the native gate, 6
  pairs have an endpoint the engine cannot identify), so the accepted
  six-closure board is unchanged and retains its 143 unrouted / 329 pad groups.
- The CLI gate judges the reporter's `unconnected_items` section on its **raw**
  row count (stability and delta), because the byte-identical duplicate rows it
  emits vary between runs of one unchanged board while the raw total does not.
  The collapsed identity comparison stays as a diagnostic and is only excused by
  a fresh native terminal-partition proof bound to the candidate; a raw rise is a
  regression and an unstable raw total is unverified. Every relevant class is
  still compared by exact identity, never by union or count alone.
- `pcb_world/agent/kicad_metadata.py` now re-verifies its own output before it
  reports success: no duplicated identifier, no structural collision, no
  reference to a duplicate, token-for-token equivalence with the input outside
  the mapped identities, and an explicit refusal if a minted UUIDv5 would collide
  with an identifier the board already keeps.
- `pcb_world/agent/serialized_metadata.py` restores `(net N)` / `(locked yes)`
  tokens that KiCad's writer drops from *non-copper* graphics (a non-copper item
  can never hold a net, so the tokens are inert but are the user's metadata),
  matched by item UUID and net name. `KiCadEngine.save` runs it on every save;
  geometry is never copied, the result is verified before writing, and it is
  idempotent. On the canonical V3 candidate 58 tokens were restored with the
  complete native inventory, terminal partition and relevant DRC multiset
  unchanged before and after.
- Promotion: the canonical six-closure candidate passes the native, terminal and
  complete-CLI production gates against the canonical original reference
  (source 149 unrouted / 335 clusters vs candidate 143 / 329; 1060 terminals,
  0 splits) and becomes the accepted generation.

### Complete item inventory and deterministic duplicate-UUID repair — 2026-09-27
- The engine's `get_board_items()` returns the complete identity-bearing item
  inventory: tracks, vias, pads, board zones, drawings and groups, and every
  footprint with its graphical items, fields, zones and groups, each row carrying
  UUID, kind, source container, parent, layer, net and a save-stable physical
  identity. The DRC gate is built from that accessor alone, so a violation naming
  a zone, a courtyard graphic or a footprint can now be *proven* to name one
  physical item instead of falling back to condition-refined identity. A missing
  or failing accessor marks the inventory incomplete; it never silently falls
  back to the copper-only rows.
- `pcb_world/agent/kicad_metadata.py` repairs reused UUIDs on a *copy* of an
  imported board. It parses the real s-expression with source spans, rewrites only
  the identity tokens whose UUID is carried by more than one item, and splices
  them into the original bytes: geometry, nets, rules, footprints, layers,
  stackup, zone outlines, fill polygons, labels and locked copper are untouched,
  and object order is preserved. Minted identifiers are UUIDv5 of the occurrence's
  own parsed structure, so one physical object maps to one identifier across files
  and import orders; structurally identical occurrences, references to a reused
  identifier, and sidecars (`.kicad_pro` / `.kicad_dru`) that name one are refused
  rather than guessed. Normalization is idempotent and carries its mapping,
  source/canonical hashes and semantic digest as evidence.
- Measured on the frozen V3 reference: 922 reused identifiers over 8164
  occurrences are repaired (0 collisions, 0 references) in both the source and the
  six-closure candidate, all 8164 shared objects map identically, and the native
  DRC gate that refused the candidate before now passes it. The CLI gate reports
  19 of 20 classes exact with zero regressions and stays unverified only because
  the 9.0.8 reporter emits a varying number of byte-identical duplicate rows in its
  `unconnected_items` section between runs of the same board.

### Inventory-proven DRC identity and production final gates — 2026-09-26
- A captured reference baseline now travels as a bound *envelope*: the violation
  multiset plus the identity policy, the reference board/project/rules hashes and
  the engine build that measured it. The saved-artifact gate re-measures all of it
  before replaying any identity, so a baseline captured from another board, other
  sidecars, another policy or a stale engine is refused instead of riding on the
  reference's hashes. The production adapters also re-hash every input after each
  child returns (refusing on drift) and cap each child by the run's remaining
  budget rather than a fixed per-child ceiling.
- The native DRC gate no longer treats a UUID pair as an identity: it builds a
  board inventory from the engine's own track/via/pad items, keeps movement
  tolerance only where a UUID is carried by exactly one item, and otherwise falls
  back to condition-refined identity, where any movement is an addition. There is
  no proximity rule - a nearest-candidate guess is not proof, and duplicate
  identifiers are repaired on a disposable copy instead of relaxed into the gate. Measured on the frozen reference: 121 of
  6845 copper UUIDs are reused (821 of 7545 items) and 7972 of 7986 relevant keys
  have no inventory proof.
- The saved-artifact gate lost its key-set fallback: the baseline travels as the
  complete violation multiset and is replayed through the same collision-aware
  comparison the local transactions use, and a request without that evidence is
  refused with an explicit recapture message. Terminal relations are compared by a
  fresh process that opens both the frozen reference and the candidate.
- `ExperimentalArtifactStore.run_final_validation` now has production adapters
  (`pcb_world/agent/final_gates.py`, `tools/reliability/run_final_gates.py`): a
  fresh native reopen and baseline replay, a fresh two-board terminal partition,
  and the complete 9.0.8 CLI gate whose only exemption (churned
  `unconnected_items` pairing) is bound to that terminal proof. A CLI refusal now
  names the class regressions it saw even when another reason already stopped the
  verdict, and an explicit CLI environment is added to the inherited one instead
  of replacing it.
- Measured against the frozen original reference: the six-closure candidate passes
  the terminal partition but is refused by the native gate (179 added relevant
  identity groups, all moved conditions) and by the CLI gate (equal-count identity
  churn in relevant classes); the copper refill is refused by all three gates,
  including 33 split terminal relations. Nothing was promoted.
- `patches/engine/0002-phase5-integrity-and-copper-fill.patch` now carries all six
  changed engine files (the four `kicad-patches/rl/` sources, the SWIG 4.5.1
  compatibility fix and `engine_server/wire.py`), is cut against the
  `0001`-applied tree, and is proved by `tools/reliability/check_engine_patches.py`
  to apply cleanly from the pinned commit and reproduce this checkout's engine
  tree byte for byte. `patches/engine/README.md` documents the order and base.
- `bash tools/reliability/check_phase.sh --strict` → exit 0, 333 unit + 49 native
  tests, no skips.

### Phase-5 recovery correction — 2026-09-26
- Withdraw the earlier phase-5 V3 candidate acceptance claim. Its terminal
  evidence did not bind reused UUIDs to physical pad identity, and the CLI gate
  required stronger report integrity checks. Historical CLI measurements remain
  diagnostic only until reproduced with the corrected gates.
- Terminal and per-net relation counts from the earlier refill analysis are
  unverified and must not be used for promotion. Physical pad identity now binds
  UUID to geometry and inventory and refuses collisions.
- Refill changed 64 non-copper silk/mask zones, accounting for 95 added silk
  findings. Refill now targets copper zones only; repairs remain experimental.
- The current Python unit group passes 295 tests and focused terminal/CLI/store
  tests pass 38. The strict phase gate fails closed on cached-router provenance;
  native changes remain unbuilt because `wx-config` is unavailable.

See `docs/agent-work/reliability/{PLAN,CHECKPOINT,RESULT}.md` and
`docs/agent-work/reliability/phase2/` and `HISTORY.md`. **Phase 1 is accepted**
(2026-09-26) on scoped synthetic evidence: 159 unit + 24 native tests. **Phase 2
(the resumable routing agent) is ready for review**, not accepted: 225 unit + 30
native tests, `check_phase.sh --strict` exit 0, plus a bounded pilot against a
private board. Phase 3's pilot reported six closures, but the old fixed-name save
flow did not prove immutable saved-artifact safety or detailed reopened DRC
identity. Phase 4 adds those recovery gates; its strict native gate passes (260 unit + 37
native, zero skips), while board-level acceptance remains pending the documented
KiCad CLI identity discrepancy. Nothing here is tagged or released, and the implementation is local,
uncommitted and unpushed.

### Added — phase 2 (routing agent)
- `pcb_world/agent/observations.py` — board-derived layer mapping (refusing
  contradictions rather than guessing), net-pair enumeration from the ratsnest, and
  a compact planner observation (one pair, components, nearest obstacles, legal
  layer/width/via choices, progress, DRC delta, prior attempts) under a character
  budget
- `pcb_world/agent/scheduler.py` — deterministic candidate generation
  (direct/walkaround/shove/bounded detours/layer change), fixed acceptance-first
  ranking, attempt history that refuses to retry a failed plan, bounded stall
  detection, and provenance-verified atomic run state
- `pcb_world/agent/runner.py` — the resumable loop: deterministic-first, planner
  only for pairs the deterministic set could not close, per-net/attempt/time/token
  limits, accepted-generation checkpointing, structured report, and a stable
  planner error taxonomy (timeout / rate-limited / server / bad-response / auth /
  cancelled / unavailable)

### Recovery corrections — phase 4, pending review
- `pcb_world/agent/artifacts.py` stores immutable board/project/rules generations
  and switches one hash-checked pointer only after fresh-process acceptance.
- `tools/reliability/verify_saved_artifact.py` reopens the exact generation, proves
  its rules, geometry digest, progress counts, and detailed relevant DRC identities.
- Engine IPC calls now have per-operation deadlines that terminate only the
  connection-owned native child. Resume validates provenance at the core runner
  boundary and restores the checkpoint snapshot committed with the pointer.
- IPC deadlines are absolute across fragmented socket reads and refresh against
  remaining run time at every native dispatch, including startup and verification.
  Saved-board verifier trees are isolated and fully reaped after timeout/cancel.
- A final DRC rejection restores the progress tracker and report to the active
  artifact, marks locally accepted candidate attempts with a separate rejected
  final disposition, and preserves candidate diagnostics. Verified unsupported
  endpoints retire only their pair; quarantined planner failures stop immediately.
- Phase-3 exact net and anchor coordinates were removed from public result notes;
  the recorded pilot's acceptance claims were downgraded pending hardened recheck.
- `methods/llm_agent/tools/pcbworld_tools.py` and
  `methods/llm_agent/policy/structured_agent.py` — the supported agent integration
  over the same JSON surface, reusing the repository's providers
- `tools/reliability/route_agent.py` — the agent CLI (board/project/rules, run dir,
  provider `scripted|deepseek|openai-compatible|repo-api|repo-remote`, limits,
  `--resume`)
- Tests: `tests/agent/test_{scheduler_unit,runner_scripted,planner_client,native_runner,integration_gaps}.py`
- `docs/agent-work/reliability/phase2/{PLAN,RESULT,CHECKPOINT}.md`

### Changed — phase 2
- A run sets `KICAD_ENGINE_REUSE=0` by default and records it in
  `state.metrics["determinism"]`: a reused engine server carries a shifted UUID
  stream, which perturbs UUID-keyed ordering between runs
- The saved best-board artifact carries the **input** `.kicad_pro` and `.kicad_dru`
  next to the board verbatim, instead of the engine's re-serialized project, so the
  artifact is validated against the settings it was routed under
- `routing_target` is advisory: it is compared and reported (and listed in
  `unverifiable_properties()`), not fatal
- The DRC cache is re-seeded after a **verified** rollback, so an unchanged board
  is not re-costed a full DRC per candidate

### Fixed — phase 2
- Sub-0.25 mm cross-layer pairs are classified `coincident_cross_layer` and
  recorded as `unsupported_pair` instead of consuming attempts and planner tokens;
  the engine lays no via for these, so they cannot be bridged by `fix_route`
- A candidate's redundant zero-length `line` step, which the engine refused and
  which discarded an otherwise valid plan, is dropped before dispatch
- Planner token usage is recorded even when the reply fails to parse
- A DRC delta on a board nobody changed (the native DRC is not fully deterministic
  on a large board) is named in `report.notes` and kept in
  `metrics.drc_jitter_suspected`, instead of reading as a result of the run
- Resuming with a `--run-dir` that does not match the checkpoint's is refused
  before the engine is opened (`resume_run_dir_mismatch`, exit 3), instead of
  writing the state file back to the checkpoint's directory while the board
  artefacts go to the new one

### Phase 3 — corrected integration and real V3 progress

`docs/agent-work/reliability/phase3/`. Gate: 237 unit + 37 native tests,
`check_phase.sh --strict` exit 0. On the private V3 board six connections closed
under full native DRC acceptance (149 → 143 unrouted edges; promoted board
reopened at DRC 8139 against a 8149 baseline, 0 added relevant).

#### Added
- `tests/agent/test_native_layers.py` — 2- and 4-layer maps, overlapping XY across
  unrelated nets, through-hole pads, inner-layer anchors, spans-copper refusal
- `tests/agent/test_phase3_integrity.py` — resume totals across planner instances,
  request budget inside the retry loop, off-target planner replies, 2-value
  waypoints, artifact sidecars, inferred sidecars, final-DRC promotion refusal, the
  fair queue, a copied checkpoint's run directory, operator stall patience
- `best_board.manifest.json` — the promoted artifact's hashes, progress and
  verification, written next to the board
- `docs/agent-work/reliability/phase3/{PLAN,RESULT,CHECKPOINT}.md`

#### Changed
- `LayerResolver.build` reads `KiCadEngine.layer_map` and verifies it on a bounded
  sample instead of majority-voting the convention from every track, via and pad
  (~12 s → 0.01 s on V3); anchors resolve their layer from the ratsnest, with a
  net-checked fallback only for spans-copper anchors
- The pair queue is `scan_net_pairs`: round-robin across nets, ordinary
  connections before degenerate cross-layer bridges, every skipped pair explained,
  and completion decided by the engine's own unrouted count
- Candidates are probed one at a time, best-first, stopping at the first accepted
  plan (one DRC per accepted pair instead of six)
- `OpenAICompatiblePlanner` enforces its request/token/time budget before every
  HTTP attempt, reports tokens spent on failed attempts, and escalates the output
  budget once on truncation instead of re-sending an identical request
- Planner replies are bound to the requested connection (another tool, another
  pair or a malformed waypoint is refused as out of scope) and to the board digest
  they were computed against
- The saved artifact is staged, sidecar-verified, atomically promoted and reopened
  in a fresh engine; the run's resolved `.kicad_pro`/`.kicad_dru` are hashed and
  copied even when the CLI arguments are omitted
- `--max-new-tokens` defaults to 8000 (1600 was measured returning an empty reply)
- A resumed run uses the operator's stall patience and starts with a fresh stall
  count instead of inheriting the checkpoint's

#### Fixed
- `AgentSession.endpoint` credited a pad on another layer (and therefore another
  net) to the cluster under the probe; pads are now matched on their converted
  human layer, with a negative layer meaning spans-copper
- `_anchor_layer` no longer falls back to "any layer with copper" — it must be the
  anchor's own layer, the other anchor's layer with copper present, or a layer
  whose net the engine reports as this pair's
- Planner request totals no longer reset on resume (the reproduced `10 → 1` bug),
  and the request ceiling can no longer be exceeded inside one retry loop
- `reasoning_content` is no longer executed as a planner answer
- `attempt_plan_key` accepts `(x, y)` waypoints instead of raising `IndexError`
- Resuming can mutate copper again: the rule proof compares the engine's loaded
  rule file with the run's resolved rules by content, and a resume opens the
  artifact's own project (a project loaded for a different board yields no rules)
- `_save_best` no longer overwrites the only checkpoint before its hash is recorded
- A final native-DRC regression refuses promotion instead of being reported on an
  otherwise accepted board
- `DrcGate.seed` drops a baseline whose rule context no longer matches

### Added
- `pcb_world/agent/` — validated structured actions (`actions`), authoritative snapshots with a
  session-bound revision/fingerprint token and canonical nanometre geometry (`state`), a proven
  rule context (`rules`), native-DRC acceptance of every copper mutation (`drc_gate`), a
  transactional session with `connect_targets` / `probe_candidates` (`session`), and the JSON
  tool surface (`tool_api`)
- `tests/agent/` — 159 unit tests plus 24 native tests over generated synthetic boards
- `tools/reliability/check_phase.sh` — the single phase-check command; `--strict` is the
  acceptance mode (native must execute, no skips, load/provenance failures are errors), writing
  `evidence/phase_check.json`
- `tools/reliability/demo_structured_actions.py` — model-free transcript of the JSON tool surface
- `patches/engine/0001-routing-rule-context.patch` — engine-side fix for the routing-time
  design-rule context, plus the `get_routing_rules_path` /
  `was_routing_rules_loaded_from_file` / `get_last_drc_rules_load_error` accessors

### Changed
- `KiCadEngine` exposes the three routing-rule-context accessors (absent on older engine builds;
  the rule layer then reports the context as unanswerable rather than assuming the rules applied)
- `run_drc(rules_path)` records a requested-but-unreadable rule file instead of silently falling
  back to default rules
- Transactions are atomic by default; partial progress requires `provisional=True`, and no
  mutation is kept when the engine's own DRC reports a new relevant violation under the proven
  rule context
- A rollback the backend cannot confirm quarantines the session instead of reporting
  `committed=False` while copper may remain
- The engine patch's comments and bindings state the measured limit explicitly: loading the
  project rule file makes the rules available to the router's queries but does not prove the
  placement obeyed them, so acceptance is post-route DRC instead

### Fixed
- `tests/test_engine_api/test_router_provenance.py` — the "refused before any board work"
  assertion no longer trips over the Python 3.13 traceback's echo of a `-c` snippet's source line
- Stale-state detection now compares live engine state, not just the last issued token; unsupported
  schema versions, fractional layers, unknown fields and malformed endpoints are refused before
  dispatch instead of being coerced or raising `IndexError`
- An error after the DRC checkpoint (connectivity rebuild, unreadable post-probe, unexpected
  exception) now always attempts a verified rollback and quarantines when it cannot be
  established, instead of leaving unverified copper behind
- `run_drc` results are only trusted after the engine's rule-load channel is re-read; a reported
  load failure, a vanished rule file or a changed rule/project/pad identity refuses the result, and
  a caller context can no longer downgrade a loaded rule file to implicit rules
- The JSON adapter requires a non-empty token even on an opted-out session, validates point
  shapes/booleans/waypoints, refuses infinite coordinates, cannot emit NaN in a refusal, and
  reports `already_connected` as success
- `committed` is three-valued with an explicit `copper_state`, rollback verification covers target
  and layer, and probe results separate `would_commit` from the restored board
- DRC error codes now match the pinned engine (`DANGLING_VIA=12`, `DANGLING_TRACK=13`,
  `DRILLED_HOLES_TOO_CLOSE=14`), so drilled-hole spacing is no longer excluded from acceptance;
  `parse_drc_enum` plus a native header-parity test binds the table to the engine build, and a
  real hole-spacing violation is rejected with rollback
- `connect_targets` checks every post-step, pre-acceptance and final probe: an unreadable read
  restores and verifies (quarantining when it cannot), and no result can claim
  `ok`/`accepted`/`committed` while its snapshot is `unverified`

### Phase 5 — installed-KiCad cross-check resolved, and derived-copper refill

`docs/agent-work/reliability/phase5-kicad/`. Gate: 275 unit + 39 native tests,
`check_phase.sh --strict` exit 0. Acceptance uses the pinned engine (C++ stamp
`16e66300`) and a complete task-isolated KiCad CLI **9.0.8**; the installed
application **10.0.6** keeps its capped report, is used for diagnosis only, and is
never reported as verified.

#### Added
- `pcb_world/agent/cli_gate.py` — the installed-CLI acceptance gate: per-class
  report-cap detection, mandatory run-to-run reproducibility, exact identity
  comparison, full verdict provenance, and `env`/`cli_path` support for a
  task-isolated binary
- `KiCadEngine.fill_zones(rules_path)` and `PNS_RL_ROUTER::fillZones` (engine
  patches, stamp `16e66300` → filled) — rule-preserving refill of a zone's derived
  copper, with `zone_filler.cpp` linked into the routing module and its one
  interactive dialog path stubbed for the headless build
- `tests/agent/test_cli_gate.py`, `tests/agent/test_native_fill.py`
- `docs/agent-work/reliability/phase5-kicad/{PLAN,RESULT,CHECKPOINT}.md`

#### Changed
- `RoutingRunner` takes a `cli_gate` / `cli_verifier` configuration, records the
  CLI verdict in every generation manifest, refuses promotion on a `regressed`
  verdict or a required-but-not-`verified` one, and returns a fail-closed
  `blocked` report when the baseline itself cannot pass
- A native-only run reports `not_configured`; a required gate with no usable CLI
  reports `unavailable` and does not promote. Native acceptance is never reported
  as a CLI acceptance

#### Fixed
- The installed KiCad CLI truncates its DRC report at KiCad's per-class caps (499
  for clearance, 199 elsewhere), so comparing two such reports compares which
  findings made the cut. The reported "23 added clearance errors" between the
  frozen source and the six-closure candidate was that artefact. A complete
  reporter built from the pinned source shows the candidate adds **zero** finding
  identities in every class, with 20 new track segments and no other physical
  change.
- A rejected baseline promotion no longer escapes `RoutingRunner` as an exception;
  the run stops with a `blocked` report naming the refusing gate.

## v1.0.1 — 2026-09-16

Patch release. Observations, actions, reward, masking, DRC rules, benchmark splits and the wire
protocol unchanged — v1.0.0 numbers carry over except for the net-subset fix.

### Added
- Quick start — three front-door scripts under `tools/quickstart/`, and two guides,
  `docs/ENVIRONMENT.md` and `docs/METRICS.md`
- Router provenance — build stamps `ENGINE_CPP_HASH`, environment refuses a router from other
  sources (`PCBWORLD_ENGINE_ALLOW_MISMATCH=1` → warning)
- `KICAD_ENGINE_MAX_RSS_MB` (default 1024, `0` off) — over-budget engine server replaced, not reused

### Fixed
- Net-subset (`target_nets`) — DRC scored whole-board after a board reload; whole-board unaffected
- D1 recipes — two undefined helpers, so exit 127 instead of the documented exit 2
- Checkpoint without `policy_net_select` read as a net index
- Synthetic grid datasets — relative `--out-prefix` outside the repo root, split json without
  `dataset_dirs`

### Changed
- README → short landing page; `docs/QUICKSTART.md` on D1 — protocol runs on a locally generated
  corpus, the paper's own numbers do not

### Removed
- `external/RAGEN` and its overlay (`external/patcher.sh` takes `verl-agent` or `all`), and the
  rule-based upstream notes — angle guidance moved to `methods/baselines/rule_based/README.md`
- The documentation-consistency checker — development tooling, no longer shipped

## v1.0.0 — 2026-09-11

First public release. PCBWorld wraps KiCad's push-and-shove router as a Gymnasium environment
for PCB routing and ships the benchmark and the training/evaluation code around it.

### The environment
- `PCBWorld`: a Gymnasium env over a real `.kicad_pcb` board — six routing actions plus an
  LLM-only `idle`, hierarchical JSON-dict observations, every step scored by KiCad's DRC through
  the engine's push-and-shove router. Deterministic: the same board geometry and the same action
  give the same result across re-saved files and reused engine servers.
- The engine runs as a separate GPLv3 program ([PCBWorld-Engine](https://github.com/LGAI-Research/PCBWorld-Engine),
  pinned as the `engine/` submodule) that the environment talks to over a unix socket; no combined
  artifact is built or distributed.
- One shipped configuration: the reward rule `pcbworld_reward` (a linear pad-group DRC ladder),
  the masking rule `pcbworld_masking` (net_end free, no finish action), the fallback DRC rules
  `pcbworld_drc`, and one defaults file, `configs/pcbworld.yaml`, whose sections feed every
  command line. Observations carry obstacle and pad-shape tokens, arc outlines, per-net
  constraint channels and multi-resolution directional candidates by default.

### The benchmark
- Synthetic board generators (2-layer `synth_2L`, 1-layer grid families) and the D3 real-board
  set: 679 PCBench boards, rebuilt from the public PCBench clone by the shipped preparation chain
  (`tools/datagen/pcbench_prep/`; README Quick start §3), listed in `configs/datasets/d3.json`
  with easy / medium / hard difficulty splits. D3 is evaluation-only.
- One uniform three-stage evaluation (`eval/pipeline.py`: rollout → post-hoc DRC → aggregate)
  applied identically to every method; DRC scoring uses the same `pcbworld_reward` rule as training.

### Methods
- A decoder-only PPO / GRPO transformer trainer (`scripts/train.py ppo|grpo`; flex-attention
  path, BC-initialised fine-tuning options with a critic warm-up and a reference-KL penalty).
- LLM tool-calling agents (`OPENAI` / `Anthropic` / `Google` / OpenAI-compatible endpoints) over
  the same environment, and classical rule-based routers (FreeRouting, OrthoRoute, KRT) through
  the same evaluation pipeline.

### Paper reproduction
- The KDD 2026 workshop paper's recipes live under `experiments/kdd/` together with the exact
  configuration the paper used (`experiments/kdd/configs/`: its reward and masking rules, DRC
  rules, dataset splits and checkpoint map). Those rules resolve by name from the recipes, so the
  paper's numbers reproduce on this release even though the repository's shipped defaults differ.
- Checkpoints saved with earlier, paper-era settings evaluate with those settings
  (`experiments/kdd/configs/legacy_ckpt_defaults.yaml`; `tools/ckpt_migrate.py` writes them into a
  checkpoint file).

### Licensing
- This repository is BSD-3-Clause; the engine repository is GPLv3 (derived from KiCad).
