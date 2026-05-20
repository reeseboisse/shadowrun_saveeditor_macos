"""Take save B (before), apply +5000 to AliceFunds and -5000 to nuyen on the
latest SaveStoryBlock, re-serialize, and compare to save A's bytes around the
relevant region. If the round-trip matches A (modulo unrelated noise like the
save-time timestamp), the simple two-field paired edit IS the complete fix."""
import sys
sys.path.insert(0, "/home/user/shadowrun_saveeditor_macos/src")
from shadowrun_editor.protobuf_engine import (
    parse_toplevel, serialize_message, WIRE_VARINT, WIRE_LEN, Field
)
from pathlib import Path

A_PATH = "/root/.claude/uploads/31741338-87ae-4936-964b-128f0fac527c/0512c7ec-3e499433afa14e4fba830abbd28bedac.sav"
B_PATH = "/root/.claude/uploads/31741338-87ae-4936-964b-128f0fac527c/80617c04-966eea2bbc614014ac745265220aa1d8.sav"

A_bytes = Path(A_PATH).read_bytes()
B_bytes = Path(B_PATH).read_bytes()
print(f"A size: {len(A_bytes)}  B size: {len(B_bytes)}")

A_top = parse_toplevel(A_bytes)
B_top = parse_toplevel(B_bytes)

# Find the LAST SaveStoryBlock in B
last = None
for f in B_top:
    if f.tag == 7 and f.wire == WIRE_LEN:
        last = f
assert last is not None

# Edit nuyen (tag 9) - 5000
for c in last.children:
    if c.tag == 9 and c.wire == WIRE_VARINT:
        old = int(c.value)
        c.set_int(old - 5000)
        print(f"nuyen {old} -> {old-5000}")
        break

# Edit Global_AliceFunds in latest block's variable_data + 5000
for sec in last.children:
    if sec.tag != 5 or sec.children is None: continue
    for pair in sec.children:
        if pair.tag != 3 or pair.children is None: continue
        name_f = next((x for x in pair.children if x.tag == 1 and x.wire == WIRE_LEN), None)
        val_f = next((x for x in pair.children if x.tag == 2 and x.wire == WIRE_LEN), None)
        if name_f is None or val_f is None: continue
        if name_f.value == b"Global_AliceFunds" and val_f.children:
            for vc in val_f.children:
                if vc.tag == 1 and vc.wire == WIRE_VARINT:
                    old = int(vc.value)
                    vc.set_int(old + 5000)
                    print(f"AliceFunds {old} -> {old+5000}")
                    val_f.mark_dirty()
                    pair.mark_dirty()
                    sec.mark_dirty()
                    last.mark_dirty()
                    break
last.mark_dirty()

simulated = serialize_message(B_top)
print(f"simulated size: {len(simulated)}")
print(f"A size:         {len(A_bytes)}")
# Diff bytes
common = min(len(simulated), len(A_bytes))
diff = sum(1 for i in range(common) if simulated[i] != A_bytes[i])
print(f"simulated vs A: {diff:,}/{common:,} bytes differ ({100*diff/common:.2f}%)")
# What's the size delta?
print(f"size delta: simulated - A = {len(simulated) - len(A_bytes)}")
