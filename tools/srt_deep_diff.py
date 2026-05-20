"""Recursive deep diff of the two .srt scene-state files."""
import sys
sys.path.insert(0, "/home/user/shadowrun_saveeditor_macos/src")
from shadowrun_editor.protobuf_engine import parse_toplevel, WIRE_VARINT, WIRE_LEN, walk
from shadowrun_editor.schema import load_for_game
from pathlib import Path
import re

SRT_A = "/root/.claude/uploads/31741338-87ae-4936-964b-128f0fac527c/3a473f3e-3e499433afa14e4fba830abbd28bedacHavenc5428ff97fc84ca98398c1527f125fed.srt"
SRT_B = "/root/.claude/uploads/31741338-87ae-4936-964b-128f0fac527c/c0b52991-966eea2bbc614014ac745265220aa1d8Haven2e80a73be621450a8b47ef2027afbb6a.srt"

A = parse_toplevel(Path(SRT_A).read_bytes())
B = parse_toplevel(Path(SRT_B).read_bytes())

print(f"SRT A: {len(A)} top-level fields")
print(f"SRT B: {len(B)} top-level fields")

# Collect all length-delimited string-like values from each save
def all_strings(top):
    strs = []
    for f in walk(top):
        if f.wire == WIRE_LEN and isinstance(f.value, (bytes, bytearray)):
            try:
                s = f.value.decode("utf-8")
                if s.isprintable():
                    strs.append(s)
            except Exception:
                pass
    return strs

a_strs = set(all_strings(A))
b_strs = set(all_strings(B))

# Strings that exist in B but not A (removed during donation)
removed = b_strs - a_strs
# Strings that exist in A but not B (added during donation)
added = a_strs - b_strs

print(f"\n{len(added)} strings appear in A but not B (added):")
for s in sorted(added):
    if len(s) < 80:
        print(f"  + {s!r}")

print(f"\n{len(removed)} strings appear in B but not A (removed):")
for s in sorted(removed):
    if len(s) < 80:
        print(f"  - {s!r}")

# Also: search for any numeric values close to 21250 or 26250 in B/A
def find_int_near(top, targets):
    hits = []
    for f in walk(top):
        if f.wire == WIRE_VARINT:
            v = int(f.value)
            for t in targets:
                if abs(v - t) <= 5:
                    hits.append((v, f.tag))
                    break
    return hits

print(f"\nValues near 21250 in B: {find_int_near(B, [21250])[:20]}")
print(f"Values near 26250 in A: {find_int_near(A, [26250])[:20]}")
print(f"Values near 5000 in either: A={find_int_near(A, [5000])[:10]} B={find_int_near(B, [5000])[:10]}")
print(f"Values 581 / 5581: A near 581={[(v,t) for v,t in find_int_near(A, [581]) if v==581][:5]}")
print(f"  B near 5581={[(v,t) for v,t in find_int_near(B, [5581]) if v==5581][:5]}")
