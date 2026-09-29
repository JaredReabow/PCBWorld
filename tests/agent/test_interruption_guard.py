"""Unit tests for the interrupted-campaign guard (``pcb_world.agent.interruption_guard``).

CPU-only and engine-free. Two layers are tested separately, because they answer
different questions:

* the **pure** helpers (``classify_ledger``, ``preflight_ledger``,
  ``charge_attribution``, ``completed_record_gate``) answer an accounting
  question about an object handed to them;
* the **composed, file-facing** ``interruption_guard_preflight_campaign`` reads
  and hashes the real record and parent-board bytes, so its verdict is the only
  one that carries an identity claim.

Every refusal is asserted to leave *no trace*: a byte-for-byte snapshot of the
fixture tree is compared before and after, and a sentinel records that no engine
was opened. The last test is a mutation check that runs the same battery against
deliberately broken copies of the guard.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pcb_world.agent as agent_pkg

from pcb_world.agent import interruption_guard as G
from pcb_world.agent.ledger import LinkLedger, stable_identity

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GUARD_SOURCE = Path(G.__file__)

KEY_A = ["mem:" + "a" * 16, "mem:" + "b" * 16]
KEY_B = ["mem:" + "c" * 16, "mem:" + "d" * 16]
KEY_C = ["mem:" + "e" * 16, "mem:" + "f" * 16]
BOARD_BYTES = b"placeholder parent board bytes\n"


def sha256(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def snapshot(root) -> dict:
    """Every path under ``root`` with the digest of its bytes (or ``dir``)."""
    state: dict = {}
    for base, directories, files in os.walk(root):
        base_path = Path(base)
        for name in list(directories):
            state[str((base_path / name).relative_to(root))] = "dir"
        for name in files:
            path = base_path / name
            state[str(path.relative_to(root))] = sha256(path)
    return state


def charge(ledger, key, count, *, pair_id="pair"):
    """Charge ``count`` tickets on one link, settling each one."""
    receipts = []
    for index in range(count):
        ticket = ledger.reserve(key, f"{pair_id}-{index}", f"spec-{index}")
        receipts.append(ledger.settle(ticket, started=True, committed=True))
    return receipts


def reserve_without_settling(ledger, key, count):
    tickets = []
    for index in range(count):
        tickets.append(ledger.reserve(key, f"pair-{index}", f"spec-{index}"))
    return tickets


def link_rows(ledger, *, charged=None):
    """One record row per charged budget, sums taken from the ledger itself."""
    rows = []
    for budget in sorted(ledger.budgets.values(), key=lambda item: item.identity):
        spent = budget.attempted if charged is None else charged.get(budget.identity, 0)
        if spent:
            rows.append({"link_key": list(budget.link_key),
                         "native_transactions_attempted": spent,
                         "stable_identity": budget.identity})
    return rows


def record_for(ledger, ledger_path, *, links=None, history=(), previous=None,
               parent_dir, parent_bytes=BOARD_BYTES, run_total=None,
               cumulative=None, **overrides):
    rows = link_rows(ledger) if links is None else [dict(row) for row in links]
    history = [dict(row) for row in history]
    total = sum(row["native_transactions_attempted"] for row in rows)
    record = {
        "ran": True,
        "ledger_file": str(ledger_path),
        "final_parent_dir": str(parent_dir),
        "final_parent_board_sha256": hashlib.sha256(parent_bytes).hexdigest(),
        "previous_record_sha256": previous,
        "native_transactions_total": total if run_total is None else run_total,
        "attempted_links": len(rows),
        "ledger_totals": {"native_transactions": (
            total + sum(row["native_transactions_attempted"] for row in history)
            if cumulative is None else cumulative)},
        "links": rows,
        "history": history,
    }
    record.update(overrides)
    return record


def write_json(path, payload) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n",
                    encoding="utf-8")
    return path


def case(root, *, charges=(), unbound_charges=(), bound="record",
         record_override=None, record_links=None, parent_bytes=BOARD_BYTES,
         board_name="board.kicad_pcb", extra_boards=()):
    """A complete synthetic campaign on disk: parent board, ledger, record.

    ``charges`` are settled; ``unbound_charges`` are reserved but never settled
    (the shape that cannot be reconciled). ``bound`` is ``none``, ``record`` (the
    ledger names this record) or ``predecessor`` (the ledger names the record's
    own declared predecessor - the crash between write and bind).
    """
    root = Path(root)
    parent = root / "parent"
    parent.mkdir(parents=True, exist_ok=True)
    (parent / board_name).write_bytes(parent_bytes)
    for extra in extra_boards:
        (parent / extra).write_bytes(b"another board\n")
    ledger_path = root / "ledger.json"
    ledger = LinkLedger(ledger_path)
    for key, count in charges:
        charge(ledger, key, count)
    for key, count in unbound_charges:
        reserve_without_settling(ledger, key, count)
    ledger.flush()
    record = record_for(
        ledger, ledger_path, parent_dir=parent, parent_bytes=parent_bytes,
        links=record_links if record_links is not None else None,
        previous=("1" * 64) if bound == "predecessor" else None)
    if record_override:
        record.update(record_override)
    record_path = write_json(root / "record.json", record)
    digest = sha256(record_path)
    if bound == "record":
        ledger.bind_campaign({"campaign_record": digest})
    elif bound == "predecessor":
        ledger.bind_campaign({"campaign_record": "1" * 64})
    ledger.flush()
    return {
        "root": root, "parent": parent, "board": parent / board_name,
        "ledger_path": ledger_path, "ledger": ledger, "record": record,
        "record_path": record_path, "record_sha256": digest,
        "parent_sha256": hashlib.sha256(parent_bytes).hexdigest(),
    }


def expect_raises(exc, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except exc:
        return
    except BaseException as other:  # noqa: BLE001 - re-raised as a failure
        raise AssertionError(
            f"expected {exc.__name__}, got {type(other).__name__}: {other}"
        ) from other
    raise AssertionError(f"expected {exc.__name__}, nothing was raised")


def expect_refusal(exc, needle, fn, *args, **kwargs):
    """Require ``exc`` and that its message names the reason it was raised."""
    try:
        fn(*args, **kwargs)
    except exc as raised:
        message = str(raised)
        if needle not in message:
            raise AssertionError(
                f"{exc.__name__} was raised for the wrong reason: "
                f"{message!r} does not name {needle!r}") from raised
        return
    except BaseException as other:  # noqa: BLE001 - re-raised as a failure
        raise AssertionError(
            f"expected {exc.__name__}, got {type(other).__name__}: {other}"
        ) from other
    raise AssertionError(f"expected {exc.__name__}, nothing was raised")


def expect_refusal_matching(exc, needles, fn, *args, **kwargs):
    """Require ``exc`` and that its message names *every* reason in ``needles``.

    A refusal can be attributable to more than one thing at once - for example a
    predecessor that is refused *because* it is the record being continued *and*
    because it does not carry the identity it declares. Requiring both keeps the
    new predecessor checks pinned to the predecessor path rather than to the
    successor path, which shares some of the same wording.
    """
    if isinstance(needles, str):
        needles = (needles,)
    try:
        fn(*args, **kwargs)
    except exc as raised:
        message = str(raised)
        missing = [needle for needle in needles if needle not in message]
        if missing:
            raise AssertionError(
                f"{exc.__name__} was raised for the wrong reason: "
                f"{message!r} does not name {missing!r}") from raised
        return
    except BaseException as other:  # noqa: BLE001 - re-raised as a failure
        raise AssertionError(
            f"expected {exc.__name__}, got {type(other).__name__}: {other}"
        ) from other
    raise AssertionError(f"expected {exc.__name__}, nothing was raised")


def drive(mod, opened, **kwargs):
    """Run the preflight, then do what a campaign would do next: open an engine.

    The sentinel in ``opened`` is the whole point: a refusal must propagate
    before this line is reached, which is how "zero engine opens" is asserted
    rather than assumed.
    """
    verdict = mod.interruption_guard_preflight_campaign(**kwargs)
    opened.append("engine")
    return verdict


def publish_with_sentinel(mod, opened, **kwargs):
    """Publish, then do what a campaign would do next: open an engine.

    The publication sentinel. A refusal must propagate before the append below,
    which is how "the refusal happened before any engine was opened" is asserted
    rather than assumed.
    """
    published = mod.publish_completed_record(**kwargs)
    opened.append("engine")
    return published


class ProcessTripwire:
    """Fail loudly if anything starts a process while a publication is refused.

    The guard is engine-free and process-free by construction; this proves it for
    the call under test rather than trusting the source. Entered as a context
    manager, and every surface is restored on exit.
    """

    def __init__(self) -> None:
        self.hits: list = []

    def __enter__(self) -> "ProcessTripwire":
        import os as _os
        import subprocess as _subprocess

        self._saved = (_subprocess.Popen, _subprocess.run, _os.system)

        def tripwire(*args, **kwargs):
            self.hits.append((args, kwargs))
            raise AssertionError("a refusal started a process")

        _subprocess.Popen = tripwire
        _subprocess.run = tripwire
        _os.system = tripwire
        return self

    def __exit__(self, *exc_info) -> bool:
        import os as _os
        import subprocess as _subprocess

        _subprocess.Popen, _subprocess.run, _os.system = self._saved
        return False


#: The bytes an existing record path holds before a refused publication, so an
#: overwrite is observable rather than merely claimed.
SENTINEL_RECORD = b'{"pre-existing": true}\n'


def publication_case(mod, root, *, scenario, transactions=2, new_transactions=1):
    """A bound ledger plus the records a guarded publication needs.

    ``scenario`` selects which continuation the case presents: ``normal`` (the
    record being continued *is* the binding), ``interrupted`` (a record written
    but never bound, declaring the binding as its own predecessor), ``unrelated``
    (a real well-formed file that is not a continuation of the binding),
    ``older-in-chain`` (the declared predecessor is an earlier chain entry),
    ``broken-chain`` (the chain does not end at the binding), ``wrong-ledger``,
    ``wrong-parent`` or ``missing-parent``.

    The publish destination always already exists, holding ``SENTINEL_RECORD``.
    """
    root = Path(root)
    parent = root / "parent"
    parent.mkdir(parents=True, exist_ok=True)
    (parent / "board.kicad_pcb").write_bytes(BOARD_BYTES)
    ledger_path = root / "ledger.json"
    ledger = mod.LinkLedger(ledger_path)
    charge(ledger, KEY_A, transactions)
    ledger.flush()

    prior_charges = transactions - new_transactions
    prior_rows = ([{"link_key": KEY_A,
                    "native_transactions_attempted": prior_charges}]
                  if prior_charges else [])
    new_rows = ([{"link_key": KEY_A,
                  "native_transactions_attempted": new_transactions}]
                if new_transactions else [])

    def prior_at(filename, previous, **overrides):
        return write_json(root / filename, record_for(
            ledger, ledger_path, parent_dir=parent, previous=previous,
            links=prior_rows, history=[], **overrides))

    def successor(previous, **overrides):
        return record_for(ledger, ledger_path, parent_dir=parent,
                          previous=previous, links=new_rows,
                          history=prior_rows, **overrides)

    head = "a" * 64
    chain = [head]
    if scenario == "normal":
        prior_path = prior_at("previous.json", None)
        head = sha256(prior_path)
        chain = [head]
        record = successor(head)
    elif scenario in ("interrupted", "unrelated"):
        declared = head if scenario == "interrupted" else "9" * 64
        prior_path = prior_at("previous.json", declared)
        record = successor(sha256(prior_path))
    elif scenario == "older-in-chain":
        first = prior_at("r1.json", None)
        second = prior_at("r2.json", sha256(first))
        head = sha256(second)
        chain = [sha256(first), head]
        prior_path = first                      # the older, in-chain record
        record = successor(sha256(first))
    elif scenario == "broken-chain":
        prior_path = prior_at("previous.json", None)
        head = sha256(prior_path)
        chain = [head, "b" * 64]                # not ending at the binding
        record = successor(head)
    elif scenario in ("wrong-ledger", "wrong-parent", "missing-parent"):
        prior_path = prior_at("previous.json", head)
        record = successor(sha256(prior_path))
        if scenario == "wrong-ledger":
            record["ledger_file"] = str(root / "other-ledger.json")
        elif scenario == "wrong-parent":
            record["final_parent_board_sha256"] = "0" * 64
        else:
            record["final_parent_dir"] = str(root / "gone")
    elif scenario == "malformed-predecessor":
        # a well-formed JSON object that is not a campaign record at all: the
        # shape that used to pass the byte-hash and the lineage predicate
        prior_path = write_json(root / "previous.json",
                                {"previous_record_sha256": head})
        record = successor(sha256(prior_path))
    elif scenario == "wrong-ledger-predecessor":
        prior_path = prior_at("previous.json", head,
                              ledger_file=str(root / "other-ledger.json"))
        record = successor(sha256(prior_path))
    elif scenario in ("wrong-parent-predecessor", "missing-parent-predecessor"):
        if scenario == "wrong-parent-predecessor":
            prior_path = prior_at("previous.json", head,
                                  final_parent_board_sha256="0" * 64)
        else:
            prior_path = prior_at("previous.json", head,
                                  final_parent_dir=str(root / "gone"))
        record = successor(sha256(prior_path))
    else:
        raise AssertionError(f"unknown publication scenario {scenario!r}")

    ledger.bind_campaign({"campaign_record": head, "campaign_records": chain})
    record_path = root / "new-record.json"
    record_path.write_bytes(SENTINEL_RECORD)
    return {
        "root": root, "parent": parent, "board": parent / "board.kicad_pcb",
        "ledger": ledger, "ledger_path": ledger_path, "record": record,
        "record_path": record_path, "previous_record_path": prior_path,
        "head": head, "chain": list(chain),
    }


def refusal_cases(mod, root):
    """``{name: builder}`` for every refusal the composed preflight makes."""
    root = Path(root)
    cases = {}

    def fresh_over_charged_evidence(case_root):
        built = case(case_root, charges=[(KEY_A, 1)], bound="none")
        return dict(ledger_path=built["ledger_path"], resuming=False)

    def fresh_over_bound_evidence(case_root):
        built = case(case_root, charges=[(KEY_A, 1)], bound="record")
        return dict(ledger_path=built["ledger_path"], resuming=False)

    def resume_without_a_record(case_root):
        built = case(case_root, charges=[(KEY_A, 1)], bound="record")
        return dict(ledger_path=built["ledger_path"], resuming=True)

    def resume_with_a_missing_record(case_root):
        built = case(case_root, charges=[(KEY_A, 1)], bound="record")
        return dict(ledger_path=built["ledger_path"], resuming=True,
                    record_path=case_root / "absent.json")

    def resume_with_a_missing_ledger(case_root):
        built = case(case_root, charges=[(KEY_A, 1)], bound="record")
        return dict(ledger_path=case_root / "absent-ledger.json", resuming=True,
                    record_path=built["record_path"])

    def resume_naming_another_ledger(case_root):
        # the ledger is bound to the edited record, so only the named-ledger
        # check can refuse it: identity comes before accounting, not after
        built = case(case_root, charges=[(KEY_A, 1)], bound="none")
        other = case_root / "other-ledger.json"
        LinkLedger(other).flush()
        record = dict(built["record"], ledger_file=str(other))
        write_json(built["record_path"], record)
        built["ledger"].bind_campaign(
            {"campaign_record": sha256(built["record_path"])})
        return dict(ledger_path=built["ledger_path"], resuming=True,
                    record_path=built["record_path"])

    def resume_with_changed_parent_bytes(case_root):
        built = case(case_root, charges=[(KEY_A, 1)], bound="record")
        built["board"].write_bytes(b"the parent board was rewritten\n")
        return dict(ledger_path=built["ledger_path"], resuming=True,
                    record_path=built["record_path"])

    def resume_with_a_missing_parent_dir(case_root):
        built = case(case_root, charges=[(KEY_A, 1)], bound="record")
        record = dict(built["record"], final_parent_dir=str(case_root / "gone"))
        write_json(built["record_path"], record)
        return dict(ledger_path=built["ledger_path"], resuming=True,
                    record_path=built["record_path"])

    def resume_with_an_ambiguous_parent_dir(case_root):
        built = case(case_root, charges=[(KEY_A, 1)], bound="record",
                     extra_boards=("second_board.kicad_pcb",))
        return dict(ledger_path=built["ledger_path"], resuming=True,
                    record_path=built["record_path"])

    def resume_with_an_outside_parent_board(case_root):
        built = case(case_root, charges=[(KEY_A, 1)], bound="record")
        outside = case_root / "outside.kicad_pcb"
        outside.write_bytes(BOARD_BYTES)
        return dict(ledger_path=built["ledger_path"], resuming=True,
                    record_path=built["record_path"],
                    parent_board_path=outside)

    def resume_from_an_unbound_ledger(case_root):
        built = case(case_root, charges=[(KEY_A, 1)], bound="none")
        return dict(ledger_path=built["ledger_path"], resuming=True,
                    record_path=built["record_path"])

    def resume_from_an_unrelated_binding(case_root):
        # bound to a record that is neither this one nor its predecessor
        built = case(case_root, charges=[(KEY_A, 1)], bound="none")
        built["ledger"].bind_campaign({"campaign_record": "2" * 64})
        return dict(ledger_path=built["ledger_path"], resuming=True,
                    record_path=built["record_path"])

    def resume_with_an_extra_settled_charge(case_root):
        built = case(case_root, charges=[(KEY_A, 1), (KEY_B, 1)], bound="record",
                     record_links=[{"link_key": KEY_A,
                                    "native_transactions_attempted": 1}])
        return dict(ledger_path=built["ledger_path"], resuming=True,
                    record_path=built["record_path"])

    def resume_with_an_extra_unsettled_charge(case_root):
        built = case(case_root, charges=[(KEY_A, 1)], bound="record",
                     unbound_charges=[(KEY_B, 1)],
                     record_links=[{"link_key": KEY_A,
                                    "native_transactions_attempted": 1}])
        return dict(ledger_path=built["ledger_path"], resuming=True,
                    record_path=built["record_path"])

    def resume_with_redistributed_charges(case_root):
        built = case(case_root, charges=[(KEY_A, 1), (KEY_B, 1)], bound="record",
                     record_links=[{"link_key": KEY_A,
                                    "native_transactions_attempted": 2}])
        return dict(ledger_path=built["ledger_path"], resuming=True,
                    record_path=built["record_path"])

    def resume_with_a_malformed_record(case_root):
        built = case(case_root, charges=[(KEY_A, 1)], bound="record")
        built["record_path"].write_text("{not json", encoding="utf-8")
        return dict(ledger_path=built["ledger_path"], resuming=True,
                    record_path=built["record_path"])

    def resume_with_a_non_object_record(case_root):
        built = case(case_root, charges=[(KEY_A, 1)], bound="record")
        write_json(built["record_path"], [1, 2, 3])
        return dict(ledger_path=built["ledger_path"], resuming=True,
                    record_path=built["record_path"])

    def resume_with_a_record_whose_digest_moved(case_root):
        built = case(case_root, charges=[(KEY_A, 1)], bound="record")
        record = dict(built["record"], stop_reason="edited after binding")
        write_json(built["record_path"], record)
        return dict(ledger_path=built["ledger_path"], resuming=True,
                    record_path=built["record_path"])

    def resume_with_a_ceiling_breaking_alias(case_root):
        built = case(case_root, charges=[(["alpha", "gamma"], 3),
                                        (["beta", "gamma"], 3)], bound="record",
                     record_links=[{"link_key": ["alpha", "gamma"],
                                    "native_transactions_attempted": 6}])
        return dict(ledger_path=built["ledger_path"], resuming=True,
                    record_path=built["record_path"],
                    alias_declarations=[("alpha", "beta")])

    cases.update({
        "fresh over charged evidence": fresh_over_charged_evidence,
        "fresh over bound evidence": fresh_over_bound_evidence,
        "resume without a record": resume_without_a_record,
        "resume with a missing record": resume_with_a_missing_record,
        "resume with a missing ledger": resume_with_a_missing_ledger,
        "resume naming another ledger": resume_naming_another_ledger,
        "resume with changed parent bytes": resume_with_changed_parent_bytes,
        "resume with a missing parent dir": resume_with_a_missing_parent_dir,
        "resume with an ambiguous parent dir": resume_with_an_ambiguous_parent_dir,
        "resume with a parent board outside the directory": resume_with_an_outside_parent_board,
        "resume from an unbound ledger": resume_from_an_unbound_ledger,
        "resume from an unrelated binding": resume_from_an_unrelated_binding,
        "resume with an extra settled charge": resume_with_an_extra_settled_charge,
        "resume with an extra unsettled charge": resume_with_an_extra_unsettled_charge,
        "resume with redistributed charges": resume_with_redistributed_charges,
        "resume with a malformed record": resume_with_a_malformed_record,
        "resume with a non-object record": resume_with_a_non_object_record,
        "resume whose record digest moved": resume_with_a_record_whose_digest_moved,
        "resume with a ceiling-breaking alias": resume_with_a_ceiling_breaking_alias,
    })
    return cases


def checks(mod, root: Path) -> dict:
    """The invariant battery for the guard, written against a module object."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)

    def case_root(name: str) -> Path:
        target = root / name.replace(" ", "-")
        target.mkdir(parents=True, exist_ok=True)
        return target

    # -- the pure layer ----------------------------------------------------
    def the_classification_vocabulary_is_exactly_four_states():
        empty = case(case_root("vocabulary/empty"), bound="none")
        fresh = mod.classify_ledger(empty["ledger"])
        assert fresh["status"] == mod.STATUS_FRESH
        assert fresh["complete"] is True and fresh["validated"] is True
        charged = case(case_root("vocabulary/charged"), charges=[(KEY_A, 1)],
                       bound="none")
        interrupted = mod.classify_ledger(charged["ledger"])
        assert interrupted["status"] == mod.STATUS_INTERRUPTED
        assert interrupted["complete"] is False
        assert interrupted["reasons"], "interrupted evidence must say why"
        bound = case(case_root("vocabulary/bound"), charges=[(KEY_A, 1)],
                     bound="record")
        active = mod.classify_ledger(bound["ledger"], record=bound["record"])
        assert active["status"] == mod.STATUS_ACTIVE
        assert active["complete"] is True and active["validated"] is True
        assert {mod.STATUS_FRESH, mod.STATUS_ACTIVE, mod.STATUS_INTERRUPTED,
                mod.STATUS_QUARANTINED} == {"fresh", "active", "interrupted",
                                            "quarantined"}
        assert fresh["schema"] == active["schema"] == mod.GUARD_SCHEMA

    def a_bound_ledger_without_a_record_is_never_complete():
        bound = case(case_root("no-record"), charges=[(KEY_A, 1)], bound="record")
        verdict = mod.classify_ledger(bound["ledger"])
        assert verdict["status"] == mod.STATUS_ACTIVE
        assert verdict["complete"] is False
        assert verdict["validated"] is False
        assert verdict["pending_checks"], "what is missing must be named"
        # the resume refuses because the record is missing, not because the
        # accounting happened to be incomplete
        expect_refusal(mod.InterruptedCampaignRefusal, "must supply the record",
                       mod.preflight_ledger, bound["ledger"], resuming=True)

    def an_inconsistent_attribution_is_itself_a_refusal():
        root_here = case_root("inconsistent")
        parent = root_here / "parent"
        parent.mkdir(parents=True, exist_ok=True)
        (parent / "board.kicad_pcb").write_bytes(BOARD_BYTES)
        ledger_path = root_here / "ledger.json"
        ledger = mod.LinkLedger(ledger_path)
        # two charges, one settled receipt, a record that accounts for both: the
        # totals reconcile, the attribution cannot
        tickets = reserve_without_settling(ledger, KEY_A, 2)
        ledger.settle(tickets[0], started=True, committed=True)
        ledger.flush()
        record = record_for(ledger, ledger_path, parent_dir=parent,
                            links=[{"link_key": KEY_A,
                                    "native_transactions_attempted": 2}])
        record_path = write_json(root_here / "record.json", record)
        ledger.bind_campaign({"campaign_record": sha256(record_path)})
        attribution = mod.charge_attribution(record, ledger)
        assert attribution["consistent"] is False
        assert attribution["held"] == attribution["recorded"] == 2
        verdict = mod.classify_ledger(ledger, record=record)
        assert verdict["status"] == mod.STATUS_QUARANTINED
        assert verdict["complete"] is False
        expect_refusal(mod.InterruptedCampaignRefusal, "does not add up",
                       mod.preflight_ledger, ledger, resuming=True,
                       record=record)

    def the_accounting_attribution_splits_settled_from_unsettled_extras():
        root_here = case_root("attribution")
        parent = root_here / "parent"
        parent.mkdir(parents=True, exist_ok=True)
        (parent / "board.kicad_pcb").write_bytes(BOARD_BYTES)
        ledger_path = root_here / "ledger.json"
        ledger = mod.LinkLedger(ledger_path)
        charge(ledger, KEY_A, 1)
        # two charges on the second link, only the first of them settled: one
        # unaccounted charge carries a receipt and one does not
        tickets = reserve_without_settling(ledger, KEY_B, 2)
        ledger.settle(tickets[0], started=True, committed=True)
        ledger.flush()
        record = record_for(ledger, ledger_path, parent_dir=parent,
                            links=[{"link_key": KEY_A,
                                    "native_transactions_attempted": 1}])
        attribution = mod.charge_attribution(record, ledger)
        assert attribution["held"] == 3
        assert attribution["recorded"] == 1
        assert attribution["unaccounted"] == 2
        assert attribution["settled_unaccounted"] == 1
        assert attribution["unsettled_unaccounted"] == 1
        assert attribution["consistent"] is True
        assert len(attribution["divergent_links"]) == 1
        assert (attribution["divergent_links"][0]["settled_unaccounted"]
                + attribution["divergent_links"][0]["unsettled_unaccounted"]) == 2

    def a_charge_the_bound_record_cannot_account_for_is_quarantined():
        built = case(case_root("reviewed-shape"), charges=[(KEY_A, 1)],
                     unbound_charges=[(KEY_B, 1)], bound="record",
                     record_links=[{"link_key": KEY_A,
                                    "native_transactions_attempted": 1}])
        verdict = mod.classify_ledger(built["ledger"], record=built["record"])
        assert verdict["status"] == mod.STATUS_QUARANTINED
        assert verdict["complete"] is False
        assert verdict["attribution"]["unaccounted"] == 1
        assert verdict["attribution"]["unsettled_unaccounted"] == 1
        expect_refusal(mod.InterruptedCampaignRefusal, "does not account for",
                       mod.preflight_ledger, built["ledger"], resuming=True,
                       record=built["record"])

    def equal_totals_redistributed_per_link_are_quarantined_both_ways():
        built = case(case_root("redistribution/one-way"),
                     charges=[(KEY_A, 1), (KEY_B, 1)], bound="record",
                     record_links=[{"link_key": KEY_A,
                                    "native_transactions_attempted": 2}])
        verdict = mod.classify_ledger(built["ledger"], record=built["record"])
        assert verdict["status"] == mod.STATUS_QUARANTINED
        assert verdict["attribution"]["held"] == verdict["attribution"]["recorded"] == 2
        expect_raises(mod.InterruptedCampaignRefusal, mod.preflight_ledger,
                      built["ledger"], resuming=True, record=built["record"])
        # the other direction: a recorded link the ledger never charged
        other = case(case_root("redistribution/other-way"),
                     charges=[(KEY_A, 2)], bound="record",
                     record_links=[{"link_key": KEY_A,
                                    "native_transactions_attempted": 1},
                                   {"link_key": KEY_B,
                                    "native_transactions_attempted": 1}])
        verdict = mod.classify_ledger(other["ledger"], record=other["record"])
        assert verdict["status"] == mod.STATUS_QUARANTINED
        expect_raises(mod.InterruptedCampaignRefusal, mod.preflight_ledger,
                      other["ledger"], resuming=True, record=other["record"])

    def the_fresh_path_refuses_charged_or_bound_evidence_before_any_write():
        charged = case(case_root("fresh/charged"), charges=[(KEY_A, 1)], bound="none")
        before = snapshot(charged["root"])
        expect_refusal(mod.InterruptedCampaignRefusal, "may not adopt it",
                       mod.preflight_ledger, charged["ledger"], resuming=False)
        assert snapshot(charged["root"]) == before
        bound = case(case_root("fresh/bound"), charges=[(KEY_A, 1)], bound="record")
        expect_refusal(mod.InterruptedCampaignRefusal, "use --resume",
                       mod.preflight_ledger, bound["ledger"], resuming=False)
        clean = case(case_root("fresh/clean"), bound="none")
        verdict = mod.preflight_ledger(clean["ledger"], resuming=False)
        assert verdict["status"] == mod.STATUS_FRESH
        assert verdict["resume_safe"] is False

    def the_completion_gate_refuses_before_anything_is_written():
        built = case(case_root("gate"), charges=[(KEY_A, 1)], bound="none")
        good = record_for(built["ledger"], built["ledger_path"],
                          parent_dir=built["parent"])
        gate = mod.completed_record_gate(good, built["ledger"])
        assert gate["status"] == "reconciled"
        assert gate["cumulative"] == 1 and gate["run_total"] == 1
        bad = record_for(built["ledger"], built["ledger_path"],
                         parent_dir=built["parent"], cumulative=9, run_total=1,
                         links=[{"link_key": KEY_A,
                                 "native_transactions_attempted": 1}])
        expect_raises(mod.CompletedRecordRefusal, mod.completed_record_gate,
                      bad, built["ledger"])
        expect_refusal(mod.CompletedRecordRefusal, "not a completed run",
                       mod.completed_record_gate, {**good, "ran": False},
                       built["ledger"])

    def the_quarantine_note_is_explicitly_incomplete():
        note = mod.quarantine_note({"status": mod.STATUS_INTERRUPTED},
                                   boundary="resume", error="refused")
        assert note["status"] == mod.STATUS_QUARANTINED
        assert note["complete"] is False
        assert note["boundary"] == "resume"
        assert note["error"] == "refused"

    # -- the composed, file-facing layer -----------------------------------
    def a_fresh_run_over_a_missing_ledger_is_allowed_and_writes_nothing():
        target = case_root("composed/fresh")
        ledger_path = target / "ledger.json"
        before = snapshot(target)
        verdict = mod.interruption_guard_preflight_campaign(
            ledger_path=ledger_path, resuming=False)
        assert verdict["status"] == mod.STATUS_FRESH
        assert verdict["resume_safe"] is False
        assert verdict["ledger_file_present"] is False
        assert verdict["ledger_unchanged"] is True
        assert verdict["aliases_persisted"] is False
        assert not ledger_path.exists(), "the preflight created the ledger"
        assert snapshot(target) == before

    def a_current_record_resume_reads_and_binds_every_identity():
        built = case(case_root("composed/current"), charges=[(KEY_A, 2),
                                                            (KEY_B, 1)],
                     bound="record")
        before = snapshot(built["root"])
        verdict = mod.interruption_guard_preflight_campaign(
            ledger_path=built["ledger_path"], resuming=True,
            record_path=built["record_path"])
        assert verdict["status"] == mod.STATUS_ACTIVE
        assert verdict["resume_safe"] is True
        assert verdict["complete"] is True
        assert verdict["binding_kind"] == "current-record"
        assert verdict["record_sha256"] == built["record_sha256"] == sha256(
            built["record_path"])
        assert verdict["parent_board_sha256"] == built["parent_sha256"]
        assert verdict["ledger_totals"]["native_transactions"] == 3
        assert verdict["ledger_unchanged"] is True
        assert snapshot(built["root"]) == before, "a preflight wrote something"

    def an_interrupted_finalization_is_accepted_as_its_own_binding_kind():
        built = case(case_root("composed/interrupted"), charges=[(KEY_A, 1)],
                     bound="predecessor")
        verdict = mod.interruption_guard_preflight_campaign(
            ledger_path=built["ledger_path"], resuming=True,
            record_path=built["record_path"])
        assert verdict["binding_kind"] == "interrupted-finalization"
        assert verdict["status"] == mod.STATUS_ACTIVE
        assert verdict["resume_safe"] is True
        assert built["ledger"].campaign_binding()["campaign_record"] == "1" * 64

    def the_same_resume_preflight_is_stable_twice():
        built = case(case_root("composed/twice"), charges=[(KEY_A, 1)],
                     bound="record")
        first = mod.interruption_guard_preflight_campaign(
            ledger_path=built["ledger_path"], resuming=True,
            record_path=built["record_path"])
        second = mod.interruption_guard_preflight_campaign(
            ledger_path=built["ledger_path"], resuming=True,
            record_path=built["record_path"])
        assert first == second

    def every_refusal_leaves_no_trace_and_opens_no_engine():
        _assert_no_engine_surface()
        for name, build in refusal_cases(mod, root).items():
            built_root = case_root(f"refusals/{name}")
            kwargs = build(built_root)
            before = snapshot(built_root)
            opened: list = []
            try:
                drive(mod, opened, **kwargs)
            except mod.CampaignPreflightRefusal:
                pass
            else:
                raise AssertionError(f"{name}: the preflight did not refuse")
            assert opened == [], f"{name}: a refusal reached the engine"
            assert snapshot(built_root) == before, f"{name}: the refusal wrote"

    def aliases_are_staged_in_memory_and_never_persisted():
        built = case(case_root("composed/alias"), charges=[(["alpha", "gamma"], 2),
                                                          (["beta", "gamma"], 1)],
                     bound="record",
                     record_links=[{"link_key": ["alpha", "gamma"],
                                    "native_transactions_attempted": 3}])
        # Without the alias the ledger's two budgets cannot reconcile with the
        # record's single merged link, so the declaration is load-bearing.
        expect_raises(mod.CampaignPreflightRefusal,
                      mod.interruption_guard_preflight_campaign,
                      ledger_path=built["ledger_path"], resuming=True,
                      record_path=built["record_path"])
        before = snapshot(built["root"])
        verdict = mod.interruption_guard_preflight_campaign(
            ledger_path=built["ledger_path"], resuming=True,
            record_path=built["record_path"],
            alias_declarations=[("alpha", "beta")])
        assert verdict["resume_safe"] is True
        assert verdict["aliases_persisted"] is False
        assert len(verdict["aliases_staged"]) == 1
        assert verdict["aliases_staged"][0]["conserved"] is True
        assert verdict["aliases_staged"][0]["merged"] == 1
        assert snapshot(built["root"]) == before, "a staged alias was written"
        on_disk = json.loads(built["ledger_path"].read_text(encoding="utf-8"))
        assert on_disk["aliases"] == {}
        assert len(on_disk["links"]) == 2

    def the_full_sequence_completes_and_advances_the_binding():
        target = case_root("composed/sequence")
        parent = target / "parent"
        parent.mkdir(parents=True, exist_ok=True)
        (parent / "board.kicad_pcb").write_bytes(BOARD_BYTES)
        ledger_path = target / "ledger.json"
        record_one = target / "record1.json"
        record_two = target / "record2.json"
        first = mod.interruption_guard_preflight_campaign(
            ledger_path=ledger_path, resuming=False)
        assert first["status"] == mod.STATUS_FRESH
        ledger = LinkLedger.load(ledger_path)                     # first write
        ticket = ledger.reserve(KEY_A, "pair-0", "spec-0")
        # the write-through happens before the native call, not after it
        def native_callback():
            on_disk = json.loads(ledger_path.read_text(encoding="utf-8"))
            assert on_disk["totals"]["native_transactions"] == 1, \
                "the reservation was not persisted before the native call"
            return {"started": True, "committed": True}

        ledger.settle(ticket, **native_callback())
        record = record_for(ledger, ledger_path, parent_dir=parent)
        gate = mod.completed_record_gate(record, ledger)
        assert gate["status"] == "reconciled"
        published = mod.publish_completed_record(
            record=record, ledger=ledger, record_path=record_one)
        assert published["binding_kind"] == "first-binding"
        digest_one = published["record_sha256"]
        assert digest_one == sha256(record_one)
        assert ledger.campaign_binding()["campaign_record"] == digest_one
        # the published record resumes: read, hash, identity, reconcile
        resumed = mod.interruption_guard_preflight_campaign(
            ledger_path=ledger_path, resuming=True, record_path=record_one)
        assert resumed["binding_kind"] == "current-record"
        assert resumed["resume_safe"] is True
        # a second run charges one more transaction and advances the binding
        second_ticket = ledger.reserve(KEY_A, "pair-1", "spec-1")
        ledger.settle(second_ticket, started=True, committed=True)
        prior = link_rows(ledger, charged={stable_identity(KEY_A): 1})
        successor = record_for(
            ledger, ledger_path, parent_dir=parent, previous=digest_one,
            links=[{"link_key": KEY_A, "native_transactions_attempted": 1}],
            history=prior)
        advanced = mod.publish_completed_record(
            record=successor, ledger=ledger, record_path=record_two,
            previous_record_path=record_one)
        assert advanced["binding_kind"] == "advance"
        chain = ledger.campaign_binding()["campaign_records"]
        assert chain == [digest_one, advanced["record_sha256"]]
        final = mod.interruption_guard_preflight_campaign(
            ledger_path=ledger_path, resuming=True, record_path=record_two)
        assert final["record_sha256"] == advanced["record_sha256"]
        assert final["resume_safe"] is True
        assert final["ledger_totals"]["native_transactions"] == 2

    def publication_without_binding_is_refused_and_writes_nothing():
        built = case(case_root("publish/unbound"), charges=[(KEY_A, 1)],
                     bound="none")
        record = record_for(built["ledger"], built["ledger_path"],
                            parent_dir=built["parent"])
        target = built["root"] / "new-record.json"
        before = snapshot(built["root"])
        expect_refusal(mod.CompletedRecordRefusal, "without binding its ledger",
                       mod.publish_completed_record, record=record,
                       ledger=built["ledger"], record_path=target, bind=False)
        assert not target.exists()
        assert built["ledger"].campaign_binding() == {}
        assert snapshot(built["root"]) == before

    def a_failed_gate_publishes_and_binds_nothing():
        built = case(case_root("publish/gate"), charges=[(KEY_A, 2)], bound="none")
        bad = record_for(built["ledger"], built["ledger_path"],
                         parent_dir=built["parent"], run_total=1, cumulative=1,
                         links=[{"link_key": KEY_A,
                                 "native_transactions_attempted": 1}])
        target = built["root"] / "new-record.json"
        before = snapshot(built["root"])
        expect_raises(mod.CompletedRecordRefusal, mod.publish_completed_record,
                      record=bad, ledger=built["ledger"], record_path=target)
        assert not target.exists(), "a refused record was published"
        assert built["ledger"].campaign_binding() == {}
        assert snapshot(built["root"]) == before

    def a_first_run_may_not_publish_a_record_that_declares_a_predecessor():
        built = case(case_root("publish/predecessor"), charges=[(KEY_A, 1)],
                     bound="none")
        record = record_for(built["ledger"], built["ledger_path"],
                            parent_dir=built["parent"], previous="9" * 64)
        target = built["root"] / "new-record.json"
        expect_refusal(mod.CompletedRecordRefusal, "not bound",
                       mod.publish_completed_record, record=record,
                       ledger=built["ledger"], record_path=target)
        assert not target.exists()
        assert built["ledger"].campaign_binding() == {}

    # -- publication must refuse before the first write --------------------
    def _publication(name, **kwargs):
        return publication_case(mod, case_root(f"publish/{name}"), **kwargs)

    def _assert_no_engine_surface():
        """The module cannot construct an engine, a session, a process or a socket.

        The runtime tripwire proves no process was started and the sentinel
        proves the caller's engine step was never reached; this proves the guard
        has no engine, session, process or network surface to reach for in the
        first place.
        """
        source = Path(mod.__file__).read_text(encoding="utf-8")
        for token in ("pcb_world.engine", "KiCadEngine", "AgentSession",
                      "import subprocess", "import socket", "import urllib",
                      "import requests", "os.system", "Popen"):
            assert token not in source, (
                f"the guard carries an engine/process surface: {token!r}")

    def _assert_refused_publication(name, built, needles, record_override=None,
                                    omit_previous_record_file=False):
        """The canary the reviewer required, for one refused publication.

        Byte-for-byte over (a) an existing record path, (b) its containing
        directory and (c) the ledger file - plus the binding, a process tripwire
        and an engine sentinel that a refusal must never reach.
        """
        target = built["record_path"]
        record = built["record"] if record_override is None else record_override
        before_record = target.read_bytes()
        before_directory = snapshot(target.parent)
        before_ledger = built["ledger_path"].read_bytes()
        before_binding = dict(built["ledger"].campaign_binding())
        opened: list = []
        previous_record_path = (None if omit_previous_record_file
                                else built["previous_record_path"])
        _assert_no_engine_surface()
        with ProcessTripwire() as tripwire:
            expect_refusal_matching(mod.CompletedRecordRefusal, needles,
                                    publish_with_sentinel, mod, opened,
                                    record=record, ledger=built["ledger"],
                                    record_path=target,
                                    previous_record_path=previous_record_path)
        assert tripwire.hits == [], f"{name}: a refusal started a process"
        assert opened == [], f"{name}: a refusal reached an engine"
        assert target.read_bytes() == before_record, (
            f"{name}: the existing record path was overwritten")
        assert snapshot(target.parent) == before_directory, (
            f"{name}: the refusal left something behind in the directory")
        assert built["ledger_path"].read_bytes() == before_ledger, (
            f"{name}: the ledger file moved")
        assert dict(built["ledger"].campaign_binding()) == before_binding, (
            f"{name}: the ledger binding moved")

    def publication_refuses_an_unrelated_new_predecessor():
        # (a) a real, well-formed record file that is not a continuation of the
        # binding: its own declared predecessor is an unrelated digest, so the
        # chain cannot lawfully accept it as an interrupted finalization
        built = _publication("unrelated", scenario="unrelated")
        _assert_refused_publication("unrelated predecessor", built,
                                    "not the interrupted record")
        # (b) a predecessor path whose bytes do not hash to the declared digest:
        # a caller-invented digest has no file to present
        built = _publication("unverified", scenario="interrupted")
        invented = dict(built["record"])
        invented["previous_record_sha256"] = "c" * 64
        _assert_refused_publication("unverified predecessor bytes", built,
                                    "refusing to chain an unverified digest",
                                    record_override=invented)
        # (c) a continuation that names no file at all: a caller-invented digest
        # has nothing on disk to present
        built = _publication("no-file", scenario="interrupted")
        _assert_refused_publication("no predecessor file", built,
                                    "must be supplied as a file",
                                    omit_previous_record_file=True)

    def publication_refuses_an_older_in_chain_predecessor():
        # an older digest already in the chain must be refused *as* an old
        # record, before the record is written - not by a later advance failure
        built = _publication("older", scenario="older-in-chain")
        _assert_refused_publication("older in-chain predecessor", built,
                                    "already in the ledger's chain")
        # and a chain that does not end at its current binding is not extended
        built = _publication("broken-chain", scenario="broken-chain")
        _assert_refused_publication("broken chain", built,
                                    "refusing to extend a broken chain")

    def publication_refuses_a_wrong_named_ledger():
        built = _publication("wrong-ledger", scenario="wrong-ledger")
        _assert_refused_publication(
            "wrong named ledger", built,
            "identity is not the identity of the ledger")

    def publication_refuses_wrong_or_missing_parent_bytes():
        built = _publication("wrong-parent", scenario="wrong-parent")
        _assert_refused_publication("wrong parent bytes", built,
                                    "parent bytes are not the bytes it names")
        built = _publication("missing-parent", scenario="missing-parent")
        _assert_refused_publication(
            "missing parent bytes", built,
            "parent directory this record declares does not exist")

    def publication_refuses_a_malformed_predecessor():
        # the reviewer's case: a file that hashes to the declared digest and
        # carries a `previous_record_sha256` field, but is not a campaign record
        # at all - so it may never extend the chain
        built = _publication("malformed-predecessor",
                             scenario="malformed-predecessor")
        _assert_refused_publication(
            "malformed predecessor", built,
            ("the record this publication continues",
             "is not a completed campaign record"))

    def publication_refuses_a_wrong_ledger_predecessor():
        # a genuine completed record, but one that names some other ledger
        built = _publication("wrong-ledger-predecessor",
                             scenario="wrong-ledger-predecessor")
        _assert_refused_publication(
            "wrong-ledger predecessor", built,
            ("the record this publication continues", "names this ledger"))

    def publication_refuses_a_wrong_parent_predecessor():
        # a genuine completed record whose declared parent bytes, or parent
        # directory, are not what is on disk
        built = _publication("wrong-parent-predecessor",
                             scenario="wrong-parent-predecessor")
        _assert_refused_publication(
            "wrong-parent predecessor", built,
            ("the record this publication continues",
             "parent bytes are not the bytes it names"))
        built = _publication("missing-parent-predecessor",
                             scenario="missing-parent-predecessor")
        _assert_refused_publication(
            "missing-parent predecessor", built,
            ("the record this publication continues",
             "parent directory this record declares does not exist"))

    def a_verified_interrupted_finalization_publishes_and_extends_the_chain():
        built = _publication("interrupted-ok", scenario="interrupted")
        opened: list = []
        published = publish_with_sentinel(
            mod, opened, record=built["record"], ledger=built["ledger"],
            record_path=built["record_path"],
            previous_record_path=built["previous_record_path"])
        assert opened == ["engine"], "the lawful path must reach the engine"
        assert published["binding_kind"] == "interrupted-finalization"
        chain = built["ledger"].campaign_binding()["campaign_records"]
        assert chain == [built["head"], sha256(built["previous_record_path"]),
                         published["record_sha256"]], (
            "the chain must be extended in order with the interrupted record")
        # and what it published is a lawful resume of the ledger it bound
        verdict = mod.interruption_guard_preflight_campaign(
            ledger_path=built["ledger_path"], resuming=True,
            record_path=built["record_path"])
        assert verdict["binding_kind"] == "current-record"
        assert verdict["resume_safe"] is True

    return {
        "the vocabulary is exactly four states": the_classification_vocabulary_is_exactly_four_states,
        "a bound ledger without a record is never complete": a_bound_ledger_without_a_record_is_never_complete,
        "an inconsistent attribution is itself a refusal": an_inconsistent_attribution_is_itself_a_refusal,
        "the attribution splits settled from unsettled": the_accounting_attribution_splits_settled_from_unsettled_extras,
        "an unaccounted charge is quarantined": a_charge_the_bound_record_cannot_account_for_is_quarantined,
        "equal totals redistributed are quarantined": equal_totals_redistributed_per_link_are_quarantined_both_ways,
        "the fresh path refuses charged or bound evidence": the_fresh_path_refuses_charged_or_bound_evidence_before_any_write,
        "the completion gate refuses first": the_completion_gate_refuses_before_anything_is_written,
        "the quarantine note is incomplete": the_quarantine_note_is_explicitly_incomplete,
        "a fresh run writes nothing": a_fresh_run_over_a_missing_ledger_is_allowed_and_writes_nothing,
        "a current-record resume binds every identity": a_current_record_resume_reads_and_binds_every_identity,
        "an interrupted finalization is accepted": an_interrupted_finalization_is_accepted_as_its_own_binding_kind,
        "the same resume preflight is stable twice": the_same_resume_preflight_is_stable_twice,
        "every refusal leaves no trace or engine": every_refusal_leaves_no_trace_and_opens_no_engine,
        "aliases are staged in memory only": aliases_are_staged_in_memory_and_never_persisted,
        "the full sequence completes and advances": the_full_sequence_completes_and_advances_the_binding,
        "publication without binding is refused": publication_without_binding_is_refused_and_writes_nothing,
        "a failed gate publishes nothing": a_failed_gate_publishes_and_binds_nothing,
        "a first run declares no predecessor": a_first_run_may_not_publish_a_record_that_declares_a_predecessor,
        "publication refuses an unrelated new predecessor": publication_refuses_an_unrelated_new_predecessor,
        "publication refuses an older in-chain predecessor": publication_refuses_an_older_in_chain_predecessor,
        "publication refuses a wrong named ledger": publication_refuses_a_wrong_named_ledger,
        "publication refuses wrong or missing parent bytes": publication_refuses_wrong_or_missing_parent_bytes,
        "publication refuses a malformed predecessor": publication_refuses_a_malformed_predecessor,
        "publication refuses a wrong ledger predecessor": publication_refuses_a_wrong_ledger_predecessor,
        "publication refuses a wrong parent predecessor": publication_refuses_a_wrong_parent_predecessor,
        "a verified interrupted finalization publishes": a_verified_interrupted_finalization_publishes_and_extends_the_chain,
    }


