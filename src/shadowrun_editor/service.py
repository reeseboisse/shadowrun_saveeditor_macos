"""High-level service API for the GUI.

The GUI (SwiftUI, or anything else) interacts with the save editor through
SaveSession objects. A session holds the original parsed bytes of every
file in a save slot plus an ordered list of pending edits; queries always
reflect "current bytes plus pending edits applied", so the UI can show a
live preview and support undo/redo without persisting until the user
commits.

Design notes:

- Edits are *descriptions*, not in-place mutations of the parsed tree.
  This makes undo trivial (drop the last edit and re-derive), keeps a
  clean record of "what will change" for the pre-commit diff modal, and
  side-steps the question of cloning Field trees.
- Re-parsing is cheap relative to file I/O (sub-second for a 12 MB .srt),
  so the simple replay-from-scratch approach is fine; if it becomes a
  bottleneck we can incrementalize later.
- Game neutrality: a SaveSession knows which game its save is from and
  routes domain calls through the right adapter. For Phase 2 only the
  Dragonfall adapter is functional; other games are recognized but their
  edit operations raise an UnsupportedGame error so the UI can show a
  "coming in Phase N" placeholder without crashing.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable

from .domain import dragonfall as df
from .protobuf_engine import parse_toplevel, serialize_message, Field, WIRE_VARINT
from .savefile import (
    SaveSlot,
    atomic_write_bytes,
    backup_file,
    detect_game,
    scan_folder,
)


SUPPORTED_GAMES = {"dragonfall"}     # games whose edit ops are wired up
KNOWN_GAMES = {"dragonfall", "returns", "hongkong"}


class UnsupportedGame(Exception):
    """Raised when a save is from a known game but Phase N doesn't wire up
    edits for it yet (Returns is Phase 3, HK is Phase 4)."""


@dataclass(frozen=True)
class PendingEdit:
    """One edit the user has queued but not yet committed."""
    op: str                              # set_etiquette, set_karma, set_nuyen, etc.
    args: dict[str, Any]                 # operation arguments (etiquette name, value, ...)
    description: str                     # human-readable summary for the diff modal


@dataclass
class SaveSummary:
    """One-line metadata for the save slot picker."""
    uuid: str
    folder: str
    sav_path: str
    thumbnail_path: str | None
    game: str
    supported: bool                      # whether this game's edits are functional
    display_name: str | None             # player-friendly title from the .sav
    char_name: str | None
    time_utc: int | None
    scene_name: str | None


@dataclass
class WorldFlagView:
    """Display row for the world-flags list view."""
    name: str
    kind: str                            # int / bool / float / string / empty
    value: Any                           # JSON-serializable
    scope_name: str | None


@dataclass
class CharacterView:
    """Everything the character editor displays for the current snapshot."""
    name: str | None
    prefab: str | None
    archetype: str | None
    portrait_code: str | None
    karma: int | None
    unspent_karma: int | None
    nuyen: int | None
    attributes: dict[str, int]
    skills: dict[str, int]
    etiquettes: dict[str, int]
    snapshot_count: int


# --------------------------------------------------------------------------- #
# Session                                                                     #
# --------------------------------------------------------------------------- #

def _edit_target_key(e: PendingEdit) -> tuple | None:
    """An immutable key identifying *which logical field* this edit targets.
    Edits with the same key are mutually exclusive — only the latest one
    matters. Return None for edits whose semantics don't fit the swap-
    supersedes model (the legacy set_etiquette swap is hard to define
    cleanly here so it skips deduplication)."""
    a = e.args
    if e.op == "set_attribute":
        return ("attribute", a["attr"])
    if e.op == "set_skill":
        return ("skill", a["skill"])
    if e.op == "set_unspent_karma":
        return ("unspent_karma",)
    if e.op == "set_nuyen":
        return ("nuyen",)
    if e.op == "set_world_flag":
        return ("world_flag", a["name"])
    if e.op in ("add_etiquette", "remove_etiquette"):
        return ("etiquette_active", a["etiquette"])
    return None


def _edit_is_noop(e: PendingEdit, original_top: list[Field]) -> bool:
    """Return True if applying `e` would not change the *displayed* state of
    the save. The UI shows the most recent player snapshot, so a "revert" is
    determined relative to that snapshot — not against every historical
    snapshot in the file. (Stale earlier snapshots stay stale; that matches
    the editor's behavior before any edit was queued.)"""
    a = e.args
    primary = df.primary_player_snapshot(original_top)

    if e.op == "set_attribute":
        tag = df.ATTRIBUTES.get(a["attr"])
        if tag is None or primary is None:
            return False
        return _varint_in(primary.attributes, tag) == int(a["value"])

    if e.op == "set_skill":
        tag = df.SKILLS.get(a["skill"])
        if tag is None or primary is None:
            return False
        return _varint_in(primary.skills, tag) == int(a["value"])

    if e.op == "set_unspent_karma":
        if primary is None:
            return False
        return (primary.unspent_karma or 0) == int(a["value"])

    if e.op == "set_nuyen":
        current = df.read_nuyen(original_top)
        return current is not None and current == int(a["value"])

    if e.op == "add_etiquette":
        tag = df.ETIQUETTES.get(a["etiquette"])
        if tag is None or primary is None:
            return False
        return _varint_in(primary.skills, tag) > 0

    if e.op == "remove_etiquette":
        tag = df.ETIQUETTES.get(a["etiquette"])
        if tag is None or primary is None:
            return False
        return _varint_in(primary.skills, tag) == 0

    if e.op == "set_world_flag":
        name = a["name"]
        kind = a["kind"]
        target = a["value"]
        # Display uses the LATEST block's value; the edit now only touches
        # the latest block too — keep the no-op check consistent.
        latest_kind = None
        latest_value: Any = None
        for fl in df.iter_world_flags(original_top):
            if fl.name == name:
                latest_kind, latest_value = fl.value()
        if latest_kind is None:
            return False
        return latest_kind == kind and latest_value == target

    return False


def _varint_in(msg: Field | None, tag: int) -> int:
    """Return the signed-int32 value of varint field `tag` inside `msg`, or 0
    if absent. Used by the no-op checks to compare displayed-state values."""
    if msg is None or msg.children is None:
        return 0
    f = next((c for c in msg.children if c.tag == tag), None)
    if f is None or f.wire != WIRE_VARINT:
        return 0
    return df._signed_int32(int(f.value))  # type: ignore[arg-type]


@dataclass
class _FileSnapshot:
    """Per-file state inside a SaveSession. Bytes are read lazily — only the
    .sav is loaded eagerly; .srt files are deferred until an edit commit
    actually needs to rewrite them, since the slot picker doesn't care
    about scene snapshots."""
    path: Path
    _bytes: bytes | None = None  # None = not loaded yet

    @property
    def original_bytes(self) -> bytes:
        if self._bytes is None:
            self._bytes = self.path.read_bytes()
        return self._bytes

    @original_bytes.setter
    def original_bytes(self, b: bytes) -> None:
        self._bytes = b


class SaveSession:
    """An open save slot with an in-memory edit queue."""

    def __init__(self, slot: SaveSlot):
        self.slot = slot
        # Read the .sav eagerly — needed for game detection and the summary.
        sav_bytes = slot.sav_path.read_bytes()
        self.game = detect_game(sav_bytes)
        sav_snap = _FileSnapshot(slot.sav_path)
        sav_snap.original_bytes = sav_bytes  # already in memory, skip another read
        self._files: list[_FileSnapshot] = [
            sav_snap,
            *(_FileSnapshot(p) for p in slot.srt_paths),
        ]
        self._edits: list[PendingEdit] = []
        self._cached_original_top: list[Field] | None = None

    # ----- factories ----- #

    @classmethod
    def open(cls, target: str | Path) -> "SaveSession":
        """Open a save slot from a folder, a .sav file, or a uuid prefix."""
        p = Path(target)
        if p.is_file() and p.suffix.lower() == ".sav":
            folder_slots = scan_folder(p.parent)
            slot = next((s for s in folder_slots if s.sav_path == p), None)
            if slot is None:
                slot = SaveSlot(uuid=p.stem, folder=p.parent, sav_path=p)
            return cls(slot)
        if p.is_dir():
            slots = scan_folder(p)
            if len(slots) == 1:
                return cls(slots[0])
            raise ValueError(
                f"{p}: contains {len(slots)} save slots; pass a .sav path instead"
            )
        raise FileNotFoundError(target)

    # ----- introspection ----- #

    @property
    def supported(self) -> bool:
        return self.game in SUPPORTED_GAMES

    @property
    def pending_edits(self) -> list[PendingEdit]:
        return list(self._edits)

    def has_changes(self) -> bool:
        return bool(self._edits)

    def summary(self) -> SaveSummary:
        """Lightweight per-slot metadata for the picker. Parses the .sav but
        only scans the protobuf tree far enough to extract display_name,
        time_utc, and the first player snapshot's char_name. The full
        character sheet is built lazily by `character()` only when the user
        actually opens this save."""
        sav_top = parse_toplevel(self._files[0].original_bytes)
        # display_name and scene_name live on the SaveGame top-level
        display = None
        time_utc: int | None = None
        # SaveGame schema: tag 2 display_name, tag 3 time_utc — for Returns/HK
        # these may shift, but tag 2 being a string near the start is consistent.
        for f in sav_top[:8]:
            if f.tag == 2 and f.wire == 2:
                try:
                    display = f.value.decode("utf-8")  # type: ignore[union-attr]
                except UnicodeDecodeError:
                    pass
            elif f.tag == 3 and f.wire == 0:
                time_utc = int(f.value)  # type: ignore[arg-type]
        char_name = df.first_player_char_name(sav_top) if self.supported else None
        # Scene name from the .srt filename (since .sav doesn't have one),
        # if there's exactly one .srt — saves usually pin a "last scene".
        scene_name: str | None = None
        if len(self.slot.srt_paths) >= 1:
            # filename is <uuid>-<SceneName>-<sceneUuid>.srt
            stem = self.slot.srt_paths[-1].stem
            parts = stem.split("-")
            if len(parts) >= 3:
                scene_name = "-".join(parts[1:-1])
        return SaveSummary(
            uuid=self.slot.uuid,
            folder=str(self.slot.folder),
            sav_path=str(self.slot.sav_path),
            thumbnail_path=str(self.slot.thumbnail_path) if self.slot.thumbnail_path else None,
            game=self.game,
            supported=self.supported,
            display_name=display,
            char_name=char_name,
            time_utc=time_utc,
            scene_name=scene_name,
        )

    def character(self) -> CharacterView | None:
        if not self.supported:
            return None
        sav_top = self._current_sav_tree()
        return self._character_view(sav_top)

    def world_flags(self) -> list[WorldFlagView]:
        if not self.supported:
            return []
        import sys as _sys
        sav_top = self._current_sav_tree()
        # Dedupe to one row per name (latest value wins, same as the CLI)
        latest: dict[str, WorldFlagView] = {}
        for fl in df.iter_world_flags(sav_top):
            try:
                kind, value = fl.value()
            except Exception as e:
                print(f"[bridge] flag {fl.name!r}: value() raised: {e}",
                      file=_sys.stderr)
                kind, value = "unknown", None
            scope = self._dragonfall_scope_name(fl.scope)
            # Best-effort JSON-friendly value
            if isinstance(value, (bytes, bytearray)):
                value = value.hex()
            elif value is not None and not isinstance(value, (int, float, bool, str)):
                # Anything exotic gets stringified so json.dumps doesn't choke
                # and the Swift side doesn't see a missing key.
                print(f"[bridge] flag {fl.name!r}: unexpected value type "
                      f"{type(value).__name__}, coercing to str",
                      file=_sys.stderr)
                value = str(value)
            # Sanity-check the name too. A long, non-ASCII, or newline-laden
            # name is probably a parser misread — log it so we can investigate.
            if len(fl.name) > 200 or any(c in fl.name for c in "\n\r\t"):
                print(f"[bridge] suspicious flag name (len={len(fl.name)}): "
                      f"{fl.name[:80]!r}...", file=_sys.stderr)
            latest[fl.name] = WorldFlagView(
                name=fl.name,
                kind=kind,
                value=value,
                scope_name=scope or fl.scope_name,
            )
        return [latest[k] for k in sorted(latest)]

    # ----- edit queue ----- #

    def queue_set_etiquette(self, etiquette: str) -> PendingEdit:
        self._require_supported()
        if etiquette not in df.ETIQUETTES:
            raise ValueError(f"unknown etiquette: {etiquette}")
        e = PendingEdit(
            op="set_etiquette",
            args={"etiquette": etiquette},
            description=f"Etiquette → {etiquette}",
        )
        self._append_edit(e)
        return e

    def queue_add_etiquette(self, etiquette: str) -> PendingEdit:
        self._require_supported()
        if etiquette not in df.ETIQUETTES:
            raise ValueError(f"unknown etiquette: {etiquette}")
        e = PendingEdit(
            op="add_etiquette",
            args={"etiquette": etiquette},
            description=f"Enable etiquette: {etiquette}",
        )
        self._append_edit(e)
        return e

    def queue_remove_etiquette(self, etiquette: str) -> PendingEdit:
        self._require_supported()
        if etiquette not in df.ETIQUETTES:
            raise ValueError(f"unknown etiquette: {etiquette}")
        e = PendingEdit(
            op="remove_etiquette",
            args={"etiquette": etiquette},
            description=f"Disable etiquette: {etiquette}",
        )
        self._append_edit(e)
        return e

    def queue_set_attribute(self, attr: str, value: int) -> PendingEdit:
        self._require_supported()
        if attr not in df.ATTRIBUTES:
            raise ValueError(f"unknown attribute: {attr}")
        e = PendingEdit("set_attribute", {"attr": attr, "value": int(value)},
                        f"{attr} = {value}")
        self._append_edit(e)
        return e

    def queue_set_skill(self, skill: str, value: int) -> PendingEdit:
        self._require_supported()
        if skill not in df.SKILLS:
            raise ValueError(f"unknown skill: {skill}")
        e = PendingEdit("set_skill", {"skill": skill, "value": int(value)},
                        f"{skill} = {value}")
        self._append_edit(e)
        return e

    def queue_set_karma(self, value: int) -> PendingEdit:
        self._require_supported()
        e = PendingEdit("set_unspent_karma", {"value": int(value)},
                        f"unspent karma = {value}")
        self._append_edit(e)
        return e

    def queue_set_nuyen(self, value: int) -> PendingEdit:
        self._require_supported()
        e = PendingEdit("set_nuyen", {"value": int(value)},
                        f"nuyen = {value}")
        self._append_edit(e)
        return e

    def queue_set_world_flag(self, name: str, kind: str, value: Any) -> PendingEdit:
        self._require_supported()
        if kind not in ("int", "bool", "float", "string"):
            raise ValueError(f"unsupported world-flag kind: {kind}")
        e = PendingEdit(
            "set_world_flag",
            {"name": name, "kind": kind, "value": value},
            f"flag {name} → ({kind}) {value}",
        )
        self._append_edit(e)
        return e

    def undo(self) -> PendingEdit | None:
        if not self._edits:
            return None
        return self._edits.pop()

    def clear(self) -> None:
        self._edits.clear()

    # ----- edit coalescing ----- #

    def _append_edit(self, e: PendingEdit) -> None:
        """Append `e` to the queue, then coalesce: drop any earlier edit that
        targets the same field (the new one supersedes it), and if the
        resulting edit's intended value matches the original on disk, drop
        the edit entirely. Net effect: editing a value and then reverting it
        leaves zero pending edits."""
        # 1) remove prior edits with the same field-target key
        new_key = _edit_target_key(e)
        if new_key is not None:
            self._edits = [
                prior for prior in self._edits
                if _edit_target_key(prior) != new_key
            ]
        # 2) drop self if no-op vs. the original .sav state
        original_top = self._original_top()
        if _edit_is_noop(e, original_top):
            return
        self._edits.append(e)

    def _original_top(self) -> list[Field]:
        """Cached parse of the original .sav bytes. Re-parsed on first call;
        subsequent edits compare against this, not against the live edited
        state."""
        if self._cached_original_top is None:
            self._cached_original_top = parse_toplevel(self._files[0].original_bytes)
        return self._cached_original_top

    # ----- diff / commit ----- #

    def diff_summary(self) -> list[str]:
        """One human-readable line per file that will change."""
        if not self._edits:
            return []
        out: list[str] = []
        for snap, new_bytes in zip(self._files, self._render_all()):
            if new_bytes != snap.original_bytes:
                d = len(new_bytes) - len(snap.original_bytes)
                out.append(
                    f"{snap.path.name}: {len(snap.original_bytes):,} → "
                    f"{len(new_bytes):,} bytes ({d:+,})"
                )
        return out

    def edit_descriptions(self) -> list[str]:
        return [e.description for e in self._edits]

    def commit(self, *, backup: bool = True) -> list[str]:
        """Apply pending edits, write files atomically with .bak backups.
        Returns a list of paths actually written."""
        if not self._edits:
            return []
        new_blobs = self._render_all()
        written: list[str] = []
        for snap, blob in zip(self._files, new_blobs):
            if blob == snap.original_bytes:
                continue
            if backup:
                backup_file(snap.path)
            atomic_write_bytes(snap.path, blob)
            snap.original_bytes = blob
            written.append(str(snap.path))
        self._edits.clear()
        # The on-disk state has rolled forward — the previous "original" tree
        # is stale. Next no-op check will re-parse the new baseline.
        self._cached_original_top = None
        return written

    # ----- internals ----- #

    def _require_supported(self) -> None:
        if not self.supported:
            raise UnsupportedGame(
                f"editing is not yet implemented for game={self.game!r}"
            )

    def _character_view(self, sav_top: list[Field]) -> CharacterView | None:
        sheet = df.CharacterSheet.from_top(sav_top)
        if sheet is None:
            return None
        return CharacterView(
            name=sheet.name,
            prefab=sheet.prefab,
            archetype=sheet.archetype,
            portrait_code=sheet.portrait_code,
            karma=sheet.karma,
            unspent_karma=sheet.unspent_karma,
            nuyen=df.read_nuyen(sav_top),
            attributes=dict(sheet.attributes),
            skills=dict(sheet.skills),
            etiquettes=dict(sheet.etiquettes),
            snapshot_count=sheet.snapshot_count,
        )

    def _current_sav_tree(self) -> list[Field]:
        """Re-parse the .sav (which is always _files[0]) and apply edits."""
        data = self._render_one(self._files[0])
        return parse_toplevel(data)

    def _render_one(self, snap: _FileSnapshot) -> bytes:
        if not self._edits:
            return snap.original_bytes
        top = parse_toplevel(snap.original_bytes)
        for e in self._edits:
            self._apply_edit(top, e)
        return serialize_message(top)

    def _render_all(self) -> list[bytes]:
        return [self._render_one(snap) for snap in self._files]

    @staticmethod
    def _apply_edit(top: list[Field], e: PendingEdit) -> None:
        a = e.args
        if e.op == "set_etiquette":
            df.set_etiquette(top, a["etiquette"])
        elif e.op == "add_etiquette":
            df.add_etiquette(top, a["etiquette"])
        elif e.op == "remove_etiquette":
            df.remove_etiquette(top, a["etiquette"])
        elif e.op == "set_attribute":
            df.set_attribute(top, a["attr"], a["value"])
        elif e.op == "set_skill":
            df.set_skill(top, a["skill"], a["value"])
        elif e.op == "set_unspent_karma":
            df.set_unspent_karma(top, a["value"])
        elif e.op == "set_nuyen":
            df.set_nuyen(top, a["value"])
        elif e.op == "set_world_flag":
            _apply_set_world_flag(top, a["name"], a["kind"], a["value"])
        else:
            raise ValueError(f"unknown op: {e.op}")

    # ----- Dragonfall enum decoding ----- #

    @staticmethod
    def _dragonfall_scope_name(scope: int | None) -> str | None:
        if scope is None:
            return None
        # Best-effort: load the Dragonfall TsVariableScope enum from the
        # schema bundle if available. Lazy import to avoid hard dependency
        # on the schemas dir at module load.
        try:
            from .schema import load_for_game
            sch = load_for_game("dragonfall")
            members = sch.enum("TsVariableScope") or {}
            for name, val in members.items():
                if val == scope:
                    return name
        except Exception:
            pass
        return None


# --------------------------------------------------------------------------- #
# World-flag edit helper                                                      #
# --------------------------------------------------------------------------- #

def _apply_set_world_flag(top: list[Field], name: str, kind: str, value: Any) -> None:
    """Set the world flag `name` to `value` (interpreted as `kind`).

    A Dragonfall .sav has one SaveStoryBlock per autosave checkpoint and
    each block carries its own variable_data section. The "current" game
    state comes from the LAST block; earlier ones are historical snapshots
    the game can roll back to. Editing every block at once rewrites
    autosave history and at least one user-observed mission-script branch
    appears to mis-fire when that happens.

    We therefore touch only the latest block. The display layer already
    reads the latest snapshot for the same reason (see
    domain.dragonfall.primary_player_snapshot)."""
    import struct
    from .protobuf_engine import WIRE_LEN, WIRE_VARINT

    type_tag_for_kind = {"int": 1, "bool": 2, "float": 3, "string": 4}
    if kind not in type_tag_for_kind:
        raise ValueError(f"unsupported world-flag kind: {kind}")
    target_tag = type_tag_for_kind[kind]

    def _encode_variant_value(tv: Field) -> None:
        """Mutate `tv` (a TsVariant Field) to hold the new (kind, value)."""
        assert tv.children is not None
        # Drop any existing concrete-value field (tags 1..6); we always set
        # exactly one. Keep order stable otherwise.
        tv.children[:] = [c for c in tv.children if c.tag not in {1, 2, 3, 4, 5, 6}]
        if kind == "int":
            new_f = Field(tag=1, wire=WIRE_VARINT, value=int(value), dirty=True)
        elif kind == "bool":
            new_f = Field(tag=2, wire=WIRE_VARINT, value=1 if value else 0, dirty=True)
        elif kind == "float":
            new_f = Field(tag=3, wire=5, value=struct.pack("<f", float(value)), dirty=True)
        elif kind == "string":
            new_f = Field(tag=4, wire=WIRE_LEN, value=str(value).encode("utf-8"), dirty=True)
        tv.children.insert(0, new_f)
        tv.mark_dirty()

    # Find the most recent SaveStoryBlock — that's the live game state.
    last_block: Field | None = None
    for f in top:
        if f.tag == 7 and f.wire == 2 and f.children is not None:
            last_block = f
    if last_block is None or last_block.children is None:
        return

    matches: list[Field] = []
    for fl in df.iter_world_flags([last_block]):
        if fl.name == name:
            matches.append(fl.value_field)
    for tv in matches:
        _encode_variant_value(tv)


# --------------------------------------------------------------------------- #
# Save-folder scanning across all three games                                 #
# --------------------------------------------------------------------------- #

# macOS default save folders for each game. The scanner walks these
# recursively so we don't need to know the exact "Saves" / "SavedGames" /
# "Save Games" subdirectory each installer chose.
DEFAULT_SAVE_FOLDERS: dict[str, list[str]] = {
    "dragonfall": [
        "~/Library/Application Support/Harebrained Schemes/Shadowrun Dragonfall",
        "~/Library/Application Support/Shadowrun Dragonfall Director's Cut",
    ],
    "returns": [
        "~/Library/Application Support/Harebrained Schemes/Shadowrun Returns",
        "~/Library/Application Support/Shadowrun Returns",
    ],
    "hongkong": [
        "~/Library/Application Support/Harebrained Schemes/Shadowrun Hong Kong",
        "~/Library/Application Support/Shadowrun Hong Kong",
    ],
}


def discover_save_folders() -> dict[str, list[str]]:
    """Return {game_id: [existing_folder_paths]} for each game."""
    out: dict[str, list[str]] = {}
    for game, candidates in DEFAULT_SAVE_FOLDERS.items():
        present = [p for p in (Path(c).expanduser() for c in candidates) if p.is_dir()]
        if present:
            out[game] = [str(p) for p in present]
    return out


def discover_diagnostics() -> dict[str, Any]:
    """Verbose discovery info for debugging when the GUI says 'no saves found'
    but the user knows the path exists. Returns HOME + per-candidate existence
    + the recursive scan count, so we can tell where the gap is."""
    import os as _os
    info: dict[str, Any] = {
        "home": _os.environ.get("HOME", ""),
        "cwd": _os.getcwd(),
        "candidates": [],
    }
    for game, candidates in DEFAULT_SAVE_FOLDERS.items():
        for raw in candidates:
            expanded = Path(raw).expanduser()
            entry: dict[str, Any] = {
                "game": game,
                "raw": raw,
                "expanded": str(expanded),
                "exists": expanded.exists(),
                "is_dir": expanded.is_dir(),
                "sav_count": 0,
            }
            if entry["is_dir"]:
                try:
                    entry["sav_count"] = sum(
                        1 for p in expanded.rglob("*.sav") if p.is_file()
                    )
                except (PermissionError, OSError) as e:
                    entry["error"] = str(e)
            info["candidates"].append(entry)
    return info


def _summarize_one(slot: SaveSlot) -> SaveSummary | None:
    """Summarize one slot, dropping unreadable saves from the picker."""
    try:
        return SaveSession(slot).summary()
    except Exception as e:
        import sys as _sys
        print(f"[bridge] skipping unreadable save {slot.sav_path}: {e}", file=_sys.stderr)
        return None


def scan_all_saves(folders: list[Path] | None = None) -> list[SaveSummary]:
    """Scan one or more folders (or, if None, the default macOS folders for
    all three games) and return a flat list of SaveSummary.

    Runs sequentially. An earlier version parallelized this across a
    ProcessPoolExecutor for ~5x speedup on large corpuses, but the pool's
    worker processes shared the bridge's stdout fd. Between Python process
    spawn and the worker's initializer running there's a brief window where
    arbitrary import-time output could leak into the JSON-RPC stream and
    corrupt in-flight responses. The corruption was confirmed in user logs;
    the speedup wasn't worth the reliability cost. Future optimization:
    use a disk-backed cache keyed by (path, mtime, size) so unchanged saves
    skip parsing entirely on subsequent launches.
    """
    targets: list[Path]
    if folders is None:
        targets = []
        for game, candidates in DEFAULT_SAVE_FOLDERS.items():
            for c in candidates:
                p = Path(c).expanduser()
                if p.is_dir():
                    targets.append(p)
    else:
        targets = list(folders)

    slots: list[SaveSlot] = []
    for folder in targets:
        try:
            slots.extend(scan_folder(folder))
        except (FileNotFoundError, NotADirectoryError):
            continue
    if not slots:
        return []

    return [s for s in (_summarize_one(slot) for slot in slots) if s is not None]


# --------------------------------------------------------------------------- #
# JSON helpers (for the bridge)                                               #
# --------------------------------------------------------------------------- #

def summary_to_dict(s: SaveSummary) -> dict[str, Any]:
    return asdict(s)


def character_to_dict(c: CharacterView | None) -> dict[str, Any] | None:
    return None if c is None else asdict(c)


def flags_to_list(flags: list[WorldFlagView]) -> list[dict[str, Any]]:
    return [asdict(f) for f in flags]


def edits_to_list(edits: list[PendingEdit]) -> list[dict[str, Any]]:
    return [asdict(e) for e in edits]
