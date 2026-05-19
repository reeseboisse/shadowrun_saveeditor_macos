//
//  PythonBridge.swift
//
//  Spawns `python3 -m shadowrun_editor.bridge` and exchanges newline-
//  delimited JSON-RPC requests over its stdin/stdout. The bridge is
//  long-lived for the lifetime of the app — one subprocess per launch.
//
//  Threading model:
//    • Stdout / stderr are drained via FileHandle.readabilityHandler,
//      which Foundation runs on a private background queue. This avoids
//      blocking Swift's cooperative thread pool on `availableData`.
//    • Response routing happens through an actor: the handler hops into
//      the actor via `Task { await self.appendStdout(data) }`.
//    • call() suspends on a CheckedContinuation keyed by request id;
//      the stdout reader resumes it when a matching line arrives.
//
//  Stderr is forwarded to NSLog with a [bridge] prefix.
//

import Foundation

enum BridgeError: LocalizedError {
    case launchFailed(String)
    case crashed(Int32)
    case rpcError(code: String, message: String)
    case decodeFailed(String)
    case writeFailed(String)

    var errorDescription: String? {
        switch self {
        case .launchFailed(let m): return "Could not launch Python bridge: \(m)"
        case .crashed(let s):       return "Python bridge exited with status \(s)"
        case .rpcError(_, let m):   return m
        case .decodeFailed(let m):  return "Could not decode bridge response: \(m)"
        case .writeFailed(let m):   return "Could not write to bridge stdin: \(m)"
        }
    }
}

/// Locates the Python interpreter and the source tree on disk.
struct BridgeLocation {
    var python: URL
    var sourceRoot: URL?

    static func resolve() -> BridgeLocation? {
        let env = ProcessInfo.processInfo.environment
        if let p = env["SHADOWRUN_EDITOR_PYTHON"], !p.isEmpty {
            return BridgeLocation(python: URL(fileURLWithPath: p),
                                  sourceRoot: env["SHADOWRUN_EDITOR_SRC"].map { URL(fileURLWithPath: $0) })
        }
        let home = FileManager.default.homeDirectoryForCurrentUser
        let venvPython = home.appendingPathComponent(".shadowrun-editor/venv/bin/python3")
        if FileManager.default.isExecutableFile(atPath: venvPython.path) {
            return BridgeLocation(python: venvPython, sourceRoot: nil)
        }
        for path in ["/usr/bin/python3",
                     "/opt/homebrew/bin/python3",
                     "/usr/local/bin/python3"] {
            if FileManager.default.isExecutableFile(atPath: path) {
                let src = env["SHADOWRUN_EDITOR_SRC"].map { URL(fileURLWithPath: $0) }
                return BridgeLocation(python: URL(fileURLWithPath: path), sourceRoot: src)
            }
        }
        return nil
    }
}

