"""Recursive field-level diff of two protobuf saves.

For each pair of messages, walks corresponding sub-fields and reports
any field whose value differs. Identifies child sub-messages by index
(repeated fields) or by tag (singular). Skips top-level fields whose
tag/wire don't appear in both trees (purely additive).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, "/home/user/shadowrun_saveeditor_macos/src")

from shadowrun_editor.protobuf_engine import (
    Field, WIRE_VARINT, WIRE_LEN, parse_toplevel,
)
from shadowrun_editor.schema import load_for_game


SCHEMA = load_for_game("dragonfall")


def field_name(parent_message_name: str | None, tag: int) -> str:
    if parent_message_name is None:
        return f"tag{tag}"
    msg = SCHEMA.message(parent_message_name)
    if msg is None:
        return f"tag{tag}"
    f = msg.field(tag)
    if f is None:
        return f"tag{tag}"
    return f.name


def guess_message_type(fields: list[Field]) -> str | None:
    """Find the schema message whose field set most closely matches."""
    tags_present = {f.tag for f in fields}
    if not tags_present:
        return None
    best_name = None
    best_score = -1
    for name, msg in SCHEMA.messages.items():
        schema_tags = set(msg.fields_by_tag.keys())
        # Score: how many present tags are KNOWN by this schema
        score = len(tags_present & schema_tags) - len(tags_present - schema_tags) * 2
        if len(tags_present) >= 3 and score > best_score:
            best_score = score
            best_name = name
    return best_name


def repr_value(f: Field, max_len: int = 60) -> str:
    if f.wire == WIRE_VARINT:
        return str(int(f.value))
    if f.wire == WIRE_LEN:
        try:
            s = f.value.decode("utf-8")
            if s.isprintable() and len(s) < max_len:
                return repr(s)
            return f"<bytes len={len(f.value)}>"
        except Exception:
            return f"<bytes len={len(f.value)}>"
    if f.wire == 5:
        import struct
        return f"f32:{struct.unpack('<f', f.value)[0]}"
    if f.wire == 1:
        import struct
        return f"f64:{struct.unpack('<d', f.value)[0]}"
    return f"<wire{f.wire}>"


def diff_messages(
    a: list[Field], b: list[Field], path: str = "", msg_type: str | None = None,
    out: list[str] | None = None,
) -> list[str]:
    if out is None:
        out = []
    # Group by tag for matching. For repeated fields, pair by index.
    from collections import defaultdict
    by_tag_a: dict[int, list[Field]] = defaultdict(list)
    by_tag_b: dict[int, list[Field]] = defaultdict(list)
    for f in a:
        by_tag_a[f.tag].append(f)
    for f in b:
        by_tag_b[f.tag].append(f)
    all_tags = sorted(set(by_tag_a) | set(by_tag_b))
    for tag in all_tags:
        la, lb = by_tag_a[tag], by_tag_b[tag]
        fname = field_name(msg_type, tag)
        # Determine child sub-message type for descent
        # (only relevant for wire=2 with parseable children)
        if len(la) != len(lb):
            out.append(f"{path}.{fname}: count A={len(la)} B={len(lb)}")
            continue
        for idx, (fa, fb) in enumerate(zip(la, lb)):
            sub_path = f"{path}.{fname}" + (f"[{idx}]" if len(la) > 1 else "")
            if fa.wire != fb.wire:
                out.append(f"{sub_path}: wire A={fa.wire} B={fb.wire}")
                continue
            if fa.wire == WIRE_LEN and fa.children is not None and fb.children is not None:
                # Recurse into sub-messages
                # Look up the field type from the parent's schema
                sub_msg_type: str | None = None
                if msg_type is not None:
                    msg = SCHEMA.message(msg_type)
                    if msg is not None:
                        sf = msg.field(tag)
                        if sf is not None and sf.type.startswith(("message:", "repeated:message:")):
                            sub_msg_type = sf.type.split(":")[-1]
                if sub_msg_type is None:
                    sub_msg_type = guess_message_type(fa.children)
                diff_messages(fa.children, fb.children, sub_path, sub_msg_type, out)
            elif fa.wire == WIRE_LEN:
                if fa.value != fb.value:
                    out.append(f"{sub_path}: bytes A={repr_value(fa)} B={repr_value(fb)}")
            else:
                if fa.value != fb.value:
                    out.append(f"{sub_path}: A={repr_value(fa)} B={repr_value(fb)}")
    return out


def main():
    A = parse_toplevel(Path(
        "/root/.claude/uploads/31741338-87ae-4936-964b-128f0fac527c/0512c7ec-3e499433afa14e4fba830abbd28bedac.sav"
    ).read_bytes())
    B = parse_toplevel(Path(
        "/root/.claude/uploads/31741338-87ae-4936-964b-128f0fac527c/80617c04-966eea2bbc614014ac745265220aa1d8.sav"
    ).read_bytes())

    diffs = diff_messages(A, B, path="SaveGame", msg_type="SaveGame")
    print(f"Total field diffs: {len(diffs)}\n")
    # Filter out known noise: timestamps and save-clock state
    noise_substrings = ["time_utc", "save_image"]
    interesting = [d for d in diffs if not any(n in d for n in noise_substrings)]
    print(f"After filtering timestamps/images: {len(interesting)}\n")
    for d in interesting:
        print(d)


if __name__ == "__main__":
    main()
