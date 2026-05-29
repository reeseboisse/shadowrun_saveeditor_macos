#!/usr/bin/env bash
# Build a distributable release of the Shadowrun Save Editor:
#   - the ad-hoc-signed .app (Release config)
#   - a wheel of the shadowrun_editor Python package (the bridge backend)
#   - an install.sh that creates the venv the app expects
#   - INSTALL.md (Gatekeeper + setup steps)
# ...zipped with a SHA-256, ready to attach to a GitHub release.
#
# No paid Apple Developer ID is involved: the app is ad-hoc signed, so
# recipients clear Gatekeeper once (see INSTALL.md). Python is NOT bundled
# inside the .app; install.sh sets up a venv from the bundled wheel.
#
# Run from the macos/ directory:  make release   (or ./scripts/package_release.sh)
set -euo pipefail

MACOS_DIR="$(cd "$(dirname "$0")/.." && pwd)"
REPO_ROOT="$(cd "$MACOS_DIR/.." && pwd)"
VERSION="$(/usr/bin/env python3 -c "import tomllib,pathlib;print(tomllib.loads(pathlib.Path('$REPO_ROOT/pyproject.toml').read_text())['project']['version'])")"

STAGE="$MACOS_DIR/dist/ShadowrunEditor-$VERSION"
ZIP="$MACOS_DIR/dist/ShadowrunEditor-$VERSION.zip"

echo "==> Releasing v$VERSION"
rm -rf "$STAGE" "$ZIP" "$ZIP.sha256"
mkdir -p "$STAGE"

echo "==> Building the Python wheel"
/usr/bin/env python3 -m pip wheel "$REPO_ROOT" --no-deps -w "$STAGE" >/dev/null
WHEEL_NAME="$(cd "$STAGE" && ls shadowrun_editor-*.whl | head -1)"
[ -n "$WHEEL_NAME" ] || { echo "wheel build failed"; exit 1; }

echo "==> Generating the Xcode project + building Release"
command -v xcodegen >/dev/null 2>&1 || { echo "xcodegen not found (brew install xcodegen)"; exit 1; }
( cd "$MACOS_DIR" && xcodegen >/dev/null )
( cd "$MACOS_DIR" && xcodebuild \
    -project ShadowrunEditor.xcodeproj \
    -scheme ShadowrunEditor \
    -configuration Release \
    -derivedDataPath build \
    CODE_SIGN_IDENTITY="-" CODE_SIGNING_REQUIRED=NO \
    build >/dev/null )

APP_SRC="$(cd "$MACOS_DIR" && xcodebuild -project ShadowrunEditor.xcodeproj -scheme ShadowrunEditor \
    -configuration Release -derivedDataPath build -showBuildSettings 2>/dev/null \
    | awk -F' = ' '/^[[:space:]]*BUILT_PRODUCTS_DIR/ {d=$2} /^[[:space:]]*FULL_PRODUCT_NAME/ {n=$2} END {print d "/" n}')"
[ -d "$APP_SRC" ] || { echo "could not locate built .app at: $APP_SRC"; exit 1; }

echo "==> Assembling $STAGE"
cp -R "$APP_SRC" "$STAGE/"
cp "$MACOS_DIR/INSTALL.md" "$STAGE/"

# install.sh travels inside the release and installs the bundled wheel into
# the venv the app looks for (~/.shadowrun-editor/venv). Non-editable, so the
# unzipped folder can be thrown away afterwards.
cat > "$STAGE/install.sh" <<'INSTALL'
#!/usr/bin/env bash
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$HOME/.shadowrun-editor/venv"
WHEEL="$(ls "$DIR"/shadowrun_editor-*.whl | head -1)"
echo "Setting up the editor's Python backend at $VENV"
[ -d "$VENV" ] || /usr/bin/env python3 -m venv "$VENV"
"$VENV/bin/pip" install --upgrade pip >/dev/null
"$VENV/bin/pip" install --force-reinstall "$WHEEL"
echo
echo "Done. Launch ShadowrunEditor.app (first time: right path is"
echo "System Settings > Privacy & Security > Open Anyway)."
echo "Smoke test:"
echo '{"id":1,"method":"ping","params":{}}' | "$VENV/bin/shadowrun-editor-bridge"
INSTALL
chmod +x "$STAGE/install.sh"

echo "==> Zipping (ditto, preserves app bundle metadata)"
( cd "$MACOS_DIR/dist" && ditto -c -k --sequesterRsrc --keepParent "ShadowrunEditor-$VERSION" "ShadowrunEditor-$VERSION.zip" )
shasum -a 256 "$ZIP" | awk '{print $1}' > "$ZIP.sha256"

echo
echo "Release ready:"
echo "  $ZIP"
echo "  $(cat "$ZIP.sha256")  (sha256)"
echo "Attach the .zip to a GitHub release. Recipients: see INSTALL.md."
