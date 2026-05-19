"""
Schema extractor: reads ShadowrunDTO.dll (and optionally Assembly-CSharp.dll)
and produces a JSON schema bundle the editor can use to interpret save
files.

Mechanism: protobuf-net decorates each serializable property with
`[ProtoMember(tag, Name="...", IsRequired=..., DataFormat=...)]`. The .NET
PE format stores these as CustomAttribute blobs in the metadata heap, so we
can recover (tag → property-name) and the .NET type for every serializable
property without decompiling any IL.

Output bundle (one per game):

    {
      "game":      "dragonfall-dc",
      "version":   "1.2.7",
      "extracted_at": "2026-05-19",
      "messages": {
        "<TypeName>": {
          "fields": {
            "<tag>": {
              "name":        "<field_name>",
              "property":    "<C# property name>",
              "wire":        <wire-type 0/1/2/5>,
              "type":        "<primitive | enum:<EnumName> | message:<TypeName> | repeated:<inner>>",
              "is_required": <bool>,
              "data_format": <0|1|2>  # 0=Default, 1=ZigZag, 2=FixedSize
            }, ...
          }
        }, ...
      },
      "enums": {
        "<EnumName>": {"<MemberName>": <int value>, ...}
      }
    }

Run:
    python -m shadowrun_editor.extractor \\
        --dto reference/dlls/dragonfall/ShadowrunDTO.dll \\
        --enums reference/dlls/dragonfall/Assembly-CSharp.dll \\
        --game dragonfall-dc \\
        --out schemas/dragonfall.json
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import dnfile


def _s(x: Any) -> str:
    """Coerce a HeapItemString/HeapItem-ish to a plain Python str."""
    if x is None:
        return ""
    if isinstance(x, str):
        return x
    v = getattr(x, "value", None)
    if v is not None:
        return str(v)
    return str(x)


# --------------------------------------------------------------------------- #
# ECMA-335 signature parsing                                                  #
# --------------------------------------------------------------------------- #

# ElementType codes we care about; the rest become "object"/unknown.
ELEMENT_PRIMITIVES = {
    0x02: ("bool", 0),
    0x03: ("char", 0),
    0x04: ("int8", 0),
    0x05: ("uint8", 0),
    0x06: ("int16", 0),
    0x07: ("uint16", 0),
    0x08: ("int32", 0),
    0x09: ("uint32", 0),
    0x0A: ("int64", 0),
    0x0B: ("uint64", 0),
    0x0C: ("float", 5),
    0x0D: ("double", 1),
    0x0E: ("string", 2),
    0x18: ("intptr", 0),
    0x19: ("uintptr", 0),
    0x1C: ("object", 2),
}

ELEMENT_VALUETYPE = 0x11
ELEMENT_CLASS = 0x12
ELEMENT_VAR = 0x13
ELEMENT_GENERICINST = 0x15
ELEMENT_OBJECT = 0x1C
ELEMENT_SZARRAY = 0x1D


def _read_compressed_uint(buf: bytes, off: int) -> tuple[int, int]:
    """ECMA-335 II.23.2 compressed unsigned integer. Returns (value, consumed)."""
    b = buf[off]
    if (b & 0x80) == 0:
        return b, 1
    if (b & 0xC0) == 0x80:
        return ((b & 0x3F) << 8) | buf[off + 1], 2
    if (b & 0xE0) == 0xC0:
        return (
            ((b & 0x1F) << 24)
            | (buf[off + 1] << 16)
            | (buf[off + 2] << 8)
            | buf[off + 3],
            4,
        )
    raise ValueError(f"invalid compressed uint at offset {off}: 0x{b:02x}")


def _read_typedef_or_ref(buf: bytes, off: int) -> tuple[int, int, int]:
    """Read TypeDefOrRefOrSpecEncoded coded index. Returns (table, row, consumed).
    table: 0=TypeDef, 1=TypeRef, 2=TypeSpec."""
    value, c = _read_compressed_uint(buf, off)
    table = value & 0x3
    row = value >> 2
    return table, row, c


@dataclass
class SigType:
    kind: str               # primitive name, "enum", "message", "list", "object", "var", "unknown"
    name: str = ""          # for enum/message: the resolved type name
    inner: "SigType | None" = None  # for list / array
    wire: int | None = None
    namespace: str = ""

    def describe(self) -> str:
        if self.kind == "list":
            return f"repeated:{self.inner.describe() if self.inner else '?'}"
        if self.kind == "enum":
            return f"enum:{self.name}"
        if self.kind == "message":
            return f"message:{self.name}"
        return self.kind


def _resolve_type_name(pe: dnfile.dnPE, table: int, row: int) -> tuple[str, str]:
    """Return (namespace, type_name) for a TypeDefOrRef coded index."""
    mdt = pe.net.mdtables
    if table == 0:
        r = mdt.TypeDef.rows[row - 1]
        return _s(r.TypeNamespace), _s(r.TypeName)
    if table == 1:
        r = mdt.TypeRef.rows[row - 1]
        return _s(r.TypeNamespace), _s(r.TypeName)
    if table == 2:
        # TypeSpec — has a signature blob describing a constructed type.
        # We don't fully resolve these; return a placeholder.
        return "", "<typespec>"
    return "", "<unknown>"


def _is_enum_type(pe: dnfile.dnPE, table: int, row: int) -> bool:
    if table != 0:
        return False
    try:
        r = pe.net.mdtables.TypeDef.rows[row - 1]
    except IndexError:
        return False
    ext = r.Extends
    if not ext or not ext.row:
        return False
    ext_row = ext.row
    ns = _s(getattr(ext_row, "TypeNamespace", ""))
    nm = _s(getattr(ext_row, "TypeName", ""))
    return ns == "System" and nm == "Enum"


def _parse_type(buf: bytes, off: int, pe: dnfile.dnPE) -> tuple[SigType, int]:
    """Parse an ECMA-335 Type signature starting at off. Returns (SigType, consumed)."""
    start = off
    et = buf[off]
    off += 1
    if et in ELEMENT_PRIMITIVES:
        name, wire = ELEMENT_PRIMITIVES[et]
        return SigType(kind=name, wire=wire), off - start
    if et == ELEMENT_VALUETYPE or et == ELEMENT_CLASS:
        table, row, c = _read_typedef_or_ref(buf, off)
        off += c
        ns, nm = _resolve_type_name(pe, table, row)
        if et == ELEMENT_VALUETYPE and _is_enum_type(pe, table, row):
            return SigType(kind="enum", name=nm, namespace=ns, wire=0), off - start
        if et == ELEMENT_VALUETYPE:
            # A struct that isn't an enum — treated as a sub-message
            return SigType(kind="message", name=nm, namespace=ns, wire=2), off - start
        return SigType(kind="message", name=nm, namespace=ns, wire=2), off - start
    if et == ELEMENT_VAR:
        idx, c = _read_compressed_uint(buf, off)
        off += c
        return SigType(kind="var", name=f"T{idx}"), off - start
    if et == ELEMENT_GENERICINST:
        # GENERICINST CLASS|VALUETYPE TypeDefOrRef ArgCount Type...
        inst = buf[off]; off += 1
        table, row, c = _read_typedef_or_ref(buf, off)
        off += c
        ns, nm = _resolve_type_name(pe, table, row)
        argcount, c = _read_compressed_uint(buf, off)
        off += c
        args: list[SigType] = []
        for _ in range(argcount):
            sub, c2 = _parse_type(buf, off, pe)
            off += c2
            args.append(sub)
        # Recognize List<T>
        if nm == "List`1" and args:
            return SigType(kind="list", name="List", inner=args[0], wire=2), off - start
        return SigType(kind="message", name=nm, namespace=ns, wire=2), off - start
    if et == ELEMENT_SZARRAY:
        sub, c = _parse_type(buf, off, pe)
        off += c
        return SigType(kind="list", name="Array", inner=sub, wire=2), off - start
    if et == ELEMENT_OBJECT:
        return SigType(kind="object", wire=2), off - start
    return SigType(kind=f"et_0x{et:02x}", wire=None), off - start


def _parse_property_sig(blob: bytes, pe: dnfile.dnPE) -> SigType:
    """Parse a Property signature blob and return the property's value type."""
    # Property sig: 0x08 (PROPERTY) or 0x28 (PROPERTY|HASTHIS) prolog,
    # ParamCount, RetType, Param*N.
    off = 0
    prolog = blob[off]
    off += 1
    if (prolog & 0x08) == 0:
        # Not a property signature — bail
        return SigType(kind="unknown")
    _param_count, c = _read_compressed_uint(blob, off)
    off += c
    ret_type, _ = _parse_type(blob, off, pe)
    return ret_type


