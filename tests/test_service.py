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


def test_summary_hongkong_is_recognized_and_supported(tmp_path: Path) -> None:
    dst = tmp_path / "hk"
    shutil.copytree(HK_SLOT, dst, ignore=shutil.ignore_patterns(".*"))
    sess = SaveSession.open(dst)
    s = sess.summary()
    assert s.game == "hongkong"
    assert s.supported is True


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


# All three trilogy games are now supported end-to-end, so there's no
# fixture left that should raise UnsupportedGame on edit. The exception
# type itself is kept around as defense-in-depth in case a future build
# loads a save tagged with an unknown game id.


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


# --------------------------------------------------------------------------- #
# Hong Kong coverage                                                          #
# --------------------------------------------------------------------------- #

@pytest.fixture
def hk_session(tmp_path: Path) -> SaveSession:
    dst = tmp_path / "hk"
    shutil.copytree(HK_SLOT, dst, ignore=shutil.ignore_patterns(".*"))
    return SaveSession.open(dst)


def test_hongkong_session_opens_and_round_trips(hk_session: SaveSession) -> None:
    """With no edits queued, commit is a no-op and bytes are unchanged."""
    assert hk_session.game == "hongkong"
    assert hk_session.supported is True
    assert hk_session.commit() == []
    from shadowrun_editor.protobuf_engine import parse_toplevel, serialize_message
    sav = hk_session.slot.sav_path.read_bytes()
    assert serialize_message(parse_toplevel(sav)) == sav


def test_hongkong_character_view_has_hk_specific_lists(hk_session: SaveSession) -> None:
    c = hk_session.character()
    assert c is not None
    # HK is the only game whose scripts actually trigger paranormal/infected.
    assert "paranormal" in c.available_etiquettes
    assert "infected" in c.available_etiquettes
    # HK is the only game whose scripts use chi_casting.
    assert "chi_casting" in c.available_skills
    # Alice Fund is Dragonfall-only.
    assert c.alice_fund is None


def test_hongkong_accepts_paranormal_etiquette_and_round_trips(hk_session: SaveSession) -> None:
    hk_session.queue_set_etiquette("paranormal")
    written = hk_session.commit()
    assert written, "expected at least one file to change"
    sess2 = SaveSession.open(hk_session.slot.sav_path)
    c = sess2.character()
    assert c is not None
    assert "paranormal" in c.etiquettes


def test_hongkong_accepts_chi_casting_skill(hk_session: SaveSession) -> None:
    # Should NOT raise — chi_casting is in HK's available set.
    hk_session.queue_set_skill("chi_casting", 3)
    assert any(e.op == "set_skill" for e in hk_session.pending_edits)


def test_hongkong_has_no_alice_fund(hk_session: SaveSession) -> None:
    c = hk_session.character()
    assert c is not None
    assert c.alice_fund is None


def test_hongkong_exposes_cyberware_affinity(hk_session: SaveSession) -> None:
    c = hk_session.character()
    assert c is not None
    assert "cyberware_affinity" in c.available_skills


def test_hongkong_cyberware_affinity_round_trips(hk_session: SaveSession) -> None:
    hk_session.queue_set_skill("cyberware_affinity", 4)
    hk_session.commit()
    sess2 = SaveSession.open(hk_session.slot.sav_path)
    c = sess2.character()
    assert c is not None
    assert c.skills.get("cyberware_affinity") == 4


# --------------------------------------------------------------------------- #
# Non-player skills + HK-only cyberware_affinity gating (cross-game)          #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("game,slot", [
    ("dragonfall", DF_SLOT), ("returns", RT_SLOT), ("hongkong", HK_SLOT),
])
def test_non_player_skills_never_editable(game, slot, tmp_path: Path) -> None:
    dst = tmp_path / game
    shutil.copytree(slot, dst, ignore=shutil.ignore_patterns(".*"))
    sess = SaveSession.open(dst)
    c = sess.character()
    assert c is not None
    for skill in ("athletics", "negotiation", "stealth"):
        assert skill not in c.available_skills, f"{skill} leaked into {game}"
        with pytest.raises(ValueError):
            sess.queue_set_skill(skill, 3)


