//
//  CharacterEditorView.swift
//
//  The rich character editor (plan §3.1 two-tier UI, tier 1). Forms-style
//  with steppers and dropdowns instead of raw text inputs. Etiquette is a
//  single-select dropdown because picking a new one in-game effectively
//  replaces the old one (you keep the rating).
//

import SwiftUI

private let attributeOrder = [
    "body", "quickness", "strength", "charisma",
    "intelligence", "willpower", "essence", "magic", "reaction",
]

private let skillGroups: [(String, [String])] = [
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
    ]),
    ("Other", [
        "athletics", "stealth", "negotiation",
    ]),
]

private let etiquetteNames = [
    "corporate", "security", "gang", "paranormal", "socialite",
    "infected", "shadowrunner", "street", "academic",
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
                ForEach(etiquetteNames, id: \.self) { name in
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
                ForEach(attributeOrder, id: \.self) { name in
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
        GroupBox("Skills") {
            VStack(alignment: .leading, spacing: 12) {
                ForEach(skillGroups, id: \.0) { group, skills in
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

struct IntStepperField: View {
    let value: Int
    let range: ClosedRange<Int>
    let onCommit: (Int) -> Void

    @State private var localText: String = ""
    @State private var localValue: Int = 0
    @State private var initialized = false

    var body: some View {
        HStack(spacing: 6) {
            TextField("", text: $localText, onCommit: commit)
                .textFieldStyle(.roundedBorder)
                .frame(width: 90)
            Stepper(value: $localValue, in: range, onEditingChanged: { _ in commit() }) {
                EmptyView()
            }
            .labelsHidden()
        }
        .onAppear {
            if !initialized {
                localValue = value
                localText = String(value)
                initialized = true
            }
        }
        .onChange(of: value) { newValue in
            localValue = newValue
            localText = String(newValue)
        }
        .onChange(of: localValue) { newValue in
            localText = String(newValue)
        }
    }

    private func commit() {
        let parsed = Int(localText) ?? localValue
        let clamped = max(range.lowerBound, min(range.upperBound, parsed))
        if clamped != value {
            onCommit(clamped)
        } else if String(clamped) != localText {
            localText = String(clamped)
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
