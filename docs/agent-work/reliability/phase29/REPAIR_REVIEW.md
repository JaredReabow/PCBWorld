# Phase 29 (T29AV) - independent verification of the netless-first repair

Status: **independently verified, with one accounted-for reporter-rendering
limit on the refilled baseline and one wording observation on the repair's own
comment.** This is a read-only verification of T29A's implementation, its
provenance chain and its frozen differential. The verifier tree holds every raw
report, PID and item identifier; this page carries aggregates only. No board was
routed, refilled, saved over, promoted or edited; the frozen sources, the
accepted pointer and the rules were untouched, and nothing was committed.

Reviewed: the T29A source diff and its narrow semantics, the patch-0005
reconstruction, the build-tree source copies, the loaded native module and CLI
kiface (hashes, stamp, waiver), [NETLESS_ORDER.md](NETLESS_ORDER.md) and
[REPAIR.md](REPAIR.md), and an independent re-measurement of the synthetic and
frozen boards on both front ends.

## 1. What was independently reproduced

Everything below was measured by this task's own fixture, scripts and staging,
not by re-reading the T29A evidence. The author tree was read for provenance and
for the pre-repair record only.

| check | result |
|---|---|
| provider source hashes (RL fork, stock overlay) | match the T29A record |
| build-tree copies vs the patch-tree copies | byte-identical for both providers |
| engine patch series | five patches apply to the pin and reproduce the tree (9 files, wire copies identical) |
| loaded native module, CLI kiface, CLI binary | hashes match the T29A record; module stamp equals the tree content hash; no provenance waiver and no provenance problem |
| T29N manifest | 78 of 78 entries hash-match; 18 of 18 in the T29A manifest |
| frozen inputs (accepted, baseline, canonical, experimental) | unchanged since the T29N record |
| strict harness | unit 499 passed, native 146 passed with no skips, patch check OK |

The engine pin, the two provider source hashes, the module and kiface hashes, the
content-hash stamp (`b47d4f0c`, version `1.4`) and the CLI hash are the ones
published on [REPAIR.md](REPAIR.md); the CLI itself is unchanged
(`56dd7910...` before and after). The native front end loads the RL module and the
CLI loads the kiface: different artifacts by design, both compiled from the same
patched tree, and the unpatched tree's content hash differs from the stamp, so a
pre-repair router cannot satisfy the guard.

## 2. Synthetic verification, on the verifier's own fixture

A 26-footprint four-copper-layer board written from nothing but this task's own
script (its own nets, geometry, UUID namespace and expectations). Every
same-logical-pad family shares one pad number; the netted member sits at
footprint-relative x = 0 and its partner at a larger x, so the "flipped" board is
the non-canonical order and the writer's own sort key
(`FOOTPRINT::cmp_pads`: pad number, then footprint-relative x, then y) restores
the canonical one on save. Both declaration orders were measured on both front
ends, three fresh native server processes and two complete CLI invocations each.

Exact item-pair identity, multiplicity one, and **10 expected plus 11 forbidden
pairs**, identical in every run and on both front ends:

| case group | shape | rows |
|---|---|---|
| netless-first, in reach | netless member declared first, overlapping | 1 |
| netted-first, in reach | the same pair, other order | 1 |
| **same-logical-pad, not touching, in reach** | 0.10 mm between the boxes, both orders | 1 |
| same-logical-pad **out of reach**, both orders | partner beyond the query's reach | 0 |
| equal net | two netted members, one net | 0 |
| both netless | overlapping | 0 |
| two real nets | overlapping | 1 |
| two-sided `unconnected-(...)` | both names | 0 |
| **mixed** `unconnected-(...)` and ordinary net | one of each | 1 |
| netless + `unconnected-(...)` netted | netless first | 1 |
| ordinary collision / same-net / near / far controls | different footprints | 1 / 0 / 0 / 0 |
| **net-tie** exclusion / non-grouped net | pad on a grouped net, then on another | 0 / 1 |
| **the pair inside a net-tie group / the same pads ungrouped** | one footprint, one pad number, mixed | 0 / 1 |
| **netless NPTH partner / the same geometry plated** | one footprint, one pad number, identical size and drill | 0 / 1 |

Those rows answer the coverage gaps in the T29A fixture directly. Its NEAR/FAR
controls use *different* footprints, so they exercise the query reach for ordinary
collisions but not for a same-logical-pad pair - here the same-footprint,
same-number pair reports once at a 0.10 mm gap in both orders, so reach rather
than box overlap decides. Its net-tie control used *separate* footprints and so
never reached the repaired branch; here the net-tie footprint carries the mixed
pair itself, and the group - not the geometry - is what silences it, since the
identical ungrouped pads report once in both orders. The one-sided
`unconnected-(...)` name reports because the exemption needs both pads to carry
it. The save/reload round trip re-ordered exactly the three families whose
declared order was non-canonical, preserved the pad identity multiset, and
reported the identical 10-pair set - so on synthetic copper the report is now a
function of the pair, not of the order.