actor PythonBridge {
    private let process: Process
    private let stdinHandle: FileHandle
    private let stdoutPipe: Pipe
    private let stderrPipe: Pipe

    private var nextID: Int = 1
    private var pendingResponses: [Int: CheckedContinuation<Data, Error>] = [:]
    private var stdoutBuffer = Data()
    private var isAlive = true

    init(location: BridgeLocation) throws {
        let p = Process()
        p.executableURL = location.python
        p.arguments = ["-u", "-m", "shadowrun_editor.bridge"]

        var env = ProcessInfo.processInfo.environment
        env["PYTHONUNBUFFERED"] = "1"
        if let src = location.sourceRoot {
            let existing = env["PYTHONPATH"] ?? ""
            env["PYTHONPATH"] = existing.isEmpty ? src.path : "\(src.path):\(existing)"
        }
        p.environment = env

        let stdinPipe = Pipe()
        let stdoutPipe = Pipe()
        let stderrPipe = Pipe()
        p.standardInput = stdinPipe
        p.standardOutput = stdoutPipe
        p.standardError = stderrPipe

        NSLog("[bridge] launching: %@ %@",
              location.python.path,
              (p.arguments ?? []).joined(separator: " "))

        p.terminationHandler = { proc in
            NSLog("[bridge] process exited: status=%d reason=%d",
                  proc.terminationStatus, proc.terminationReason.rawValue)
        }

        do {
            try p.run()
        } catch {
            throw BridgeError.launchFailed(error.localizedDescription)
        }

        self.process = p
        self.stdinHandle = stdinPipe.fileHandleForWriting
        self.stdoutPipe = stdoutPipe
        self.stderrPipe = stderrPipe

        // Install readability handlers. These fire on Foundation's
        // private background queue whenever data is available; the
        // closures forward into the actor via async Tasks.
        let weakSelf = WeakRef(self)
        stdoutPipe.fileHandleForReading.readabilityHandler = { handle in
            let data = handle.availableData
            if data.isEmpty {
                // EOF from the subprocess. Detach the handler so we
                // don't keep firing.
                handle.readabilityHandler = nil
                if let ref = weakSelf.value {
                    Task { await ref.handleEOF() }
                }
                return
            }
            if let ref = weakSelf.value {
                Task { await ref.appendStdout(data) }
            }
        }
        stderrPipe.fileHandleForReading.readabilityHandler = { handle in
            let data = handle.availableData
            if data.isEmpty {
                handle.readabilityHandler = nil
                return
            }
            if let s = String(data: data, encoding: .utf8), !s.isEmpty {
                NSLog("[bridge] stderr: %@", s)
            }
        }
    }

    deinit {
        stdoutPipe.fileHandleForReading.readabilityHandler = nil
        stderrPipe.fileHandleForReading.readabilityHandler = nil
        if process.isRunning {
            process.terminate()
        }
    }

    // MARK: - Reader side

    private func appendStdout(_ data: Data) {
        stdoutBuffer.append(data)
        while let nl = stdoutBuffer.firstIndex(of: 0x0A) {
            let line = stdoutBuffer.subdata(in: 0..<nl)
            stdoutBuffer.removeSubrange(0...nl)
            handleLine(line)
        }
    }

    private func handleLine(_ data: Data) {
        guard let envelope = try? JSONDecoder().decode(ResponseEnvelope.self, from: data) else {
            NSLog("[bridge] could not parse response line: %@",
                  String(data: data, encoding: .utf8) ?? "<binary>")
            return
        }
        guard let id = envelope.id, let continuation = pendingResponses.removeValue(forKey: id) else {
            NSLog("[bridge] response for unknown id=%@: %@",
                  envelope.id.map(String.init) ?? "nil",
                  String(data: data, encoding: .utf8) ?? "<binary>")
            return
        }
        if let err = envelope.error {
            continuation.resume(throwing: BridgeError.rpcError(code: err.code, message: err.message))
        } else {
            continuation.resume(returning: data)
        }
    }

    private func handleEOF() {
        isAlive = false
        NSLog("[bridge] EOF on stdout. Failing %d pending request(s).",
              pendingResponses.count)
        let pending = pendingResponses
        pendingResponses.removeAll()
        for (_, c) in pending {
            c.resume(throwing: BridgeError.crashed(process.terminationStatus))
        }
    }

    private struct ResponseEnvelope: Decodable {
        var id: Int?
        var error: ErrorBody?
        struct ErrorBody: Decodable { var code: String; var message: String }
    }

    private struct ResultWrapper<T: Decodable>: Decodable {
        var id: Int
        var result: T
    }

    // MARK: - Public RPC

    func call<R: Decodable>(_ method: String, params: [String: Any]) async throws -> R {
        guard isAlive else { throw BridgeError.crashed(process.terminationStatus) }
        let id = nextID; nextID += 1
        let envelope: [String: Any] = ["id": id, "method": method, "params": params]
        let payload = try JSONSerialization.data(withJSONObject: envelope, options: [])

        NSLog("[bridge] -> id=%d method=%@", id, method)

        let line = payload + Data([0x0A])
        do {
            try stdinHandle.write(contentsOf: line)
        } catch {
            throw BridgeError.writeFailed(error.localizedDescription)
        }

        let body: Data = try await withCheckedThrowingContinuation { continuation in
            pendingResponses[id] = continuation
        }
        NSLog("[bridge] <- id=%d (%d bytes)", id, body.count)

        do {
            let wrapper = try JSONDecoder().decode(ResultWrapper<R>.self, from: body)
            return wrapper.result
        } catch {
            throw BridgeError.decodeFailed("\(error) - body=\(String(data: body, encoding: .utf8) ?? "?")")
        }
    }

    // MARK: - Method conveniences

    func ping() async throws { let _: PingResult = try await call("ping", params: [:]) }
    struct PingResult: Decodable { var ok: Bool; var version: String }

    func discoverFolders() async throws -> DiscoverResponse {
        try await call("discover_save_folders", params: [:])
    }

    func discoverDiagnostics() async throws -> DiagnosticsResponse {
        try await call("discover_diagnostics", params: [:])
    }

    func scanSaves(folders: [String]? = nil) async throws -> ScanResponse {
        var params: [String: Any] = [:]
        if let f = folders { params["folders"] = f }
        return try await call("scan_saves", params: params)
    }

    func openSave(path: String) async throws -> OpenSaveResponse {
        try await call("open_save", params: ["path": path])
    }

    func setEtiquette(handle: Int, etiquette: String) async throws -> RefreshResponse {
        try await call("set_etiquette", params: ["handle": handle, "etiquette": etiquette])
    }

    func setKarma(handle: Int, value: Int) async throws -> RefreshResponse {
        try await call("set_karma", params: ["handle": handle, "value": value])
    }

    func setNuyen(handle: Int, value: Int) async throws -> RefreshResponse {
        try await call("set_nuyen", params: ["handle": handle, "value": value])
    }

    func setAttribute(handle: Int, attr: String, value: Int) async throws -> RefreshResponse {
        try await call("set_attribute", params: ["handle": handle, "attribute": attr, "value": value])
    }

    func setSkill(handle: Int, skill: String, value: Int) async throws -> RefreshResponse {
        try await call("set_skill", params: ["handle": handle, "skill": skill, "value": value])
    }

    func setWorldFlag(handle: Int, name: String, kind: String, value: Any) async throws -> RefreshResponse {
        try await call("set_world_flag",
                       params: ["handle": handle, "name": name, "kind": kind, "value": value])
    }

    func undo(handle: Int) async throws -> RefreshResponse {
        try await call("undo", params: ["handle": handle])
    }

    func clearPending(handle: Int) async throws -> RefreshResponse {
        try await call("clear_pending", params: ["handle": handle])
    }

    func commit(handle: Int) async throws -> CommitResponse {
        try await call("commit", params: ["handle": handle])
    }

    func close(handle: Int) async throws {
        let _: [String: Bool] = try await call("close", params: ["handle": handle])
    }
}

/// Weak reference helper so the readabilityHandler doesn't strongly
/// retain the actor (Foundation's pipe queue would otherwise hold the
/// bridge alive forever).
private final class WeakRef<T: AnyObject>: @unchecked Sendable {
    weak var value: T?
    init(_ value: T) { self.value = value }
}
