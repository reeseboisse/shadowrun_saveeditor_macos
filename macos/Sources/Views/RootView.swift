//
//  RootView.swift
//
//  Top-level navigation. Left: a unified save-slot picker spanning all
//  three games. Right: when a save is open, a tabbed character editor /
//  world flags / pending changes view, or a placeholder if the save's
//  game isn't supported yet.
//

import SwiftUI

struct RootView: View {
    @EnvironmentObject var editor: EditorState

    var body: some View {
        NavigationSplitView {
            SaveSlotPicker()
                .frame(minWidth: 320)
        } detail: {
            if case .missing(let msg) = editor.bridgeStatus {
                BridgeMissingView(message: msg)
            } else if case .crashed(let msg) = editor.bridgeStatus {
                BridgeCrashedView(message: msg)
            } else if let open = editor.openSave {
                if open.summary.supported {
                    OpenSaveView(open: open)
                } else {
                    UnsupportedGameView(summary: open.summary)
                }
            } else {
                EmptyDetailView()
            }
        }
        .alert("Editor error",
               isPresented: Binding<Bool>(
                get: { editor.lastError != nil },
                set: { if !$0 { editor.lastError = nil } }
               ),
               actions: {
                   Button("OK") { editor.lastError = nil }
               },
               message: {
                   Text(editor.lastError ?? "")
               })
    }
}

// MARK: - Detail panes

private struct EmptyDetailView: View {
    var body: some View {
        VStack(spacing: 16) {
            Image(systemName: "doc.text.magnifyingglass")
                .font(.system(size: 48))
                .foregroundStyle(.secondary)
            Text("Select a save from the list to begin.")
                .font(.title3)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

private struct OpenSaveView: View {
    let open: EditorState.OpenSave
    @EnvironmentObject var editor: EditorState
    @State private var showCommitSheet = false

    var body: some View {
        VStack(spacing: 0) {
            // Pending edits banner
            if !open.pendingEdits.isEmpty {
                HStack(spacing: 12) {
                    Image(systemName: "pencil.tip.crop.circle")
                    Text("\(open.pendingEdits.count) unsaved edit\(open.pendingEdits.count == 1 ? "" : "s")")
                        .fontWeight(.medium)
                    Spacer()
                    Button("Discard") { Task { await editor.clearPending() } }
                    Button("Review & Save…") { showCommitSheet = true }
                        .keyboardShortcut(.return, modifiers: .command)
                        .buttonStyle(.borderedProminent)
                }
                .padding(12)
                .background(Color.accentColor.opacity(0.12))
            }

            TabView {
                CharacterEditorView(character: open.character)
                    .tabItem { Label("Character", systemImage: "person.fill") }
                WorldFlagsView(flags: open.worldFlags)
                    .tabItem { Label("World Flags", systemImage: "flag.fill") }
                PendingChangesView(edits: open.pendingEdits, diff: open.diff)
                    .tabItem {
                        Label("Pending (\(open.pendingEdits.count))", systemImage: "tray.fill")
                    }
            }
        }
        .sheet(isPresented: $showCommitSheet) {
            CommitSheet(open: open)
        }
    }
}

private struct BridgeMissingView: View {
    let message: String
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Label("Python bridge not found", systemImage: "exclamationmark.triangle.fill")
                .font(.title2)
                .foregroundStyle(.orange)
            Text(message)
                .font(.system(.body, design: .monospaced))
                .textSelection(.enabled)
        }
        .padding(24)
        .frame(maxWidth: 700)
    }
}

private struct BridgeCrashedView: View {
    let message: String
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Label("Python bridge error", systemImage: "exclamationmark.octagon.fill")
                .font(.title2)
                .foregroundStyle(.red)
            Text(message)
                .font(.system(.body, design: .monospaced))
                .textSelection(.enabled)
        }
        .padding(24)
        .frame(maxWidth: 700)
    }
}

// MARK: - Commit sheet (pre-write diff modal, per plan §9 Phase 2)

private struct CommitSheet: View {
    let open: EditorState.OpenSave
    @Environment(\.dismiss) private var dismiss
    @EnvironmentObject var editor: EditorState
    @State private var result: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Review changes")
                .font(.title2).bold()
            Text("These edits will be written to your save files. Originals will be backed up alongside them as .bak.")
                .foregroundStyle(.secondary)

            GroupBox("Edits") {
                VStack(alignment: .leading, spacing: 4) {
                    ForEach(open.pendingEdits) { e in
                        Text("• \(e.description)")
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.vertical, 4)
            }

            GroupBox("Files") {
                VStack(alignment: .leading, spacing: 4) {
                    if open.diff.isEmpty {
                        Text("No files will change.").foregroundStyle(.secondary)
                    } else {
                        ForEach(open.diff, id: \.self) { line in
                            Text("• \(line)")
                                .font(.system(.body, design: .monospaced))
                        }
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.vertical, 4)
            }

            if let r = result {
                Text(r).foregroundStyle(.secondary).font(.callout)
            }

            HStack {
                Spacer()
                Button("Cancel") { dismiss() }.keyboardShortcut(.cancelAction)
                Button("Save to Disk") {
                    Task {
                        switch await editor.commit() {
                        case .success(let paths):
                            result = "Wrote \(paths.count) file\(paths.count == 1 ? "" : "s")."
                            // Close after a short delay so the user sees confirmation
                            try? await Task.sleep(nanoseconds: 600_000_000)
                            dismiss()
                        case .failure(let err):
                            result = err.message
                        }
                    }
                }
                .keyboardShortcut(.defaultAction)
                .buttonStyle(.borderedProminent)
            }
        }
        .padding(24)
        .frame(minWidth: 560, minHeight: 360)
    }
}
