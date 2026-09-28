"""Restoring serialized metadata KiCad's writer drops from non-copper graphics.

KiCad refuses to give a non-copper item a net (``PCB_SHAPE::SetLayer`` clears it,
``BOARD_CONNECTED_ITEM::SetNetCode`` coerces it to 0), so a ``(net N)`` token an
importer wrote on a ``gr_*`` item on e.g. ``Dwgs.User`` is inert natively and the
writer drops it on the first save. These tests pin the restore: only those
tokens, only for a unique UUID and a net that exists by name on both sides, never
geometry, verified before writing and idempotent.
"""

from __future__ import annotations

from pcb_world.agent.serialized_metadata import (
    restore_board_metadata,
    restore_noncopper_graphic_metadata,
)


def _board(net_name: str = "NET5", *, net_index: int = 5) -> str:
    return (
        "(kicad_pcb\n"
        "  (version 20241229)\n"
        '  (generator "unit-test")\n'
        f'  (net {net_index} "{net_name}")\n'
    )


def _graphic(uuid: str, *, net: int | None = None, locked: bool = False,
             layer: str = "Dwgs.User", start: int = 1,
             with_uuid: bool = True) -> str:
    extra = f"    (net {net})\n" if net is not None else ""
    locked_line = "    (locked yes)\n" if locked else ""
    uuid_line = f'    (uuid "{uuid}")\n' if with_uuid else ""
    return (
        "  (gr_line\n"
        f"    (start {start} 1)\n"
        f"    (end {start + 1} 2)\n"
        "    (stroke (width 0.2) (type default))\n"
        f'    (layer "{layer}")\n'
        f"{locked_line}"
        f"{extra}"
        f"{uuid_line}"
        "  )\n"
    )


UUID = "aaaa1111-2222-4333-8444-555566667777"


def test_a_dropped_net_token_is_restored_by_uuid():
    source = _board() + _graphic(UUID, net=5) + ")\n"
    saved = _board() + _graphic(UUID) + ")\n"
    result = restore_noncopper_graphic_metadata(source, saved)
    assert result.ok, result.problems
    assert result.restored == {"net": 1}
    assert result.changed
    assert "(net 5)" in result.text
    # Nothing else moved: same geometry tokens, same order.
    assert result.text.count("(start 1 1)") == 1
    assert result.text.index("(net 5)") < result.text.index("(uuid")


def test_a_restored_board_is_unchanged_when_restored_again():
    source = _board() + _graphic(UUID, net=5) + ")\n"
    saved = _board() + _graphic(UUID) + ")\n"
    once = restore_noncopper_graphic_metadata(source, saved)
    twice = restore_noncopper_graphic_metadata(source, once.text)
    assert once.ok and twice.ok
    assert twice.text == once.text
    assert not twice.changed


def test_a_deliberate_unlock_is_never_undone():
    """``locked`` is read (shove/cleanup honour it), so it is not restored.

    Only inert metadata may be copied back: a session that deliberately unlocked
    an item must keep it unlocked across a save.
    """
    source = _board() + _graphic(UUID, net=5, locked=True) + ")\n"
    saved = _board() + _graphic(UUID) + ")\n"
    result = restore_noncopper_graphic_metadata(source, saved)
    assert result.ok, result.problems
    assert result.restored == {"net": 1}
    assert "(net 5)" in result.text
    assert "(locked yes)" not in result.text


def test_a_renumbered_net_table_restores_the_saved_code():
    """The token must name the same net *name*, using the saved table's code."""
    source = _board("NET5", net_index=5) + _graphic(UUID, net=5) + ")\n"
    saved = (
        "(kicad_pcb\n"
        "  (version 20241229)\n"
        "  (generator \"unit-test\")\n"
        "  (net 12 \"NET5\")\n"
        "  (net 7 \"OTHER\")\n"
        + _graphic(UUID) + ")\n"
    )
    result = restore_noncopper_graphic_metadata(source, saved)
    assert result.ok, result.problems
    assert result.restored == {"net": 1}
    assert "(net 12)" in result.text
    assert "(net 5)" not in result.text


