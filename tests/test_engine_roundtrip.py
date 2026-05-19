"""Round-trip integrity tests.

For every save file in the reference corpus, parse → serialize must equal
the original bytes exactly. This is the fundamental contract before any
edit feature can be trusted.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from shadowrun_editor.protobuf_engine import (
    parse_toplevel,
    serialize_message,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
SAVES_DIR = REPO_ROOT / "reference" / "saves"


def _all_save_files() -> list[Path]:
    paths: list[Path] = []
    for game_dir in sorted(SAVES_DIR.iterdir()):
        if not game_dir.is_dir():
            continue
        for p in sorted(game_dir.iterdir()):
            if p.suffix in (".sav", ".srt"):
                paths.append(p)
    return paths


@pytest.mark.parametrize("path", _all_save_files(), ids=lambda p: f"{p.parent.name}/{p.name}")
def test_roundtrip_bytes_identical(path: Path) -> None:
    data = path.read_bytes()
    tree = parse_toplevel(data)
    out = serialize_message(tree)
    assert out == data, (
        f"round-trip mismatch for {path}: "
        f"in={len(data)} bytes, out={len(out)} bytes"
    )
