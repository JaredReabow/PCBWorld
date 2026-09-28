# Phase 15 - result

Status: **stopped at the research gate, as the contract requires.** The engine
can answer one half of the margin (the via a net actually adopts) and **cannot**
answer the other half (the applicable hole-to-copper clearance, and the
applicable copper clearance where custom rules are conditional) without guessing.
No engine or wire change was made, no screen was added, and no transactional
trial ran. The re-screen did produce one hard finding about the existing screen,
reported below. The accepted pointer is unchanged.

Contract: [PLAN.md](PLAN.md).

## The rule surface, measured

**Available, read-only.**

* The **via a net actually adopts** — `rules.resolve_via_size(engine, net)`
  returns the net's own effective netclass values, the board's floors, and the
  adopted diameter/drill, with a `source` and a `usable` flag. Every one of the
  four nets in question resolves cleanly (`source="netclass"`), so this half of
  the margin is answerable today and needs no new interface.
* The board's **minimum** copper clearance, track/via/drill floors, hole-to-hole
  minimum, copper-to-edge clearance, the default netclass and every netclass
  (`get_design_rules()`, `get_netclass_for_net()`), plus the custom-rule file's
  path, whether it loaded, and the last load error.

**Not available.**

* **Hole-to-copper clearance.** KiCad's own board settings carry it
  (`board_design_settings.h`: "Hole to copper clearance"), the rule language has
  a distinct `hole_clearance` constraint and a distinct `hole_to_hole` one, and
  the pinned engine's `getDesignRules()` reads neither: its list stops at
  `min_hole_to_hole_mm`, which is a **hole-to-hole** minimum and a different
  rule. The clearance provider resolves the hole case through the rule engine
  per item and layer, so the value is not a board scalar even in principle.
* **The applicable copper clearance where custom rules are conditional.** The
  board's `.kicad_dru` exists and loads, and it carries conditional clearance
  rules — including one scoped to anything touching a zone, at a value stricter
  than the board minimum — plus explicit hole and hole-to-hole constraints. The
  applicable value is the rule engine's answer for a specific pair of items on a
  specific layer; the engine exposes no rule-evaluation accessor, so a screen
  reading `min_clearance_mm` would be guessing.

**Verdict.** Term one of the intended margin is answerable; term two is not, and
term one is not answerable either when a custom rule overrides it. Per the
contract this is a stop, not a place to substitute guesswork.

## What the re-screen found

The stop does not mean the phase produced nothing. Repeating the phase-13 screen
while asking **every copper layer a through via spans** — instead of only the two
endpoint layers — is possible with the API as it stands, and it changes the
answer:

| | count |
|---|---:|
| cross-layer refusal edges re-screened | 16 |
| edges with a sample clear of foreign pour on **every** copper layer | **1** |
| of the four earlier openings, still clear on every layer | **1** |
| sample margin used | the board minimum + the adopted copper radius (unchanged) |

Three of the four openings the earlier phases relied on were **never clear**: at
two of them the point sits inside foreign pour on interior copper layers, which
the two-layer screen never queried, and for the third the same is true on a layer
beyond its endpoints. That is consistent with the corrected phase-14 replay,
whose added findings were hole-clearance and clearance findings on layers the
screen had not asked about. **This is a defect in the existing screen, not in the
rule question**, it needs no new interface, and it is reported here rather than
fixed because the rule half of the contract stops this phase and a change that
disqualifies three quarters of the previously reported openings is a decision the
orchestrator should see first.

**No transactional trial ran:** with the rules unanswerable, no opening can be
called rule-complete, and the contract's own condition ("trial only if
rule-complete openings remain") is therefore not met. Nothing closed; the
accepted pointer and hash are untouched.

## Proposed minimal query contract (decision requested)

One read-only accessor that answers the *rule engine's* own verdict for
prospective copper, so conditional custom rules and the board floors are applied
exactly as the DRC applies them. It must not mutate the board, must not be a
whole-board pass, and must be able to say "I cannot answer".

