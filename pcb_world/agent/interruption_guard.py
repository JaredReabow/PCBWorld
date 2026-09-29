#!/usr/bin/env python3
"""The fail-closed guard for an interrupted routing campaign.

The :mod:`pcb_world.agent.ledger` limiter bounds the *count* of native
transactions. It does not decide whether a ledger it is handed is a legitimate
continuation of a campaign. That gap is what this guard closes. The shape it
exists for:

    a launch was killed after it had charged native transactions but before it
    wrote a campaign record. The continuation therefore had no record to
    continue, took its *fresh* path, loaded the pre-charged, unbound ledger as
    if it were fresh, and produced a record whose own link history accounts for
    fewer charges than its ``ledger_totals`` declare. That record can never be
    resumed: the limiter's own validator refuses it, but only after it exists.

This module is the guard. It is deliberately narrow, and it is *additive* to the
limiter: it imports it and never re-implements its accounting. Three refusals:

1. ``preflight_ledger`` - a *fresh* invocation whose ledger already carries
   charges, links or anchor pairs, and which is not bound to a campaign record,
   is refused **before any write and before any engine is opened**. Evidence
   from a run that never published a record is classified ``interrupted`` and
   quarantined rather than adopted: there is no record to reconcile it against,
   so it cannot lawfully be continued. A bound ledger handed to a fresh
   invocation is refused as well, in the same pre-write position.

2. ``completed_record_gate`` - before a completed record is *published* or the
   ledger is *bound* to it, the guard re-validates the whole cumulative and
   per-link history against the ledger, using the limiter's own
   ``validate_campaign_record`` and ``reconcile_resume_charges``. A record whose
   history does not account for every ledger charge fails closed: nothing is
   written and nothing is bound.

3. ``classify_ledger`` - the single vocabulary both refusals and the record
   carry: ``fresh``, ``active``, ``interrupted`` (incomplete), ``quarantined``.
   Interrupted evidence is never silently complete.

Why the classification reconciles the ledger and not only the record. An earlier
revision classified a *bound* ledger as ``active`` and ``complete`` whenever the
bound record validated on its own, without reconciling the record against the
ledger's charges. A ledger that carried a charge the record could not account
for - a valid one-charge record whose ledger also holds a reserved-but-unsettled
charge - therefore read as ``active/complete: true`` while the caller's own
later check refused it. The classification here reconciles both:

* reconciles the bound record against the ledger **cumulatively and per
  canonical link**, through the accepted limiter's own ``recorded_charges`` and
  ``reconcile_resume_charges``. Any unaccounted, mismatched or parked charge
  makes the ledger ``quarantined`` and explicitly incomplete, with the reason;
* never reports ``complete`` or a checked ``resume_safe`` without a supplied,
  validated and reconciled record. A bound ledger with no record is
  ``active``/``complete: false`` with ``pending_checks`` naming what is missing,
  and ``preflight_ledger(resuming=True)`` refuses outright unless the record is
  supplied;
* reports an "unaccounted charges" attribution - held, recorded, unaccounted,
  and how many of the unaccounted charges carry a settled receipt - so an
  interrupted continuation can be described exactly instead of only refused.
  The invariant this rests on: every charge a *published* record
  accounts for was settled before that record was written, so any settled
  receipt beyond the recorded count is an unaccounted charge, and every charge
  with no receipt at all is unsettled.

The low-level helpers here (``classify_ledger``, ``preflight_ledger``,
``charge_attribution``, ``completed_record_gate``) are pure: they open no board,
touch no engine, write no file and route nothing. They read the ledger object
they are handed and return a verdict. A ``resume_safe`` or ``complete`` flag from
them is a statement about *accounting only* - it says the amounts add up. It is
not an identity check and not a permission to route; that is what the
file-facing :func:`interruption_guard_preflight_campaign` is for, because only it
reads and hashes the record and parent bytes an identity claim is made from.

This module records only counts, statuses and digests; it names no board
geometry, net, component reference or coordinate beyond what a synthetic
placeholder ledger literally contains.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pcb_world.agent.ledger import (
    LinkLedger,
    LinkLedgerError,
    reconcile_resume_charges,
    recorded_charges,
    validate_campaign_record,
)

#: The guard's own schema tag, written into every record this campaign produces
#: so a reader can tell a guarded record from an unguarded one. ``/2`` marks the
#: classification that reconciles the ledger as well as the record; a ``/1``
#: consumer must not read a ``/2`` verdict as the earlier, weaker one. The tag is
#: carried unchanged from the accepted private guard so persisted evidence keeps
#: its meaning.
GUARD_SCHEMA = "t31f-interruption-guard/2"

#: The four classifications. ``interrupted`` is the incomplete/quarantined
#: state: evidence exists, but no campaign record accounts for it.
STATUS_FRESH = "fresh"
STATUS_ACTIVE = "active"
STATUS_INTERRUPTED = "interrupted"
STATUS_QUARANTINED = "quarantined"


class InterruptedCampaignRefusal(LinkLedgerError):
    """A ledger state that may not be adopted by this invocation."""


class CompletedRecordRefusal(LinkLedgerError):
    """A completed record whose history does not reconcile with the ledger."""


def charge_attribution(record: dict, ledger: LinkLedger) -> dict:
    """How the ledger's charges divide against what the record accounts for.

    Reported, never decisive: the *decision* is the accepted limiter's
    ``reconcile_resume_charges``. This is the arithmetic that lets a refusal say
    "seven charges the record does not account for, six of them settled" rather
    than only "refused".

    Per canonical link: ``held`` is the ledger's counter, ``recorded`` is the
    record's flat history for that link, and ``unaccounted`` is the difference
    when the ledger holds more. Unsettled charges are the charges with no
    receipt at all (``held - receipts``); because a published record only ever
    accounts for charges its own run settled before writing, every accounted
    charge has a receipt, so the settled remainder beyond the recorded count is
    unaccounted too. ``consistent`` records whether that decomposition adds up;
    a false value is a malformed ledger and is treated as a refusal, not
    smoothed over.
    """
    expected = recorded_charges(record, ledger.aliases)
    actual = {identity: int(budget.attempted)
              for identity, budget in ledger.budgets.items()}
    held = recorded = unaccounted = settled = unsettled = 0
    consistent = True
    per_link: list[dict] = []
    for identity in sorted(set(expected) | set(actual)):
        charges = actual.get(identity, 0)
        accounted = expected.get(identity, 0)
        budget = ledger.budgets.get(identity)
        receipts = len(budget.attempts) if budget is not None else 0
        extra = max(0, charges - accounted)
        unsettled_here = max(0, charges - receipts)
        settled_here = max(0, receipts - accounted)
        if budget is not None and settled_here + unsettled_here != extra:
            consistent = False
        held += charges
        recorded += accounted
        unaccounted += extra
        settled += settled_here
        unsettled += unsettled_here
        if extra or charges != accounted:
            per_link.append({
                # A short digest, not the identity: the verdict stays a
                # counts-and-digests record, even for a divergent link.
                "identity_digest":
                    hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16],
                "held": charges,
                "recorded": accounted,
                "receipts": receipts,
                "unaccounted": extra,
                "settled_unaccounted": settled_here,
                "unsettled_unaccounted": unsettled_here,
            })
    return {
        "held": held,
        "recorded": recorded,
        "unaccounted": unaccounted,
        "settled_unaccounted": settled,
        "unsettled_unaccounted": unsettled,
        "consistent": consistent and unaccounted == settled + unsettled,
        "links": {"recorded": len(expected), "held": len(actual)},
        "divergent_links": per_link,
    }


def classify_ledger(ledger: LinkLedger, *, record: dict | None = None) -> dict:
    """Classify one loaded ledger against the campaign record it names.

    ``fresh``       - nothing charged, nothing offered, no binding: a lawful
                      start.
    ``active``      - bound to a campaign record that validates *and*
                      reconciles with every ledger charge. Complete only then;
                      a bound ledger with no record supplied is ``active`` but
                      explicitly ``complete: false`` with ``pending_checks``.
    ``interrupted`` - carries charges, links or anchor-pair offers but no
                      campaign binding: the incomplete evidence of a run that
                      never published a record. It is quarantined, not adopted.
    ``quarantined`` - bound, but the bound record does not validate, or the
                      ledger holds charges that record cannot account for - per
                      link as well as in total.

    The reconciliation is the accepted limiter's own: ``validate_campaign_record``
    for the record, then ``reconcile_resume_charges`` for the record against the
    ledger, plus ``charge_attribution`` for the readable split. Missing evidence
    never reads as complete.
    """
    charges = int(ledger.total_attempted())
    links = len(ledger.budgets)
    pairs = len(ledger.pair_links)
    binding = ledger.campaign_binding()
    bound_record = binding.get("campaign_record")
    chain = [str(item) for item in (binding.get("campaign_records") or [])]
    reasons: list[str] = []
    pending: list[str] = []
    attribution: dict | None = None
    if bound_record:
        status = STATUS_ACTIVE
        validated = False
        if record is None:
            pending.append("a campaign record supplied and reconciled with "
                           "every ledger charge")
            reasons.append(
                "the ledger is bound to a campaign record, but no record was "
                "supplied, so the ledger cannot be shown to be complete; "
                "checks pending")
        else:
            try:
                validate_campaign_record(record)
            except LinkLedgerError as exc:
                status = STATUS_QUARANTINED
                reasons.append(f"the bound record does not validate: {exc}")
            else:
                try:
                    attribution = charge_attribution(record, ledger)
                except LinkLedgerError as exc:
                    status = STATUS_QUARANTINED
                    reasons.append(
                        f"the ledger's charges cannot be attributed to the "
                        f"bound record: {exc}")
                else:
                    if attribution["consistent"] is False:
                        status = STATUS_QUARANTINED
                        reasons.append(
                            "the ledger's charge attribution does not add up "
                            "with the bound record")
                    else:
                        try:
                            reconcile_resume_charges(record, ledger)
                        except LinkLedgerError as exc:
                            status = STATUS_QUARANTINED
                            reasons.append(
                                f"the ledger holds charges the bound record "
                                f"does not account for: {exc}")
                        else:
                            validated = True
                            reasons.append(
                                "the bound record reconciles with every ledger "
                                "charge, cumulative and per link")
    elif charges or links or pairs:
        status = STATUS_INTERRUPTED
        validated = False
        reasons.append(
            f"the ledger carries {charges} charges on {links} links and "
            f"{pairs} anchor-pair offers but is bound to no campaign record; "
            f"this is incomplete evidence of an interrupted run")
    else:
        status = STATUS_FRESH
        validated = True
    complete = (status == STATUS_FRESH
                or (status == STATUS_ACTIVE and validated))
    return {
        "schema": GUARD_SCHEMA,
        "status": status,
        "charges": charges,
        "links": links,
        "terminal_pairs": pairs,
        "bound_record": bound_record,
        "binding_chain_length": len(chain),
        "validated": validated,
        "complete": complete,
        "pending_checks": pending,
        "attribution": attribution,
        "reasons": reasons,
    }


def preflight_ledger(ledger: LinkLedger, *, resuming: bool,
                     record: dict | None = None, ledger_path=None) -> dict:
    """Refuse an unlawful ledger before any write or engine is opened.

    Read-only by construction: the function inspects the ledger object it is
    handed and raises. It never flushes, never creates a directory and never
    touches an engine, so a caller can run it as the first act of ``main()``.

    A resume must supply the record it is continuing: without it there is
    nothing to reconcile the ledger against, and this function refuses rather
    than returning a ``resume_safe`` verdict it has no evidence for.
    """
    where = f"{ledger_path}: " if ledger_path else ""
    if resuming:
        if record is None:
            raise InterruptedCampaignRefusal(
                f"{where}a resume must supply the record it is continuing, so "
                f"the ledger can be reconciled against it; refusing before "
                f"any write or engine open")
        classification = classify_ledger(ledger, record=record)
        if classification["status"] != STATUS_ACTIVE:
            raise InterruptedCampaignRefusal(
                f"{where}the ledger is {classification['status']}, so it is "
                f"not a checked continuation of the record supplied: "
                f"{classification['reasons'][0] if classification['reasons'] else ''}"
                f"; refusing before any write or engine open")
        if not classification["complete"]:
            raise InterruptedCampaignRefusal(
                f"{where}the ledger could not be shown complete against the "
                f"record supplied; refusing before any write or engine open")
        classification["resume_safe"] = True
        return classification
    classification = classify_ledger(ledger)
    if ledger.campaign is not None:
        raise InterruptedCampaignRefusal(
            f"{where}the ledger is bound to campaign "
            f"{ledger.campaign.get('campaign_record')}; use --resume to "
            f"continue it; refusing before any write or engine open")
    if classification["status"] != STATUS_FRESH:
        raise InterruptedCampaignRefusal(
            f"{where}the ledger is {classification['status']}: "
            f"{classification['reasons'][0] if classification['reasons'] else ''}"
            f"; a fresh campaign may not adopt it, so it is quarantined; "
            f"refusing before any write or engine open")
    classification["resume_safe"] = False
    return classification


def completed_record_gate(record: dict, ledger: LinkLedger) -> dict:
    """Validate a finished record's cumulative and per-link history.

    Runs the accepted limiter's own two checks - ``validate_campaign_record``
    (the record's own totals against its own link records) and
    ``reconcile_resume_charges`` (every ledger charge, per canonical link,
    against the record's whole flat history) - and refuses on either. The
    campaign calls this *before* it writes the record and *before* it binds the
    ledger, so a record that could never be resumed is never published and its
    charges are never bound.
    """
    if not isinstance(record, dict) or record.get("ran") is not True:
        raise CompletedRecordRefusal(
            "refusing to publish or bind a record that is not a completed run")
    try:
        summary = validate_campaign_record(record)
    except LinkLedgerError as exc:
        raise CompletedRecordRefusal(
            f"refusing to publish or bind a completed record: {exc}") from exc
    try:
        reconciliation = reconcile_resume_charges(record, ledger)
    except LinkLedgerError as exc:
        raise CompletedRecordRefusal(
            f"refusing to publish or bind a completed record: {exc}") from exc
    per_link = {
        "recorded": len(reconciliation["expected"]),
        "charged": len(reconciliation["actual"]),
        "cumulative": int(reconciliation["cumulative"]),
    }
    return {
        "schema": GUARD_SCHEMA,
        "status": "reconciled",
        "run_total": int(summary["run_total"]),
        "cumulative": int(summary["cumulative"]),
        "links_attempted": int(summary["links_attempted"]),
        "link_records": int(summary["link_records"]),
        "per_link": per_link,
        "attribution": charge_attribution(record, ledger),
    }


def quarantine_note(classification: dict, *, boundary: str, error=None) -> dict:
    """A small, explicit quarantine note for an interrupted evidence packet."""
    note = {
        "schema": GUARD_SCHEMA,
        "status": "quarantined",
        "boundary": str(boundary),
        "complete": False,
        "classification": classification,
    }
    if error is not None:
        note["error"] = str(error)
    return note


#: The digest name every identity in a verdict is stated in. The guard hashes
#: exact bytes; a caller that re-serialises a record before hashing it will not
#: reproduce this value, which is the point.
DIGEST_NAME = "sha256"


class CampaignPreflightRefusal(InterruptedCampaignRefusal):
    """A file-facing preflight refused a campaign before any write or engine."""


def sha256_bytes(payload: bytes) -> str:
    """The digest of exact bytes, in the form every verdict states an identity."""
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path) -> str:
    """Hash a file's bytes in one streaming pass, without holding it in memory."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_record(path: Path) -> tuple[dict, str]:
    """Read one campaign record and its digest, or refuse the file by name."""
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise CampaignPreflightRefusal(
            f"{path}: the campaign record could not be read: {exc}") from exc
    digest = sha256_bytes(payload)
    try:
        record = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise CampaignPreflightRefusal(
            f"{path}: the campaign record is not readable JSON: {exc}") from exc
    if not isinstance(record, dict):
        raise CampaignPreflightRefusal(
            f"{path}: the campaign record is not an object")
    return record, digest


def resolve_parent_board(parent_dir, *, parent_board_path=None) -> Path:
    """The one parent board that ``final_parent_dir`` names.

    A record pins its parent by a directory and a digest, not by a filename, so
    the board inside that directory has to be unambiguous: with no explicit
    ``parent_board_path`` the directory must hold exactly one top-level
    ``*.kicad_pcb`` file. An explicit path is honoured only when it is inside the
    directory the record names, so a caller cannot redirect the identity check
    at an unrelated file.
    """
    directory = Path(parent_dir)
    if parent_board_path is not None:
        board = Path(parent_board_path)
        if not board.is_file():
            raise CampaignPreflightRefusal(
                f"{board}: the parent board named for this campaign does not "
                f"exist")
        if not board.resolve().is_relative_to(directory.resolve()):
            raise CampaignPreflightRefusal(
                f"{board}: the parent board named for this campaign is not "
                f"inside the parent directory {directory} the record declares")
        return board
    if not directory.is_dir():
        raise CampaignPreflightRefusal(
            f"{directory}: the parent directory this record declares does not "
            f"exist")
    candidates = sorted(item for item in directory.glob("*.kicad_pcb")
                        if item.is_file())
    if len(candidates) != 1:
        raise CampaignPreflightRefusal(
            f"{directory}: the parent directory holds {len(candidates)} "
            f"top-level *.kicad_pcb files, so the parent board this record "
            f"declares is ambiguous")
    return candidates[0]


def _verify_record_identity(record: dict, ledger_path, *,
                            parent_board_path=None) -> dict:
    """Check the file identity a record claims against the bytes on disk.

    Two claims, both checked here and shared by *both* callers, so the composed
    preflight and the guarded publication can never disagree about whether a
    record's identity is real:

    * the record's ``ledger_file`` must name the ledger this run drives. A record
      that names a substitute ledger is refused even when its accounting is
      perfect, because the next resume would refuse it;
    * the parent board inside ``final_parent_dir`` must hash to the
      ``final_parent_board_sha256`` the record declares. Missing, ambiguous or
      unreadable parent bytes refuse.

    Read-only: it compares the named ledger's path and reads the parent board's
    bytes, and it creates nothing. Returns the identity it verified.
    """
    named = Path(str(record["ledger_file"]))
    if named.resolve() != Path(ledger_path).resolve():
        raise CampaignPreflightRefusal(
            f"{named}: the record names this ledger, not the "
            f"{Path(ledger_path)} this run drives; refusing a record whose "
            f"identity is not the identity of the ledger it is used against")
    parent_dir = Path(str(record["final_parent_dir"]))
    parent_board = resolve_parent_board(
        parent_dir, parent_board_path=parent_board_path)
    try:
        actual = sha256_file(parent_board)
    except OSError as exc:
        raise CampaignPreflightRefusal(
            f"{parent_board}: the parent board could not be read: {exc}") from exc
    declared = str(record["final_parent_board_sha256"])
    if actual != declared:
        raise CampaignPreflightRefusal(
            f"{parent_board}: the parent board hashes {actual}, but the record "
            f"declares {declared}; refusing a record whose parent bytes are not "
            f"the bytes it names")
    return {
        "ledger_file": str(named),
        "parent_board": str(parent_board),
        "parent_board_sha256": actual,
    }


def _binding_chain(binding: dict) -> tuple:
    """One binding's current record and its chain, in the stored order."""
    current = binding.get("campaign_record")
    chain = [str(item) for item in (binding.get("campaign_records") or [])]
    return current, chain


def _is_interrupted_finalization(current, chain, declared_predecessor,
                                 successor) -> bool:
    """The one other lawful continuation: a written-but-unbound successor.

    ``successor`` is the digest of the record that was written and whose binding
    was never advanced - the record being *resumed from* in the preflight, and
    the record being *superseded* in publication. The shape is the same in both:
    the successor's own declared predecessor is the ledger's binding, that
    binding is the head of the chain, and the successor is not already in it.
    Both callers share this predicate so the two can never drift apart.
    """
    return bool(declared_predecessor
                and current == declared_predecessor
                and chain
                and chain[-1] == current
                and successor not in chain
                and successor != current)


def _stage_aliases(ledger: LinkLedger, alias_declarations) -> list[dict]:
    """Stage alias declarations in memory, merging budgets and never writing.

    ``declare_alias(..., flush=False)`` is budget-conserving and self-rolling
    back: a declaration that would break a ceiling, or that would combine two
    different retained joins, raises and leaves both the alias map and the
    budgets exactly as they were. Staging therefore cannot spend, refund or
    persist anything, which is what lets the composed preflight stage a
    prospective equivalence *before* it reconciles and decide afterwards
    whether the run may start at all.
    """
    staged: list[dict] = []
    for declaration in alias_declarations or ():
        try:
            alias, canonical = declaration
        except (TypeError, ValueError) as exc:
            raise CampaignPreflightRefusal(
                f"an alias declaration must be a (alias, canonical) pair, not "
                f"{declaration!r}") from exc
        try:
            outcome = ledger.declare_alias(alias, canonical, flush=False)
        except LinkLedgerError as exc:
            raise CampaignPreflightRefusal(
                f"staging the alias {alias!r} -> {canonical!r}: {exc}") from exc
        staged.append({
            "alias": outcome["alias"],
            "canonical": outcome["canonical"],
            "resolved": outcome["resolved"],
            "conserved": outcome["conserved"],
            "merged": len(outcome["merged"]),
        })
    return staged


def interruption_guard_preflight_campaign(
    *,
    ledger_path,
    resuming: bool,
    record_path=None,
    alias_declarations=(),
    parent_board_path=None,
) -> dict:
    """Read, hash and reconcile a campaign *before* its first write or engine.

    This is the composed, file-facing preflight: the only function in this
    module that reads real files, and the only one whose verdict may be treated
    as a permission to continue. It runs the whole read-and-validate phase ahead
    of the caller's first write and ahead of any engine, in this order:

    1. **read** the ledger the run would drive (a resume requires the file to
       exist - a missing ledger is never silently replaced by a fresh one);
    2. **read and hash** the campaign record's exact bytes and parse it, on a
       resume;
    3. **validate** the record through the limiter's own
       ``validate_campaign_record``;
    4. **bind identity**: the record's named ``ledger_file`` must be the ledger
       being preflighted, and the ledger must be bound either to this record
       (``current-record``) or to this record's own declared predecessor with the
       predecessor at the head of the binding chain
       (``interrupted-finalization``, the crash-between-write-and-bind shape);
    5. **hash the parent board's actual bytes** and require them to equal the
       ``final_parent_board_sha256`` the record declares;
    6. **stage the alias declarations in memory** (merging, never writing);
    7. **reconcile**: the limiter's cumulative and per-link accounting check, run
       last so identity is established before any accounting verdict is read.

    Only then does it return, with ``resume_safe``/``complete`` already backed by
    real bytes rather than by accounting alone. Every refusal raises
    :class:`CampaignPreflightRefusal` *before* the caller's first ``mkdir``, first
    write and first engine open: this function creates no directory, writes no
    file, opens no engine and imports none, and staged aliases are left in memory
    (``aliases_persisted`` is reported as ``False``).

    The pure helpers it composes stay low-level by design. ``preflight_ledger``
    and ``classify_ledger`` answer an accounting question about an object handed
    to them; their ``resume_safe`` and ``complete`` flags say the amounts add up,
    not that the record's identity, its parent bytes or its ledger binding have
    been checked, and not that a route may be attempted. Only this function
    reads the bytes an identity claim is made from, so only its verdict carries
    them.
    """
    ledger_path = Path(ledger_path)
    if resuming and record_path is None:
        raise CampaignPreflightRefusal(
            f"{ledger_path}: a resume must name the record it continues, so the "
            f"ledger can be reconciled against it; refusing before any write or "
            f"engine open")
    try:
        # A resume must find the ledger its record names; a fresh run may start
        # from nothing. Either way the load is read-only.
        ledger = LinkLedger.load(ledger_path, require_existing=bool(resuming))
    except LinkLedgerError as exc:
        raise CampaignPreflightRefusal(str(exc)) from exc
    try:
        totals_before = ledger.to_json()["totals"]
    except LinkLedgerError as exc:  # pragma: no cover - defensive
        raise CampaignPreflightRefusal(
            f"{ledger_path}: the ledger could not be summarised: {exc}") from exc
    verdict: dict = {
        "schema": GUARD_SCHEMA,
        "digest": DIGEST_NAME,
        "resuming": bool(resuming),
        "ledger_file_present": ledger_path.is_file(),
        "ledger_path_sha256":
            sha256_bytes(str(ledger_path.resolve()).encode("utf-8")),
        "ledger_totals": totals_before,
        "aliases_staged": [],
        "aliases_persisted": False,
        "record_sha256": None,
        "binding": ledger.campaign_binding(),
        "binding_kind": None,
        "parent_board_sha256": None,
    }
    verdict["aliases_staged"] = _stage_aliases(ledger, alias_declarations)
    if resuming:
        record_file = Path(record_path)
        if not record_file.is_file():
            raise CampaignPreflightRefusal(
                f"{record_file}: the campaign record this resume names does not "
                f"exist; refusing before any write or engine open")
        record, record_digest = _read_record(record_file)
        try:
            validate_campaign_record(record)
        except LinkLedgerError as exc:
            raise CampaignPreflightRefusal(
                f"{record_file}: the campaign record does not validate: {exc}") \
                from exc
        identity = _verify_record_identity(
            record, ledger_path, parent_board_path=parent_board_path)
        binding = ledger.campaign_binding()
        current, chain = _binding_chain(binding)
        predecessor = record.get("previous_record_sha256")
        if not current:
            raise CampaignPreflightRefusal(
                f"{ledger_path}: the ledger is not bound to a campaign record, "
                f"so it cannot be continued from {record_file}; refusing before "
                f"any write or engine open")
        if current == record_digest:
            binding_kind = "current-record"
        elif _is_interrupted_finalization(current, chain, predecessor,
                                          record_digest):
            # The successor record was written and the binding never advanced:
            # this record's own declared predecessor *is* the binding, and it is
            # the head of the chain, so the chain can still be extended in order.
            binding_kind = "interrupted-finalization"
        else:
            raise CampaignPreflightRefusal(
                f"{ledger_path}: the ledger is bound to {current}, which is "
                f"neither this record ({record_digest}) nor this record's "
                f"declared predecessor ({predecessor}); refusing an unguarded "
                f"continuation")
        try:
            classification = preflight_ledger(
                ledger, resuming=True, record=record, ledger_path=ledger_path)
        except InterruptedCampaignRefusal as exc:
            raise CampaignPreflightRefusal(str(exc)) from exc
        verdict.update({
            "record_sha256": record_digest,
            "binding_kind": binding_kind,
            "parent_board_sha256": identity["parent_board_sha256"],
            "parent_board": identity["parent_board"],
            "classification": classification,
        })
    else:
        try:
            classification = preflight_ledger(
                ledger, resuming=False, ledger_path=ledger_path)
        except InterruptedCampaignRefusal as exc:
            raise CampaignPreflightRefusal(str(exc)) from exc
        verdict["classification"] = classification
    verdict["status"] = classification["status"]
    verdict["complete"] = bool(classification.get("complete"))
    verdict["resume_safe"] = bool(classification.get("resume_safe"))
    verdict["ledger_totals_after"] = ledger.to_json()["totals"]
    verdict["ledger_unchanged"] = verdict["ledger_totals"] == verdict["ledger_totals_after"]
    return verdict


def publish_completed_record(*, record: dict, ledger: LinkLedger, record_path,
                             previous_record_path=None, parent_board_path=None,
                             bind: bool = True) -> dict:
    """Gate, verify, publish and bind a completed record as one guarded step.

    The completion gate runs **first**: a record whose cumulative or per-link
    history does not account for every charge the ledger holds raises before
    anything is written, so a record that could never be resumed is never
    published and its charges are never bound.

    Then the two things the gate cannot see are verified, because a record with
    perfect accounting and a dishonest identity is still refused by the next
    resume: the record's named ``ledger_file`` must be *this* ledger's own path,
    and the parent board's actual bytes must hash to the digest the record
    declares (``_verify_record_identity``, the same check the composed preflight
    runs, so the two can never disagree).

    Then the lineage is verified against bytes, not against a claim:

    * a **fresh** run is one whose ledger is unbound, and its record must declare
      no predecessor - publishing a record that claims a predecessor onto an
      unbound ledger would silently drop the chain;
    * a **continuation** must supply ``previous_record_path``, the file of the
      record it continues. That file must exist, must hash to the digest the new
      record declares, and must itself be a **completed campaign record** for this
      ledger: validated by the limiter's own ``validate_campaign_record`` and
      checked by ``_verify_record_identity``, exactly as the composed preflight
      checks a record before it resumes one. A file that merely carries a
      ``previous_record_sha256`` field is a well-formed JSON object but not a
      record, and is refused. The digest must then be either the ledger's current
      binding (the normal advance) or an interrupted finalization the chain can
      genuinely accept (``_is_interrupted_finalization``: the binding is that
      record's own declared predecessor and the head of the chain). A digest
      already in the chain at an earlier position is refused as an old record, and
      a digest the caller simply invented has no file to present, so it is
      refused too.

    Publication and binding are one step. ``bind=False`` is refused outright: a
    record published without the binding that names it is indistinguishable, to
    a later resume, from an unrelated file. **Every** refusal above - the gate,
    the identity checks and the lineage checks - is decided before the first
    ``mkdir`` and the first write, so a refused publication leaves the filesystem
    byte-identical. ``previous_record_path`` is ignored for a first binding,
    where the record declares no predecessor.

    The one failure this ordering cannot remove is a crash *between* the two
    writes - the record written, the binding not yet advanced - and that state is
    not lost: the composed preflight accepts it as ``interrupted-finalization``
    precisely because the successor declares the binding as its own predecessor.
    """
    if not bind:
        raise CompletedRecordRefusal(
            "refusing to publish a completed record without binding its ledger: "
            "publication and binding are one guarded step")

    # ---- every refusal from here to the first write is pre-write by design ----
    gate = completed_record_gate(record, ledger)
    if ledger.path is None:
        raise CompletedRecordRefusal(
            "the ledger has no path, so no record can name it; refusing to "
            "publish against a ledger with no identity")
    ledger_path = Path(ledger.path)
    try:
        identity = _verify_record_identity(
            record, ledger_path, parent_board_path=parent_board_path)
    except InterruptedCampaignRefusal as exc:
        raise CompletedRecordRefusal(f"refusing to publish: {exc}") from exc
    binding = ledger.campaign_binding()
    current, chain = _binding_chain(binding)
    predecessor = record.get("previous_record_sha256")
    prior_identity: dict | None = None
    if current is None:
        if predecessor:
            raise CompletedRecordRefusal(
                f"the ledger is not bound, so it cannot publish a record that "
                f"declares the predecessor {predecessor}: a first run declares "
                f"none")
        lineage = {"kind": "first-binding", "previous_record": None,
                   "head_predecessor": None, "predecessor_sha256": None}
    else:
        if not predecessor:
            raise CompletedRecordRefusal(
                f"the ledger is bound to {current}, so a completed record must "
                f"declare the record it continues; refusing a successor that "
                f"names none")
        if chain and chain[-1] != current:
            raise CompletedRecordRefusal(
                f"the ledger's binding chain ends at {chain[-1]}, not at its "
                f"current record {current}; refusing to extend a broken chain")
        if predecessor in chain and predecessor != current:
            raise CompletedRecordRefusal(
                f"the record this publication continues ({predecessor}) is "
                f"already in the ledger's chain at an earlier position; a "
                f"completed record may only continue the current binding "
                f"{current}")
        prior_path = (Path(previous_record_path)
                      if previous_record_path is not None else None)
        if prior_path is None or not prior_path.is_file():
            raise CompletedRecordRefusal(
                f"the record this publication continues must be supplied as a "
                f"file so its bytes can be verified; {prior_path} is not a "
                f"readable path")
        try:
            prior_sha256 = sha256_file(prior_path)
        except OSError as exc:
            raise CompletedRecordRefusal(
                f"{prior_path}: the record this publication continues could not "
                f"be read: {exc}") from exc
        if prior_sha256 != predecessor:
            raise CompletedRecordRefusal(
                f"{prior_path} hashes {prior_sha256}, but the new record "
                f"declares the predecessor {predecessor}; refusing to chain an "
                f"unverified digest")
        # The record being continued must be the same *kind* of object the
        # composed preflight accepts before it resumes one: a completed campaign
        # record for this ledger, carrying the parent bytes it declares. A file
        # that merely hashes to the declared digest and happens to carry a
        # ``previous_record_sha256`` field is not one, and the chain must not be
        # extended with it. These are the same two helpers the preflight runs, so
        # publication and the preflight cannot disagree about what a predecessor
        # is; the digest check above runs first so an unverified digest keeps
        # naming that reason rather than this one.
        try:
            prior_record, _ = _read_record(prior_path)
        except InterruptedCampaignRefusal as exc:
            raise CompletedRecordRefusal(str(exc)) from exc
        try:
            validate_campaign_record(prior_record)
        except LinkLedgerError as exc:
            raise CompletedRecordRefusal(
                f"{prior_path}: the record this publication continues is not a "
                f"completed campaign record: {exc}") from exc
        try:
            prior_identity = _verify_record_identity(prior_record, ledger_path)
        except InterruptedCampaignRefusal as exc:
            raise CompletedRecordRefusal(
                f"{prior_path}: the record this publication continues does not "
                f"carry the identity it declares: {exc}") from exc
        if predecessor == current:
            lineage = {"kind": "advance", "previous_record": predecessor,
                       "head_predecessor": None,
                       "predecessor_sha256": prior_sha256}
        else:
            declared = prior_record.get("previous_record_sha256")
            if not _is_interrupted_finalization(current, chain, declared,
                                                predecessor):
                raise CompletedRecordRefusal(
                    f"{prior_path} declares the predecessor {declared}, not "
                    f"the ledger's binding {current}, so it is not the "
                    f"interrupted record this chain can accept; refusing an "
                    f"unverified predecessor")
            lineage = {"kind": "interrupted-finalization",
                       "previous_record": predecessor,
                       "head_predecessor": current,
                       "predecessor_sha256": prior_sha256}

    # ---- the first write, only now that every refusal above has been decided ----
    record_path = Path(record_path)
    record_path.parent.mkdir(parents=True, exist_ok=True)
    record_path.write_text(
        json.dumps(record, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    digest = sha256_file(record_path)
    if current is None:
        ledger.bind_campaign({"campaign_record": digest})
    else:
        ledger.advance_campaign(digest,
                                previous_record=lineage["previous_record"],
                                head_predecessor=lineage["head_predecessor"])
    return {
        "schema": GUARD_SCHEMA,
        "status": "published",
        "record_sha256": digest,
        "binding_kind": lineage["kind"],
        "previous_record_sha256": lineage["predecessor_sha256"],
        "identity": identity,
        "predecessor_identity": prior_identity,
        "binding": ledger.campaign_binding(),
        "completed_record": gate,
    }
