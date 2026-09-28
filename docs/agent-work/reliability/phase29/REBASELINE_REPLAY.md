# Phase 29 (T29C) - the rebaseline under the accepted T29 provider

Status: **rebaseline complete; read-only; pending Astra review.** The six
historical phase-26 restorations were re-measured under the accepted post-T29
generation and replayed through the accepted T27A verifier; all six retain. The
final experimental candidate's original-relative and accepted-relative gates
were regenerated and **still refuse**. Nothing was routed, refilled, promoted or
written back to a frozen input, and the accepted pointer did not move.

Owner: task T29C. Private evidence (board references, identifiers, nets,
coordinates, raw reports and PIDs) lives in `phase29_rebaseline_replay/` in the
board workspace; this page carries aggregates only. Predecessors:
[phase 28 REPLAY](../phase28/REPLAY.md), [phase 29 REPAIR](REPAIR.md),
[phase 29 REPAIR_REVIEW](REPAIR_REVIEW.md).

## 1. Why the rebaseline

The phase-28 replay regenerated its six gate reports under the post-T28A
kiface. T29A then rebuilt both providers (the netless-first same-logical-pad
release), so those reports no longer bind the binaries the accepted verifier
measures with — the same failure mode the phase-28 replay existed to repair,
one generation later. This task regenerates the measurement once, under the
binaries the ledger now accepts, and keeps every verdict.

| artefact | digest prefix |
|---|---|
| build-tree `kicad-cli` | `56dd7910af7d190c` |
| `_pcbnew.kiface` | `c23f8cb9023e9e8d` |
| `kicad_rl_router.so` | `819a26f1ebe5affe` |
| `ENGINE_CPP_HASH` stamp | `b47d4f0c`, equal to the live tree content hash |
| provenance waiver | unset in every process |

## 2. The restoration baseline, corrected

The T27A restoration baseline is the **phase-25 refilled start**, and it reads a
complete **339-cluster** partition under the T29 binaries. It is not a phase-24
board and not a 335-cluster one. The fresh per-role ladder is:

| role | clusters | terminals | nets |
|---|---:|---:|---:|
| canonical original | 335 | 1060 | 191 |
| accepted generation | 321 | 1060 | 191 |
| phase-25 refilled start (T27A baseline) | 339 | 1060 | 191 |
| final experimental candidate | 333 | 1060 | 191 |

Every input — the accepted generation, the phase-25 start, the canonical
original, the final experimental candidate and each step's immediate parent,
post-refill candidate and pre-refill snapshot — was hash-pinned before
measurement and re-hashed afterwards. Nothing moved.

## 3. The six steps under one generation

Each step stages copies of its immediate parent, post-refill candidate and
pre-refill snapshot, regenerates the **complete pinned CLI gate for both sides**
with the same phase-26 gate code the original evidence came from, and then
replays the accepted verifier with **three fresh native instances per side**.
Every board was opened in its own engine server process, and no process ran
under the provenance waiver.

| step | transaction | joins vs accepted | clusters | native instances | CLI added classes | verdict |
|---:|---|---:|---:|---|---|---|
| 1 | walkaround, direct | 18 -> 17 | 339 -> 338 | 3 + 3, agree | unconnected pairing only (9) | retain |
| 2 | walkaround, direct | 17 -> 16 | 338 -> 337 | 3 + 3, agree | unconnected pairing only (6) | retain |
| 3 | push-and-shove, direct | 16 -> 15 | 337 -> 336 | 3 + 3, agree | unconnected pairing only (9) | retain |
| 6 | walkaround, direct | 15 -> 14 | 336 -> 335 | 3 + 3, agree | unconnected pairing only (7) | retain |
| 9 | walkaround, direct | 14 -> 13 | 335 -> 334 | 3 + 3, agree | unconnected pairing only (5) | retain |
| 17 | walkaround, one dogleg | 13 -> 12 | 334 -> 333 | 3 + 3, agree | unconnected pairing only (6) | retain |

Six of six retained with all six conditions true. No class was waived and no
instance was dropped: the CLI's only added class on every step is the
unconnected endpoint pairing, which the rule already admits behind a bound
fresh-process terminal-partition proof reporting zero vanished terminals, zero
vanished nets and zero split relations on every step.

## 4. Closure, multiplicity and the class that used to move

The anchor closure is re-derived from the candidate's own reopened cluster rows
and reported as the row index each anchor was found at. On every step both
indices are valid and equal (for example the last step reads one shared index
inside a 333-cluster partition), and that agrees with the verifier's own
closure boolean. An anchor found in no row can never read as "equal to another
missing anchor": that case is a refusal, and the module's own harness pins it.

The native condition is decided over all nine parent-by-candidate instance
pairs per step. Every instance of every board reports the same relevant
identities **at the same multiplicities**, the relevant union is 317 identities
on both sides of every step, and no pair and no instance shows an added
relevant identity. Nothing was hidden by picking a quiet instance.

The instability earlier phases recorded is gone in this generation: the
"items shorting two nets" class reports **34 rows** in every pass of every step
on both sides — the four pairs the T29 repair made visible are included, and
they are pre-existing overlaps the reporter used to drop on these bytes, not
new copper.

## 5. Terminal partitions, and the class that still churns

The native read and the CLI gate's bound terminal-partition proof, produced by
different processes, agree exactly on every step: 191 nets, 1060 terminals, no
vanished terminal, no vanished net, no changed net membership, and the cluster
count falling one per step from 339 to 333.

