"""The bounded native-transaction ledger for a routing campaign.

A routing campaign spends *native transactions* - one engine call that actually
moves copper. The budget is a scope rule, not a target: the campaign may spend
how much it was authorised to spend and not one call more. This module is the
book that makes that true across process boundaries.

The contract, all four ceilings hard (not defaults):

* five native transactions per stable link;
* twelve distinct anchor pairs per stable link;
* twelve links per campaign;
* sixty native transactions per campaign.

A caller may ask for a *tighter* value through the campaign's command line and
never a looser one. Every one of those ceilings is re-validated at construction,
when a ledger is restored from disk, and again on every reservation and every
pair offer, so a raised ceiling in a ledger file - or in a mutated object - is
refused rather than honoured.

Three further invariants, in order of strength:

1. ``StableLinkBudget.reserve`` refuses a sixth native transaction outright. It
   is the only way to obtain a :class:`Ticket`, and a ticket is the only licence
   the driver will hand to a native call, so the sixth call cannot be expressed.
2. ``LinkLedger.reserve`` writes the charged counter through to disk *before*
   the caller issues the call, so a crash inside a transaction still charges
   that attempt on the next resume. Charges are cumulative across resumes and
   are never reset.
3. The stable identity is the canonical, order-independent pair of component
   identities, with the harness's net code and case normalised away, and with
   declared aliases forming a *symmetric* equivalence. Declaring an alias merges
   any budget already held under either side, adding the charged counts and
   unioning the anchor sets; a merge that would break a ceiling, or that would
   combine two different retained joins, is refused and leaves the ledger
   unchanged.

Nothing here opens a board, touches an engine or writes a routing artefact: the
native call is supplied by the caller. This file carries no net names,
coordinates or component identifiers beyond what a link key literally is, so it
contains no board geometry.

The persisted schema tags below are carried unchanged from the accepted
limiter this module was promoted from, so a ledger file written by that limiter
loads here, and a spent counter is never re-interpreted by a rename.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

#: Approved ceilings. These are hard caps, not targets.
MAX_TRANSACTIONS_PER_LINK = 5
MAX_ANCHOR_PAIRS_PER_LINK = 12
MAX_LINKS_PER_CAMPAIGN = 12
MAX_TRANSACTIONS_PER_CAMPAIGN = (
    MAX_LINKS_PER_CAMPAIGN * MAX_TRANSACTIONS_PER_LINK)

#: The ledger schema. ``/1`` predates the hard ceilings and the symmetric alias
#: algebra and is refused by name rather than migrated, so a spent counter can
#: never be re-interpreted. ``/2`` needs no migration: the campaign binding
#: added in cycle 3 is an additive field, and loading a ``/2`` file leaves every
#: charged counter, anchor set and receipt exactly as it was written.
LEDGER_SCHEMA = "t30l-link-ledger/2"
REFUSED_SCHEMA = "t30l-link-ledger/1"


class LinkLedgerError(RuntimeError):
    """Base for every refusal this module makes."""


class LinkTransactionLimit(LinkLedgerError):
    """A sixth native transaction was requested for one stable link."""


class LinkCeilingError(LinkTransactionLimit):
    """A requested, constructed or persisted ceiling is illegal or reached."""


class LinkIdentityConflict(LinkLedgerError):
    """A link identity could not be resolved without contradicting itself."""


class LinkStateCorruption(LinkLedgerError):
    """A persisted or passed state is malformed, missing or an overwrite."""


def _validated_count(value, approved: int, name: str) -> int:
    """One legible to one approved ceiling: integers only, tighter only."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise LinkStateCorruption(
            f"{name} must be an integer, not {value!r}")
    if value < 1:
        raise LinkCeilingError(f"{name} of {value} is not usable")
    if value > approved:
        raise LinkCeilingError(
            f"{name} of {value} exceeds the approved {approved}")
    return value


