"""
Shared HBS-engine domain layer.

The Returns, Dragonfall, and Hong Kong save formats are all the same
protobuf-net dialect with the same `SaveGame` / `SaveStoryBlock` /
`CharacterInstance` / `CharacterMod` shape. Attribute, skill, and
etiquette tag numbers are identical (see ETIQUETTES, ATTRIBUTES, SKILLS
below). The per-game `domain.<game>` modules import everything here and
add only the bits that genuinely differ (e.g. Dragonfall's Alice Fund
donation, HK's cyberware essence accounting).

Save-file layout:

    SaveGame                               (top of .sav)
    ├── tag 1   save_name                 string (UUID)
    ├── tag 2   display_name              string ("Shadowrun: Dragonfall ...")
    ├── tag 7   story_data    repeated    SaveStoryBlock
    │           ├── tag 9   nuyen                 int32
    │           ├── tag 5   variable_data         repeated VariableDataSection
    │           │           └── tag 3 values      repeated TsNameValuePair
    │           │                       ├── tag 1 name (string, the flag name)
    │           │                       └── tag 2 value (TsVariant)
    │           ├── tag 3   newsave_party         repeated CharacterInstance
    │           └── tag 11  oldsave_party         repeated ActiveCharacterState
    │                       └── (contains CharacterInstance snapshots)
    └── ...

    CharacterInstance                     (the "player container")
    ├── tag 1   prefab_name              string  ("ElfMale" etc.)
    ├── tag 4   character_mod            CharacterMod
    │           ├── tag 1 stats          Attributes (body, quickness, ...)
    │           ├── tag 2 skills         Skills (incl. etiquettes 20-31)
    │           ├── tag 3 specializations Specializations
    │           ├── tag 4 archetypeName  string  -- == "Player" for the PC
    │           └── ...
    ├── tag 8   char_name                string
    ├── tag 40  portrait                 TextureRef
    ├── tag 42  portrait_code_override   string
    ├── tag 60  karma                    int32   (per-character earned karma)
    ├── tag 65  unspent_karma            int32   (karma the player can still spend)
    └── ...
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator

from ..protobuf_engine import (
    Field,
    WIRE_LEN,
    WIRE_VARINT,
    find_all,
    find_first,
    walk,
)


def _signed_int32(n: int) -> int:
    """Interpret a varint value as a signed int32. protobuf-net encodes
    negative int32 as 64-bit-wide varints (TwosComplement DataFormat=2)."""
    n &= (1 << 64) - 1
    if n & (1 << 63):
        n -= 1 << 64
    if -(1 << 31) <= n <= (1 << 31) - 1:
        return n
    # Some fields are genuinely wider (int64); leave them alone.
    return n


# Tag map mirrors the Skills schema for tags 20-31 (the etiquettes block).
ETIQUETTES: dict[str, int] = {
    "corporate":     20,
    "security":      21,
    "gang":          22,
    "paranormal":    23,   # HK only — see plan §10 note 6
    "socialite":     24,
    "infected":      25,   # HK only
    "shadowrunner":  29,
    "street":        30,
    "academic":      31,
}
ETIQUETTE_TAGS: set[int] = set(ETIQUETTES.values())


# Attribute names (Attributes message, tags 1-9).
#
# NOTE: these stored values are a *modifier over a content-pack racial base
# sheet* (character_sheet_id = "CG <Race> None"), not the effective attribute
# the game shows. The service resolves effective = base + modifier when the
# content-pack catalog is installed (see catalog.base_attributes / service
# _effective_attributes); without it, the raw modifier is shown and an
# attribute left at base reads as 0. Full write-up in
# SHADOWRUN_EDITOR_PLAN.md §10 gotcha 12.
ATTRIBUTES: dict[str, int] = {
    "body":         1,
    "quickness":    2,
    "strength":     3,
    "charisma":     4,
    "intelligence": 5,
    "willpower":    6,
    "essence":      7,
    "magic":        8,
    "reaction":     9,
}

# Attributes the player never spends karma on directly — the games derive
# them at runtime, so CharacterMod.stats stores engine-internal values, not
# something a user should set. Empirically across all three reference saves:
# `reaction` is stored as a negative modifier/offset (-33 / -3 / -41, not a
# real Reaction score) and `essence` is omitted entirely (it's 6 minus
# cyberware, computed at load). They stay in ATTRIBUTES so any save still
# reads + round-trips, but no adapter offers them as editable — a stepper
# showing "-41" for Reaction is worse than no stepper. The other seven
# (body/quickness/strength/charisma/intelligence/willpower/magic) are the
# real karma-buyable attributes and their read/write is in-game verified.
DERIVED_ATTRIBUTES: frozenset[str] = frozenset({"reaction", "essence"})


# Skills (Skills message, tags 1-19 + 26-28 + 32; etiquettes are 20-25, 29-31)
SKILLS: dict[str, int] = {
    "ranged_combat":     1,
    "close_combat":      2,
    "throwing_weapons":  3,
    "spellcasting":      4,
    "decking":           5,
    "deck_build_repair": 6,
    "conjuring":         7,
    "spirit_summoning":  8,
    "spirit_control":    9,
    "spirit_banishing":  10,
    "magic_defense":     11,
    "drone_control":     12,
    "remote_gunnery":    13,
    "drone_build_repair":14,
    "athletics":         15,
    "biotech":           16,
    "dodge":             17,
    "negotiation":       18,
    "stealth":           19,
    "chi_casting":       26,
    "drain_resistance":  27,
    "drone_combat":      28,
    # HK-only. The Skills message declares this field only in Hong Kong's
    # schema (absent from Returns/Dragonfall). Gated to HK in that adapter's
    # AVAILABLE_SKILLS; kept here so it reads/round-trips on any HK save.
    "cyberware_affinity": 32,
}

# Fields that exist in the Skills message but the games never expose for
# karma investment. They're 0 on every player snapshot across all three
# titles' reference corpus — including a fully-built Hong Kong endgame
# character — and there's no in-game skill bar for them: the HBS games
# route those checks through attributes (Strength/Quickness) and
# Charisma + Etiquettes instead. They stay in SKILLS so any save reads and
# round-trips cleanly, but no adapter offers them as editable (a slider
# that does nothing is worse than no slider). Contrast dodge/biotech, which
# ARE real player skills and do carry values.
NON_PLAYER_SKILLS: frozenset[str] = frozenset({"athletics", "negotiation", "stealth"})


# --------------------------------------------------------------------------- #
# Player snapshot identification                                              #
# --------------------------------------------------------------------------- #

def _msg_marks_player(cm_fields: list[Field]) -> bool:
    """Inside a CharacterMod, the archetypeName field (tag 4) == 'Player' for
    the playable character."""
    if not cm_fields:
        return False
    for f in cm_fields:
        if f.tag == 4 and f.wire == WIRE_LEN and f.value == b"Player":
            return True
    return False


def _is_player_container(ci_fields: list[Field]) -> bool:
    """A CharacterInstance is THE playable character (not a party member or
    drone) when:
      1) its character_mod.archetypeName == "Player" — this marks anything
         the player can control (so it includes party members and drones),
         AND
      2) its pc_spawn_number (tag 16) == 0 — only the PC gets spawn slot 0;
         party members get 1, 2, 3, etc.

    The plan's original heuristic in patch_etiquette.py used only condition 1,
    which is why it counted 71 "player containers" in a real save: party
    members and drones share the Player archetype. Adding condition 2 filters
    down to the actual player character snapshots.
    """
    if not ci_fields:
        return False
    has_player_archetype = False
    pc_spawn = None
    for f in ci_fields:
        if f.tag == 4 and f.wire == WIRE_LEN and f.children is not None:
            if _msg_marks_player(f.children):
                has_player_archetype = True
        elif f.tag == 16 and f.wire == WIRE_VARINT:
            pc_spawn = int(f.value)  # type: ignore[arg-type]
    return has_player_archetype and pc_spawn == 0


@dataclass
class PlayerSnapshot:
    """One CharacterInstance message that represents the player character.

    A single .sav can contain dozens of these (newsave_party, oldsave_party,
    autosave history, scene-pinned snapshots). For most edits we mutate
    every snapshot that has substantive data so the game can't resurrect an
    old state from a stale snapshot — see plan §10 note 2.
    """
    container: Field  # the CharacterInstance Field

    @property
    def char_name(self) -> str | None:
        f = find_first(self.container.children or [], 8, WIRE_LEN)
        return None if f is None else f.value.decode("utf-8", errors="replace")  # type: ignore[union-attr]

    @property
    def prefab_name(self) -> str | None:
        f = find_first(self.container.children or [], 1, WIRE_LEN)
        return None if f is None else f.value.decode("utf-8", errors="replace")  # type: ignore[union-attr]

    @property
    def character_sheet_id(self) -> str | None:
        """CharacterInstance tag 2 — the base sheet this character derives
        from, e.g. 'CG Elf None'. Used to resolve racial base attributes from
        the content-pack catalog (stored stats are modifiers over this base)."""
        f = find_first(self.container.children or [], 2, WIRE_LEN)
        return None if f is None else f.value.decode("utf-8", errors="replace")  # type: ignore[union-attr]

    @property
    def character_mod(self) -> Field | None:
        return find_first(self.container.children or [], 4, WIRE_LEN)

    @property
    def attributes(self) -> Field | None:
        cm = self.character_mod
        if cm is None or cm.children is None:
            return None
        return find_first(cm.children, 1, WIRE_LEN)

    @property
    def skills(self) -> Field | None:
        cm = self.character_mod
        if cm is None or cm.children is None:
            return None
        return find_first(cm.children, 2, WIRE_LEN)

    @property
    def specializations(self) -> Field | None:
        cm = self.character_mod
        if cm is None or cm.children is None:
            return None
        return find_first(cm.children, 3, WIRE_LEN)

    @property
    def unspent_karma(self) -> int | None:
        f = find_first(self.container.children or [], 65, WIRE_VARINT)
        return None if f is None else _signed_int32(int(f.value))  # type: ignore[arg-type]

    @property
    def karma(self) -> int | None:
        f = find_first(self.container.children or [], 60, WIRE_VARINT)
        return None if f is None else _signed_int32(int(f.value))  # type: ignore[arg-type]

    @property
    def portrait_code(self) -> str | None:
        f = find_first(self.container.children or [], 42, WIRE_LEN)
        return None if f is None else f.value.decode("utf-8", errors="replace")  # type: ignore[union-attr]

    @property
    def equipment(self) -> list[Field]:
        """The repeated Equipment messages (CharacterInstance tag 5). One
        entry per item the character carries; duplicates of the same prefab
        represent a stack (the game has no quantity field — two HealthPacks
        are two Equipment entries)."""
        return [
            c for c in (self.container.children or [])
            if c.tag == 5 and c.wire == WIRE_LEN and c.children is not None
        ]

    def has_meaningful_data(self) -> bool:
        """True if this snapshot has a built-up character (attributes set or
        skills set). Pre-character-creation skeleton snapshots are empty."""
        attrs = self.attributes
        skills = self.skills
        if attrs is not None and attrs.children:
            return True
        if skills is not None and skills.children:
            return True
        return False


def find_player_snapshots(top: list[Field]) -> list[PlayerSnapshot]:
    """Return every PlayerSnapshot anywhere in the parsed tree."""
    out: list[PlayerSnapshot] = []
    for f in walk(top):
        if f.wire == WIRE_LEN and f.children is not None and _is_player_container(f.children):
            out.append(PlayerSnapshot(container=f))
    return out


def first_player_char_name(top: list[Field]) -> str | None:
    """Lightweight: stop at the first player snapshot we find and return its
    char_name. Skips the full tree walk that find_player_snapshots does, so
    the save-slot picker can build summaries for 100+ saves quickly."""
    for f in walk(top):
        if f.wire == WIRE_LEN and f.children is not None and _is_player_container(f.children):
            name_f = find_first(f.children, 8, WIRE_LEN)
            if name_f is not None and isinstance(name_f.value, (bytes, bytearray)):
                try:
                    return name_f.value.decode("utf-8", errors="replace")
                except UnicodeDecodeError:
                    return None
    return None


def primary_player_snapshot(top: list[Field]) -> PlayerSnapshot | None:
    """Pick the snapshot most representative of the player's *current* state
    for display.

    The .sav contains snapshots in chronological order, oldest first: the
    earliest are the post-character-creation autosaves (before karma spent),
    later ones reflect the player's progression. The user wants to see what
    their character looks like right now, so we pick the LAST meaningful
    snapshot rather than the first.
    """
    snaps = find_player_snapshots(top)
    for s in reversed(snaps):
        if s.has_meaningful_data():
            return s
    return snaps[-1] if snaps else None


# --------------------------------------------------------------------------- #
# Edits                                                                       #
# --------------------------------------------------------------------------- #

@dataclass
class EditReport:
    """Summary of an edit applied across all matching player snapshots."""
    operation: str
    target: str
    changes: list[str] = field(default_factory=list)

    def add(self, msg: str) -> None:
        self.changes.append(msg)

    def __bool__(self) -> bool:
        return bool(self.changes)


def _set_or_insert_varint(parent: Field, tag: int, value: int) -> tuple[bool, int | None]:
    """In `parent.children`, set field `tag` to `value` (insert if absent).
    Returns (changed, old_value)."""
    assert parent.children is not None, "parent must be a parsed message"
    for c in parent.children:
        if c.tag == tag and c.wire == WIRE_VARINT:
            old = int(c.value)  # type: ignore[arg-type]
            if old == value:
                return False, old
            c.set_int(value)
            parent.mark_dirty()
            return True, old
    # Insert. Place after the last field with tag <= our tag, to keep ordering
    # roughly numeric (the game itself emits sparse fields in tag order).
    new_f = Field(tag=tag, wire=WIRE_VARINT, value=value, dirty=True)
    insert_at = len(parent.children)
    for i, c in enumerate(parent.children):
        if c.tag > tag:
            insert_at = i
            break
    parent.children.insert(insert_at, new_f)
    parent.mark_dirty()
    return True, None


def _remove_field(parent: Field, tag: int) -> Field | None:
    """Remove the first matching field by tag. Returns the removed Field or None."""
    assert parent.children is not None
    for i, c in enumerate(parent.children):
        if c.tag == tag:
            removed = parent.children.pop(i)
            parent.mark_dirty()
            return removed
    return None


def set_etiquette(top: list[Field], etiquette_name: str) -> EditReport:
    """Set the player's etiquette by *replacing* whichever etiquette tag is
    currently present. Preserves the existing skill rating value.

    Single-etiquette semantics — kept for the legacy CLI command and for
    callers that want "swap one for another". For multi-etiquette editing
    use add_etiquette / remove_etiquette.

    If no etiquette is currently set on a snapshot it's left alone (we don't
    invent a value). Snapshots without character data are also skipped.
    """
    if etiquette_name not in ETIQUETTES:
        raise ValueError(
            f"unknown etiquette {etiquette_name!r}; valid: {sorted(ETIQUETTES)}"
        )
    target_tag = ETIQUETTES[etiquette_name]
    report = EditReport(operation="set_etiquette", target=etiquette_name)

    for snap in find_player_snapshots(top):
        skills = snap.skills
        if skills is None or skills.children is None:
            continue
        present = [c for c in skills.children if c.tag in ETIQUETTE_TAGS]
        if not present:
            continue
        # If the target is already present, nothing to do for this snapshot.
        if any(c.tag == target_tag for c in present):
            continue
        # Replace the FIRST etiquette tag with the target, preserving value.
        # If multiple etiquettes are present (e.g. the PC bought two with
        # karma), the others are left in place — this matches what the game
        # itself allows. For Phase 1 we don't try to be cleverer.
        ef = present[0]
        old_tag = ef.tag
        old_value = ef.value if ef.wire == WIRE_VARINT else None
        ef.set_tag(target_tag)
        snap.skills.mark_dirty()  # ancestor lengths will be recomputed
        skills.children.sort(key=lambda f: f.tag)
        report.add(
            f"  player {snap.char_name or '?'}: etiquette tag {old_tag} "
            f"-> {target_tag} (value={old_value})"
        )
    return report


def add_etiquette(top: list[Field], etiquette_name: str, default_value: int = 1) -> EditReport:
    """Activate an etiquette on the player. If the etiquette field is already
    present with a non-zero rating, it's left untouched (preserves the rating
    the player has paid karma for). If absent — or present with rating 0 —
    it's set to `default_value` (1 by default, the rating a freshly-picked
    etiquette starts at)."""
    if etiquette_name not in ETIQUETTES:
        raise ValueError(
            f"unknown etiquette {etiquette_name!r}; valid: {sorted(ETIQUETTES)}"
        )
    target_tag = ETIQUETTES[etiquette_name]
    report = EditReport(operation="add_etiquette", target=etiquette_name)

    for snap in find_player_snapshots(top):
        if not snap.has_meaningful_data():
            continue
        skills = snap.skills
        if skills is None or skills.children is None:
            continue
        existing = next((c for c in skills.children if c.tag == target_tag), None)
        if existing is not None and existing.wire == WIRE_VARINT and int(existing.value) > 0:  # type: ignore[arg-type]
            continue
        changed, old = _set_or_insert_varint(skills, target_tag, default_value)
        if changed:
            skills.children.sort(key=lambda f: f.tag)
            report.add(
                f"  player {snap.char_name or '?'}: +{etiquette_name} "
                f"(rating {old or 0} -> {default_value})"
            )
    return report


def remove_etiquette(top: list[Field], etiquette_name: str) -> EditReport:
    """Deactivate an etiquette on the player by dropping its skill field
    entirely. protobuf-net's default-value omission means an absent field
    reads back as 0, so the in-game character sheet will no longer list it."""
    if etiquette_name not in ETIQUETTES:
        raise ValueError(
            f"unknown etiquette {etiquette_name!r}; valid: {sorted(ETIQUETTES)}"
        )
    target_tag = ETIQUETTES[etiquette_name]
    report = EditReport(operation="remove_etiquette", target=etiquette_name)

    for snap in find_player_snapshots(top):
        skills = snap.skills
        if skills is None or skills.children is None:
            continue
        existing = next((c for c in skills.children if c.tag == target_tag), None)
        if existing is None:
            continue
        old_value = existing.value if existing.wire == WIRE_VARINT else None
        removed = _remove_field(skills, target_tag)
        if removed is not None:
            report.add(
                f"  player {snap.char_name or '?'}: -{etiquette_name} "
                f"(was rating {old_value})"
            )
    return report


def set_etiquette_rating(top: list[Field], etiquette_name: str, rating: int) -> EditReport:
    """Set an etiquette to an exact rating across all meaningful snapshots.
    rating 0 removes it (same as remove_etiquette); >0 inserts/updates the
    tag. Unlike add_etiquette (which won't lower or change an already-set
    rating) this writes the precise value — needed to faithfully restore an
    exported character template, where ratings above 1 matter."""
    if etiquette_name not in ETIQUETTES:
        raise ValueError(
            f"unknown etiquette {etiquette_name!r}; valid: {sorted(ETIQUETTES)}"
        )
    if rating < 0:
        raise ValueError(f"rating must be >= 0, got {rating}")
    if rating == 0:
        return remove_etiquette(top, etiquette_name)
    target_tag = ETIQUETTES[etiquette_name]
    report = EditReport(operation="set_etiquette_rating",
                        target=f"{etiquette_name}={rating}")
    for snap in find_player_snapshots(top):
        if not snap.has_meaningful_data():
            continue
        skills = snap.skills
        if skills is None or skills.children is None:
            continue
        changed, old = _set_or_insert_varint(skills, target_tag, rating)
        if changed:
            skills.children.sort(key=lambda f: f.tag)
            report.add(
                f"  player {snap.char_name or '?'}: {etiquette_name} "
                f"rating {old or 0} -> {rating}"
            )
    return report


def set_attribute(top: list[Field], attr_name: str, value: int) -> EditReport:
    if attr_name not in ATTRIBUTES:
        raise ValueError(f"unknown attribute {attr_name!r}; valid: {sorted(ATTRIBUTES)}")
    tag = ATTRIBUTES[attr_name]
    report = EditReport(operation="set_attribute", target=f"{attr_name}={value}")
    for snap in find_player_snapshots(top):
        attrs = snap.attributes
        if attrs is None or attrs.children is None:
            continue
        if not snap.has_meaningful_data():
            continue
        changed, old = _set_or_insert_varint(attrs, tag, value)
        if changed:
            report.add(f"  player {snap.char_name or '?'}: {attr_name} {old} -> {value}")
    return report


def set_skill(top: list[Field], skill_name: str, value: int) -> EditReport:
    if skill_name not in SKILLS:
        raise ValueError(f"unknown skill {skill_name!r}; valid: {sorted(SKILLS)}")
    tag = SKILLS[skill_name]
    report = EditReport(operation="set_skill", target=f"{skill_name}={value}")
    for snap in find_player_snapshots(top):
        skills = snap.skills
        if skills is None or skills.children is None:
            continue
        if not snap.has_meaningful_data():
            continue
        changed, old = _set_or_insert_varint(skills, tag, value)
        if changed:
            report.add(f"  player {snap.char_name or '?'}: {skill_name} {old} -> {value}")
    return report


# --------------------------------------------------------------------------- #
# Inventory (Equipment)                                                       #
# --------------------------------------------------------------------------- #

def _equipment_prefab(eq: Field) -> str | None:
    """The prefab_name (Equipment tag 1) of one Equipment field, or None."""
    f = find_first(eq.children or [], 1, WIRE_LEN)
    if f is None or not isinstance(f.value, (bytes, bytearray)):
        return None
    return f.value.decode("utf-8", errors="replace")


def _new_equipment(prefab: str) -> Field:
    """Build a minimal Equipment message carrying only a prefab_name. The
    game resolves all other item data from the static ItemDef catalog at
    load time, so a name-only entry is valid — every consumable/spell entry
    in a real save is exactly this shape (tag 1 and nothing else)."""
    name = Field(tag=1, wire=WIRE_LEN, value=prefab.encode("utf-8"), dirty=True)
    eq = Field(tag=5, wire=WIRE_LEN, value=b"", children=[name], dirty=True)
    eq.mark_dirty()
    return eq


def _insert_equipment(container: Field, eq: Field) -> None:
    """Insert an Equipment field among the container's existing tag-5 entries,
    preserving the engine's roughly-tag-ordered field layout."""
    assert container.children is not None
    last_eq_idx = -1
    for i, c in enumerate(container.children):
        if c.tag == 5:
            last_eq_idx = i
    insert_at = last_eq_idx + 1 if last_eq_idx >= 0 else len(container.children)
    container.children.insert(insert_at, eq)
    container.mark_dirty()


