//
//  CharacterEditorView.swift
//
//  The rich character editor (plan §3.1 two-tier UI, tier 1). Forms-style
//  with steppers and dropdowns instead of raw text inputs. Etiquette is a
//  single-select dropdown because picking a new one in-game effectively
//  replaces the old one (you keep the rating).
//

import SwiftUI
import AppKit

// Canonical UI grouping for skills — only used to organize the form into
// Combat / Magic / Tech / Other. The actual list of skills rendered per
// group is filtered against the open save's `available_skills`, so e.g.
// Returns hides chi_casting / drone_combat / drain_resistance and the
// "Magic" group still appears for Returns (it has other entries) while
// any group that ends up empty after filtering is hidden entirely.
//
// Attribute and etiquette order come straight from the adapter's
// `available_*` lists (which preserve the engine's canonical order).
private let skillGroupingHint: [(String, [String])] = [
    ("Combat", [
        "ranged_combat", "close_combat", "throwing_weapons", "dodge",
    ]),
    ("Magic", [
        "spellcasting", "conjuring", "spirit_summoning", "spirit_control",
        "spirit_banishing", "magic_defense", "chi_casting", "drain_resistance",
    ]),
    ("Tech", [
        "decking", "deck_build_repair", "drone_control", "drone_combat",
        "drone_build_repair", "remote_gunnery", "biotech",
        // HK-only; sits with the other non-combat/non-magic skills. Only
        // appears when the open save's available_skills includes it.
        "cyberware_affinity",
    ]),
    // athletics/stealth/negotiation are deliberately absent: they're fields
    // in the Skills message but no game lets the player invest in them, so
    // the adapters exclude them from available_skills. Any skill the adapter
    // exposes that isn't named here still falls into a synthesized "Other"
    // group below, so nothing is ever silently hidden.
]

