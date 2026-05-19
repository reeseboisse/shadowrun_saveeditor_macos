//
//  UnsupportedGameView.swift
//
//  Phase-2 placeholder shown when a Returns or Hong Kong save is
//  selected. The picker remains game-neutral; this view just explains
//  the timing without making it feel like an error. Per plan §9 Phase 2.
//

import SwiftUI

struct UnsupportedGameView: View {
    let summary: SaveSummary

    var body: some View {
        VStack(spacing: 16) {
            Image(systemName: "moon.stars.fill")
                .font(.system(size: 64))
                .foregroundStyle(.purple)
            Text(summary.gameDisplayName)
                .font(.title2).bold()
            Text(messageForGame)
                .multilineTextAlignment(.center)
                .foregroundStyle(.secondary)
                .frame(maxWidth: 460)
            if let name = summary.char_name {
                Text("Save: \(name)")
                    .font(.callout)
                    .foregroundStyle(.tertiary)
            }
            Text(summary.uuid)
                .font(.system(.caption, design: .monospaced))
                .foregroundStyle(.tertiary)
        }
        .padding(48)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private var messageForGame: String {
        switch summary.game {
        case "returns":
            return "Editing Shadowrun Returns saves lands in Phase 3. The save is recognized and parses cleanly — only the edit operations are still wiring up."
        case "hongkong":
            return "Editing Shadowrun: Hong Kong saves lands in Phase 4. Hong Kong introduces cyberware essence accounting that needs its own UI section."
        default:
            return "This save's game is recognized but not yet wired up for editing."
        }
    }
}
