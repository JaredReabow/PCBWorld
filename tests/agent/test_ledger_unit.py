"""Unit tests for the bounded native-transaction ledger (``pcb_world.agent.ledger``).

CPU-only and engine-free: every check builds a ledger over a temporary path and
works on exact bytes. The approved ceilings, the charge-before-call write-through,
the symmetric alias merge and its rollback, and cumulative/per-link resume
reconciliation are each asserted from a *reloaded* ledger, because the property
that matters is what survives a process boundary.

The last test is a mutation check. ``check_core_invariants`` is written against a
module object rather than a module name, so the same battery can be run against
deliberately broken copies of the source in a subprocess: every mutation in
``MUTATIONS`` must be rejected by the battery, or the mutation test fails. A
silent regression in any of these invariants therefore cannot pass the suite.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from pcb_world.agent import ledger as L

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LEDGER_SOURCE = Path(L.__file__)

KEY_A = ["mem:" + "a" * 16, "mem:" + "b" * 16]
KEY_B = ["mem:" + "c" * 16, "mem:" + "d" * 16]
KEY_C = ["mem:" + "e" * 16, "mem:" + "f" * 16]


def expect_raises(exc, fn, *args, **kwargs):
    """Run ``fn`` and require it to raise ``exc``; anything else is a failure."""
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
    """Require ``exc`` *and* that its message names the reason it was raised.

    Several guards in the reconciliation share one backstop: a wrong total is
    refused whether it arrived as a missing link, a mismatch or a parked charge.
    Asserting on the message is what keeps each check attributable to its own
    guard, so removing that one guard is caught rather than masked by the next.
    """
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


def link_row(link_key, charged, *, stable=None):
    row = {"link_key": list(link_key), "native_transactions_attempted": charged,
           "pair_id": "pair-0", "spec": "step-0"}
    if stable is not None:
        row["stable_identity"] = stable
    return row


def make_record(mod, *, links, ledger_file, run_total=None, cumulative=None,
                parent_dir="/tmp/parent", parent_sha="0" * 64, previous=None,
                history=(), **overrides):
    """A syntactically complete campaign record, overridden by the caller."""
    record = {
        "ran": True,
        "ledger_file": str(ledger_file),
        "final_parent_dir": str(parent_dir),
        "final_parent_board_sha256": parent_sha,
        "previous_record_sha256": previous,
        "native_transactions_total": (
            sum(row["native_transactions_attempted"] for row in links)
            if run_total is None else run_total),
        "attempted_links": len(links),
        "ledger_totals": {
            "native_transactions": (
                sum(row["native_transactions_attempted"] for row in links)
                if cumulative is None else cumulative)},
        "links": [dict(row) for row in links],
        "history": [dict(row) for row in history],
    }
    record.update(overrides)
    return record


def charge(ledger, key, count, *, pair_id="pair-0"):
    """Charge ``count`` tickets on one link, settling each one."""
    for index in range(count):
        ticket = ledger.reserve(key, f"{pair_id}-{index}", f"step-{index}")
        ledger.settle(ticket, started=True, committed=True)
    return ledger.budget(key)


def checks(mod, root: Path) -> dict:
    """The invariant battery, written against a module object.

    Returning ``{name: callable}`` lets a single battery serve the ordinary
    tests and the mutation harness. Each callable raises on a broken invariant
    and returns ``None`` when the invariant holds.
    """
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)

    def path(name: str) -> Path:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    # -- ceilings ---------------------------------------------------------
    def ceilings_are_the_approved_numbers():
        assert mod.MAX_TRANSACTIONS_PER_LINK == 5
        assert mod.MAX_ANCHOR_PAIRS_PER_LINK == 12
        assert mod.MAX_LINKS_PER_CAMPAIGN == 12
        assert mod.MAX_TRANSACTIONS_PER_CAMPAIGN == 60

    def a_requested_ceiling_may_only_tighten():
        for name, enforce, approved in (
            ("transaction", mod.enforce_transaction_ceiling, 5),
            ("anchor pair", mod.enforce_anchor_pair_ceiling, 12),
            ("link", mod.enforce_link_ceiling, 12),
        ):
            assert enforce(approved) == approved
            assert enforce(1) == 1
            expect_raises(mod.LinkCeilingError, enforce, approved + 1)
            expect_raises(mod.LinkCeilingError, enforce, 0)
            expect_raises(mod.LinkCeilingError, enforce, -1)
            expect_raises(mod.LinkStateCorruption, enforce, True)
            expect_raises(mod.LinkStateCorruption, enforce, "3")

    def campaign_totals_are_absolute_not_scaled_by_the_link_count():
        assert mod.enforce_campaign_totals(12, 60) == {
            "links": 12, "native_transactions": 60}
        expect_raises(mod.LinkCeilingError, mod.enforce_campaign_totals, 12, 61)
        expect_raises(mod.LinkCeilingError, mod.enforce_campaign_totals, 13, 0)
        expect_raises(mod.LinkStateCorruption, mod.enforce_campaign_totals, -1, 0)
        # A raised per-link ceiling must not raise the campaign ceiling with it.
        expect_raises(mod.LinkCeilingError, mod.enforce_campaign_totals, 12, 144)

    def the_sixth_transaction_on_one_link_is_unrepresentable():
        target = path("sixth/ledger.json")
        ledger = mod.LinkLedger(target)
        charge(ledger, KEY_A, 5)
        expect_raises(mod.LinkTransactionLimit, ledger.reserve, KEY_A, "p9", "s9")
        assert ledger.total_attempted() == 5
        reloaded = mod.LinkLedger.load(target, flush=False)
        assert reloaded.total_attempted() == 5
        assert reloaded.budget(KEY_A).attempted == 5

    def the_anchor_pair_ceiling_counts_distinct_pairs():
        target = path("pairs/ledger.json")
        ledger = mod.LinkLedger(target)
        budget = ledger.budget(KEY_A)
        for index in range(12):
            assert budget.offer_pair(f"pair-{index}") is True
        assert budget.offer_pair("pair-0") is True, "a repeat is not a new pair"
        assert budget.offer_pair("pair-12") is False
        assert len(budget.pairs) == 12

    def the_campaign_link_ceiling_is_enforced():
        target = path("links/ledger.json")
        ledger = mod.LinkLedger(target)
        for index in range(12):
            ledger.budget(["mem:" + f"{index:02d}" * 8, "mem:" + f"{index:02d}" * 8 + "ff"])
        assert len(ledger.budgets) == 12
        expect_raises(mod.LinkCeilingError, ledger.budget,
                      ["mem:" + "9" * 16, "mem:" + "8" * 16])

    def a_tighter_campaign_total_still_bounds_the_run():
        target = path("total/ledger.json")
        ledger = mod.LinkLedger(target, max_transactions_campaign=3)
        charge(ledger, KEY_A, 3)
        expect_raises(mod.LinkCeilingError, ledger.reserve, KEY_B, "p", "s")
        assert ledger.total_attempted() == 3

    # -- persistence and resume ------------------------------------------
    def the_charge_is_written_before_the_call_returns():
        target = path("write-through/ledger.json")
        ledger = mod.LinkLedger(target)
        ledger.reserve(KEY_A, "pair-0", "step-0")
        on_disk = json.loads(target.read_text(encoding="utf-8"))
        assert on_disk["totals"]["native_transactions"] == 1
        assert next(iter(on_disk["links"].values()))["attempted"] == 1

    def a_budget_driven_directly_still_persists_its_charge():
        target = path("direct/ledger.json")
        ledger = mod.LinkLedger(target)
        budget = ledger.budget(KEY_A)
        budget.reserve("pair-0", "step-0")
        on_disk = json.loads(target.read_text(encoding="utf-8"))
        assert next(iter(on_disk["links"].values()))["attempted"] == 1

    def a_resume_continues_the_counter_and_never_resets_it():
        target = path("resume/ledger.json")
        ledger = mod.LinkLedger(target)
        charge(ledger, KEY_A, 3)
        first = ledger.settle(ledger.reserve(KEY_A, "pair-3", "step-3"),
                              started=True, committed=True)
        ledger.flush()
        resumed = mod.LinkLedger.load(target)
        assert resumed.budget(KEY_A).attempted == 4
        assert [row["ordinal"] for row in resumed.budget(KEY_A).attempts] == [1, 2, 3, 4]
        assert first["ordinal"] == 4
        # An exhausted link makes no further call on any later resume.
        charge(resumed, KEY_A, 1)
        again = mod.LinkLedger.load(target, flush=False)
        assert again.budget(KEY_A).exhausted is True
        expect_raises(mod.LinkTransactionLimit, again.reserve, KEY_A, "p", "s")

    def a_raised_persisted_ceiling_is_refused_on_load_and_on_use():
        target = path("raise/ledger.json")
        ledger = mod.LinkLedger(target)
        ledger.reserve(KEY_A, "pair-0", "step-0")
        payload = json.loads(target.read_text(encoding="utf-8"))
        next(iter(payload["links"].values()))["max_transactions"] = 6
        target.write_text(json.dumps(payload), encoding="utf-8")
        expect_raises(mod.LinkCeilingError, mod.LinkLedger.load, target)
        # A ceiling mutated on a live object is re-checked at the next use. The
        # check is driven on the budget directly: the ledger's own ``budget()``
        # would tighten the object back to the approved value first.
        fresh = mod.LinkLedger(path("raise/live.json"))
        budget = fresh.budget(KEY_A, max_transactions=5)
        budget.max_transactions = 6
        expect_raises(mod.LinkCeilingError, budget.reserve, "p", "s")
        # and the ledger cannot be asked for a looser ceiling in the first place
        expect_raises(mod.LinkCeilingError, fresh.budget, KEY_A, max_transactions=6)

    def settled_receipts_cannot_exceed_the_charged_counter():
        target = path("receipts/ledger.json")
        ledger = mod.LinkLedger(target)
        ledger.reserve(KEY_A, "pair-0", "step-0")
        payload = json.loads(target.read_text(encoding="utf-8"))
        budget = next(iter(payload["links"].values()))
        budget["attempts"] = [{"ordinal": 1, "pair_id": "p", "spec": "s"},
                              {"ordinal": 1, "pair_id": "p", "spec": "s"}]
        target.write_text(json.dumps(payload), encoding="utf-8")
        expect_raises(mod.LinkStateCorruption, mod.LinkLedger.load, target)

    def a_missing_ledger_is_refused_for_a_resume_but_allowed_for_a_fresh_run():
        missing = path("absent/ledger.json")
        expect_raises(mod.LinkStateCorruption, mod.LinkLedger.load, missing,
                      require_existing=True)
        fresh = mod.LinkLedger.load(missing)
        assert fresh.total_attempted() == 0
        assert not missing.parent.exists() or not missing.exists()

    def the_expected_campaign_binding_is_verified_on_load():
        target = path("binding/ledger.json")
        ledger = mod.LinkLedger(target, campaign={"campaign_record": "a" * 64})
        ledger.flush()
        loaded = mod.LinkLedger.load(
            target, expect_campaign={"campaign_record": "a" * 64})
        assert loaded.campaign_binding()["campaign_record"] == "a" * 64
        expect_raises(mod.LinkStateCorruption, mod.LinkLedger.load, target,
                      expect_campaign={"campaign_record": "b" * 64})
        unbound = mod.LinkLedger(path("binding/unbound.json"))
        unbound.flush()
        expect_raises(mod.LinkStateCorruption, mod.LinkLedger.load, unbound.path,
                      expect_campaign={"campaign_record": "a" * 64})

    def a_refused_schema_or_a_malformed_file_is_refused_by_name():
        for name, payload in (
            ("schema-1", {"schema": mod.REFUSED_SCHEMA, "links": {}}),
            ("schema-9", {"schema": "t30l-link-ledger/9", "links": {}}),
            ("no-links", {"schema": mod.LEDGER_SCHEMA}),
            ("not-an-object", [1, 2, 3]),
        ):
            target = path(f"schema/{name}.json")
            target.write_text(json.dumps(payload), encoding="utf-8")
            expect_raises(mod.LinkStateCorruption, mod.LinkLedger.load, target)

    # -- identity and aliases ---------------------------------------------
    def a_link_identity_is_order_independent_and_net_code_independent():
        assert mod.stable_identity(KEY_A) == mod.stable_identity(list(reversed(KEY_A)))
        assert (mod.stable_identity(["net:9:mem:" + "a" * 16, "net:2:mem:" + "b" * 16])
                == mod.stable_identity(["net:1:mem:" + "a" * 16,
                                        "net:7:mem:" + "b" * 16]))
        expect_raises(mod.LinkStateCorruption, mod.stable_identity, ["only-one"])
        expect_raises(mod.LinkIdentityConflict, mod.stable_identity, ["x", "x"])

    def an_alias_declaration_is_symmetric_and_resolves_both_ways():
        aliases = mod.AliasMap()
        aliases.add("alpha", "beta")
        assert aliases.equivalent("beta", "alpha")
        assert aliases.resolve("alpha") == aliases.resolve("beta")
        assert mod.stable_identity(["alpha", "gamma"], aliases) == \
            mod.stable_identity(["beta", "gamma"], aliases)
        # A chain unions rather than double-books, and a self-alias is a no-op.
        aliases.add("beta", "gamma")
        assert aliases.equivalent("alpha", "gamma")
        aliases.add("gamma", "gamma")
        assert aliases.resolve("gamma") == aliases.resolve("alpha")

    def a_declared_alias_merges_the_budgets_it_joins_conserving_everything():
        target = path("alias/ledger.json")
        ledger = mod.LinkLedger(target)
        ledger.budget(["alpha", "gamma"]).offer_pair("pair-x")
        ledger.budget(["beta", "gamma"]).offer_pair("pair-y")
        charge(ledger, ["alpha", "gamma"], 2)
        charge(ledger, ["beta", "gamma"], 1)
        # a terminal-pair offer recorded against the link that made it, which a
        # merge must not move, share or reset either
        ledger.note_pair(["alpha", "gamma"], "pair-x")
        ledger.note_pair(["beta", "gamma"], "pair-y")
        before = (ledger.total_attempted(), ledger.total_pairs(), len(ledger.budgets))
        outcome = ledger.declare_alias("alpha", "beta")
        assert outcome["conserved"] is True
        assert len(outcome["merged"]) == 1
        assert (ledger.total_attempted(), ledger.total_pairs()) == (before[0], before[1])
        assert len(ledger.budgets) == before[2] - 1
        merged = next(iter(ledger.budgets.values()))
        assert merged.attempted == 3
        assert sorted(merged.pairs) == ["pair-x", "pair-y"]
        assert sorted(row["ordinal"] for row in merged.attempts) == [1, 1, 2]
        assert sorted(ledger.pair_links) == ["pair-x", "pair-y"]
        reloaded = mod.LinkLedger.load(target, flush=False)
        assert reloaded.total_attempted() == 3
        assert len(reloaded.budgets) == 1

    def a_merge_that_would_break_a_ceiling_is_refused_and_leaves_nothing_staged():
        target = path("alias-refused/ledger.json")
        ledger = mod.LinkLedger(target)
        charge(ledger, ["alpha", "gamma"], 3)
        charge(ledger, ["beta", "gamma"], 3)
        before_aliases = dict(ledger.aliases.parent)
        before_identities = sorted(ledger.budgets)
        before_charges = ledger.total_attempted()
        expect_raises(mod.LinkCeilingError, ledger.declare_alias, "alpha", "beta")
        assert dict(ledger.aliases.parent) == before_aliases
        assert sorted(ledger.budgets) == before_identities
        assert ledger.total_attempted() == before_charges == 6
        assert not ledger.aliases.equivalent("alpha", "beta")

    def a_merge_cannot_combine_two_different_retained_joins():
        target = path("alias-retained/ledger.json")
        ledger = mod.LinkLedger(target)
        left = ledger.budget(["alpha", "gamma"])
        right = ledger.budget(["beta", "gamma"])
        left.retained = "wire-a"
        right.retained = "wire-b"
        expect_raises(mod.LinkIdentityConflict, ledger.declare_alias,
                      "alpha", "beta")
        assert not ledger.aliases.equivalent("alpha", "beta")

    # -- resume reconciliation --------------------------------------------
    def reconciliation_refuses_every_way_a_record_can_disagree():
        target = path("reconcile/ledger.json")
        ledger = mod.LinkLedger(target)
        charge(ledger, KEY_A, 2)
        charge(ledger, KEY_B, 1)
        good = make_record(mod, links=[link_row(KEY_A, 2), link_row(KEY_B, 1)],
                           ledger_file=target)
        mod.reconcile_resume_charges(good, ledger)
        assert mod.recorded_charges(good, ledger.aliases) == {
            mod.stable_identity(KEY_A): 2, mod.stable_identity(KEY_B): 1}
        # a link the history charges but the ledger does not hold
        missing = make_record(mod, links=[link_row(KEY_A, 2), link_row(KEY_B, 1),
                                          link_row(KEY_C, 1)], ledger_file=target)
        expect_refusal(mod.LinkStateCorruption, "holds no such link",
                       mod.reconcile_resume_charges, missing, ledger)
        # charges redistributed between links, same total
        moved = make_record(mod, links=[link_row(KEY_A, 1), link_row(KEY_B, 2)],
                            ledger_file=target)
        expect_refusal(mod.LinkStateCorruption, "accounts for",
                       mod.reconcile_resume_charges, moved, ledger)
        # a charge parked on a link no row of the record names at all
        parked = make_record(mod, links=[link_row(KEY_A, 2)], ledger_file=target)
        expect_refusal(mod.LinkStateCorruption, "no record accounts for",
                       mod.reconcile_resume_charges, parked, ledger)
        # the declared cumulative total disagreeing with the record's own rows
        wrong_total = make_record(mod, links=[link_row(KEY_A, 2), link_row(KEY_B, 1)],
                                  ledger_file=target, cumulative=9)
        expect_refusal(mod.LinkStateCorruption, "ledger totals declare",
                       mod.validate_campaign_record, wrong_total)
        # a record whose run total disagrees with its own rows
        wrong_run = make_record(mod, links=[link_row(KEY_A, 2), link_row(KEY_B, 1)],
                                ledger_file=target, run_total=0, cumulative=3)
        expect_refusal(mod.LinkStateCorruption, "link records account for",
                       mod.validate_campaign_record, wrong_run)

    def a_persisted_receipt_must_fit_the_charged_counter():
        target = path("ordinal/ledger.json")
        ledger = mod.LinkLedger(target, campaign={"campaign_record": "c" * 64})
        charge(ledger, KEY_A, 2)
        payload = json.loads(target.read_text(encoding="utf-8"))
        budget = next(iter(payload["links"].values()))
        budget["attempts"] = [dict(budget["attempts"][0]), dict(budget["attempts"][1])]
        budget["attempts"][1]["ordinal"] = 5
        target.write_text(json.dumps(payload), encoding="utf-8")
        expect_refusal(mod.LinkStateCorruption, "do not fit the charged counter",
                       mod.LinkLedger.load, target)
        # the unmodified file still loads, so the check is not vacuous
        payload["links"][next(iter(payload["links"]))]["attempts"][1]["ordinal"] = 2
        target.write_text(json.dumps(payload), encoding="utf-8")
        assert mod.LinkLedger.load(target, flush=False).total_attempted() == 2

    def a_record_is_validated_field_by_field():
        target = path("validate/ledger.json")
        ledger = mod.LinkLedger(target)
        charge(ledger, KEY_A, 1)
        good = make_record(mod, links=[link_row(KEY_A, 1)], ledger_file=target)
        summary = mod.validate_campaign_record(good)
        assert summary == {"run_total": 1, "cumulative": 1, "links_attempted": 1,
                           "link_records": 1}
        for field, value in (
            ("ran", False), ("ledger_file", ""), ("ledger_file", None),
            ("final_parent_dir", None), ("final_parent_board_sha256", "short"),
            ("previous_record_sha256", "short"), ("native_transactions_total", 61),
            ("native_transactions_total", True), ("attempted_links", 13),
            ("ledger_totals", None), ("links", "not-a-list"),
        ):
            expect_raises(mod.LinkStateCorruption, mod.validate_campaign_record,
                          {**good, field: value})
        # the record's own run total must match its own link rows
        expect_raises(mod.LinkStateCorruption, mod.validate_campaign_record,
                      {**good, "native_transactions_total": 0})
        expect_raises(mod.LinkStateCorruption, mod.validate_campaign_record,
                      {**good, "attempted_links": 0})
        # a stamped identity that contradicts the canonical form is a conflict
        stamped = make_record(mod, links=[link_row(KEY_A, 1, stable="mem:" + "9" * 16)],
                              ledger_file=target)
        expect_raises(mod.LinkIdentityConflict, mod.recorded_charges, stamped)

    def the_successor_binding_is_guarded_and_extends_the_chain_in_order():
        target = path("successor/ledger.json")
        ledger = mod.LinkLedger(target)
        ledger.bind_campaign({"campaign_record": "1" * 64})
        ledger.advance_campaign("2" * 64, previous_record="1" * 64)
        assert ledger.campaign_binding()["campaign_records"] == ["1" * 64, "2" * 64]
        # an unrelated predecessor, and a repeat, are both refused
        expect_raises(mod.LinkStateCorruption, ledger.advance_campaign,
                      "3" * 64, previous_record="9" * 64)
        expect_raises(mod.LinkStateCorruption, ledger.advance_campaign,
                      "1" * 64, previous_record="2" * 64)
        # an interrupted finalisation extends the chain with the record that was
        # written but never bound, before the new record
        ledger.advance_campaign("3" * 64, previous_record="2" * 64)
        ledger.advance_campaign("4" * 64, previous_record="3" * 64,
                                head_predecessor="3" * 64)
        assert ledger.campaign_binding()["campaign_records"] == [
            "1" * 64, "2" * 64, "3" * 64, "4" * 64]
        unbound = mod.LinkLedger(path("successor/unbound.json"))
        expect_raises(mod.LinkStateCorruption, unbound.advance_campaign,
                      "5" * 64, previous_record="4" * 64)

    def run_link_budget_makes_exactly_one_call_per_charge_and_stops_at_the_ceiling():
        target = path("loop/ledger.json")
        ledger = mod.LinkLedger(target)
        calls = []

        def attempt_fn(pair, spec, ticket):
            calls.append((pair, spec, ticket.ordinal))
            return {"started": True, "committed": True, "retain": False}

        pairs = [f"pair-{index}" for index in range(12)]
        outcome = mod.run_link_budget(
            ledger=ledger, link_key=KEY_A, pairs=pairs,
            ladder=["only"], attempt_fn=attempt_fn, pair_id=lambda pair: pair)
        assert outcome["native_calls"] == 5
        assert len(calls) == 5
        assert ledger.total_attempted() == 5
        assert outcome["charges_after"] == 5
        assert outcome["refused"] is True, "an exhausted link retains nothing"
        assert sum(len(pair["attempts"]) for pair in outcome["pairs"]) == 5
        # An exhausted link makes no call at all on the next pass.
        calls.clear()
        again = mod.run_link_budget(
            ledger=ledger, link_key=KEY_A, pairs=pairs,
            ladder=["only"], attempt_fn=attempt_fn, pair_id=lambda pair: pair)
        assert calls == []
        assert again["refused"] is True
        assert again["stop_reason"] == "transaction budget exhausted on resume"

    return {
        "the four ceilings are the approved numbers": ceilings_are_the_approved_numbers,
        "a requested ceiling may only tighten": a_requested_ceiling_may_only_tighten,
        "campaign totals are absolute": campaign_totals_are_absolute_not_scaled_by_the_link_count,
        "the sixth transaction on one link is unrepresentable": the_sixth_transaction_on_one_link_is_unrepresentable,
        "the anchor-pair ceiling counts distinct pairs": the_anchor_pair_ceiling_counts_distinct_pairs,
        "the campaign link ceiling is enforced": the_campaign_link_ceiling_is_enforced,
        "a tighter campaign total bounds the run": a_tighter_campaign_total_still_bounds_the_run,
        "the charge is written before the call returns": the_charge_is_written_before_the_call_returns,
        "a directly driven budget persists its charge": a_budget_driven_directly_still_persists_its_charge,
        "a resume continues the counter, never resets it": a_resume_continues_the_counter_and_never_resets_it,
        "a raised persisted ceiling is refused": a_raised_persisted_ceiling_is_refused_on_load_and_on_use,
        "settled receipts cannot exceed the counter": settled_receipts_cannot_exceed_the_charged_counter,
        "a missing ledger is refused for a resume": a_missing_ledger_is_refused_for_a_resume_but_allowed_for_a_fresh_run,
        "the expected campaign binding is verified": the_expected_campaign_binding_is_verified_on_load,
        "a refused schema is refused by name": a_refused_schema_or_a_malformed_file_is_refused_by_name,
        "an identity is order and net-code independent": a_link_identity_is_order_independent_and_net_code_independent,
        "an alias is symmetric and resolves both ways": an_alias_declaration_is_symmetric_and_resolves_both_ways,
        "an alias merge conserves charges and pairs": a_declared_alias_merges_the_budgets_it_joins_conserving_everything,
        "a ceiling-breaking merge rolls back": a_merge_that_would_break_a_ceiling_is_refused_and_leaves_nothing_staged,
        "a merge cannot combine two retained joins": a_merge_cannot_combine_two_different_retained_joins,
        "reconciliation refuses every disagreement": reconciliation_refuses_every_way_a_record_can_disagree,
        "a persisted receipt must fit the counter": a_persisted_receipt_must_fit_the_charged_counter,
        "a record is validated field by field": a_record_is_validated_field_by_field,
        "the successor binding is guarded": the_successor_binding_is_guarded_and_extends_the_chain_in_order,
        "run_link_budget charges one call per ticket": run_link_budget_makes_exactly_one_call_per_charge_and_stops_at_the_ceiling,
    }


def check_core_invariants(mod, root) -> list[str]:
    """Run the whole battery against ``mod``; return one line per failure."""
    failures: list[str] = []
    for name, check in checks(mod, Path(root)).items():
        try:
            check()
        except BaseException as exc:  # noqa: BLE001 - a failure is the datum
            failures.append(f"{name}: {type(exc).__name__}: {exc}")
    return failures


def run_one(tmp_path, name: str) -> None:
    """Run one named invariant against the shipped module."""
    battery = checks(L, tmp_path)
    assert name in battery, f"unknown invariant {name!r}"
    battery[name]()


def test_the_invariant_battery_is_green():
    import tempfile

    with tempfile.TemporaryDirectory() as root:
        failures = check_core_invariants(L, Path(root))
    assert failures == []


def test_the_approved_ceilings_are_exactly_the_approved_numbers(tmp_path):
    run_one(tmp_path, "the four ceilings are the approved numbers")


def test_a_requested_ceiling_may_only_tighten(tmp_path):
    run_one(tmp_path, "a requested ceiling may only tighten")


def test_campaign_totals_are_absolute(tmp_path):
    run_one(tmp_path, "campaign totals are absolute")


def test_the_sixth_transaction_is_unrepresentable(tmp_path):
    run_one(tmp_path, "the sixth transaction on one link is unrepresentable")


def test_the_anchor_pair_ceiling_counts_distinct_pairs(tmp_path):
    run_one(tmp_path, "the anchor-pair ceiling counts distinct pairs")


def test_the_campaign_link_ceiling_is_enforced(tmp_path):
    run_one(tmp_path, "the campaign link ceiling is enforced")


def test_the_charge_is_persisted_before_the_call(tmp_path):
    run_one(tmp_path, "the charge is written before the call returns")
    run_one(tmp_path, "a directly driven budget persists its charge")


def test_a_resume_continues_the_counter(tmp_path):
    run_one(tmp_path, "a resume continues the counter, never resets it")


def test_a_raised_ceiling_is_refused(tmp_path):
    run_one(tmp_path, "a raised persisted ceiling is refused")


def test_a_missing_ledger_is_refused_for_a_resume(tmp_path):
    run_one(tmp_path, "a missing ledger is refused for a resume")


def test_the_campaign_binding_is_verified_on_load(tmp_path):
    run_one(tmp_path, "the expected campaign binding is verified")


def test_an_alias_is_symmetric_and_conserves_charges(tmp_path):
    run_one(tmp_path, "an alias is symmetric and resolves both ways")
    run_one(tmp_path, "an alias merge conserves charges and pairs")
    run_one(tmp_path, "a ceiling-breaking merge rolls back")
    run_one(tmp_path, "a merge cannot combine two retained joins")


def test_reconciliation_refuses_every_disagreement(tmp_path):
    run_one(tmp_path, "reconciliation refuses every disagreement")


def test_a_persisted_receipt_must_fit_the_counter(tmp_path):
    run_one(tmp_path, "a persisted receipt must fit the counter")


def test_a_record_is_validated_field_by_field(tmp_path):
    run_one(tmp_path, "a record is validated field by field")


def test_the_successor_binding_is_guarded(tmp_path):
    run_one(tmp_path, "the successor binding is guarded")


def test_run_link_budget_charges_one_call_per_ticket(tmp_path):
    run_one(tmp_path, "run_link_budget charges one call per ticket")


#: ``(name, old, new)``. Each ``old`` must occur exactly once in the source; each
#: replacement must break at least one invariant in the battery above.
MUTATIONS = (
    ("sixth-call guard removed",
     "        if self.attempted >= self.max_transactions:\n"
     "            raise LinkTransactionLimit(",
     "        if False:\n"
     "            raise LinkTransactionLimit("),
    ("a persisted receipt ordinal no longer has to fit the counter",
     "        if ordinals and (min(ordinals) < 1 or max(ordinals) > self.attempted):",
     "        if False:"),
    ("a direct budget charge is not persisted",
     "        self.attempted += 1\n        self._persist()\n",
     "        self.attempted += 1\n"),
    ("the anchor-pair ceiling is removed",
     "        if len(self.pairs) >= self.max_anchor_pairs:",
     "        if False:"),
    ("the campaign link ceiling is removed",
     "        if len(self.budgets) >= self.max_links:",
     "        if False:"),
    ("the campaign total is scaled by the link ceiling",
     "    transactions = _validated_total(\n"
     "        transactions_attempted, MAX_TRANSACTIONS_PER_CAMPAIGN,\n"
     '        "native transactions attempted in the campaign")',
     "    transactions = _validated_total(\n"
     "        transactions_attempted,\n"
     "        MAX_LINKS_PER_CAMPAIGN * MAX_LINKS_PER_CAMPAIGN,\n"
     '        "native transactions attempted in the campaign")'),
    ("the identity becomes order dependent",
     "    left, right = sorted(resolved)",
     "    left, right = (resolved[0], resolved[-1])"),
    ("the net code is kept in the identity",
     '        return f"mem:{match.group(2).lower()}"',
     "        return text"),
    ("a declared alias map is ignored on resolve",
     "        return self._find(key)",
     "        return key"),
    ("an alias declaration does not merge existing budgets",
     "            reconciliation = self._reconcile()\n",
     '            reconciliation = {"merged": [], "moved": []}\n'),
    ("an alias declaration does not roll back the map",
     "            self.aliases.parent = aliases_before\n",
     ""),
    ("a missing ledger is accepted for a resume",
     "            if require_existing:\n",
     "            if False:\n"),
    ("the campaign binding is not verified on load",
     "                if recorded != value:\n",
     "                if False:\n"),
    ("a persisted ceiling is not re-validated at the next use",
     "        self.check_ceilings()\n"
     "        if self.attempted >= self.max_transactions:\n"
     "            raise LinkTransactionLimit(",
     "        if self.attempted >= self.max_transactions:\n"
     "            raise LinkTransactionLimit("),
    ("a missing recorded link is ignored",
     "    if missing:\n",
     "    if False:\n"),
    ("redistributed charges are ignored",
     "    if mismatched:\n",
     "    if False:\n"),
    ("parked charges are ignored",
     "    if parked:\n",
     "    if False:\n"),
    ("the successor binding is not guarded",
     "        if current == previous_record:\n            extension = []\n"
     "        elif (head_predecessor is not None",
     "        if True:\n            extension = []\n"
     "        elif (head_predecessor is not None"),
    ("the successor binding does not extend the chain",
     '            "campaign_records": chain + extension + [str(record_digest)],',
     '            "campaign_records": [str(record_digest)],'),
)

#: The subprocess driver: load a mutated module and the battery by path, then
#: report the battery's failures as JSON on stdout.
MUTATION_DRIVER = '''
import importlib.util, json, os, sys
from pathlib import Path

sys.path.insert(0, sys.argv[4])


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


battery = load("ledger_battery", sys.argv[1])
mutant = load("ledger_mutant", sys.argv[2])
failures = battery.check_core_invariants(mutant, Path(sys.argv[3]))
print(json.dumps({"failures": failures}))
'''


def test_the_invariant_battery_rejects_every_mutation(tmp_path):
    """Every mutation of the ledger must break at least one invariant."""
    source = LEDGER_SOURCE.read_text(encoding="utf-8")
    battery_file = Path(__file__).resolve()
    driver = tmp_path / "mutation_driver.py"
    driver.write_text(MUTATION_DRIVER, encoding="utf-8")
    missed: list[str] = []
    for index, (name, old, new) in enumerate(MUTATIONS):
        assert source.count(old) == 1, (
            f"mutation {name!r} matched {source.count(old)} sites of {old!r}")
        work = tmp_path / f"mutation{index:02d}"
        work.mkdir(parents=True, exist_ok=True)
        mutant = work / "ledger_mutant.py"
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
    assert missed == [], f"the invariant battery missed these mutations: {missed}"


def test_the_persisted_schema_tags_are_unchanged(tmp_path):
    """A rename would silently re-interpret a spent counter, so pin the tags."""
    assert L.LEDGER_SCHEMA == "t30l-link-ledger/2"
    assert L.REFUSED_SCHEMA == "t30l-link-ledger/1"
    # A ledger written by the accepted limiter still loads here, byte for byte.
    target = tmp_path / "legacy" / "ledger.json"
    legacy = {
        "schema": "t30l-link-ledger/2",
        "approved_max_transactions_per_link": 5,
        "approved_max_anchor_pairs_per_link": 12,
        "approved_max_links_per_campaign": 12,
        "approved_max_transactions_per_campaign": 60,
        "ceilings": {"max_links": 12, "max_transactions_campaign": 60},
        "campaign": None,
        "aliases": {},
        "links": {
            L.stable_identity(KEY_A): {
                "link_key": list(KEY_A),
                "identity": L.stable_identity(KEY_A),
                "max_transactions": 5,
                "max_anchor_pairs": 12,
                "attempted": 2,
                "pairs": ["pair-0"],
                "attempts": [
                    {"ordinal": 1, "pair_id": "pair-0", "spec": "step-0",
                     "started": True, "committed": True, "retained": False,
                     "seconds": 1.0},
                    {"ordinal": 2, "pair_id": "pair-0", "spec": "step-1",
                     "started": True, "committed": True, "retained": False,
                     "seconds": 1.0},
                ],
                "aliases_seen": [list(KEY_A)],
                "retained": None,
                "stop_reason": None,
            },
        },
        "terminal_pairs": {"pair-0": [L.stable_identity(KEY_A)]},
        "totals": {"links": 1, "terminal_pairs": 1, "native_transactions": 2,
                   "anchor_pairs": 1},
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(legacy), encoding="utf-8")
    loaded = L.LinkLedger.load(target, flush=False)
    assert loaded.total_attempted() == 2
    assert loaded.budget(KEY_A).attempts[1]["spec"] == "step-1"
    # and it re-serialises to the same schema tag
    assert loaded.to_json()["schema"] == "t30l-link-ledger/2"


def test_the_module_carries_no_absolute_path_or_task_identifier():
    """A public module must not carry a checkout path or an internal task id.

    Checked by shape rather than by listing the names: the schema tags below are
    the only internal-looking strings this module is allowed to keep, they are
    lower-case, and they are pinned by the test above.
    """
    import re

    source = LEDGER_SOURCE.read_text(encoding="utf-8")
    for pattern in (r"/Users/", r"[A-Za-z]:\\", r"\bT\d{2}[A-Z0-9]*\b",
                    r"Helix", r"phase\d\d_"):
        assert not re.search(pattern, source), (
            f"the public module matches the forbidden pattern {pattern!r}")
