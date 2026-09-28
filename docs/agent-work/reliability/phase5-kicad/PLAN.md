# PLAN — phase 5: installed-KiCad cross-check and recovery

Phase 4 left board-level acceptance blocked on a real disagreement between the
pinned PCBWorld engine (zero added relevant DRC identities on the V3 candidate)
and the installed KiCad CLI (23 added clearance errors). Historical measurements
explained the installed CLI cap, but later review found integrity defects in the
terminal and CLI gates. Candidate acceptance is withdrawn until all evidence is
reproduced with the corrected gates and current native build.

## The two questions

1. **Is the candidate physically regressed?** Separate a save-only effect from a
   routing effect from a reporting effect, using identical inputs and the actual
   installed tools.
2. **Is the CLI gate itself sound?** Only after (1) may a CLI verdict be used for
   acceptance, and only if the report it judges is complete.

## What was measured

* A **zero-routing load/save roundtrip** of the frozen baseline through the pinned
  engine: 6073 segments, 209 vias, 321 footprint blocks and 177 zones all
  unchanged, fills byte-identical by digest — the save is physically transparent.
* The **installed CLI is capped**: `DRC_ENGINE` limits findings per error code
  (`ERROR_LIMIT = 199`, `EXTENDED_ERROR_LIMIT = 499` for clearance/unconnected).
  The installed 10.0.6 CLI reports exactly 499 `clearance` and exactly 199 in five
  other classes, every run. Comparing capped reports by identity compares which
  findings made the cut; two runs of one unchanged file differed by ~18
  identities. The phase-4 "23 added" number was that artefact.
* A **complete reporter** was built from the pinned source (same tree as the
  engine; the report caps are already removed there by the existing PCBWorld-mod;
  the CLI links the **stock** copper-clearance provider while the router module
  links the RL fork, so it is an independent DRC implementation). It reports 9758
  findings on the source board, repeats that report identity-for-identity, and
  reproduces the source exactly for the load/save copy.
* Against that complete reporter the six-closure candidate shows **0 added
  identities** in every class and 4 resolved `track_dangling` warnings, with the
  only physical change being 20 new track segments.

## Contracts

* **No count-only acceptance.** Equal totals can hide a new violation replacing a
  resolved one; a class judged by count is not judged.
* **Completeness before identity.** A report with any class at a known per-class
  cap is refused as possibly truncated, with the reason recorded. Reporting
  completeness is a tool property, not a rule change: no clearance rule, severity
  or class is ignored or relaxed anywhere.
* **Reproducibility before identity.** Fewer than two runs per board, or a class
  whose identity set varies between runs, yields `unverified` — never a pass.
* **Exact identities.** Promotion requires that no finding identity in any class
  is new, that no class count rises, and that unconnected items do not rise.
* **Task-isolated tooling.** The complete CLI is a build-tree artifact configured
  explicitly (`cli_path` + `env`); the user's installed application, global
  settings and design rules are untouched, and the pinned engine build is not
  rebuilt or modified.
* Native-only and dual-gate acceptance are distinguishable in the manifest
  (`not_configured` / `unavailable` / `unverified` / `verified`).

## Out of scope for this phase

Zone refill and further routing: phase C of the operator's contract stays
authorised but starts only once this boundary is resolved, which it now is.
