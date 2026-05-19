"""
Protobuf read-modify-write engine for Shadowrun save files.

Schema-less: parses any protobuf byte stream into a tree of Field nodes
without requiring a .proto definition. Each Field preserves the original
byte slice it came from so an unmodified parse-then-serialize round-trips
byte-for-byte.

Wire types (standard protobuf):
    0 = varint            (int32/64, uint32/64, sint, bool, enum)
    1 = 64-bit fixed      (double, fixed64, sfixed64)
    2 = length-delimited  (string, bytes, embedded message, packed array)
    5 = 32-bit fixed      (float, fixed32, sfixed32)

Edits update an in-memory tree; serialize() emits a fresh byte string,
recomputing every length-delimited length-prefix automatically.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from typing import Iterable, Iterator


WIRE_VARINT = 0
WIRE_FIXED64 = 1
WIRE_LEN = 2
WIRE_FIXED32 = 5


# --------------------------------------------------------------------------- #
# Varint helpers                                                              #
# --------------------------------------------------------------------------- #

def read_varint(buf: bytes, off: int) -> tuple[int, int]:
    """Read one varint. Returns (value, bytes_consumed). Raises on truncation."""
    result = 0
    shift = 0
    start = off
    n = len(buf)
    while True:
        if off >= n:
            raise ValueError(f"truncated varint at offset {start}")
        b = buf[off]
        off += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, off - start
        shift += 7
        if shift >= 64:
            raise ValueError("varint too long")


def encode_varint(n: int) -> bytes:
    if n < 0:
        # protobuf int32/64 negative values are encoded as 10-byte varints
        # by widening to 64 bits two's complement.
        n &= (1 << 64) - 1
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def encode_tag(tag: int, wire: int) -> bytes:
    return encode_varint((tag << 3) | wire)


# --------------------------------------------------------------------------- #
# Field tree                                                                  #
# --------------------------------------------------------------------------- #

@dataclass
class Field:
    """One protobuf field.

    For wire 0 (varint): value is an int.
    For wire 1/5 (fixed): value is bytes (8 / 4).
    For wire 2 (length-delimited): value is bytes; children may be a list of
        Field if the payload also parsed as a valid sub-message.

    raw_tag preserves the exact tag-varint bytes from the source so a
    pristine parse-then-serialize round-trips byte-for-byte (some encoders
    emit "redundant-zero" varints; protobuf-net does not, but we preserve
    just in case).

    raw_len_prefix preserves the exact length-prefix bytes for wire 2.

    Once dirty=True (any structural change), serialization is rebuilt from
    the canonical encoding and the raw_* fields are discarded.
    """
    tag: int
    wire: int
    value: object  # int | bytes | list[Field] handled by `children`
    children: list["Field"] | None = None  # only for wire 2 sub-messages
    raw_tag: bytes | None = None
    raw_len_prefix: bytes | None = None
    dirty: bool = False

    # ----- convenience accessors ----- #

    @property
    def is_message(self) -> bool:
        return self.wire == WIRE_LEN and self.children is not None

    @property
    def is_bytes(self) -> bool:
        return self.wire == WIRE_LEN

    def as_int(self) -> int:
        if self.wire != WIRE_VARINT:
            raise TypeError(f"field tag {self.tag} wire {self.wire} is not varint")
        return self.value  # type: ignore[return-value]

    def as_bytes(self) -> bytes:
        if self.wire != WIRE_LEN:
            raise TypeError(f"field tag {self.tag} wire {self.wire} is not length-delimited")
        return self.value  # type: ignore[return-value]

    def as_str(self, encoding: str = "utf-8") -> str:
        return self.as_bytes().decode(encoding)

    # ----- mutation ----- #

    def set_int(self, n: int) -> None:
        if self.wire != WIRE_VARINT:
            raise TypeError(f"field tag {self.tag} is not varint")
        self.value = n
        self.dirty = True
        # invalidate cached raw varint
        self.raw_tag = None  # tag stays the same width usually but recompute is safe

    def set_bytes(self, b: bytes) -> None:
        if self.wire != WIRE_LEN:
            raise TypeError(f"field tag {self.tag} is not length-delimited")
        self.value = b
        self.children = None  # if it was a sub-message, dropping that view
        self.dirty = True
        self.raw_tag = None
        self.raw_len_prefix = None

    def set_str(self, s: str) -> None:
        self.set_bytes(s.encode("utf-8"))

    def set_tag(self, new_tag: int) -> None:
        """Change the field number. Same wire type."""
        self.tag = new_tag
        self.dirty = True
        self.raw_tag = None

    def mark_dirty(self) -> None:
        """Mark this field and discard cached raw encodings.
        Call on any mutation of children/value."""
        self.dirty = True
        self.raw_tag = None
        self.raw_len_prefix = None


# --------------------------------------------------------------------------- #
# Parsing                                                                     #
# --------------------------------------------------------------------------- #

def parse_message(data: bytes, start: int = 0, end: int | None = None) -> list[Field] | None:
    """Parse [start, end) as a protobuf message. Returns list[Field] or None
    if the bytes don't cleanly tile a sequence of valid fields ending at `end`.
    """
    if end is None:
        end = len(data)
    fields: list[Field] = []
    off = start
    while off < end:
        tag_off = off
        try:
            tag_and_wire, c = read_varint(data, off)
        except ValueError:
            return None
        next_off = off + c
        wire = tag_and_wire & 7
        tag = tag_and_wire >> 3
        if tag == 0:
            return None
        raw_tag = data[tag_off:next_off]
        off = next_off

        if wire == WIRE_VARINT:
            try:
                val, c = read_varint(data, off)
            except ValueError:
                return None
            fields.append(Field(tag, wire, val, raw_tag=raw_tag))
            off += c

        elif wire == WIRE_FIXED64:
            if off + 8 > end:
                return None
            fields.append(Field(tag, wire, data[off:off + 8], raw_tag=raw_tag))
            off += 8

        elif wire == WIRE_FIXED32:
            if off + 4 > end:
                return None
            fields.append(Field(tag, wire, data[off:off + 4], raw_tag=raw_tag))
            off += 4

        elif wire == WIRE_LEN:
            len_off = off
            try:
                length, c = read_varint(data, off)
            except ValueError:
                return None
            off += c
            raw_len_prefix = data[len_off:off]
            if off + length > end:
                return None
            val_start = off
            val_end = off + length
            payload = data[val_start:val_end]
            children = parse_message(data, val_start, val_end)
            fields.append(Field(
                tag, wire, payload,
                children=children,
                raw_tag=raw_tag,
                raw_len_prefix=raw_len_prefix,
            ))
            off = val_end

        else:
            # wire 3 (start group) / 4 (end group) are deprecated and not used
            # by protobuf-net for newer schemas; treat as parse failure.
            return None

    if off != end:
        return None
    return fields


def parse_toplevel(data: bytes) -> list[Field]:
    """Parse a complete buffer; raise on failure (top-level must succeed)."""
    fields = parse_message(data, 0, len(data))
    if fields is None:
        raise ValueError("failed to parse buffer as protobuf message")
    return fields


# --------------------------------------------------------------------------- #
# Serialization                                                               #
# --------------------------------------------------------------------------- #

def _serialize_field_value(f: Field, out: bytearray) -> None:
    """Append just the value portion (excluding tag) to `out`."""
    if f.wire == WIRE_VARINT:
        out += encode_varint(int(f.value))  # type: ignore[arg-type]

    elif f.wire == WIRE_FIXED64 or f.wire == WIRE_FIXED32:
        b = f.value  # type: ignore[assignment]
        assert isinstance(b, (bytes, bytearray))
        expected = 8 if f.wire == WIRE_FIXED64 else 4
        if len(b) != expected:
            raise ValueError(f"fixed field tag {f.tag} expected {expected} bytes, got {len(b)}")
        out += b

    elif f.wire == WIRE_LEN:
        # If we have a sub-message view, emit from it (after recursive serialize),
        # otherwise emit the raw bytes payload.
        if f.children is not None:
            child_bytes = serialize_message(f.children)
            payload = child_bytes
        else:
            payload = f.value  # type: ignore[assignment]
            assert isinstance(payload, (bytes, bytearray))
        # length prefix: prefer original raw if clean and length still matches
        if not f.dirty and f.raw_len_prefix is not None and len(payload) == _decode_len_prefix(f.raw_len_prefix):
            out += f.raw_len_prefix
        else:
            out += encode_varint(len(payload))
        out += payload

    else:
        raise ValueError(f"unsupported wire type {f.wire}")


def _decode_len_prefix(prefix: bytes) -> int:
    v, _ = read_varint(prefix, 0)
    return v


def serialize_field(f: Field) -> bytes:
    """Serialize a single field including its tag."""
    # If we have an unmodified message field whose children are also unmodified,
    # we could shortcut to raw bytes — but recomputing is safe and round-trip-
    # tested. For non-message fields we trust the canonical encoding.
    if not _is_dirty_deep(f) and f.raw_tag is not None:
        # Fast path: just rebuild from cached raw_tag + canonical value.
        # We still re-emit the value canonically; the round-trip test exercises
        # that this matches the source bytes for every Field we've seen.
        out = bytearray()
        out += f.raw_tag
        _serialize_field_value(f, out)
        return bytes(out)

    out = bytearray()
    out += encode_tag(f.tag, f.wire)
    _serialize_field_value(f, out)
    return bytes(out)


def _is_dirty_deep(f: Field) -> bool:
    if f.dirty:
        return True
    if f.children is not None:
        for c in f.children:
            if _is_dirty_deep(c):
                return True
    return False


def serialize_message(fields: Iterable[Field]) -> bytes:
    """Serialize a list of fields back to a byte string (no length prefix)."""
    out = bytearray()
    for f in fields:
        out += serialize_field(f)
    return bytes(out)


# --------------------------------------------------------------------------- #
# Tree walking                                                                #
# --------------------------------------------------------------------------- #

def walk(fields: Iterable[Field]) -> Iterator[Field]:
    """Pre-order traversal of every Field in a tree."""
    for f in fields:
        yield f
        if f.children is not None:
            yield from walk(f.children)


def find_first(fields: Iterable[Field], tag: int, wire: int | None = None) -> Field | None:
    for f in fields:
        if f.tag == tag and (wire is None or f.wire == wire):
            return f
    return None


def find_all(fields: Iterable[Field], tag: int, wire: int | None = None) -> list[Field]:
    return [f for f in fields if f.tag == tag and (wire is None or f.wire == wire)]
