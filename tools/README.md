# tools/

Ad-hoc diagnostic scripts for save-file investigation. Not part of the
distributed editor, not unit-tested. Kept so the same analysis can be
re-run when a new "what does this in-game action mutate?" question comes
up.

Each script hard-codes the paths it was last run against — edit the
constants at the top before re-running.

## What's here

- **`deep_diff.py`** — recursive field-by-field diff of two `.sav` files,
  walking nested sub-messages and reporting every field whose value
  differs. Filters out save-clock noise (timestamps, save-image bytes).
  Used to answer "which fields move when a player donates 5000 nuyen to
  the Alice Fund?".

- **`sim_donation.py`** — applies a +X / -X paired edit to a "before"
  save and compares the resulting bytes to a known "after" save. Used to
  confirm that a hypothesized edit reproduces what the game itself
  writes.

- **`srt_deep_diff.py`** — diffs two `.srt` (scene state) files by
  string set, looking for added/removed string literals and for numeric
  values that might mirror a `.sav`-side counter. Used to confirm
  whether scene-state files carry copies of world-flag values that the
  editor would need to update in parallel.

- **`per_block_diff.py`** — per–SaveStoryBlock dump of selected scalar
  fields (currently nuyen + Global_AliceFunds) across all blocks of two
  saves. Used to confirm "only the latest block changes" hypotheses.

- **`inspect_specific.py`** — pretty-prints specific suspected-relevant
  fields (player_measures, life_measures, scene_mapping) from each
  save's latest block. Used to validate or rule out individual
  candidates flagged by deep_diff.

## Findings so far

The Alice Fund donation (Dragonfall, mission-computer action) writes
exactly two fields on the latest SaveStoryBlock:
`Global_AliceFunds += amount` and `nuyen -= amount`. Nothing else in
the save package changes — confirmed by `sim_donation.py` reproducing
the "after" save to within ~100 bytes of unrelated save-clock state.

Whether editing those two fields alone is sufficient to keep the
mission-computer dialog flowing has NOT been confirmed; a paired edit
applied by the user via the editor still left the dialog stuck. Either
the dialog is broken for an unrelated reason or there's something we
haven't found yet — see chat log for the next debugging step.
