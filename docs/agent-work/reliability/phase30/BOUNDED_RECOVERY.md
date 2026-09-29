# Phase 30 (T30R2) - the bounded recovery campaign and its independent verification

Status: **bounded trial complete; cap conserved; two joins retained; not
promotable; record not resumable; no promotion.** One bounded recovery campaign
continued the frozen phase-30 restoration lineage on disposable copies, under
the accepted driver and limiter unchanged. It spent **55 of the approved 60**
native transactions across **12 links**, never more than **five on any stable
link**, and retained **two joins**, taking the remaining restoration joins from
**twelve to nine**. The final candidate fails the accepted-generation and
canonical-original promotion gates, and the campaign record cannot serve as a
resume base, so nothing was promoted and the accepted pointer did not move.

Owner: task T31D, requirements R1/R2/R3; the campaign was T30R2 and the
independent verification T30R2V, and Astra owns acceptance. This page carries
aggregates only - no board geometry, net names, component references,
coordinates or board hashes. Private evidence and the full transaction record
stay in the board workspace.

## 1. What T30R2 was

Phase 30's first campaign (T30R) continued the phase-26 restoration lineage on
the frozen experimental candidate, but it offered the declared five-attempt
ladder once per anchor pair instead of once per link, spent 575 native
transactions and was refused; its public account is
[the phase 30 restoration page](RESTORATION.md). T30R2 re-ran the same recovery
idea under a limiter that accepts at most five charged native calls per stable
logical link, twelve links and sixty transactions across all resumes, charging
each call before it is made and persisting the charge before the call.

Everything else is inherited from the refused lineage and unchanged: the
declared ordering, the declared five-rung ladder, the six immediate-parent
retention conditions, the decision taken on the saved and reopened bytes rather
than on an in-session snapshot, and the rollback proof that re-captures the
terminal partition and requires it to come back as a canonical set of terminal
groups.

## 2. The bounded run, and the interruption

| quantity | approved | actual |
|---|---|---|
| links | 12 max | 12 |
| native transactions, cumulative | 60 max | 55 |
| native transactions per stable link | 5 max | max 5, every link at or below |
| distinct anchor pairs per link | 12 max | 1 per link |
| this run's own record charges | - | 48 |
| charges from the killed launch, with no record | - | 7 |

A first launch was killed when its shell session ended. It had already charged
seven native transactions and had written no campaign record: five on the first
unvisited link (its full five-rung declared ladder, every record entered and
none committed) and two on the second (one recorded attempt plus one charge that
was reserved and persisted but never settled, with no record at all). Replacing
the ledger would have been the reset the rule forbids, so the campaign was
continued from the **same ledger** and the cumulative figure stayed honest: the
record reports its own 48 charges and the ledger's cumulative 55 together. The
continuation re-staged from the byte-unchanged frozen start and spent zero
charges on its first link before moving down the ordering.

## 3. Rollback coverage: what is proved, and what is not

**Clarification of an earlier blanket claim.** The campaign's own report
described every rejected transaction as carrying a proven rollback and reported
no quarantine. Read as a blanket statement over the whole campaign, that is not
what the frozen evidence supports, and the correction is recorded here.

Proved: all **46 rejections in the resumed run** carry a rollback receipt -
`restored: true`, `matches_baseline: true`, a geometry digest equal to the
starting copper digest, and a complete terminal proof with cluster-membership
and net-membership equality - and both retained joins pass every saved-bytes
check.

Not proved the same way: the **seven charges from the killed launch** - six
settled ledger records plus one reserved-but-unsettled charge with no record -
carry no per-attempt receipt, because the killed process could not write one.
Their coverage is **restart isolation only**: the frozen start board is
byte-unchanged, the resumed run re-staged from it and named it as the parent of
both its first steps, its first link spent zero charges, and no candidate
directory exists for the killed launch's links. What that proves is the frozen
start and the restart isolation above, and nothing about those seven charges'
per-attempt rollback: no receipt exists, so whether the killed attempts changed
copper is **unknown** rather than disproved. The campaign's criterion that every
rejection carry a proven rollback is therefore **unmet**, and the author
campaign stands at REQUEST_CHANGES; the independent acceptance covers the
diagnosis and accounting, not the campaign.

## 4. The retained joins and the restoration debt

Two joins were retained. Each was saved, legally refilled in its own process,
reopened from disk, checked by the bounded verifier and gated by the complete
pinned immediate-parent CLI gate bound to the same candidate bytes. Both gates
return `ok` with complete staged-input matching on a fresh process, and both bind
the same candidate hash the verifier read. Each gate records one reason - the CLI's
unconnected endpoint pairing churned - which is accepted only because the bound
native terminal-partition proof shows every original connection preserved; that
is the accepted path in this verifier.

