# Phase 29 - the netless-first same-logical-pad suppression

T29 prerequisite diagnosis. Status: **complete; read-only; no repair**. Nothing
was routed, refilled or promoted, the accepted generation and the accepted
pointer are unchanged, and no engine, verifier, fixture, gate or ledger file was
edited. Private evidence (board references, nets, coordinates, UUIDs and hashes)
lives in `phase29_netless_diagnosis/` in the board workspace; this page carries
aggregates only.

The question comes straight out of T28's `RESULT.md` section 4: a family that
declares its netless partners *before* the netted pad reports nothing at all.

## 1. Where the behaviour comes from

The build tree's KiCad source is 9.0.8. Fetching the upstream 9.0.8
`drc_test_provider_copper_clearance.cpp` and diffing it against this tree's
overlay copy of the same file gives **one hunk**: T28's canonical-pointer swap in
the visitor. The build tree's copy of that provider is byte-identical to the
patch tree's overlay copy, so the `kicad-cli` front end really does load the
repaired provider.

The `SameLogicalPadAs` branch itself is **byte-identical in all four copies** of
the provider (upstream 9.0.8, the patch-tree overlay, the RL fork and the build
tree), sha256 of the block
`ea9907f40719d72453f8a9f08f10f75c56bb10bd7994593ee0a36676c67aeb9f`. The
suppression is upstream KiCad behaviour; the RL fork did not introduce it and
T28 did not touch it.

What the branch does, given a pair of pads from one footprint that share a pad
number:

```cpp
if( otherPad && pad->SameLogicalPadAs( otherPad ) )
{
    if( testShorting )
    {
        if( pad->GetNetCode() == 0 || pad->GetNetCode() == otherPad->GetNetCode() )
            return true;                  // no finding, and no signal to the caller
        ...
        reportViolation( drcItem, otherPad->GetPosition(), aLayer );
        has_error = true;
    }
    return !has_error;
}
```

The pad loop runs `QueryColliding` per layer with an R-tree filter and a
visitor. The filter records the layer it is about to test in the run's
`checkedPairs` cache and returns true; the visitor sets that entry's `has_error`
only when the test returned false (a finding was filed). So a visit that returns
`true` from the netless side - no finding - has already claimed the layer, and
the *other* pad's visit on that layer is filtered out. The pair is never
reported, on any layer.

Because the outer loop is `for pad { for layer }`, the deciding factor is which
pad of the pair the footprint declares first:

| declared first | rows filed for the pair |
|---|---:|
| the netted pad | exactly one (the first layer it shares) |
| the netless pad | zero |

One further scope difference, on the RL side only: under an incremental
clearance scope the RL fork returns from `testPadClearances()` immediately, so
the routing-time pass tests no pad-pad pairs at all. The stock provider has no
such guard. Both front ends therefore only ever see this class in a whole-board
pass, which is what the acceptance gate runs.

## 2. Reproduced on synthetic copper

Two disposable four-copper-layer boards were written from nothing but the phase
script. They carry the same footprints, pad numbers, nets, shapes and
coordinates; the only difference is the order in which a footprint's pad blocks
appear. Four family shapes: two netted pads on different nets wholly
overlapping; a netted pad with a netless same-number partner whose boxes are
0.1 mm apart; the same pair 3.5 mm apart; and a netless pair.

| board | native rows | CLI rows | same-logical-pad rows |
|---|---:|---:|---:|
| netted pad declared first | 2 | 2 | 1 |
| netless partner declared first | 1 | 1 | 0 |

The one case tested that is filed in both orders is a pair of two pads that each
carry a defined net on different nets - the pair the branch files before
consulting geometry. That is all this measurement says: it does **not** settle
what an overlap between a netted pad and a netless one means electrically. The
0.1 mm pair is filed when the netted pad is first and dropped when it is not:
reach is the R-tree's maximum-clearance proximity, not shape overlap. The 3.5 mm
pair is out of reach in both orders. Native and CLI agree on each board.

## 3. The frozen generations

Five frozen boards were scanned (text only): the accepted generation, the
experimental candidate, the canonical original, a canonical re-save and the
phase-24 refilled baseline. All five have the same shape, and there is **no
declaration-order difference between them**:

| | count |
|---|---:|
| footprints with two or more pads sharing a pad number | 24 |
| pairs of one netted and one netless pad sharing a copper layer | 69 |
| ... of those, whose pad boxes touch | 10 |
| pairs of two netless pads sharing a number | 178 |
| pairs of two netted pads on different nets | 0 |

