"""Content-pack item extraction.

We can't ship the games' content files, so these build ItemDef payloads
with the same serializer the engine uses and verify the extractor reads
them back. The field layout mirrors a real `healthpack_hi.item.bytes`
(confirmed by hex dump): id=tag1, type=tag2, uirep=tag3 with
icon=1/name=2/description=3. Real-install validation is done by running
the extractor against an actual ContentPacks directory.
"""

from __future__ import annotations

from pathlib import Path

from shadowrun_editor.content_extractor import (
    build_catalog,
    parse_char_sheet,
    parse_item_def,
)
from shadowrun_editor.protobuf_engine import (
    Field,
    WIRE_LEN,
    WIRE_VARINT,
    serialize_message,
)


def _make_item(prefab: str, *, name=None, desc=None, icon=None,
               type_value=None) -> bytes:
    top: list[Field] = [Field(tag=1, wire=WIRE_LEN, value=prefab.encode(), dirty=True)]
    if type_value is not None:
        top.append(Field(tag=2, wire=WIRE_VARINT, value=type_value, dirty=True))
    uirep_kids: list[Field] = []
    if icon is not None:
        uirep_kids.append(Field(tag=1, wire=WIRE_LEN, value=icon.encode(), dirty=True))
    if name is not None:
        uirep_kids.append(Field(tag=2, wire=WIRE_LEN, value=name.encode(), dirty=True))
    if desc is not None:
        uirep_kids.append(Field(tag=3, wire=WIRE_LEN, value=desc.encode(), dirty=True))
    if uirep_kids:
        top.append(Field(tag=3, wire=WIRE_LEN, value=b"", children=uirep_kids, dirty=True))
    return serialize_message(top)


def test_parse_full_item_like_healthpack_hi() -> None:
    data = _make_item(
        "HealthPack_hi",
        type_value=9,
        icon="icon_medkit3",
        name="Premium Medkit",
        desc="The premium medkit that heals you or any team member.",
    )
    e = parse_item_def(data)
    assert e is not None
    assert e.prefab == "HealthPack_hi"
    assert e.name == "Premium Medkit"
    assert e.description.startswith("The premium medkit")
    assert e.icon == "icon_medkit3"
    assert e.type_value == 9
    j = e.to_json()
    assert j["name"] == "Premium Medkit" and "description" in j


def test_parse_item_without_uirep_still_yields_prefab() -> None:
    e = parse_item_def(_make_item("CyberdeckSony"))
    assert e is not None and e.prefab == "CyberdeckSony"
    assert e.name is None
    assert e.to_json() == {}  # nothing presentational to emit


def test_parse_item_with_name_but_no_description() -> None:
    e = parse_item_def(_make_item("AR 3 Colt M23", name="Colt M23"))
    assert e is not None
    assert e.name == "Colt M23"
    assert e.description is None


def test_parse_empty_or_garbage_returns_none() -> None:
    # No tag-1 id present.
    only_type = serialize_message([Field(tag=2, wire=WIRE_VARINT, value=1, dirty=True)])
    assert parse_item_def(only_type) is None


# --- character base sheets ------------------------------------------------- #

def _make_char_sheet(sheet_id: str, base: dict[str, int]) -> bytes:
    # Character message: tag1 id, tag4 stats (Attributes). Attributes tags:
    # body=1 quickness=2 strength=3 charisma=4 intelligence=5 willpower=6
    # essence=7 magic=8 reaction=9.
    tagmap = {"body": 1, "quickness": 2, "strength": 3, "charisma": 4,
              "intelligence": 5, "willpower": 6, "essence": 7, "magic": 8,
              "reaction": 9}
    stats_kids = [Field(tag=tagmap[n], wire=WIRE_VARINT, value=v, dirty=True)
                  for n, v in base.items()]
    top = [
        Field(tag=1, wire=WIRE_LEN, value=sheet_id.encode(), dirty=True),
        Field(tag=4, wire=WIRE_LEN, value=b"", children=stats_kids, dirty=True),
    ]
    return serialize_message(top)


def test_parse_char_sheet_reads_racial_base() -> None:
    # The real Elf base from the hex dump.
    data = _make_char_sheet("CG Elf None.ch_sht", {
        "body": 1, "quickness": 1, "strength": 1, "charisma": 2,
        "intelligence": 1, "willpower": 1, "essence": 6, "magic": 0,
        "reaction": 4,
    })
    parsed = parse_char_sheet(data)
    assert parsed is not None
    sid, attrs = parsed
    assert sid == "CG Elf None"          # ".ch_sht" stripped
    assert attrs["charisma"] == 2 and attrs["essence"] == 6
    assert attrs["body"] == 1 and attrs["magic"] == 0


def test_build_catalog_over_a_content_tree(tmp_path: Path) -> None:
    cp = tmp_path / "ContentPacks"
    items = cp / "HongKong" / "data" / "items"
    chars = cp / "shadowrun_core" / "data" / "chars"
    items.mkdir(parents=True)
    chars.mkdir(parents=True)
    (items / "healthpack_hi.item.bytes").write_bytes(
        _make_item("HealthPack_hi", name="Premium Medkit",
                   desc="Heals.", icon="icon_medkit3", type_value=9))
    (chars / "cg elf none.ch_sht.bytes").write_bytes(
        _make_char_sheet("CG Elf None.ch_sht", {"charisma": 2, "essence": 6}))
    # An NPC sheet that must be skipped (doesn't start with "cg ").
    (chars / "npc_guard.ch_sht.bytes").write_bytes(
        _make_char_sheet("npc_guard.ch_sht", {"body": 9}))

    cat = build_catalog(cp, "hongkong")
    assert cat["format"] == "shadowrun-editor-catalog/1"
    assert cat["item_count"] == 1
    assert cat["items"]["HealthPack_hi"]["name"] == "Premium Medkit"
    assert cat["base_sheet_count"] == 1
    assert "CG Elf None" in cat["base_sheets"]
    assert "npc_guard" not in cat["base_sheets"]
    assert cat["base_sheets"]["CG Elf None"]["charisma"] == 2