def check_guard_invariants(mod, root) -> list[str]:
    """Run the whole guard battery against ``mod``; one line per failure."""
    failures: list[str] = []
    for name, check in checks(mod, Path(root)).items():
        try:
            check()
        except BaseException as exc:  # noqa: BLE001 - a failure is the datum
            failures.append(f"{name}: {type(exc).__name__}: {exc}")
    return failures


def run_one(tmp_path, name: str) -> None:
    battery = checks(G, tmp_path)
    assert name in battery, f"unknown invariant {name!r}"
    battery[name]()


def test_the_guard_battery_is_green():
    import tempfile

    with tempfile.TemporaryDirectory() as root:
        failures = check_guard_invariants(G, Path(root))
    assert failures == []


def test_the_classification_vocabulary(tmp_path):
    run_one(tmp_path, "the vocabulary is exactly four states")


def test_a_bound_ledger_without_a_record_is_incomplete(tmp_path):
    run_one(tmp_path, "a bound ledger without a record is never complete")


def test_an_inconsistent_attribution_is_itself_a_refusal(tmp_path):
    run_one(tmp_path, "an inconsistent attribution is itself a refusal")


def test_the_accounting_attribution_splits_settled_from_unsettled(tmp_path):
    run_one(tmp_path, "the attribution splits settled from unsettled")


