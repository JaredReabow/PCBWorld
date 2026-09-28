# Phase 25 - the reconnection pilot

Status: **ready for review.** One of the nineteen component joins phase 24's
refill cost is back, placed by the pinned native router under the pinned project
and rules, and it **survives a legal refill of its own zones**. The candidate
adds **zero** relevant DRC identities and **zero** fragmentation against the
refilled board it was built from, and takes the fragmentation against the
accepted generation from 7 clusters / 19 minimum joins to 6 clusters / 18 (and
the canonical original from 33 broken relations to 32). It is **not
promotable**: eighteen joins remain, the complete pinned CLI gate returns
`unverified` for reasons this phase attributes, and the accepted pointer did not
move.

Contract: [PLAN.md](PLAN.md). Predecessors: [phase 23](../phase23/RESULT.md),
[phase 24](../phase24/RESULT.md).

## The answer

**One of the refill's nineteen missing joins is repairable with ordinary
routing.** On the shortest of the six two-component cases, the engine's own
walkaround placer committed a real connection between two pads that the
recomputed fills no longer join - three track segments, no via, no rule change -
and the whole-board native DRC did not gain a single relevant identity. The
board's fragmentation against the accepted generation went from 7 fragmented
clusters / 19 minimum joins to **6 fragmented clusters / 18 minimum joins** (five
two-component cases plus the fourteen-component one), and against the canonical
original from 33 broken relations to 32. Eighteen rejoins remain, and this phase
measures none of them.

What that does **not** buy is a promotable board. The candidate is the refilled
board plus one join, so it carries the refill's entire debt: nineteen isolated
copper fills, 313 relevant DRC identities, and eighteen of the nineteen rejoins
still missing. The complete pinned CLI gate refuses it, and the terminal proof
that the gate wants attaches only when a candidate preserves *every* connection
the canonical original has - which the accepted generation does (0 broken
relations) and the refilled lineage does not (33 before this phase's route, 32
after).

## 1. The reconstruction manifest (A25-1)

The seven fragmented reference clusters were re-derived from the boards
themselves rather than read out of phase 24's evidence, and the derivation
reproduces phase 24 exactly: 191 nets, 1 060 terminals, 321 reference clusters,
340 candidate clusters, **seven** fragmented reference clusters - six split in
two and one into fourteen - 33 comparator witness pairs, and
`Σ(k-1) = 6×1 + 13 = **19**` minimum component joins.

The manifest records the restoration as **19 component links**: one link per
`k-1`, each naming its two components and the closest pair of existing pads that
share a copper layer. The 33 witness pairs are the comparator's representatives
of those same seven fragments - one anchor per reference cluster - so they are
redundant: several of them name different terminals inside fragments that one
link already rejoins, and attempting all 33 pairs would place redundant copper
between components rather than restore more of the partition. The link count,
not the witness count, is the restoration target, and that distinction is the
reason this phase did not simply re-drive the recorded pairs.

## 2. Selection: a measured native path (A25-2)

Every two-component case was probed with the engine's own router, not with a
static clearance proxy: two strategies per case (walkaround, and mark-obstacles
as the straight-line control), each as `checkpoint -> native route -> whole-board
DRC -> restore`, with the restore checked against the baseline track, via, pad
group and ratsnest counts before the next probe ran. All twelve restores
returned the board to its baseline.

| rank | walkaround | straight-line control | added relevant DRC identities |
|---:|---|---|---:|
| 1 | committed, joined | refused | **0** |
| 2 | committed, joined | refused | **0** |
| 3 | committed, joined | committed | 1 (clearance) |
| 4 | committed, joined | committed | 1 (clearance) |
| 5 | committed, joined | committed | 1 (clearance) |
| 6 | refused | refused | 0 |

The selection rule was declared before those probes ran - walkaround committed,
then fewest added relevant identities, then the control committed, then shortest
anchor span, then lowest routing layer, then lowest net code - and it picks the
shortest-span case: both anchors on one copper layer, joined by the placer's own
strategy with no added findings. No part of the ranking consults a proxy
geometric argument.

## 3. The pilot transaction (A25-2)

The attempt ladder was bounded to five native transactions on a disposable copy,
each starting from the same checkpoint: walkaround direct, shove direct,
mark-obstacles direct, walkaround with one dogleg waypoint, and shove with the
same waypoint. The retention rule was declared before the ladder ran: the two
anchors must share one native cluster, the whole-board native DRC must add zero
relevant identities, the terminal partition must add no fragmentation against
the refilled baseline it started from, and it must reduce the fragmentation
against the accepted generation.

