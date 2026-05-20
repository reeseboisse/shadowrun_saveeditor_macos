//
//  SaveSlotPicker.swift
//
//  Game-neutral list of every save the bridge found on disk. Saves from
//  all three games appear in a single list with the game shown as a
//  label, NOT as a sectioning hierarchy that implies precedence (per
//  plan §3.1 game-neutrality requirement).
//

import SwiftUI
import AppKit

struct SaveSlotPicker: View {
    @EnvironmentObject var editor: EditorState
    @State private var search = ""
    @State private var selectedID: SaveSummary.ID?

    private func chooseFolder() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.prompt = "Add Folder"
        panel.message = "Choose your Shadowrun save folder. The app will scan it for .sav files."
        panel.directoryURL = FileManager.default
            .urls(for: .applicationSupportDirectory, in: .userDomainMask).first
        if panel.runModal() == .OK, let url = panel.url {
            editor.addCustomFolder(url.path)
        }
    }

    var filtered: [SaveSummary] {
        guard !search.isEmpty else { return editor.allSaves }
        let q = search.lowercased()
        return editor.allSaves.filter { s in
            (s.display_name?.lowercased().contains(q) ?? false)
            || (s.char_name?.lowercased().contains(q) ?? false)
            || (s.scene_name?.lowercased().contains(q) ?? false)
            || s.gameDisplayName.lowercased().contains(q)
            || s.uuid.lowercased().contains(q)
        }
    }

    private var searchAndToolsBar: some View {
        VStack(spacing: 0) {
            HStack {
                TextField("Search saves", text: $search)
                    .textFieldStyle(.roundedBorder)
                Button {
                    chooseFolder()
                } label: {
                    Image(systemName: "folder.badge.plus")
                }
                .help("Add a save folder to scan")
                Button {
                    Task { await editor.rescanSaves() }
                } label: {
                    Image(systemName: editor.loadingSaves ? "arrow.triangle.2.circlepath.circle" : "arrow.clockwise")
                }
                .help("Rescan save folders")
            }
            .padding(8)
            if !editor.customFolders.isEmpty {
                CustomFoldersList(folders: editor.customFolders) {
                    editor.removeCustomFolder($0)
                }
            }
            Divider()
        }
        .background(.bar)
    }

    var body: some View {
        Group {
            if editor.allSaves.isEmpty && !editor.loadingSaves {
                VStack(spacing: 0) {
                    searchAndToolsBar
                    EmptyState(discovered: editor.discoveredFolders, onChooseFolder: chooseFolder)
                }
            } else {
                List(filtered, selection: $selectedID) { s in
                    SaveSlotRow(summary: s)
                        .tag(s.id)
                }
                .listStyle(.sidebar)
                .safeAreaInset(edge: .top, spacing: 0) {
                    // Pin the search bar / folder picker as a top inset so
                    // it never scrolls away with the list content.
                    searchAndToolsBar
                }
                // Drive open() off selection changes rather than a
                // computed Binding. A previous attempt used Binding<ID?>
                // with get/set, but SwiftUI's List can call set without
                // re-reading get on the next render, leaving the visual
                // selection out of sync with editor.openSave.
                .onChange(of: selectedID) { newID in
                    guard let newID,
                          let s = editor.allSaves.first(where: { $0.id == newID })
                    else { return }
                    Task { await editor.open(summary: s) }
                }
                // Sync the other direction: when openSave changes externally
                // (e.g. after a save reload or an open-from-elsewhere flow),
                // the highlighted row tracks it.
                .onChange(of: editor.openSave?.summary.id) { newID in
                    if selectedID != newID { selectedID = newID }
                }
            }
        }
    }
}

private struct EmptyState: View {
    let discovered: [String: [String]]
    let onChooseFolder: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("No saves found.")
                .font(.headline)
            if discovered.isEmpty {
                Text("The editor looks for Shadowrun saves under ~/Library/Application Support/Harebrained Schemes/ — none of the expected folders exist on this machine.")
                    .foregroundStyle(.secondary)
                Text("If your saves live elsewhere (GOG, Steam, or App Store installs use different paths), add the folder manually.")
                    .foregroundStyle(.secondary)
                    .font(.callout)
            } else {
                Text("Searched these folders:")
                    .foregroundStyle(.secondary)
                ForEach(discovered.keys.sorted(), id: \.self) { game in
                    if let folders = discovered[game] {
                        ForEach(folders, id: \.self) { f in
                            Text(f).font(.system(.caption, design: .monospaced))
                                .foregroundStyle(.secondary)
                        }
                    }
                }
            }
            Button(action: onChooseFolder) {
                Label("Choose Folder…", systemImage: "folder.badge.plus")
            }
            .buttonStyle(.borderedProminent)