def test_an_unaccounted_charge_is_quarantined(tmp_path):
    run_one(tmp_path, "an unaccounted charge is quarantined")


def test_equal_totals_redistributed_are_quarantined(tmp_path):
    run_one(tmp_path, "equal totals redistributed are quarantined")


def test_the_fresh_path_refuses_charged_or_bound_evidence(tmp_path):
    run_one(tmp_path, "the fresh path refuses charged or bound evidence")


def test_the_completion_gate_refuses_first(tmp_path):
    run_one(tmp_path, "the completion gate refuses first")


def test_a_fresh_run_writes_nothing(tmp_path):
    run_one(tmp_path, "a fresh run writes nothing")


def test_a_current_record_resume_binds_every_identity(tmp_path):
    run_one(tmp_path, "a current-record resume binds every identity")


def test_an_interrupted_finalization_is_accepted(tmp_path):
    run_one(tmp_path, "an interrupted finalization is accepted")


def test_the_same_resume_preflight_is_stable_twice(tmp_path):
    run_one(tmp_path, "the same resume preflight is stable twice")


def test_every_refusal_leaves_no_trace_and_opens_no_engine(tmp_path):
    run_one(tmp_path, "every refusal leaves no trace or engine")


def test_aliases_are_staged_in_memory_only(tmp_path):
    run_one(tmp_path, "aliases are staged in memory only")