| attempt | committed | joined | added relevant | joins vs refilled | joins vs accepted | retained |
|---|---|---|---:|---:|---:|---|
| walkaround direct | yes | yes | 0 | 0 | 18 | **yes** |
| shove direct | yes | yes | 0 | 0 | 18 | supplemental |
| shove with a dogleg | yes | yes | 0 | 0 | 18 | supplemental |

The first attempt satisfied all four conditions, so by the declared rule the
ladder stopped there and no later attempt ran. It added three track segments in
0.13 s, changing the whole-board native DRC from 533 / 313 to 532 / 313
(fifteen connectivity findings resolved, fourteen re-paired, **zero** relevant
identities added).

Push-and-shove is exposed by this engine build, so once the declared rule had
stopped the ladder the two shove transactions were still run as a bounded
supplement, each from its own checkpoint on a disposable copy and each restored
and verified afterwards. Both commit (0.11 s each), both join the pair, both add
three segments and no via, and neither adds a relevant DRC identity or disturbs
the rest of the partition. Shove was therefore measured, not assumed; the route
of record remains the walkaround attempt because it ran first under the declared
rule.

Saved under its own name with the writer's metadata restore reported `ok`, the
disposable copy byte-identical afterwards, and the refilled input untouched. The
saved candidate is `0bb7155167a9de4e013dca329eb9978456c158a4c9b870d9f46455cf8622f80a`.

## 4. Measurement, and survival of a legal refill (A25-2, A25-3)

Four boards, each opened in its own fresh process, two whole-board native DRC
passes each, all passes within a board identical:

| board | total | relevant | connectivity | clusters | tracks | unrouted edges |
|---|---:|---:|---:|---:|---:|---:|
| accepted generation | 8 133 | 7 933 | 200 | 321 | 6 210 | 135 |
| refilled baseline | 533 | 313 | 220 | 340 | 6 210 | 154 |
| candidate | 532 | 313 | 219 | 339 | 6 213 | 153 |
| candidate after its own refill | 532 | 313 | 219 | 339 | 6 213 | 153 |

The accepted-generation row is the value **this phase's own process measured**:
8 133 / 7 933, with 33 `Items shorting two nets` identities. Three further
processes run immediately afterwards on the same bytes under the same rule file
measured 8 130 / 7 930 with 30, which is phase 24's recorded number. That is the
known cross-process shorting-class variation in the reporter, not a difference
in the board; it lands in the *resolved* column of any reference-relative delta
and never in the added column, and both values are reported rather than one
being chosen.

**Against the refilled baseline (regression test):** zero added relevant DRC
identities, twenty resolved, zero fragmented reference clusters, no vanished
terminal, comparator `ok`. The connectivity signal improved rather than
regressed: one ratsnest edge fewer, one pad group fewer.

**Against the accepted generation (restoration test):** 19 added relevant
identities, all of them the refill's own isolated copper fills - the ones phase
24 already measured - and **18 minimum joins against the baseline's 19**. The
fragmentation is now **six reference clusters, not seven**: the six two-component
cases became five, and the fourteen-component case is untouched, so the minimum
joins are `5×1 + 13 = 18`. One of the nineteen rejoins is in; eighteen are not.

**Refill survival:** a copy of the candidate was reopened, its zones refilled
under the same pinned rule file, saved, and reopened in yet another fresh
process. The reopened board carries the same clusters, the same tracks and the
same 313 relevant identities, and the relation against the candidate is zero
added relevant identities and zero fragmentation. The closure is copper, not a
stale fill polygon: recomputing the pours under the rules does not take it away.

## 5. The complete pinned CLI, and who owns the refusal (A25-3)

Three boards were gated against the canonical original, each staged with the
reference's own project and rule bytes on both sides (a save rewrites the
project sidecar into the writer's own schema) and each with the vetted
build-tree reporter run twice per board, `report_complete: true`,
`staged_inputs_match: true`, CLI `56dd7910af7d…`, provider `79ac6f6fcf39…`. All
three verdicts are **`unverified`, `ok: false`**; each carries its own terminal
proof, `complete: true` and `ok: false` in every case.

| gated board | clusters vs original | broken relations | proof `complete` / `ok` |
|---|---:|---:|---|
| refilled baseline | 340 | 33 | true / false |
| candidate, before its own refill | 339 | 32 | true / false |
| candidate, after its own refill | 339 | 32 | true / false |