            Divider().padding(.vertical, 4)

            DiagnosticPanel()
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

private struct DiagnosticPanel: View {
    @EnvironmentObject var editor: EditorState
    @State private var running = false

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text("Debug").font(.caption).foregroundStyle(.secondary)
                Spacer()
                Button {
                    running = true
                    Task {
                        await editor.runDiagnostics()
                        running = false
                    }
                } label: {
                    Label(running ? "Running…" : "Run Bridge Diagnostic",
                          systemImage: "stethoscope")
                        .font(.caption)
                }
            }
            if let d = editor.diagnostics {
                ScrollView {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("HOME = \(d.home)").font(.system(.caption, design: .monospaced))
                        Text("CWD  = \(d.cwd)").font(.system(.caption, design: .monospaced))
                        ForEach(d.candidates) { c in
                            HStack(alignment: .top, spacing: 6) {
                                Image(systemName: c.is_dir ? "checkmark.circle.fill" : "xmark.circle.fill")
                                    .foregroundStyle(c.is_dir ? .green : .red)
                                    .font(.caption)
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(c.expanded)
                                        .font(.system(.caption, design: .monospaced))
                                        .lineLimit(2)
                                        .truncationMode(.middle)
                                    Text("exists=\(String(c.exists))  is_dir=\(String(c.is_dir))  sav_count=\(c.sav_count)\(c.error.map { "  err: \($0)" } ?? "")")
                                        .font(.caption2)
                                        .foregroundStyle(.secondary)
                                }
                            }
                        }
                    }
                }
                .frame(maxHeight: 200)
            }
        }
    }
}

private struct CustomFoldersList: View {
    let folders: [String]
    let onRemove: (String) -> Void
    @State private var expanded = false

    var body: some View {
        DisclosureGroup(isExpanded: $expanded) {
            VStack(alignment: .leading, spacing: 2) {
                ForEach(folders, id: \.self) { p in
                    HStack(spacing: 4) {
                        Text(p)
                            .font(.system(.caption, design: .monospaced))
                            .lineLimit(1)
                            .truncationMode(.middle)
                        Spacer()
                        Button {
                            onRemove(p)
                        } label: {
                            Image(systemName: "minus.circle").font(.caption)
                        }
                        .buttonStyle(.plain)
                        .help("Stop scanning this folder")
                    }
                }
            }
            .padding(.vertical, 2)
        } label: {
            Text("Custom folders (\(folders.count))")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .padding(.horizontal, 8).padding(.vertical, 4)
        Divider()
    }
}

struct SaveSlotRow: View {
    let summary: SaveSummary

    var body: some View {
        HStack(spacing: 10) {
            ThumbnailView(path: summary.thumbnail_path, game: summary.game)
                .frame(width: 56, height: 36)
                .cornerRadius(4)

            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Text(summary.char_name ?? summary.display_name ?? summary.uuid.prefix(8).description)
                        .lineLimit(1)
                        .fontWeight(.medium)
                    if !summary.supported {
                        Image(systemName: "moon.zzz")
                            .foregroundStyle(.secondary)
                            .help("Edits for this game land in a later phase. You can still inspect the save.")
                    }
                }
                HStack(spacing: 6) {
                    GameBadge(game: summary.game)
                    if let scene = summary.scene_name {
                        Text(scene).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                    }
                }
                if let date = summary.saveDate {
                    Text(date, style: .date)
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
            }
        }
        .padding(.vertical, 2)
    }
}

private struct GameBadge: View {
    let game: String
    var body: some View {
        let (label, color) = badgeFor(game)
        Text(label)
            .font(.caption2)
            .padding(.horizontal, 6).padding(.vertical, 2)
            .background(color.opacity(0.20))
            .foregroundStyle(color)
            .cornerRadius(4)
    }
    private func badgeFor(_ g: String) -> (String, Color) {
        switch g {
        case "dragonfall": return ("Dragonfall", .orange)
        case "returns":    return ("Returns",    .blue)
        case "hongkong":   return ("Hong Kong",  .purple)
        default:           return (g, .gray)
        }
    }
}

private struct ThumbnailView: View {
    let path: String?
    let game: String

    var body: some View {
        if let p = path, let image = NSImage(contentsOfFile: p) {
            Image(nsImage: image)
                .resizable()
                .aspectRatio(contentMode: .fill)
        } else {
            Rectangle()
                .fill(Color.gray.opacity(0.18))
                .overlay(
                    Image(systemName: "photo")
                        .foregroundStyle(.tertiary)
                )
        }
    }
}
