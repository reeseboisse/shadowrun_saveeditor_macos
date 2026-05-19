"""
Shadowrun: Dragonfall - Director's Cut  --  Etiquette Patcher
=============================================================

Walks the protobuf structure of a .sav or .srt file, locates the player
character's skills sub-message, and changes which etiquette tag is set.

Schema (from ShadowrunDTO.dll, class containing skill ratings):
   tag 20  etiquette_corporate
   tag 21  etiquette_security
   tag 22  etiquette_gang
   tag 23  etiquette_paranormal       (Hong Kong only)
   tag 24  etiquette_socialite
   tag 25  etiquette_infected         (Hong Kong only)
   tag 29  etiquette_shadowrunner
   tag 30  etiquette_street
   tag 31  etiquette_academic

The skills sub-message sits inside the player character's stats group at
   <player_container>.#4 (stats group).#2 (skills+etiquettes group)
The player container is identified by having a sub-field #4 (stats group)
that contains a string field #4 == "Player".

Every etiquette tag encodes as a 2-byte varint of equal length, so this is
a single-byte edit per file (the first byte of the tag varint), no length
recomputation, no file resize.
"""

import io, os, sys, struct, shutil, glob, argparse

ETIQUETTE_TAGS = {
    'corporate':     20,
    'security':      21,
    'gang':          22,
    'paranormal':    23,
    'socialite':     24,
    'infected':      25,
    'shadowrunner':  29,
    'street':        30,
    'academic':      31,
}
ETIQUETTE_TAG_SET = set(ETIQUETTE_TAGS.values())

# ----- varint helpers ---------------------------------------------------------

def read_varint(buf: bytes, off: int):
    """Read a varint starting at off. Returns (value, bytes_consumed)."""
    result = 0
    shift = 0
    start = off
    while True:
        if off >= len(buf):
            raise ValueError(f"truncated varint at offset {start}")
        b = buf[off]; off += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, off - start
        shift += 7
        if shift >= 64:
            raise ValueError("varint too long")

def varint_encode(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)

# ----- protobuf field walker --------------------------------------------------

class Field:
    __slots__ = ('tag','wire','tag_off','val_off','end_off','payload','children','data')
    def __init__(self, tag, wire, tag_off, val_off, end_off, payload, data):
        self.tag = tag
        self.wire = wire
        self.tag_off = tag_off    # absolute byte offset of the tag varint
        self.val_off = val_off    # absolute byte offset of the value
        self.end_off = end_off    # absolute byte offset just past this field
        self.payload = payload    # for wire 2: the raw bytes; for wire 0/1/5: the decoded int
        self.children = None      # if wire-2 and parseable as a message, list of Field
        self.data = data          # reference to the full buffer

def parse_message(data: bytes, start: int, end: int):
    """Parse [start, end) as a sequence of protobuf fields. Returns list[Field]."""
    fields = []
    off = start
    while off < end:
        tag_off = off
        try:
            tag_and_wire, consumed = read_varint(data, off)
        except ValueError:
            return None
        off += consumed
        wire = tag_and_wire & 7
        tag = tag_and_wire >> 3
        if tag == 0:
            return None
        if wire == 0:                                # varint
            try:
                val, c = read_varint(data, off)
            except ValueError:
                return None
            f = Field(tag, wire, tag_off, off, off + c, val, data)
            off += c
        elif wire == 1:                              # 64-bit
            if off + 8 > end: return None
            f = Field(tag, wire, tag_off, off, off + 8, data[off:off+8], data)
            off += 8
        elif wire == 5:                              # 32-bit
            if off + 4 > end: return None
            f = Field(tag, wire, tag_off, off, off + 4, data[off:off+4], data)
            off += 4
        elif wire == 2:                              # length-delimited
            try:
                length, c = read_varint(data, off)
            except ValueError:
                return None
            off += c
            if off + length > end: return None
            val_start = off
            val_end = off + length
            f = Field(tag, wire, tag_off, val_start, val_end, data[val_start:val_end], data)
            # try parsing as a sub-message (could fail if it's actually a string)
            f.children = parse_message(data, val_start, val_end)
            off = val_end
        else:
            return None
        fields.append(f)
    if off != end:
        return None
    return fields

def parse_toplevel(data: bytes):
    return parse_message(data, 0, len(data))

# ----- player + etiquette location --------------------------------------------

def is_player_container(msg_fields):
    """A 'player container' is a message that has a sub-message at tag #4 which
    in turn contains a string field at tag #4 with value 'Player'."""
    if not msg_fields: return False
    for f in msg_fields:
        if f.tag == 4 and f.wire == 2 and f.children is not None:
            for sub in f.children:
                if sub.tag == 4 and sub.wire == 2 and sub.payload == b'Player':
                    return True
    return False

def walk(fields, visit):
    for f in fields:
        visit(f)
        if f.children:
            walk(f.children, visit)

def find_players(top_fields):
    """Find every player-container message anywhere in the file."""
    hits = []
    def visit(f):
        if f.wire == 2 and f.children and is_player_container(f.children):
            hits.append(f)
    walk(top_fields, visit)
    return hits

def find_skills_msg(player_container):
    """Return the player's skills+etiquettes sub-message (player.#4.#2)."""
    stats = next((f for f in player_container.children if f.tag == 4 and f.wire == 2), None)
    if not stats or not stats.children: return None
    skills = next((f for f in stats.children if f.tag == 2 and f.wire == 2), None)
    if not skills or not skills.children: return None
    return skills

def current_etiquette_fields(skills_msg):
    """Return [Field, ...] for any etiquette tags present in the skills sub-message."""
    return [f for f in skills_msg.children if f.tag in ETIQUETTE_TAG_SET]

