//
//  EditorState.swift
//
//  App-wide observable state. Owns the PythonBridge, the list of saves
//  found on disk, and the currently-open save (if any). UI views observe
//  this and call its async methods to drive edits.
//

import Foundation
import SwiftUI

@MainActor
final class EditorState: ObservableObject {

    // Bridge lifecycle
    @Published var bridgeStatus: BridgeStatus = .pending
    private var bridge: PythonBridge?

    // Save discovery
    @Published var allSaves: [SaveSummary] = []
    @Published var discoveredFolders: [String: [String]] = [:]
    @Published var loadingSaves: Bool = false

    // Currently open save
    @Published var openSave: OpenSave?

    // User-facing error to show in a banner
    @Published var lastError: String?

    enum BridgeStatus: Equatable {
        case pending
        case ready
        case missing(String)
        case crashed(String)
    }

    struct OpenSave: Equatable {
        var handle: Int
        var summary: SaveSummary
        var character: CharacterView?
        var worldFlags: [WorldFlagView]
        var pendingEdits: [PendingEdit]
        var diff: [String]
    }

    // MARK: - Bootstrap

    func bootstrap() async {
        guard let loc = BridgeLocation.resolve() else {
            bridgeStatus = .missing(
                """
                Could not find a Python 3 interpreter.

                Install the editor's Python package first. From the repo root:

                    python3 -m venv ~/.shadowrun-editor/venv
                    ~/.shadowrun-editor/venv/bin/pip install -e .

                Or set the SHADOWRUN_EDITOR_PYTHON environment variable to
                the python you want this app to use.
                """
            )
            return
        }
        do {
            let b = try PythonBridge(location: loc)
            try await b.ping()
            self.bridge = b
            self.bridgeStatus = .ready
            await rescanSaves()
        } catch let BridgeError.rpcError(code, message) {
            bridgeStatus = .crashed("Bridge error \(code): \(message)")
        } catch {
            bridgeStatus = .crashed(error.localizedDescription)
        }
    }

    // MARK: - Save discovery

    func rescanSaves() async {
        guard let b = bridge else { return }
        loadingSaves = true
        defer { loadingSaves = false }
        do {
            let discover = try await b.discoverFolders()
            self.discoveredFolders = discover.folders
            let scan = try await b.scanSaves(folders: nil)
            self.allSaves = scan.saves.sorted { lhs, rhs in
                // Most recent first; saves without a time go to the bottom.
                switch (lhs.time_utc, rhs.time_utc) {
                case let (a?, b?): return a > b
                case (.some, .none): return true
                case (.none, .some): return false
                case (.none, .none): return lhs.uuid < rhs.uuid
                }
            }
        } catch {
            lastError = error.localizedDescription
        }
    }

    // MARK: - Opening / closing a save

    func open(summary: SaveSummary) async {
        guard let b = bridge else { return }
        // Close any previously-open save first
        await closeCurrent()
        do {
            let r = try await b.openSave(path: summary.sav_path)
            openSave = OpenSave(
                handle: r.handle, summary: r.summary, character: r.character,
                worldFlags: r.world_flags, pendingEdits: r.pending_edits, diff: r.diff
            )
        } catch {
            lastError = error.localizedDescription
        }
    }

    func closeCurrent() async {
        guard let b = bridge, let handle = openSave?.handle else { return }
        try? await b.close(handle: handle)
        openSave = nil
    }

    // MARK: - Edit operations (all queue, none commit)

    private func apply(_ block: (PythonBridge, Int) async throws -> RefreshResponse) async {
        guard let b = bridge, let h = openSave?.handle else { return }
        do {
            let r = try await block(b, h)
            openSave = OpenSave(
                handle: h, summary: r.summary, character: r.character,
                worldFlags: r.world_flags, pendingEdits: r.pending_edits, diff: r.diff
            )
        } catch {
            lastError = error.localizedDescription
        }
    }

    func setEtiquette(_ name: String) async {
        await apply { try await $0.setEtiquette(handle: $1, etiquette: name) }
    }

    func setKarma(_ v: Int) async {
        await apply { try await $0.setKarma(handle: $1, value: v) }
    }

    func setNuyen(_ v: Int) async {
        await apply { try await $0.setNuyen(handle: $1, value: v) }
    }

    func setAttribute(_ attr: String, _ v: Int) async {
        await apply { try await $0.setAttribute(handle: $1, attr: attr, value: v) }
    }

    func setSkill(_ skill: String, _ v: Int) async {
        await apply { try await $0.setSkill(handle: $1, skill: skill, value: v) }
    }

    func setWorldFlag(_ name: String, kind: String, value: Any) async {
        await apply { try await $0.setWorldFlag(handle: $1, name: name, kind: kind, value: value) }
    }

    func undo() async {
        await apply { try await $0.undo(handle: $1) }
    }

    func clearPending() async {
        await apply { try await $0.clearPending(handle: $1) }
    }

    // MARK: - Commit

    struct CommitFailure: LocalizedError {
        let message: String
        var errorDescription: String? { message }
    }

    func commit() async -> Result<[String], CommitFailure> {
        guard let b = bridge, let h = openSave?.handle else {
            return .failure(CommitFailure(message: "No save open"))
        }
        do {
            let r = try await b.commit(handle: h)
            openSave = OpenSave(
                handle: h, summary: r.summary, character: r.character,
                worldFlags: r.world_flags, pendingEdits: r.pending_edits, diff: r.diff
            )
            return .success(r.written)
        } catch {
            return .failure(CommitFailure(message: error.localizedDescription))
        }
    }
}
