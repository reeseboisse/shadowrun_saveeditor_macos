# macos/Makefile — convenience targets for the SwiftUI app.
#
# All targets assume you're on macOS with Xcode and xcodegen installed.
#   brew install xcodegen

.PHONY: help setup project build run clean

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
	xcodebuild -project ShadowrunEditor.xcodeproj -scheme ShadowrunEditor -configuration Debug

run: build
	open "build/Debug/Shadowrun Save Editor.app" || \
	    xcodebuild -project ShadowrunEditor.xcodeproj -scheme ShadowrunEditor -configuration Debug -derivedDataPath build run

clean:
	rm -rf ShadowrunEditor.xcodeproj build
