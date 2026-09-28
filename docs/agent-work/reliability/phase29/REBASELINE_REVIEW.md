# Phase 29 (T29CV) - final independent review of the T29C refresh (snapshot `3fa97a20`)

Status: **ACCEPT recommended; Astra owns acceptance. T30R stays held until
Astra accepts.** Taken on the frozen sixth packet. Everything is pinned to the
hashes below; no mutable live script was read without a hash check, and no
author artefact, board, rule or pointer was written. Aggregates only - no board
geometry, identifiers, nets or coordinate examples. Previous reviews on this
path are historical and describe earlier revisions.

## 0. Pinned snapshot

| artefact | sha256 |
|---|---|
| public `REBASELINE_REFRESH.md` | `5435a97664338184f8f956957de66a53e5c27b167cc57b84423fa6470f9786a5` |
| private `refresh/MANIFEST.json` | `fb0c69a82089b35fd2ee0df8a99e5ac11d4d191031bd93e3cfe2b9a34dbd5ead` (474 files, tree digest `857422f9e248e963e07d18d377ad84f0e75781cfa244ddb523652c4627d16039`) |
| `p29c_fill_repr_diff.py` | `3fa97a20452c8749…` |
| `p29c_native_envelope.py` | `514dac647b6fcd41…` |
| `p29c_relation_verdict.py` | `6af3380d462e57e3…` |
| `p29c_common_fill.py` | `11f0671d801d64fd…` |

## 1. Generated-fill exclusion now enforces depth (prior finding closed)

`_is_generated_fill` now takes an explicit `ancestry` and requires
`tuple(ancestry) == ("zone",)` in addition to `top_head == "zone"` and
`parent_head == "zone"`, so a `filled_polygon` is excluded only as a direct
child of the root-level zone. Exercised through `structure_report` and
`fill_only_proof`, both directions:

| shape (fill content differs) | contract | observed |
|---|---|---|
| direct child of a top-level zone | exclude | excluded (correct) |
| inner zone under a top-level zone | difference | **substantive** |
| unknown wrapper then inner zone | difference | **substantive** |
| group then inner zone | difference | **substantive** |
| one-side add / one-side remove under an inner zone | difference | **substantive** |
| non-zone wrapper direct parent (control) | difference | substantive |

The predicate itself returns false for the nested and wrapper-then-inner-zone
shapes. All call sites pass `ancestry` - the raw node counter, the absorbed
unmatched accounting, `compare`, the fill-collection step and `exact_form` - and
the generated-fill exclusion is enumerated as a contract rule
(`CONTRACT_RULES = KNOWN_FOLDS + (GENERATED_FILL_EXCLUSION,)`).

## 2. Fold contract audited and exercised, both directions

Differences are granted only through `recognise_fold` over
`FOLD_RULES = (_angle_fold, _fill_fold)`. Source inspection confirms every
lexical relaxation is enumerated and constrained by the full structural path,
exact shape, token kind and value relation: the angle rule requires the exact
angle position, an approved immediate parent, full approved ancestry, equal
arity and both tokens bare (`_bare_int` rejects quoted) and congruent mod 360;
the fill rule requires a `fill` whose top head and immediate parent are a
top-level zone, ancestry exactly `("zone",)`, one extra token that is the bare
text `yes`, and every other child equal by raw text. 16 contract cases, 0
failures:

| class | cases | result |
|---|---|---|
| positive | bare `0`/`360` angle; `(fill)`/`(fill yes)` | fold (`at_angle_mod_360`, `fill_mode_default_expansion`) |
| negative | quoted `"360"`, quoted `"yes"`, other token, changed body, non-congruent angle, moved coordinate | substantive |
| negative | wrong arity, nested head in angle, fill not a direct top-level-zone child | substantive |
| negative | unknown subtree, root head changed, list/atom swap, duplicate identifier, missing item | substantive |

## 3. Regressions and bindings re-checked

| check | result |
|---|---|
| numeric alias | the canonical-integer rewrite of a fractional coordinate, the integer-for-fraction rewrite and the quoted-numeric rewrite are all `substantive` on both references (exact values in private evidence) |
| native envelope digest/PID/waiver | clean envelopes validate; every PID, digest, provenance, waiver and pass-count mutation is refused; the only accepted mutation is the no-op `capture_rules_sha_removed` (the real capture carries no `rules_sha256`) |
| raw coverage | independent node count equals the report totals exactly on both references; no identifier collisions |
| six-step envelopes | 6 of 6 true for both roles |
| six relations | `FR→F(C)` complete-retains on both references; all four R-based relations are measured refusals; `integrity_ok` true, no integrity failure |
| four fill proofs | representation-only with all folds named and zero unexplained nodes |
| manifest | 474 declared, 474 recomputed, tree digest identical, 0 missing/extra/changed |
| protected inputs | 35 of 35 re-hash equal |
| accepted pointer/board | pointer `57b8db61…` and board `6c4f8ab8…` unchanged |
| public strict harness | 640 unit, 146 native, engine patches reproduce the tree |

## 4. Private evidence

The packet's retained `refresh/tests.json` (read-only) reports 281 of 281
passing, matching the claimed count. The private suite was not run in place
because `p29c_tests.py` writes its evidence into the refresh tree; an isolated
copy is not accepted as evidence because its path bindings differ. The public
strict harness was run and is green. The author manifest hash was re-checked
after every verifier run and is unchanged (`fb0c69a8…`), so no author drift was
introduced.

## 5. Limits

* no board and no engine was opened; the native, CLI and terminal evidence are
  the frozen packet's, re-read and re-checked, not re-measured;
* the fold, generated-fill, numeric and envelope attacks were produced on
  disposable copies by calling the isolated snapshot's own functions; no author
  artefact was written;
* the six steps, four fill proofs and six relation verdicts were read from the
  frozen snapshot's retained evidence, not regenerated;
* this is a verification packet; Astra owns the acceptance decision.

## 6. Verdict

**ACCEPT recommended for T29C snapshot `3fa97a20`.** The generated-fill
exclusion now enforces the direct-top-level-zone depth requirement and the
nested-zone, wrapper and one-side cases are substantive; the finite fold
contract is enumerated in one place and passes both directions; the numeric and
quoting collapses, the envelope PID/digest/waiver gaps, duplicate/missing
occurrences and the coverage reconciliation are all clean; and the 474-file
manifest, the 35 protected hashes, the accepted pointer, the six steps, the four
fill proofs and the six relation verdicts stand as measured. No material
finding remains on this snapshot; T30R is released only on Astra's acceptance.
