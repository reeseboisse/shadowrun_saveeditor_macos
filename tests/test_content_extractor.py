"""Content-pack item extraction.

We can't ship the games' content files, so these build ItemDef payloads
with the same serializer the engine uses and verify the extractor reads
them back. The field layout mirrors a real `healthpack_hi.item.bytes`
(confirmed by hex dump): id=tag1, type=tag2, uirep=tag3 with
icon=1/name=2/description=3. Real-install validation is done by running
the extractor against an actual ContentPacks directory.
"""

from __future__ import annotations

from shadowrun_editor.content_extractor import parse_item_def
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