def read_inventory(top: list[Field]) -> dict[str, int]:
    """Return ``{prefab_name: quantity}`` for the primary (displayed) player
    snapshot. Quantity is the number of Equipment entries sharing that prefab.
    Empty dict if there's no player snapshot."""
    primary = primary_player_snapshot(top)
    if primary is None:
        return {}
    counts: dict[str, int] = {}
    for eq in primary.equipment:
        name = _equipment_prefab(eq)
        if name is None:
            continue
        counts[name] = counts.get(name, 0) + 1
    return counts


def add_item(top: list[Field], prefab: str, count: int = 1) -> EditReport:
    """Append `count` copies of `prefab` to every meaningful player snapshot.

    Propagating to all snapshots (not just the displayed one) matches how the
    other edits behave and keeps the inventory consistent across the autosave
    blocks and scene caches in the file, so the game can't restore a pre-edit
    loadout when re-entering a visited scene (plan §10 note 2)."""
    if count < 1:
        raise ValueError(f"count must be >= 1, got {count}")
    report = EditReport(operation="add_item", target=f"{prefab} x{count}")
    for snap in find_player_snapshots(top):
        if not snap.has_meaningful_data():
            continue
        for _ in range(count):
            _insert_equipment(snap.container, _new_equipment(prefab))
        report.add(f"  player {snap.char_name or '?'}: +{count} {prefab}")
    return report


