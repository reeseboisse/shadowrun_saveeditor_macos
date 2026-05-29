"""Catalog heuristics: classification + display-name tidying of prefab ids."""

from __future__ import annotations

from shadowrun_editor import catalog


def test_spell_and_foci_disambiguated() -> None:
    # "Spell Foci ..." must win over the generic "Spell ..." rule.
    assert catalog.categorize("Spell Foci Life Siphon 2")[0] == catalog.CATEGORY_FOCI
    assert catalog.categorize("Spell Manabolt 2")[0] == catalog.CATEGORY_SPELL


def test_weapon_class_detection() -> None:
    cat, sub = catalog.categorize("Sh 3 Enfield AS-7")
    assert cat == catalog.CATEGORY_WEAPON and sub == "Shotgun"
    cat, sub = catalog.categorize("AR 3 Colt M23")
    assert cat == catalog.CATEGORY_WEAPON and sub == "Assault Rifle"


def test_weapon_prefix_requires_force_digit() -> None:
    # A word that merely starts with a weapon-prefix letter pair but isn't
    # shaped like "<class> <digit> ..." must not be miscategorized.
    assert catalog.categorize("Picklock")[0] != catalog.CATEGORY_WEAPON


def test_consumables_and_services() -> None:
    assert catalog.categorize("HealthPack_med")[0] == catalog.CATEGORY_MEDKIT
    assert catalog.categorize("Grenade 2 (Frag)")[0] == catalog.CATEGORY_GRENADE
    assert catalog.categorize("DocWagonPlatinum")[0] == catalog.CATEGORY_SERVICE
    assert catalog.categorize("TotemRaccoon")[0] == catalog.CATEGORY_TOTEM


def test_outfit_beats_drone_token() -> None:
    # A rigger outfit whose name contains "Drone" is apparel, not a drone.
    assert catalog.categorize("Berlin_Player_Outfit_Tech2Drone")[0] == catalog.CATEGORY_OUTFIT
    assert catalog.categorize("Berlin_DroneAttackA_SteelLynx")[0] == catalog.CATEGORY_DRONE


def test_display_name_strips_namespace_and_underscores() -> None:
    assert catalog.describe("Berlin_Grenade 3 (Flashbang)").display_name == "Grenade 3 (Flashbang)"
    assert catalog.describe("HealthPack_med").display_name == "HealthPack med"


def test_unknown_prefab_is_item_shown_verbatim() -> None:
    info = catalog.describe("CyberdeckSony")
    assert info.category == catalog.CATEGORY_ITEM
    assert info.display_name == "CyberdeckSony"


# --- extracted-catalog integration ---------------------------------------- #

import json as _json
from shadowrun_editor import catalog as _catmod


def _install_catalog(tmp_path, game, data) -> None:
    """Point the catalog module at a temp dir holding one game's bundle."""
    d = tmp_path / "catalog"
    d.mkdir(exist_ok=True)
    (d / f"{game}.json").write_text(_json.dumps(data), encoding="utf-8")
    _catmod.CATALOG_DIR = d
    _catmod.load_game_catalog.cache_clear()


def test_describe_uses_catalog_name_and_description(tmp_path) -> None:
    _install_catalog(tmp_path, "hongkong", {
        "items": {
            "AR 3 Colt M23": {"name": "Colt M23", "description": "Rifle: ...",
                              "type_value": 2},
        },
        "base_sheets": {},
    })
    try:
        info = catalog.describe("AR 3 Colt M23", "hongkong")
        assert info.display_name == "Colt M23"          # real name, not prettified prefab
        assert info.description.startswith("Rifle")
        # Category still derived from the prefab, so grouping is unaffected.
        assert info.category == catalog.CATEGORY_WEAPON
        assert info.subtype == "Assault Rifle"
    finally:
        _catmod.CATALOG_DIR = _catmod.Path(__file__).resolve().parents[1] / "catalog"
        _catmod.load_game_catalog.cache_clear()


def test_describe_falls_back_when_no_catalog(tmp_path) -> None:
    # Point at an empty dir → no bundle → heuristic prettify.
    _catmod.CATALOG_DIR = tmp_path / "empty"
    _catmod.load_game_catalog.cache_clear()
    try:
        info = catalog.describe("HealthPack_med", "hongkong")
        assert info.display_name == "HealthPack med"   # prettified, not a real name
        assert info.description is None
    finally:
        _catmod.CATALOG_DIR = _catmod.Path(__file__).resolve().parents[1] / "catalog"
        _catmod.load_game_catalog.cache_clear()


def test_base_attributes_lookup_is_case_insensitive(tmp_path) -> None:
    _install_catalog(tmp_path, "hongkong", {
        "items": {},
        "base_sheets": {"CG Elf None": {"charisma": 2, "essence": 6, "body": 1}},
    })
    try:
        base = catalog.base_attributes("hongkong", "cg elf none")
        assert base["charisma"] == 2 and base["essence"] == 6
        assert catalog.base_attributes("hongkong", "CG Dwarf None") is None
    finally:
        _catmod.CATALOG_DIR = _catmod.Path(__file__).resolve().parents[1] / "catalog"
        _catmod.load_game_catalog.cache_clear()
