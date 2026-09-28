# Phase 28 — repair shorting reporter, then resume restoration routing

Status: Astra plan, pending implementation and review. The accepted V3 board is unchanged. The phase-26 refilled candidate is experimental and cannot be promoted while its original-relative connectivity and CLI gates fail.

## Decision from phase 27

The native reporter can emit one or four rows for the same shorting item pair on unchanged board bytes. Independent measurement found an invariant set of 30 pairs across 193 passes on three retained boards and the pinned CLI reports. The pair set is a useful comparison only when both boards prove that each UUID names one unchanged physical item. The original import contains duplicated UUIDs, so a bare-UUID gate would conceal real substitutions there.

T27A's multi-instance verifier remains fail-closed until the reporter is repaired and independently validated. Its diagnostic single-instance mode must never return a retention verdict. T27B's source-level diagnosis is evidence for a reporter defect, not permission to relax DRC acceptance.

## T28A — DeepSeek implementation: source-level cache repair

Fix the inconsistent pointer-pair canonicalization in both the RL copper-clearance provider and the standard provider loaded by the pinned CLI. In each visitor, use the same canonical pointer ordering as its R-tree filter before looking up and updating `checkedPairs`. Preserve mutex scope, layer tracking, `has_error`, cancellation and collision tests. Persist the standard-provider change in the source overlay or patch-copy mechanism, add a sequential engine patch, and rebuild both the RL module and CLI provider. Freeze pre-repair binaries and source hashes first. Do not add a pair-set comparator, class waiver or rule change.

Deterministic tests must exercise the actual cache-key path in both pointer orderings, with same-logical-pad mixed-net multilayer pads, ordinary different-net pads, genuine shorts and a clean case. Differential evidence must use all three frozen phase-27 boards, twelve fresh native processes and six fresh CLI invocations per board, with process and binary provenance. Require the same 30 shorting item pairs, stable multiplicity, no lost genuine finding and no unexplained movement in other relevant classes. Terminal partitions and board/rule hashes must remain unchanged. Rebaseline both sides of comparisons under the repaired engine generation; never compare a pre-repair provider against a post-repair candidate. Replay the six retained phase-26 steps through corrected T27A with complete CLI and terminal gates, then run the strict integrated harness. Keep all private geometry and item identifiers in the private workspace.

## T28B — independent DeepSeek verification, then Astra acceptance

Independently inspect patch reproducibility, both loaded providers, deterministic defect coverage, and raw differential evidence. Review the actual patch and all new files, check rules/engine/board hashes and no public-data leak, and test that genuine shorting and non-shorting DRC behavior remains intact. Astra accepts or returns one consolidated correction packet. A passing test run alone does not authorize board promotion.

## T29 — bounded restoration campaign after T28 acceptance

After T27A and T28A acceptance, start from a disposable copy of the phase-26 experimental candidate. Offer the previously unvisited two-component link first, then new anchor/layer/strategy alternatives across the five residual fragmented clusters. Cap the campaign at 12 unique anchor pairs and five native transactions per selected link. Recompute the component structure after every retained join; checkpoint and prove complete terminal-partition rollback after each refusal. Save, legally refill, reopen, and run the bound native, terminal and complete CLI gates on every candidate. Quarantine unknown engine or rollback state. Preserve the accepted pointer unless the candidate passes the original-relative gates as well as all step gates.

Retain the full private attempt records and hashes. Publish aggregate results only. After independent DeepSeek verification, Astra reviews the final saved bytes, the terminal relations, complete CLI results, reporter stability, rollback evidence, frozen input hashes, and whether any candidate can legitimately replace the accepted generation. If restoration succeeds, plan the next campaign against the accepted board's remaining unrouted edges.