def test_the_full_sequence_completes_and_advances(tmp_path):
    run_one(tmp_path, "the full sequence completes and advances")


def test_publication_refusals(tmp_path):
    run_one(tmp_path, "publication without binding is refused")
    run_one(tmp_path, "a failed gate publishes nothing")
    run_one(tmp_path, "a first run declares no predecessor")


def test_publication_refuses_an_unrelated_new_predecessor(tmp_path):
    """A digest the caller invented, or a file that is not a continuation."""
    run_one(tmp_path, "publication refuses an unrelated new predecessor")


def test_publication_refuses_an_older_in_chain_predecessor(tmp_path):
    """An older chain entry, and a chain that does not end at its binding."""
    run_one(tmp_path, "publication refuses an older in-chain predecessor")


def test_publication_refuses_a_wrong_named_ledger(tmp_path):
    """A record whose ``ledger_file`` is not the ledger it is published to."""
    run_one(tmp_path, "publication refuses a wrong named ledger")


def test_publication_refuses_wrong_or_missing_parent_bytes(tmp_path):
    """A parent board whose bytes, or whose directory, are not what it names."""
    run_one(tmp_path, "publication refuses wrong or missing parent bytes")


def test_publication_refuses_a_malformed_predecessor(tmp_path):
    """The supplied predecessor must be a completed campaign record, not JSON."""
    run_one(tmp_path, "publication refuses a malformed predecessor")