def remove_item(top: list[Field], prefab: str, count: int | None = None) -> EditReport:
    """Remove up to `count` copies of `prefab` (or all copies if count is None)
    from every meaningful player snapshot."""
    report = EditReport(operation="remove_item", target=prefab)
    for snap in find_player_snapshots(top):
        if not snap.has_meaningful_data():
            continue
        assert snap.container.children is not None
        matches = [
            c for c in snap.container.children
            if c.tag == 5 and c.wire == WIRE_LEN and _equipment_prefab(c) == prefab
        ]
        if not matches:
            continue
        to_drop = matches if count is None else matches[:count]
        drop_set = set(id(m) for m in to_drop)
        snap.container.children[:] = [
            c for c in snap.container.children if id(c) not in drop_set
        ]
        snap.container.mark_dirty()
        report.add(f"  player {snap.char_name or '?'}: -{len(to_drop)} {prefab}")
    return report


def set_item_quantity(top: list[Field], prefab: str, quantity: int) -> EditReport:
    """Make every meaningful player snapshot hold exactly `quantity` copies of
    `prefab` — adding or removing entries as needed. quantity 0 removes it."""
    if quantity < 0:
        raise ValueError(f"quantity must be >= 0, got {quantity}")
    report = EditReport(operation="set_item_quantity", target=f"{prefab}={quantity}")
    for snap in find_player_snapshots(top):
        if not snap.has_meaningful_data():
            continue
        assert snap.container.children is not None
        matches = [
            c for c in snap.container.children
            if c.tag == 5 and c.wire == WIRE_LEN and _equipment_prefab(c) == prefab
        ]
        have = len(matches)
        if have == quantity:
            continue
        if have > quantity:
            drop_set = set(id(m) for m in matches[quantity:])
            snap.container.children[:] = [
                c for c in snap.container.children if id(c) not in drop_set
            ]
            snap.container.mark_dirty()
        else:
            for _ in range(quantity - have):
                _insert_equipment(snap.container, _new_equipment(prefab))
        report.add(f"  player {snap.char_name or '?'}: {prefab} {have} -> {quantity}")
    return report


