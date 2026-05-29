"""
Heuristic item catalog.

Inventory items in a save are stored only as engine *prefab-name* IDs —
strings like ``Sh 3 Enfield AS-7``, ``Spell Manabolt 2``, ``HealthPack_med``,
``DocWagonPlatinum``. The trilogy's real localized item names, icons, and
descriptions live in the games' content packs (``.cpack`` archives), which
are NOT bundled with this editor. Rather than ship nothing, this module
derives a *presentation* layer from the naming conventions the games use
internally:

  - a **category** (Weapon, Spell, Foci, Medkit, …) for grouping and icons
  - a **display name** that's tidier than the raw prefab id

This is best-effort and deliberately conservative: when a prefab doesn't
match a known convention it's categorized as ``item`` and shown verbatim.
If a user later points the editor at extracted ``.cpack`` data, this module
is the single place to swap the heuristics for a real lookup table — the
rest of the app only consumes :func:`describe`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

# Where extracted per-game catalogs live (mirrors schemas/ at the repo root).
# Generated locally by content_extractor and intentionally NOT committed
# (item descriptions are game text); the editor degrades to the heuristics
# below when the file is absent.
CATALOG_DIR = Path(__file__).resolve().parents[2] / "catalog"


# Item categories, ordered for stable display grouping. The string values are
# the contract with the UI layer (Swift switches on them for section headers
# and SF Symbols), so treat them as a stable enum.
CATEGORY_WEAPON = "weapon"
CATEGORY_SPELL = "spell"
CATEGORY_FOCI = "foci"
CATEGORY_MEDKIT = "medkit"
CATEGORY_GRENADE = "grenade"
CATEGORY_CONSUMABLE = "consumable"
CATEGORY_DRONE = "drone"
CATEGORY_OUTFIT = "outfit"
CATEGORY_SERVICE = "service"
CATEGORY_TOTEM = "totem"
CATEGORY_KIT = "kit"
CATEGORY_ITEM = "item"

# Display order for the categories above.
CATEGORY_ORDER = [
    CATEGORY_WEAPON,
    CATEGORY_GRENADE,
    CATEGORY_SPELL,
    CATEGORY_FOCI,
    CATEGORY_MEDKIT,
    CATEGORY_CONSUMABLE,
    CATEGORY_DRONE,
    CATEGORY_OUTFIT,
    CATEGORY_SERVICE,
    CATEGORY_TOTEM,
    CATEGORY_KIT,
    CATEGORY_ITEM,
]


# Two-/three-letter weapon-class prefixes the HBS engine uses at the start of
# weapon prefab ids, e.g. "Sh 3 Enfield AS-7" (shotgun), "AR 3 Colt M23"
# (assault rifle). Matched only when followed by a space + digit (the force /
# tier rating), which is how every weapon prefab in the trilogy is shaped —
# this avoids misclassifying e.g. a word that merely starts with "Pi".
_WEAPON_PREFIXES = {
    "pi": "Pistol",
    "smg": "SMG",
    "ar": "Assault Rifle",
    "ri": "Rifle",
    "rfl": "Rifle",
    "sh": "Shotgun",
    "hmg": "Heavy MG",
    "mg": "Machine Gun",
    "bl": "Blade",
    "cl": "Club",
    "un": "Unarmed",
    "wh": "Whip",
    "th": "Thrown",
    "bo": "Bow",
    "mi": "Minigun",
    "ca": "Cannon",
}
_WEAPON_RE = re.compile(r"^([A-Za-z]{2,3})\s+\d")

# Campaign / owner prefixes that prefix-namespace a prefab without being part
# of its human name. "Berlin_" tags Dragonfall (Berlin) content; "HK_" tags
# Hong Kong content. Stripped for display only.
_NAMESPACE_PREFIX_RE = re.compile(
    r"^(Berlin|HK|HongKong|DLC|Bonus|DMS)_", re.IGNORECASE
)


@dataclass(frozen=True)
class ItemInfo:
    """Presentation metadata for one item prefab id."""
    prefab: str          # the raw engine id, unchanged (the storage key)
    display_name: str    # tidied name for the UI
    category: str         # one of the CATEGORY_* constants
    subtype: str | None   # e.g. weapon class ("Shotgun"), else None
    description: str | None = None  # real flavor/description text, if cataloged


@dataclass(frozen=True)
class GameCatalog:
    """Parsed content-pack catalog for one game (see content_extractor)."""
    game: str
    items: dict        # prefab -> {"name", "description", "icon", "type_value"}
    base_sheets: dict   # sheet id ("CG Elf None") -> {attribute: base_value}


@lru_cache(maxsize=8)
def load_game_catalog(game: str | None) -> GameCatalog | None:
    """Load catalog/<game>.json if it exists, else None. Cached; call
    load_game_catalog.cache_clear() if the file changes at runtime."""
    if not game:
        return None
    path = CATALOG_DIR / f"{game}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    return GameCatalog(
        game=game,
        items=data.get("items", {}) or {},
        base_sheets=data.get("base_sheets", {}) or {},
    )


def base_attributes(game: str | None, character_sheet_id: str | None) -> dict | None:
    """Racial base attributes for a character_sheet_id (e.g. 'CG Elf None'),
    from the extracted catalog. None if no catalog or no matching sheet.
    Match is case-insensitive since the save's id casing can vary."""
    cat = load_game_catalog(game)
    if cat is None or not character_sheet_id:
        return None
    sheets = cat.base_sheets
    if character_sheet_id in sheets:
        return sheets[character_sheet_id]
    low = character_sheet_id.lower()
    for sid, attrs in sheets.items():
        if sid.lower() == low:
            return attrs
    return None


