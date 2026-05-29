# Shadowrun Trilogy Save Editor

A macOS save-game editor for Shadowrun Returns (2013), Shadowrun Dragonfall
– Director's Cut (2014), and Shadowrun: Hong Kong (2015). A SwiftUI app
drives a schema-less protobuf engine (Python) over a JSON-RPC bridge; the
engine and CLI are portable.

All three games are editable end-to-end, with edits verified in-game.

## Features

- **Character editor** — attributes, skills, etiquettes (with ratings),
  unspent karma, and nuyen, with per-game surfaces (e.g. Hong Kong's
  paranormal/infected etiquettes and cyberware affinity; Dragonfall's Alice
  Fund). Derived attributes (reaction, essence) and non-player skills are
  kept out of the editable surface.
- **Inventory editor** — add, remove, and set item stack quantities.
- **Real item names + correct attributes** — when the games' content packs
  are read locally (see below), items show their in-game names and
  descriptions and attributes show effective values (racial base +
  modifier). Without them the editor falls back to readable heuristics.
- **JSON character templates** — export a character to JSON and import it
  back (or onto another save) for backup or sharing a build; cross-game
  import keeps the compatible fields and skips the rest.
- **World-flag viewer** — searchable, filterable listing of story flags.
- **Safe writes** — every write makes a `.bak`, and edits propagate to the
  master `.sav` and every scene-cache `.srt` so the game can't restore old
  state on scene re-entry.

## Install (download a release)

Grab the latest `ShadowrunEditor-<version>.zip` from the repository's
**Releases** page, unzip it, and follow `INSTALL.md` inside. In short:

```sh
./install.sh        # sets up the Python backend at ~/.shadowrun-editor/venv
```

Then launch `ShadowrunEditor.app`. The build is **ad-hoc signed, not
notarized** (refuse to buy a paid Apple Developer ID), so the first launch needs a
one-time **System Settings → Privacy & Security → Open Anyway**. Full
details, including optional content-pack setup, are in
[macos/INSTALL.md](macos/INSTALL.md).

## Build from source (macOS)