The pre-refill candidate's refusal names: the fresh-process terminal proof
reports **`complete: true`, `ok: false`**; 39 unconnected identities the source
does not have; 26 source unconnected identities missing while the total did not
drop (147 vs 131); `isolated_copper` 4 vs 1; 36 added `starved_thermal`
identities; one added `track_dangling`; and unconnected items rising 153 vs 149.
The refilled baseline and the post-refill survivor refuse in the same shape (37
and 36 added `starved_thermal` identities respectively, the same
`isolated_copper` rise, the same single `track_dangling`, unconnected 154 and 153
against 149).

The same repository terminal proof was then run against three boards, each in
its own fresh process, to attribute that refusal rather than assume it:

| candidate | clusters | broken relations vs the canonical original | proof `ok` |
|---|---:|---:|---|
| accepted generation | 321 | **0** | true |
| refilled baseline | 340 | 33 | false |
| phase 25 candidate | 339 | **32** | false |

The accepted generation preserves every connection the original has; the refill
breaks 33 of them; this phase's reconnection restores one and leaves 32. So the
CLI's refusal is inherited refill debt plus one joined pair - **the pilot's own
route does not create it** - and it is still a refusal. Nothing in this phase
promotes the candidate, and nothing should.

### Inherited or introduced

The gate only ever compares a candidate with the canonical original, so it
cannot say whether a finding came from the refill or from the step under test.
The three gated boards' raw reports were therefore re-read and compared
class by class at finding-identity level, using the gate's own identity policy:
the refilled baseline as the reference, and the two candidates against it.

| class (identity count) | refilled baseline | candidate pre-refill | candidate post-refill | added vs baseline | removed vs baseline |
|---|---:|---:|---:|---:|---:|
| `clearance` | 0 | 0 | 0 | 0 | 0 |
| `hole_clearance` | 2 | 2 | 2 | 0 | 0 |
| `isolated_copper` | 4 | 4 | 4 | **0** | 0 |
| `solder_mask_bridge` | 23 | 23 | 23 | 0 | 0 |
| `starved_thermal` | 73 | 72 | 72 | **0** | 1 |
| `track_dangling` | 66 | 66 | 66 | **0** | 0 |
| `items_not_allowed` | 90 | 90 | 90 | 0 | 0 |
| `copper_sliver` | 0 | 0 | 0 | 0 | 0 |
| `shorting_items` | 30 | 30 | 30 | 0 | 0 |
| `hole_to_hole`, `drill_out_of_range`, `courtyards_overlap`, `lib_footprint_issues`, `silk_*`, `text_*`, `nonmirrored_text_on_back_layer` | 1 669 combined | unchanged | unchanged | 0 | 0 |
| unconnected (endpoint pairing) | 150 | 148 | 146 | 6 | 8 / 10 |

Read plainly: **no added relevant identities** - all classes above are unchanged
except one starved-thermal identity, which the reconnection *resolves* (73 in the
refilled baseline, 72 in each candidate). The `isolated_copper` identities the
gate objects to are 4 in the baseline and the same 4 in each candidate. The only
movement is the unconnected section's endpoint pairing, which is the reporter's
visualisation choice that the gate itself refuses to judge without a terminal
proof. **These findings were already present in the refilled baseline**; the
comparison proves inheritance, not the cause of every pre-existing finding. The
reconnection adds none at either the native or the CLI level.

## 6. Guards

| | |
|---|---|
| accepted pointer | unchanged, `57b8db6121ee…` |
| accepted generation board | unchanged, `6c4f8ab81b83…` |
| canonical original board | unchanged, `a6232800646a…` |
| phase 24 refilled candidate | unchanged, `c8648ff5853f…` |
| phase 25 candidate | own directory, own name, never the accepted board |
| refilled input to the pilot | byte-identical before and after |
| original EasyEDA project | hash unchanged, `8f8ddaed…`, mtime predates this phase |
| planner calls / paid API calls | 0 |
| staging, commit, push | none |

"Read-only" here means the bytes are unchanged, and that is what the guards
check: opening a board through the pinned engine moved the containing
directories' metadata timestamps on this host while no file inside changed hash
or mtime. A poll of both directories across a full proof run saw no file
created, renamed or removed, so this is filesystem metadata, not a write to the
board.

## 7. Verification

