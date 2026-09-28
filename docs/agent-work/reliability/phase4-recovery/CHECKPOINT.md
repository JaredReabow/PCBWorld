# CHECKPOINT — phase 4 recovery boundary

Updated 2026-09-26. Status: **recovery implementation passes strict native gate;
board-level acceptance has a KiCad CLI identity discrepancy**.

**Superseded (phase 5).** The discrepancy below is explained in
[../phase5-kicad/RESULT.md](../phase5-kicad/RESULT.md): the installed KiCad CLI
truncates its DRC report at KiCad's per-class caps (499 for clearance, 199
elsewhere), so the "23 added / 27 resolved" comparison was measuring the
truncation boundary, not the board. A complete reporter built from the pinned
source shows the six-closure candidate adds **zero** finding identities, and a
zero-routing load/save roundtrip of the source is physically identical to it.
This document is kept as the phase-4 record.

| Gate | Status |
|---|---|
| Immutable generation + atomic pointer | passed unit pointer/hash/crash-boundary regressions |
| Detailed fresh-process saved-board gate | passed 37 native tests and private checkpoint migration; zero added engine DRC identities |
| Owned IPC deadlines | absolute send/header/body deadline, late-dispatch budget refresh, bounded startup/connect/handshake, and owner-only child reaping covered |
| Verifier process-tree timeout | isolated verifier + hanging engine grandchild both reaped; unrelated sentinel survives |
| Resume provenance and pointer reconciliation | passed direct API, missing artifact, legacy migration, and strict native resume tests |
| Planner reservation and terminal accounting | passed malformed-response then timeout/503 and deadline-clamping tests |
| Endpoint identity and fair retry scheduling | passed native layer tests and tiny-distinct-gap regression |
| Pair-local unsupported endpoints | verified track-only unknown endpoint is retired while another net is attempted; definite net mismatch remains a global safety stop |
| Planner quarantine exit | failed tool response after quarantine stops before another planner request or native query |
| Accepted-board progress consistency | final DRC rejection and artifact-verifier rejection persist tracker/report/pointer progress consistently; resumed rejection preserves the prior accepted count; resume accepts a later genuine improvement |
| Public privacy / historical claims | phase-3 coordinates removed and promotion claims corrected |
| Strict native harness | passed: exit 0, 260 unit + 37 native, zero skips |
| Historical pilot copy migration/reverification | passed in a private copy; zero new attempts; preserved source reference unchanged |
| Independent candidate DRC identity comparison | PCBWorld engine: zero added relevant identities; installed KiCad CLI: 23 added / 27 resolved |
| KiCad CLI identity diagnosis | CLI reports positions as `{x, y}` millimetre objects; 2,367/2,428 shared identities retained position at 0.001 mm, 61 moved; added/resolved identities have zero exact normalized position-signature matches |
| Installed KiCad verification | Report identifies KiCad 10.0.6; 23 added clearance errors and 27 resolutions (23 clearance errors, four track-dangling warnings); geometry vs serialization remains unresolved and blocks board-level acceptance |

## Resume

```bash
cd /Users/leo/Documents/PCBWorld-reliability
bash tools/reliability/check_phase.sh --strict
.venv/bin/python -m pytest -o addopts='' tests/agent -q
```

Keep the exact private pilot reference and the original EasyEDA project
untouched. The recovery copy contains the immutable generation, CLI JSON
reports, and private `cli_diagnosis.json` with exact source/candidate commands,
file hashes, sidecar identities, reported KiCad version, engine build hash, and
aggregate normalized-position comparison. The effective `.kicad_pro` and
`.kicad_dru` files are byte-identical between source and candidate. CLI item
positions changed for 61 shared identities; the 50 added/resolved identities
have no matching normalized location signature. UUID churn alone does not
account for the mismatch, but the CLI report cannot prove whether the remaining
differences come from changed geometry or serialization semantics. Keep
board-level acceptance blocked until that distinction is verified. Do not start
zone refill or broader V3 routing before then.