| touching netted/netless pairs | order | outcome |
|---:|---|---|
| 6 (five families) | netted pad first | filed - today's 6 same-logical-pad rows |
| 4 (four families) | netless partner first | **dropped silently** |

The 59 non-touching pairs are outside the pad test's reach in both orders. Every
other family with a netted pad is netted-first for the same geometric reason: the
netless partners are large thermal pads whose position sorts after the netted
pad, while the four dropped pairs are a small netless rectangle sorted before a
large netted one.

## 4. Identical geometry, different order

A disposable copy of the accepted generation was rewritten with **only** the four
dropped families' `(pad ...)` blocks reversed. Every block is re-emitted byte for
byte; the parsed pad tables (number, position, size, net, UUID per footprint)
prove the two files carry identical geometry.

| bytes | native shorting rows | CLI shorting rows |
|---|---:|---:|
| accepted generation | 30 | 30 |
| order-flipped copy | 34 | 34 |

The four new rows are exactly the four dropped pairs; the six existing
same-logical-pad rows are the six netted-first pairs. Every other class is
identical on both files: clearance 7488, hole clearance 149, hole size 230,
unconnected 135, dangling tracks 65, holes too close 32, isolated copper 1.
So: identical copper, 30 rows or 34 rows depending only on declaration order,
on both front ends.

## 5. Save and reload

Each board was opened, saved into a disposable copy through the repository's own
writer, and re-measured:

| board | pads reordered by the save | shorting rows source -> saved |
|---|---:|---|
| accepted generation | 0 | 30 -> 30 |
| refilled baseline | 0 | 30 -> 30 |
| order-flipped copy | 4 | 34 -> 30 |
| hand-written synthetic fixture, netless-first | 4 | 1 -> 2 |

Two conclusions. First, the frozen boards are already in KiCad's canonical pad
order, so for them a save/reload is stable: no reorder, no report movement, every
class identical. Second, KiCad's writer **normalises** the order - the
hand-flipped copy came back in the original order, and the suppression with it.
A hand-reordered file is therefore not a durable fix; and a hand-written or
freshly imported board that has never been saved by KiCad can gain or lose this
class on its first save. The observed canonical order is consistent with sorting
each footprint's pads by position (x, then y) across six families, with one
unresolved tie-break for pads at an identical centre.

## 6. Front ends under the repaired binaries

Engine source hash `2f9e6153`, `_pcbnew.kiface` `dfe466906eff9bc8`, `kicad-cli`
`56dd7910af7d190c`; no provenance waiver was set for any capture in this phase.
On the accepted generation the native engine and the pinned CLI report the same
30 shorting rows, the same 30 pairs, and the same six same-logical-pad pairs;
every class both front ends run agrees row for row (clearance 7488, hole
clearance 149, hole-to-hole 32, hole size 230, isolated copper 1, dangling tracks
65). On the order-flipped copy they agree on 34.

The shorting movement T27B chased was therefore the pointer-multiplicity defect
T28 repaired, not a front-end disagreement: the remaining discrepancy is
declaration order, which both front ends follow identically.

## 7. Risk scope

* The one case measured as order-independent is a pair of two pads that each
  carry a defined net on different nets. Nothing here establishes that an
  overlap between a netted pad and a netless one *is* a genuine short, or that
  this class is the only one the suppression could hide: the electrical
  implication of netless copper inside a netted pad is undecided by this
  diagnosis, which measures what the provider reports, not what the copper
  means.
* On the accepted generation the suppression leaves **four geometric
  netted/netless same-number overlaps reported by neither front end**, and the
  accepted board's shorting class reads 30 where the same copper read out of
  order reads 34. Whatever those four overlaps mean electrically, they are
  findings the reporter drops on these bytes.
* The gate exposure was measured only on the frozen boards. There, pad order is
  stable across saves (0 pads reordered, every class identical), so a baseline
  capture and a post-attempt capture on those bytes see the same order and no
  gate decision is perturbed by this defect. The experiment that reordered a
  disposable copy shows the row set *does* move with the file's order (34 -> 30
  once the writer normalised it), so no general direction is claimed here: a
  board whose order changed between a baseline and a post capture could move
  rows either way, and that movement was not carried through the gate end to
  end. A board that has never been through KiCad's writer could also acquire or
  lose this class on its first save mid-run.
