"""
Content-pack extractor.

The shipped games keep their content as *loose files* (no packed archive)
under each title's app bundle:

    <Game>.app/Contents/Data/StreamingAssets/ContentPacks/<pack>/data/
        items/<prefab>.item.bytes     -- one ItemDef per item
        chars/<name>.ch_sht.bytes     -- character base sheets

Every `*.bytes` is the same protobuf wire format as the save files (Unity
appends `.bytes` only so it loads them as TextAssets), so the existing
`protobuf_engine` parses them directly. An item's display name and
description live inline in its UIRep sub-message — no localization table to
chase:

    ItemDef {
      1: id            string   "HealthPack_hi"
      2: type          enum:ItemType
      3: uirep UIRep {
           1: icon     string   "icon_medkit3"
           2: name     string   "Premium Medkit"     <- display name
           3: description string "The premium medkit that heals..."
         }
    }

This module reads those files and emits a per-game catalog JSON the editor
loads to show real item names instead of prefab-id heuristics. It's a tool
the user runs locally against their own install (like the DLL schema
`extractor.py`); nothing here is bundled with the app.

Run:
    python -m shadowrun_editor.content_extractor \\
        --content-packs "/Applications/Shadowrun Hong Kong - Extended Edition/SRHK.app/Contents/Data/StreamingAssets/ContentPacks" \\
        --game hongkong \\
        -o catalog/hongkong.json
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterator

from .protobuf_engine import Field, parse_toplevel

CATALOG_FORMAT = "shadowrun-editor-catalog/1"

# ItemDef / UIRep field tags (from the extracted schema; verified against
# real .item.bytes files).
_ITEM_ID = 1
_ITEM_TYPE = 2
_ITEM_UIREP = 3
_UIREP_ICON = 1
_UIREP_NAME = 2
_UIREP_DESC = 3


@dataclass
class ItemEntry:
    prefab: str
    name: str | None
    description: str | None
    icon: str | None
    type_value: int | None

    def to_json(self) -> dict:
        d: dict = {}
        if self.name is not None:
            d["name"] = self.name
        if self.description is not None:
            d["description"] = self.description
        if self.icon is not None:
            d["icon"] = self.icon
        if self.type_value is not None:
            d["type_value"] = self.type_value
        return d


def _find(fields: list[Field] | None, tag: int) -> Field | None:
    if not fields:
        return None
    return next((f for f in fields if f.tag == tag), None)


def _as_str(f: Field | None) -> str | None:
    if f is None or not isinstance(f.value, (bytes, bytearray)):
        return None
    try:
        return f.value.decode("utf-8")
    except UnicodeDecodeError:
        return f.value.decode("utf-8", errors="replace")


def parse_item_def(data: bytes) -> ItemEntry | None:
    """Parse one `.item.bytes` payload into an ItemEntry, or None if it has
    no id. Tolerant of fields we don't care about."""
    try:
        top = parse_toplevel(data)
    except Exception:
        return None
    prefab = _as_str(_find(top, _ITEM_ID))
    if not prefab:
        return None
    type_f = _find(top, _ITEM_TYPE)
    type_value = int(type_f.value) if type_f is not None and type_f.wire == 0 else None  # type: ignore[arg-type]

    name = desc = icon = None
    uirep = _find(top, _ITEM_UIREP)
    if uirep is not None:
        kids = uirep.children
        if kids is None and isinstance(uirep.value, (bytes, bytearray)):
            # Engine didn't auto-parse the sub-message; try once explicitly.
            try:
                kids = parse_toplevel(uirep.value)
            except Exception:
                kids = None
        icon = _as_str(_find(kids, _UIREP_ICON))
        name = _as_str(_find(kids, _UIREP_NAME))
        desc = _as_str(_find(kids, _UIREP_DESC))

    return ItemEntry(prefab=prefab, name=name, description=desc,
                     icon=icon, type_value=type_value)


def iter_item_files(content_packs: Path) -> Iterator[Path]:
    """Yield every `*.item.bytes` under a ContentPacks directory."""
    yield from content_packs.rglob("*.item.bytes")


def extract_items(content_packs: Path) -> dict[str, ItemEntry]:
    """Parse all item files. Keyed by the ItemDef's own id (the prefab the
    save stores). If two packs define the same id the later-walked one wins;
    rglob order is filesystem-dependent but core/dlc collisions are rare and
    the names are identical when they do collide."""
    out: dict[str, ItemEntry] = {}
    for path in iter_item_files(content_packs):
        try:
            entry = parse_item_def(path.read_bytes())
        except OSError:
            continue
        if entry is not None:
            out[entry.prefab] = entry
    return out


def build_catalog(content_packs: Path, game: str) -> dict:
    items = extract_items(content_packs)
    return {
        "format": CATALOG_FORMAT,
        "game": game,
        "extracted_at": date.today().isoformat(),
        "item_count": len(items),
        "items": {k: items[k].to_json() for k in sorted(items)},
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="shadowrun-content-extractor")
    p.add_argument("--content-packs", required=True,
                   help="Path to a game's ContentPacks directory")
    p.add_argument("--game", required=True,
                   choices=["dragonfall", "returns", "hongkong"])
    p.add_argument("-o", "--output", help="Output catalog .json (stdout if omitted)")
    args = p.parse_args(argv)

    cp = Path(args.content_packs).expanduser()
    if not cp.is_dir():
        print(f"error: not a directory: {cp}", file=sys.stderr)
        return 2
    catalog = build_catalog(cp, args.game)
    text = json.dumps(catalog, indent=2, ensure_ascii=False)
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
        print(f"Wrote {out}  ({catalog['item_count']} items, game={args.game})")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