def test_publication_refuses_a_wrong_ledger_predecessor(tmp_path):
    """The supplied predecessor must name the ledger it is published against."""
    run_one(tmp_path, "publication refuses a wrong ledger predecessor")


def test_publication_refuses_a_wrong_parent_predecessor(tmp_path):
    """The supplied predecessor must carry the parent bytes it declares."""
    run_one(tmp_path, "publication refuses a wrong parent predecessor")


def test_publication_accepts_a_verified_interrupted_finalization(tmp_path):
    """The lawful path the correction must retain."""
    run_one(tmp_path, "a verified interrupted finalization publishes")


def test_the_pure_helpers_are_not_a_route_permission(tmp_path):
    """A pure verdict is accounting only; the composed verdict is the authority.

    The whole point of keeping the pure helpers low-level is that their
    ``resume_safe``/``complete`` flags can be true for a ledger whose *identity*
    has not been checked. This test pins that separation explicitly.
    """
    built = case(tmp_path / "separation", charges=[(KEY_A, 1)], bound="record")
    pure = G.preflight_ledger(built["ledger"], resuming=True,
                              record=built["record"])
    assert pure["resume_safe"] is True
    # The same accounting state with the record's parent bytes altered: the pure
    # helper is unchanged, because it never read them.
    built["board"].write_bytes(b"different parent bytes\n")
    still_pure = G.preflight_ledger(built["ledger"], resuming=True,
                                    record=built["record"])
    assert still_pure["resume_safe"] is True
    expect_raises(G.CampaignPreflightRefusal,
                  G.interruption_guard_preflight_campaign,
                  ledger_path=built["ledger_path"], resuming=True,
                  record_path=built["record_path"])
    assert "preflight_ledger" not in agent_pkg.__all__
    assert "classify_ledger" not in agent_pkg.__all__
    assert "charge_attribution" not in agent_pkg.__all__
    assert "interruption_guard_preflight_campaign" in agent_pkg.__all__


