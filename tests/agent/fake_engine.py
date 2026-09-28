"""A tiny stand-in for ``KiCadEngine`` for the pure-Python tests.

It models only what :class:`pcb_world.agent.session.AgentSession` relies on:
copper clusters, a routing session, an item list whose content feeds the
geometry digest, and a checkpoint/restore that really does round-trip the state
(so a test can detect a session that claims a rollback it did not perform).

It is deliberately *not* a router: the native behaviour is covered by the
native tests, which skip when no engine build is present.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

from pcb_world.engine.layer_mapping import LayerMapping
from pcb_world.engine.wire import DesignRules, NetClassInfo
@dataclass
class BBox:
    x_mm: float = 0.0
    y_mm: float = 0.0
    width_mm: float = 50.0
    height_mm: float = 30.0


@dataclass
class SessionState:
    state_code: int = 0
    is_routing: bool = False
    is_dragging: bool = False
    is_placing_via: bool = False
    current_layer: int = -1
    route_head: tuple = (0.0, 0.0, -1.0)
    current_net_code: int = -1
    routing_target: tuple = (0.0, 0.0, -1.0)


@dataclass
class TrackInfo:
    x1_mm: float
    y1_mm: float
    x2_mm: float
    y2_mm: float
    width_mm: float = 0.25
    layer: int = 1
    net_code: int = 1
    net_name: str = "NET1"
    uuid: str = "t"


@dataclass
class ViaInfo:
    x_mm: float
    y_mm: float
    diameter_mm: float = 0.6
    drill_mm: float = 0.3
    top_layer: int = 1
    bottom_layer: int = 2
    net_code: int = 1
    net_name: str = "NET1"
    uuid: str = "v"


@dataclass
class PadInfo:
    x_mm: float
    y_mm: float
    width_mm: float = 1.0
    height_mm: float = 1.0
    layer: int = 1
    net_code: int = 1
    net_name: str = "NET1"
    pad_name: str = "1"
    footprint_ref: str = "P"
    pad_type: str = "smd"
    shape: str = "roundrect"


@dataclass
class FakeState:
    """Everything a checkpoint must round-trip."""

    clusters: dict[str, set[tuple[float, float, int]]] = field(default_factory=dict)
    tracks: list[TrackInfo] = field(default_factory=list)
    vias: list[ViaInfo] = field(default_factory=list)
    violations: list = field(default_factory=list)
    ratsnest: list = field(default_factory=list)
    session: SessionState = field(default_factory=SessionState)
    pending_via: bool = False


@dataclass
class FakeViolation:
    """Mirror of ``pcb_world.engine.wire.DRCViolation``."""

    error_code: int
    error_type: str
    message: str = ""
    x_mm: float = 0.0
    y_mm: float = 0.0
    layer: int = 0
    net_names: list = field(default_factory=list)
    severity: int = 0x20
    item_a: str = ""
    item_b: str = ""


class FakeEngine:
    """See the module docstring. ``fail_fix_at`` makes a chosen target fail."""

    def __init__(
        self,
        *,
        clusters: dict[str, set[tuple[float, float, int]]] | None = None,
        pads: list[PadInfo] | None = None,
        copper_layers: int = 2,
        fail_fix_at: set[tuple[float, float]] | None = None,
        fail_restore: bool = False,
        restore_exception: str = "",
        restore_silently_wrong: bool = False,
        mutate_before_fail: bool = False,
        breaking_reads: bool = False,
        rules_path: str = "",
        project_loaded: bool = True,
        drc_rules_error: str = "",
        drc_failure: str = "",
        drc_fail_after_calls: int = 0,
        drc_duration_s: float = 0.0,
        drc_duration_after_calls: int = 0,
        drc_durations: tuple[float, ...] = (),
        violation_on_fix: FakeViolation | None = None,
        #: Every violation appended to the board when a fix lands. Together with
        #: (not instead of) ``violation_on_fix``: one refusal that adds more
        #: findings than the delta's row sample holds needs several.
        violations_on_fix: tuple[FakeViolation, ...] = (),
        build_connectivity_failure: str = "",
        checkpoint_failure: str = "",
        restore_keeps_session: bool = False,
        drc_sets_rules_error: str = "",
        ratsnest: list | None = None,
        project_path: str = "/tmp/fake_board.kicad_pro",
        restore_target_override: tuple | None = None,
        tracks: list | None = None,
    ) -> None:
        self.state = FakeState(
            clusters=dict(clusters or {}), ratsnest=list(ratsnest or []),
            tracks=list(tracks or []),
        )
        self.pads = list(pads or [])
        self.copper_layers = copper_layers
        # The engine's own human<->board layer map, exactly as KiCadEngine exposes
        # it: LayerResolver reads this instead of guessing from board contents.
        self.layer_map = LayerMapping(copper_layers)
        self.fail_fix_at = set(fail_fix_at or set())
        self.fail_restore = fail_restore
        #: Models a backend that cannot be asked to restore *at all* — the owned
        #: engine child was reaped at its native deadline, so the checkpoint the
        #: transaction needs is gone with the process. ``fail_restore`` is the
        #: different shape: a live child that answers "no".
        self.restore_exception = restore_exception
        self.restore_silently_wrong = restore_silently_wrong
        self.mutate_before_fail = mutate_before_fail
        self.breaking_reads = breaking_reads
        self.rules_path = rules_path
        self.project_loaded = project_loaded
        self.drc_rules_error = drc_rules_error
        self.drc_failure = drc_failure
        #: How many DRC calls ``drc_failure`` lets through before it raises. A test
        #: uses this to let the startup and transaction baselines succeed and reap
        #: the child only on the acceptance pass of the copper it would keep.
        self.drc_fail_after_calls = int(drc_fail_after_calls)
        #: What a DRC call costs in the caller's simulated time, from
        #: ``drc_duration_after_calls`` onward. The engine does not spend that time
        #: itself; it asks ``advance_clock`` to move the caller's clock and checks
        #: the answer against ``allowance_provider``, which is how the real client
        #: enforces an absolute deadline. See ``_account``.
        self.drc_duration_s = float(drc_duration_s)
        self.drc_duration_after_calls = int(drc_duration_after_calls)
        #: Explicit per-call costs, when a test needs two DRCs in one run to cost
        #: different amounts (entry 0 is the first call). Takes precedence over
        #: ``drc_duration_s`` for the calls it covers.
        self.drc_durations = tuple(float(value) for value in drc_durations)
        #: Set by the test to the runner's own ``_native_timeout_s``: the allowance
        #: the real client would resolve for this dispatch. ``None`` leaves the
        #: deadline unenforced.
        self.allowance_provider = None
        #: Set by the test to move the caller's fake run clock.
        self.advance_clock = None
        #: Allowance sampled at each accounted dispatch, and whether one of them
        #: reaped the child (which makes every later call fail as the real client
        #: does on a dead socket).
        self.allowances: list[float | None] = []
        self.reaped = False
        self.violation_on_fix = violation_on_fix
        self.violations_on_fix = tuple(violations_on_fix)
        self.build_connectivity_failure = build_connectivity_failure
        self.checkpoint_failure = checkpoint_failure
        self.restore_keeps_session = restore_keeps_session
        self.drc_sets_rules_error = drc_sets_rules_error
        self.project_path = project_path
        # Models the engine recomputing the advisory routing target during a
        # world resync: applied after a successful restore.
        self.restore_target_override = restore_target_override
        self.release_calls = 0
        self.run_drc_calls = 0
        self.saved: list[tuple[str, str | None]] = []
        self._checkpoints: dict[int, FakeState] = {}
        self._next_handle = 1
        self.build_connectivity_calls = 0

    # -- state -------------------------------------------------------------

    @property
    def _s(self) -> FakeState:
        return self.state

    def _cluster_of(self, x: float, y: float, layer: int) -> str | None:
        for key, anchors in self._s.clusters.items():
            if (float(x), float(y), int(layer)) in anchors:
                return key
        return None

    # -- reads -------------------------------------------------------------

    def get_board_bbox(self) -> BBox:
        return BBox()

    def get_copper_layer_count(self) -> int:
        if self.breaking_reads:
            raise RuntimeError("engine read failed")
        return self.copper_layers

    def get_routing_session_state(self) -> SessionState:
        if self.breaking_reads:
            raise RuntimeError("engine read failed")
        return copy.copy(self._s.session)

    def get_track_count(self) -> int:
        return len(self._s.tracks)

    def get_via_count(self) -> int:
        return len(self._s.vias)

    def get_unrouted_count(self) -> int:
        """Outstanding connections = ratsnest edges (mirrors the engine's meaning)."""
        return len(self._s.ratsnest)

    def get_tracks(self) -> list[TrackInfo]:
        return list(self._s.tracks)

    def get_vias(self) -> list[ViaInfo]:
        return list(self._s.vias)

    def get_pads(self) -> list[PadInfo]:
        return list(self.pads)

    def get_ratsnest(self) -> list:
        return list(self._s.ratsnest)

    def _refresh_ratsnest(self) -> None:
        """Drop edges whose endpoints are now in one cluster (mirrors the engine)."""
        kept = []
        for edge in self._s.ratsnest:
            joined = False
            for layer in range(1, self.copper_layers + 1):
                start_key = self._cluster_of(float(edge.x1_mm), float(edge.y1_mm), layer)
                target_key = self._cluster_of(float(edge.x2_mm), float(edge.y2_mm), layer)
                if start_key is not None and start_key == target_key:
                    joined = True
                    break
            if not joined:
                kept.append(edge)
        self._s.ratsnest = kept

    def get_net_names(self) -> dict[int, str]:
        names: dict[int, str] = {
            int(p.net_code): str(p.net_name) for p in self.pads if p.net_code > 0
        }
        for item in (*self._s.tracks, *self._s.vias):
            if int(item.net_code) > 0 and int(item.net_code) not in names:
                names[int(item.net_code)] = str(item.net_name)
        return names

    def get_pad_groups(self) -> dict[int, int]:
        """Clusters holding at least one pad, counted per net (mirrors the engine)."""
        groups: dict[int, int] = {}
        for anchors in self._s.clusters.values():
            nets = {
                int(pad.net_code)
                for pad in self.pads
                if (round(float(pad.x_mm), 4), round(float(pad.y_mm), 4))
                in {(a[0], a[1]) for a in anchors}
                and int(pad.net_code) > 0
            }
            for net in nets:
                groups[net] = groups.get(net, 0) + 1
        return groups

    def get_netclass_for_net(self, net_code: int) -> NetClassInfo:
        return NetClassInfo(
            name="Default", clearance_mm=0.2, track_width_mm=0.25,
            via_diameter_mm=0.6, via_drill_mm=0.3,
        )

    def get_design_rules(self) -> DesignRules:
        return DesignRules(
            min_clearance_mm=0.2, min_track_width_mm=0.25,
            min_via_diameter_mm=0.6, min_through_hole_mm=0.3,
            min_hole_to_hole_mm=0.2, copper_edge_clearance_mm=0.2,
            default_netclass=self.get_netclass_for_net(0),
        )

    def get_project_path(self) -> str:
        return self.project_path

    def save(self, output_path: str, project_output_path: str | None = None) -> None:
        """Stand-in for the engine save: writes a small JSON board image."""
        import json
        import pathlib

        def row(item):
            return list(item._asdict().items()) if hasattr(item, "_asdict") else repr(item)

        payload = {
            "tracks": [row(t) for t in self._s.tracks],
            "vias": [row(v) for v in self._s.vias],
            "clusters": {k: sorted(v) for k, v in self._s.clusters.items()},
        }
        pathlib.Path(output_path).write_text(json.dumps(payload), encoding="utf-8")
        if project_output_path:
            pathlib.Path(project_output_path).write_text("{}", encoding="utf-8")
        self.saved.append((output_path, project_output_path))

    def get_connected_points(self, x: float, y: float, layer: int):
        key = self._cluster_of(x, y, layer)
        if key is None:
            return []
        # Same shape the real KiCadEngine returns: (x_mm, y_mm, human_layer).
        return [(a[0], a[1], a[2]) for a in sorted(self._s.clusters[key])]

    def is_routing(self) -> bool:
        return bool(self._s.session.is_routing)

    def is_dragging(self) -> bool:
        return False

    def get_current_net_code(self) -> int:
        return int(self._s.session.current_net_code)

    def get_current_layer(self) -> int:
        return int(self._s.session.current_layer)

    # -- rule context / DRC ------------------------------------------------

    def get_routing_rules_path(self) -> str:
        return self.rules_path

    def was_routing_rules_loaded_from_file(self) -> bool:
        return bool(self.rules_path)

    def get_last_drc_rules_load_error(self) -> str:
        return self.drc_rules_error

    def was_project_loaded_from_file(self) -> bool:
        return self.project_loaded

    def run_drc(self, rules_path: str = "") -> list:
        self.run_drc_calls += 1
        self._account(self._drc_duration())
        if self.drc_failure and self.run_drc_calls > self.drc_fail_after_calls:
            raise RuntimeError(self.drc_failure)
        if self.drc_sets_rules_error:
            # Models the engine falling back and reporting it afterwards — the
            # side-channel a returned violation list cannot express.
            self.drc_rules_error = self.drc_sets_rules_error
        return list(self._s.violations)

    def _drc_duration(self) -> float:
        """What this DRC call costs in the caller's simulated time."""
        index = self.run_drc_calls - 1
        if index < len(self.drc_durations):
            return self.drc_durations[index]
        if self.run_drc_calls > self.drc_duration_after_calls:
            return self.drc_duration_s
        return 0.0

    def _account(self, seconds: float) -> None:
        """Model the client's absolute-deadline handling for one native dispatch.

        The real client resolves the caller's ``call_timeout_s`` callback at
        dispatch, bounds the socket by it, and reaps the child if the operation
        does not answer inside it. A test that wants that behaviour without a real
        engine wires ``allowance_provider`` to the runner's own callback and
        ``advance_clock`` to its fake run clock.
        """
        if seconds <= 0:
            return
        allowance = (
            None if self.allowance_provider is None else self.allowance_provider()
        )
        self.allowances.append(allowance)
        if self.advance_clock is not None:
            self.advance_clock(seconds)
        if allowance is not None and seconds > allowance:
            self.reaped = True
            raise RuntimeError(
                "owned engine operation 'call' exceeded its "
                f"{allowance:.3f} s deadline; child reaped"
            )

    # -- mutations ---------------------------------------------------------

    def build_connectivity(self) -> None:
        if self.build_connectivity_failure:
            raise RuntimeError(self.build_connectivity_failure)
        self.build_connectivity_calls += 1

    def set_routing_mode(self, mode: int) -> None:
        self.routing_mode = int(mode)

    def set_corner_mode(self, mode: int) -> None:
        pass

    def start_route(self, x_mm: float, y_mm: float, layer: int) -> bool:
        key = self._cluster_of(x_mm, y_mm, layer)
        if key is None:
            return False
        self._s.session.is_routing = True
        self._s.session.current_layer = int(layer)
        self._s.session.route_head = (float(x_mm), float(y_mm), float(layer))
        self._s.session.routing_target = (0.0, 0.0, -1.0)
        # Net of the anchor's cluster owner is not modelled; tests use one net.
        self._s.session.current_net_code = 1
        return True

    def toggle_via(self) -> None:
        self._s.pending_via = True

    def reset_via_mode(self) -> None:
        self._s.pending_via = False

    def switch_layer(self, layer: int) -> bool:
        if 1 <= int(layer) <= self.copper_layers:
            self._s.session.current_layer = int(layer)
            head = self._s.session.route_head
            self._s.session.route_head = (head[0], head[1], float(layer))
            return True
        return False

    def pad_block_reason(self, x_mm, y_mm, *, item_radius_mm, for_via) -> str | None:
        return None

    def route_item_radius_mm(self, *, for_via: bool) -> float:
        return 0.3 if for_via else 0.125

    def fix_route(self, x_mm, y_mm, *args, **kwargs) -> bool:
        key = self._cluster_of(x_mm, y_mm, self._s.session.current_layer)
        if key is None or not self.is_routing():
            return False
        if (float(x_mm), float(y_mm)) in self.fail_fix_at:
            if self.mutate_before_fail:
                # Models a shove that displaced copper before giving up: the board
                # changed, the step failed, and only a real rollback undoes it.
                head = self._s.session.route_head
                self._s.tracks.append(TrackInfo(
                    x1_mm=float(head[0]), y1_mm=float(head[1]),
                    x2_mm=float(x_mm), y2_mm=float(y_mm),
                    layer=int(self._s.session.current_layer),
                ))
            return False
        head = self._s.session.route_head
        head_key = self._cluster_of(head[0], head[1], head[2])
        if head_key is not None and head_key != key:
            self._s.clusters[head_key] |= self._s.clusters[key]
            del self._s.clusters[key]
            self._refresh_ratsnest()
        self._s.tracks.append(TrackInfo(
            x1_mm=float(head[0]), y1_mm=float(head[1]),
            x2_mm=float(x_mm), y2_mm=float(y_mm),
            layer=int(self._s.session.current_layer),
        ))
        if kwargs.get("require_via") and self._s.pending_via:
            self._s.vias.append(ViaInfo(float(x_mm), float(y_mm)))
            self._s.pending_via = False
            new_layer = 2 if int(self._s.session.current_layer) == 1 else 1
            self._s.session.current_layer = new_layer
        self._s.session.route_head = (float(x_mm), float(y_mm), float(self._s.session.current_layer))
        if self.violation_on_fix is not None:
            self._s.violations.append(copy.deepcopy(self.violation_on_fix))
        for violation in self.violations_on_fix:
            self._s.violations.append(copy.deepcopy(violation))
        return True

    def finish(self, max_attempts: int = 5) -> bool:
        return False

    def cancel_route(self) -> None:
        self._s.session.is_routing = False

    # -- checkpoints -------------------------------------------------------

    def checkpoint(self) -> int:
        if self.checkpoint_failure:
            raise RuntimeError(self.checkpoint_failure)
        handle = self._next_handle
        self._next_handle += 1
        self._checkpoints[handle] = copy.deepcopy(self._s)
        return handle

    def restore(self, handle: int) -> bool:
        if self.reaped:
            # The child this checkpoint lives in is gone, so the restore cannot
            # even be asked — the shape a deadline-reaped transaction leaves.
            raise RuntimeError(
                "owned engine operation 'call' exceeded its 0.000 s deadline; "
                "child reaped"
            )
        if self.restore_exception:
            # The child this checkpoint lives in is gone; there is no answer to
            # give, which the session must report as an unverifiable rollback
            # rather than as a restore that returned false.
            raise RuntimeError(self.restore_exception)
        if self.fail_restore:
            return False
        saved = self._checkpoints.get(handle)
        if saved is None:
            return False
        if self.restore_silently_wrong:
            # Simulates a backend that claims success and does not roll back: the
            # session must notice via the geometry digest.
            return True
        if self.restore_keeps_session:
            # Copper comes back, router session state does not: models a backend
            # whose restore is only partial.
            session = self.state.session
            self.state = copy.deepcopy(saved)
            self.state.session = session
            return True
        self.state = copy.deepcopy(saved)
        if self.restore_target_override is not None:
            self.state.session.routing_target = tuple(self.restore_target_override)
        return True

    def has_checkpoint(self, handle: int) -> bool:
        return handle in self._checkpoints

    def release_checkpoint(self, handle: int) -> None:
        self.release_calls += 1
        self._checkpoints.pop(handle, None)

    @property
    def live_checkpoints(self) -> int:
        return len(self._checkpoints)
