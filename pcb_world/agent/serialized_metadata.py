"""Restore serialized board metadata that KiCad's writer drops.

KiCad's s-expression writer only emits ``(net N)`` for a shape whose net code is
positive (``pcb_io_kicad_sexpr.cpp``), and a shape on a non-copper layer can
never hold one:

* ``PCB_SHAPE::SetLayer()`` clears the net for any shape that is not on a copper
  layer (``pcb_shape.cpp``: ``if( !IsOnCopperLayer() ) SetNetCode( -1 );``);
* ``BOARD_CONNECTED_ITEM::SetNetCode()`` coerces a net to 0 for a non-copper item
  (``board_connected_item.cpp``: ``if( !IsOnCopperLayer() ) aNetCode = 0;``).

An importer can still write ``(net 5)`` on, say, a ``gr_line`` on ``Dwgs.User``.
The value is inert natively - KiCad itself refuses to give a non-copper item a
net, and connectivity/DRC never read it - but it is the user's serialized
metadata, and the first save silently drops it. This module copies exactly those
tokens back from the board a save came from, matched by the item's UUID and by
the net's *name* (never by a raw index into a possibly renumbered net table).

Geometry is never copied: only the tags in :data:`RESTORABLE_TAGS`, and only
when the saved item lacks them. The result is re-parsed and verified before it is
written, and running it twice changes nothing.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from pcb_world.agent.kicad_metadata import (
    Atom,
    SexprError,
    SList,
    _splice,
    parse,
)

#: Board-level graphic item tags. Footprint graphics are written relative to their
#: parent and are never touched by this module.
GRAPHIC_TAGS = (
    "gr_line", "gr_arc", "gr_rect", "gr_circle", "gr_poly", "gr_curve",
    "gr_bbox", "gr_text", "gr_textbox", "dimension", "target",
)

#: Child tokens this module is willing to copy back. Only ``net`` qualifies: it
#: is inert on a non-copper item (KiCad coerces the net of a non-copper item to
#: 0, so nothing reads it) and it is dropped by the writer. ``locked`` is
#: deliberately absent - it *is* read (it protects copper from shove and from the
#: cleanup pass), so copying it back would silently re-lock an item a later
#: session deliberately unlocked. Geometry tokens (start, end, pts, stroke,
#: width, layer, ...) are absent for the same reason.
RESTORABLE_TAGS = ("net",)

_COPPER_LAYER_SUFFIX = (".Cu",)


@dataclass
class RestoreResult:
    """What was restored, and why the rest was not."""

    ok: bool = False
    text: str = ""
    restored: dict[str, int] = field(default_factory=dict)   # tag -> count
    items_examined: int = 0
    skipped: list[dict[str, Any]] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.restored)

    def evidence(self) -> dict[str, Any]:
        return {
            "ok": bool(self.ok),
            "restored": dict(sorted(self.restored.items())),
            "items_examined": int(self.items_examined),
            "skipped": list(self.skipped[:20]),
            "skipped_total": len(self.skipped),
            "problems": list(self.problems),
        }


# ---------------------------------------------------------------------------
# Small readers over the shared parser
# ---------------------------------------------------------------------------


def _children(node: SList) -> Iterator[SList]:
    for item in node.items:
        if isinstance(item, SList):
            yield item


def _atom_child(node: SList, tag: str) -> Atom | None:
    """The value atom of the first ``(tag <atom>)`` child."""
    child = _child(node, tag)
    if child is not None and len(child.items) > 1 \
            and isinstance(child.items[1], Atom):
        return child.items[1]
    return None


def _child(node: SList, tag: str) -> SList | None:
    """The first ``(tag ...)`` child list itself (source position included)."""
    for child in _children(node):
        if child.head == tag:
            return child
    return None


def _own_uuid(item: SList) -> str | None:
    atom = _atom_child(item, "uuid")
    return atom.text if atom is not None else None


def _layer(item: SList) -> str | None:
    atom = _atom_child(item, "layer")
    return atom.text if atom is not None else None


def _is_copper(layer: str | None) -> bool:
    return bool(layer) and layer.endswith(_COPPER_LAYER_SUFFIX)


def _net_names(root: SList) -> dict[int, str]:
    """The board's own ``(net <code> "<name>")`` table."""
    names: dict[int, str] = {}
    for item in root.items:
        if not isinstance(item, SList) or item.head != "net":
            continue
        if len(item.items) < 3 or not isinstance(item.items[1], Atom) \
                or not isinstance(item.items[2], Atom):
            continue
        try:
            names[int(item.items[1].text)] = item.items[2].text
        except ValueError:
            continue
    return names