@pytest.mark.parametrize("game,slot", [
    ("dragonfall", DF_SLOT), ("returns", RT_SLOT), ("hongkong", HK_SLOT),
])
def test_derived_attributes_never_editable(game, slot, tmp_path: Path) -> None:
    dst = tmp_path / game
    shutil.copytree(slot, dst, ignore=shutil.ignore_patterns(".*"))
    sess = SaveSession.open(dst)
    c = sess.character()
    assert c is not None
    # reaction/essence are engine-derived — not offered for editing.
    for attr in ("reaction", "essence"):
        assert attr not in c.available_attributes, f"{attr} leaked into {game}"
        with pytest.raises(ValueError):
            sess.queue_set_attribute(attr, 5)
    # The seven real karma attributes are all present and editable.
    for attr in ("body", "quickness", "strength", "charisma",
                 "intelligence", "willpower", "magic"):
        assert attr in c.available_attributes, f"{attr} missing from {game}"


@pytest.mark.parametrize("game,slot,expected", [
    ("dragonfall", DF_SLOT, False),
    ("returns", RT_SLOT, False),
    ("hongkong", HK_SLOT, True),
])
def test_cyberware_affinity_is_hongkong_only(game, slot, expected, tmp_path: Path) -> None:
    dst = tmp_path / game
    shutil.copytree(slot, dst, ignore=shutil.ignore_patterns(".*"))
    sess = SaveSession.open(dst)
    c = sess.character()
    assert c is not None
    assert ("cyberware_affinity" in c.available_skills) is expected
    if not expected:
        with pytest.raises(ValueError):
            sess.queue_set_skill("cyberware_affinity", 3)


# --------------------------------------------------------------------------- #
# Inventory editor (game-agnostic; exercised against HK)                      #
# --------------------------------------------------------------------------- #

def test_inventory_view_populated_and_decorated(hk_session: SaveSession) -> None:
    c = hk_session.character()
    assert c is not None
    assert c.inventory, "expected a non-empty inventory"
    by_prefab = {it.prefab: it for it in c.inventory}
    # Stacks are counted (the HK reference save carries two HealthPack_med).
    assert by_prefab["HealthPack_med"].quantity == 2
    # Catalog metadata rides along.
    ar = by_prefab["AR 3 Colt M23"]
    assert ar.category == "weapon" and ar.subtype == "Assault Rifle"


def test_inventory_set_quantity_round_trips(hk_session: SaveSession) -> None:
    hk_session.queue_set_item_quantity("HealthPack_hi", 25)
    written = hk_session.commit()
    assert written, "expected at least one file to change"
    sess2 = SaveSession.open(hk_session.slot.sav_path)
    inv = {it.prefab: it.quantity for it in sess2.character().inventory}
    assert inv["HealthPack_hi"] == 25


def test_inventory_add_new_prefab_round_trips(hk_session: SaveSession) -> None:
    hk_session.queue_add_item("Spell Fireball 4", 2)
    hk_session.commit()
    sess2 = SaveSession.open(hk_session.slot.sav_path)
    inv = {it.prefab: it.quantity for it in sess2.character().inventory}
    assert inv.get("Spell Fireball 4") == 2


def test_inventory_remove_drops_item(hk_session: SaveSession) -> None:
    hk_session.queue_remove_item("DocWagonPlatinum")
    hk_session.commit()
    sess2 = SaveSession.open(hk_session.slot.sav_path)
    inv = {it.prefab: it.quantity for it in sess2.character().inventory}
    assert "DocWagonPlatinum" not in inv


def test_inventory_set_quantity_coalesces_and_noops(hk_session: SaveSession) -> None:
    # Two set_quantity ops on the same prefab → only the latest survives.
    hk_session.queue_set_item_quantity("HealthPack_hi", 10)
    hk_session.queue_set_item_quantity("HealthPack_hi", 25)
    assert len(hk_session.pending_edits) == 1
    assert hk_session.pending_edits[0].args["quantity"] == 25
    # Setting a prefab to the quantity it already has is a no-op (dropped).
    hk_session.clear()
    hk_session.queue_set_item_quantity("AR 3 Colt M23", 1)  # already exactly 1
    assert hk_session.pending_edits == []


