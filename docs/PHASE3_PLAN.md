# Phase 3 — Returns support

Per SHADOWRUN_EDITOR_PLAN.md §9, Phase 3 brings Shadowrun Returns (The
Dead Man's Switch + Dragonfall's progenitor) into the editor. The plan
calls this "mostly schema + domain work" with no UI surgery required —
the structural identicality across the trilogy's save formats means
Returns reuses the same engine, the same SwiftUI shell, and the same
JSON-RPC bridge.

This document tracks the concrete steps, the user-stated additional
requirement that game-specific surfaces (etiquettes, attributes, skills,
flags) only expose what the active game actually uses, and the
verification needed before Phase 3 can be called done.

## Goals

1. **Returns saves load and edit cleanly** in the GUI and CLI. Same
   set of supported operations as Dragonfall, minus Dragonfall-only
   mechanics (Alice Fund).
2. **Game-specific filtering of the editor surface**. When a Returns
   save is open, the editor must not display or accept edits for:
     * `etiquette_paranormal`, `etiquette_infected` (HK-only)
     * `chi_casting`, `drone_combat`, `drain_resistance` skills
       (not used by Returns scripts)
     * Alice Fund mechanic (Dragonfall-only)
   Conversely, when a Dragonfall save is open, the editor must not
   display HK-only etiquettes / skills.
3. **Round-trip integrity preserved**. Every existing Returns save
   in the reference corpus must continue to parse → serialize ==
   input.
4. **Game neutrality discipline maintained** (plan §10 note 11).
   No save-game-game-name string in non-test code outside its own
   domain adapter.

## Phase 3 exit criterion

Per plan §9: "Same round-trip + edit-loads-cleanly tests pass for
Returns saves through the GUI."

Concrete acceptance test: open the reference Returns save in the GUI,
make an edit (e.g. change an etiquette and increase nuyen), commit,
load the resulting save in Shadowrun Returns, and see the change
reflected in the character sheet without dialog soft-locks or other
script regressions.

## Implementation steps

### A. Engine / domain refactor [DONE — committed in 6e30f1c]

- [x] Extract shared HBS-engine domain into `domain/_common.py`.
- [x] `domain/dragonfall.py` becomes a thin specialization that
  re-exports `_common` and adds Alice Fund (`donate_to_alice_fund`,
  `read_alice_fund`) + `AVAILABLE_ETIQUETTES` / `AVAILABLE_ATTRIBUTES`
  / `AVAILABLE_SKILLS` constants.
- [x] `domain/returns.py` created as a thin specialization with the
  same shared base + Returns-specific `AVAILABLE_*` constants
  (drops paranormal/infected/chi_casting/drone_combat/drain_resistance).
- [x] `service.py` routes through `self._adapter` based on
  `detect_game(...)`.
- [x] `service.py.CharacterView` gains `available_etiquettes`,
  `available_attributes`, `available_skills`. Alice Fund gated via
  `hasattr(adapter, "read_alice_fund")`.
- [x] Queue methods (`queue_set_etiquette`, `queue_add_etiquette`,
  `queue_remove_etiquette`, `queue_set_attribute`, `queue_set_skill`)
  validate against `self._adapter.AVAILABLE_*` and report the allowed
  set in their error messages.

### B. Test suite update [PENDING]

- [ ] `tests/test_service.py::test_summary_returns_is_recognized_but_unsupported`
  needs to flip: Returns is now supported, so this should assert
  `s.supported is True`.
- [ ] `tests/test_service.py::test_unsupported_game_raises_on_edit`:
  Hong Kong is the new "unsupported" sentinel game for that test.
- [ ] Add positive Returns coverage:
  - `test_returns_session_opens_and_round_trips` — round-trip the
    reference Returns save through SaveSession with no edits, bytes
    unchanged.
  - `test_returns_set_etiquette_round_trips` — apply a set_etiquette,
    verify on re-parse.
  - `test_returns_rejects_hk_only_etiquette` — `queue_add_etiquette`
    with `"paranormal"` or `"infected"` raises `ValueError`.
  - `test_returns_rejects_hk_only_skill` — `queue_set_skill` with
    `"chi_casting"` raises.
  - `test_returns_has_no_alice_fund` — `character.alice_fund is None`
    after open.