#: ``(name, old, new)``. Each ``old`` must occur exactly once in the source.
MUTATIONS = (
    ("the classification stops reconciling the record against the ledger",
     "                            reconcile_resume_charges(record, ledger)\n",
     "                            pass\n"),
    ("a bound ledger reports complete without a validated record",
     "    complete = (status == STATUS_FRESH\n"
     "                or (status == STATUS_ACTIVE and validated))",
     "    complete = status in (STATUS_FRESH, STATUS_ACTIVE)"),
    ("an inconsistent attribution is not itself a refusal",
     '                    if attribution["consistent"] is False:',
     "                    if False:"),
    ("the fresh path adopts a bound ledger",
     "    if ledger.campaign is not None:",
     "    if False:"),
    ("the fresh path adopts charged evidence",
     '    if classification["status"] != STATUS_FRESH:',
     "    if False:"),
    ("a resume without a record returns a verdict",
     "        if record is None:\n"
     "            raise InterruptedCampaignRefusal(",
     "        if False:\n"
     "            raise InterruptedCampaignRefusal("),
    ("the record's named ledger is not checked",
     "    if named.resolve() != Path(ledger_path).resolve():",
     "    if False:"),
    ("the parent bytes are not checked",
     "    if actual != declared:",
     "    if False:"),
    ("the composed preflight accepts any binding",
     "        if current == record_digest:\n"
     '            binding_kind = "current-record"',
     "        if True:\n"
     '            binding_kind = "current-record"'),
    ("aliases are staged by writing them",
     "            outcome = ledger.declare_alias(alias, canonical, flush=False)",
     "            outcome = ledger.declare_alias(alias, canonical, flush=True)"),
    ("publication is allowed without binding",
     "    if not bind:",
     "    if False:"),
    ("the completion gate does not run before publication",
     "    gate = completed_record_gate(record, ledger)",
     "    gate = {}"),
    ("a first run may publish a record that declares a predecessor",
     "        if predecessor:",
     "        if False:"),
    ("publication does not verify the predecessor's bytes",
     "        if prior_sha256 != predecessor:",
     "        if False:"),
    ("publication accepts a continuation with no predecessor file",
     "        if prior_path is None or not prior_path.is_file():",
     "        if False:"),
    ("publication accepts an older in-chain predecessor",
     "        if predecessor in chain and predecessor != current:",
     "        if False:"),
    ("publication extends a chain that does not end at its binding",
     "        if chain and chain[-1] != current:",
     "        if False:"),
    ("the interrupted-finalization predicate ignores the declared predecessor",
     "                and current == declared_predecessor\n",
     "                and True\n"),
    ("the predecessor record is not validated as a completed record",
     "            validate_campaign_record(prior_record)\n",
     "            pass\n"),
    ("the predecessor record's identity is not checked",
     "            prior_identity = _verify_record_identity(prior_record, ledger_path)\n",
     "            prior_identity = None\n"),
)

