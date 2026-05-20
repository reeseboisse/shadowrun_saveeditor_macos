"""Drill into the specific fields the deep diff flagged."""
import sys
sys.path.insert(0, "/home/user/shadowrun_saveeditor_macos/src")
from shadowrun_editor.protobuf_engine import parse_toplevel, WIRE_VARINT, WIRE_LEN
from pathlib import Path

paths = [
    ("A (after donation)", "/root/.claude/uploads/31741338-87ae-4936-964b-128f0fac527c/0512c7ec-3e499433afa14e4fba830abbd28bedac.sav"),
    ("B (before donation)", "/root/.claude/uploads/31741338-87ae-4936-964b-128f0fac527c/80617c04-966eea2bbc614014ac745265220aa1d8.sav"),
]

for label, p in paths:
    print(f"\n=== {label} ===")
    top = parse_toplevel(Path(p).read_bytes())
    # Last SaveStoryBlock
    last = None
    for f in top:
        if f.tag == 7 and f.wire == WIRE_LEN: last = f
    if last is None:
        continue

    # scene_mapping (tag 100, repeated SceneVersionMapping)
    for sm in (last.children or []):
        if sm.tag != 100: continue
        # children: tag 1 = filename, tag 2 = scene_version?
        for c in (sm.children or []):
            if c.tag == 1 and c.wire == WIRE_LEN:
                print(f"  scene_mapping.tag1: len={len(c.value)} hex={c.value[:80].hex()}")
                try:
                    print(f"    decoded={c.value.decode('utf-8', errors='replace')!r}")
                except Exception:
                    pass

    # player_measures (tag 340 inside CharacterInstance, repeated int)
    for party in (last.children or []):
        if party.tag not in (3, 11): continue  # newsave_party, oldsave_party
        if party.children is None: continue
        ci_name = "?"
        for c in party.children:
            if c.tag == 8 and c.wire == WIRE_LEN:
                ci_name = c.value.decode("utf-8", "replace")
        pms = []
        for c in (party.children or []):
            if c.tag == 340 and c.wire == WIRE_VARINT:
                pms.append(int(c.value))
        print(f"  party (tag={party.tag}) name={ci_name} player_measures={pms}")

    # life_measures (tag 25 on SaveStoryBlock, repeated int)
    lms = [int(c.value) for c in (last.children or []) if c.tag == 25 and c.wire == WIRE_VARINT]
    print(f"  life_measures={lms}")
