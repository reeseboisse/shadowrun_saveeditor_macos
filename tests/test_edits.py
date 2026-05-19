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