MUTATION_DRIVER = '''
import importlib.util, json, sys
from pathlib import Path

sys.path.insert(0, sys.argv[4])


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


battery = load("guard_battery", sys.argv[1])
mutant = load("guard_mutant", sys.argv[2])
failures = battery.check_guard_invariants(mutant, Path(sys.argv[3]))
print(json.dumps({"failures": failures}))
'''


def test_the_guard_battery_rejects_every_mutation(tmp_path):
    source = GUARD_SOURCE.read_text(encoding="utf-8")
    battery_file = Path(__file__).resolve()
    driver = tmp_path / "guard_mutation_driver.py"
    driver.write_text(MUTATION_DRIVER, encoding="utf-8")
    missed: list[str] = []
    for index, (name, old, new) in enumerate(MUTATIONS):
        assert source.count(old) == 1, (
            f"mutation {name!r} matched {source.count(old)} sites of {old!r}")
        work = tmp_path / f"mutation{index:02d}"
        work.mkdir(parents=True, exist_ok=True)
        mutant = work / "interruption_guard.py"
        mutant.write_text(source.replace(old, new, 1), encoding="utf-8")
        completed = subprocess.run(
            [sys.executable, str(driver), str(battery_file), str(mutant),
             str(work / "scratch"), str(REPO_ROOT)],
            capture_output=True, text=True, check=False, cwd=str(REPO_ROOT))
        assert completed.returncode == 0, (
            f"mutation {name!r}: the battery driver failed: "
            f"{completed.stderr.strip()[-2000:]}")
        failures = json.loads(completed.stdout.strip().splitlines()[-1])["failures"]
        if not failures:
            missed.append(name)
    assert missed == [], f"the guard battery missed these mutations: {missed}"


def test_the_guard_module_carries_no_absolute_path_or_task_identifier():
    """A public module must not carry a checkout path or an internal task id.

    Including the private ``sys.path`` insertion the guard's predecessor needed:
    the public guard imports its limiter from the package, so no path appears.
    """
    import re

    source = GUARD_SOURCE.read_text(encoding="utf-8")
    for pattern in (r"/Users/", r"[A-Za-z]:\\", r"\bT\d{2}[A-Z0-9]*\b",
                    r"Helix", r"phase\d\d_", r"sys\.path"):
        assert not re.search(pattern, source), (
            f"the public module matches the forbidden pattern {pattern!r}")