def _validated_total(value, approved: int, name: str) -> int:
    """One non-negative count, never above an approved absolute."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise LinkStateCorruption(
            f"{name} must be an integer, not {value!r}")
    if value < 0:
        raise LinkStateCorruption(f"{name} of {value} is negative")
    if value > approved:
        raise LinkCeilingError(
            f"{name} of {value} exceeds the approved {approved}")
    return value


def enforce_transaction_ceiling(requested) -> int:
    """Validate a requested per-link transaction ceiling: tighter only."""
    return _validated_count(
        requested, MAX_TRANSACTIONS_PER_LINK, "per-link transaction ceiling")


def enforce_anchor_pair_ceiling(requested) -> int:
    """Validate a requested per-link anchor-pair ceiling: tighter only."""
    return _validated_count(
        requested, MAX_ANCHOR_PAIRS_PER_LINK, "per-link anchor-pair ceiling")


def enforce_link_ceiling(requested) -> int:
    """Validate a requested campaign link ceiling: tighter only."""
    return _validated_count(
        requested, MAX_LINKS_PER_CAMPAIGN, "campaign link ceiling")


def enforce_campaign_totals(links_attempted, transactions_attempted) -> dict:
    """Validate what a campaign actually spent against the absolute totals.

    Deliberately absolute: the per-link ceiling times the links *attempted*
    would scale with the link count, which is how a raised ``--max-links`` used
    to raise the transaction budget with it.
    """
    links = _validated_total(links_attempted, MAX_LINKS_PER_CAMPAIGN,
                             "links attempted")
    transactions = _validated_total(
        transactions_attempted, MAX_TRANSACTIONS_PER_CAMPAIGN,
        "native transactions attempted in the campaign")
    return {"links": links, "native_transactions": transactions}


def canonical_component(alias) -> str:
    """The stable form of one component alias.

    The harness names a component ``net:<code>:mem:<digest>``, where the digest
    is over the component's sorted terminal membership and the net code is an
    enumerator's label for it. The membership digest is the part that survives a
    reload; the net code does not, because the engine renumbers nets. So a
    string of that shape is keyed on its digest alone, and anything else is kept
    verbatim apart from surrounding whitespace.
    """
    text = str(alias).strip()
    match = re.match(r"^net:([^:]*):mem:([0-9a-fA-F]+)$", text, re.IGNORECASE)
    if match:
        return f"mem:{match.group(2).lower()}"
    return text


class AliasMap:
    """A symmetric equivalence between component aliases.

    Declaring ``add(a, b)`` makes ``a`` and ``b`` resolve to the same
    representative in *both* directions, and unions their existing equivalence
    classes, so the side of the declaration an operator happens to write can
    never decide whether a spent link is recognised. The representative is the
    lexicographically smallest member, so the map is deterministic.

    Shape alone is not a conflict: a self-equivalence is a no-op, and a chain
    simply unions. What is refused is a merge that would break a ceiling or
    contradict a retained join, and that refusal belongs to the ledger, which
    knows the budgets (see ``LinkLedger.declare_alias``).
    """

    def __init__(self, mapping: dict | None = None) -> None:
        self.parent: dict[str, str] = {}
        for alias, canonical in (mapping or {}).items():
            self.add(alias, canonical)

    def _find(self, key: str) -> str:
        root = key
        while self.parent.get(root, root) != root:
            root = self.parent[root]
        while self.parent.get(key, key) != key:
            self.parent[key], key = root, self.parent[key]
        return root

    def add(self, alias, canonical) -> None:
        left = canonical_component(alias)
        right = canonical_component(canonical)
        self.parent.setdefault(left, left)
        self.parent.setdefault(right, right)
        left_root, right_root = self._find(left), self._find(right)
        if left_root == right_root:
            return
        keep, drop = sorted((left_root, right_root))
        self.parent[drop] = keep

    def resolve(self, token) -> str:
        key = canonical_component(token)
        if key not in self.parent:
            return key
        return self._find(key)

    def equivalent(self, left, right) -> bool:
        return self.resolve(left) == self.resolve(right)

    def resolve_link_key(self, link_key) -> tuple[str, str]:
        return tuple(self.resolve(item) for item in link_key)

    def tokens(self) -> list[str]:
        return sorted(self.parent)

    def state(self) -> dict:
        return {token: self._find(token) for token in self.tokens()}

    @classmethod
    def from_state(cls, state: dict | None) -> "AliasMap":
        mapping = cls()
        if state is None:
            return mapping
        if not isinstance(state, dict):
            raise LinkStateCorruption(
                f"the persisted alias map is not an object: {state!r}")
        for token, root in state.items():
            mapping.add(token, root)
        return mapping


def stable_identity(link_key, aliases: AliasMap | None = None) -> str:
    """The order-independent identity of one logical link.

    ``link_key`` is the two-component key the campaign already uses. Sorting it
    means an enumeration that lists the same components in the other order names
    the same link, so a resume continues the existing counter instead of
    silently starting a second budget. Each component is reduced to its stable
    form first, and resolved through the alias map when one is given, so a
    renumbered net or an equivalent alias names the same link too.
    """
    if not isinstance(link_key, (list, tuple)) or len(link_key) != 2:
        raise LinkStateCorruption(
            f"a link key must name two components, not {link_key!r}")
    if aliases is None:
        resolved = [canonical_component(item) for item in link_key]
    else:
        resolved = [aliases.resolve(item) for item in link_key]
    left, right = sorted(resolved)
    if left == right:
        raise LinkIdentityConflict(
            f"{list(link_key)} resolves to one component ({left}), which is not "
            f"a two-component link")
    return f"{left}|{right}"


def _spec_name(spec) -> str:
    """The declared name of one ladder rung, whether given as a dict or a str."""
    if isinstance(spec, dict):
        return str(spec.get("name"))
    return str(spec)


@dataclass(frozen=True)
class Ticket:
    """The licence for exactly one native transaction.

    ``ordinal`` is the 1-based charge on the link's counter: the first native
    call is 1, the fifth is 5, and a sixth ticket cannot be minted.
    """

    identity: str
    ordinal: int
    pair_id: str
    spec: str


@dataclass
class StableLinkBudget:
    """One stable link's native-transaction budget.

    The counter (``attempted``) sits on the link, not on the anchor pair, so
    switching pairs never resets it. ``pairs`` records the distinct anchor pairs
    offered, bounded by ``max_anchor_pairs``. ``aliases_seen`` records every raw
    key that resolved to this identity, so an alias equivalence is visible in
    the record rather than implied by it.
    """

    link_key: tuple
    max_transactions: int = MAX_TRANSACTIONS_PER_LINK
    max_anchor_pairs: int = MAX_ANCHOR_PAIRS_PER_LINK
    attempted: int = 0
    pairs: list = field(default_factory=list)
    attempts: list = field(default_factory=list)
    aliases_seen: list = field(default_factory=list)
    retained: str | None = None
    stop_reason: str | None = None

    def __post_init__(self) -> None:
        if (not isinstance(self.link_key, (list, tuple))
                or len(self.link_key) != 2):
            raise LinkStateCorruption(
                f"a link key must name two components, not {self.link_key!r}")
        self.link_key = tuple(sorted(canonical_component(item)
                                     for item in self.link_key))
        if self.link_key[0] == self.link_key[1]:
            raise LinkIdentityConflict(
                f"{list(self.link_key)} names one component twice")
        self.check_ceilings()
        self.pairs = [str(item) for item in self.pairs]
        for row in self.attempts:
            if not isinstance(row, dict):
                raise LinkStateCorruption(
                    f"{self.identity}: a persisted receipt is not an object: "
                    f"{row!r}")
        self.attempts = [dict(item) for item in self.attempts]
        self.aliases_seen = [list(item) for item in self.aliases_seen]
        if len(self.attempts) > self.attempted:
            raise LinkStateCorruption(
                f"{self.identity}: {len(self.attempts)} settled receipts exceed "
                f"the charged counter {self.attempted}; refusing an overwrite")
        ordinals = [int(row.get("ordinal") or 0) for row in self.attempts]
        if ordinals and (min(ordinals) < 1 or max(ordinals) > self.attempted):
            raise LinkStateCorruption(
                f"{self.identity}: settled receipt ordinals {sorted(ordinals)} "
                f"do not fit the charged counter {self.attempted}")
        for row in self.attempts:
            ordinal = row.get("ordinal")
            if isinstance(ordinal, bool) or not isinstance(ordinal, int):
                raise LinkStateCorruption(
                    f"{self.identity}: a persisted receipt ordinal is "
                    f"{ordinal!r}, not an integer")
            pair_id = row.get("pair_id")
            if not isinstance(pair_id, str) or not pair_id:
                raise LinkStateCorruption(
                    f"{self.identity}: a persisted receipt has no pair_id")
            spec = row.get("spec")
            if not isinstance(spec, str) or not spec:
                raise LinkStateCorruption(
                    f"{self.identity}: a persisted receipt has no spec")
            for flag in ("started", "committed", "retained"):
                value = row.get(flag)
                if value is not None and not isinstance(value, bool):
                    raise LinkStateCorruption(
                        f"{self.identity}: a persisted receipt has "
                        f"{flag}={value!r}")
        if self.retained is not None and not isinstance(self.retained, str):
            raise LinkStateCorruption(
                f"{self.identity}: retained is {self.retained!r}")

    @property
    def identity(self) -> str:
        return stable_identity(self.link_key)

    @property
    def remaining(self) -> int:
        return max(0, self.max_transactions - self.attempted)

    @property
    def exhausted(self) -> bool:
        return self.attempted >= self.max_transactions

    def check_ceilings(self) -> None:
        """Re-validate the ceilings and the counter.

        Called from the constructor, from ``reserve``, from ``offer_pair`` and
        from ``settle``, so a ceiling raised in the persisted file *or* in a
        mutated object is refused at the next use rather than honoured.
        """
        self.max_transactions = _validated_count(
            self.max_transactions, MAX_TRANSACTIONS_PER_LINK, "max_transactions")
        self.max_anchor_pairs = _validated_count(
            self.max_anchor_pairs, MAX_ANCHOR_PAIRS_PER_LINK, "max_anchor_pairs")
        self.attempted = _validated_total(
            self.attempted, MAX_TRANSACTIONS_PER_LINK, "attempted")
        if len(self.attempts) > self.attempted:
            raise LinkStateCorruption(
                f"{self.identity}: settled receipts exceed the charged counter")

    def note_alias(self, link_key) -> list:
        """Record one raw key that resolved to this identity."""
        seen = [str(item) for item in link_key]
        if seen not in self.aliases_seen:
            self.aliases_seen.append(seen)
        return self.aliases_seen

    def attach(self, on_change) -> None:
        """Attach the owning ledger's write-through, for direct module use.

        The driver charges through ``LinkLedger.reserve``, which flushes before
        the native call. A caller that drives a budget directly would otherwise
        charge without persisting, so a budget that belongs to a ledger carries
        that ledger's flush and every counter change is written through.
        """
        self.on_change = on_change

    def _persist(self) -> None:
        callback = getattr(self, "on_change", None)
        if callback is not None:
            callback()

    def offer_pair(self, pair_id) -> bool:
        """Register one distinct anchor pair; False at the pair ceiling.

        Registering a pair does not spend the transaction counter, and a pair
        already registered is not re-counted, so the ceiling counts distinct
        pairs exactly as "at most twelve unique anchor pairs" intends.
        """
        self.check_ceilings()
        pair_id = str(pair_id)
        if pair_id in self.pairs:
            return True
        if len(self.pairs) >= self.max_anchor_pairs:
            return False
        self.pairs.append(pair_id)
        return True

    def reserve(self, pair_id, spec) -> Ticket:
        """Charge the next native transaction and return its ticket.

        Charged before the call and regardless of its outcome, so a failed
        start still counts. Raises :class:`LinkTransactionLimit` when the fifth
        has already been charged, which makes a sixth call unrepresentable.
        """
        self.check_ceilings()
        if self.attempted >= self.max_transactions:
            raise LinkTransactionLimit(
                f"{self.identity}: {self.attempted} native transactions already "
                f"attempted; the per-link ceiling is {self.max_transactions}")
        self.attempted += 1
        self._persist()
        return Ticket(self.identity, self.attempted, str(pair_id),
                      _spec_name(spec))

    def settle(self, ticket: Ticket, *, started, committed=None,
               retained: bool = False, seconds=None) -> dict:
        """Record the outcome of a charged transaction."""
        self.check_ceilings()
        if not isinstance(ticket, Ticket) or ticket.identity != self.identity:
            raise LinkIdentityConflict(
                f"a ticket for {getattr(ticket, 'identity', None)} cannot be "
                f"settled against {self.identity}")
        if ticket.ordinal < 1 or ticket.ordinal > self.attempted:
            raise LinkIdentityConflict(
                f"{self.identity}: ticket ordinal {ticket.ordinal} is not "
                f"charged against a counter of {self.attempted}")
        if any(int(row.get("ordinal") or 0) == ticket.ordinal
               for row in self.attempts):
            raise LinkIdentityConflict(
                f"{self.identity}: ordinal {ticket.ordinal} is already settled")
        receipt = {
            "ordinal": ticket.ordinal,
            "pair_id": ticket.pair_id,
            "spec": ticket.spec,
            "started": bool(started),
            "committed": None if committed is None else bool(committed),
            "retained": bool(retained),
            "seconds": seconds,
        }
        self.attempts.append(receipt)
        # Defensive on the key: a receipt that arrived through the public API is
        # always well formed, and every persisted one is validated on load.
        self.attempts.sort(key=lambda row: (int(row.get("ordinal") or 0),
                                            str(row.get("pair_id") or ""),
                                            str(row.get("spec") or "")))
        if retained:
            self.retained = ticket.spec
        self._persist()
        return receipt

    def state(self) -> dict:
        return {
            "link_key": list(self.link_key),
            "identity": self.identity,
            "max_transactions": self.max_transactions,
            "max_anchor_pairs": self.max_anchor_pairs,
            "attempted": self.attempted,
            "pairs": list(self.pairs),
            "attempts": [dict(item) for item in self.attempts],
            "aliases_seen": [list(item) for item in self.aliases_seen],
            "retained": self.retained,
            "stop_reason": self.stop_reason,
        }

    @classmethod
    def from_state(cls, state: dict) -> "StableLinkBudget":
        """Rebuild one persisted budget, refusing anything illegal.

        The ceilings are validated by the constructor, so a persisted record
        that raises either ceiling - including one that raises the ceiling of a
        link already spent to five - is refused rather than honoured.
        """
        if not isinstance(state, dict):
            raise LinkStateCorruption(
                f"a persisted budget is not an object: {state!r}")
        if "link_key" not in state:
            raise LinkStateCorruption("a persisted budget has no link_key")
        link_key = state["link_key"]
        if (not isinstance(link_key, (list, tuple)) or len(link_key) != 2):
            raise LinkStateCorruption(
                f"a persisted link key must name two components: {link_key!r}")
        identity = stable_identity(link_key)
        recorded = state.get("identity")
        if recorded is not None and recorded != identity:
            raise LinkIdentityConflict(
                f"the persisted identity {recorded} does not match the "
                f"canonical identity {identity} of {list(link_key)}")
        if "attempted" not in state:
            raise LinkStateCorruption("a persisted budget has no counter")
        return cls(
            link_key,
            max_transactions=state.get("max_transactions",
                                       MAX_TRANSACTIONS_PER_LINK),
            max_anchor_pairs=state.get("max_anchor_pairs",
                                       MAX_ANCHOR_PAIRS_PER_LINK),
            attempted=state.get("attempted", 0),
            pairs=list(state.get("pairs", [])),
            attempts=list(state.get("attempts", [])),
            aliases_seen=[list(item) for item in state.get("aliases_seen", [])],
            retained=state.get("retained"),
            stop_reason=state.get("stop_reason"),
        )


def merge_budgets(left: StableLinkBudget,
                  right: StableLinkBudget) -> StableLinkBudget:
    """Merge two budgets that alias to the same logical link.

    Budget-conserving by construction: the charged counters add, the anchor
    sets union, and nothing is reset. The merged ceilings are the tighter of the
    two. A merge that would leave the result above either ceiling, or that would
    combine two *different* retained joins, is refused and both budgets are left
    untouched.
    """
    if left.retained and right.retained and left.retained != right.retained:
        raise LinkIdentityConflict(
            f"{left.identity} keeps {left.retained!r} and {right.identity} "
            f"keeps {right.retained!r}; refusing to merge two retained joins")
    attempted = left.attempted + right.attempted
    ceiling = min(left.max_transactions, right.max_transactions)
    pair_ceiling = min(left.max_anchor_pairs, right.max_anchor_pairs)
    pairs = list(left.pairs)
    pairs.extend(item for item in right.pairs if item not in pairs)
    if attempted > ceiling:
        raise LinkCeilingError(
            f"merging {left.identity} ({left.attempted} charged) with "
            f"{right.identity} ({right.attempted} charged) would charge "
            f"{attempted}, above the tighter ceiling {ceiling}")
    if len(pairs) > pair_ceiling:
        raise LinkCeilingError(
            f"merging {left.identity} ({len(left.pairs)} pairs) with "
            f"{right.identity} ({len(right.pairs)} pairs) would offer "
            f"{len(pairs)} pairs, above the tighter ceiling {pair_ceiling}")
    aliases_seen = list(left.aliases_seen)
    aliases_seen.extend(item for item in right.aliases_seen
                        if item not in aliases_seen)
    attempts = left.attempts + right.attempts
    attempts.sort(key=lambda row: (int(row["ordinal"]), str(row["pair_id"])))
    return StableLinkBudget(
        left.link_key,
        max_transactions=ceiling,
        max_anchor_pairs=pair_ceiling,
        attempted=attempted,
        pairs=pairs,
        attempts=attempts,
        aliases_seen=aliases_seen,
        retained=left.retained or right.retained,
        stop_reason=left.stop_reason or right.stop_reason,
    )


class LinkLedger:
    """The persisted set of per-link budgets, shared across resumptions.

    ``reserve`` is the single gate before a native call and flushes to disk
    before returning, so the charge survives a crash inside the transaction.
    ``settle`` records the outcome afterwards. The file is keyed by stable
    identity, so a resume finds the same budget for the same link; the campaign
    and per-link ceilings are enforced here as well as in the budget.
    """

    def __init__(self, path, budgets: dict | None = None, *,
                 aliases: AliasMap | None = None,
                 pair_links: dict | None = None,
                 campaign: dict | None = None,
                 max_links: int = MAX_LINKS_PER_CAMPAIGN,
                 max_transactions_campaign: int = MAX_TRANSACTIONS_PER_CAMPAIGN,
                 flush: bool = True) -> None:
        self.path = Path(path) if path else None
        self.budgets: dict[str, StableLinkBudget] = dict(budgets or {})
        self.aliases = aliases or AliasMap()
        self.pair_links: dict[str, list[str]] = {
            str(pair): [str(link) for link in links]
            for pair, links in (pair_links or {}).items()}
        self.campaign: dict | None = (
            dict(campaign) if campaign else None)
        self.max_links = _validated_count(
            max_links, MAX_LINKS_PER_CAMPAIGN, "campaign link ceiling")
        self.max_transactions_campaign = _validated_count(
            max_transactions_campaign, MAX_TRANSACTIONS_PER_CAMPAIGN,
            "campaign transaction ceiling")
        self.flush_enabled = bool(flush)

    # -- accounting ------------------------------------------------------

    def total_attempted(self) -> int:
        return sum(int(budget.attempted) for budget in self.budgets.values())

    def total_pairs(self) -> int:
        return sum(len(budget.pairs) for budget in self.budgets.values())

    # -- budgets ---------------------------------------------------------

    def budget(self, link_key, *, max_transactions=MAX_TRANSACTIONS_PER_LINK,
               max_anchor_pairs=MAX_ANCHOR_PAIRS_PER_LINK) -> StableLinkBudget:
        """The budget for one link, created on first use and never re-labelled.

        The link is resolved through the alias map first, so every equivalent
        alias, and every renumbering the structural canonical form absorbs,
        lands on the one budget this identity already holds. Requested ceilings
        are validated here and may only tighten an existing budget.
        """
        max_transactions = _validated_count(
            max_transactions, MAX_TRANSACTIONS_PER_LINK, "max_transactions")
        max_anchor_pairs = _validated_count(
            max_anchor_pairs, MAX_ANCHOR_PAIRS_PER_LINK, "max_anchor_pairs")
        identity = stable_identity(link_key, self.aliases)
        existing = self.budgets.get(identity)
        if existing is not None:
            if existing.identity != identity:
                raise LinkIdentityConflict(
                    f"the ledger holds {existing.identity} for {identity}")
            if max_transactions < existing.max_transactions:
                existing.max_transactions = max_transactions
            if max_anchor_pairs < existing.max_anchor_pairs:
                existing.max_anchor_pairs = max_anchor_pairs
            existing.check_ceilings()
            existing.note_alias(link_key)
            existing.attach(self.flush)
            return existing
        if len(self.budgets) >= self.max_links:
            raise LinkCeilingError(
                f"the campaign link ceiling {self.max_links} is reached; "
                f"refusing to open a budget for {identity}")
        budget = StableLinkBudget(self.aliases.resolve_link_key(link_key),
                                  max_transactions=max_transactions,
                                  max_anchor_pairs=max_anchor_pairs)
        budget.note_alias(link_key)
        budget.attach(self.flush)
        self.budgets[identity] = budget
        return budget

    # -- the two mutation points -----------------------------------------

    def reserve(self, link_key, pair_id, spec) -> Ticket:
        """Charge one native transaction and persist the charge first."""
        self.check_campaign_ceilings()
        budget = self.budget(link_key)
        if self.total_attempted() >= self.max_transactions_campaign:
            raise LinkCeilingError(
                f"the campaign transaction ceiling "
                f"{self.max_transactions_campaign} is reached; refusing "
                f"another native call")
        ticket = budget.reserve(pair_id, spec)
        self.flush()
        return ticket

    def settle(self, ticket: Ticket, **outcome) -> dict:
        """Record one transaction's outcome and persist it."""
        budget = self.budgets.get(ticket.identity)
        if budget is None:
            raise LinkIdentityConflict(
                f"no budget is charged for {ticket.identity}")
        receipt = budget.settle(ticket, **outcome)
        self.flush()
        return receipt

    def note_pair(self, link_key, pair_id) -> dict:
        """Record one terminal-pair offer against the link that made it.

        This is deliberately separate from the transaction budget: a terminal
        pair may legitimately be offered by more than one link when those links
        are genuinely different restoration goals, and seeing the same pair
        again must never reset or share anyone's counter. The reuse is recorded
        here so it is explicit in the ledger rather than inferred later.
        """
        identity = stable_identity(link_key, self.aliases)
        pair_id = str(pair_id)
        owners = self.pair_links.setdefault(pair_id, [])
        reused_by = [owner for owner in owners if owner != identity]
        if identity not in owners:
            owners.append(identity)
        self.flush()
        return {"pair_id": pair_id, "link": identity,
                "cross_link_reuse": reused_by, "distinct_links": len(owners)}

    # -- aliases ---------------------------------------------------------

    def declare_alias(self, alias, canonical, *, flush: bool = True) -> dict:
        """Declare two component names equivalent, merging any existing budgets.

        Symmetric by construction. Whatever budgets already exist under either
        side are merged into one: charges add, anchor sets union, nothing is
        reset. A merge that would break a ceiling, or combine two different
        retained joins, is refused **and the whole declaration is rolled back**:
        the equivalence itself is unpublished too, so a caller that swallows the
        refusal cannot leave the ledger with an active alias and unmerged
        budgets, which would let one logical link spend twice.

        ``flush=False`` stages the declaration in memory without writing, so a
        caller can reconcile state before publishing anything.
        """
        before = {
            "links": len(self.budgets),
            "charges": self.total_attempted(),
            "pairs": self.total_pairs(),
            "identities": sorted(self.budgets),
        }
        # Stage the declaration: the alias map, the budget objects and the
        # budgets' own link keys are all snapshotted, and every one of them is
        # restored if the reconciliation refuses the merge.
        aliases_before = dict(self.aliases.parent)
        budgets_before = dict(self.budgets)
        keys_before = {identity: budget.link_key
                       for identity, budget in self.budgets.items()}
        self.aliases.add(alias, canonical)
        try:
            reconciliation = self._reconcile()
        except Exception:
            self.aliases.parent = aliases_before
            self.budgets = budgets_before
            for identity, link_key in keys_before.items():
                self.budgets[identity].link_key = link_key
            raise
        if flush:
            self.flush()
        return {
            "alias": canonical_component(alias),
            "canonical": canonical_component(canonical),
            "resolved": self.aliases.resolve(alias),
            "equivalent": self.aliases.equivalent(alias, canonical),
            "before": before,
            "after": {"links": len(self.budgets),
                      "charges": self.total_attempted(),
                      "pairs": self.total_pairs(),
                      "identities": sorted(self.budgets)},
            "conserved": (before["charges"] == self.total_attempted()
                          and before["pairs"] == self.total_pairs()
                          and before["links"] - len(reconciliation["merged"])
                          == len(self.budgets)),
            "merged": reconciliation["merged"],
            "moved": reconciliation["moved"],
        }

    def _reconcile(self) -> dict:
        """Re-key every budget through the alias map, merging collisions."""
        merged: list[dict] = []
        moved: list[dict] = []
        original_keys = {identity: budget.link_key
                         for identity, budget in self.budgets.items()}
        rebuilt: dict[str, StableLinkBudget] = {}
        try:
            for identity in sorted(self.budgets):
                budget = self.budgets[identity]
                resolved_key = tuple(sorted(
                    self.aliases.resolve_link_key(budget.link_key)))
                target = stable_identity(resolved_key)
                existing = rebuilt.get(target)
                if existing is None:
                    budget.link_key = resolved_key
                    budget.attach(self.flush)
                    rebuilt[target] = budget
                    if target != identity:
                        moved.append({"from": identity, "to": target,
                                      "charges": budget.attempted,
                                      "pairs": len(budget.pairs)})
                    continue
                combined = merge_budgets(existing, budget)
                combined.attach(self.flush)
                rebuilt[target] = combined
                merged.append({"identity": target,
                               "from": [existing.identity, identity],
                               "charges": combined.attempted,
                               "pairs": len(combined.pairs)})
        except Exception:
            for identity, budget in self.budgets.items():
                budget.link_key = original_keys[identity]
            raise
        self.budgets = rebuilt
        return {"merged": merged, "moved": moved}

    def check_campaign_ceilings(self) -> None:
        """Re-validate the campaign ceilings and the cumulative totals."""
        self.max_links = _validated_count(
            self.max_links, MAX_LINKS_PER_CAMPAIGN, "campaign link ceiling")
        self.max_transactions_campaign = _validated_count(
            self.max_transactions_campaign, MAX_TRANSACTIONS_PER_CAMPAIGN,
            "campaign transaction ceiling")
        _validated_total(len(self.budgets), MAX_LINKS_PER_CAMPAIGN,
                         "budgets held")
        _validated_total(self.total_attempted(),
                         MAX_TRANSACTIONS_PER_CAMPAIGN,
                         "cumulative native transactions")

    def bind_campaign(self, binding: dict) -> dict:
        """Bind this ledger to a campaign record, or verify an existing binding.

        The first bind publishes the binding; every later bind must agree with
        it. A ledger bound to one campaign can therefore never be silently
        re-bound to another, which is what stops a resume from pointing at an
        unrelated file and starting the counters again.
        """
        if not isinstance(binding, dict) or not binding:
            raise LinkStateCorruption(
                "a campaign binding must be a non-empty object")
        if self.campaign is None:
            self.campaign = {}
        for key, value in binding.items():
            recorded = self.campaign.get(key)
            if recorded is not None and recorded != value:
                raise LinkStateCorruption(
                    f"the ledger is bound to {key}={recorded!r}, not {value!r}")
            self.campaign[key] = value
        chain = [str(item) for item in (self.campaign.get("campaign_records")
                                        or [])]
        record = self.campaign.get("campaign_record")
        if record and record not in chain:
            chain.append(str(record))
        if chain:
            self.campaign["campaign_records"] = chain
        self.flush()
        return dict(self.campaign)

    def campaign_binding(self) -> dict:
        """The ledger's campaign binding, or an empty one when unbound."""
        return dict(self.campaign or {})

    def advance_campaign(self, record_digest: str, *, previous_record: str,
                         head_predecessor: str | None = None) -> dict:
        """Advance the binding to a successor record, guarded - cycle-5 (1).

        A resumed run writes a *new* record, so the binding must move from the
        record it continued to the record it produced. It may only do so from
        the record it is actually bound to. The one other lawful state is an
        interrupted finalization: the successor record was written but the
        binding never advanced, which is accepted only when that record's own
        declared predecessor is the binding - and then the chain is extended
        with the record that was written first, in order, so the history stays
        a single chain.

        Anything else - an unrelated digest, a re-bind, a repeat - is refused,
        so the binding can never be re-pointed at arbitrary state or reset.

        One limit is stated rather than enforced here: the interrupted
        finalization branch below trusts the ``previous_record`` digest its
        caller supplies, because this module cannot open a record file and so
        cannot check that a digest names bytes that exist. A caller must verify
        the predecessor's own declared predecessor and its bytes on disk
        *before* advancing - otherwise a digest invented by the caller can
        extend the chain with a record that is not a genuine continuation. The
        public guard's ``publish_completed_record`` is the path that does that
        verification, and it is the only path that should drive a continuation.
        """
        bound = dict(self.campaign or {})
        current = bound.get("campaign_record")
        chain = [str(item) for item in (bound.get("campaign_records")
                                        or ([current] if current else []))]
        if not current:
            raise LinkStateCorruption(
                "the ledger is not bound to a campaign; a successor binding "
                "must advance an existing one")
        if not isinstance(record_digest, str) or not record_digest:
            raise LinkStateCorruption("a successor binding needs a record digest")
        if not isinstance(previous_record, str) or not previous_record:
            raise LinkStateCorruption(
                "a successor binding must name the record it continues")
        if record_digest in chain:
            raise LinkStateCorruption(
                f"the record {record_digest} is already in the ledger's chain")
        if current == previous_record:
            extension = []
        elif (head_predecessor is not None
              and current == head_predecessor
              and chain
              and chain[-1] == head_predecessor
              and previous_record not in chain
              and previous_record != current):
            # Interrupted finalization: the previous record was written but never
            # bound. Its own declared predecessor is the current binding, so the
            # chain is extended with it before the new record.
            extension = [previous_record]
        else:
            raise LinkStateCorruption(
                f"the ledger is bound to {current}, so it cannot advance from "
                f"{previous_record}; refusing an unguarded successor binding")
        self.campaign = {
            "campaign_record": str(record_digest),
            "campaign_records": chain + extension + [str(record_digest)],
        }
        self.flush()
        return dict(self.campaign)

    # -- persistence -----------------------------------------------------

    def to_json(self) -> dict:
        return {
            "schema": LEDGER_SCHEMA,
            "approved_max_transactions_per_link": MAX_TRANSACTIONS_PER_LINK,
            "approved_max_anchor_pairs_per_link": MAX_ANCHOR_PAIRS_PER_LINK,
            "approved_max_links_per_campaign": MAX_LINKS_PER_CAMPAIGN,
            "approved_max_transactions_per_campaign":
                MAX_TRANSACTIONS_PER_CAMPAIGN,
            "ceilings": {
                "max_links": self.max_links,
                "max_transactions_campaign": self.max_transactions_campaign,
            },
            "campaign": dict(self.campaign) if self.campaign else None,
            "aliases": self.aliases.state(),
            "links": {identity: budget.state()
                      for identity, budget in sorted(self.budgets.items())},
            "terminal_pairs": {
                pair: list(links)
                for pair, links in sorted(self.pair_links.items())},
            "totals": {
                "links": len(self.budgets),
                "terminal_pairs": len(self.pair_links),
                "native_transactions": self.total_attempted(),
                "anchor_pairs": self.total_pairs(),
            },
        }

    def flush(self) -> None:
        if self.path is None or not self.flush_enabled:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.to_json(), indent=1, sort_keys=True) + "\n",
            encoding="utf-8")

    @classmethod
    def load(cls, path, *, flush: bool = True,
             aliases: AliasMap | None = None,
             require_existing: bool = False,
             expect_campaign: dict | None = None,
             max_links: int = MAX_LINKS_PER_CAMPAIGN,
             max_transactions_campaign: int = MAX_TRANSACTIONS_PER_CAMPAIGN,
             ) -> "LinkLedger":
        """Read a ledger back, refusing anything that is not a legal state.

        Read-only: a load never rewrites the file. Every ceiling in the file is
        validated, every budget is re-validated by its own constructor, any
        equivalent identities already in the file are merged, and the absolute
        campaign totals are checked - so a tampered file cannot raise a spent
        link's ceiling or smuggle in a thirteenth link.

        ``require_existing`` refuses a missing file instead of returning an
        empty ledger: a resume must not silently substitute a fresh ledger for
        the one it is continuing. ``expect_campaign`` refuses a ledger that is
        not bound to the campaign the caller is resuming, which is what stops an
        unrelated or empty file from resetting a chain's cumulative charges.
        """
        path = Path(path)
        ledger = cls(path, aliases=aliases, flush=flush,
                     max_links=max_links,
                     max_transactions_campaign=max_transactions_campaign)
        if not path.is_file():
            if require_existing:
                raise LinkStateCorruption(
                    f"{path}: the ledger this campaign recorded does not exist; "
                    f"refusing to continue from a substitute")
            if expect_campaign is not None:
                raise LinkStateCorruption(
                    f"{path}: no ledger exists, so it cannot be bound to the "
                    f"campaign this caller requires")
            return ledger
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise LinkStateCorruption(f"{path}: the ledger is not an object")
        schema = payload.get("schema")
        if schema != LEDGER_SCHEMA:
            if schema == REFUSED_SCHEMA:
                raise LinkStateCorruption(
                    f"{path}: ledger schema {schema!r} predates the hard "
                    f"ceilings and the symmetric alias algebra; it is refused "
                    f"by name rather than migrated, so a spent counter cannot "
                    f"be re-interpreted")
            raise LinkStateCorruption(
                f"{path}: unrecognised ledger schema {schema!r}; only "
                f"{LEDGER_SCHEMA!r} is supported")
        if "links" not in payload or not isinstance(payload["links"], dict):
            raise LinkStateCorruption(f"{path}: the ledger has no links map")
        ceilings = payload.get("ceilings")
        if ceilings is not None:
            if not isinstance(ceilings, dict):
                raise LinkStateCorruption(
                    f"{path}: the persisted ceilings are not an object")
            ledger.max_links = _validated_count(
                ceilings.get("max_links", ledger.max_links),
                MAX_LINKS_PER_CAMPAIGN, "persisted campaign link ceiling")
            ledger.max_transactions_campaign = _validated_count(
                ceilings.get("max_transactions_campaign",
                             ledger.max_transactions_campaign),
                MAX_TRANSACTIONS_PER_CAMPAIGN,
                "persisted campaign transaction ceiling")
        # A tighter option on the command line still tightens a resumed ledger.
        ledger.max_links = min(
            ledger.max_links, _validated_count(
                max_links, MAX_LINKS_PER_CAMPAIGN, "campaign link ceiling"))
        ledger.max_transactions_campaign = min(
            ledger.max_transactions_campaign, _validated_count(
                max_transactions_campaign, MAX_TRANSACTIONS_PER_CAMPAIGN,
                "campaign transaction ceiling"))
        persisted_aliases = AliasMap.from_state(payload.get("aliases"))
        for token, root in persisted_aliases.state().items():
            ledger.aliases.add(token, root)
        ledger.pair_links = {
            str(pair): [str(link) for link in links]
            for pair, links in (payload.get("terminal_pairs") or {}).items()}
        campaign = payload.get("campaign")
        if campaign is not None and not isinstance(campaign, dict):
            raise LinkStateCorruption(
                f"{path}: the persisted campaign binding is not an object")
        ledger.campaign = dict(campaign) if campaign else None
        if expect_campaign is not None:
            if ledger.campaign is None:
                raise LinkStateCorruption(
                    f"{path}: the ledger is not bound to a campaign, so it "
                    f"cannot be the ledger this resume continues")
            for key, value in expect_campaign.items():
                recorded = ledger.campaign.get(key)
                if recorded != value:
                    raise LinkStateCorruption(
                        f"{path}: the ledger is bound to {key}={recorded!r}, "
                        f"not {value!r}")
        for identity, state in payload["links"].items():
            budget = StableLinkBudget.from_state(state)
            existing = ledger.budgets.get(str(identity))
            if existing is not None:
                raise LinkStateCorruption(
                    f"{path}: the identity {identity} is recorded twice")
            budget.attach(ledger.flush)
            ledger.budgets[str(identity)] = budget
        ledger._reconcile()
        ledger.check_campaign_ceilings()
        return ledger


