//
//  WorldFlagsView.swift
//
//  The "basic list" world-flag editor (plan §3.1 two-tier UI, tier 2).
//  Flat searchable / filterable table. No semantic understanding of what
//  any flag does. Includes a prominent warning banner.
//

import SwiftUI

struct WorldFlagsView: View {
    let flags: [WorldFlagView]
    @EnvironmentObject var editor: EditorState

    @State private var search: String = ""
    @State private var activePrefix: String? = nil

    private var prefixes: [String] {
        // Auto-derive prefix chips from common patterns: anything matching
        // `<prefix>_*` where prefix is 2-15 chars. Group by the first segment
        // before the second underscore (e.g. "a1_Humanis_*" → "a1_Humanis").
        var counts: [String: Int] = [:]
        for f in flags {
            let parts = f.name.split(separator: "_")
            if parts.count >= 2 {
                let key = parts[0] + "_"
                counts[String(key), default: 0] += 1
            }
        }
        return counts.filter { $0.value >= 5 }
            .map(\.key)
            .sorted()
    }

    private var filtered: [WorldFlagView] {
        flags.filter { f in
            if let p = activePrefix, !f.name.hasPrefix(p) { return false }
            if !search.isEmpty,
               !f.name.localizedCaseInsensitiveContains(search) { return false }
            return true
        }
    }

    var body: some View {
        VStack(spacing: 0) {
            WarningBanner()

            HStack {
                TextField("Search flag names", text: $search)
                    .textFieldStyle(.roundedBorder)
                Text("\(filtered.count) of \(flags.count)")
                    .foregroundStyle(.secondary)
                    .font(.caption)
            }
            .padding(.horizontal, 12).padding(.vertical, 8)

            // Prefix chip strip (filters by prefix)
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 6) {
                    Chip(text: "all", active: activePrefix == nil) {
                        activePrefix = nil
                    }
                    ForEach(prefixes, id: \.self) { p in
                        Chip(text: p, active: activePrefix == p) {
                            activePrefix = (activePrefix == p) ? nil : p
                        }
                    }
                }
                .padding(.horizontal, 12).padding(.bottom, 6)
            }

            Divider()

            Table(filtered) {
                TableColumn("Flag", value: \.name) { f in
                    HStack {
                        Text(f.name).font(.system(.body, design: .monospaced))
                        if let s = f.scope_name, !s.isEmpty {
                            Text(s)
                                .font(.caption2)
                                .padding(.horizontal, 4).padding(.vertical, 1)
                                .background(Color.gray.opacity(0.18))
                                .cornerRadius(3)
                        }
                    }
                }
                TableColumn("Type") { f in
                    Text(f.kind)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                .width(60)
                TableColumn("Value") { f in
                    FlagValueEditor(flag: f)
                }
                .width(min: 160, ideal: 220)
            }
        }
    }
}

// MARK: - Warning banner

private struct WarningBanner: View {
    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "exclamationmark.triangle.fill")
                .foregroundStyle(.yellow)
            VStack(alignment: .leading, spacing: 2) {
                Text("Here be dragons").font(.headline)
                Text("Editing world flags can put your save into an inconsistent state. The editor doesn't know what most flags do — back up your save folder before mucking around.")
                    .font(.caption)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer()
        }
        .padding(12)
        .background(Color.yellow.opacity(0.12))
        .overlay(Rectangle().fill(Color.yellow.opacity(0.30)).frame(height: 1), alignment: .bottom)
    }
}

private struct Chip: View {
    let text: String
    let active: Bool
    let action: () -> Void
    var body: some View {
        Button(action: action) {
            Text(text)
                .font(.caption)
                .padding(.horizontal, 8).padding(.vertical, 3)
                .background(active ? Color.accentColor.opacity(0.30) : Color.gray.opacity(0.18))
                .cornerRadius(4)
        }
        .buttonStyle(.plain)
    }
}

// MARK: - Per-row editor

private struct FlagValueEditor: View {
    let flag: WorldFlagView
    @EnvironmentObject var editor: EditorState
    @State private var editingText: String = ""
    @State private var initialized = false

    var body: some View {
        switch flag.value {
        case .bool(let b):
            Toggle("", isOn: Binding(
                get: { b },
                set: { newValue in
                    Task { await editor.setWorldFlag(flag.name, kind: "bool", value: newValue) }
                }
            ))
            .labelsHidden()
            .toggleStyle(.switch)
        case .int(let v):
            HStack {
                TextField("", text: $editingText, onCommit: commitInt)
                    .textFieldStyle(.roundedBorder)
                    .frame(width: 110)
                    .onAppear { if !initialized { editingText = String(v); initialized = true } }
                    .onChange(of: v) { n in editingText = String(n) }
            }
        case .double(let v):
            HStack {
                TextField("", text: $editingText, onCommit: commitDouble)
                    .textFieldStyle(.roundedBorder)
                    .frame(width: 110)
                    .onAppear { if !initialized { editingText = String(v); initialized = true } }
                    .onChange(of: v) { n in editingText = String(n) }
            }
        case .string(let s):
            HStack {
                TextField("", text: $editingText, onCommit: commitString)
                    .textFieldStyle(.roundedBorder)
                    .onAppear { if !initialized { editingText = s; initialized = true } }
                    .onChange(of: s) { n in editingText = n }
            }
        case .empty:
            Text("—").foregroundStyle(.secondary)
        }
    }

    private func commitInt() {
        if let n = Int(editingText) {
            Task { await editor.setWorldFlag(flag.name, kind: "int", value: n) }
        }
    }
    private func commitDouble() {
        if let n = Double(editingText) {
            Task { await editor.setWorldFlag(flag.name, kind: "float", value: n) }
        }
    }
    private func commitString() {
        Task { await editor.setWorldFlag(flag.name, kind: "string", value: editingText) }
    }
}