## 3. Report limit and scale

The pinned build removes KiCad's per-error-code caps
(`ERROR_LIMIT`/`EXTENDED_ERROR_LIMIT` are `INT_MAX` in the tree the module was
built from), so the caps never exhaust a class. **That does not make
`testShorting` always true**: the same function clears it, before the
same-logical-pad branch, for two order-independent conditions - both pad numbers
of one footprint in one net-tie group, and either pad being an NPTH that does not
flash on the layer. Disabling the caps does not disable either.

Of the two, only the net-tie group can apply to a same-logical-pad pair, and it
is measured here: the grouped pair reports nothing in either order while the
identical ungrouped pads report once. The NPTH condition cannot apply to one at
all, because setting the attribute clears the pad number and forces the net code
to unconnected, so an NPTH pad is never a same-logical-pad partner; the
attribute-isolating pair above (identical size and drill, one plated, one not)
shows the attribute alone changes the outcome on the ordinary path.

The caps were also tested as a consequence: a board carrying 320 ordinary
collisions plus 96 netless-declared-first same-logical-pad families reported
**416 shorting rows, all multiplicity one, with the exact pair identity set** and
no truncation near the stock 199 cap.

The repair's release cannot manufacture a row through either suppression: the
opposite traversal re-checks the same guards, so with `testShorting` false the
pair is silent in both orientations, and the release can only let the netted
member decide a pair that the netted-first order would already have decided.

## 4. Frozen differential

Measured on the accepted generation, the phase-24 refilled baseline and the final
experimental candidate, three fresh native processes and two complete CLI
invocations each, with the frozen inputs re-hashed afterwards and unchanged.

| board | native rows / pair keys / identities | CLI raw / distinct / identities | terminal partition | class counts and pair set |
|---|---|---|---|---|
| accepted | 34 / 34 / 34 | 34 / 34 / 34 | 191 nets, 1060 terminals, 321 clusters, complete | stable every run |
| baseline | 34 / 28 / 34 | 34 / **31** / 28 | 191 / 1060 / 335, complete | stable every run |
| experimental | 34 / 34 / 34 | 34 / 34 / 34 | 191 / 1060 / 333, complete | stable every run |

**The accepted board moves from 30 to 34 rows: exactly four pairs added, none
lost, multiplicity one**, and the four added pairs are the four
netless-declared-first same-logical-pad overlaps the diagnosis identified,
attributed here from the board **text** (declaration order plus the provider's own
branch rule) rather than from the report. The pre-repair side is T29N's frozen
capture of the same bytes; the repaired side is this task's own measurement.

Two derived copies of the accepted board converge:

* **order-flipped** - the four families' pad blocks exchanged in the text, every
  other byte preserved, geometry verified identical by re-parsing: 34 rows on
  both front ends, the identical pair set, and the identical terminal partition
  structure as the accepted board;
* **save/reopened** through the harness's own writer: geometry identical, 34 rows
  on both front ends, the same pair set and the same terminal partition
  structure.

The two front ends agree on **every** board at the raw row level and on the bare
item-pair set, and the class counts are stable across every repeat.

## 5. The baseline's 34-versus-31, and the identity accounting

The table above reports the baseline's CLI column as 34 raw rows but 31 distinct,
and its identity count as 28. That comes from the board, not the repair:

| step | count |
|---|---|
| CLI shorting rows as reported | 34 |
| minus rows repeated byte for byte (one collision rendered four times) | -3 |
| distinct rows | 31 |
| minus distinct rows sharing one report identity (a second family group whose four members render under one item pair) | -3 |
| distinct report identities | 28 |

Both collapses trace to the same cause: the refilled baseline's text reuses two
pad identifiers across four footprint instances in each of those two groups,
while the accepted and experimental boards do not contain those identifiers at
all. The native report carries each finding's own position and keeps the 34
distinct; the CLI report resolves the shared identifier to a single item, so its
report cannot express six of them separately.

This was isolated with a focused diagnosis: **eight CLI runs across two
independently staged copies all emitted 34 raw rows, 28 pair keys and the same
three dropped duplicates, identically**; the native side emitted 34 raw rows and
the bare pair sets matched exactly in both directions; and the MH-style
repetition is present in T29N's own pre-repair capture of that board, so it
predates this repair. Staging was verified byte-identical between the native and
CLI directories and against the frozen source for all three boards.

**This is recorded, not waived.** The comparison demanded exact raw-row and
pair-set agreement, and it accepts a lower distinct-identity count only when the
gap equals the collapsed duplicates plus the identity collisions, each counted
from the reports themselves. A gap larger than that accounting fails the check.
The consequence to carry forward is a property of the *gate and the board*, not
of this repair: on a board whose report cannot separate six findings, the CLI
gate's shorting identity count is a lower bound, so a removal among those six
could pass the CLI gate unnoticed - the native gate path distinguishes them by
position.

