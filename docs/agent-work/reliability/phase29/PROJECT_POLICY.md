# Phase 29 (T29P) - do the two project sidecars change effective policy?

Status: **research complete; read-only.** On identical board bytes, under the
pinned post-T29 generation, the reduced EasyEDA-export `.kicad_pro` and the full
KiCad-rewritten `.kicad_pro` resolve to the **same effective routing and DRC
policy for every setting this board and this generation actually exercise**:
the same global minima, the same eight netclasses, the same class on each of the
191 populated nets, and the same DRC findings class by class, under both the
pinned native engine and the pinned build-tree CLI. Nothing was routed,
refilled, promoted or written back to a frozen input, and the accepted pointer
did not move.

This is a measurement, not a general claim that the two files are
interchangeable. The scope is stated in section 3.

Owner: task T29P. Private evidence (board bytes, board hashes, net names,
coordinates, item identifiers and raw reports) lives in
`phase29_project_policy/` in the board workspace; this page carries aggregates
and the two project-file digests only. Predecessors:
[phase 29 REBASELINE_REPLAY](REBASELINE_REPLAY.md),
[phase 29 REBASELINE_REVIEW](REBASELINE_REVIEW.md),
[phase 24 RESULT](../phase24/RESULT.md).

## 1. The two sidecars

The frozen V3 lineage carries exactly two project-file policies, in three
digests:

| | digest | bytes | shape |
|---|---|---:|---|
| **A** - reduced EasyEDA export | `08dcb693cf8801d5a7e461785c202ab5ff56fd2bcc8152a1aa09d5ad158e0ea7` | 5121 | empty rule-severity and design-default blocks, scalar netclass assignments, no `cvpcb` |
| **middle** - phase-24 refill | `886d6896d91c048bda406af79cccd073ceb0c23d92ec5bace4202edd4d28c9fe` | 13295 | policy B under a different `meta.filename` |
| **B** - KiCad-rewritten | `117b5243866dfdc4eb02450597e64151d8cbda34406677da609d880c4634f669` | 13295 | 57 explicit severities, 34 explicit defaults, list-valued assignments, `cvpcb` |

Every role was hash-pinned before measurement and re-hashed afterwards:

| role | sidecar |
|---|---|
| accepted generation | A |
| canonical original | A |
| phase-25 restoration baseline (the T27A baseline) | B |
| each of the six restoration steps' immediate parent, post-refill candidate and pre-refill snapshot | B on both sides |
| final experimental candidate | B |

One rule file, `ce92dd7b83822c4fdee4128afb284d77d9aeeb94d69fd869706d97bef5b71e25`,
is identical in every role directory that carries one, and was held constant in
every experiment below.

## 2. Effective policy on identical board bytes

Three boards - the accepted generation, the phase-25 restoration baseline and
the final experimental candidate - were copied byte-for-byte twice and measured
once under A and once under B, with the rule file constant and three whole-board
native DRC passes per probe in a fresh process of the accepted post-T29 engine
generation.

| board | under A | under B | resolved policy equal |
|---|---:|---:|---|
| accepted generation | 7934 relevant | 7934 relevant | **yes** |
| phase-25 restoration baseline | 317 | 317 | **yes** |
| final experimental candidate | 317 | 317 | **yes** |

"Equal" covers the resolved global minima, the Default class and all seven named
classes, the class the engine assigns to each of the 191 populated nets, and the
project-loaded and legacy-settings flags. The per-class identity table matches
too; on the accepted board both sidecars report the same eight classes, led by
7488 clearance and 149 hole-clearance identities, and on the restoration
baseline both report the same eight classes with no clearance identities.

Eight further hybrids of A were built, each adopting exactly one of B's
differing blocks (rule severities, design defaults, the extra `rules` keys, the
list-valued assignments, the dropped per-class annular width, the project
version, the `cvpcb` block, the BDS meta), and each was measured on the accepted
board's bytes. Every one returned the same 7934 relevant identities with the
same class table. **No single block is load-bearing, and neither is the whole
file.**

The native numbers were re-derived on a second path that counts violations
straight off the engine without importing the repository's classification
module, which another task was editing while this study ran. That path returns
7934 relevant / 8134 total on the accepted board under both sidecars and 317 /
536 on the restoration baseline under both, with identical class tables.

## 3. Representation, default expansion, effective policy

The files are very different as JSON - 2 pointers exist only in A, 142 only in
B, and 63 differ - but the difference is representational:

| difference | reading |
|---|---|
| A omits, B writes out, the design-defaults and `rule_severities` blocks | default expansion. The BDS parser is constructed with `_resetParamsIfMissing = false` and pre-populates its severity table from the library defaults, so an absent key keeps the same default the explicit value names |
| A omits 11 extra `rules` keys B writes | default expansion; every shared key carries the same value |
| A uses the version-1 scalar netclass assignment, B the version-3 one-element list | representation. The parser iterates the value and a JSON string iterates once, so both describe the same single-class assignment; all 60 nets and all 8 class names are identical |
| A writes `via_annular_width` 0.1 per class, B omits it | default expansion; the global `min_via_annular_width` is 0.1 in both |
| B writes transparent netclass colours, a `cvpcb` block, `ipc2581`, `layer_pairs` and 3d viewports | not read by the router or the DRC on this board |
| B writes teardrop options/parameters and tuning-pattern settings | **not exercised by this board and therefore not measured** - see the scope paragraph below |
| `meta.filename` and `meta.version` differ | representation; the net-settings meta version is 4 in both |