struct CharacterEditorView: View {
    let character: CharacterView?
    @EnvironmentObject var editor: EditorState

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                if let c = character {
                    headerSection(c)
                    resourcesSection(c)
                    etiquettesSection(c)
                    attributesSection(c)
                    skillsSection(c)
                    InventorySectionView(items: c.inventory)
                        .environmentObject(editor)
                    Spacer(minLength: 24)
                } else {
                    Text("No player character found in this save.")
                        .foregroundStyle(.secondary)
                        .padding()
                }
            }
            .padding(24)
            .frame(maxWidth: 720, alignment: .leading)
        }
        // Clicking blank space commits whatever field has focus by resigning
        // first responder. Without this the macOS default is "focus stays on
        // the text field until another control is clicked", which feels wrong
        // when the form has lots of blank background. Buttons / fields
        // consume the tap themselves, so this only fires on actual blank space.
        .contentShape(Rectangle())
        .onTapGesture {
            NSApp.keyWindow?.makeFirstResponder(nil)
        }
    }

    // MARK: - Sections

    private func headerSection(_ c: CharacterView) -> some View {
        HStack(spacing: 16) {
            PortraitPlaceholder(code: c.portrait_code, prefab: c.prefab)
                .frame(width: 80, height: 100)
                .cornerRadius(6)
            VStack(alignment: .leading, spacing: 4) {
                Text(c.name ?? "(unnamed)")
                    .font(.title)
                if let p = c.prefab {
                    Text(p).foregroundStyle(.secondary)
                }
                if let a = c.archetype {
                    Text(a).foregroundStyle(.secondary).font(.callout)
                }
                Text("\(c.snapshot_count) snapshot\(c.snapshot_count == 1 ? "" : "s") in save")
                    .font(.caption)
                    .foregroundStyle(.tertiary)
            }
            Spacer()
            VStack(spacing: 6) {
                Button {
                    Task { await editor.exportCharacter() }
                } label: {
                    Label("Export…", systemImage: "square.and.arrow.up")
                        .frame(maxWidth: .infinity)
                }
                .help("Save this character to a JSON template")
                Button {
                    Task { await editor.importCharacter() }
                } label: {
                    Label("Import…", systemImage: "square.and.arrow.down")
                        .frame(maxWidth: .infinity)
                }
                .help("Load a JSON template and queue its changes for review")
            }
            .buttonStyle(.bordered)
            .fixedSize()
        }
    }

    private func resourcesSection(_ c: CharacterView) -> some View {
        GroupBox("Resources") {
            Grid(alignment: .leading, horizontalSpacing: 12, verticalSpacing: 8) {
                GridRow {
                    Text("Unspent karma")
                    IntStepperField(
                        value: c.unspent_karma ?? 0,
                        range: 0...999_999,
                        onCommit: { v in Task { await editor.setKarma(v) } }
                    )
                    Text(c.karma.map { "(earned: \($0))" } ?? "")
                        .foregroundStyle(.secondary)
                }
                GridRow {
                    Text("Nuyen")
                    IntStepperField(
                        value: c.nuyen ?? 0,
                        range: 0...9_999_999,
                        onCommit: { v in Task { await editor.setNuyen(v) } }
                    )
                    Text("")
                }
            }
            .padding(.vertical, 4)
        }
    }

    private func etiquettesSection(_ c: CharacterView) -> some View {
        GroupBox("Etiquettes") {
            VStack(alignment: .leading, spacing: 4) {
                ForEach(c.available_etiquettes, id: \.self) { name in
                    let rating = c.etiquettes[name] ?? 0
                    let isActive = rating > 0
                    HStack {
                        Toggle(isOn: Binding(
                            get: { isActive },
                            set: { newValue in
                                Task {
                                    if newValue {
                                        await editor.addEtiquette(name)
                                    } else {
                                        await editor.removeEtiquette(name)
                                    }
                                }
                            }
                        )) {
                            Text(prettifyEtiquette(name))
                                .frame(minWidth: 140, alignment: .leading)
                        }
                        Spacer()
                        if isActive {
                            Text("rating \(rating)")
                                .foregroundStyle(.secondary)
                                .font(.caption)
                                .monospacedDigit()
                        }
                    }
                }
                Text("Check the etiquettes you want active. Enabling a new etiquette sets its rating to 1 — adjust further via in-game karma. Disabling drops the rating to 0.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .padding(.top, 6)
            }
            .padding(.vertical, 4)
        }
    }

    private func attributesSection(_ c: CharacterView) -> some View {
        GroupBox("Attributes") {
            VStack(alignment: .leading, spacing: 6) {
                ForEach(c.available_attributes, id: \.self) { name in
                    HStack {
                        Text(prettify(name))
                            .frame(minWidth: 120, alignment: .leading)
                        IntStepperField(
                            value: c.attributes[name] ?? 0,
                            range: -10...20,
                            onCommit: { v in Task { await editor.setAttribute(name, v) } }
                        )
                        Spacer()
                    }
                }
            }
            .padding(.vertical, 4)
        }
    }

    private func skillsSection(_ c: CharacterView) -> some View {
        // Filter each canonical group to skills actually available in this
        // game. A group whose every member got filtered out is dropped from
        // the form entirely. Any skill the adapter exposes but our grouping
        // hint doesn't mention gets collected into a trailing "Other" bucket
        // so we never silently hide a real skill.
        let availableSet = Set(c.available_skills)
        var grouped: [(String, [String])] = skillGroupingHint.compactMap { group, names in
            let filtered = names.filter { availableSet.contains($0) }
            return filtered.isEmpty ? nil : (group, filtered)
        }
        let placed = Set(grouped.flatMap { $0.1 })
        let leftover = c.available_skills.filter { !placed.contains($0) }
        if !leftover.isEmpty {
            // Merge into existing "Other" if present, otherwise add a new one.
            if let idx = grouped.firstIndex(where: { $0.0 == "Other" }) {
                grouped[idx] = ("Other", grouped[idx].1 + leftover)
            } else {
                grouped.append(("Other", leftover))
            }
        }
        return GroupBox("Skills") {
            VStack(alignment: .leading, spacing: 12) {
                ForEach(grouped, id: \.0) { group, skills in
                    Text(group).font(.headline).foregroundStyle(.secondary)
                    ForEach(skills, id: \.self) { name in
                        HStack {
                            Text(prettify(name))
                                .frame(minWidth: 180, alignment: .leading)
                            IntStepperField(
                                value: c.skills[name] ?? 0,
                                range: 0...12,
                                onCommit: { v in Task { await editor.setSkill(name, v) } }
                            )
                            Spacer()
                        }
                    }
                }
            }
            .padding(.vertical, 4)
        }
    }
}

// MARK: - Helpers

private func prettify(_ name: String) -> String {
    name.split(separator: "_").map { $0.capitalized }.joined(separator: " ")
}

private func prettifyEtiquette(_ name: String) -> String {
    name.prefix(1).uppercased() + name.dropFirst()
}

// MARK: - Inventory

/// Inventory editor section. Items arrive pre-sorted by the backend
/// (category display-order, then name); this view groups the contiguous
/// runs into labeled sections, lets the user adjust each stack's quantity
/// or delete it, and add new items by prefab id.
struct InventorySectionView: View {
    let items: [InventoryItem]
    @EnvironmentObject var editor: EditorState

    @State private var newItemPrefab: String = ""

    /// A contiguous run of items sharing a category, for a labeled subsection.
    private struct InventoryGroup: Identifiable {
        let category: String
        let title: String
        var items: [InventoryItem]
        var id: String { category }
    }