```cpp
// engine/kicad-patches/rl/pns_rl_router.h  (new, read-only)
struct RLRuleProbeQuery {
    int    layer;             // PCB_LAYER_ID
    double x_mm;
    double y_mm;
    int    net_code;          // the net the prospective copper belongs to
    int    item_kind;         // RL_RULE_ITEM_VIA | _TRACK | _PAD
    double copper_radius_mm;  // half-width of the prospective copper (0 = none)
    double hole_radius_mm;    // radius of the prospective hole (0 = none)
    double window_mm;         // how far to look for items that could obstruct it
};

struct RLRuleProbeHit {
    std::string item_uuid;        // the foreign item this verdict is about
    std::string item_kind;        // "zone" | "track" | "pad" | "via" | ...
    int         item_net_code;
    int         item_layer;       // the layer the verdict applies to
    double      distance_mm;      // engine-measured distance to that item's copper
    double      copper_clearance_mm;  // rule engine: clearance A=prospective, B=item, layer
    double      hole_clearance_mm;    // rule engine: hole_clearance, same pair/layer
    std::string copper_source;    // which rule produced the copper value
    std::string hole_source;      // which rule produced the hole value
    bool        copper_ok;        // distance >= copper_radius + copper_clearance
    bool        hole_ok;          // distance >= hole_radius + hole_clearance
};

struct RLRuleProbeAnswer {
    int         layer;
    bool        resolved;         // false => the engine cannot answer; never guess
    std::string reason;           // why not, when resolved is false
    std::vector<RLRuleProbeHit> hits;   // every foreign item inside window_mm
};

std::vector<RLRuleProbeAnswer> probeRules(
        const std::vector<RLRuleProbeQuery>& queries ) const;
```

Semantics the implementation must hold to:

* **The rule engine answers, not the harness.** Values come from
  `EvalRules(CLEARANCE_CONSTRAINT, prospective, foreign, layer)` and
  `EvalRules(HOLE_CLEARANCE_CONSTRAINT, prospective, foreign, layer)`, so
  conditional custom rules, netclass values and the board floors apply exactly as
  the DRC applies them. Reading `min_clearance_mm` and `m_HoleClearance` and
  taking the larger is *not* the contract.
* **Every copper layer the item spans.** A through via's barrel and hole exist on
  every copper layer it passes, so the answer is per layer and a caller must ask
  for all of them — the defect this phase measured.
* **Read-only and bounded.** No mutation, no checkpoint, no world resync, no
  whole-board DRC; the answer is bounded by `window_mm` per query, like the
  existing point query.
* **Unknown is first-class.** `resolved=false` with a reason when the rule
  context cannot be evaluated (no rule file, a failed rule load, a prospective
  item the engine cannot represent). A caller must then refuse to place the item
  rather than assume the board minimum.

Wire mirror: one `RLRuleProbeHit` / `RLRuleProbeAnswer` pair registered in both
`wire.py` copies, one wrapper in `kicad_engine.py`, and the screen change in
`pcb_world/agent/zone_coverage.py`. That is an interface change and is the
orchestrator's decision; this phase deliberately stopped short of it.

## Verification

| command | exit | result |
|---|---:|---|
| `bash tools/reliability/check_phase.sh --strict` | 0 | 480 unit + 135 native, no skips |
| `python tools/check_separation.py` | 0 | 4/4 checks; wire copies identical |
| `python tools/reliability/check_engine_patches.py` | 0 | 3 patches, 6 files byte-equal to the pin |
| `git diff --check` | 0 | clean |
| accepted-generation sha256 | - | unchanged; pointer untouched |

No public code changed in this phase: the rule probe above is a proposal, and the
re-screen and rule-surface measurements are private evidence. The strict gate was
re-run anyway and is green.

## Limits and risks

* **The verdict is about the current interface, not about the board.** A
  rule-complete margin may be obtainable another way (for example if the engine
  already exposes a scoped rule query this research missed); the contract above
  is the minimal shape that answers the question with the rule engine's own
  authority.
* **The layer-coverage defect is unfixed and now reported.** Until it is fixed,
  any opening the current screen reports must be treated as *not* proof that the
  via is clear, because interior layers are unchecked. Three of the four earlier
  openings demonstrate it.
* **No trial ran**, so this phase says nothing about whether a rule-complete
  opening would survive; it says the question cannot be asked with today's
  interface.