- [ ] Verify `tests/test_engine_roundtrip.py` already covers Returns
  files (it should — it iterates everything under `reference/saves/`).

### C. SwiftUI surface update [PENDING]

The character editor currently hard-codes etiquette / attribute / skill
lists inside `macos/Sources/Views/CharacterEditorView.swift`:

```swift
private let attributeOrder = [...]
private let skillGroups: [(String, [String])] = [...]
private let etiquetteNames = [...]
```

These need to come from the open save's `CharacterView.available_*`
instead, so when the user picks a Returns save the picker doesn't list
the HK-only options and vice versa.

- [ ] Add to `macos/Sources/Bridge/BridgeModels.swift`:
  ```swift
  struct CharacterView: Codable, Hashable {
      // ...
      var available_etiquettes: [String]
      var available_attributes: [String]
      var available_skills: [String]
  }
  ```
- [ ] In `CharacterEditorView.swift`:
  - Remove the module-level `attributeOrder`, `etiquetteNames`
    constants.
  - For `attributesSection`: iterate `c.available_attributes`.
  - For `etiquettesSection`: iterate `c.available_etiquettes`.
  - For `skillsSection`: rebuild the grouped display so that each
    group only includes skills that are in `c.available_skills`.
    Hide a group entirely if it ends up empty.
- [ ] Sanity-check the field labels still render correctly for skills
  whose name contains underscores (`chi_casting` → "Chi Casting").

### D. Game-detection routing [LIKELY OK]

`savefile.detect_game` returns one of `dragonfall`, `returns`,
`hongkong`, or `unknown` based on substring markers in the .sav.
Confirm via a quick test that the reference Returns save reports
`returns` and gets a Returns adapter via `_ADAPTERS["returns"]`.

### E. End-to-end verification [PENDING]

- [ ] CLI smoke test: `shadowrun-editor inspect <returns-save.sav>`
  prints a clean character sheet with no exceptions.
- [ ] CLI round-trip: `shadowrun-editor verify <returns-folder>`
  reports OK for all files.
- [ ] CLI edit: `set-etiquette --slot <returns-slot> academic` runs,
  re-parsing the edited file shows the expected etiquette change,
  game loads the save without complaint.
- [ ] GUI: open a Returns save, observe that paranormal / infected
  etiquettes do not appear, observe that Alice Fund section is hidden,
  make a small edit, commit, load in Shadowrun Returns and verify the
  change reflects in-game.

### F. Bridge-side `donate-to-alice-fund` guarding [SMALL]

The CLI exposes `donate-to-alice-fund` regardless of which save is
opened. Right now it would no-op silently for a Returns save (the flag
isn't present). Tighten: error out clearly if the open save is not
Dragonfall.

## Out of scope for Phase 3 (per plan §3.2)

- Returns party-member editing (player only in v1).
- Adding new items or abilities (catalog-extending edits).
- Modifying content packs (`*.cpack.bytes`).
- Saved-game Steam Cloud sync.

## Risks

1. **Returns-specific structural differences**. The exploratory diff
   showed structural parity, but Returns is a much older codebase than
   Dragonfall; there may be field tags that the schema declares but
   Returns scripts handle differently. The end-to-end test (E) is the
   only real safety net.
2. **Skill restrictions might be wrong**. I excluded
   `drone_combat`, `drain_resistance`, `chi_casting` from Returns based
   on plan-implied lore. If these turn out to be valid Returns skills,
   the editor will reject legitimate edits. Easy to revisit if
   verification shows this.
3. **Schema mismatch in older Returns saves**. The reference Returns
   save (`7f1ea04…`) parses cleanly today; user-provided saves from
   pre-Director's-Cut Returns might not. We don't have such saves on
   hand to test.

## Next agent / next session

If a fresh context is needed, work from this document. The repo state
at commit `6e30f1c` is the start point. Tests (B), Swift (C), and
verification (E) are the remaining work in priority order. Each is a
contained piece that can be done independently and unit-tested before
moving to the next.