# --------------------------------------------------------------------------- #
# CustomAttribute blob parsing                                                #
# --------------------------------------------------------------------------- #

# .NET serialization element types (subset used in named args)
SER_TYPE_BOOL = 0x02
SER_TYPE_CHAR = 0x03
SER_TYPE_I1 = 0x04
SER_TYPE_U1 = 0x05
SER_TYPE_I2 = 0x06
SER_TYPE_U2 = 0x07
SER_TYPE_I4 = 0x08
SER_TYPE_U4 = 0x09
SER_TYPE_I8 = 0x0A
SER_TYPE_U8 = 0x0B
SER_TYPE_R4 = 0x0C
SER_TYPE_R8 = 0x0D
SER_TYPE_STRING = 0x0E
SER_TYPE_TYPE = 0x50
SER_TYPE_BOXED = 0x51
SER_TYPE_FIELD = 0x53
SER_TYPE_PROPERTY = 0x54
SER_TYPE_ENUM = 0x55


def _read_ser_string(buf: bytes, off: int) -> tuple[str | None, int]:
    """Read a SerString (ECMA-335 II.23.3): compressed-uint length + UTF-8 bytes.
    Length 0xFF means null."""
    b = buf[off]
    if b == 0xFF:
        return None, 1
    length, c = _read_compressed_uint(buf, off)
    off += c
    s = buf[off:off + length].decode("utf-8", errors="replace")
    return s, c + length


