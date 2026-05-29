# Shadowrun Trilogy Save Editor — Project Plan

**Target platform:** macOS (Apple Silicon + Intel)
**Target games:** Shadowrun Returns (2013), Shadowrun Dragonfall – Director's Cut (2014), Shadowrun: Hong Kong (2015) + Shadows of Hong Kong DLC
**Source-of-truth context:** This document captures what has already been verified through reverse-engineering a Dragonfall save. Treat the "What's Already Established" section as ground truth — the implementing agent should not rediscover these facts.

---

## 1. Executive Summary

Build a macOS save game editor for the Shadowrun trilogy. The editor reads the games' protobuf-encoded save files, exposes the player character's stats, skills, etiquettes, inventory, karma/nuyen, and party state for inspection and editing, then writes valid save files back to disk with backups preserved.

The three games share a save file lineage (Google Protocol Buffers serialized via the `protobuf-net` library) but each has its own schema. The architecture must isolate the schema per game so all three are supported through a common engine.

A working byte-level patcher for Dragonfall etiquettes already exists (`patch_etiquette.py`) and should be brought into the project as the starting point for the protobuf read/walk layer.

---

## 2. What's Already Established (Do Not Re-derive)

### 2.1 File format

- Save files are raw **Google Protocol Buffers** binary, no envelope, no compression, no encryption.
- Serialized by **protobuf-net 2.0.0.565** (confirmed via strings in `ShadowrunDTO.dll`).
- A "save" is actually a **directory of files**, not a single file:
  - `<save_uuid>.sav` — the master game state (player, party, world flags, inventory, money, karma)
  - `<save_uuid>-<SceneName>-<scene_uuid>.srt` — one per visited scene; holds runtime state for that scene including a snapshot of the player actor
- The Dragonfall save folder on macOS GOG is:
  `~/Library/Application Support/Harebrained Schemes/Shadowrun Dragonfall/Saves/`
  Returns and Hong Kong use sibling directories under `Harebrained Schemes/`.

### 2.2 Wire format basics (standard protobuf)

| Wire type | Meaning | Encoding |
|---|---|---|
| 0 | varint (ints, bools, enums) | LEB128, 1-10 bytes |
| 1 | 64-bit fixed (double, fixed64) | 8 bytes LE |
| 2 | length-delimited (string, bytes, embedded message, packed array) | varint length + bytes |
| 5 | 32-bit fixed (float, fixed32) | 4 bytes LE |

Each field starts with a varint tag = `(field_number << 3) | wire_type`. Field numbers within a single message are unique but the same number can mean different things across messages — context (parent message identity) is what disambiguates.

### 2.3 The character container

A player character record is identifiable by structural pattern, not a string marker:

```
player_container (msg)
├── #4 (msg)  — character stats group
│    ├── #1 (msg)  — attributes (Body, Strength, etc.)
│    ├── #2 (msg)  — skills + etiquettes (combined; etiquettes are just skill tags 20-31)
│    ├── #3 (msg)  — derived/secondary stats group
│    └── #4 (string)  — actor role marker, value "Player" for the playable character
└── ... other actor metadata fields
```

Inside the skills sub-message (`#4.#2`), each present skill or etiquette is a varint field where the tag is the protobuf field number (from `ShadowrunDTO.dll`) and the value is the level. Sparse storage: only nonzero skills are written.

### 2.4 Etiquette tag map (Dragonfall, from schema extraction)

| Tag | Field name | Game availability |
|---|---|---|
| 20 | `etiquette_corporate` | All three |
| 21 | `etiquette_security` | All three |
| 22 | `etiquette_gang` | All three |
| 23 | `etiquette_paranormal` | HK only |
| 24 | `etiquette_socialite` | All three |
| 25 | `etiquette_infected` | HK only |
| 29 | `etiquette_shadowrunner` | All three |
| 30 | `etiquette_street` | All three |
| 31 | `etiquette_academic` | All three |

Returns and HK may add/remove etiquettes — verify against each game's DLL.