The CLI's unconnected pairing identity counts differ from the phase-28
campaign's (9, 6, 9, 7, 5, 6 here against 9, 6, 8, 13, 6, 8 there) while the
raw unconnected row counts are identical on both sides of every step
(153/152, 152/151, 151/150, 150/149, 149/148, 148/147). That is the documented
churn of that class: the row count is a property of the copper, the pairing is
the reporter's choice, and the rule judges it on the count plus the fresh
native proof.

## 6. Duplicate identifiers: the native comparison gate holds

This board reuses pad identifiers, so one pair key can stand for more than one
physical finding. The regression drives the verifier's own
`strict_native_delta` over three instances per side with evidence produced and
read by the real serialiser and loader — not a re-implementation of the gate.
Every case passed:

| case | shape | result |
|---|---|---|
| unchanged ambiguous inventory | same finding, same key, same condition, key unproven | accept — no false addition |
| one more occurrence, same key and condition | raw class total 1 -> 2, pair set unchanged | **refuse** — only the multiset sees it (0 unexplained identities, all 9 instance pairs show the addition) |
| same-count replacement, different condition | raw class total 1 -> 1, key unchanged | **refuse** — the condition-refined identity is an addition |
| equal-count movement under two ambiguous keys | raw total 4 -> 4, one row moved | **refuse** |

The completeness side is decided by the verifier's own guards: a payload
without native provenance, without a server identity, with the engine reuse
pool active, with a diverged board or with a stamp mismatch each refuses with
its own code, and a real-board run with a single native instance refuses
`native_passes_insufficient` — with the explicit diagnostic flag it measures
but is diagnostic-only and never retention-eligible.

The input census records the same property for every real input: **zero
duplicated identifiers on every pinned board** (the phase-6 metadata restore
repaired the source's reused UUIDs on the lineage this task measures), with
296-301 pair keys per board that the inventory cannot prove — keys, not
duplicates. Raw rows, distinct pair keys and collision-aware identities differ,
and the last of the three is what the comparison uses: the phase-25 start reads
536 raw rows / 513 pair keys / 535 collision-aware identities, the accepted
generation 8134 / 8116 / 8134, the final candidate 530 / 507 / 530. That
collapse is an accounting property of a coarser key, not a safety rule, which
is exactly why the same-count swap above is refused.

Every route item added by a step is checked the same way: the steps add 2, 2,
12, 6, 5 and 9 items against their parents, and **every added identifier names
exactly one item** — no new copper shares an identifier with anything else.

The structural inventory is immutable across the whole lineage: every pinned
board carries 321 footprints and 1161 pads, and the via, zone, shape, text,
field and arc totals are identical on all of them. The only kind whose total
moves is the routed copper (6108 tracks on the accepted generation, 6138 on the
final candidate). Routing added copper; it changed nothing the board declares
as structure.

## 7. The final candidate still refuses

Four pinned boards (accepted generation, canonical original, phase-25 start,
final experimental candidate) were each measured with three fresh instances,
and the complete pinned CLI was run twice per side with the accepted generation
as source and then with the canonical original. The gates are **complete** —
staged inputs match, both runs per side, report complete — and both **refuse**:

| relation | native added identities | terminal comparator | fragmented clusters / minimum joins | verdict |
|---|---:|---|---|---|
| candidate vs phase-25 start | 0 | ok | 0 / 0 | retain |
| candidate vs accepted generation | 19, all isolated copper | 21 split relations | 5 / 12 | refuse |
| candidate vs canonical original | 19, all isolated copper | 21 split relations | 5 / 12 | refuse |

The CLI refusal reasons are the phase-26 set unchanged in kind — the
unconnected pairing churn, unconnected items risen from 135 to 147, isolated
copper 4 against 1, the starved-thermal set, one more dangling track — with only
the pairing identity counts moving between campaigns. Twelve joins remain
before the fragmented clusters are whole again, and the phase-26 promotional
refusal stands.

## 8. Guards and limits

The protected hashes were re-checked after the run and are unchanged: the
accepted pointer, the accepted generation, the canonical original, the phase-24
and phase-25 boards, the phase-26 start board, all six parents, candidates and
pre-refill snapshots, the pinned rule file, the original EasyEDA project, the
accepted post-T29 binaries and the repaired provider sources. A freeze snapshot
of the verifier, the comparison source, the engine sources, the binaries and
this task's own scripts shows no drift. Two snapshots are kept: the one taken
while the captures were running, and the one taken after this task's own
candidate-gate script was corrected; the only difference between them is that
script, and no verifier, engine, binary or frozen board moved in either.
The public-document leak scan is clean and `git diff --check` passes.

Limits:

* this is a rebaseline of the *same* historical restorations. It is not a
  routing campaign, it does not close the twelve remaining joins, and it does
  not move the accepted pointer;
* the unconnected pairing identity counts are not stable across campaigns by
  design; only the raw row counts are, and no claim is made that a stable
  pairing identity exists;
* the accepted generation and the phase-25 refilled start carry different
  project sidecars. The verifier binds the candidate's and the immediate
  parent's sidecars and hash-pins each role's own; the difference has no
  binding effect, and it is stated rather than silently tolerated. The phase-28
  driver carried the same comparison without wiring it to an abort;
* `violations_from_evidence` replays ambiguity verbatim from the payload, so a
  payload stripped of its inventory proof would be treated as fully proven. The
  real worker always emits the binding, and the measurement path refuses a
  payload with no native stamp or no server identity, but there is **no explicit
  verifier check that the inventory binding field is present**. That is
  a finding for review, not a waiver;
* the candidate gates are one measurement each of a board pair, not a routing
  result;
* no paid API calls were made.