def _read_fixed_value(buf: bytes, off: int, type_byte: int) -> tuple[Any, int]:
    if type_byte == SER_TYPE_BOOL:
        return bool(buf[off]), 1
    if type_byte in (SER_TYPE_I1, SER_TYPE_U1):
        return buf[off], 1
    if type_byte in (SER_TYPE_I2, SER_TYPE_U2):
        return struct.unpack_from("<h" if type_byte == SER_TYPE_I2 else "<H", buf, off)[0], 2
    if type_byte in (SER_TYPE_I4, SER_TYPE_U4):
        return struct.unpack_from("<i" if type_byte == SER_TYPE_I4 else "<I", buf, off)[0], 4
    if type_byte in (SER_TYPE_I8, SER_TYPE_U8):
        return struct.unpack_from("<q" if type_byte == SER_TYPE_I8 else "<Q", buf, off)[0], 8
    if type_byte == SER_TYPE_R4:
        return struct.unpack_from("<f", buf, off)[0], 4
    if type_byte == SER_TYPE_R8:
        return struct.unpack_from("<d", buf, off)[0], 8
    if type_byte == SER_TYPE_STRING or type_byte == SER_TYPE_TYPE:
        s, c = _read_ser_string(buf, off)
        return s, c
    raise ValueError(f"unsupported serialization type 0x{type_byte:02x}")


def parse_protomember_blob(blob: bytes) -> dict[str, Any]:
    """Parse a ProtoMemberAttribute blob.

    The common ctor is `ProtoMember(int tag)` so the first fixed arg is the
    tag. Named args may include `Name`, `IsRequired`, `DataFormat`.
    """
    out: dict[str, Any] = {
        "tag": None,
        "name": None,
        "is_required": False,
        "data_format": 0,
    }
    if len(blob) < 6:
        raise ValueError("ProtoMember blob too short")
    if blob[0] != 0x01 or blob[1] != 0x00:
        raise ValueError(f"bad blob prolog: {blob[:2].hex()}")
    off = 2
    # Fixed arg: int32 tag
    out["tag"] = struct.unpack_from("<i", blob, off)[0]
    off += 4
    # Named args
    num_named = struct.unpack_from("<H", blob, off)[0]
    off += 2
    for _ in range(num_named):
        member_kind = blob[off]; off += 1
        if member_kind not in (SER_TYPE_FIELD, SER_TYPE_PROPERTY):
            raise ValueError(f"unexpected named-arg kind 0x{member_kind:02x}")
        type_byte = blob[off]; off += 1
        enum_type_name: str | None = None
        if type_byte == SER_TYPE_ENUM:
            enum_type_name, c = _read_ser_string(blob, off)
            off += c
        # field name
        name, c = _read_ser_string(blob, off)
        off += c
        # value
        if type_byte == SER_TYPE_ENUM:
            # Enum underlying type is int32 (we assume — protobuf-net's enums are)
            value = struct.unpack_from("<i", blob, off)[0]
            off += 4
        else:
            value, c = _read_fixed_value(blob, off, type_byte)
            off += c
        if name == "Name":
            out["name"] = value
        elif name == "IsRequired":
            out["is_required"] = bool(value)
        elif name == "DataFormat":
            out["data_format"] = int(value)
    return out


