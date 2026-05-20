"""Edit-then-re-parse integrity tests.

After an edit:
  1) The output must re-parse cleanly.
  2) The output must round-trip (parse → serialize == bytes).
  3) Re-parsing must reflect the edit.

Exercises both same-width (tag flip, etiquette) and width-growing (karma
1 byte → karma 3 bytes) edits, so length-prefix recomputation is covered.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from shadowrun_editor.domain import dragonfall as df
from shadowrun_editor.protobuf_engine import parse_toplevel, serialize_message


REPO_ROOT = Path(__file__).resolve().parents[1]
DF_SAV = REPO_ROOT / "reference" / "saves" / "dragonfall" / "8b5bf6014c7a48b1a4a10b6b12147410.sav"


def _read_then_edit_then_roundtrip(data: bytes, edit_fn):
    top = parse_toplevel(data)
    report = edit_fn(top)
    out = serialize_message(top)
    # Re-parse must succeed
    top2 = parse_toplevel(out)
    # Re-serializing the re-parsed tree must equal `out` (idempotent)
    out2 = serialize_message(top2)
    assert out2 == out, "second round-trip diverged after edit"
    return report, top2, out


def test_edit_etiquette_same_width():
    data = DF_SAV.read_bytes()
    report, top2, out = _read_then_edit_then_roundtrip(
        data, lambda top: df.set_etiquette(top, "academic")
    )
    assert report.changes, "expected at least one snapshot changed"
    # Verify the edit took: every snapshot with skills should now have tag 31
    snaps = df.find_player_snapshots(top2)
    found_academic = 0
    for s in snaps:
        if s.skills is None or s.skills.children is None:
            continue
        present = [c.tag for c in s.skills.children if c.tag in df.ETIQUETTE_TAGS]
        if 31 in present and 21 not in present:
            found_academic += 1
    assert found_academic > 0, "expected academic etiquette set on at least one snapshot"


def test_edit_unspent_karma_growing_width():
    data = DF_SAV.read_bytes()
    # 2 -> 100000 grows the varint from 1 byte to 3 bytes; every ancestor
    # length prefix must be recomputed.
    report, top2, _ = _read_then_edit_then_roundtrip(
        data, lambda top: df.set_unspent_karma(top, 100_000)
    )
    assert report.changes
    for s in df.find_player_snapshots(top2):
        if not s.has_meaningful_data():
            continue
        assert s.unspent_karma == 100_000


def test_edit_nuyen():
    data = DF_SAV.read_bytes()
    report, top2, _ = _read_then_edit_then_roundtrip(
        data, lambda top: df.set_nuyen(top, 999_999)
    )
    assert report.changes
    assert df.read_nuyen(top2) == 999_999


def test_edit_attribute_negative_value():
    """Negative int32s are encoded as 10-byte varints via sign extension.
    Confirm a 'body=-1' edit re-encodes and re-parses correctly."""
    data = DF_SAV.read_bytes()
    report, top2, _ = _read_then_edit_then_roundtrip(
        data, lambda top: df.set_attribute(top, "body", -1)
    )
    assert report.changes
    sheet = df.CharacterSheet.from_top(top2)
    assert sheet is not None
    assert sheet.attributes.get("body") == -1


def test_no_op_edit_produces_no_byte_change():
    """Re-applying the existing etiquette must be a true no-op."""
    data = DF_SAV.read_bytes()
    top = parse_toplevel(data)
    # The save's current etiquette is security; setting it again should be a no-op
    report = df.set_etiquette(top, "security")
    out = serialize_message(top)
    assert out == data, "no-op etiquette set should produce byte-identical output"
    assert not report.changes


def test_add_etiquette_inserts_new_tag():
    """add_etiquette enables an etiquette without disturbing existing ones."""
    data = DF_SAV.read_bytes()
    report, top2, _ = _read_then_edit_then_roundtrip(
        data, lambda top: df.add_etiquette(top, "academic")
    )
    assert report.changes
    snaps_with_data = [
        s for s in df.find_player_snapshots(top2) if s.has_meaningful_data()
    ]
    assert snaps_with_data, "expected at least one snapshot with character data"
    for s in snaps_with_data:
        assert s.skills is not None and s.skills.children is not None
        tags = {f.tag for f in s.skills.children if f.tag in df.ETIQUETTE_TAGS}
        # academic == 31; should be present after add. The original security (21)
        # rating should also still be present — we're additive, not destructive.
        assert df.ETIQUETTES["academic"] in tags
        assert df.ETIQUETTES["security"] in tags


def test_remove_etiquette_drops_field():
    data = DF_SAV.read_bytes()
    report, top2, _ = _read_then_edit_then_roundtrip(
        data, lambda top: df.remove_etiquette(top, "security")
    )
    assert report.changes
    snaps_with_data = [
        s for s in df.find_player_snapshots(top2) if s.has_meaningful_data()
    ]
    for s in snaps_with_data:
        if s.skills is None or s.skills.children is None:
            continue
        tags = {f.tag for f in s.skills.children}
        assert df.ETIQUETTES["security"] not in tags, "security should be removed"


def test_add_etiquette_is_idempotent():
    data = DF_SAV.read_bytes()
    report1, top1, out1 = _read_then_edit_then_roundtrip(
        data, lambda top: df.add_etiquette(top, "academic")
    )
    # Apply again to the now-academic save; should produce no changes.
    report2 = df.add_etiquette(top1, "academic")
    assert not report2.changes, "second add_etiquette should be a no-op"


def test_donate_to_alice_fund_pairs_nuyen_and_flag():
    """+amount to Global_AliceFunds AND -amount from nuyen, both on the
    latest SaveStoryBlock only. Matches the in-game donation transaction
    exactly (verified against a user-provided before/after save pair)."""
    data = DF_SAV.read_bytes()
    top = parse_toplevel(data)
    fund_before = df.read_alice_fund(top)
    nuyen_before = df.read_nuyen(top)
    assert fund_before is not None and nuyen_before is not None

    df.donate_to_alice_fund(top, 1000)
    out = serialize_message(top)
    top2 = parse_toplevel(out)

    fund_after = df.read_alice_fund(top2)
    nuyen_after = df.read_nuyen(top2)
    assert fund_after == fund_before + 1000
    assert nuyen_after == nuyen_before - 1000


def test_donate_to_alice_fund_does_not_touch_earlier_blocks():
    """Earlier SaveStoryBlocks (autosave history) must keep their original
    AliceFunds value when only the latest block is donated to."""
    data = DF_SAV.read_bytes()
    top = parse_toplevel(data)

    # Snapshot Global_AliceFunds across every block before
    def fund_per_block(tree):
        from shadowrun_editor.protobuf_engine import WIRE_LEN, WIRE_VARINT
        out = []
        for f in tree:
            if f.tag != 7 or f.wire != WIRE_LEN or f.children is None:
                continue
            value = None
            for sec in f.children:
                if sec.tag != 5 or sec.children is None:
                    continue
                for pair in sec.children:
                    if pair.tag != 3 or pair.children is None:
                        continue
                    name_f = next((x for x in pair.children if x.tag == 1 and x.wire == WIRE_LEN), None)
                    val_f = next((x for x in pair.children if x.tag == 2 and x.wire == WIRE_LEN), None)
                    if name_f is None or val_f is None or val_f.children is None:
                        continue
                    if name_f.value == b"Global_AliceFunds":
                        for vc in val_f.children:
                            if vc.tag == 1 and vc.wire == WIRE_VARINT:
                                value = int(vc.value)
            out.append(value)
        return out

    before = fund_per_block(top)
    df.donate_to_alice_fund(top, 500)
    out = serialize_message(top)
    after = fund_per_block(parse_toplevel(out))

    assert before[:-1] == after[:-1], "earlier blocks were modified"
    assert after[-1] == (before[-1] or 0) + 500, "latest block didn't change"
