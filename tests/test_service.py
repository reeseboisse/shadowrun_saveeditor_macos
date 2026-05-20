"""Tests for the high-level service layer (the GUI's API surface).

These exercise the same code paths the SwiftUI app will use, just over
direct Python calls instead of JSON-RPC.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from shadowrun_editor.service import (
    SaveSession,
    UnsupportedGame,
    scan_all_saves,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
DF_SLOT = REPO_ROOT / "reference" / "saves" / "dragonfall"
RT_SLOT = REPO_ROOT / "reference" / "saves" / "returns"
HK_SLOT = REPO_ROOT / "reference" / "saves" / "hongkong"


@pytest.fixture
def df_session(tmp_path: Path) -> SaveSession:
    """Copy the Dragonfall save into a temp folder so we can mutate it."""
    dst = tmp_path / "df"
    shutil.copytree(DF_SLOT, dst, ignore=shutil.ignore_patterns(".*"))
    return SaveSession.open(dst)


def test_summary_dragonfall(df_session: SaveSession) -> None:
    s = df_session.summary()
    assert s.game == "dragonfall"
    assert s.supported is True
    assert s.char_name == "Cooma"
    assert s.display_name and "Dragonfall" in s.display_name


def test_summary_returns_is_recognized_and_supported(tmp_path: Path) -> None:
    dst = tmp_path / "rt"
    shutil.copytree(RT_SLOT, dst, ignore=shutil.ignore_patterns(".*"))
    sess = SaveSession.open(dst)
    s = sess.summary()
    assert s.game == "returns"
    assert s.supported is True


def test_summary_hongkong_is_recognized_but_unsupported(tmp_path: Path) -> None:
    dst = tmp_path / "hk"
    shutil.copytree(HK_SLOT, dst, ignore=shutil.ignore_patterns(".*"))
    sess = SaveSession.open(dst)
    s = sess.summary()
    assert s.game == "hongkong"
    assert s.supported is False


def test_character_view_has_etiquette(df_session: SaveSession) -> None:
    c = df_session.character()
    assert c is not None
    assert c.name == "Cooma"
    assert "security" in c.etiquettes
    assert c.etiquettes["security"] == 1
    assert c.attributes.get("magic") == 5


def test_queue_and_undo(df_session: SaveSession) -> None:
    df_session.queue_set_etiquette("academic")
    df_session.queue_set_karma(50000)
    assert len(df_session.pending_edits) == 2
    df_session.undo()
    assert len(df_session.pending_edits) == 1
    assert df_session.pending_edits[0].op == "set_etiquette"


def test_diff_is_empty_when_no_edits(df_session: SaveSession) -> None:
    assert df_session.diff_summary() == []
    assert df_session.has_changes() is False


def test_pending_edits_reflected_in_character_view(df_session: SaveSession) -> None:
    df_session.queue_set_karma(100_000)
    c = df_session.character()
    assert c is not None
    assert c.unspent_karma == 100_000


def test_commit_writes_files_and_creates_backups(df_session: SaveSession) -> None:
    df_session.queue_set_etiquette("academic")
    df_session.queue_set_karma(100_000)
    written = df_session.commit()
    assert written, "expected at least one file to change"
    # Backups exist
    for p in [df_session.slot.sav_path, *df_session.slot.srt_paths]:
        bak = p.with_name(p.name + ".bak")
        assert bak.exists(), f"missing backup for {p.name}"
    # Reload and verify the changes persisted
    sess2 = SaveSession.open(df_session.slot.sav_path)
    c = sess2.character()
    assert c is not None
    assert c.unspent_karma == 100_000
    assert "academic" in c.etiquettes


def test_unsupported_game_raises_on_edit(tmp_path: Path) -> None:
    dst = tmp_path / "hk"
    shutil.copytree(HK_SLOT, dst, ignore=shutil.ignore_patterns(".*"))
    sess = SaveSession.open(dst)
    with pytest.raises(UnsupportedGame):
        sess.queue_set_etiquette("academic")


# --------------------------------------------------------------------------- #
# Returns coverage                                                            #
# --------------------------------------------------------------------------- #

@pytest.fixture
def rt_session(tmp_path: Path) -> SaveSession:
    dst = tmp_path / "rt"
    shutil.copytree(RT_SLOT, dst, ignore=shutil.ignore_patterns(".*"))
    return SaveSession.open(dst)


def test_returns_session_opens_and_round_trips(rt_session: SaveSession) -> None:
    """With no edits queued, commit is a no-op and bytes are unchanged."""
    assert rt_session.game == "returns"
    assert rt_session.supported is True
    # No edits → commit writes nothing.
    assert rt_session.commit() == []
    # And the raw bytes parse + reserialize identically (engine round-trip).
    from shadowrun_editor.protobuf_engine import parse_toplevel, serialize_message
    sav = rt_session.slot.sav_path.read_bytes()
    assert serialize_message(parse_toplevel(sav)) == sav


def test_returns_character_view_has_available_lists(rt_session: SaveSession) -> None:
    c = rt_session.character()
    assert c is not None
    # HK-only etiquettes must NOT appear in the Returns available set.
    assert "paranormal" not in c.available_etiquettes
    assert "infected" not in c.available_etiquettes
    # Returns drops three skills compared to the full HK set.
    assert "chi_casting" not in c.available_skills
    assert "drone_combat" not in c.available_skills
    assert "drain_resistance" not in c.available_skills
    # Alice Fund is Dragonfall-only.
    assert c.alice_fund is None


def test_returns_set_etiquette_round_trips(rt_session: SaveSession) -> None:
    rt_session.queue_set_etiquette("academic")
    written = rt_session.commit()
    assert written, "expected at least one file to change"
    sess2 = SaveSession.open(rt_session.slot.sav_path)
    c = sess2.character()
    assert c is not None
    assert "academic" in c.etiquettes


def test_returns_rejects_hk_only_etiquette(rt_session: SaveSession) -> None:
    with pytest.raises(ValueError):
        rt_session.queue_add_etiquette("paranormal")
    with pytest.raises(ValueError):
        rt_session.queue_add_etiquette("infected")


def test_returns_rejects_hk_only_skill(rt_session: SaveSession) -> None:
    with pytest.raises(ValueError):
        rt_session.queue_set_skill("chi_casting", 3)
    with pytest.raises(ValueError):
        rt_session.queue_set_skill("drone_combat", 3)


def test_returns_has_no_alice_fund(rt_session: SaveSession) -> None:
    c = rt_session.character()
    assert c is not None
    assert c.alice_fund is None


def test_world_flags_listed_with_value_kinds(df_session: SaveSession) -> None:
    flags = df_session.world_flags()
    # Sanity: Dragonfall always emits some non-trivial number of flags
    assert len(flags) > 100
    kinds = {f.kind for f in flags}
    # We expect bool / int / string in the wild (every save has some of each)
    # ("empty" is allowed too — protobuf-net omits default values)
    assert kinds.intersection({"int", "bool", "string", "empty"})


def test_world_flag_edit_round_trips(df_session: SaveSession) -> None:
    flags = df_session.world_flags()
    # Pick the first int flag we can find
    target = next(f for f in flags if f.kind == "int")
    df_session.queue_set_world_flag(target.name, "int", 42)
    after = {f.name: f for f in df_session.world_flags()}
    assert after[target.name].value == 42


def test_scan_all_saves_with_explicit_folders(tmp_path: Path) -> None:
    # Stage all three games into a flat scan target
    parent = tmp_path / "root"
    for name, src in (("df", DF_SLOT), ("rt", RT_SLOT), ("hk", HK_SLOT)):
        shutil.copytree(src, parent / name, ignore=shutil.ignore_patterns(".*"))
    out = scan_all_saves([parent / "df", parent / "rt", parent / "hk"])
    games = {s.game for s in out}
    assert games == {"dragonfall", "returns", "hongkong"}
    assert {s.supported for s in out} == {True, False}