def set_unspent_karma(top: list[Field], value: int) -> EditReport:
    report = EditReport(operation="set_unspent_karma", target=str(value))
    for snap in find_player_snapshots(top):
        if not snap.has_meaningful_data():
            continue
        changed, old = _set_or_insert_varint(snap.container, 65, value)
        if changed:
            report.add(f"  player {snap.char_name or '?'}: unspent_karma {old} -> {value}")
    return report


def _latest_story_block(top: list[Field]) -> Field | None:
    """Return the last SaveStoryBlock (tag 7 directly under the SaveGame top
    level). The game writes nuyen, world flags, and donation-coupled state
    to this block only; earlier blocks are autosave history."""
    last: Field | None = None
    for f in top:
        if f.tag == 7 and f.wire == WIRE_LEN and f.children is not None:
            last = f
    return last


def set_nuyen(top: list[Field], value: int) -> EditReport:
    """Set nuyen on the latest SaveStoryBlock only.

    Empirically (see two-save donation diff in /tmp/sim_donation.py) the
    game itself only writes nuyen to the latest block — earlier blocks
    preserve the historical balance at that autosave point. Writing nuyen
    across every block worked for casual cheats but didn't match the
    game's own behavior; for donation-coupled mechanics (Alice Fund,
    bribes, faction donations) consistency with the game's pattern
    matters."""
    report = EditReport(operation="set_nuyen", target=str(value))
    block = _latest_story_block(top)
    if block is None:
        return report
    changed, old = _set_or_insert_varint(block, 9, value)
    if changed:
        report.add(f"  story block nuyen {old} -> {value}")
    return report


