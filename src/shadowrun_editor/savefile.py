"""File I/O for Shadowrun save folders.

A save *slot* is a directory of files sharing a UUID prefix:
    <uuid>.sav                              - master state
    <uuid>-<SceneName>-<sceneUuid>.srt      - one per visited scene
    <uuid>.png                              - thumbnail (untouched by editor)
    <uuid>.metadata                         - optional metadata blob

The editor groups these together so an edit that mutates the player
snapshot can be propagated to every .srt in the slot.
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path


UUID_RE = re.compile(r"^([0-9a-fA-F]{32})")

# Substring markers used to identify which game a save came from.
# Multiple markers per game because campaign DLCs use different titles.
_GAME_MARKERS: list[tuple[str, list[bytes]]] = [
    ("dragonfall", [
        b"Shadowrun: Dragonfall - Director's Cut",
        b"Dragonfall",
    ]),
    ("hongkong", [
        b"Shadowrun: Hong Kong",
        b"Shadows of Hong Kong",
        b"Hong Kong",
    ]),
    ("returns", [
        b"Dead Man's Switch",
        b"Shadowrun Returns",
    ]),
]

# Folder-name markers used as a fallback when the in-bytes title doesn't
# match (UGC / user-made campaigns embed the campaign's own title string, so
# the byte scan misses; but the save still lives under the same per-engine
# install root: each game keeps its own Saves folder). The fragments here
# are matched case-insensitively against any component of the file's full
# path. Order matters — "Hong Kong" must be checked before "Dragonfall" /
# "Returns" so a path that somehow contains multiple still picks the most
# specific engine root.
_GAME_PATH_MARKERS: list[tuple[str, list[str]]] = [
    ("hongkong",   ["shadowrun hong kong", "hong kong"]),
    ("dragonfall", ["shadowrun dragonfall", "dragonfall"]),
    ("returns",    ["shadowrun returns"]),
]


@dataclass
class SaveSlot:
    """One save slot — a directory of files sharing a UUID prefix."""
    uuid: str
    folder: Path
    sav_path: Path
    srt_paths: list[Path] = field(default_factory=list)
    thumbnail_path: Path | None = None
    metadata_path: Path | None = None

    def all_protobuf_files(self) -> list[Path]:
        """Every file in the slot whose bytes are protobuf and need editing."""
        return [self.sav_path, *self.srt_paths]


def detect_game(data: bytes, path: str | Path | None = None) -> str:
    """Identify which engine wrote this save.

    Primary signal is the title string the engine embedded in the file
    (works for every stock campaign). When that fails — overwhelmingly the
    UGC / user-made-campaign case, where the title field carries the
    campaign's own name — fall back to the file's path: each game keeps its
    own Saves folder, so a save under ``…/Shadowrun Hong Kong/Saves/…`` is a
    Hong Kong engine save regardless of which campaign produced it. Returns
    ``"unknown"`` only when both signals miss."""
    head = data[:65536]
    for game, markers in _GAME_MARKERS:
        for marker in markers:
            if marker in head:
                return game
    if path is not None:
        return detect_game_from_path(path)
    return "unknown"


def detect_game_from_path(path: str | Path) -> str:
    """Match the file's path components against the per-engine save-folder
    markers. Case-insensitive (macOS save folders are typically case-
    insensitive but we don't want to depend on that)."""
    parts = [p.lower() for p in Path(path).expanduser().parts]
    for game, fragments in _GAME_PATH_MARKERS:
        for frag in fragments:
            if any(frag in part for part in parts):
                return game
    return "unknown"


def detect_game_from_file(path: str | Path) -> str:
    return detect_game(Path(path).read_bytes(), path=path)


def scan_folder(folder: str | Path, *, recursive: bool = True, max_depth: int = 4) -> list[SaveSlot]:
    """Walk a folder of save files, returning one SaveSlot per UUID prefix.

    By default the walk is recursive (capped at `max_depth` levels) so the
    user can point the editor at a high-level folder like
    `~/Library/Application Support/Harebrained Schemes/Shadowrun Dragonfall/`
    without needing to know which sub-directory the game writes saves to.

    Save slot files are grouped by UUID; if a slot's files happen to live
    in two different sub-directories they're still grouped together (we
    keep the .sav's parent directory as the slot's `folder`).
    """
    root = Path(folder)
    if not root.is_dir():
        raise NotADirectoryError(root)

    slots: dict[str, SaveSlot] = {}

    def _ingest(p: Path) -> None:
        if not p.is_file():
            return
        m = UUID_RE.match(p.name)
        if not m:
            return
        uid = m.group(1).lower()
        ext = p.suffix.lower()
        slot = slots.get(uid)
        if slot is None:
            slot = SaveSlot(uuid=uid, folder=p.parent, sav_path=Path())
            slots[uid] = slot
        if ext == ".sav":
            slot.sav_path = p
            slot.folder = p.parent  # canonical folder is where the .sav lives
        elif ext == ".srt":
            slot.srt_paths.append(p)
        elif ext == ".png":
            slot.thumbnail_path = p
        elif ext == ".metadata":
            slot.metadata_path = p

    if not recursive:
        for p in sorted(root.iterdir()):
            _ingest(p)
    else:
        # Bounded-depth walk: skip hidden dirs and the game's own backup
        # snapshot folders. The Dragonfall installer (and at least one HK
        # build) keeps a parallel Save Games tree under BACKUP/Saves/ that
        # holds older copies of the same UUIDs. Since slots are deduped by
        # UUID, a backup copy can otherwise win the race and shadow the
        # live save — the user picker would then show e.g. a stale
        # 2026-05-13 copy of a save the game itself last wrote 2026-05-24.
        # These folders are the game's mechanism, not anything the editor
        # should touch.
        skip_dir_names = {"backup", "backups"}
        root_depth = len(root.parts)
        for dirpath, dirnames, filenames in os.walk(root):
            depth = len(Path(dirpath).parts) - root_depth
            if depth > max_depth:
                dirnames[:] = []
                continue
            # In-place prune of dirs we won't descend
            dirnames[:] = [
                d for d in dirnames
                if not d.startswith(".") and d.lower() not in skip_dir_names
            ]
            for fn in filenames:
                _ingest(Path(dirpath) / fn)

    # Drop incomplete slots (no .sav file)
    return [s for s in slots.values() if s.sav_path != Path()]


def backup_file(path: str | Path, suffix: str = ".bak") -> Path:
    """Create <path>.bak if it doesn't already exist. Idempotent."""
    src = Path(path)
    bak = src.with_name(src.name + suffix)
    if not bak.exists():
        shutil.copy2(src, bak)
    return bak


def atomic_write_bytes(path: str | Path, data: bytes) -> None:
    """Write data to path via a temp file in the same directory then rename."""
    dst = Path(path)
    tmp = dst.with_name(dst.name + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, dst)