def test_inventory_edit_propagates_to_srt(hk_session: SaveSession) -> None:
    hk_session.queue_set_item_quantity("HealthPack_hi", 9)
    written = hk_session.commit()
    # Both the .sav and its .srt scene cache should be rewritten so the game
    # can't restore the pre-edit loadout on scene re-entry (plan §10 note 2).
    assert any(w.endswith(".sav") for w in written)
    assert any(w.endswith(".srt") for w in written)


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
    # Every game is supported as of Phase 4; if a future build adds an
    # unrecognized game id this becomes a meaningful boundary check again.
    assert {s.supported for s in out} == {True}


# --------------------------------------------------------------------------- #
# Character template import / export                                          #
# --------------------------------------------------------------------------- #

@pytest.fixture
def hk_template_session(tmp_path: Path) -> SaveSession:
    dst = tmp_path / "hk"
    shutil.copytree(HK_SLOT, dst, ignore=shutil.ignore_patterns(".*"))
    return SaveSession.open(dst)


def test_export_character_shape(hk_template_session: SaveSession) -> None:
    from shadowrun_editor.service import CHARACTER_TEMPLATE_FORMAT
    tpl = hk_template_session.export_character()
    assert tpl["format"] == CHARACTER_TEMPLATE_FORMAT
    assert tpl["game"] == "hongkong"
    for key in ("attributes", "skills", "etiquettes", "inventory", "resources"):
        assert key in tpl
    # Derived attributes / non-player skills never appear in an export.
    assert "reaction" not in tpl["attributes"]
    assert "essence" not in tpl["attributes"]
    assert "stealth" not in tpl["skills"]


def test_export_then_import_same_save_is_noop(hk_template_session: SaveSession) -> None:
    tpl = hk_template_session.export_character()
    report = hk_template_session.import_character(tpl)
    assert report["applied"], "expected fields to be accepted"
    # Every value already matches, so the coalescer drops them all.
    assert hk_template_session.pending_edits == []
    assert hk_template_session.has_changes() is False


def test_import_mutated_template_round_trips(hk_template_session: SaveSession) -> None:
    tpl = hk_template_session.export_character()
    tpl["skills"]["ranged_combat"] = 9
    tpl["etiquettes"]["gang"] = 4
    tpl["resources"]["nuyen"] = 123456
    tpl["inventory"]["HealthPack_hi"] = 20
    hk_template_session.import_character(tpl)
    hk_template_session.commit()
    sess2 = SaveSession.open(hk_template_session.slot.sav_path)
    c = sess2.character()
    assert c.skills["ranged_combat"] == 9
    assert c.etiquettes["gang"] == 4
    assert c.nuyen == 123456
    assert {it.prefab: it.quantity for it in c.inventory}["HealthPack_hi"] == 20


def test_import_rejects_unknown_format(hk_template_session: SaveSession) -> None:
    with pytest.raises(ValueError):
        hk_template_session.import_character({"format": "bogus/9", "game": "hongkong"})


def test_import_cross_game_skips_incompatible_fields(tmp_path: Path) -> None:
    # A HK template carrying HK-only skills imported into Dragonfall: the
    # HK-only fields are skipped, the shared ones applied.
    from shadowrun_editor.service import CHARACTER_TEMPLATE_FORMAT
    dst = tmp_path / "df"
    shutil.copytree(DF_SLOT, dst, ignore=shutil.ignore_patterns(".*"))
    sess = SaveSession.open(dst)
    tpl = {
        "format": CHARACTER_TEMPLATE_FORMAT,
        "game": "hongkong",
        "attributes": {"body": 6, "reaction": 9},      # reaction not editable
        "skills": {"ranged_combat": 5, "chi_casting": 4, "cyberware_affinity": 2},
        "etiquettes": {"gang": 1, "paranormal": 2},     # paranormal HK-only
        "inventory": {},
    }
    report = sess.import_character(tpl)
    joined = " ".join(report["applied"])
    assert "skill ranged_combat" in joined and "attribute body" in joined
    skipped = " ".join(report["skipped"])
    for bad in ("chi_casting", "cyberware_affinity", "reaction", "paranormal"):
        assert bad in skipped
    # And the from-hongkong note is present.
    assert any("hongkong" in s for s in report["skipped"])


def test_set_etiquette_rating_round_trips(hk_template_session: SaveSession) -> None:
    hk_template_session.queue_set_etiquette_rating("gang", 5)
    hk_template_session.commit()
    sess2 = SaveSession.open(hk_template_session.slot.sav_path)
    assert sess2.character().etiquettes["gang"] == 5