def _strip_namespace(prefab: str) -> str:
    return _NAMESPACE_PREFIX_RE.sub("", prefab, count=1)


def _prettify(name: str) -> str:
    """Tidy a prefab id for display without mangling it: drop a campaign
    namespace prefix, turn underscores into spaces, collapse whitespace.
    CamelCase is left intact (splitting it risks wrecking names like
    'DocWagonPlatinum' → 'Doc Wagon Platinum' inconsistently)."""
    s = _strip_namespace(name)
    s = s.replace("_", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s or name


def categorize(prefab: str) -> tuple[str, str | None]:
    """Return ``(category, subtype)`` for a prefab id. ``subtype`` is only
    populated for weapons (their class name); it's None otherwise."""
    p = _strip_namespace(prefab)
    low = p.lower()

    # Magic comes first: "Spell Foci ..." must beat the generic "Spell ..."
    if low.startswith("spell foci") or low.startswith("foci"):
        return CATEGORY_FOCI, None
    if low.startswith("spell "):
        return CATEGORY_SPELL, None

    if "healthpack" in low or "medkit" in low or "trauma" in low:
        return CATEGORY_MEDKIT, None
    if "grenade" in low or "molotov" in low:
        return CATEGORY_GRENADE, None
    if "docwagon" in low:
        return CATEGORY_SERVICE, None
    if "totem" in low:
        return CATEGORY_TOTEM, None
    # Outfit before drone: a rigger's "Player_Outfit_Tech2Drone" is apparel,
    # not a deployable drone, and the "Outfit" token is the stronger signal.
    if "outfit" in low or "armor" in low or "jacket" in low:
        return CATEGORY_OUTFIT, None
    if "drone" in low:
        return CATEGORY_DRONE, None
    if low.startswith("player") or "starter" in low:
        return CATEGORY_KIT, None

    m = _WEAPON_RE.match(p)
    if m:
        cls = _WEAPON_PREFIXES.get(m.group(1).lower())
        if cls is not None:
            return CATEGORY_WEAPON, cls

    # Consumable-ish leftovers (stim/aura/etc. patches) — keep a light touch.
    if "patch" in low or "stim" in low or "aura" in low:
        return CATEGORY_CONSUMABLE, None

    return CATEGORY_ITEM, None


def describe(prefab: str, game: str | None = None) -> ItemInfo:
    """Presentation metadata for a prefab id. When `game` is given and an
    extracted catalog is present, the real in-game name and description are
    used; otherwise the name falls back to the prettified prefab id. Category
    is always derived from the prefab id (stable regardless of the catalog),
    so weapon grouping etc. work the same with or without content packs."""
    category, subtype = categorize(prefab)
    display_name = _prettify(prefab)
    description: str | None = None
    cat = load_game_catalog(game)
    if cat is not None:
        entry = cat.items.get(prefab)
        if entry:
            name = entry.get("name")
            if name:
                display_name = name
            description = entry.get("description")
    return ItemInfo(
        prefab=prefab,
        display_name=display_name,
        category=category,
        subtype=subtype,
        description=description,
    )
