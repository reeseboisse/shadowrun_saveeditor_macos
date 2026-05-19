//
//  PendingChangesView.swift
//
//  A simple list of queued edits and the file-size delta they'd produce.
//

import SwiftUI

struct PendingChangesView: View {
    let edits: [PendingEdit]
    let diff: [String]
    @EnvironmentObject var editor: EditorState

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            if edits.isEmpty {
                Spacer()
                Text("No pending changes.")
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, alignment: .center)
                Spacer()
            } else {
                GroupBox("Queued edits") {
                    VStack(alignment: .leading, spacing: 4) {
                        ForEach(edits) { e in
                            HStack {
                                Image(systemName: "pencil")
                                    .foregroundStyle(.secondary)
                                Text(e.description)
                                Spacer()
                            }
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.vertical, 4)
                }

                GroupBox("File changes (preview)") {
                    VStack(alignment: .leading, spacing: 4) {
                        if diff.isEmpty {
                            Text("No files will change.")
                                .foregroundStyle(.secondary)
                        } else {
                            ForEach(diff, id: \.self) { line in
                                Text(line).font(.system(.body, design: .monospaced))
                            }
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.vertical, 4)
                }

                HStack {
                    Button("Undo last edit") { Task { await editor.undo() } }
                        .keyboardShortcut("z")
                    Button("Discard all") { Task { await editor.clearPending() } }
                        .keyboardShortcut(.delete)
                    Spacer()
                }
            }
        }
        .padding(24)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
    }
}