# ----- the patch itself -------------------------------------------------------

def patch_etiquette(data: bytes, new_tag: int):
    """Return (new_data, summary). summary describes what changed.

    new_tag: integer ProtoMember tag of the target etiquette.
    """
    top = parse_toplevel(data)
    if top is None:
        raise RuntimeError("Failed to parse file as protobuf at top level")

    players = find_players(top)
    if not players:
        raise RuntimeError("No player character found in this file")

    out = bytearray(data)
    changes = []

    for pc in players:
        skills = find_skills_msg(pc)
        if not skills:
            continue
        etiq_fields = current_etiquette_fields(skills)
        if not etiq_fields:
            continue  # no etiquette set in this player snapshot
        for ef in etiq_fields:
            if ef.tag == new_tag:
                changes.append(f"  player @0x{pc.tag_off:x}: already etiquette tag {new_tag}, no change")
                continue
            # Replace the field's tag varint with a new one of (hopefully) the same length
            old_tag_bytes = data[ef.tag_off : ef.val_off]
            new_wire = ef.wire
            new_key = (new_tag << 3) | new_wire
            new_tag_bytes = varint_encode(new_key)
            if len(new_tag_bytes) != len(old_tag_bytes):
                raise RuntimeError(
                    f"Tag width mismatch ({len(old_tag_bytes)} -> {len(new_tag_bytes)} bytes). "
                    f"This shouldn't happen between etiquette tags 20-31."
                )
            out[ef.tag_off : ef.val_off] = new_tag_bytes
            changes.append(
                f"  player @0x{pc.tag_off:x}: changed etiquette tag {ef.tag} -> {new_tag} "
                f"(byte @0x{ef.tag_off:x}: 0x{old_tag_bytes[0]:02x} -> 0x{new_tag_bytes[0]:02x}), "
                f"value preserved = {ef.payload}"
            )

    return bytes(out), changes

# ----- CLI / batch ------------------------------------------------------------

def name_to_tag(name_or_num):
    s = str(name_or_num).strip().lower()
    if s.isdigit():
        n = int(s)
        if n not in ETIQUETTE_TAG_SET:
            raise SystemExit(f"Tag {n} is not a known etiquette tag")
        return n
    if s in ETIQUETTE_TAGS:
        return ETIQUETTE_TAGS[s]
    raise SystemExit(f"Unknown etiquette '{name_or_num}'. Valid: {', '.join(ETIQUETTE_TAGS)}")

def process_file(path, new_tag, do_backup=True, dry_run=False):
    with open(path, 'rb') as f:
        data = f.read()
    try:
        new_data, changes = patch_etiquette(data, new_tag)
    except RuntimeError as e:
        return f"  {os.path.basename(path)}: SKIP ({e})"
    if not changes:
        return f"  {os.path.basename(path)}: no player etiquette field found"
    if dry_run:
        return (f"  {os.path.basename(path)}: would change:\n" + "\n".join(changes))
    if do_backup:
        bak = path + '.bak'
        if not os.path.exists(bak):
            shutil.copy2(path, bak)
    with open(path, 'wb') as f:
        f.write(new_data)
    return (f"  {os.path.basename(path)}: PATCHED\n" + "\n".join(changes))

def main():
    p = argparse.ArgumentParser(
        description="Patch the player's etiquette in Shadowrun: Dragonfall DC save files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Valid etiquettes:  """ + ', '.join(f"{n}={t}" for n,t in ETIQUETTE_TAGS.items()) + """

Examples:
  Dry run a folder:
      python3 patch_etiquette.py --to academic --dry-run ~/Library/Application\\ Support/Shadowrun\\ Dragonfall\\ Director\\'s\\ Cut/Save\\ Games

  Patch a specific save slot's .sav and matching .srt files in place:
      python3 patch_etiquette.py --to security d84c2e23a66e4ceb9d4c12cae9919cad

  Patch one file:
      python3 patch_etiquette.py --to street some_save.sav
""")
    p.add_argument('paths', nargs='+', help='File(s), folder(s), or save-id prefix(es)')
    p.add_argument('--to', required=True,
                   help='Target etiquette name (e.g. security) or tag number (20-31)')
    p.add_argument('--no-backup', action='store_true', help='Skip writing .bak files')
    p.add_argument('--dry-run', action='store_true', help='Show what would change, do not write')
    args = p.parse_args()

    new_tag = name_to_tag(args.to)
    target_name = next(n for n,t in ETIQUETTE_TAGS.items() if t == new_tag)
    print(f"Target etiquette: {target_name} (tag {new_tag})")
    print(f"Mode: {'DRY-RUN' if args.dry_run else 'WRITE'}  Backup: {not args.no_backup}\n")

    files = []
    for arg in args.paths:
        if os.path.isfile(arg):
            files.append(arg)
        elif os.path.isdir(arg):
            files.extend(sorted(glob.glob(os.path.join(arg, '*.sav'))))
            files.extend(sorted(glob.glob(os.path.join(arg, '*.srt'))))
        else:
            # Treat as save-id prefix
            base = os.path.basename(arg)
            d = os.path.dirname(arg) or '.'
            files.extend(sorted(glob.glob(os.path.join(d, base + '*.sav'))))
            files.extend(sorted(glob.glob(os.path.join(d, base + '*.srt'))))

    if not files:
        print("No .sav or .srt files matched.")
        sys.exit(1)

    print(f"Found {len(files)} file(s) to inspect:\n")
    for f in files:
        print(process_file(f, new_tag, do_backup=not args.no_backup, dry_run=args.dry_run))
        print()

if __name__ == '__main__':
    main()
