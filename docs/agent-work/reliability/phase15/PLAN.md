# Phase 15 - a rule-complete via margin, or an honest stop

Status: in progress (2026-09-28). Phase 14 is the accepted predecessor
([RESULT.md](../phase14/RESULT.md)); this phase's outcome is in
[RESULT.md](RESULT.md).

## What phase 14 left

Phase 14's three executed openings closed their connections and were refused by
the native DRC. The corrected replay showed the refusals are `Clearance violation`
and `Hole clearance violation` findings between a **zone** and the copper the
transaction had just created, at or beside the opening. The obvious next question
is whether the screen's margin can be made authoritative: the real via size, the
real copper clearance, and the real hole-to-copper clearance.

## Contract

1. **Research first, read-only.** Determine what the pinned engine actually
   exposes for (a) the via size PNS adopts for a net, (b) the copper clearance
   applicable to via-vs-foreign-zone on a layer, and (c) the hole-to-copper
   clearance for the same pair — without conflating hole-to-copper with
   hole-to-hole, and accounting for custom `.kicad_dru` rules.
2. **If the rules cannot be answered without guessing, stop.** No engine or wire
   change in this phase; return the minimal query contract and the evidence that
   the current interface is insufficient.
3. **If they can, implement an opt-in read-only screen** whose margin is
   `max(via_radius + copper_clearance, drill_radius + hole_clearance)` per net
   and layer, with full source/provenance and explicit unknown handling.
4. **Re-screen the sixteen cross-layer refusal edges** from the accepted board,
   report counts publicly and the per-edge detail privately, and compare the four
   earlier openings.
5. **A transactional trial only if rule-complete openings remain**, matched
   ON/OFF, prioritising the opening for the three edges the DRC refused before;
   otherwise no DRC trial at all.
6. **Promotion only through the immutable store** and only if something closes.
   Otherwise the accepted pointer stays put.
7. Public docs carry no board geometry, net names or rule values; no footprint
   moves, zone deletion, clearance relaxation, commit, stage or push.

## Evidence

* Rule surface: `pcb_world/engine/wire.py` (`DesignRules`, `NetClassInfo`),
  `engine/kicad-patches/rl/pns_rl_router.cpp` (`getDesignRules`), and the bound
  export list in `engine/kicad-patches/rl/pns_rl_bindings.cpp`.
* Via adoption: `pcb_world/agent/rules.py` (`resolve_via_size`).
* Pinned KiCad source: `board_design_settings.h`, `drc_engine.cpp`,
  `drc_rule_parser.cpp`, `drc/drc_test_provider_copper_clearance.cpp`.
* Private evidence: `phase15_routing/` (rule-surface probe, layer-complete
  re-screen, notes).