# --------------------------------------------------------------------------- #
# Read helpers (for inspect/CLI display)                                      #
# --------------------------------------------------------------------------- #

@dataclass
class CharacterSheet:
    """A rendered view of the primary player snapshot, for display."""
    name: str | None
    prefab: str | None
    portrait_code: str | None
    archetype: str | None
    karma: int | None
    unspent_karma: int | None
    attributes: dict[str, int]
    skills: dict[str, int]
    etiquettes: dict[str, int]      # name → rating
    specializations: dict[str, int]
    snapshot_count: int             # number of snapshots in the .sav

    @classmethod
    def from_top(cls, top: list[Field]) -> "CharacterSheet | None":
        snaps = find_player_snapshots(top)
        if not snaps:
            return None
        # Pick the most-recent meaningful snapshot; the early snapshots in the
        # file are post-character-creation autosaves that won't show karma
        # spent later in the playthrough.
        primary = next(
            (s for s in reversed(snaps) if s.has_meaningful_data()),
            snaps[-1],
        )

        def _msg_int_map(msg: Field | None, tag_to_name: dict[int, str]) -> dict[str, int]:
            out: dict[str, int] = {}
            if msg is None or msg.children is None:
                return out
            for f in msg.children:
                if f.tag in tag_to_name and f.wire == WIRE_VARINT:
                    out[tag_to_name[f.tag]] = _signed_int32(int(f.value))  # type: ignore[arg-type]
            return out

        attr_inv = {v: k for k, v in ATTRIBUTES.items()}
        skill_inv = {v: k for k, v in SKILLS.items()}
        etiq_inv = {v: k for k, v in ETIQUETTES.items()}

        attrs = _msg_int_map(primary.attributes, attr_inv)
        skills = _msg_int_map(primary.skills, skill_inv)
        etiqs = _msg_int_map(primary.skills, etiq_inv)

        # archetypeName lives on character_mod tag 4
        archetype = None
        cm = primary.character_mod
        if cm is not None and cm.children is not None:
            af = find_first(cm.children, 4, WIRE_LEN)
            if af is not None:
                archetype = af.value.decode("utf-8", errors="replace")  # type: ignore[union-attr]

        # Specializations: schema mostly overlaps with skill names — represent
        # them just as a tag → value map, then look up names against schema.
        spec_map: dict[str, int] = {}
        if primary.specializations is not None and primary.specializations.children is not None:
            # We don't ship a Specializations name table here; just show the
            # tag numbers raw. The schema bundle is the source of truth.
            for f in primary.specializations.children:
                if f.wire == WIRE_VARINT:
                    spec_map[f"tag_{f.tag}"] = int(f.value)  # type: ignore[arg-type]

        return cls(
            name=primary.char_name,
            prefab=primary.prefab_name,
            portrait_code=primary.portrait_code,
            archetype=archetype,
            karma=primary.karma,
            unspent_karma=primary.unspent_karma,
            attributes=attrs,
            skills=skills,
            etiquettes=etiqs,
            specializations=spec_map,
            snapshot_count=len(snaps),
        )


