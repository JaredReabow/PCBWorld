"""Native-DRC acceptance gate for copper mutations.

The routing-time engine does not enforce a project rule file on the copper it
places (measured; see RESULT.md §5), so "the router obeyed the rules" is not a
claim this codebase can make. What it *can* do is refuse to keep copper that the
engine's own DRC rejects under the exact context that applies:

1. take a baseline (every violation, with a stable identity key);
2. let the attempt run;
3. take the same DRC again;
4. reject the attempt — and roll it back — if it introduced any *
   relevant* violation.

Connectivity findings are deliberately excluded: ``Missing connection between
items`` (the unrouted ratsnest) and dangling track/via ends are *progress*
signals on a board that is being routed, not rule violations. Everything else —
clearance, shorting, track width, via/hole geometry, edge clearance, hole to
hole — gates the commit.

Violations are compared by identity, not by count: pre-existing violations stay
pre-existing, and a violation that merely moved is not mistaken for a new one
(the item UUIDs are the primary identity; geometry and net names are the
fallback for legacy items without UUIDs).

An item UUID is only an identity when the board's own inventory proves it names
exactly one physical item. This board reuses UUIDs (measured on the frozen V3
source: 121 of 6845 distinct UUIDs cover 821 of 7545 items), so two different
physical violations can be reported under one UUID pair. A pair key is therefore
only trusted when every UUID in it names exactly one inventory item; everything
else - a reused UUID, an item the inventory does not cover, the nil UUID, an
unreadable inventory - is *unverified*, and its key is refined by the violation's
own condition so that a swapped pair at an equal count is an addition rather than
"no change" (see :func:`ambiguous_pair_keys`).

There is deliberately no proximity rule: "the reported point is nearest to this
candidate" is a heuristic, not proof of physical identity, and labelling a guess
"proven" is what let a swapped pair pass. Repairing the duplicate identifiers
belongs on a disposable copy of the board, not in this gate.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from pcb_world.agent.state import to_nm


# Error codes from KiCad's DRC error-code enum. These are **not** a stable API:
# they are the positional values of ``enum PCB_DRC_CODE`` in the engine this
# build was compiled from (``build_rl/kicad_src/pcbnew/drc/drc_item.h``, pinned
# to the engine submodule commit). `parse_drc_enum` reads that header so
# `tests/agent/test_native_drc_enum.py` can fail loudly if the pinned engine ever
# renumbers the enum instead of this module silently mis-classifying violations.
#
#   DRCE_UNCONNECTED_ITEMS      = 1   items are unconnected
#   DRCE_DANGLING_VIA           = 12  via not connected to anything
#   DRCE_DANGLING_TRACK         = 13  track end not connected to anything
#   DRCE_DRILLED_HOLES_TOO_CLOSE = 14 overlapping drills break bits  (RELEVANT)
#   DRCE_DRILLED_HOLES_COLOCATED = 15 two holes at the same location (RELEVANT)
DRCE_UNCONNECTED_ITEMS = 1
DRCE_DANGLING_VIA = 12
DRCE_DANGLING_TRACK = 13
DRCE_DRILLED_HOLES_TOO_CLOSE = 14
DRCE_DRILLED_HOLES_COLOCATED = 15

#: The engine reports a nil UUID for an item it cannot identify; it is not a
#: physical identity, so it never resolves to an inventory item.
NIL_UUID = "00000000-0000-0000-0000-000000000000"

#: The identity policy a serialized violation multiset was measured under. The
#: serializer stamps it, the loader *requires* it, and a payload naming anything
#: else is refused rather than replayed under today's rules. Bumping it
#: invalidates stored baselines on purpose.
EVIDENCE_POLICY = "collision-aware-inventory-v1"
SUPPORTED_EVIDENCE_POLICIES: frozenset[str] = frozenset({EVIDENCE_POLICY})

#: The inventory policy the payload's binding summary was measured under. The
#: summary is the second half of the identity proof - it says how many keys the
#: board inventory could not prove - so a payload whose summary is absent, empty
#: or names a policy this reader does not know cannot be replayed.
INVENTORY_POLICY = "inventory-identity-v2"
SUPPORTED_INVENTORY_POLICIES: frozenset[str] = frozenset({INVENTORY_POLICY})

#: Every field ``take_violations`` writes into ``binding``: the whole
#: ``BoardInventory.to_evidence()`` summary plus the violation and ambiguity
#: accounting it merges in. All of them are required, so "clear the ambiguity
#: list and hope the summary is ignored" is a refusal rather than a pass.
INVENTORY_BINDING_FIELDS: tuple[str, ...] = (
    "policy",
    "complete",
    "problems",
    "items",
    "distinct_uuids",
    "duplicated_uuids",
    "duplicated_items",
    "nil_uuids",
    "kinds",
    "unknown_kinds",
    "violations",
    "keys_without_inventory_proof",
    "violations_without_inventory_proof",
    "unresolved_reasons",
)

#: Every field a serialized multiset must carry *explicitly*. A reader that
#: defaults a missing field - the ambiguity flags, a row's condition, a row's
#: geometry - turns an incomplete payload into a usable identity, which is how a
#: payload with its ambiguity deleted used to compare "clean" against a physical
#: substitution. Absent is not the same as empty, so presence is checked before
#: any value is interpreted.
EVIDENCE_PAYLOAD_FIELDS: tuple[str, ...] = (
    "policy",
    "rules_path",
    "context",
    "binding",
    "inventory_ambiguous",
    "violations",
    "total",
    "relevant",
    "connectivity",
    "keys_without_inventory_proof",
    "violations_without_inventory_proof",
    "counts",
)
EVIDENCE_ROW_FIELDS: tuple[str, ...] = (
    "key",
    "condition",
    "relevant",
    "error_code",
    "error_type",
    "message",
    "layer",
    "x_nm",
    "y_nm",
    "net_names",
    "item_a",
    "item_b",
)

#: There is deliberately no proximity threshold here. "The reported point is
#: nearest to this candidate" is a heuristic, not proof of physical identity, so a
#: reused UUID is never resolved by geometry: it stays *unverified* and its key is
#: refined by condition, which refuses movement instead of guessing. Duplicate
#: identifiers are repaired on a disposable copy of the board; this gate does not
#: launder the ambiguity.

# Spot-checks the parser must reproduce, including the two codes whose
# mis-assignment caused a real copper-integrity defect (a 0.1 mm hole-spacing
# violation was being excluded as "connectivity noise").
ENUM_EXPECTATIONS: dict[str, int] = {
    "DRCE_UNCONNECTED_ITEMS": DRCE_UNCONNECTED_ITEMS,
    "DRCE_DANGLING_VIA": DRCE_DANGLING_VIA,
    "DRCE_DANGLING_TRACK": DRCE_DANGLING_TRACK,
    "DRCE_DRILLED_HOLES_TOO_CLOSE": DRCE_DRILLED_HOLES_TOO_CLOSE,
    "DRCE_DRILLED_HOLES_COLOCATED": DRCE_DRILLED_HOLES_COLOCATED,
}

# Progress signals: a partially routed board legitimately has these. They are
# reported in the delta but never gate a commit.
CONNECTIVITY_ERROR_CODES: frozenset[int] = frozenset({
    DRCE_UNCONNECTED_ITEMS, DRCE_DANGLING_TRACK, DRCE_DANGLING_VIA,
})
CONNECTIVITY_ERROR_TYPES: frozenset[str] = frozenset({
    "Missing connection between items",
    "Unconnected items",
    "Track has unconnected end",
    "Via has unconnected end",
})


def parse_drc_enum(header_text: str) -> dict[str, int]:
    """Parse ``enum PCB_DRC_CODE`` out of a ``drc_item.h`` source text.

    Pure so it can be unit-tested, and used by the native test that compares this
    module's constants against the engine the router was actually built from.
    Handles the two spellings KiCad uses: an explicit ``= DRCE_FIRST`` on the
    first member, then implicit successors.
    """
    match = re.search(
        r"enum\s+PCB_DRC_CODE\s*\{(?P<body>.*?)\}", header_text, re.DOTALL
    )
    if not match:
        raise ValueError("enum PCB_DRC_CODE not found in the given source text")

    values: dict[str, int] = {}
    current: int | None = None
    for raw_line in match.group("body").splitlines():
        line = raw_line.split("//", 1)[0].strip().rstrip(",").strip()
        if not line:
            continue
        if "=" in line:
            name, _, expression = line.partition("=")
            name = name.strip()
            expression = expression.strip()
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                continue
            if expression in values:
                current = values[expression]
            else:
                try:
                    current = int(expression, 0)
                except ValueError:
                    continue
            values[name] = current
        else:
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", line):
                continue
            if current is None:
                continue
            current += 1
            values[line] = current
    if not values:
        raise ValueError("enum PCB_DRC_CODE parsed empty")
    return values


@dataclass(frozen=True)
class PhysicalItem:
    """One item as the board inventory sees it.

    ``uuid`` is the engine's own item identity (a KIID string). It is not unique on
    this board, so an item is only a *physical* identity together with its own
    geometry - and a UUID carried by more than one item is never treated as one.
    """

    kind: str                      # "track" | "via" | "pad" | ...
    uuid: str
    geometry: tuple                # canonical, rounded item geometry
    source: str = ""               # container the row was read from
    parent_kind: str = ""          # "board" | "footprint"
    parent_ref: str = ""           # parent footprint reference, when there is one
    layer: int = -1
    net_code: int = -1

    @property
    def identity(self) -> str:
        """Collision-safe identity for this item, stable while it is not moved."""
        return f"{self.kind}:{self.uuid}@{self.geometry}"


@dataclass(frozen=True)
class BoardInventory:
    """Every identity-bearing board item the engine can name, keyed by UUID.

    Built from the engine's own complete item accessor, which reads every
    container the board owns - the copper lists, board drawings, zones and
    groups, and each footprint with its pads, graphical items, fields, zones and
    groups. A violation can name a zone, a courtyard graphic or a footprint, so
    an inventory that only knew the copper would make those UUIDs look unique
    when the board reuses them.

    ``complete`` and an empty ``problems`` mean the accessor answered; anything
    else means the inventory cannot prove identity and every key it did not
    cover stays condition-refined (fail closed). There is no distance rule here:
    a UUID carried by more than one item is an ambiguous identifier, and the
    repair for that lives in :mod:`pcb_world.agent.kicad_metadata`, on a
    disposable copy of the board - never in this gate.
    """

    items: dict = field(default_factory=dict)      # uuid -> tuple[PhysicalItem, ...]
    item_count: int = 0
    complete: bool = False
    problems: tuple = ()
    policy: str = "inventory-identity-v2"
    kinds: tuple = ()                 # (kind, count), sorted: what was covered
    unknown_kinds: tuple = ()         # classes the engine could not classify
    nil_uuids: int = 0                # items that name no identifier at all

    def candidates(self, uuid: str) -> tuple:
        return tuple(self.items.get(uuid, ()))

    @property
    def distinct_uuids(self) -> int:
        return len(self.items)

    @property
    def duplicated_uuids(self) -> int:
        return sum(1 for group in self.items.values() if len(group) > 1)

    @property
    def duplicated_items(self) -> int:
        return sum(len(group) for group in self.items.values() if len(group) > 1)

    def to_evidence(self) -> dict[str, Any]:
        return {
            "policy": self.policy,
            "complete": bool(self.complete),
            "problems": list(self.problems),
            "items": int(self.item_count),
            "distinct_uuids": self.distinct_uuids,
            "duplicated_uuids": self.duplicated_uuids,
            "duplicated_items": self.duplicated_items,
            "nil_uuids": int(self.nil_uuids),
            "kinds": [list(entry) for entry in self.kinds],
            "unknown_kinds": [list(entry) for entry in self.unknown_kinds],
        }


def capture_inventory(engine: Any) -> BoardInventory:
    """Read the engine's complete item inventory, or say precisely why not.

    Only the complete accessor is consulted. A partial inventory (copper only)
    would answer "unique" for a UUID whose other carrier is a zone or a
    footprint, which is exactly the ambiguity this gate must not launder, so a
    missing or failed accessor is *incomplete*, never a fallback.
    """
    getter = getattr(engine, "get_board_items", None)
    if not callable(getter):
        return BoardInventory(
            problems=("engine exposes no get_board_items() item inventory",),
        )
    try:
        rows = list(getter())
    except Exception as exc:  # noqa: BLE001 - an unreadable inventory proves nothing
        return BoardInventory(
            problems=(f"get_board_items() failed: {type(exc).__name__}: {exc}",),
        )

    items: dict[str, list[PhysicalItem]] = {}
    kind_counts: dict[str, int] = {}
    problems: list[str] = []
    nil_uuids = 0
    for row in rows:
        uuid = str(getattr(row, "uuid", "") or "")
        kind = str(getattr(row, "kind", "") or "unknown")
        if not uuid:
            problems.append("an inventory row carries no uuid field at all")
            continue
        if uuid == NIL_UUID:
            nil_uuids += 1
        kind_counts[kind] = kind_counts.get(kind, 0) + 1
        source = str(getattr(row, "source", "") or "")
        parent_kind = str(getattr(row, "parent_kind", "") or "")
        parent_ref = str(getattr(row, "parent_ref", "") or "")
        layer = int(getattr(row, "layer", -1))
        net_code = int(getattr(row, "net_code", -1))
        physical_id = str(getattr(row, "physical_id", "") or "")
        geometry = (
            source, parent_kind, parent_ref, layer, net_code, physical_id,
        )
        items.setdefault(uuid, []).append(
            PhysicalItem(kind=kind, uuid=uuid, geometry=geometry,
                         source=source, parent_kind=parent_kind,
                         parent_ref=parent_ref, layer=layer, net_code=net_code)
        )

    unknown = tuple(sorted(
        (kind, count) for kind, count in kind_counts.items()
        if kind.startswith("other:")
    ))
    return BoardInventory(
        items={uuid: tuple(group) for uuid, group in items.items()},
        item_count=sum(kind_counts.values()),
        complete=not problems,
        problems=tuple(problems),
        kinds=tuple(sorted(kind_counts.items())),
        unknown_kinds=unknown,
        nil_uuids=nil_uuids,
    )


def resolve_item_identity(
    uuid: str, inventory: BoardInventory | None,
) -> tuple[str | None, str]:
    """What physical item does ``uuid`` name, if the inventory proves one?

    Returns ``(identity, why)``. A UUID carried by exactly one inventory item *is*
    that item (its identity is the UUID itself, which keeps existing pair keys
    stable) and keeps its movement tolerance. Everything else - a reused UUID, an
    item the inventory does not cover, the nil UUID, an unreadable inventory - is
    ``None``: unverified, so the key is refined by condition and movement is an
    addition. No distance is consulted; nearest-candidate resolution would be a
    guess about identity, not a proof of it.
    """
    if not uuid:
        return None, "no uuid"
    if uuid == NIL_UUID:
        return None, "nil uuid"
    if inventory is None or not inventory.complete:
        return None, "no complete board inventory"
    candidates = inventory.candidates(uuid)
    if not candidates:
        return None, "uuid is not in the inventory"
    if len(candidates) == 1:
        return uuid, "unique item"
    return None, f"uuid is carried by {len(candidates)} items (duplicate identifier)"


def violation_identity(
    v: Any, inventory: BoardInventory | None = None,
) -> tuple[tuple, bool, tuple]:
    """``(key, provable, reasons)`` for one violation.

    ``key`` is the UUID pair when the report names items (kept so a violation
    that moves with a re-route keeps its identity) and geometry plus net names
    when it does not. ``provable`` says whether every UUID in the pair was
    resolved to one physical item by the board inventory; when it was not, the
    key alone is not an identity and the diff refines it by condition.
    """
    item_a = str(getattr(v, "item_a", "") or "")
    item_b = str(getattr(v, "item_b", "") or "")
    if item_a or item_b:
        reasons: list[str] = []
        resolved: list[str] = []
        for uuid in sorted((item_a, item_b)):
            if not uuid:
                # One side named no item at all; there is nothing to resolve it
                # to, and it does not weaken the other side's identity.
                resolved.append("")
                continue
            identity, why = resolve_item_identity(uuid, inventory)
            if identity is None:
                reasons.append(f"{uuid or '<empty>'}: {why}")
                resolved.append(uuid)
            else:
                resolved.append(identity)
        return (("uuid", int(v.error_code), int(v.layer), tuple(resolved)),
                not reasons, tuple(reasons))
    return (
        (
            "geom",
            int(v.error_code),
            str(v.error_type),
            int(v.layer),
            to_nm(v.x_mm), to_nm(v.y_mm),
            tuple(sorted(str(n) for n in (v.net_names or ()))),
        ),
        True,
        (),
    )


def violation_key(v: Any, inventory: BoardInventory | None = None) -> tuple:
    """Stable identity for one violation (see :func:`violation_identity`)."""
    return violation_identity(v, inventory)[0]


def violation_condition(v: Any) -> tuple:
    """Where and on what the violation was reported.

    Used only to *disambiguate* a shared key: the board reuses item UUIDs, so two
    different physical pairs can produce the same pair key, and equal counts of
    that key would otherwise hide a replacement. Movement alone never changes an
    identity (the key is pair-based); a condition only enters an identity when the
    key is genuinely shared by different conditions.
    """
    return (
        to_nm(v.x_mm), to_nm(v.y_mm), int(v.layer),
        tuple(sorted(str(n) for n in (v.net_names or ()))),
    )


def ambiguous_pair_keys(*sets: "ViolationSet") -> frozenset:
    """Keys whose UUID pair is not proven to name one physical violation.

    Two things make a key ambiguous, and both are real duplicated-ID ambiguity
    rather than movement:

    * one set reports two different *conditions* under one key (the report itself
      shows the key standing for more than one physical finding); and
    * the board inventory could not resolve every UUID in the key to one physical
      item (:attr:`ViolationSet.inventory_ambiguous`), so two different physical
      pairs can share it across boards even when each report shows it once.

    A key that is proven on both sides and merely sits at a different position is
    a *moved* violation and keeps its movement tolerance.
    """
    ambiguous: set = set()
    for violations in sets:
        ambiguous.update(getattr(violations, "inventory_ambiguous", ()) or ())
        for key, group in violations.members.items():
            if len({violation_condition(v) for v in group}) > 1:
                ambiguous.add(key)
    return frozenset(ambiguous)


def collision_aware_counts(violations: "ViolationSet", ambiguous: frozenset) -> dict:
    """Multiset keyed by pair key, refined by condition for ambiguous keys.

    A key that is unique keeps its movement tolerance; a key shared by different
    physical conditions is split so that swapping one for the other is an
    addition, not "no change".
    """
    counts: dict[tuple, int] = {}
    for key, group in violations.members.items():
        for violation in group:
            identity = ((key, violation_condition(violation))
                        if key in ambiguous else key)
            counts[identity] = counts.get(identity, 0) + 1
    return counts


def identity_key_fields(identity: Any) -> tuple:
    """Split an identity into ``(key, condition_or_None)``.

    An identity is a :func:`violation_key` result, or - when the key is
    ambiguous - the ``(key, condition)`` refinement :func:`collision_aware_counts`
    produces. Both shapes flow through the diff and the evidence, so every reader
    goes through this one splitter.
    """
    if (isinstance(identity, tuple) and len(identity) == 2
            and isinstance(identity[0], tuple)):
        return identity[0], identity[1]
    return identity, None


def identity_sort_key(identity: Any) -> tuple:
    """Total order over keys and condition-refined identities.

    Comparing the two shapes directly raises ``TypeError`` (a tuple against a
    string), so the order is by canonical text instead of by value.
    """
    key, _condition = identity_key_fields(identity)
    return (repr(key), repr(identity))


def is_connectivity_finding(v: Any) -> bool:
    """True for progress signals that must not gate a commit."""
    return (
        int(v.error_code) in CONNECTIVITY_ERROR_CODES
        or str(v.error_type) in CONNECTIVITY_ERROR_TYPES
    )


@dataclass(frozen=True)
class ViolationSet:
    """Violations of one DRC run under one rule context.

    A key is not unique: ``violation_key`` is pair-based so a violation that
    moves with a re-route keeps its identity, and the board reuses item UUIDs, so
    several violations can share one key (measured on the frozen source: 226 keys
    hold more than one violation, 998 of them relevant). The set therefore keeps
    the violations as a *multiset*: ``counts``/``members`` alongside the
    representative per key in ``by_key``. A dict keyed on the identity alone
    silently dropped 835 violations and could not see one added violation under a
    key that already existed.
    """

    rules_path: str
    keys: frozenset[tuple]
    total: int
    connectivity: int
    relevant: int
    by_key: dict = field(default_factory=dict)
    context: tuple = ()          # context_identity() as of this run
    #: key -> number of violations carrying it (kept, not collapsed)
    counts: dict = field(default_factory=dict)
    #: key -> every violation carrying it
    members: dict = field(default_factory=dict)
    #: violations in report order (the multiset the counts describe)
    violations: tuple = ()
    #: the identity that was measured for each entry of :attr:`violations`, in
    #: the same order; carried so a replayed set keeps the keys that were
    #: resolved against the board inventory rather than recomputing them
    violation_keys: tuple = ()
    #: keys whose UUID pair the board inventory could not resolve to physical
    #: items (see :func:`ambiguous_pair_keys`); empty when every key is proven
    inventory_ambiguous: frozenset = frozenset()
    #: what the inventory (or its absence) looked like, kept as evidence
    binding: dict = field(default_factory=dict)
    #: True when this set came from the engine's scoped clearance re-check
    #: rather than a whole-board pass; carried into the delta evidence so a
    #: report never implies a full DRC where only the scoped one ran
    incremental: bool = False

    @property
    def ambiguous_keys(self) -> int:
        """Keys that name more than one violation."""
        return sum(1 for count in self.counts.values() if count > 1)

    @property
    def unproven_keys(self) -> int:
        """Keys the board inventory could not resolve to physical items."""
        return len(self.inventory_ambiguous)

    def representative(self, identity: Any):
        """The violation behind an identity, ``None`` when it is not in this set."""
        record = self.by_key.get(identity)
        if record is not None:
            return record
        key, condition = identity_key_fields(identity)
        if condition is None:
            return None
        for violation in self.members.get(key, ()):
            if violation_condition(violation) == condition:
                return violation
        return None

    @property
    def ambiguous_relevant_violations(self) -> int:
        """Violations sitting under a key that is shared with another."""
        return sum(len(self.members.get(key, ()))
                   for key, count in self.counts.items()
                   if count > 1
                   and not is_connectivity_finding(self.by_key.get(key)))

    def to_evidence(self, limit: int = 8) -> dict[str, Any]:
        samples = [
            {
                "error_code": int(v.error_code),
                "error_type": str(v.error_type),
                "message": str(v.message),
                "layer": int(v.layer),
                "x_mm": float(v.x_mm),
                "y_mm": float(v.y_mm),
                "net_names": list(v.net_names or ()),
            }
            for v in list(self.by_key.values())[:limit]
        ]
        return {
            "rules_path": self.rules_path,
            "total": self.total,
            "connectivity_findings": self.connectivity,
            "relevant_violations": self.relevant,
            "distinct_identities": len(self.counts),
            "keys_carrying_more_than_one_violation": self.ambiguous_keys,
            "relevant_violations_under_a_shared_key":
                self.ambiguous_relevant_violations,
            "keys_without_inventory_proof": self.unproven_keys,
            "inventory": dict(self.binding),
            "samples": samples,
        }


class DrcContextError(RuntimeError):
    """The rule context a DRC run used is not the one that was asked for.

    Raised when the engine reports a rule-load failure, or when the context
    identity (rule file content, project, pads) changed between the baseline and
    the verification run. The session treats it as "no acceptance".
    """

    def __init__(self, message: str, **detail: Any) -> None:
        super().__init__(message)
        self.detail: dict[str, Any] = {"reason": "drc_context_changed", **detail}


def rules_content_digest(rules_path: str) -> str:
    """Content digest of the rule file, or ``absent`` when there is no file."""
    if not rules_path:
        return "implicit"
    try:
        with open(rules_path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()[:16]
    except OSError:
        return "missing"


def context_identity(engine: Any, rules_path: str) -> tuple:
    """Everything a gate judgement depends on besides the copper itself.

    Bound into the cache key, so a changed rule file, project file, board path or
    pad set invalidates the comparison instead of silently reusing a baseline that
    was computed under different rules.
    """
    def _stat(path: str) -> tuple:
        try:
            info = os.stat(path)
            return (round(info.st_mtime_ns), info.st_size)
        except OSError:
            return (0, 0)

    project_path = ""
    try:
        project_path = str(engine.get_project_path())
    except Exception:  # noqa: BLE001 - an absent accessor is part of the identity
        project_path = "<unavailable>"

    pads_digest = "unavailable"
    try:
        pads = engine.get_pads()
        h = hashlib.sha256()
        for pad in sorted(
            ((round(float(p.x_mm), 4), round(float(p.y_mm), 4), int(p.net_code)) for p in pads)
        ):
            h.update(repr(pad).encode())
        pads_digest = h.hexdigest()[:12]
    except Exception:  # noqa: BLE001
        pads_digest = "unavailable"

    return (
        rules_path or "",
        rules_content_digest(rules_path),
        _stat(rules_path) if rules_path else (0, 0),
        project_path,
        _stat(project_path) if project_path else (0, 0),
        pads_digest,
    )


def _assert_context_clean(engine: Any, rules_path: str) -> None:
    """After a DRC run, prove the engine really used ``rules_path``.

    ``run_drc`` is allowed to fall back (and report nothing) when it cannot read
    the file it was given, so the return value alone is not evidence. The
    engine's own load-error channel is checked here, after every run, and a
    reported failure refuses the result.
    """
    try:
        error = str(engine.get_last_drc_rules_load_error() or "")
    except AttributeError:
        error = ""
    except Exception as exc:  # noqa: BLE001
        raise DrcContextError(
            f"cannot read the engine's DRC rule-load status: {type(exc).__name__}: {exc}",
            rules_path=rules_path,
        ) from None
    if error:
        raise DrcContextError(
            f"DRC did not run under the requested rules: {error}",
            rules_path=rules_path, engine_error=error,
        )
    if rules_path and not os.path.isfile(rules_path):
        raise DrcContextError(
            f"requested rule file does not exist: {rules_path}", rules_path=rules_path,
        )


def take_violations(
    engine: Any, rules_path: str, *, incremental: bool = False,
) -> ViolationSet:
    """Run the engine's DRC and classify the result.

    Raises :class:`DrcContextError` when the run did not happen under the
    requested context, and any other exception the engine raises.

    ``incremental`` asks the engine for its scoped re-check
    (:meth:`KiCadEngine.run_drc_incremental`) instead of a whole-board pass. The
    engine re-runs every provider in full and only scopes the *clearance* pass to
    the tracks/vias whose geometry or net changed since its last DRC, merging back
    the retained clearance violations; on the frozen V3 board that is 0.2 s
    against 39 s for the same multiset. The engine forces a full run when it has
    no baseline, when the rules path changed, or after a design-rule write or a
    zone refill (both of which clear its DRC state), and
    :func:`_assert_context_clean` still refuses a run that did not use the
    requested rules. What the engine *cannot* see is a rule file whose contents
    changed while its path stayed the same, so callers must only ask for the
    incremental pass while the rule-context identity they baselined under still
    holds -- :meth:`DrcGate.verify` enforces exactly that.
    """
    violations: Sequence[Any] = (
        engine.run_drc_incremental(rules_path or "")
        if incremental else engine.run_drc(rules_path or "")
    )
    _assert_context_clean(engine, rules_path)
    inventory = capture_inventory(engine)
    counts: dict[tuple, int] = {}
    members: dict[tuple, list] = {}
    inventory_ambiguous: set = set()
    unresolved: list[str] = []
    measured_keys: list[tuple] = []
    for violation in violations:
        key, provable, reasons = violation_identity(violation, inventory)
        if not provable:
            inventory_ambiguous.add(key)
            unresolved.extend(reasons)
        measured_keys.append(key)
        counts[key] = counts.get(key, 0) + 1
        members.setdefault(key, []).append(violation)
    by_key = {key: group[0] for key, group in members.items()}
    relevant = sum(0 if is_connectivity_finding(v) else 1 for v in violations)
    connectivity = len(violations) - relevant
    binding = {
        **inventory.to_evidence(),
        "violations": len(violations),
        "keys_without_inventory_proof": len(inventory_ambiguous),
        "violations_without_inventory_proof": sum(
            counts[key] for key in inventory_ambiguous
        ),
        "unresolved_reasons": sorted(set(unresolved))[:8],
    }
    return ViolationSet(
        rules_path=rules_path or "",
        keys=frozenset(counts),
        total=len(violations),
        connectivity=connectivity,
        relevant=relevant,
        by_key=by_key,
        context=context_identity(engine, rules_path),
        incremental=bool(incremental),
        counts=counts,
        members={key: tuple(group) for key, group in members.items()},
        violations=tuple(violations),
        violation_keys=tuple(measured_keys),
        inventory_ambiguous=frozenset(inventory_ambiguous),
        binding=binding,
    )


def violations_evidence(violations: ViolationSet) -> dict[str, Any]:
    """Serialise a violation set so another process can replay its identity.

    A saved-artifact gate runs in its own process, so the baseline it compares
    against has to travel. Only the *identity* has to survive: every violation's
    key, its condition (:func:`violation_condition`), whether it is a
    connectivity signal, and the fields the evidence display reads. The condition
    is carried as exact integer nanometres, and the reconstructed record carries
    the same integers back through ``to_nm``, so the replayed set hashes to the
    same keys and conditions it was captured with.

    The payload also carries its own accounting - the row total, the relevant /
    connectivity split, one count per key, and how many keys and rows lost their
    inventory proof - so :func:`violations_from_evidence` can refuse a payload
    whose rows do not add up to what it claims (a truncated or edited row list)
    instead of silently replaying a subset. The result is *self-consistent*, not
    authenticated: a payload rewritten consistently end to end is a different
    problem, and the outer envelope's board/project/rules/engine binding is what
    a caller has to check for that.
    """
    return {
        "policy": EVIDENCE_POLICY,
        "rules_path": violations.rules_path,
        "context": [_key_json(value) for value in violations.context],
        "binding": dict(violations.binding),
        "inventory_ambiguous": [
            _key_json(key) for key in sorted(violations.inventory_ambiguous)
        ],
        "total": len(violations.violations),
        "relevant": int(violations.relevant),
        "connectivity": int(violations.connectivity),
        "keys_without_inventory_proof": len(violations.inventory_ambiguous),
        "violations_without_inventory_proof": sum(
            violations.counts.get(key, 0)
            for key in violations.inventory_ambiguous
        ),
        "counts": [
            {"key": _key_json(key), "count": int(count)}
            for key, count in sorted(violations.counts.items(),
                                     key=lambda item: identity_sort_key(item[0]))
        ],
        "violations": [
            {
                "key": _key_json(
                    violations.violation_keys[index]
                    if index < len(violations.violation_keys)
                    else violation_key(v)
                ),
                "condition": _key_json(violation_condition(v)),
                "relevant": not is_connectivity_finding(v),
                "error_code": int(v.error_code),
                "error_type": str(v.error_type),
                "message": str(v.message),
                "layer": int(v.layer),
                "x_nm": to_nm(v.x_mm),
                "y_nm": to_nm(v.y_mm),
                "net_names": [str(n) for n in (v.net_names or ())],
                "item_a": str(getattr(v, "item_a", "") or ""),
                "item_b": str(getattr(v, "item_b", "") or ""),
            }
            for index, v in enumerate(violations.violations)
        ],
    }


def _key_json(value: Any) -> Any:
    """JSON form of a key/condition: tuples become lists, primitives stay put."""
    if isinstance(value, (tuple, list)):
        return [_key_json(item) for item in value]
    return value


def _key_tuple(value: Any) -> Any:
    """Inverse of :func:`_key_json` for the fixed-shape identity tuples."""
    if isinstance(value, list):
        return tuple(_key_tuple(item) for item in value)
    return value


class EvidenceSchemaError(ValueError):
    """Serialized native evidence is not a complete, self-consistent proof.

    Raised by :func:`validate_evidence_payload` instead of building a usable
    :class:`ViolationSet` out of an incomplete payload. A caller converts it into
    an explicit refusal; it is never a reason to fall back to "nothing changed",
    and it is never a reason to retain.
    """

    def __init__(self, reasons: Sequence[str]) -> None:
        self.reasons: tuple[str, ...] = tuple(str(reason) for reason in reasons)
        super().__init__(
            "serialized native DRC evidence failed schema validation: "
            + "; ".join(self.reasons)
        )


def _is_int(value: Any) -> bool:
    """A JSON integer. ``bool`` is a subclass of ``int`` and is never one."""
    return isinstance(value, int) and not isinstance(value, bool)


def _is_str(value: Any) -> bool:
    return isinstance(value, str)


def _is_bool(value: Any) -> bool:
    return isinstance(value, bool)


def _is_sequence(value: Any) -> bool:
    """A JSON list, as the serializer writes tuples/lists."""
    return isinstance(value, (list, tuple)) and not isinstance(value, (str, bytes))


def _hashable_key(value: Any) -> tuple | None:
    """Tuple form of a key whose parts are all primitives, else ``None``.

    A key (or count entry) that carries a nested object, ``None``, a float or a
    boolean is not a key this reader can compare. Returning ``None`` here keeps
    those values out of every ``set``/``dict`` operation, where they would raise
    ``TypeError`` instead of becoming a structured refusal.
    """
    if not _is_sequence(value):
        return None
    parts: list[Any] = []
    for part in value:
        if _is_str(part) or _is_int(part):
            parts.append(part)
        elif _is_sequence(part):
            nested = _hashable_key(part)
            if nested is None:
                return None
            parts.append(nested)
        else:
            return None
    return tuple(parts)


def _key_shape_problems(label: str, key: Any) -> list[str]:
    """Why ``key`` is not one of the two identity shapes :func:`violation_key` makes.

    Accepts exactly ``("uuid", code, layer, (a, b))`` and
    ``("geom", code, type, layer, x_nm, y_nm, (nets...))``. Anything else is a
    shape this loader does not know how to compare, so it must never be replayed
    as an identity.
    """
    if not _is_sequence(key) or not key:
        return [f"{label} is not a key"]
    head = key[0]
    if head == "uuid":
        if (len(key) != 4 or not _is_int(key[1]) or not _is_int(key[2])
                or not _is_sequence(key[3]) or len(key[3]) != 2
                or not all(_is_str(part) for part in key[3])):
            return [f"{label} is not a well-formed uuid key"]
        return []
    if head == "geom":
        if (len(key) != 7 or not _is_int(key[1]) or not _is_str(key[2])
                or not _is_int(key[3]) or not _is_int(key[4])
                or not _is_int(key[5])
                or not _is_sequence(key[6])
                or not all(_is_str(part) for part in key[6])):
            return [f"{label} is not a well-formed geom key"]
        return []
    return [f"{label} names an unsupported identity shape {head!r}"]


def _condition_shape_problems(label: str, condition: Any) -> list[str]:
    """Why ``condition`` is not a :func:`violation_condition` tuple."""
    if (not _is_sequence(condition) or len(condition) != 4
            or not _is_int(condition[0]) or not _is_int(condition[1])
            or not _is_int(condition[2]) or not _is_sequence(condition[3])
            or not all(_is_str(part) for part in condition[3])):
        return [f"{label} is not a well-formed condition"]
    return []


def _row_problems(index: int, row: Any) -> tuple[list[str], tuple | None]:
    """Validate one serialized violation row; return its key when well-formed."""
    label = f"violation row {index}"
    if not isinstance(row, Mapping):
        return [f"{label} is a {type(row).__name__}, not an object"], None
    missing = [field for field in EVIDENCE_ROW_FIELDS if field not in row]
    if missing:
        return [f"{label} does not carry {field!r} at all" for field in missing], None

    problems: list[str] = []
    key = row["key"]
    problems.extend(_key_shape_problems(f"{label} key", key))
    condition = row["condition"]
    problems.extend(_condition_shape_problems(f"{label} condition", condition))
    if not _is_bool(row["relevant"]):
        problems.append(f"{label} relevant is not a boolean")
    for field in ("error_code", "layer", "x_nm", "y_nm"):
        if not _is_int(row[field]):
            problems.append(f"{label} {field} is not an integer")
    for field in ("error_type", "message", "item_a", "item_b"):
        if not _is_str(row[field]):
            problems.append(f"{label} {field} is not a string")
    if not _is_sequence(row["net_names"]) or not all(
            _is_str(name) for name in row["net_names"]):
        problems.append(f"{label} net_names is not a list of strings")
    if problems:
        return problems, None

    key_tuple = _hashable_key(key)
    condition_tuple = _hashable_key(condition)
    if key_tuple is None:
        # A key carrying an object, None, a float or a boolean is not a key this
        # reader can compare; it must never reach a set/dict and raise TypeError.
        return problems + [f"{label} key is not a comparable key"], None
    if condition_tuple is None:
        return problems + [f"{label} condition is not a comparable condition"], None

    # Every field is present and correctly typed from here on. The row's own
    # fields, its key and its condition all describe the same finding; where they
    # disagree the payload is contradictory and proves nothing.
    nets = tuple(sorted(str(name) for name in row["net_names"]))
    expected_condition = (int(row["x_nm"]), int(row["y_nm"]), int(row["layer"]), nets)
    if condition_tuple != expected_condition:
        problems.append(
            f"{label} condition disagrees with its own geometry/layer/net_names"
        )
    if key_tuple[1] != int(row["error_code"]):
        problems.append(f"{label} key disagrees with its error_code")
    if key_tuple[0] == "uuid":
        if int(key_tuple[2]) != int(row["layer"]):
            problems.append(f"{label} key disagrees with its layer")
        if tuple(key_tuple[3]) != tuple(sorted((row["item_a"], row["item_b"]))):
            problems.append(f"{label} key disagrees with its item pair")
    else:
        if str(key_tuple[2]) != row["error_type"]:
            problems.append(f"{label} key disagrees with its error_type")
        if int(key_tuple[3]) != int(row["layer"]):
            problems.append(f"{label} key disagrees with its layer")
        if (int(key_tuple[4]), int(key_tuple[5])) != (int(row["x_nm"]),
                                                      int(row["y_nm"])):
            problems.append(f"{label} key disagrees with its geometry")
        if tuple(key_tuple[6]) != nets:
            problems.append(f"{label} key disagrees with its net_names")
        if row["item_a"] or row["item_b"]:
            problems.append(
                f"{label} is a geometry key but still names an item"
            )
    connectivity = (
        int(row["error_code"]) in CONNECTIVITY_ERROR_CODES
        or str(row["error_type"]) in CONNECTIVITY_ERROR_TYPES
    )
    if bool(row["relevant"]) == connectivity:
        problems.append(
            f"{label} relevant disagrees with its own error classification"
        )
    return problems, key_tuple


def _binding_problems(binding: Any, *, rows: int, ambiguous_keys: Sequence[tuple],
                      occurrences: int, uuid_keys: Sequence[tuple],
                      declared_ambiguity: Any, declared_occurrences: Any) -> list[str]:
    """The inventory summary beside the payload, checked against the payload.

    The summary is not decoration: it is the only place that says how the
    inventory looked when the multiset was measured, and its counts are the
    second witness to the ambiguity list. Clearing the ambiguity list - and even
    the payload's own two ambiguity counts - leaves ``complete``,
    ``keys_without_inventory_proof`` and ``violations_without_inventory_proof``
    behind, so those are required, typed, and cross-checked here.

    An inventory that could not name every item proves nothing about identity, so
    a summary that says ``complete: false`` must declare every ``uuid`` key
    unproven. That is the conservative direction: no UUID is certified by a
    missing proof.
    """
    if not isinstance(binding, Mapping):
        return [f"binding is a {type(binding).__name__}, not an object"]
    problems: list[str] = []
    absent = [field for field in INVENTORY_BINDING_FIELDS if field not in binding]
    if absent:
        problems.extend(
            f"the binding does not carry its inventory summary field {field!r}"
            for field in absent
        )
        return problems

    policy = binding["policy"]
    if not _is_str(policy) or policy not in SUPPORTED_INVENTORY_POLICIES:
        problems.append(
            f"unsupported inventory policy {policy!r}; this reader supports "
            f"{sorted(SUPPORTED_INVENTORY_POLICIES)}"
        )
    if not _is_bool(binding["complete"]):
        problems.append("binding complete is not a boolean")
    for field in ("items", "distinct_uuids", "duplicated_uuids",
                  "duplicated_items", "nil_uuids", "violations",
                  "keys_without_inventory_proof",
                  "violations_without_inventory_proof"):
        if not _is_int(binding[field]) or binding[field] < 0:
            problems.append(f"binding {field} is not a non-negative integer")
    for field in ("problems", "unresolved_reasons"):
        if not _is_sequence(binding[field]) or not all(
                _is_str(entry) for entry in binding[field]):
            problems.append(f"binding {field} is not a list of strings")
    for field in ("kinds", "unknown_kinds"):
        entries = binding[field]
        if not _is_sequence(entries) or not all(
                _is_sequence(entry) and len(entry) == 2 and _is_str(entry[0])
                and _is_int(entry[1]) and entry[1] >= 0
                for entry in entries):
            problems.append(f"binding {field} is not a list of (name, count) pairs")

    if problems:
        return problems

    # Internal consistency of the summary itself.
    if bool(binding["problems"]) == bool(binding["complete"]):
        problems.append(
            "binding complete and problems disagree: an inventory with problems "
            "is incomplete, and one without them is complete"
        )
    if binding["items"] < binding["distinct_uuids"]:
        problems.append("binding items is smaller than distinct_uuids")
    if binding["nil_uuids"] > binding["items"]:
        problems.append("binding nil_uuids is larger than items")

    # The summary against the payload the reader is about to trust.
    if binding["violations"] != rows:
        problems.append(
            f"binding violations is {binding['violations']} but the payload "
            f"carries {rows} row(s)"
        )
    if binding["keys_without_inventory_proof"] != len(ambiguous_keys):
        problems.append(
            "binding keys_without_inventory_proof does not match the "
            "ambiguity list"
        )
    if binding["violations_without_inventory_proof"] != occurrences:
        problems.append(
            "binding violations_without_inventory_proof does not match the "
            "ambiguous keys' occurrence counts"
        )
    if _is_int(declared_ambiguity) and (
            binding["keys_without_inventory_proof"] != declared_ambiguity):
        problems.append(
            "binding keys_without_inventory_proof disagrees with the payload's "
            "own ambiguity count"
        )
    if _is_int(declared_occurrences) and (
            binding["violations_without_inventory_proof"] != declared_occurrences):
        problems.append(
            "binding violations_without_inventory_proof disagrees with the "
            "payload's own occurrence count"
        )
    if binding["complete"] is False:
        proven = set(ambiguous_keys)
        unproven_missing = [key for key in uuid_keys if key not in proven]
        if unproven_missing:
            problems.append(
                f"the inventory summary is incomplete but {len(unproven_missing)} "
                "uuid key(s) are not declared unproven: an incomplete inventory "
                "certifies no identifier"
            )
    return problems


def _evidence_schema_problems(payload: Any) -> tuple[str, ...]:
    """The checks behind :func:`evidence_schema_problems` (see its docstring)."""
    if not isinstance(payload, Mapping):
        return (f"the payload is a {type(payload).__name__}, not an object",)
    absent = [field for field in EVIDENCE_PAYLOAD_FIELDS if field not in payload]
    if absent:
        return tuple(
            f"the payload does not carry {field!r} at all" for field in absent
        )

    problems: list[str] = []
    policy = payload["policy"]
    if not _is_str(policy) or policy not in SUPPORTED_EVIDENCE_POLICIES:
        problems.append(
            f"unsupported evidence policy {policy!r}; this reader supports "
            f"{sorted(SUPPORTED_EVIDENCE_POLICIES)}"
        )
    if not _is_str(payload["rules_path"]):
        problems.append("rules_path is not a string")
    if not _is_sequence(payload["context"]):
        problems.append("context is not a list")

    rows = payload["violations"]
    if not _is_sequence(rows):
        problems.append("violations is not a list")
        return tuple(problems)

    row_keys: list[tuple] = []
    derived_counts: dict[tuple, int] = {}
    derived_relevant = 0
    for index, row in enumerate(rows):
        row_problems, key = _row_problems(index, row)
        problems.extend(row_problems)
        if key is None:
            continue
        row_keys.append(key)
        derived_counts[key] = derived_counts.get(key, 0) + 1
        if bool(row.get("relevant")):
            derived_relevant += 1
    if len(row_keys) != len(rows):
        # The counts below would describe a subset; stop before comparing them.
        return tuple(problems)

    ambiguous_keys: list[tuple] = []
    ambiguous = payload["inventory_ambiguous"]
    if not _is_sequence(ambiguous):
        problems.append("inventory_ambiguous is not a list")
    else:
        for index, entry in enumerate(ambiguous):
            key = _hashable_key(entry)
            problems.extend(_key_shape_problems(
                f"inventory_ambiguous entry {index}", entry))
            if key is None:
                problems.append(
                    f"inventory_ambiguous entry {index} is not a comparable key"
                )
                continue
            ambiguous_keys.append(key)
        if len(set(ambiguous_keys)) != len(ambiguous_keys):
            problems.append("inventory_ambiguous repeats a key")
        unknown = sorted(
            (key for key in set(ambiguous_keys) if key not in derived_counts),
            key=identity_sort_key,
        )
        if unknown:
            problems.append(
                "inventory_ambiguous names keys the payload does not carry: "
                + ", ".join(repr(key) for key in unknown[:4])
                + ("..." if len(unknown) > 4 else "")
            )
        else:
            declared_keys = payload["keys_without_inventory_proof"]
            declared_rows = payload["violations_without_inventory_proof"]
            if not _is_int(declared_keys) or declared_keys != len(ambiguous_keys):
                problems.append(
                    "keys_without_inventory_proof does not match the "
                    "ambiguity list"
                )
            # A key the rows do not carry has no occurrence count of its own;
            # the "names keys the payload does not carry" problem above already
            # says so, and it must not turn into a KeyError here.
            occurrences = sum(
                derived_counts.get(key, 0) for key in ambiguous_keys)
            if not _is_int(declared_rows) or declared_rows != occurrences:
                problems.append(
                    "violations_without_inventory_proof does not match the "
                    "ambiguous keys' occurrence counts"
                )

    for field in ("total", "relevant", "connectivity"):
        if not _is_int(payload[field]):
            problems.append(f"{field} is not an integer")
    if all(_is_int(payload[field])
           for field in ("total", "relevant", "connectivity")):
        if payload["total"] != len(rows):
            problems.append(
                f"total is {payload['total']} but the payload carries "
                f"{len(rows)} row(s)"
            )
        if payload["total"] != payload["relevant"] + payload["connectivity"]:
            problems.append(
                "relevant + connectivity does not add up to total"
            )
        if payload["relevant"] != derived_relevant:
            problems.append(
                "the relevant count disagrees with the rows' own classifications"
            )
        if payload["connectivity"] != len(rows) - derived_relevant:
            problems.append(
                "the connectivity count disagrees with the rows' own "
                "classifications"
            )

    counts = payload["counts"]
    if not _is_sequence(counts):
        problems.append("counts is not a list")
    else:
        declared_counts: dict[tuple, int] = {}
        for index, entry in enumerate(counts):
            label = f"counts entry {index}"
            if (not isinstance(entry, Mapping) or "key" not in entry
                    or "count" not in entry):
                problems.append(
                    f"{label} is not an object carrying a key and a count")
                continue
            key = _hashable_key(entry["key"])
            problems.extend(_key_shape_problems(f"{label} key", entry["key"]))
            if not _is_int(entry["count"]) or entry["count"] < 1:
                problems.append(f"{label} count is not a positive integer")
                continue
            if key is None:
                problems.append(f"{label} key is not a comparable key")
                continue
            if key in declared_counts:
                problems.append(f"{label} repeats a key")
                continue
            declared_counts[key] = int(entry["count"])
        if declared_counts != derived_counts:
            missing = sorted(set(derived_counts) - set(declared_counts),
                             key=identity_sort_key)
            extra = sorted(set(declared_counts) - set(derived_counts),
                           key=identity_sort_key)
            mismatched = sorted(
                (key for key in set(declared_counts) & set(derived_counts)
                 if declared_counts[key] != derived_counts[key]),
                key=identity_sort_key,
            )
            detail: list[str] = []
            if missing:
                detail.append(f"{len(missing)} key(s) absent from counts")
            if extra:
                detail.append(f"{len(extra)} counted key(s) absent from rows")
            if mismatched:
                detail.append(f"{len(mismatched)} key(s) with a wrong count")
            problems.append(
                "the declared per-key counts do not match the rows"
                + (f" ({', '.join(detail)})" if detail else "")
            )
    occurrences = sum(derived_counts.get(key, 0) for key in ambiguous_keys)
    problems.extend(_binding_problems(
        payload["binding"], rows=len(rows), ambiguous_keys=ambiguous_keys,
        occurrences=occurrences,
        uuid_keys=[key for key in row_keys if key and key[0] == "uuid"],
        declared_ambiguity=payload["keys_without_inventory_proof"],
        declared_occurrences=payload["violations_without_inventory_proof"],
    ))
    return tuple(problems)


def evidence_schema_problems(payload: Any) -> tuple[str, ...]:
    """Why ``payload`` is not a replayable violation multiset. Empty means it is.

    The checks are structural and internal: every required field is present and
    correctly typed, every key is a shape this loader can compare, every row's
    fields agree with its key and condition, the ambiguity list names keys the
    payload actually carries, the inventory summary beside it agrees with the
    rows and counts, and the declared totals and per-key counts are exactly what
    the rows add up to. A payload that fails any of them cannot be turned into a
    :class:`ViolationSet` that a comparison could trust.

    Any unexpected failure while checking is itself a refusal: a malformed shape
    that this reader cannot interpret must never become a pass.
    """
    try:
        return _evidence_schema_problems(payload)
    except Exception as exc:  # noqa: BLE001 - an unreadable payload is not proof
        return (
            "the payload could not be validated "
            f"({type(exc).__name__}: {exc}); no field of it counts as proof",
        )


def validate_evidence_payload(payload: Any) -> Mapping:
    """Refuse a payload this module cannot replay as proof; return it unchanged.

    The serializer and the loader live in this module so they cannot drift: a
    caller that reads stored native evidence goes through this one check (or
    through :func:`violations_from_evidence`, which calls it) and turns a
    failure into an explicit refusal.
    """
    problems = evidence_schema_problems(payload)
    if problems:
        raise EvidenceSchemaError(problems)
    return payload


@dataclass(frozen=True)
class RecordedViolation:
    """A violation replayed from evidence, carrying its original fields."""

    error_code: int
    error_type: str
    message: str
    x_mm: float
    y_mm: float
    layer: int
    net_names: tuple
    item_a: str
    item_b: str


def violations_from_evidence(payload: Mapping[str, Any]) -> ViolationSet:
    """Rebuild the exact multiset a :func:`violations_evidence` payload describes.

    The keys and conditions are replayed verbatim rather than recomputed from the
    geometry, so a comparison in another process uses the same identities the
    capturing process measured - including which keys the board inventory could
    not prove.

    The payload is schema-checked first (:func:`validate_evidence_payload`) and
    every field is read explicitly: an absent ambiguity list, an absent row
    condition or absent geometry is a refusal, never a zero-valued default. An
    incomplete payload is the one thing a replayed comparison must not be able to
    launder into "nothing changed".
    """
    validate_evidence_payload(payload)
    rows = list(payload["violations"])
    violations: list[RecordedViolation] = []
    measured_keys: list[tuple] = []
    counts: dict[tuple, int] = {}
    members: dict[tuple, list] = {}
    for index, row in enumerate(rows):
        key = _key_tuple(row["key"])
        record = RecordedViolation(
            error_code=int(row["error_code"]),
            error_type=str(row["error_type"]),
            message=str(row["message"]),
            x_mm=int(row["x_nm"]) / 1_000_000,
            y_mm=int(row["y_nm"]) / 1_000_000,
            layer=int(row["layer"]),
            net_names=tuple(str(name) for name in row["net_names"]),
            item_a=str(row["item_a"]),
            item_b=str(row["item_b"]),
        )
        violations.append(record)
        measured_keys.append(key)
        counts[key] = counts.get(key, 0) + 1
        members.setdefault(key, []).append(record)
    by_key = {key: group[0] for key, group in members.items()}
    relevant = sum(1 for v in violations if not is_connectivity_finding(v))
    return ViolationSet(
        rules_path=str(payload["rules_path"]),
        keys=frozenset(counts),
        total=len(violations),
        connectivity=len(violations) - relevant,
        relevant=relevant,
        by_key=by_key,
        context=tuple(_key_tuple(value) for value in payload["context"]),
        counts=counts,
        members={key: tuple(group) for key, group in members.items()},
        violations=tuple(violations),
        violation_keys=tuple(measured_keys),
        inventory_ambiguous=frozenset(
            _key_tuple(value) for value in payload["inventory_ambiguous"]
        ),
        binding=dict(payload["binding"]),
    )


@dataclass(frozen=True)
class DrcDelta:
    """What changed between two DRC runs of the same board."""

    baseline: ViolationSet
    candidate: ViolationSet
    added: tuple
    resolved: tuple
    added_relevant: tuple
    added_connectivity: tuple
    #: How many violations the added/resolved keys stand for (a shared key can
    #: stand for several), so a count increase under one key is visible.
    added_occurrences: int = 0
    resolved_occurrences: int = 0

    @property
    def acceptable(self) -> bool:
        return not self.added_relevant

    def class_histogram(self, identities) -> dict[str, int]:
        """Complete ``error_type -> count`` over every identity given.

        ``to_evidence``'s ``rows`` are capped by ``limit`` so one refusal cannot
        write an unbounded evidence block into a checkpoint. That cap must not
        reach the class breakdown: "which classes, and how many of each" is the
        question a stored refusal is read to answer, and a histogram taken from
        the capped rows under-reports it. (Measured: the phase-17 trial recorded
        a refusal whose delta added 50 relevant findings as eight, because the
        eight were the first eight rows.) Counting walks the identity tuple
        itself, so the answer does not depend on ``limit``.
        """
        counts: dict[str, int] = {}
        for key in identities:
            record = (self.candidate.representative(key)
                      or self.baseline.representative(key))
            if record is None:
                continue
            name = str(record.error_type)
            counts[name] = counts.get(name, 0) + 1
        return dict(sorted(counts.items()))

    def to_evidence(self, limit: int = 8) -> dict[str, Any]:
        # ``rows`` is the only place a caller can see *which* findings a delta
        # added, so each row carries the item identities the gate attributed the
        # finding to as well as its class and position. Without them a refusal is
        # a class and a coordinate: a "clearance violation" cannot be told apart
        # from a via landing in a pour, a track crossing a pad, or any other pair
        # of items, and a report has to guess at the cause instead of reading it.
        def rows(keys) -> list[dict[str, Any]]:
            out = []
            for key in list(keys)[:limit]:
                v = (self.candidate.representative(key)
                     or self.baseline.representative(key))
                if v is None:
                    continue
                out.append({
                    "error_code": int(v.error_code),
                    "error_type": str(v.error_type),
                    "message": str(v.message),
                    "layer": int(v.layer),
                    "x_mm": float(v.x_mm),
                    "y_mm": float(v.y_mm),
                    "net_names": list(v.net_names or ()),
                    "item_a": str(getattr(v, "item_a", "") or ""),
                    "item_b": str(getattr(v, "item_b", "") or ""),
                })
            return out

        return {
            "rules_path": self.candidate.rules_path,
            #: "incremental" when the verification pass used the engine's scoped
            #: clearance re-check instead of a whole-board DRC, so a reader can
            #: tell which pass produced this delta.
            "candidate_drc_mode": (
                "incremental" if self.candidate.incremental else "full"
            ),
            "baseline_drc_mode": (
                "incremental" if self.baseline.incremental else "full"
            ),
            "baseline_relevant": self.baseline.relevant,
            "candidate_relevant": self.candidate.relevant,
            "added_relevant_count": len(self.added_relevant),
            "added_relevant_occurrences": self.added_occurrences,
            #: The complete histogram. ``added_relevant`` below is the capped row
            #: sample, so a reader that wants "how many of each class" must not
            #: count rows: it would see at most ``limit`` of them.
            "added_relevant_class_counts": self.class_histogram(self.added_relevant),
            "added_connectivity_count": len(self.added_connectivity),
            "resolved_count": len(self.resolved),
            "resolved_occurrences": self.resolved_occurrences,
            "shared_key_ambiguity": {
                "baseline_keys_with_more_than_one_violation":
                    self.baseline.ambiguous_keys,
                "candidate_keys_with_more_than_one_violation":
                    self.candidate.ambiguous_keys,
                "keys_disambiguated_by_condition":
                    len(ambiguous_pair_keys(self.baseline, self.candidate)),
                "baseline_keys_without_inventory_proof":
                    self.baseline.unproven_keys,
                "candidate_keys_without_inventory_proof":
                    self.candidate.unproven_keys,
                "baseline_inventory": dict(self.baseline.binding),
            },
            "added_relevant": rows(self.added_relevant),
            "added_connectivity": rows(self.added_connectivity),
            "resolved": rows(self.resolved),
            "acceptable": self.acceptable,
        }


class DrcGate:
    """Baseline/verify wrapper with a one-entry cache keyed on context + copper."""

    def __init__(self, session: Any, *, allow_incremental_drc: bool = False) -> None:
        self._session = session
        self._cached_key: tuple | None = None
        self._cached: ViolationSet | None = None
        #: Opt-in: the verification pass may ask the engine for its scoped
        #: clearance re-check. Off unless the caller enabled it, so the default
        #: acceptance path stays a whole-board DRC.
        self._allow_incremental = bool(allow_incremental_drc)

    def _key(self, rules_path: str, digest: str) -> tuple:
        return (context_identity(self._session._engine, rules_path), digest)

    def baseline(self, rules_path: str, digest: str) -> ViolationSet:
        """Baseline for the current copper, reusing the cache when unchanged."""
        key = self._key(rules_path, digest)
        if self._cached is not None and self._cached_key == key:
            return self._cached
        baseline = take_violations(self._session._engine, rules_path)
        self._cached, self._cached_key = baseline, key
        return baseline

    def verify(
        self, baseline: ViolationSet, rules_path: str, digest: str,
    ) -> DrcDelta:
        """Re-run the DRC and diff against ``baseline``; caches the new state."""
        current_context = context_identity(self._session._engine, rules_path)
        if baseline.context and current_context != baseline.context:
            self.invalidate()
            raise DrcContextError(
                "the rule context changed between the baseline and the verification run",
                rules_path=rules_path, baseline_rules_path=baseline.rules_path,
                baseline_context=list(baseline.context),
                current_context=list(current_context),
            )
        # The incremental pass is only a *re-check* of copper the engine already
        # has a complete violation set for under this exact rule context. The
        # gate's cache key is that proof: it is written by the last DRC this
        # gate ran, carries the context identity, and is cleared whenever the
        # copper it described no longer exists. Anything else -- a new rule
        # file, a rule-content change behind the same path, a refill, a
        # quarantined state -- leaves the cache cold or stale, and the full pass
        # runs.
        use_incremental = bool(
            self._allow_incremental
            and self._cached is not None
            and self._cached_key is not None
            and self._cached_key[0] == current_context
        )
        candidate = take_violations(
            self._session._engine, rules_path, incremental=use_incremental,
        )
        delta = diff_sets(baseline, candidate)
        if delta.acceptable:
            self._cached, self._cached_key = candidate, self._key(rules_path, digest)
        else:
            # The copper is about to be rolled back; the cache must not describe
            # a state that no longer exists.
            self._cached, self._cached_key = None, None
        return delta

    def invalidate(self) -> None:
        self._cached, self._cached_key = None, None

    def seed(self, baseline: ViolationSet, digest: str) -> None:
        """Re-arm the cache with a *verified* state's baseline.

        After a rollback that was proven to restore the copper, the baseline DRC
        result for that copper digest is still exactly valid — re-running it would
        cost a full DRC (tens of seconds on a real board) for the same answer.
        Only call this with a digest whose restoration has been verified.

        The cached baseline is also only valid under the rule context it was
        measured in. Carrying a baseline across a rule-file change (or a project /
        pad change) would attach an answer from one context to a cache key from
        another, so the seed is dropped when the context no longer matches.
        """
        current = context_identity(self._session._engine, baseline.rules_path)
        if baseline.context and current != baseline.context:
            self.invalidate()
            return
        self._cached = baseline
        self._cached_key = self._key(baseline.rules_path, digest)

    @staticmethod
    def _is_connectivity(key: tuple, candidate: ViolationSet, baseline: ViolationSet) -> bool:
        for source in (candidate, baseline):
            v = source.representative(key)
            if v is not None:
                return is_connectivity_finding(v)
        return False


def diff_sets(baseline: ViolationSet, candidate: ViolationSet) -> DrcDelta:
    """Diff two violation sets as multisets, splitting added findings by kind.

    A key whose *count* rose is an addition even when the key already existed:
    with pair-based keys (kept so a re-route does not turn a violation into a new
    one) a set difference would call that "no change" and pass a board that
    really did gain a violation.
    """
    ambiguous = ambiguous_pair_keys(baseline, candidate)
    base_counts = collision_aware_counts(baseline, ambiguous)
    cand_counts = collision_aware_counts(candidate, ambiguous)
    added = tuple(sorted(
        (key for key, count in cand_counts.items()
         if count > base_counts.get(key, 0)),
        key=identity_sort_key,
    ))
    resolved = tuple(sorted(
        (key for key, count in base_counts.items()
         if count > cand_counts.get(key, 0)),
        key=identity_sort_key,
    ))
    added_occurrences = sum(
        cand_counts[key] - base_counts.get(key, 0) for key in added
    )
    resolved_occurrences = sum(
        base_counts[key] - cand_counts.get(key, 0) for key in resolved
    )

    def classify(key: tuple) -> bool:
        for source in (candidate, baseline):
            record = source.representative(key)
            if record is not None:
                return is_connectivity_finding(record)
        return False

    added_relevant = tuple(k for k in added if not classify(k))
    added_connectivity = tuple(k for k in added if classify(k))
    return DrcDelta(
        baseline=baseline, candidate=candidate, added=added, resolved=resolved,
        added_relevant=added_relevant, added_connectivity=added_connectivity,
        added_occurrences=added_occurrences,
        resolved_occurrences=resolved_occurrences,
    )