# --------------------------------------------------------------------------- #
# Schema extraction                                                           #
# --------------------------------------------------------------------------- #

@dataclass
class FieldInfo:
    tag: int
    name: str           # the protobuf "Name=" or, if absent, the C# property name
    property: str
    type_desc: str
    wire: int | None
    is_required: bool
    data_format: int


@dataclass
class MessageInfo:
    name: str
    namespace: str
    fields: dict[int, FieldInfo] = field(default_factory=dict)


def _build_property_to_typedef(pe: dnfile.dnPE) -> dict[int, int]:
    """Map Property row-index → owning TypeDef row-index."""
    mdt = pe.net.mdtables
    mapping: dict[int, int] = {}
    for pm in mdt.PropertyMap.rows:
        td_row = pm.Parent.row_index  # TypeDef row
        for prop_idx in pm.PropertyList:
            mapping[prop_idx.row_index] = td_row
    return mapping


def _extract_messages(pe: dnfile.dnPE) -> dict[str, MessageInfo]:
    mdt = pe.net.mdtables
    prop_to_td = _build_property_to_typedef(pe)
    messages: dict[str, MessageInfo] = {}

    def _msg_for(td_row: int) -> MessageInfo:
        td = mdt.TypeDef.rows[td_row - 1]
        nm = _s(td.TypeName) or "?"
        ns = _s(td.TypeNamespace)
        if nm not in messages:
            messages[nm] = MessageInfo(name=nm, namespace=ns)
        return messages[nm]

    for ca in mdt.CustomAttribute.rows:
        t = ca.Type.row
        if not hasattr(t, "Class"):
            continue
        cls = t.Class.row
        if _s(getattr(cls, "TypeName", "")) != "ProtoMemberAttribute":
            continue
        parent = ca.Parent.row
        if not isinstance(parent, dnfile.mdtable.PropertyRow):
            continue
        prop_name = _s(parent.Name)

        # Resolve property → typedef
        prop_idx = ca.Parent.row_index
        td_row = prop_to_td.get(prop_idx)
        if td_row is None:
            continue

        # Parse blob
        blob_obj = ca.Value
        blob = blob_obj.value if hasattr(blob_obj, "value") else bytes(blob_obj)
        if isinstance(blob, str):
            blob = bytes(blob, "latin1")
        if not isinstance(blob, (bytes, bytearray)):
            continue
        try:
            pm = parse_protomember_blob(bytes(blob))
        except ValueError:
            continue

        # Parse property signature for type
        sig_blob_obj = parent.Type
        sig_blob = sig_blob_obj.value if hasattr(sig_blob_obj, "value") else bytes(sig_blob_obj)
        try:
            sig = _parse_property_sig(bytes(sig_blob), pe)
        except Exception:
            sig = SigType(kind="unknown")

        # Determine wire type.
        # protobuf-net DataFormat enum: 0=Default, 1=ZigZag, 2=TwosComplement,
        # 3=FixedSize, 4=Group. Only FixedSize changes the wire type away from
        # the default for int32/64 fields.
        wire = sig.wire
        if pm["data_format"] == 3:
            if sig.kind in ("int32", "uint32"):
                wire = 5
            elif sig.kind in ("int64", "uint64"):
                wire = 1

        info = FieldInfo(
            tag=pm["tag"],
            name=pm["name"] or prop_name,
            property=prop_name,
            type_desc=sig.describe(),
            wire=wire,
            is_required=pm["is_required"],
            data_format=pm["data_format"],
        )

        msg = _msg_for(td_row)
        msg.fields[pm["tag"]] = info

    return messages


# --------------------------------------------------------------------------- #
# Enum extraction                                                             #
# --------------------------------------------------------------------------- #

