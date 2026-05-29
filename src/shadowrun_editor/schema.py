"""Schema bundle loading. Provides typed accessors over the JSON bundles
produced by extractor.py.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any


SCHEMAS_DIR = Path(__file__).resolve().parent / "schemas"


@dataclass(frozen=True)
class SchemaField:
    tag: int
    name: str
    property: str
    wire: int | None
    type: str
    is_required: bool
    data_format: int


@dataclass
class SchemaMessage:
    name: str
    namespace: str
    fields_by_tag: dict[int, SchemaField]

    def field(self, tag: int) -> SchemaField | None:
        return self.fields_by_tag.get(tag)

    def field_by_name(self, name: str) -> SchemaField | None:
        for f in self.fields_by_tag.values():
            if f.name == name:
                return f
        return None


@dataclass
class Schema:
    game: str
    version: str
    messages: dict[str, SchemaMessage]
    enums: dict[str, dict[str, int]]

    def message(self, name: str) -> SchemaMessage | None:
        return self.messages.get(name)

    def enum(self, name: str) -> dict[str, int] | None:
        return self.enums.get(name)


def load_schema(path: str | Path) -> Schema:
    raw: dict[str, Any] = json.loads(Path(path).read_text())
    messages: dict[str, SchemaMessage] = {}
    for nm, m in raw.get("messages", {}).items():
        fields = {}
        for tag_s, f in m["fields"].items():
            fields[int(tag_s)] = SchemaField(
                tag=int(tag_s),
                name=f["name"],
                property=f["property"],
                wire=f.get("wire"),
                type=f.get("type", ""),
                is_required=bool(f.get("is_required", False)),
                data_format=int(f.get("data_format", 0)),
            )
        messages[nm] = SchemaMessage(
            name=nm,
            namespace=m.get("namespace", ""),
            fields_by_tag=fields,
        )
    return Schema(
        game=raw.get("game", ""),
        version=raw.get("version", ""),
        messages=messages,
        enums=raw.get("enums", {}),
    )


@lru_cache(maxsize=8)
def load_for_game(game_id: str) -> Schema:
    """Load a built-in schema bundle by game id (one of: dragonfall, returns,
    hongkong). The bundle file is expected at schemas/<game>.json."""
    aliases = {
        "dragonfall": "dragonfall.json",
        "dragonfall-dc": "dragonfall.json",
        "returns": "returns.json",
        "hongkong": "hongkong.json",
        "hong-kong": "hongkong.json",
        "hk": "hongkong.json",
    }
    fn = aliases.get(game_id)
    if fn is None:
        raise ValueError(f"unknown game id: {game_id}")
    return load_schema(SCHEMAS_DIR / fn)