### 2.5 A single `.sav` contains many player snapshots

Empirically, a Dragonfall `.sav` (one save slot, hours into the game) contained **22 player-container messages**, of which **6 had an etiquette field set**. The others are NPC actors with the same structural template, pre-character-creation skeletons, or autosave history snapshots. The editor must either patch all matching snapshots or pick the "canonical" one — current behavior of `patch_etiquette.py` is to patch all snapshots that have an etiquette set, and that has been verified to load cleanly.

### 2.6 The schema is in `ShadowrunDTO.dll`

Inside the game's `Dragonfall_Data/Managed/` folder (or equivalent), `ShadowrunDTO.dll` is the .NET assembly containing all the `[ProtoMember(N, Name="...", IsRequired=..., DataFormat=...)]` attributes. The tag → name mapping is recoverable by parsing the .NET custom-attribute blobs out of the DLL's metadata heap. A working extractor exists in the project ground-truth code — it returned 1,136 unique (tag, name) pairs from Dragonfall's DLL.

---

## 3. Scope

### 3.1 In scope (v1)

- Read any `.sav` or `.srt` from all three games and parse to a structured representation.
- **macOS GUI as a primary deliverable**, not an afterthought. The CLI is for testing and scripting; the GUI is what the user actually opens. The GUI ships as soon as the core engine and Dragonfall domain are stable (see Phase 2 in §9).
- Display the player character: name, race, gender, portrait ID, archetype, attributes, skills, etiquettes, current karma/nuyen, inventory, equipped items, cyberware (HK), party members.
- Edit any of the above with type-aware validation (e.g. attribute clamped to game's stat ceiling, etiquette is a known enum value, inventory item ID exists in the game's catalog).
- **Two-tier UI for editing:**
  - **Character editor (rich):** structured forms for attributes, skills, etiquettes, karma, nuyen, inventory — the things a typical user wants to change. Type-validated, semantically labeled, with sensible defaults and warnings when an edit would put the character outside legal in-game limits.
  - **World flags (basic list):** a separate tab/view that shows the ~400 quest/world flags as a flat, searchable, filterable list. Each row shows the flag's name, current value, and an inline editor. No semantic understanding of what each flag does — this is a power-user tool with a "here be dragons" warning banner. Search by substring, filter by flag prefix (e.g. `a1_`, `s2_`, `Global_`), sort by name or by recently modified. Edits to flags go through the same backup/safety flow as character edits.
- **Game neutrality in the finished UI.** Although Dragonfall is the first game supported (because the ground-truth research was done there), the completed app must treat all three games as equal first-class citizens. No Dragonfall-themed branding, no "default game" setting, no game-specific feature hidden behind a non-game-neutral toggle. Specifically:
  - The app's title, icon, and launch screen reference the Shadowrun trilogy as a whole, not any one game.
  - The save folder picker scans the save directories of *all three games* (and any future games using the same engine), presenting saves in a unified list with the game shown as a column/label, not as a sectioning hierarchy that implies precedence.
  - Game detection is per-save (from the version string in the file), not a global app state. Opening a Returns save then a Hong Kong save in the same session must work seamlessly with no mode switch.
  - Game-specific UI sections (e.g. Hong Kong cyberware) appear/hide based on which save is currently open, with the same visual weight regardless of which game it is.
  - If a feature exists in only one game, that's noted neutrally in documentation ("Cyberware editing — Hong Kong only") rather than treated as the others being deficient.
- Round-trip edits back to disk preserving file structure. Backup originals as `.bak` before writing.
- Detect which game a save belongs to (from version string in the file — confirmed present in Dragonfall as `"Shadowrun: Dragonfall - Director's Cut"`) and apply the right schema automatically.
- Patch all relevant `.srt` files when an edit affects the player snapshot, not just the master `.sav`.

### 3.2 Out of scope (v1)