def _board_graphics(root: SList) -> tuple[dict[str, SList], set[str]]:
    """Unique-UUID board-level graphics, plus the UUIDs that are not unique."""
    items: dict[str, SList] = {}
    duplicated: set[str] = set()
    for item in root.items:
        if not isinstance(item, SList) or item.head not in GRAPHIC_TAGS:
            continue
        uuid = _own_uuid(item)
        if not uuid:
            continue
        if uuid in items:
            duplicated.add(uuid)
            continue
        items[uuid] = item
    for uuid in duplicated:
        items.pop(uuid, None)
    return items, duplicated


def _metadata_child(node: SList, tag: str) -> SList | None:
    return _child(node, tag)


def _form_without_metadata(value: Any) -> Any:
    """Structural form with the restorable tags removed, identity tokens kept."""
    if isinstance(value, Atom):
        return value.text
    return tuple(
        _form_without_metadata(item) for item in value.items
        if not (isinstance(item, SList) and item.head in RESTORABLE_TAGS)
    )


def _full_form(value: Any) -> Any:
    """Every token of a non-graphic item, verbatim."""
    if isinstance(value, Atom):
        return value.text
    return tuple(_full_form(item) for item in value.items)


def _document_form(root: SList) -> tuple:
    """Per-item forms: restorable metadata dropped for graphics, exact elsewhere."""
    out: list[Any] = []
    for item in root.items:
        if isinstance(item, SList) and item.head in GRAPHIC_TAGS:
            out.append(_form_without_metadata(item))
        elif isinstance(item, SList):
            out.append(_full_form(item))
        else:
            out.append(item.text)
    return tuple(out)


# ---------------------------------------------------------------------------
# Restoration
# ---------------------------------------------------------------------------


def restore_noncopper_graphic_metadata(
    source_text: str, saved_text: str,
) -> RestoreResult:
    """Copy dropped metadata tokens from ``source_text`` back into ``saved_text``.

    ``source_text`` is the board the save came from (the metadata authority);
    ``saved_text`` is what KiCad just wrote. Returns the restored text with the
    token counts; ``ok`` is only set once the result has been re-parsed and
    verified to carry exactly those tokens and nothing else.
    """
    result = RestoreResult()
    try:
        source_root = parse(source_text)
        saved_root = parse(saved_text)
    except SexprError as error:
        result.problems.append(
            f"cannot parse for metadata restore: {type(error).__name__}: {error}"
        )
        return result

    source_items, source_duplicates = _board_graphics(source_root)
    saved_items, saved_duplicates = _board_graphics(saved_root)
    source_nets = _net_names(source_root)
    saved_nets = _net_names(saved_root)

    edits: list[tuple[int, int, str]] = []
    restored: dict[str, int] = {}
    for uuid in sorted(source_duplicates):
        result.skipped.append(
            {"uuid": uuid, "reason": "uuid not unique in the source board"}
        )
    for uuid, source_item in sorted(source_items.items()):
        saved_item = saved_items.get(uuid)
        if saved_item is None:
            if uuid in saved_duplicates:
                result.skipped.append({"uuid": uuid, "reason": "uuid not unique in the saved board"})
            continue
        if source_item.head != saved_item.head:
            result.skipped.append({
                "uuid": uuid,
                "reason": f"saved item is {saved_item.head}, source is {source_item.head}",
            })
            continue
        layer = _layer(source_item)
        if _is_copper(layer) or _is_copper(_layer(saved_item)):
            continue                     # copper metadata is written natively
        result.items_examined += 1

        anchor = _child(saved_item, "uuid")
        for tag in RESTORABLE_TAGS:
            source_token = _metadata_child(source_item, tag)
            if source_token is None:
                continue
            if _metadata_child(saved_item, tag) is not None:
                continue
            replacement: str | None = None
            if tag == "net":
                replacement = _mapped_net(
                    source_token, source_nets, saved_nets, uuid, result,
                )
                if replacement is None:
                    continue
            if anchor is None:
                result.skipped.append({"uuid": uuid, "reason": f"no uuid anchor for ({tag} ...)"})
                continue
            line_start = saved_text.rfind("\n", 0, anchor.start) + 1
            indent = saved_text[line_start:anchor.start]
            # The saved board's own net table is authoritative: the token is
            # rewritten with *its* code for the same net name, never the source's
            # numeric code copied across a possible renumbering.
            raw = replacement if replacement is not None \
                else source_text[source_token.start:source_token.end]
            edits.append((line_start, line_start, f"{indent}{raw}\n"))
            restored[tag] = restored.get(tag, 0) + 1

    if not edits:
        result.text = saved_text
        result.restored = {}
        result.ok = not result.problems
        return result

    restored_text = _splice(saved_text, edits)
    result.text = restored_text
    result.restored = restored
    result.problems.extend(
        _verify(saved_text, restored_text, source_items,
                source_nets=source_nets, saved_nets=saved_nets)
    )
    result.ok = not result.problems
    return result