Restoration debt falls from twelve open joins to eleven and then to nine. The
candidate chain is unbroken from the frozen start: the first retained join's
parent is the frozen start board, and the second's parent is the first's refilled
candidate.

## 5. Independent verification (accepted)

T30R2V verified the frozen packet read-only and reached a narrow verdict:

* **Accounting - pass.** 55 cumulative charges recounted across 12 links, at
  most five on any link, one anchor pair per link, 55 of the approved 60. The
  seven killed-launch charges are reconstructed from the frozen bytes - six
  ledger records plus the one unsettled reservation - matching the carried total
  exactly, and 46 of 46 resumed rejections re-checked with valid receipts.
* **Fresh gates - pass, no promotion.** Six boards, three fresh engine processes
  each, five relations and five complete pinned CLI gates (the gate run took
  432.4 s).
* **Integrity - pass with a finding.** Every one of the 108 frozen campaign
  artefacts and 24 protected inputs re-hashes equal, the verifier's own 96-entry
  packet manifest reproduces in full, all 20 raw CLI reports are present and
  hash-equal, and the accepted pointer is unchanged. The finding is the resume
  defect in section 6.

## 6. Promotion, and the record that does not resume

**Promotion: not eligible.** Against the accepted generation and against the
canonical original the final candidate adds **19** relevant native identities -
all in the single isolated-copper class - and the terminal comparator reports
**18** split relations. Both complete CLI gates are refused: their terminal
proofs are complete and report the same 18 split relations, and the
unconnected-item count moves 135 to 144 on the accepted-relative gate. Against
the phase-25 refilled baseline the candidate is clean (0 added, 0 splits, gate
verified).

The deviation is inherited, not created here. The frozen T30R start board
already carries the same 19 added identities and 21 split relations against both
references, and the final candidate is **clean against that start** (0 added, 0
splits, twelve joins to nine). T30R2 introduced none of it, and none of it is
promotable.

**The record does not resume.** The frozen campaign record fails the accepted
limiter's own `validate_campaign_record` and `reconcile_resume_charges`: it
declares **55** cumulative transactions while its link **history accounts for
48**. The seven charges the ledger holds are never attributed in the record,
because the continuation had no `--resume` record to load - the killed launch
wrote none - so the driver took its fresh-run path and treated the pre-charged,
unbound ledger as if it were fresh. The cumulative counter still held in fact,
which is why the 60-charge ceiling was respected, but the record cannot
reproduce it. Read-only evidence cannot repair this: it would mean rewriting a
frozen record or its ledger, and a record the killed launch never wrote cannot be
created retroactively.

**Disposition.** The campaign is **quarantined at 55/60** with no promotion: do
not resume from the frozen record, and do not reset or re-bind the ledger. The
phase-31 successor work is the guard, not a new campaign - a fail-closed refusal
for a pre-charged, unbound ledger before any write or engine open, plus a
validation that the full cumulative and per-link history is complete before a
record is published or bound. Durable journaling is a design-only proposal
alongside that guard. Any further routing needs a separately Astra-reviewed
bounded plan, under the user's existing authorisation; this page neither grants
nor requests one.

## 7. Two related phase-30 clarifications (attribution)

Both are attributed to the accepted phase-30 attribution study (T30N) and its
independent addendum (T30NV); neither is a re-measurement here.

* **Thirteen unbound-declaration inputs.** The attribution study registered
  thirteen inputs that carry no `declared_by` entry in any declaration its
  verifier traverses: eight raw measurement work files, three declaration
  documents and two frozen summaries. Only the eight raw work files are raw
  measurement gaps; the other five are documents or summaries that appear in the
  registry without a declaring entry. The corrected text in the study's report
  section 7 and in its notes both say exactly that, and the addendum's own
  reproduce snippet prints the same three-way split.
* **The revision-2 archive pointer.** The live freeze's machine-readable
  `supersedes` list names only the first archived revision, so a reader that
  walks that field jumps from the current revision to the first and skips the
  revision that was actually reviewed. Every byte of the reviewed revision is
  preserved in the packet's own archive with its own supersession record, and no
  derivation, input, board, rule, pointer or count moved, so the gap is in the
  chain of custody rather than in the measurement. Closing it is
  documentation-only: name both archived revisions in the live freeze's
  supersedes block.

## 8. Evidence

The private board workspace holds the campaign packet - the input binding, the
freeze, the audit, the mutation results, the ledger, the retained candidate
boards, the refill and reopen records and the raw CLI reports. The independent
verifier's own frozen packet holds the recount, the fresh gate measurements, the
attribution comparison and the verdict. This page carries the aggregates; the
private record carries the exact commands, hashes, coordinates and net
identifiers.
