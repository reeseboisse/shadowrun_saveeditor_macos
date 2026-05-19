# Shadowrun Trilogy Save Editor

Save game editor for Shadowrun Returns (2013), Shadowrun Dragonfall –
Director's Cut (2014), and Shadowrun: Hong Kong (2015). Initial target
platform is macOS; the Python core is portable.

The plan and ground-truth research is in
[SHADOWRUN_EDITOR_PLAN.md](SHADOWRUN_EDITOR_PLAN.md) (uploaded reference,
treated as authoritative).

## Status

**Phase 1 complete** — schema-less protobuf read–modify–write engine,
schema extractor that produces JSON bundles from each game's
`ShadowrunDTO.dll` and `Assembly-CSharp.dll`, three generated schema
bundles (Returns / Dragonfall / Hong Kong), and a CLI covering inspect,
verify, etiquette / attribute / skill / karma / nuyen edits, and a
world-flag listing.

Phase 1 exit criterion is met: every save in the reference corpus
round-trips byte-for-byte through the engine, and edits both
same-width (etiquette tag flip) and width-growing (karma 1B→3B varint,
with cascading length-prefix recomputation up the message tree)
re-serialize to valid protobuf that parses back to the expected state.

## Layout

```
src/shadowrun_editor/
├── protobuf_engine.py       # Schema-less read/walk/modify/write of protobuf
├── extractor.py             # DLL → JSON schema bundle
├── schema.py                # Bundle loader
├── savefile.py              # Save folder scan, game detection, atomic write
├── cli.py                   # `shadowrun-editor` command
└── domain/
    └── dragonfall.py        # Dragonfall semantic operations
schemas/
├── dragonfall.json          # Extracted schema bundles
├── returns.json
└── hongkong.json
tests/
├── test_engine_roundtrip.py # Byte-exact parse → serialize for every save
└── test_edits.py            # Edit-then-reparse integrity
reference/                   # DLLs + example saves + original prototype
```

## Setup

Requires Python 3.11+. From the repo root:

```sh
pip install -e .[dev]            # installs deps (dnfile, pytest), CLI entrypoint
pytest                            # run tests
```

## CLI usage

```sh
# Byte-exact round-trip integrity check
shadowrun-editor verify reference/saves/dragonfall

# Print the player character
shadowrun-editor inspect reference/saves/dragonfall/<uuid>.sav

# Same-width edit (etiquette tag flip — no file resize)
shadowrun-editor set-etiquette academic --slot reference/saves/dragonfall

# Width-growing edit (1-byte → 3-byte varint, recomputes length prefixes)
shadowrun-editor set-karma  --slot reference/saves/dragonfall 100000
shadowrun-editor set-nuyen  --slot reference/saves/dragonfall 999999
shadowrun-editor set-attribute body 6 --slot reference/saves/dragonfall
shadowrun-editor set-skill   decking 7 --slot reference/saves/dragonfall

# World flags (read-only listing; Phase 2 GUI will get the editor)
shadowrun-editor list-flags reference/saves/dragonfall/<uuid>.sav --filter Global_
```

Every write produces a `.bak` next to the original (skip with
`--no-backup`). Edits propagate across every file in the save slot — the
master `.sav` and every `.srt` — so the game can't resurrect old state
from a scene cache.

## Regenerating schema bundles

```sh
shadowrun-extract-schema \
    --dto    reference/dlls/dragonfall/ShadowrunDTO.dll \
    --enums  reference/dlls/dragonfall/Assembly-CSharp.dll \
    --game   dragonfall-dc \
    --out    schemas/dragonfall.json
```

The extractor parses `[ProtoMember]` custom-attribute blobs out of the
.NET PE metadata heap directly — no decompilation, no Mono runtime, no
.proto generation. It also pulls enum value tables from any TypeDef that
extends `System.Enum`. Dragonfall produces 143 messages / 1238 fields /
191 enums; the same code handles Returns (138 / 1118 / 167) and Hong
Kong (156 / 1534 / 240).

## Engine notes (Phase 1 design highlights)

- **Schema-less core.** The protobuf engine parses any wire-format
  payload into a `Field` tree without needing a compiled `.proto`. The
  schema bundle is only used by the domain layer to give field names
  semantic meaning.
- **Round-trip safety.** Each `Field` preserves the raw tag-varint and
  length-prefix bytes it was parsed from, so an unedited parse →
  serialize emits the source bytes exactly. Mutations set a dirty flag,
  triggering canonical re-encoding and recursive length-prefix recompute.
- **PC identification.** The plan's original heuristic
  (`archetypeName == "Player"`) over-matches: it also catches party
  members and drones, which inherit the "Player" archetype for control
  semantics. The PC is specifically the snapshot with
  `pc_spawn_number == 0` AND the Player archetype — every other
  controllable character gets a non-zero spawn number.
- **Snapshot multiplicity.** A `.sav` contains many snapshots of the PC
  (autosave history). Edits apply to every PC snapshot in the file so the
  game can't pick up a stale one when reloading a previously visited
  scene.

## Next phase

Phase 2 (macOS GUI for Dragonfall) per the plan §9. The CLI continues to
be a first-class deliverable for scripting and CI integrity checks.
