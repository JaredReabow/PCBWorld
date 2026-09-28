# Phase 18 — endpoint and anchor census

Status: assigned for read-only analysis. Phase 17 is the accepted predecessor.

## Objective

Classify all 49 exact `connection_not_verified` edges on the unchanged accepted
Helix V3 board. The phase must distinguish anchor identity from route geometry
and DRC refusal before choosing another harness change or routing trial.

## Contract

1. Join the phase-11 taxonomy to the current accepted board by exact offered
   edge identity, not by net name alone. Account for all 49 once; report missing
   or ambiguous matches explicitly.
2. For each endpoint, establish whether same-net copper is present at the
   offered anchor; identify usable alternate same-net anchors only with evidence
   from the board/native engine. Keep unknowns explicit.
3. Classify each edge as anchor identity/availability, geometric obstruction,
   both, or unresolved, with evidence and confidence. Do not infer all 49 from
   phase 17's seven reached edges or reconstruct historical failure steps as
   if they were recorded.
4. Save per-edge nets, coordinates, geometry, and rule values only in the private
   evidence tree. The public result may contain aggregate counts, methods, and
   limitations only.
5. Read-only: no copper/footprint/zone/rule changes, no routing campaign, no
   accepted-pointer update, no engine/wire modification, no planner calls, no
   staging, commit or push. Check the accepted board digest before and after.

## Deliverables and validation

DeepSeek owns private phase-18 evidence and a reproducible read-only analysis
script in the private tree; public `phase18/RESULT.md`, and HISTORY/CHANGELOG
entries with aggregate findings. Add focused tests only if public harness code
is changed. Check reproducibility, classification total and board hash. Report a
single, concrete next intervention if the evidence supports one; otherwise name
the physical-design decision needed. Astra reviews the evidence and decides.