    // Group while preserving the backend's order. Same-category items are
    // already contiguous, so a single pass yields ordered groups.
    private var groups: [InventoryGroup] {
        var out: [InventoryGroup] = []
        for item in items {
            if !out.isEmpty, out[out.count - 1].category == item.category {
                out[out.count - 1].items.append(item)
            } else {
                out.append(InventoryGroup(category: item.category,
                                          title: item.categoryTitle,
                                          items: [item]))
            }
        }
        return out
    }

    var body: some View {
        GroupBox("Inventory") {
            VStack(alignment: .leading, spacing: 12) {
                if items.isEmpty {
                    Text("No items on this character.")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                } else {
                    ForEach(groups) { group in
                        Text(group.title)
                            .font(.headline)
                            .foregroundStyle(.secondary)
                        ForEach(group.items) { item in
                            itemRow(item)
                        }
                    }
                }
                Divider().padding(.vertical, 2)
                addItemRow
            }
            .padding(.vertical, 4)
        }
    }

    private func itemRow(_ item: InventoryItem) -> some View {
        HStack(spacing: 10) {
            Image(systemName: item.systemImage)
                .frame(width: 20)
                .foregroundStyle(.secondary)
            VStack(alignment: .leading, spacing: 1) {
                Text(item.display_name)
                if let sub = item.subtype {
                    Text(sub).font(.caption).foregroundStyle(.tertiary)
                }
            }
            .help(item.description ?? item.prefab)
            Spacer()
            IntStepperField(
                value: item.quantity,
                range: 1...99,
                onCommit: { v in Task { await editor.setItemQuantity(item.prefab, v) } }
            )
            Button(role: .destructive) {
                Task { await editor.removeItem(item.prefab) }
            } label: {
                Image(systemName: "trash")
            }
            .buttonStyle(.borderless)
            .help("Remove all \(item.display_name)")
        }
    }

    private var addItemRow: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 8) {
                TextField("Item prefab id (e.g. HealthPack_hi)", text: $newItemPrefab)
                    .textFieldStyle(.roundedBorder)
                    .onSubmit(submitNewItem)
                Button("Add", action: submitNewItem)
                    .disabled(newItemPrefab.trimmingCharacters(in: .whitespaces).isEmpty)
            }
            Text("Items are added by their engine prefab id. The game fills in stats, icon, and description from its own catalog at load — names here are derived heuristically since the content packs aren't bundled.")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }

    private func submitNewItem() {
        let prefab = newItemPrefab.trimmingCharacters(in: .whitespaces)
        guard !prefab.isEmpty else { return }
        Task { await editor.addItem(prefab) }
        newItemPrefab = ""
    }
}

struct IntStepperField: View {
    let value: Int
    let range: ClosedRange<Int>
    let onCommit: (Int) -> Void

    @State private var localText: String = ""
    @State private var initialized = false
    @FocusState private var isFocused: Bool

    var body: some View {
        HStack(spacing: 6) {
            TextField("", text: $localText)
                .textFieldStyle(.roundedBorder)
                .frame(width: 90)
                .focused($isFocused)
                .onSubmit { commitText() }
                .onChange(of: isFocused) { focused in
                    // Commit when focus leaves the field (clicking elsewhere,
                    // tabbing away, or pressing Esc) — not just on Return.
                    if !focused { commitText() }
                }
            Stepper("", value: Binding<Int>(
                get: { value },
                set: { newValue in
                    let clamped = max(range.lowerBound, min(range.upperBound, newValue))
                    if clamped != value {
                        onCommit(clamped)
                    }
                }
            ), in: range)
            .labelsHidden()
        }
        .onAppear {
            if !initialized {
                localText = String(value)
                initialized = true
            }
        }
        .onChange(of: value) { newValue in
            // External value change (from queue dedupe, undo, or refresh)
            // reflects back into the editing field.
            localText = String(newValue)
        }
    }

    private func commitText() {
        let parsed = Int(localText) ?? value
        let clamped = max(range.lowerBound, min(range.upperBound, parsed))
        localText = String(clamped)
        if clamped != value {
            onCommit(clamped)
        }
    }
}

struct PortraitPlaceholder: View {
    let code: String?
    let prefab: String?
    var body: some View {
        ZStack {
            Rectangle().fill(Color.gray.opacity(0.18))
            VStack(spacing: 4) {
                Image(systemName: "person.crop.square")
                    .font(.system(size: 28))
                    .foregroundStyle(.secondary)
                if let p = prefab {
                    Text(p).font(.caption2).foregroundStyle(.secondary).lineLimit(1)
                }
                if let c = code, !c.isEmpty {
                    Text(c).font(.system(.caption2, design: .monospaced)).foregroundStyle(.tertiary)
                }
            }
            .padding(4)
        }
    }
}
