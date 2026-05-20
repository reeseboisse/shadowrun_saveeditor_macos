import sys
sys.path.insert(0, "/home/user/shadowrun_saveeditor_macos/src")
from shadowrun_editor.protobuf_engine import parse_toplevel, WIRE_VARINT, WIRE_LEN
from pathlib import Path

for label, p in [
    ("A", "/root/.claude/uploads/31741338-87ae-4936-964b-128f0fac527c/0512c7ec-3e499433afa14e4fba830abbd28bedac.sav"),
    ("B", "/root/.claude/uploads/31741338-87ae-4936-964b-128f0fac527c/80617c04-966eea2bbc614014ac745265220aa1d8.sav"),
]:
    top = parse_toplevel(Path(p).read_bytes())
    print(f"\n=== {label} per-block nuyen + AliceFunds ===")
    for idx, f in enumerate(top):
        if f.tag != 7 or f.wire != WIRE_LEN or f.children is None:
            continue
        nuyen = None
        alice = None
        for c in f.children:
            if c.tag == 9 and c.wire == WIRE_VARINT:
                nuyen = int(c.value)
            if c.tag == 5 and c.wire == WIRE_LEN and c.children is not None:
                for pair in c.children:
                    if pair.tag == 3 and pair.children is not None:
                        name_f = next((x for x in pair.children if x.tag == 1 and x.wire == WIRE_LEN), None)
                        val_f = next((x for x in pair.children if x.tag == 2 and x.wire == WIRE_LEN), None)
                        if name_f is None or val_f is None: continue
                        if name_f.value == b"Global_AliceFunds" and val_f.children:
                            for vc in val_f.children:
                                if vc.tag == 1 and vc.wire == WIRE_VARINT:
                                    alice = int(vc.value); break
        print(f"  block#{idx:>2}  nuyen={str(nuyen):<6}  AliceFunds={alice}")