def _record_int(value, name: str, *, high: int, low: int = 0) -> int:
    """One strictly typed count out of a campaign record."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise LinkStateCorruption(
            f"the campaign record field {name} is {value!r}, not an integer")
    if value < low or value > high:
        raise LinkStateCorruption(
            f"the campaign record field {name} is {value}, outside "
            f"{low}..{high}")
    return value


def history_link_records(record: dict) -> list[dict]:
    """Every link record a resumed campaign record carries.

    ``history`` is the flattened per-link history the writer accumulates, and
    ``links`` is this run's own record, so the pair is the flat history a resume
    must account for. A record written before the history was flattened carries
    ``prior_links`` instead, so that is used when ``history`` is absent - never
    both, which would count the same run twice and invent charges. Retained and
    refused summaries are deliberately never added: they repeat links already
    present here.
    """
    if not isinstance(record, dict):
        raise LinkStateCorruption("a campaign record must be an object")
    rows: list[dict] = []
    history = record.get("history")
    if history is not None:
        if not isinstance(history, list):
            raise LinkStateCorruption(
                "the campaign record field history is not a list")
        rows.extend(history)
    else:
        legacy = record.get("prior_links")
        if legacy is not None:
            if not isinstance(legacy, list):
                raise LinkStateCorruption(
                    "the campaign record field prior_links is not a list")
            rows.extend(legacy)
    links = record.get("links", [])
    if links is None:
        links = []
    if not isinstance(links, list):
        raise LinkStateCorruption(
            "the campaign record field links is not a list")
    rows.extend(links)
    return rows


def validate_campaign_record(record: dict) -> dict:
    """Strictly validate every field a resume depends on - nothing optional.

    A resume that accepts a record it cannot interpret is a resume that can be
    steered, so the record's identity fields, its run total, its link count and
    its cumulative ledger totals are all checked for type and range before any
    of them is used.
    """
    if not isinstance(record, dict):
        raise LinkStateCorruption("a campaign record must be an object")
    if record.get("ran") is not True:
        raise LinkStateCorruption("the campaign record is not a completed run")
    for field in ("ledger_file", "final_parent_dir"):
        value = record.get(field)
        if not isinstance(value, str) or not value:
            raise LinkStateCorruption(
                f"the campaign record has no {field}")
    final_sha = record.get("final_parent_board_sha256")
    if not isinstance(final_sha, str) or len(final_sha) != 64:
        raise LinkStateCorruption(
            "the campaign record has no final parent board digest")
    previous = record.get("previous_record_sha256")
    if previous is not None and (not isinstance(previous, str)
                                 or len(previous) != 64):
        raise LinkStateCorruption(
            f"the campaign record's previous_record_sha256 is {previous!r}")
    run_total = _record_int(record.get("native_transactions_total"),
                            "native_transactions_total",
                            high=MAX_TRANSACTIONS_PER_CAMPAIGN)
    links_attempted = _record_int(record.get("attempted_links"),
                                  "attempted_links", high=MAX_LINKS_PER_CAMPAIGN)
    totals = record.get("ledger_totals")
    if not isinstance(totals, dict):
        raise LinkStateCorruption("the campaign record has no ledger_totals")
    cumulative = _record_int(totals.get("native_transactions"),
                             "ledger_totals.native_transactions",
                             high=MAX_TRANSACTIONS_PER_CAMPAIGN)
    rows = history_link_records(record)
    deltas = 0
    for row in rows:
        if not isinstance(row, dict):
            raise LinkStateCorruption(f"a link record is not an object: {row!r}")
        delta = _record_int(row.get("native_transactions_attempted"),
                            "native_transactions_attempted",
                            high=MAX_TRANSACTIONS_PER_LINK)
        deltas += delta
    own = sum(
        _record_int((row or {}).get("native_transactions_attempted"),
                    "native_transactions_attempted",
                    high=MAX_TRANSACTIONS_PER_LINK)
        for row in (record.get("links") or []))
    if own != run_total:
        raise LinkStateCorruption(
            f"the campaign record declares {run_total} transactions but its "
            f"link records account for {own}")
    if len(record.get("links") or []) != links_attempted:
        raise LinkStateCorruption(
            f"the campaign record declares {links_attempted} links but "
            f"carries {len(record.get('links') or [])}")
    if deltas != cumulative:
        raise LinkStateCorruption(
            f"the campaign record's ledger totals declare {cumulative} "
            f"transactions but its link history accounts for {deltas}")
    return {"run_total": run_total, "cumulative": cumulative,
            "links_attempted": links_attempted, "link_records": len(rows)}


def recorded_charges(record: dict, aliases: AliasMap | None = None) -> dict:
    """The charges the recorded history accounts for, per canonical link.

    Every link is resolved to its current canonical identity first, so a history
    written before an alias was declared still names the link the ledger holds.
    """
    if not isinstance(record, dict):
        raise LinkStateCorruption("a campaign record must be an object")
    totals: dict[str, int] = {}
    for row in history_link_records(record):
        if not isinstance(row, dict):
            raise LinkStateCorruption(f"a link record is not an object: {row!r}")
        link_key = row.get("link_key")
        identity = stable_identity(link_key, aliases)
        delta = _record_int(row.get("native_transactions_attempted"),
                            "native_transactions_attempted",
                            high=MAX_TRANSACTIONS_PER_LINK)
        stamped = row.get("stable_identity")
        if stamped is not None and stamped not in (
                identity, stable_identity(link_key)):
            raise LinkIdentityConflict(
                f"the link record for {identity} is stamped {stamped!r}")
        totals[identity] = totals.get(identity, 0) + delta
    return totals


def reconcile_resume_charges(record: dict, ledger: "LinkLedger") -> dict:
    """Refuse any ledger whose per-link counters disagree with the history.

    This is the check a resume runs *before* it writes anything or opens
    anything: it reconciles charge by canonical link, not by campaign total. A
    total-only check accepts a ledger whose charges have been moved between
    links (5+1 rewritten as 1+5) and a ledger holding charges on a link no record
    ever visited (5 parked on an unrecorded link); both are refused here, because
    each counter must equal what the record says that link spent.
    """
    summary = validate_campaign_record(record)
    expected = recorded_charges(record, ledger.aliases)
    actual = {identity: int(budget.attempted)
              for identity, budget in ledger.budgets.items()}
    missing, mismatched, parked = [], [], []
    for identity, charge in sorted(expected.items()):
        if identity not in actual:
            if charge:
                missing.append((identity, charge))
            continue
        if actual[identity] != charge:
            mismatched.append((identity, charge, actual[identity]))
    for identity, charge in sorted(actual.items()):
        if charge and identity not in expected:
            parked.append((identity, charge))
    if missing:
        raise LinkStateCorruption(
            f"the recorded history charges link {missing[0][0]} "
            f"{missing[0][1]} times but the ledger holds no such link")
    if mismatched:
        identity, charge, held = mismatched[0]
        raise LinkStateCorruption(
            f"the ledger holds {held} charges on link {identity} but the "
            f"recorded history accounts for {charge}")
    if parked:
        identity, charge = parked[0]
        raise LinkStateCorruption(
            f"the ledger holds {charge} charges on link {identity}, which no "
            f"record accounts for")
    cumulative = sum(actual.values())
    if cumulative != summary["cumulative"]:
        raise LinkStateCorruption(
            f"the ledger holds {cumulative} charges but the record's ledger "
            f"totals declare {summary['cumulative']}")
    delta_total = sum(expected.values())
    if delta_total != summary["cumulative"]:
        raise LinkStateCorruption(
            f"the recorded history accounts for {delta_total} charges but the "
            f"record's ledger totals declare {summary['cumulative']}")
    return {"expected": expected, "actual": actual,
            "run_total": summary["run_total"], "cumulative": cumulative}


def reconcile_record_identities(records, aliases: AliasMap | None = None) -> dict:
    """Re-resolve a prior campaign's link stamps under the current aliases.

    A resume carries the previous run's records forward. If an alias is declared
    between the two runs, a record stamped before the declaration names the old
    identity while the ledger now resolves the same link to the new one. That is
    a *restamp*, not a re-label: the ledger is authoritative and the change is
    recorded. Nothing here aborts, which is the point - an applied alias used to
    stop the campaign.
    """
    restamped: list[dict] = []
    resolved: list[dict] = []
    for record in records or []:
        raw = record.get("link_key")
        if not raw:
            continue
        identity = stable_identity(raw, aliases)
        stamped = record.get("stable_identity")
        resolved.append({"link_key": list(raw), "stable_identity": identity})
        if stamped is not None and stamped != identity:
            restamped.append({"link_key": list(raw), "was": stamped,
                              "now": identity,
                              "step": record.get("step")})
    return {"restamped": restamped, "resolved": resolved}


def run_link_budget(*, ledger: LinkLedger, link_key, pairs, ladder, attempt_fn,
                    pair_id) -> dict:
    """Offer anchor pairs in order under one per-link transaction budget.

    ``ladder`` is the declared attempt ladder tried per pair, in declared order.
    It may be a sequence, or a callable taking the pair and returning that
    pair's ladder (the declared waypoints depend on the pair's own endpoints).
    ``attempt_fn(pair, spec, ticket)`` performs exactly one native transaction
    and returns ``{"started", "committed", "retain", "seconds", "record"}``; the
    driver calls it once per ticket, so the number of native calls equals the
    number of charges, and the ceiling on charges is the ceiling on calls.

    The loop stops the moment the budget is exhausted, whichever pair it is on,
    and never offers a pair the identity already used. A link whose budget was
    already exhausted by an earlier run makes no native call at all.
    """
    budget = ledger.budget(link_key)
    outcome = {
        "identity": budget.identity,
        "link_key": list(budget.link_key),
        "retained": None,
        "retained_pair": None,
        "refused": False,
        "stop_reason": None,
        "pairs": [],
        "pair_reuse": [],
        "native_calls": 0,
        "charges_before": budget.attempted,
    }
    if budget.exhausted:
        budget.stop_reason = "transaction budget exhausted on resume"
        outcome["stop_reason"] = budget.stop_reason
        outcome["refused"] = True
        outcome["charges_after"] = budget.attempted
        ledger.flush()
        return outcome

    ladder_of = ladder if callable(ladder) else (lambda _pair: ladder)
    retained = False
    for pair in pairs:
        pid = str(pair_id(pair))
        if not budget.offer_pair(pid):
            outcome["stop_reason"] = "anchor-pair ceiling reached"
            break
        # Record the offer against this link. A pair another link already
        # offered is recorded as a cross-link reuse and changes no counter.
        reuse = ledger.note_pair(link_key, pid)
        if reuse["cross_link_reuse"]:
            outcome["pair_reuse"].append(reuse)
        pair_record = {"pair_id": pid, "attempts": []}
        for spec in ladder_of(pair):
            if budget.exhausted:
                outcome["stop_reason"] = "transaction budget exhausted"
                break
            ticket = ledger.reserve(link_key, pid, spec)
            outcome["native_calls"] += 1
            result = dict(attempt_fn(pair, spec, ticket) or {})
            receipt = ledger.settle(
                ticket,
                started=result.get("started"),
                committed=result.get("committed"),
                retained=bool(result.get("retain")),
                seconds=result.get("seconds"))
            pair_record["attempts"].append(
                {**receipt, "record": result.get("record")})
            if receipt["retained"]:
                retained = True
                break
        outcome["pairs"].append(pair_record)
        if retained or budget.exhausted:
            break

    outcome["charges_after"] = budget.attempted
    outcome["retained"] = budget.retained
    if retained:
        kept = [attempt for pair in outcome["pairs"]
                for attempt in pair["attempts"] if attempt["retained"]]
        outcome["retained_pair"] = kept[0]["pair_id"] if kept else None
    else:
        outcome["refused"] = True
        budget.stop_reason = outcome["stop_reason"] or "every offered pair refused"
        outcome["stop_reason"] = budget.stop_reason
    ledger.flush()
    return outcome
