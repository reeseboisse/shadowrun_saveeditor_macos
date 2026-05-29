# Installing the Shadowrun Save Editor

This build is **ad-hoc signed, not notarized** (no paid Apple Developer ID),
so macOS Gatekeeper will stop it on first launch until you allow it once.
The app also uses a small Python backend that `install.sh` sets up.

## 1. Set up the backend

From the unzipped folder, in Terminal:

```sh
./install.sh
```

This creates a virtual environment at `~/.shadowrun-editor/venv` and installs
the editor's bridge into it. It needs Python 3.11+ (preinstalled on recent
macOS, or `brew install python`). You can throw the unzipped folder away
afterwards — the app only needs the venv.

## 2. First launch (clear Gatekeeper)

Double-clicking the first time shows "Apple could not verify…". To allow it:

- **System Settings → Privacy & Security**, scroll to the message about
  *ShadowrunEditor*, and click **Open Anyway**. Confirm once more on the next
  dialog. (On macOS 15 Sequoia and later this is the only path — the old
  right-click → Open shortcut was removed for unsigned apps.)

Or, equivalently, from Terminal:

```sh
xattr -dr com.apple.quarantine /path/to/ShadowrunEditor.app
```

After that it launches normally every time.

## 3. (Optional) Real item names + correct attributes

The editor works out of the box with heuristic item names and raw attribute
modifiers. To show the games' **real item names/descriptions** and
**effective attribute values**, generate catalogs from your own game install
(this reads the games' content; nothing copyrighted is bundled):

```sh
PY=~/.shadowrun-editor/venv/bin/python3
CAT=~/.shadowrun-editor/catalog
"$PY" -m shadowrun_editor.content_extractor \
  --content-packs "/Applications/Shadowrun Hong Kong - Extended Edition/SRHK.app/Contents/Data/StreamingAssets/ContentPacks" \
  --game hongkong -o "$CAT/hongkong.json"
# ...and likewise --game dragonfall / --game returns against their ContentPacks dirs.
```

The app picks up `~/.shadowrun-editor/catalog/<game>.json` automatically.

## Updating

Re-run `./install.sh` from a newer release and replace the `.app`.