def test_an_ambiguous_saved_net_name_is_skipped():
    source = _board("NET5", net_index=5) + _graphic(UUID, net=5) + ")\n"
    saved = (
        "(kicad_pcb\n"
        "  (version 20241229)\n"
        "  (generator \"unit-test\")\n"
        "  (net 12 \"NET5\")\n"
        "  (net 7 \"NET5\")\n"
        + _graphic(UUID) + ")\n"
    )
    result = restore_noncopper_graphic_metadata(source, saved)
    assert result.ok
    assert not result.changed
    assert any("ambiguous in the saved board" in entry["reason"]
               for entry in result.skipped)


def test_copper_graphics_are_not_touched():
    """A copper shape's net is written natively; the restore leaves it alone."""
    source = _board() + _graphic(UUID, net=5, layer="F.Cu") + ")\n"
    saved = _board() + _graphic(UUID, layer="F.Cu") + ")\n"
    result = restore_noncopper_graphic_metadata(source, saved)
    assert result.ok
    assert not result.changed
    assert result.text == saved


def test_a_net_that_does_not_exist_by_name_on_both_sides_is_skipped():
    source = _board("NET5") + _graphic(UUID, net=5) + ")\n"
    saved = _board("SOMETHING_ELSE") + _graphic(UUID) + ")\n"
    result = restore_noncopper_graphic_metadata(source, saved)
    assert result.ok
    assert not result.changed
    assert any("does not exist in the saved board" in entry["reason"]
               for entry in result.skipped)


def test_geometry_from_the_saved_board_is_never_replaced():
    source = _board() + _graphic(UUID, net=5, start=100) + ")\n"
    saved = _board() + _graphic(UUID, start=7) + ")\n"
    result = restore_noncopper_graphic_metadata(source, saved)
    assert result.ok, result.problems
    assert result.restored == {"net": 1}
    assert "(start 7 1)" in result.text          # the saved geometry stays
    assert "(start 100 1)" not in result.text


def test_a_uuid_that_is_not_unique_in_the_source_is_skipped():
    source = (_board() + _graphic(UUID, net=5)
              + _graphic(UUID, net=5, start=9) + ")\n")
    saved = _board() + _graphic(UUID) + ")\n"
    result = restore_noncopper_graphic_metadata(source, saved)
    assert result.ok
    assert not result.changed
    assert any("not unique in the source" in entry["reason"]
               for entry in result.skipped)


def test_an_item_without_an_identity_token_is_skipped():
    source = _board() + _graphic(UUID, net=5, with_uuid=False) + ")\n"
    saved = _board() + _graphic(UUID) + ")\n"
    result = restore_noncopper_graphic_metadata(source, saved)
    assert result.ok
    assert not result.changed


def test_the_file_level_restore_writes_only_when_verified(tmp_path):
    source = tmp_path / "source.kicad_pcb"
    saved = tmp_path / "saved.kicad_pcb"
    source.write_text(_board() + _graphic(UUID, net=5) + ")\n", encoding="utf-8")
    saved.write_text(_board() + _graphic(UUID) + ")\n", encoding="utf-8")

    result = restore_board_metadata(source, saved)
    assert result.ok and result.changed
    assert "(net 5)" in saved.read_text(encoding="utf-8")

    # A second run is a no-op on the file.
    before = saved.read_bytes()
    again = restore_board_metadata(source, saved)
    assert again.ok and not again.changed
    assert saved.read_bytes() == before


def test_an_unparsable_side_is_reported_not_raised():
    result = restore_noncopper_graphic_metadata("(kicad_pcb", _board() + ")\n")
    assert not result.ok
    assert any("cannot parse" in problem for problem in result.problems)
