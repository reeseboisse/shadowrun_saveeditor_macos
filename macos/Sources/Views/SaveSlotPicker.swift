//
//  SaveSlotPicker.swift
//
//  Game-neutral list of every save the bridge found on disk. Saves from
//  all three games appear in a single list with the game shown as a
//  label, NOT as a sectioning hierarchy that implies precedence (per
//  plan §3.1 game-neutrality requirement).
//

import SwiftUI

struct SaveSlotPicker: View {
    @EnvironmentObject var editor: EditorState
    @State private var search = ""

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

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                TextField("Search saves", text: $search)
                    .textFieldStyle(.roundedBorder)
                Button {
                    Task { await editor.rescanSaves() }
                } label: {
                    Image(systemName: editor.loadingSaves ? "arrow.triangle.2.circlepath.circle" : "arrow.clockwise")
                }
                .help("Rescan save folders")
            }
            .padding(8)
            Divider()

            if editor.allSaves.isEmpty && !editor.loadingSaves {
                EmptyState(discovered: editor.discoveredFolders)
            } else {
                List(filtered, selection: Binding<SaveSummary.ID?>(
                    get: { editor.openSave?.summary.id },
                    set: { id in
                        guard let id, let s = editor.allSaves.first(where: { $0.id == id }) else { return }
                        Task { await editor.open(summary: s) }
                    })
                ) { s in
                    SaveSlotRow(summary: s)
                        .tag(s.id)
                }
                .listStyle(.sidebar)
            }
        }
    }
}

private struct EmptyState: View {
    let discovered: [String: [String]]
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("No saves found.")
                .font(.headline)
            if discovered.isEmpty {
                Text("The editor looks for Shadowrun saves under ~/Library/Application Support/Harebrained Schemes/ — none of the expected folders exist on this machine yet.")
                    .foregroundStyle(.secondary)
            } else {
                Text("Searched folders:")
                    .foregroundStyle(.secondary)
                ForEach(discovered.keys.sorted(), id: \.self) { game in
                    if let folders = discovered[game] {
                        ForEach(folders, id: \.self) { f in
                            Text(f).font(.system(.caption, design: .monospaced))
                        }
                    }
                }
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
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