def _extract_enums(pe: dnfile.dnPE) -> dict[str, dict[str, int]]:
    """Walk every TypeDef that extends System.Enum and pull out
    (member_name → integer_value) from its literal Fields' Constant rows."""
    mdt = pe.net.mdtables
    enums: dict[str, dict[str, int]] = {}

    # Pre-index Constant table by Parent (HasConstant coded index)
    # HasConstant coded index: tables Field=0, Param=1, Property=2 (tag bits low 2)
    const_by_field: dict[int, Any] = {}
    for cr in mdt.Constant.rows:
        parent = cr.Parent
        if parent is None:
            continue
        # Determine if parent is a Field row
        prow = parent.row
        if isinstance(prow, dnfile.mdtable.FieldRow):
            const_by_field[parent.row_index] = cr

    for td in mdt.TypeDef.rows:
        ext = td.Extends
        if not ext or not ext.row:
            continue
        ext_row = ext.row
        if _s(getattr(ext_row, "TypeName", "")) != "Enum" or _s(getattr(ext_row, "TypeNamespace", "")) != "System":
            continue
        members: dict[str, int] = {}
        # The TypeDef's FieldList is a list of MDTableIndex for Field rows
        for fidx in td.FieldList:
            field_row = fidx.row
            fname = _s(field_row.Name)
            # Skip the special "value__" instance field
            if fname == "value__":
                continue
            cr = const_by_field.get(fidx.row_index)
            if cr is None:
                continue
            # Constant Type is an ElementType byte; Value is the literal
            ct = cr.Type
            val = cr.Value
            raw = val.value if hasattr(val, "value") else bytes(val)
            try:
                if ct == SER_TYPE_I4:
                    n = struct.unpack("<i", raw[:4])[0]
                elif ct == SER_TYPE_U4:
                    n = struct.unpack("<I", raw[:4])[0]
                elif ct == SER_TYPE_I8:
                    n = struct.unpack("<q", raw[:8])[0]
                elif ct == SER_TYPE_U8:
                    n = struct.unpack("<Q", raw[:8])[0]
                elif ct == SER_TYPE_I2:
                    n = struct.unpack("<h", raw[:2])[0]
                elif ct == SER_TYPE_U2:
                    n = struct.unpack("<H", raw[:2])[0]
                elif ct in (SER_TYPE_I1, SER_TYPE_U1):
                    n = raw[0]
                else:
                    continue
            except struct.error:
                continue
            members[fname] = n
        if members:
            enums[_s(td.TypeName) or "?"] = members

    return enums


# --------------------------------------------------------------------------- #
# Public API                                                                  #
# --------------------------------------------------------------------------- #

def extract_schema(
    dto_path: Path,
    *,
    enums_dll: Path | None = None,
    game: str = "unknown",
    version: str = "",
) -> dict[str, Any]:
    dto_pe = dnfile.dnPE(str(dto_path))
    dto_pe.parse_data_directories()

    messages = _extract_messages(dto_pe)

    # Enums: prefer the larger Assembly-CSharp.dll if provided (more enums),
    # otherwise the DTO dll has the protobuf-relevant ones too.
    enums: dict[str, dict[str, int]] = {}
    enums.update(_extract_enums(dto_pe))
    if enums_dll is not None and enums_dll.exists():
        enums_pe = dnfile.dnPE(str(enums_dll))
        enums_pe.parse_data_directories()
        enums.update(_extract_enums(enums_pe))

    bundle: dict[str, Any] = {
        "game": game,
        "version": version,
        "extracted_at": _dt.date.today().isoformat(),
        "messages": {
            name: {
                "namespace": m.namespace,
                "fields": {
                    str(tag): {
                        "name": fi.name,
                        "property": fi.property,
                        "wire": fi.wire,
                        "type": fi.type_desc,
                        "is_required": fi.is_required,
                        "data_format": fi.data_format,
                    }
                    for tag, fi in sorted(m.fields.items())
                },
            }
            for name, m in sorted(messages.items())
        },
        "enums": {
            name: dict(sorted(members.items(), key=lambda kv: kv[1]))
            for name, members in sorted(enums.items())
        },
    }
    return bundle


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Extract protobuf-net schema bundle from .NET DLLs")
    p.add_argument("--dto", required=True, type=Path, help="Path to ShadowrunDTO.dll")
    p.add_argument("--enums", type=Path, default=None,
                   help="Path to Assembly-CSharp.dll (for enums)")
    p.add_argument("--game", required=True, help="Game identifier, e.g. dragonfall-dc")
    p.add_argument("--version", default="", help="Game version string")
    p.add_argument("--out", required=True, type=Path, help="Output JSON path")
    args = p.parse_args(argv)

    bundle = extract_schema(
        args.dto, enums_dll=args.enums, game=args.game, version=args.version,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(bundle, indent=2, ensure_ascii=False))

    n_msgs = len(bundle["messages"])
    n_fields = sum(len(m["fields"]) for m in bundle["messages"].values())
    n_enums = len(bundle["enums"])
    n_enum_members = sum(len(e) for e in bundle["enums"].values())
    print(
        f"Wrote {args.out}: {n_msgs} messages / {n_fields} fields / "
        f"{n_enums} enums / {n_enum_members} enum members"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
