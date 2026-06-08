"""Game detection for stock and UGC saves.

Stock saves carry the campaign's title string in-bytes; detect_game finds
them via _GAME_MARKERS. User-made campaigns embed the campaign's own title,
so byte detection misses — but each game still keeps its own per-engine
Saves folder, so a path-based fallback picks the right engine.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from shadowrun_editor.savefile import (
    detect_game,
    detect_game_from_path,
    detect_game_from_file,
)
from shadowrun_editor.service import SaveSession

REPO_ROOT = Path(__file__).resolve().parents[1]
HK_SLOT = REPO_ROOT / "reference" / "saves" / "hongkong"


def test_detect_from_bytes_takes_precedence_over_path() -> None:
    # Stock HK title in the bytes; the path is a Dragonfall folder. Bytes win
    # (this guards against a UGC save accidentally getting mislabeled when
    # someone hand-moves it between game folders).
    bytes_with_title = b"\x12\x14Shadowrun: Hong Kong"
    assert detect_game(bytes_with_title, path="/x/Dragonfall/Saves/y.sav") == "hongkong"


def test_detect_path_fallback_when_bytes_anonymous() -> None:
    # Bytes with no recognized title -> path decides.
    anon = b"\x0a\x05hello"
    assert detect_game(anon, path="/Apps/Shadowrun Hong Kong/Saves/x.sav") == "hongkong"
    assert detect_game(anon, path="/Apps/Shadowrun Dragonfall/Saves/x.sav") == "dragonfall"
    assert detect_game(anon, path="/Apps/Shadowrun Returns/Saves/x.sav") == "returns"
    assert detect_game(anon) == "unknown"                       # no path, no markers
    assert detect_game(anon, path="/tmp/nowhere/x.sav") == "unknown"


def test_path_marker_is_case_insensitive_and_matches_any_component() -> None:
    assert detect_game_from_path("/Foo/SHADOWRUN HONG KONG/Saves/x.sav") == "hongkong"
    # Deeply nested under the engine root still matches.
    assert detect_game_from_path(
        "/Users/me/Library/Application Support/Harebrained Schemes/"
        "Shadowrun Dragonfall/Saves/abc.sav"
    ) == "dragonfall"


def test_detect_game_from_file_uses_path_fallback(tmp_path: Path) -> None:
    # A file whose bytes carry no marker should still detect via its folder.
    folder = tmp_path / "Shadowrun Returns" / "Saves"
    folder.mkdir(parents=True)
    f = folder / "abc.sav"
    f.write_bytes(b"\x0a\x05hello")
    assert detect_game_from_file(f) == "returns"


# --- UGC save integration: full SaveSession + per-game adapter ------------ #

# These markers must all be neutralized in the bytes for path detection to
# be the sole signal. Same-length ASCII replacements keep the protobuf
# length prefixes valid.
_TITLE_REPLACEMENTS = [
    (b"Shadowrun: Hong Kong", b"FoundationCampaignXX"),  # 20 chars each
    (b"Shadows of Hong Kong", b"FoundationCampaign22"),  # 20 chars each
    (b"Hong Kong",            b"FdnUgcCmp"),              # 9 chars each
]


def _scrub_title_markers(blob: bytes) -> bytes:
    out = blob
    for src, dst in _TITLE_REPLACEMENTS:
        assert len(src) == len(dst), f"{src!r} / {dst!r} length mismatch"
        out = out.replace(src, dst)
    return out


def test_ugc_save_under_hk_folder_is_recognized_as_hongkong(tmp_path: Path) -> None:
    # Stage the HK reference save under a path that LOOKS like a Hong Kong
    # install root, but scrub every title marker from the .sav so byte
    # detection misses. The session should still pick the HK adapter and
    # report supported=True via path fallback.
    folder = tmp_path / "Applications" / "Shadowrun Hong Kong" / "Saves"
    folder.mkdir(parents=True)
    for src in HK_SLOT.iterdir():
        if src.name.startswith("."):
            continue
        dst = folder / src.name
        if src.suffix == ".sav":
            dst.write_bytes(_scrub_title_markers(src.read_bytes()))
        else:
            shutil.copy2(src, dst)
    sess = SaveSession.open(folder)
    s = sess.summary()
    assert s.game == "hongkong"
    assert s.supported is True
    # Sanity: the adapter is wired up — a HK-only edit doesn't raise.
    sess.queue_set_etiquette("paranormal")