def read_nuyen(top: list[Field]) -> int | None:
    """Most-recent nuyen value across all story blocks."""
    latest: int | None = None
    for f in top:
        if f.tag == 7 and f.wire == WIRE_LEN and f.children is not None:
            nf = find_first(f.children, 9, WIRE_VARINT)
            if nf is not None:
                latest = _signed_int32(int(nf.value))  # type: ignore[arg-type]
    return latest


# --------------------------------------------------------------------------- #
# World flags                                                                 #
# --------------------------------------------------------------------------- #

@dataclass
class WorldFlag:
    name: str
    scope: int | None       # TsVariableScope enum value
    scope_name: str | None
    field: Field            # The TsNameValuePair Field
    value_field: Field      # The TsVariant Field (tag 2 of TsNameValuePair)

    def value(self) -> tuple[str, object]:
        """Return (kind, value) where kind is one of int, bool, float, string."""
        if self.value_field.children is None:
            return ("empty", None)
        for vc in self.value_field.children:
            if vc.tag == 1 and vc.wire == WIRE_VARINT:
                return ("int", int(vc.value))  # type: ignore[arg-type]
            if vc.tag == 2 and vc.wire == WIRE_VARINT:
                return ("bool", bool(vc.value))
            if vc.tag == 3 and vc.wire == 5:
                import struct
                return ("float", struct.unpack("<f", vc.value)[0])  # type: ignore[arg-type]
            if vc.tag == 4 and vc.wire == WIRE_LEN:
                try:
                    return ("string", vc.value.decode("utf-8"))  # type: ignore[union-attr]
                except UnicodeDecodeError:
                    return ("bytes", bytes(vc.value))  # type: ignore[arg-type]
        return ("unknown", None)


