"""Terminal connectivity as a *partition*, not a per-net group count.

A count says "net GND has 34 groups"; it cannot say whether the two pads that
were joined yesterday are still joined. Two boards can have identical counts and
completely different connectivity - a lost connection hidden by a gained one.

This module captures, per net, which *terminals* share a cluster, and compares
two such partitions with the acceptance condition the contract states:

* every pair of terminals connected in the reference must still be connected in
  the candidate;
* a terminal that the reference had and the candidate does not is a refusal (a
  pad cannot vanish from a routed board);
* a net that the reference had and the candidate does not is a refusal.

A terminal is identified the way a netlist identifies it - ``REF.PAD`` - because
that is stable across a save/reload while the internal item UUIDs are not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class TerminalPartition:
    """Which terminals of each net are electrically joined on one board."""

    #: net name -> every stable physical-pad identity seen on that net
    net_terminals: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    #: terminal -> opaque cluster representative (equal iff joined)
    cluster_of: Mapping[str, str] = field(default_factory=dict)
    #: Capture is proof only when a native inventory and its full partition agree.
    complete: bool = True
    reasons: tuple[str, ...] = ()

    @property
    def terminals(self) -> set[str]:
        return set(self.cluster_of)

    def to_evidence(self) -> dict[str, Any]:
        return {
            "nets": len(self.net_terminals),
            "terminals": len(self.cluster_of),
            "clusters": len(set(self.cluster_of.values())),
            "complete": self.complete,
            "reasons": list(self.reasons),
            "net_terminals": {k: list(v) for k, v in sorted(self.net_terminals.items())},
            "cluster_of": dict(sorted(self.cluster_of.items())),
        }

    @classmethod
    def from_evidence(cls, payload: Mapping[str, Any]) -> "TerminalPartition":
        return cls(
            net_terminals={str(k): tuple(v) for k, v in
                           (payload.get("net_terminals") or {}).items()},
            cluster_of={str(k): str(v) for k, v in
                        (payload.get("cluster_of") or {}).items()},
            complete=bool(payload.get("complete", False)),
            reasons=tuple(str(x) for x in (payload.get("reasons") or [])),
        )


@dataclass(frozen=True)
class TerminalDelta:
    """What changed between a reference partition and a candidate partition."""

    ok: bool
    reasons: tuple[str, ...] = ()
    vanished_terminals: tuple[str, ...] = ()
    vanished_nets: tuple[str, ...] = ()
    new_terminals: tuple[str, ...] = ()
    split_relations: tuple[tuple[str, str, str], ...] = ()   # (net, term_a, term_b)
    merged_pairs: int = 0
    changed_net_membership: tuple[str, ...] = ()

    def to_evidence(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "reasons": list(self.reasons),
            "vanished_terminals": list(self.vanished_terminals),
            "vanished_nets": list(self.vanished_nets),
            "new_terminals": list(self.new_terminals),
            "split_relations": [list(item) for item in self.split_relations],
            "merged_pairs": self.merged_pairs,
            "changed_net_membership": list(self.changed_net_membership),
        }


def compare_terminal_partitions(
    reference: TerminalPartition, candidate: TerminalPartition
) -> TerminalDelta:
    """Reference connectivity must survive; counts are never consulted.

    "Every original connected pair remains connected" is checked per reference
    cluster: all of its terminals that still exist in the candidate must lie in
    one candidate cluster. That is a subset check over the partition, so it is
    linear in terminals and cannot be satisfied by a compensating gain elsewhere.
    """
    ref_terminals, cand_terminals = reference.terminals, candidate.terminals
    vanished = tuple(sorted(ref_terminals - cand_terminals))
    new = tuple(sorted(cand_terminals - ref_terminals))
    vanished_nets = tuple(sorted(set(reference.net_terminals) -
                                set(candidate.net_terminals)))

    reasons: list[str] = []
    for label, partition in (("reference", reference), ("candidate", candidate)):
        if not partition.complete:
            reasons.append(
                f"{label} terminal capture is incomplete: "
                + ("; ".join(partition.reasons) or "native inventory was not proven")
            )

    def terminal_nets(partition: TerminalPartition) -> dict[str, str]:
        result: dict[str, str] = {}
        for net, terminals in partition.net_terminals.items():
            for terminal in terminals:
                if terminal in result:
                    reasons.append(f"terminal identity {terminal!r} occurs on multiple nets")
                result[terminal] = net
        if set(result) != set(partition.cluster_of):
            reasons.append("terminal inventory and cluster partition contain different identities")
        return result

    ref_nets = terminal_nets(reference)
    cand_nets = terminal_nets(candidate)
    if vanished_nets:
        reasons.append(
            f"{len(vanished_nets)} net(s) present in the reference are absent from "
            f"the candidate: {list(vanished_nets[:5])}"
        )
    if vanished:
        reasons.append(
            f"{len(vanished)} terminal(s) present in the reference are absent from "
            f"the candidate: {list(vanished[:5])}"
        )
    changed_net = tuple(sorted(
        terminal for terminal in ref_terminals & cand_terminals
        if ref_nets.get(terminal) != cand_nets.get(terminal)
    ))
    if changed_net:
        reasons.append(
            f"{len(changed_net)} physical pad(s) changed net membership: "
            f"{list(changed_net[:5])}"
        )

    # Group reference terminals by (net, reference cluster) and ask what the
    # candidate does with the survivors of each group.
    groups: dict[tuple[str, str], list[str]] = {}
    for net, terminals in reference.net_terminals.items():
        for terminal in terminals:
            cluster = reference.cluster_of.get(terminal)
            if cluster is None:
                continue
            groups.setdefault((net, cluster), []).append(terminal)

    split: list[tuple[str, str, str]] = []
    for (net, _cluster), members in groups.items():
        if len(members) < 2:
            continue
        survivors = [t for t in members if t in cand_terminals]
        if len(survivors) < 2:
            continue
        target = candidate.cluster_of.get(survivors[0])
        for other in survivors[1:]:
            if candidate.cluster_of.get(other) != target:
                split.append((net, survivors[0], other))
    if split:
        reasons.append(
            f"{len(split)} previously connected terminal pair(s) are no longer "
            "connected"
        )

    return TerminalDelta(
        ok=not reasons,
        reasons=tuple(reasons),
        vanished_terminals=vanished,
        vanished_nets=vanished_nets,
        new_terminals=new,
        split_relations=tuple(split),
        changed_net_membership=changed_net,
    )


def capture_terminals(engine: Any, *, limit: int | None = None) -> TerminalPartition:
    """Read the board's terminal partition through the engine's own connectivity.

    The native cluster accessor is checked against the complete native pad
    inventory. KIID plus copper geometry and inventory labels identify physical pads
    across saves; a UUID alone is insufficient on imported boards that reuse IDs, and ``REF.PAD`` alone
    is not unique when footprints contain repeated pad numbers or duplicate
    references. There is deliberately no point-probe fallback: it cannot prove
    connectivity mediated by filled zones.
    """
    rows = getattr(engine, "get_pad_cluster_members", None)
    if not callable(rows):
        return TerminalPartition(complete=False, reasons=(
            "native get_pad_cluster_members accessor is unavailable; anchor probes "
            "cannot establish zone-mediated connectivity",
        ))
    if limit is not None:
        return TerminalPartition(complete=False, reasons=(
            "bounded terminal captures cannot prove complete physical-pad inventory",
        ))

    try:
        net_names = {int(k): str(v) for k, v in engine.get_net_names().items()}
        inventory: dict[str, tuple[int, str]] = {}
        for pad in engine.get_pads():
            net = int(getattr(pad, "net_code", 0) or 0)
            if net <= 0:
                continue
            physical_id = str(getattr(pad, "physical_id", "") or "").strip()
            pad_uuid = str(getattr(pad, "uuid", "") or "").strip()
            if not physical_id:
                return TerminalPartition(complete=False, reasons=(
                    "native pad inventory lacks collision-safe UUID plus geometry identity",
                ))
            if not pad_uuid:
                return TerminalPartition(complete=False, reasons=(
                    "native pad inventory lacks stable UUIDs",
                ))
            if physical_id in inventory:
                return TerminalPartition(complete=False, reasons=(
                    f"native pad inventory has duplicate physical identity {physical_id!r}",
                ))
            label = f"{getattr(pad, 'footprint_ref', '')}.{getattr(pad, 'pad_name', '')}"
            inventory[physical_id] = (net, f"{label}@{pad_uuid}")

        seen: set[str] = set()
        memberships: dict[str, list[str]] = {}
        cluster_of: dict[str, str] = {}
        for row_index, (net_code, members) in enumerate(rows()):
            net = int(net_code)
            if net not in net_names:
                return TerminalPartition(complete=False, reasons=(
                    f"cluster row {row_index} names unknown net code {net}",
                ))
            physical_ids = [str(value) for value in members]
            if not physical_ids:
                return TerminalPartition(complete=False, reasons=(
                    f"cluster row {row_index} has no physical pad members",
                ))
            if len(set(physical_ids)) != len(physical_ids):
                return TerminalPartition(complete=False, reasons=(
                    f"cluster row {row_index} repeats a physical pad identity",
                ))
            for physical_id in physical_ids:
                if physical_id not in inventory:
                    return TerminalPartition(complete=False, reasons=(
                        f"cluster membership references unknown physical identity "
                        f"{physical_id!r}",
                    ))
                if physical_id in seen:
                    return TerminalPartition(complete=False, reasons=(
                        f"physical identity {physical_id!r} occurs in multiple clusters",
                    ))
                if inventory[physical_id][0] != net:
                    return TerminalPartition(complete=False, reasons=(
                        f"physical identity {physical_id!r} has inconsistent net membership",
                    ))
                seen.add(physical_id)
            representative = f"net:{net}:cluster:{row_index}"
            for physical_id in physical_ids:
                terminal = f"{inventory[physical_id][1]}@{physical_id}"
                memberships.setdefault(net_names[net], []).append(terminal)
                cluster_of[terminal] = representative

        missing = set(inventory) - seen
        if missing:
            labels = [inventory[item][1] for item in sorted(missing)[:5]]
            return TerminalPartition(complete=False, reasons=(
                f"native cluster accessor omitted {len(missing)} net-assigned pads "
                f"from complete inventory: {labels}",
            ))
        if not inventory:
            return TerminalPartition(complete=False, reasons=(
                "native inventory exposed no net-assigned physical pads",
            ))
        return TerminalPartition(
            net_terminals={key: tuple(sorted(value)) for key, value in memberships.items()},
            cluster_of=cluster_of,
        )
    except Exception as exc:  # noqa: BLE001 - unreadable capture is not proof
        return TerminalPartition(complete=False, reasons=(
            f"native terminal capture failed: {type(exc).__name__}: {exc}",
        ))