Requires Xcode, [xcodegen](https://github.com/yonaskolb/XcodeGen)
(`brew install xcodegen`), and Python 3.11+.

```sh
cd macos
make setup     # creates ~/.shadowrun-editor/venv with the engine installed
make run       # xcodegen + xcodebuild + launch
make release   # build a distributable zip (app + wheel + installer) in dist/
```

See [macos/README.md](macos/README.md) for details.

## Content packs (real item names + effective attributes)

The games keep their content as loose files inside the app bundle; nothing
copyrighted is shipped with this editor. To enable real item names and
effective attribute values, generate a catalog from your own install:

```sh
PY=~/.shadowrun-editor/venv/bin/python3
"$PY" -m shadowrun_editor.content_extractor \
  --content-packs "/Applications/Shadowrun Hong Kong - Extended Edition/SRHK.app/Contents/Data/StreamingAssets/ContentPacks" \
  --game hongkong -o ~/.shadowrun-editor/catalog/hongkong.json
# ...likewise --game dragonfall / --game returns against their ContentPacks dirs.
```

The app and CLI pick up `~/.shadowrun-editor/catalog/<game>.json`
automatically (a `catalog/` dir at the repo root also works for an editable
checkout). Catalogs are never committed.

## CLI usage

The Python package installs a `shadowrun-editor` command.

```sh
# Byte-exact round-trip integrity check
shadowrun-editor verify reference/saves/dragonfall

# Print the player character (and inventory, grouped by category)
shadowrun-editor inspect reference/saves/dragonfall/<uuid>.sav

# Character edits (--slot takes a save folder, .sav, or uuid)
shadowrun-editor set-etiquette academic --slot reference/saves/dragonfall
shadowrun-editor set-karma  --slot reference/saves/dragonfall 100000
shadowrun-editor set-nuyen  --slot reference/saves/dragonfall 999999
shadowrun-editor set-attribute body 6 --slot reference/saves/dragonfall
shadowrun-editor set-skill   decking 7 --slot reference/saves/dragonfall

# Inventory (by engine prefab id; 0 quantity removes the stack)
shadowrun-editor set-item HealthPack_hi 42 --slot reference/saves/hongkong
shadowrun-editor add-item "Grenade 2 (Frag)" --count 5 --slot reference/saves/hongkong

# Character templates (JSON) — back up, share a build, or copy across saves
shadowrun-editor export-character --slot reference/saves/hongkong -o coomer.json
shadowrun-editor import-character coomer.json --slot reference/saves/hongkong --dry-run

# World flags (read-only listing)
shadowrun-editor list-flags reference/saves/dragonfall/<uuid>.sav --filter Global_
```

Every write produces a `.bak` next to the original (skip with
`--no-backup`), and `--dry-run` shows what would change without writing.

## Develop / test

Requires Python 3.11+. From the repo root:

```sh
pip install -e .[dev]            # deps (dnfile, pytest) + CLI entrypoint
pytest
```

## Regenerating schema bundles

The schema bundles in `src/shadowrun_editor/schemas/` are extracted from the
games' DLLs and ship inside the package. To regenerate one:

```sh
shadowrun-extract-schema \
    --dto    reference/dlls/dragonfall/ShadowrunDTO.dll \
    --enums  reference/dlls/dragonfall/Assembly-CSharp.dll \
    --game   dragonfall-dc \
    --out    src/shadowrun_editor/schemas/dragonfall.json
```

The extractor parses `[ProtoMember]` custom-attribute blobs out of the .NET
PE metadata heap directly — no decompilation, no Mono runtime, no `.proto`
generation — and pulls enum tables from any TypeDef extending
`System.Enum`. Dragonfall produces 143 messages / 1238 fields / 191 enums;
Returns (138 / 1118 / 167) and Hong Kong (156 / 1534 / 240) use the same
code.

## Layout

```
src/shadowrun_editor/
├── protobuf_engine.py       # Schema-less read/walk/modify/write of protobuf
├── extractor.py             # DLL → JSON schema bundle
├── content_extractor.py     # ContentPacks → item/base-sheet catalog
├── schema.py                # Schema bundle loader
├── catalog.py               # Item-name + base-attribute catalog (+ heuristics)
├── savefile.py              # Save folder scan, game detection, atomic write
├── service.py               # High-level SaveSession API (GUI/CLI surface)
├── bridge.py                # JSON-RPC over stdio (frontend transport)
├── cli.py                   # `shadowrun-editor` command
├── schemas/                 # Bundled schema JSON (dragonfall/returns/hongkong)
└── domain/
    ├── _common.py           # Shared HBS-engine operations
    ├── dragonfall.py        # + Alice Fund
    ├── returns.py
    └── hongkong.py          # + cyberware affinity
macos/                       # SwiftUI frontend (see macos/README.md, INSTALL.md)
├── project.yml              # xcodegen project spec
├── Makefile                 # setup / project / build / run / release
├── App/                     # @main entry + Info.plist + entitlements
├── Sources/{Bridge,Models,Views}/
└── scripts/                 # setup.sh (dev venv), package_release.sh
tests/                       # engine round-trip, edits, service, bridge, cli,
                             #   catalog, content_extractor
reference/                   # DLLs + example saves
SHADOWRUN_EDITOR_PLAN.md     # design notes + empirically-discovered gotchas
```

## Engine notes

- **Schema-less core.** The engine parses any protobuf payload into a
  `Field` tree without a compiled `.proto`; the schema bundle only adds
  semantic field names for the domain layer.
- **Round-trip safety.** Each `Field` keeps the raw tag-varint and
  length-prefix bytes it parsed from, so an unedited parse → serialize emits
  the source bytes exactly. Mutations set a dirty flag, triggering canonical
  re-encoding and recursive length-prefix recompute up the tree.
- **PC identification.** The playable character is the snapshot whose
  `character_mod.archetypeName == "Player"` **and** `pc_spawn_number == 0`.
  Archetype alone over-matches — party members and drones share the Player
  archetype for control semantics; only the PC gets spawn slot 0.
- **Snapshot multiplicity.** A `.sav` holds many PC snapshots (autosave
  history); edits apply to every meaningful one so the game can't load a
  stale snapshot when re-entering a visited scene.
- **Attributes are modifiers over a base.** Stored stats are deltas over a
  content-pack racial base sheet (`CG <Race> None`); the editor resolves
  effective = base + modifier when a catalog is present. See
  `SHADOWRUN_EDITOR_PLAN.md` §10 for this and other gotchas.

## License

Licensed under the GNU General Public License v3.0 or later. See
[LICENSE](LICENSE). This is an unofficial fan tool; Shadowrun and its save
data are property of their respective owners and no game content is bundled.