**Requirement on T29C.** The successor task must show, on a synthetic board that
reuses item identifiers the way the refilled baseline does, that the *native*
collision-aware multiset gate rejects a same-count replacement - one finding
swapped for another that the report renders under the same identity pair - and
that it does so when the raw class count is unchanged. A CLI-only demonstration
cannot discharge this: the CLI report resolves a reused identifier to one item,
which is exactly where the distinction is lost.

## 6. The comparison gate itself

The full gate was exercised on the repaired reporter:

* **accept** - candidate byte-identical to the source: `verified`, `ok`,
  `report_complete`, staged inputs matching, identical class totals across two
  runs per side;
* **refuse** - one new **reachable** mixed netless/netted same-logical-pad
  finding: `regressed`, refused with an added shorting identity that the source
  never reported and a raised total.

## 7. The "purely because this member was reached first" comment

The comment is too strong as written, and this review corrects it.

Orientation is *a* no-finding cause, not the only one. Within
`testPadAgainstItem`, `testShorting` is cleared before the same-logical-pad
branch whenever both pad numbers of one footprint sit in a single net-tie group,
and whenever either pad is an NPTH that does not flash on the layer. Both
conditions are order-independent and both survive the removal of the per-code
caps, so "the branch returned without a finding purely because *this* member was
reached first" is false as a general statement - even on this build.

What is true, and what the release actually relies on: for a reached mixed
same-logical-pad pair that is *not* suppressed by either guard, the branch
returns without a finding exactly when the visited pad is the netless member, and
had the netted member been visited first the pair would have been filed. The
release's guard therefore fires only on that orientation, and only inside the
SameLogicalPadAs branch: the equal-net and both-netless early return and the
two-sided `unconnected-(...)` exemption can never coincide with it, a filed pair
keeps its `has_error` de-duplication, and the two suppressions above cannot be
turned into a row because the opposite traversal re-checks them unchanged.

The net-tie case is measured here; the NPTH case cannot be reached by the
repaired branch at all, because setting that attribute clears the pad number and
the net. One further wording note: "reached first" is the declaration order only
because the pad pass runs as a single task; the deciding factor is which member
the visitor sees first, so the sentence would need revisiting if that pass were
ever parallelised.

## 8. Limits

* The pre-repair side of the frozen differential is T29N's capture of the same
  bytes under the pre-repair binaries, not a pre-repair binary rebuilt inside
  this task; the recorded pre-repair engine and kiface digests differ from the
  repaired ones, which is the A/B under test.
* The distinct-identity accounting is specific to boards whose reports reuse
  identifiers; it is reported per board rather than converted into a tolerance.
* The save/reload normalisation is pinned on synthetic copper; the real families
  are already in the writer's canonical order, and the writer's sort key was read
  out of the source rather than inferred.
* Nothing here decides what a netless pad physically inside a netted pad *means*
  electrically. The repair makes the overlap visible; the four additional rows on
  the accepted board are pre-existing overlap the reporter dropped, not new
  copper.
* The reruns this task made are all recorded: a two-run smoke measurement, two
  full three-by-two measurements that failed on verifier-side comparison wording
  (pair attribution typed as strings versus tuples; the distinct-identity
  accounting before it was split into its two components), and the final
  three-by-two measurement reported here. The measurements themselves were
  stable in every run; only the comparison code changed.
* The synthetic controls were extended after review to cover the same-footprint
  same-number net-tie pair, the in-reach non-touching pair and the
  attribute-isolating plated/non-plated pair; that extension was re-run on the
  synthetic board only. The frozen boards were not re-measured, and the review
  still reports the T29C requirement it raised.

## 9. Verdict

**In favour of acceptance.** The repair is the narrow one the diagnosis
specified, it lands in both provider copies and in the tree the two binaries were
built from, the patch series reproduces that tree, the loaded artifacts match the
stamp with no waiver, and on synthetic copper and on the frozen accepted board
alike the report is now a function of the pair rather than of the declaration
order. The accepted board's shorting class moves 30 to 34 with exactly the four
diagnosed pairs added and none lost, at multiplicity one, and the order-flipped
and save/reopened copies converge on the identical pair set and terminal
partition.

Two observations travel with the verdict and need no change to this repair: the
refilled baseline's CLI report cannot express six of its findings separately
because its text reuses pad identifiers (pre-existing, absent from the accepted
and experimental boards), and the repair's in-code comment is too strong as
written, because the net-tie-group and NPTH suppressions also produce a
no-finding outcome without regard to order - the first measured here, the second
shown not to be reachable by the repaired branch at all. Neither changes the
measured behaviour, and correcting the comment would be a source edit outside
this review's scope.

No promotion, routing or write-back is implied or performed by this review.
