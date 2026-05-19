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


def detect_game(data: bytes) -> str:
    """Sniff the game id from the bytes of a .sav file."""
    head = data[:65536]
    for game, markers in _GAME_MARKERS:
        for marker in markers:
            if marker in head:
                return game
    return "unknown"


def detect_game_from_file(path: str | Path) -> str:
    return detect_game(Path(path).read_bytes())


def scan_folder(folder: str | Path) -> list[SaveSlot]:
    """Walk a folder of save files, returning one SaveSlot per UUID prefix.

    Accepts both:
      * a folder containing many .sav files (the game's Saves directory)
      * a single save slot whose .sav and .srt files all share a UUID
    """
    folder = Path(folder)
    if not folder.is_dir():
        raise NotADirectoryError(folder)

    slots: dict[str, SaveSlot] = {}
    for p in sorted(folder.iterdir()):
        if not p.is_file():
            continue
        m = UUID_RE.match(p.name)
        if not m:
            continue
        uid = m.group(1).lower()
        ext = p.suffix.lower()
        slot = slots.get(uid)
        if slot is None:
            slot = SaveSlot(uuid=uid, folder=folder, sav_path=Path())  # placeholder
            slots[uid] = slot
        if ext == ".sav":
            slot.sav_path = p
        elif ext == ".srt":
            slot.srt_paths.append(p)
        elif ext == ".png":
            slot.thumbnail_path = p
        elif ext == ".metadata":
            slot.metadata_path = p

    # Drop incomplete slots (no .sav)
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
