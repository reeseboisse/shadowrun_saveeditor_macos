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

import concurrent.futures
import datetime as _dt
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable

from .domain import dragonfall as df
from .protobuf_engine import parse_toplevel, serialize_message, Field
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
        sav_top = self._current_sav_tree()
        # Dedupe to one row per name (latest value wins, same as the CLI)
        latest: dict[str, WorldFlagView] = {}
        for fl in df.iter_world_flags(sav_top):
            kind, value = fl.value()
            scope = self._dragonfall_scope_name(fl.scope)
            # Best-effort JSON-friendly value
            if isinstance(value, bytes):
                value = value.hex()
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
        self._edits.append(e)
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
        self._edits.append(e)
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
        self._edits.append(e)
        return e

    def queue_set_attribute(self, attr: str, value: int) -> PendingEdit:
        self._require_supported()
        if attr not in df.ATTRIBUTES:
            raise ValueError(f"unknown attribute: {attr}")
        e = PendingEdit("set_attribute", {"attr": attr, "value": int(value)},
                        f"{attr} = {value}")
        self._edits.append(e)
        return e

    def queue_set_skill(self, skill: str, value: int) -> PendingEdit:
        self._require_supported()
        if skill not in df.SKILLS:
            raise ValueError(f"unknown skill: {skill}")
        e = PendingEdit("set_skill", {"skill": skill, "value": int(value)},
                        f"{skill} = {value}")
        self._edits.append(e)
        return e

    def queue_set_karma(self, value: int) -> PendingEdit:
        self._require_supported()
        e = PendingEdit("set_unspent_karma", {"value": int(value)},
                        f"unspent karma = {value}")
        self._edits.append(e)
        return e

    def queue_set_nuyen(self, value: int) -> PendingEdit:
        self._require_supported()
        e = PendingEdit("set_nuyen", {"value": int(value)},
                        f"nuyen = {value}")
        self._edits.append(e)
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
        self._edits.append(e)
        return e

    def undo(self) -> PendingEdit | None:
        if not self._edits:
            return None
        return self._edits.pop()

    def clear(self) -> None:
        self._edits.clear()

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
    """Set the world flag `name` to `value` (interpreted as `kind`) in every
    SaveStoryBlock that defines it. Does not insert flags that don't exist."""
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

    for fl in df.iter_world_flags(top):
        if fl.name != name:
            continue
        _encode_variant_value(fl.value_field)


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
    """Pure function (picklable) for ProcessPoolExecutor workers."""
    try:
        return SaveSession(slot).summary()
    except Exception:
        return None


def scan_all_saves(folders: list[Path] | None = None) -> list[SaveSummary]:
    """Scan one or more folders (or, if None, the default macOS folders for
    all three games) and return a flat list of SaveSummary.

    Protobuf parsing dominates the cost (~150 ms per save for Hong Kong's
    1.2 MB .sav files). For users with full save corpuses (100+ slots
    across the trilogy) that's >10 s sequentially. Parallelize across CPU
    cores via ProcessPoolExecutor — each worker parses one slot. This
    drops the total to roughly (longest single parse + pool startup).
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

    # For tiny corpuses the process-pool spawn cost outweighs parallelism;
    # use the in-process path. For larger sets, parallelize.
    if len(slots) <= 4:
        return [s for s in (_summarize_one(slot) for slot in slots) if s is not None]

    workers = min(len(slots), os.cpu_count() or 4, 8)
    out: list[SaveSummary] = []
    with concurrent.futures.ProcessPoolExecutor(max_workers=workers) as pool:
        for summary in pool.map(_summarize_one, slots, chunksize=2):
            if summary is not None:
                out.append(summary)
    return out


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