The two blocks that would matter if they were read differently - the severity
table and the netclass assignments - are the two this experiment measures
directly, and both resolve identically. The severity table is also checked by
consequence: a severity only decides whether a finding is reported under which
label, and both sidecars produce the same reported class table, class for class,
on both the native and the CLI path. A severity that disagreed on any class this
board exercises would show up as a missing or relabelled class.

**Scope of the equivalence.** The blocks B carries and A does not include
teardrop options and parameters and the tuning-pattern settings. This board
exercises neither, so neither is measured here, and the equivalence above says
nothing about them: on a board that uses teardrops or tuned patterns, a writer
that drops those blocks could change behaviour that these probes never touch.
Two further limits follow from the same reasoning. A one-time editor default
seeds new items and this study creates no items, so the `defaults` block is
measured only through the rules it resolves to. And these digests are two
particular files: an edited severity or netclass value in either would change
the policy, and nothing here would notice.

## 4. The independent CLI agrees, with a control

The pinned build-tree CLI was run four times on the accepted board's bytes:
three times under sidecar A and once under sidecar B.

As the reporter wrote them, all four reports are identical class for class:
9757 violation rows and **135 unconnected rows** in every one, with clearance
7488, hole clearance 149, isolated copper 1, shorting 34, drill out of range 230
and the other sixteen classes unchanged. Nothing in the raw report distinguishes
the switch, and nothing distinguishes two runs of the same policy either.

The gate's comparison view is stricter than the raw rows and less stable. It
drops byte-identical repeat rows and then keys each finding by its collision-safe
item identities, so the unconnected class collapses to **116, 117, 118 and 117
distinct identities** in the four reports - varying across the three runs of the
same policy, not just across the switch, and dropping 17 or 18 repeat rows each
time. That is the phase-27/T29A reporter instability: the same missing
connection is emitted with a different representative item, so a collapsed
identity set can move while the row count and the connection count do not.

Both views agree on the question this study asks. A rule difference would move
the raw row counts, and it does not; the collapsed-identity movement is a
property of the reporter that appears within one policy.

## 5. What the 7934-against-317 observation actually is

The observation is real and the attribution was not. Identical board bytes give
identical numbers under either sidecar, so the gap belongs to the **board**, not
the project file:

| board | relevant | clearance | hole clearance | isolated copper |
|---|---:|---:|---:|---:|
| accepted generation | 7934 | 7488 | 149 | 1 |
| canonical original | 7934 | 7488 | 149 | 1 |
| phase-25 refilled lineage | 317 | 0 | 2 | 19 |

Every one of the accepted board's 7488 clearance items involves a zone: 5462
track/zone, 696 via/zone, 691 through-hole-pad/zone, 573 pad/zone and 66
zone/zone, with none that involves no zone at all. That is phase 24's finding
read from the other side: those pours were filled at a finer clearance than the
pinned rule file asks for, and a supported refill under that rule file removes
all 7488 of them and resolves 147 of the 149 hole-clearance identities.

The isolated-copper class moves from 1 to 19. That is a **net +18**: nineteen
new identities appear and one disappears, so a comparison that reports what is
present in one side and absent from the other can legitimately show 19 added
identities for a net change of 18. Both numbers describe the same refill, and
phase 24 already records them as separate counts of one event rather than an
item-for-item correspondence.

So the two compared sides were indeed not comparable, but the reason is that
their copper carries different stored fills, not that their project files
disagree. The accepted and canonical roles are the un-refilled basis; the
phase-25 and candidate roles are the refilled basis. A comparison across those
two bases is dominated by the fill difference, and the sidecar contributes
none of it.

## 6. What this changes

* **A sidecar normalisation is not required for this lineage.** For the routing
  and DRC settings these boards and this generation exercise, either file gives
  the same policy, so the accepted lineage does not need a project-policy
  rebaseline. That is not a general statement that the files are
  interchangeable; see the scope paragraph in section 3.
* **The native relation still needs one common basis.** The T29C candidate
  relations compare role directories that each carry their own fills *and*
  their own sidecar. The fills are what matter. Every compared role should be
  staged from one fill basis. Reporting the zone-dependent classes separately
  from the classes that describe the candidate's own change is available as a
  **diagnostic only** - it explains where a difference comes from and it cannot
  stand in for a gate, relax one, or be used to accept a board. The candidate's
  real, refill-induced movement is the isolated-copper class (1 to 19, a net
  +18), which is exactly what the six retained step comparisons, all staged
  within the refilled basis, already show cleanly.
* **The CLI gate already does this.** `run_gate` refuses a configuration whose
  two sides carry different `.kicad_pro` digests, and the phase-26 wrapper
  supplies the reference project to both sides, so the CLI half of every
  relation is single-policy by construction.
* **Nothing here authorises promotion.** This study measures the policy the
  engine resolves; the candidate's own gates remain where T29C left them.

## 7. Limits

Three of the ten frozen roles were switched; the six step roles carry one
sidecar on both sides of their own comparisons and are not exposed to a switch
at all. The native probe ran three passes per side, which bounds but does not
eliminate the reporter's instability, so the comparison is the per-class table
and the invariant identity set rather than a raw row multiset. The CLI switch
ran one sidecar-B run against three sidecar-A runs, on the accepted board only.
Nothing in this document was changed by a new measurement; the corrections
after review re-read the captures that were already kept. This study does not
re-run the T29C candidate gates end to end, does not re-measure phase 24's
refill, does not exercise teardrops or tuned patterns, and does not authorise
promotion. One imported module is owned by another task and was edited during
the run; a second native path that does not import it, and the CLI path, both
reproduce the reported numbers.