- Semantic understanding of what each world flag *means* (e.g. "setting `a2_Humanis_s1_bribedTheGangers` to true unlocks dialog X"). The flag editor exposes raw values; figuring out which flag controls which game behavior is left to the user.
- Adding new items, abilities, or content not present in the game's catalogs.
- Modding the content packs (`*.cpack.bytes`).
- Steam Cloud sync detection or syncing (Mac GOG users mostly don't have this).
- Windows or Linux support (Mac-only initially; the Python core is cross-platform but the bundling is not).
- Modifying party member characters (focus on the player only for v1).

### 3.3 Stretch goals (v2)

- Party member editing.
- Side-by-side save diffing for debugging.
- Import/export character templates as JSON.
- Cyberware essence cost recalculation when adding/removing implants (Hong Kong).

---

## 4. Architecture

Layered design, each layer independently testable:

```
┌─────────────────────────────────────────────────────┐
│  UI layer (chosen tech stack — see §5)              │
├─────────────────────────────────────────────────────┤
│  Domain layer                                       │
│    "set_karma(save, 100000)"                        │
│    "add_inventory_item(save, item_id, count)"       │
│    "change_etiquette(save, 'security')"             │
├─────────────────────────────────────────────────────┤
│  Schema layer (per-game)                            │
│    Tag↔name maps, enum value tables, class shapes   │
│    Loaded from extracted DLL metadata               │
├─────────────────────────────────────────────────────┤
│  Protobuf engine                                    │
│    Read / walk / modify / write with length-prefix  │
│    recomputation. Wire-format-correct.              │
├─────────────────────────────────────────────────────┤
│  File I/O                                           │
│    Save folder scanning, backups, atomic writes     │
└─────────────────────────────────────────────────────┘
```

### 4.1 Protobuf engine requirements

The existing `patch_etiquette.py` handles a narrow case: same-width tag-byte flip. The full editor needs a generic **read–modify–write** engine that:

- Parses an entire file into an in-memory tree of typed fields.
- Supports edits that change a field's encoded length (e.g. raising karma from 100 → 100000 grows a varint by 2 bytes).
- Recomputes every ancestor length-delimited message's length prefix on serialize-out.
- Supports adding fields not currently present (e.g. giving the character cyberware they don't have).
- Supports removing fields.
- Supports repeated fields (inventory items are stored as repeated sub-messages).
- Preserves field ordering and unknown fields verbatim (forward-compatibility with content the editor doesn't understand).

Reference implementation note: `google.protobuf` Python library can serialize/deserialize *if* given a `.proto` schema, but the schema must be generated from the extracted DTO. An alternative is a schema-less reader (already prototyped) plus a generic writer — that path avoids generating .proto files but requires care with packed-vs-unpacked repeated fields and default-value omission.

### 4.2 Schema layer

For each game, the editor loads a precomputed schema bundle containing:

- Class shape definitions: which tags belong to which message type
- Tag → field name mappings
- Field name → wire type mappings
- Enum value tables (from `Assembly-CSharp.dll`)
- Item ID catalog (from content packs, if extracted)

The schema extractor (offline tool, run once per game version) reads the DLLs and writes a JSON bundle. The editor ships pre-extracted bundles so end users don't need the DLLs installed.

### 4.3 Domain layer

Semantic operations expressed in game terms, not protobuf terms. Each game gets its own domain adapter because attribute names, stat ceilings, and concepts differ (e.g. HK adds essence/cyberware accounting).

---

## 5. Tech Stack

**Recommended:** Python 3.11+ core engine, SwiftUI frontend talking to it via a local IPC bridge — OR — Python with PyQt6/PySide6 for a single-language solution.

**Justification:**

- Python is the right choice for the core because: protobuf parsing libraries are mature, the existing prototype is Python, .NET DLL metadata parsing is doable in pure Python with the `dnfile` library, and Reese has the most leverage debugging Python.
- For the UI, SwiftUI gives the most native-feeling Mac app but doubles the language count. PyQt6 gets a working GUI faster, looks decent on macOS, and ships as a single `.app` via `py2app` or `briefcase`.
- A local web app (FastAPI + React in a wrapper like Tauri or `pywebview`) is a viable third path — modern UI for less effort than SwiftUI, single bundle, but adds web-tier complexity.

**Claude Code should pick one of these three and stick with it.** Don't mix.

**Required Python packages:**
- `protobuf` (Google's, for canonical wire format reference)
- `dnfile` (parse .NET DLL metadata for schema extraction)
- One of: `PyQt6` / `PySide6` / `fastapi`+`pywebview` (UI)
- `pytest` (testing)

---

## 6. File Format Reference

### 6.1 Save folder layout (per game)

Each save slot is a UUID-named group:

```
~/Library/Application Support/Harebrained Schemes/Shadowrun Dragonfall/Saves/
├── <uuid>.sav                              ← master state
├── <uuid>-<SceneName>-<scene_uuid>.srt     ← one per visited scene
├── <uuid>-<SceneName>-<scene_uuid>.srt
├── <uuid>.png                              ← screenshot thumbnail
├── <uuid>.metadata                         ← (if present) small metadata blob
```

The `.sav` and the `.srt` files all use the same protobuf format but different root message types.

### 6.2 Save identification

The top-level `.sav` message includes a string field with the game name. Verified value for Dragonfall: `"Shadowrun: Dragonfall - Director's Cut"`. Returns and HK use their own version strings — capture these during schema extraction.

### 6.3 Varint encoding rules (critical for editing)

A varint up to 7 bits encodes as 1 byte. Each additional 7 bits adds a byte. Tag bytes for etiquettes 20-31 all fit in 2 bytes because `(tag << 3) | wire_type` for tag 31 = `0xF8` `0x01` (still under the 14-bit boundary). This is why the etiquette patch could be a same-width byte flip. Other edits won't have this luxury — for example, karma at 1000 is one byte, karma at 100000 is three bytes, so the parent message's length prefix must be updated by +2.

### 6.4 Player snapshot multiplicity

Treat the `.sav` as having multiple player snapshots — the editor's domain layer should hide this from the UI by treating them as a single logical character but writing the edit to every snapshot. This was verified to load correctly in Dragonfall.

---

## 7. Schema Extraction Methodology

### 7.1 What's in `ShadowrunDTO.dll`

This is a small (~500 KB) .NET assembly containing the protobuf-net data contract classes. Every serializable property is decorated with `[ProtoMember(tag, Name="field_name", IsRequired=..., DataFormat=...)]`. The .NET PE format stores these attribute parameter values as binary blobs in the metadata heap — fully parseable without decompilation.

A working blob parser is in the ground-truth code. It returns `(tag, field_name)` pairs by scanning for the custom-attribute blob signature (`01 00` prolog + 4-byte tag + named-param count + named-param entries).

### 7.2 What's in `Assembly-CSharp.dll`

This is the large (~5-10 MB) game logic assembly. It contains:

- Enum definitions with member names and underlying integer values (race enums, item type enums, archetype enums, dialog state enums)
- Prerequisite logic (encoded as method bodies — harder to extract automatically)
- Item, skill, and ability ID strings (often hardcoded or referenced by interned string constants)

For v1, extract enum value tables and string constants. Skip method body analysis (too much work for the value).

### 7.3 Schema bundle format

Output of schema extraction, one per game, ships with the editor:

```json
{
  "game": "dragonfall-dc",
  "version": "1.2.7",
  "extracted_at": "2026-05-19",
  "messages": {
    "ActorSkills": {
      "fields": {
        "1": {"name": "bows", "wire": 0, "type": "int32"},
        "12": {"name": "drone_control", "wire": 0, "type": "int32"},
        ...
        "29": {"name": "etiquette_shadowrunner", "wire": 0, "type": "int32"}
      }
    },
    ...
  },
  "enums": {
    "EtiquetteType": {"Corporate": 20, "Security": 21, ...}
  },
  "items": [
    {"id": "Berlin_Grenade_Frag", "display": "Fragmentation Grenade", "price": 250, ...}
  ]
}
```

---

## 8. Files Reese Needs to Provide

For **each of the three games**, locate via:

```bash
find ~ -name "ShadowrunDTO.dll" 2>/dev/null
find ~ -name "Assembly-CSharp.dll" 2>/dev/null
```

### 8.1 Required (six DLLs total)

| File | Purpose | Expected size |
|---|---|---|
| `<Returns>/Resources/Data/Managed/ShadowrunDTO.dll` | Returns schema | ~400 KB |
| `<Returns>/Resources/Data/Managed/Assembly-CSharp.dll` | Returns enums + IDs | ~5-10 MB |
| `<Dragonfall>/Resources/Data/Managed/ShadowrunDTO.dll` | Dragonfall schema (likely identical to Returns DC) | ~500 KB |
| `<Dragonfall>/Resources/Data/Managed/Assembly-CSharp.dll` | Dragonfall enums + IDs | ~5-10 MB |
| `<HongKong>/Resources/Data/Managed/ShadowrunDTO.dll` | HK schema | ~600 KB |
| `<HongKong>/Resources/Data/Managed/Assembly-CSharp.dll` | HK enums + IDs | ~5-10 MB |

### 8.2 Required (three example saves)

For each game, a save file taken right after character creation with deliberately distinctive choices:

- Specific etiquette picked
- Specific archetype (Decker, Street Samurai, Shaman, Adept, Mage, Rigger)
- Note the in-game karma and nuyen values at that save point

For each save, include the entire save slot (`.sav` + all `.srt` files for that slot). Total per save typically 1-5 MB.

Also helpful: one mid-game save per game (after recruiting party, installing cyberware in HK, etc.) for testing the editor against more complete state.

### 8.3 Optional but high-value (content packs)

| File | Purpose |
|---|---|
| `DeadMansSwitch.cpack.bytes` (Returns) | Item catalog, ability descriptions |
| `Berlin.cpack.bytes` (Dragonfall) | Item catalog, ability descriptions |
| `HongKong.cpack.bytes` + `Bonus_HongKong.cpack.bytes` | Item catalog, ability descriptions |

These let the editor display "Fragmentation Grenade — 250¥, AoE damage" instead of `Berlin_Grenade_Frag`. They're large (50-100 MB each) and v1 can ship without them, but the editor's UX is significantly better with them.

### 8.4 Bring-along from prior work

- `patch_etiquette.py` — working byte-level patcher, use as starting point for the protobuf walker
- The schema extractor prototype that returned 1,136 entries from Dragonfall's DTO DLL

---

## 9. Implementation Phases

### Phase 1: Core engine + Dragonfall domain (CLI only)

- Extend the existing protobuf walker into a full read–modify–write engine
- Build the schema extractor as a standalone CLI tool that takes a DLL and outputs a JSON schema bundle
- Generate the Dragonfall schema bundle
- Build the domain layer for Dragonfall: stats, skills, etiquettes, karma, nuyen, inventory list, party roster
- CLI-only at this stage. Get the engine 100% right before adding UI. The CLI is a permanent deliverable (used for scripting and CI), not throwaway.
- **Exit criterion:** Round-trip every Dragonfall save in the test corpus with no byte changes when no edits are made, then verify every supported edit type loads cleanly in-game.

### Phase 2: macOS GUI for Dragonfall

Once the engine is proven on Dragonfall via the CLI, build the GUI. Don't wait for Returns/HK support — getting the UX right for one game first is faster than designing in the abstract for three.

- Pick one of the three UI stacks from §5 and commit to it
- **Save slot picker:** scan the save folders for **all three games** even though only Dragonfall is functional in Phase 2. Returns and Hong Kong saves appear in the list with their game labeled, and selecting one shows a "Support for this game arrives in Phase 3 / Phase 4" placeholder rather than an error. This forces the picker UI to be game-neutral from day one — no Dragonfall-specific paths, no hardcoded folder names. Show screenshot thumbnail (read from the `.png` next to the `.sav`), character name, location, in-game timestamp, and game. Sort by recency.
- **Character editor tab (rich):**
  - Header: name, race/gender, portrait, archetype
  - Attributes section with steppers and current/max display
  - Skills section grouped by category (combat, magic, tech, social)
  - Etiquettes section as a single-select dropdown (since picking a new one effectively replaces the old)
  - Resources: karma, nuyen with simple number inputs
  - Inventory: scrollable list of equipped + carried items, each with quantity edit
- **World flags tab (basic list):**
  - Flat table: flag name, type, current value, edit control
  - Search bar (substring match on flag name)
  - Filter chips for common prefixes (`a1_`, `a2_`, `s1_`, `Global_`, `Haven_`, etc.) auto-derived from the data
  - Prominent warning banner: "Editing world flags can put your save into an inconsistent state. Back up first."
  - Per-row "reset to original" button
- **Save flow:** "Save changes" button writes through the engine, creating `.bak` files and patching all `.srt` snapshots. A pre-commit diff modal shows what's about to change.
- **Edit history within a session:** undo/redo for the current open save before commit.
- macOS app bundling (`.app` via the bundler matching the chosen UI stack)
- **Exit criterion:** Reese can launch the app, open the Dragonfall save we already analyzed, change the etiquette + give the character 10000 nuyen + flip a world flag, save, load in-game, and see all three reflected correctly.

### Phase 3: Returns support

With a working app, adding Returns is mostly schema + domain work:

- Run the schema extractor on Returns' DLLs
- Diff the Returns schema against Dragonfall; expect significant overlap with some renames
- Build the Returns domain adapter (most logic reused from Dragonfall)
- Wire game detection so the app auto-routes Returns saves to the right domain layer
- Verify the existing UI works against Returns saves with no per-game UI customization (it should — that's what the schema/domain separation is for)
- **Exit criterion:** Same round-trip + edit-loads-cleanly tests pass for Returns saves through the GUI.

### Phase 4: Hong Kong support

- Run the schema extractor on HK's DLLs
- HK introduces cyberware essence accounting and matrix changes — expect new message types and possibly a new UI section for cyberware/essence
- Build the HK domain adapter, including essence cost recalculation if cyberware is edited
- Add cyberware section to the character editor (gracefully hidden for games that don't have it)
- **Exit criterion:** Round-trip + edits validated against HK example saves through the GUI, with the cyberware section appearing only on HK saves.

### Phase 5: Polish

- Item catalog integration (if content packs were extracted): swap raw item IDs for human-readable names + descriptions in the inventory editor
- Portrait thumbnail picker (vs. raw portrait ID dropdown)
- Side-by-side save diffing as a separate view
- Import/export character templates as JSON
- macOS app signing / notarization if distributing beyond personal use

---

## 10. Known Gotchas and Design Constraints

These have been discovered empirically and must not be re-discovered the hard way.

1. **One `.sav` contains many player-shaped messages.** Most are NPCs, autosave snapshots, or pre-character-creation skeletons. Identify true player snapshots by the `#4 (msg) > #4 (string) == "Player"` structural pattern, not by string match alone. The Dragonfall save had 22 such matches with only 6 carrying real character state.

2. **Edits must propagate to `.srt` files.** When the player's etiquette/skills/inventory changes, every `.srt` in the save slot needs the matching player snapshot updated, or the game will resurrect the old state when re-entering a scene the player has previously visited.

3. **Tag-flip edits are a special case.** Same-width varint tag changes (which is what the existing patcher does) are byte-clean. Most edits aren't: they grow or shrink the encoded value and require length-prefix updates all the way up the message tree.

4. **Unknown fields must survive round-trip.** The editor will not understand every field. Anything it doesn't recognize must be preserved byte-exact on write, in the original order. This is non-negotiable for forward compatibility.

5. **Etiquette values are skill levels, not booleans.** A character with Shadowrunner etiquette doesn't have a boolean flag — they have `etiquette_shadowrunner: 1` in their skills sub-message. Changing the etiquette is a tag change, not a value change. Adding karma points to etiquettes (allowed in-game) increases the integer value.

6. **The DLL is shared across games, the values aren't.** Dragonfall's `ShadowrunDTO.dll` defines `etiquette_infected` and `etiquette_paranormal` even though they're Hong Kong-only. Don't assume a field's presence in the DLL means it's usable in that game. Validate against each game's actual save corpus.

7. **protobuf-net's default value omission.** If a field's value equals the protobuf default (0, empty string, false), protobuf-net omits it from the wire entirely. The editor must mirror this on serialize-out — writing an explicit zero where the original omitted it can subtly change behavior if the game distinguishes "absent" from "zero" anywhere.

8. **macOS app signing and notarization.** A `.app` bundle distributed outside the Mac App Store needs to be signed and notarized or users will hit Gatekeeper. For a personal-use tool this is fine to skip; for sharing, factor in an Apple Developer ID.

9. **The decoder string `"shadowrunners"` is the team affiliation, not the etiquette.** Every party member, drone, and friendly NPC in a scene has `team_affiliation = "Shadowrunners"`. This is a classic false-positive — don't let the UI surface this field as user-editable without a clear label.

10. **Backups, always.** Every write must produce a `.bak` of the original. The editor should also support a "snapshot the whole save folder before this session" workflow for users doing exploratory edits.

11. **Game-neutrality discipline.** Because Phase 1 and Phase 2 only support Dragonfall, it's easy to accidentally encode Dragonfall as the "primary" or "default" game in code paths that will later be hard to untangle (hardcoded folder paths, default-routed save-game detection, asset paths assuming a Berlin-themed UI, etc.). Watch for this actively during Phase 2. Anything that references "Dragonfall" by name in non-test code should either be inside the Dragonfall domain adapter (correct) or refactored before Phase 3 begins (incorrect). A useful check: imagine adding a fourth Shadowrun game to the app — if doing so requires changing anything outside its own domain adapter and schema bundle, the abstraction has leaked.

12. **Attributes in `CharacterMod.stats` are a *modifier over a content-pack base*, not the effective value — and the base isn't bundled.** (Investigated Phase 5; fix deferred to content-pack extraction.) The player's `CharacterInstance.character_sheet_id` is `"CG <Race> None"` (e.g. `CG Elf None`), a character-generation racial base sheet that lives in the games' `.cpack` content, not in `ShadowrunDTO.dll`/`Assembly-CSharp.dll` and not anywhere else in the save. The DLL exposes `SetInstanceModFromBaseAttributes` / `baseAttributes`: the engine computes effective attributes as **base-sheet value + stored modifier**, and the save stores only the modifier. Evidence it's a modifier and not an absolute value: attributes a character never raised are stored as `0` and therefore omitted by protobuf-net (gotcha 7) — Cooma's strength/willpower, Coomer's intelligence — and no character can have an absolute attribute of 0. But it isn't a clean delta-over-1 either (Returns Cummer stores `charisma = 6`, which as a delta over a base of 1 would exceed the cap), so the per-attribute, per-race base offsets are genuinely unknown without the content packs. Consequence: the attribute numbers the editor displays today are the stored modifiers and may differ from the in-game character screen by the racial base — for omitted attributes they read as `0`. Editing still works (writes the absolute modifier and round-trips), and the seven editable attributes were exercised in-game; only the *displayed baseline* is affected. Correct fix needs the `CG <Race> None` base sheets from `.cpack` extraction (or one ground-truth in-game attribute reading per race to back out the offsets). Derived attributes (`reaction`, `essence`) are already excluded from the editable surface; see `DERIVED_ATTRIBUTES`.

---

## 11. Testing Strategy

### 11.1 Round-trip integrity (mandatory before any edit feature ships)

For every save in the test corpus, parse it to the in-memory tree and re-serialize without modification. The output bytes must equal the input bytes exactly. Any mismatch is a bug in the engine.

### 11.2 Edit-then-load tests

For each edit operation, apply it to a known save, load the result in the actual game, and verify the change is reflected in the character sheet UI. This is manual but catches semantic errors the byte-level tests miss.

### 11.3 Schema completeness tests

For each example save, walk every field and confirm the schema bundle has a name for every tag encountered. Unknown tags should be logged (not errors — they may be legitimate optional fields) so the schema can be improved.

### 11.4 Corpus

Reese should provide 3-5 saves per game spanning early/mid/late game and different character builds. The mid-game and late-game saves are especially valuable because they exercise more of the schema (party members, cyberware, larger inventories, all dialog flags set).

---

## 12. Deliverables

When complete, Reese should have:

1. A macOS `.app` bundle (or alternate distribution if web/SwiftUI path) that opens save folders for all three games. Two-tier UI: rich character editor + basic world flags list view.
2. A CLI tool for headless edits and scripting (developed in Phase 1, maintained throughout).
3. The schema extractor as a separate CLI (so it can be re-run when a game patch ships).
4. Three schema bundles (Returns, Dragonfall, HK).
5. A test suite that runs round-trip integrity checks over a corpus of saves.
6. Documentation: how to use it, how to extract schemas for a new game patch, what's editable and what isn't, what the world flag warning banner is warning about.

---

## Appendix A: Handoff to Claude Code

Suggested initial prompt:

> I'm building a macOS save editor for the Shadowrun trilogy (Returns, Dragonfall DC, Hong Kong). Read SHADOWRUN_EDITOR_PLAN.md in full before doing anything else. The plan captures everything that's already known about the save format, the schema extraction technique, and the architecture. Treat sections 2 and 10 as established facts — don't re-derive them.
>
> Start with Phase 1: extend the existing `patch_etiquette.py` into a full protobuf read–modify–write engine, build the schema extractor as a separate CLI that produces JSON schema bundles, and generate the Dragonfall schema bundle. Get round-trip integrity (parse → reserialize == input bytes) working on every Dragonfall save I provide before adding any edit features.
>
> Phase 1 is CLI-only — the CLI is a permanent deliverable (used for scripting and CI), not a throwaway. Once Phase 1 passes its exit criterion, move to Phase 2 (macOS GUI for Dragonfall) before touching Returns or Hong Kong support. The two-tier UI design in section 3.1 is important: a polished character editor for the common case, and a separate basic list view for world flags. Don't conflate them.
>
> Files I'm providing: [list]. Ask me before adding dependencies beyond what's recommended in section 5.

---

## Appendix B: Files Checklist for Reese

Before kicking off the project, gather and upload:

- [ ] `Shadowrun Returns/Contents/Resources/Data/Managed/ShadowrunDTO.dll`
- [ ] `Shadowrun Returns/Contents/Resources/Data/Managed/Assembly-CSharp.dll`
- [ ] `Shadowrun Dragonfall/Contents/Resources/Data/Managed/ShadowrunDTO.dll`
- [ ] `Shadowrun Dragonfall/Contents/Resources/Data/Managed/Assembly-CSharp.dll`
- [ ] `Shadowrun Hong Kong/Contents/Resources/Data/Managed/ShadowrunDTO.dll`
- [ ] `Shadowrun Hong Kong/Contents/Resources/Data/Managed/Assembly-CSharp.dll`
- [ ] Returns example save: post-character-creation, full save slot directory
- [ ] Dragonfall example save: post-character-creation, full save slot directory (the one already analyzed counts)
- [ ] Hong Kong example save: post-character-creation, full save slot directory
- [ ] (Optional) Mid-game save per game for richer test coverage
- [ ] `patch_etiquette.py` (from prior work, as starting point)

Use `find ~ -name "ShadowrunDTO.dll" 2>/dev/null` to locate the DLLs — Mac install paths vary between GOG and Steam.