* Anything that turns a same-logical-pad row into a per-layer row would be a
  regression of T28; the pointer repair and this defect are independent, and
  both must hold at once.

## 8. Proposed repair and validation contract (for Astra)

Not implemented here.

**Invariant to restore.** Scoped to the pairs the provider already reaches: two
pads of one footprint sharing a pad number, queried on a shared copper layer,
returned by the existing R-tree query under the board's own maximum clearance,
with shorting enabled (no `DRCE_SHORTING_ITEMS` error limit reached, the layer a
copper layer, and both pads flashing on it) and with **every existing exemption
preserved verbatim** - the equal-net and netless early returns, the net-tie
suppression keyed on the footprint's pad-number-to-net-tie map, and the
`unconnected-(...)` short-name exemption that requires *both* pads to carry that
name. For those reached pairs, the findings must be identical in both
declaration orders: exactly one shorting row for a pair the branch decides to
report, and none for a pair it exempts. Nothing in this invariant widens the
reached set, changes a clearance or proximity bound, or changes an exemption.

**Minimal change.** The visited member's netness must not decide the pair. The
smallest edit that gets there is in the visitor, not the branch: when a pad-pad
test returns *without* a finding for a `SameLogicalPadAs` pair, release the layer
bit the filter claimed instead of leaving it claimed (and do not set
`has_error`). Then the netted member's later visit passes the filter, files the
single row and sets `has_error`, which suppresses the remaining layers exactly as
today. The principled framing is the same change expressed as a contract: the
pair cache may record "decided" only for a decision that was actually taken, so
the decision must be a function of the pair (both pads), not of which member the
loop happened to reach first.

Points the repair must respect:

* it must land in **both** provider copies (the RL fork and the stock overlay the
  CLI loads), as T28's did, and the build-tree copy must stay byte-identical to
  the overlay;
* the `has_error` de-duplication must keep collapsing the pair to one row; a fix
  that merely clears the bit unconditionally would trade a false negative for a
  per-layer multiplicity - the defect T28 removed;
* no rule, no waiver, no severity change, and no clearance or proximity change;
* every existing exemption must survive unchanged: the equal-net and netless
  early returns, the net-tie pad-group suppression, the error-limit checks and
  the two-sided `unconnected-(...)` short-name exemption. In particular the
  `unconnected-(...)` test must keep requiring **both** pads to carry that
  name, so a pair with one such name and one ordinary net stays reportable
  exactly as it is today;
* it must not alter which layer the row is filed on for the already-reporting
  order, or the accepted board's six existing rows would churn.

**Validation contract.**

1. Extend the synthetic fixture so the netted/netless family exists in *both*
   declaration orders and assert **1 row per pair in both**, for the pairs the
   existing query already reaches - the present reverse-order fixture asserts
   nothing about the suppressed pairs, which is why this defect survived T28.
2. Keep the existing order-independent assertions: exact pair set, multiplicity
   exactly one, ordinary collisions and the clean control unchanged, on both the
   RL provider and the pinned CLI, over fresh processes.
3. Re-run the frozen differential: the accepted generation's shorting class must
   move from 30 to 34 rows, the four new pairs must be the four
   netted/netless overlaps, and every other class must keep both its count and
   its identity digest. Record that movement as *reported debt that was already
   there*, not as new copper.
4. Prove the provenance chain: the patch set reproduces the engine tree, the
   stamp equals the tree hash, no waiver is set, and the two compiled artifacts
   are the ones measured.
5. Confirm the gate's behaviour on the new row set: with the accepted
   generation re-baselined, an unchanged board still passes and a board with a
   deliberately introduced family overlap is refused.

**Open design question, for the owner, not the reporter.** Those four pads are
netted (or netless) as the design says; whether the intended copper relationship
is what the schematic intends is a board decision, and so is what copper a
netless pad physically inside a netted pad actually forms. The reporter repair
makes the overlap visible; it does not decide what the design should be, and it
is not evidence that the netless pad is a short.

## 9. Limits

"Dropped" is derived from the declaration order plus the branch semantics, then
confirmed for the four frozen pairs by the flip probe; the 59 non-touching pairs
were not forced into range, which would be a different experiment. The R-tree
reach bound is the board's own maximum clearance and was observed, not
re-derived. The writer's sort key is inferred from six families rather than read
out of KiCad's writer source; the durable result - that a save reproduces the
suppression - does not depend on the key. Private evidence and the exact board
hashes are in `phase29_netless_diagnosis/` in the board workspace.