def _mapped_net(
    source_token: SList, source_nets: Mapping[int, str],
    saved_nets: Mapping[int, str], uuid: str, result: RestoreResult,
) -> str | None:
    """The saved board's token for the same net *name*, or ``None`` to skip.

    Net codes are file-local indices: the writer renumbers them
    (``m_mapping->Translate``), so copying the source's numeric code would point
    the restored token at whichever net now holds that index. The name is the
    stable key; the returned text carries the saved table's code for it.
    """
    if len(source_token.items) != 2 or not isinstance(source_token.items[1], Atom):
        result.skipped.append({"uuid": uuid, "reason": "(net ...) is not a level value"})
        return None
    try:
        code = int(source_token.items[1].text)
    except ValueError:
        result.skipped.append({"uuid": uuid, "reason": "(net ...) is not an index"})
        return None
    name = source_nets.get(code)
    if name is None:
        result.skipped.append(
            {"uuid": uuid, "reason": f"net {code} is not declared in the source"})
        return None
    saved_codes = sorted(key for key, value in saved_nets.items() if value == name)
    if not saved_codes:
        result.skipped.append({
            "uuid": uuid,
            "reason": f"net {code} ({name!r}) does not exist in the saved board",
        })
        return None
    if len(saved_codes) > 1:
        result.skipped.append({
            "uuid": uuid,
            "reason": f"net name {name!r} is ambiguous in the saved board "
                      f"({saved_codes}); the token is not rewritten",
        })
        return None
    return f"(net {saved_codes[0]})"


def _verify(
    saved_text: str, restored_text: str,
    source_items: Mapping[str, SList],
    *, source_nets: Mapping[int, str], saved_nets: Mapping[int, str],
) -> list[str]:
    """Prove the restore added exactly the tokens and nothing else.

    ``source_nets`` and ``saved_nets`` are the two boards' own net tables: a
    restored token must name the same net *by name* as the token it came from,
    not necessarily carry the same numeric code (the writer may have renumbered
    the table between the two files).
    """
    problems: list[str] = []
    try:
        saved_root = parse(saved_text)
        restored_root = parse(restored_text)
    except SexprError as error:
        return [f"the restored board does not parse: {type(error).__name__}: {error}"]

    # 1. Everything except the restorable tags of a board-level graphic is
    #    token-for-token the saved board.
    if _document_form(saved_root) != _document_form(restored_root):
        problems.append(
            "the restore changed something other than the metadata tokens"
        )

    # 2. Each restored token names the same net as the token it came from.
    restored_items, _duplicates = _board_graphics(restored_root)
    for uuid, source_item in source_items.items():
        target_item = restored_items.get(uuid)
        if target_item is None:
            continue
        source_token = _metadata_child(source_item, "net")
        target_token = _metadata_child(target_item, "net")
        if source_token is None or target_token is None:
            continue
        wanted = _net_name_of(source_token, source_nets)
        got = _net_name_of(target_token, saved_nets)
        if wanted is None or got != wanted:
            problems.append(
                f"item {uuid}: restored (net ...) does not name the source "
                f"token's net ({got!r} vs {wanted!r})"
            )

    return problems


def _net_code_of(token: SList) -> int:
    if len(token.items) == 2 and isinstance(token.items[1], Atom):
        try:
            return int(token.items[1].text)
        except ValueError:
            return -1
    return -1


def _net_name_of(token: SList, nets: Mapping[int, str]) -> str | None:
    return nets.get(_net_code_of(token))


def restore_board_metadata(
    source_path: str | os.PathLike, saved_path: str | os.PathLike,
) -> RestoreResult:
    """File-level restore; writes ``saved_path`` only when verified and changed."""
    source = Path(source_path)
    saved = Path(saved_path)
    result = restore_noncopper_graphic_metadata(
        source.read_text(encoding="utf-8"), saved.read_text(encoding="utf-8"),
    )
    if result.ok and result.changed:
        saved.write_text(result.text, encoding="utf-8")
    return result