def iter_world_flags(top: list[Field]) -> Iterator[WorldFlag]:
    """Yield every world-flag TsNameValuePair found under SaveStoryBlock
    variable_data sections."""
    for f in top:
        if f.tag != 7 or f.wire != WIRE_LEN or f.children is None:
            continue
        # SaveStoryBlock.tag 5 = variable_data (repeated VariableDataSection)
        for vds in find_all(f.children, 5, WIRE_LEN):
            if vds.children is None:
                continue
            scope_f = find_first(vds.children, 1, WIRE_VARINT)
            scope_name_f = find_first(vds.children, 2, WIRE_LEN)
            scope = int(scope_f.value) if scope_f else None  # type: ignore[arg-type]
            scope_name = scope_name_f.value.decode("utf-8", errors="replace") if scope_name_f else None  # type: ignore[union-attr]
            for pair in find_all(vds.children, 3, WIRE_LEN):
                if pair.children is None:
                    continue
                name_f = find_first(pair.children, 1, WIRE_LEN)
                val_f = find_first(pair.children, 2, WIRE_LEN)
                if name_f is None or val_f is None:
                    continue
                yield WorldFlag(
                    name=name_f.value.decode("utf-8", errors="replace"),  # type: ignore[union-attr]
                    scope=scope,
                    scope_name=scope_name,
                    field=pair,
                    value_field=val_f,
                )