| command | exit | result |
|---|---:|---|
| `phase25_fragments.py` | 0 | 7 fragmented clusters, 19 links, 33 witnesses, matches phase 24 exactly |
| `phase25_select.py` | 0 | 6 cases x 2 native probes, 12/12 restores to baseline, shortest-span case selected |
| `phase25_route.py` | 0 | attempt 1 retained, 3 segments, 0 added relevant, 19 -> 18 joins |
| `phase25_shove_probe.py` | 0 | both shove transactions committed, joined, 0 added relevant, both restored to baseline |
| `phase25_measure.py` | 0 | 4 boards x 2 identical passes; 0 added relevant vs refilled; 0 added / 0 fragmentation after refill |
| `phase25_reference_proofs.py` | 0 | original-relative broken relations: accepted 0, refilled 33, candidate 32 |
| `phase25_cli_gate.py` | 4 | complete pinned CLI, `report_complete`, verdict `unverified` (a refusal is a result) |
| `phase25_cli_gate.py` (refilled baseline, own evidence file) | 4 | `unverified`; proof `complete: true`, `ok: false`, 33 relations; 37 added `starved_thermal` |
| `phase25_cli_gate.py` (post-refill survivor, own evidence file) | 4 | `unverified`; proof `complete: true`, `ok: false`, 32 relations; 36 added `starved_thermal` |
| `phase25_cli_identity_compare.py` | 0 | no added relevant identities vs the refilled baseline; unchanged except one `starved_thermal` resolved |
| `phase25_guards.py` | 0 | every guard holds |
| `phase25_tests.py` | 0 | 21 tests (unit arithmetic, declared rules, frozen-evidence consistency) |
| `phase25_docs_consistency.py` | 0 | 22 checks; this document and the private notes still state what the evidence says |
| `bash tools/reliability/check_phase.sh --strict` | 0 | 496 unit + 138 native, no skips |
| `.venv/bin/python tools/check_separation.py` | 0 | 4/4 checks; wire copies identical |
| `.venv/bin/python tools/reliability/check_engine_patches.py` | 0 | 3 patches, 6 files byte-equal to the pin |
| `git diff --check` | 0 | clean |

The public harness writes its own `docs/agent-work/reliability/evidence/
phase_check.json` on every run; that is its normal output path and the only
public file this phase caused to be written outside its own directory. The bare
`python3` on this host cannot spawn its own interpreter, so the repository checks
above were run with the interpreter `check_phase.sh` itself selects.

## 8. Limits and risks

* **One join of nineteen, one board, one engine build.** This is a pilot
  measurement. It says this repair exists and is cheap; it does not say the
  remaining eighteen joins are the same shape, and it does not measure them.
* **The candidate is not promotable and was not promoted.** It inherits the
  refill's debt in full, and the CLI gate refuses it - all three gated boards
  (refilled baseline, candidate before its own refill, candidate after its own
  refill) return `unverified` with a terminal proof that is `complete: true` and
  `ok: false`.
* **The CLI refusal is attributed, not excused.** The terminal proof shows the
  accepted generation passes where the refilled lineage fails, so the phase-25
  route is not the cause; the refusal itself still stands against this candidate.
* **Push-and-shove is measured but not stressed.** Both shove transactions
  committed and joined the pair without displacing other copper, so this phase
  shows shove works on this case; it does not test a shove that must move someone
  else's copper to succeed, which is the case where shove's cost would matter.
* **The cross-process shorting-class variation is present.** One
  accepted-generation process in this phase reported three more shorting
  identities than three repeats of the same measurement, which matched phase 24.
  It inflates the *resolved* column of reference-relative deltas and is reported
  rather than smoothed.
* **One instrument is weaker than it looks.** The selection probes and the pilot
  all use the engine's whole-board DRC under the pinned rule file; the complete
  CLI sees classes the native gate does not surface (as phase 24 also recorded),
  which is why both are run and neither is treated as a substitute.

## 9. Next bounded action

**Nothing here is promotable as it stands.** The pilot proves the repair
mechanism, and the honest next step is to decide whether to repeat it over the
remaining eighteen links or to attack the refill debt itself:

* **Repeat the pilot over the remaining links.** The manifest already names every
  link and its anchors, and the unclaimed second case has the same shape as the
  one piloted here (both anchors on one layer, committed under walkaround, zero
  added findings). A bounded follow-on could take the eighteen remaining joins
  link by link, with the same retention rule and the same refill-survival check,
  and measure whether the original-reference broken-relation count falls to zero.
  That would take the refilled lineage past the terminal proof the CLI gate
  wants, at which point the CLI refusal reduces to the refill's own classes
  (isolated fills, starved thermals) - the second decision.
* **Or attack the refill debt directly.** Nineteen isolated fills and 36 added
  starved-thermal identities are refill artefacts, not routing artefacts. The
  alternative phase-24 named - making the router pour-aware so the pours stay as
  drawn - is an engine capability decision with its own contract.

Either way the promotion ladder does not change: the accepted pointer's
connectivity preserved with no fragmented reference cluster, the whole-board
native DRC gate under the pinned rules, the terminal-partition gate, and the
complete pinned CLI gate against the canonical original - all of them, on the
candidate's own generation, before the pointer may move.
