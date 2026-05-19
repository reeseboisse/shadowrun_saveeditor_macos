# macos/Makefile — convenience targets for the SwiftUI app.
#
# All targets assume you're on macOS with Xcode and xcodegen installed.
#   brew install xcodegen

.PHONY: help setup project build run clean

XCODEBUILD_FLAGS = -project ShadowrunEditor.xcodeproj \
                   -scheme ShadowrunEditor \
                   -configuration Debug \
                   -derivedDataPath build

help:
	@echo "Targets:"
	@echo "  make setup    Install Python deps into ~/.shadowrun-editor/venv"
	@echo "  make project  Run xcodegen to materialize ShadowrunEditor.xcodeproj"
	@echo "  make build    Build the app via xcodebuild"
	@echo "  make run      Build and launch the app"
	@echo "  make clean    Remove the generated .xcodeproj and build artifacts"

setup:
	@./scripts/setup.sh

project:
	@command -v xcodegen >/dev/null 2>&1 || { echo "xcodegen not found. Install with: brew install xcodegen"; exit 1; }
	xcodegen

build: project
	xcodebuild $(XCODEBUILD_FLAGS)

run: build
	@APP=$$(xcodebuild $(XCODEBUILD_FLAGS) -showBuildSettings 2>/dev/null | awk -F' = ' '/^[[:space:]]*BUILT_PRODUCTS_DIR/ {d=$$2} /^[[:space:]]*FULL_PRODUCT_NAME/ {n=$$2} END {print d "/" n}'); \
	  echo "Opening $$APP"; open "$$APP"

clean:
	rm -rf ShadowrunEditor.xcodeproj build
