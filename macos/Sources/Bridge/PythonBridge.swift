//
//  PythonBridge.swift
//
//  Spawns `python3 -m shadowrun_editor.bridge` and exchanges newline-
//  delimited JSON-RPC requests over its stdin/stdout. The bridge is
//  long-lived for the lifetime of the app — one subprocess per launch.
//
//  Threading: this class is an actor so the request/response cycle is
//  serialized. Each call() suspends until its response line arrives.
//  Stderr is forwarded to NSLog for diagnostics.
//

import Foundation

enum BridgeError: LocalizedError {
    case launchFailed(String)
    case crashed(Int32)
    case rpcError(code: String, message: String)
    case decodeFailed(String)

    var errorDescription: String? {
        switch self {
        case .launchFailed(let m): return "Could not launch Python bridge: \(m)"
        case .crashed(let s):       return "Python bridge exited with status \(s)"
        case .rpcError(_, let m):   return m
        case .decodeFailed(let m):  return "Could not decode bridge response: \(m)"
        }
    }
}

/// Locates the Python interpreter and the source tree on disk.
///
/// Resolution order (first hit wins):
///   1. $SHADOWRUN_EDITOR_PYTHON env var (full python path)
///   2. ~/.shadowrun-editor/venv/bin/python3 (the recommended local install)
///   3. /usr/bin/python3 (system python — works for dev if the package
///      is on PYTHONPATH)
///   4. /opt/homebrew/bin/python3, /usr/local/bin/python3 (brew installs)
struct BridgeLocation {
    var python: URL
    var sourceRoot: URL?   // PYTHONPATH addition (the `src` folder), if known

    static func resolve() -> BridgeLocation? {
        let env = ProcessInfo.processInfo.environment

        // 1. Explicit override
        if let p = env["SHADOWRUN_EDITOR_PYTHON"], !p.isEmpty {
            return BridgeLocation(python: URL(fileURLWithPath: p),
                                  sourceRoot: env["SHADOWRUN_EDITOR_SRC"].map { URL(fileURLWithPath: $0) })
        }

        // 2. Local virtualenv
        let home = FileManager.default.homeDirectoryForCurrentUser
        let venvPython = home.appendingPathComponent(".shadowrun-editor/venv/bin/python3")
        if FileManager.default.isExecutableFile(atPath: venvPython.path) {
            return BridgeLocation(python: venvPython, sourceRoot: nil)
        }

        // 3+4. Common system locations
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
    private let stdoutHandle: FileHandle
    private let stderrHandle: FileHandle

    private var nextID: Int = 1
    private var pendingResponses: [Int: CheckedContinuation<Data, Error>] = [:]
    private var stdoutBuffer = Data()
    private var readerTask: Task<Void, Never>?

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

        do {
            try p.run()
        } catch {
            throw BridgeError.launchFailed(error.localizedDescription)
        }

        self.process = p
        self.stdinHandle = stdinPipe.fileHandleForWriting
        self.stdoutHandle = stdoutPipe.fileHandleForReading
        self.stderrHandle = stderrPipe.fileHandleForReading

        Task { [weak self] in
            await self?.startReaders()
        }
    }

    deinit {
        readerTask?.cancel()
        if process.isRunning {
            process.terminate()
        }
    }

    // MARK: - Reader loop

    private func startReaders() {
        readerTask = Task { [stdoutHandle, stderrHandle] in
            // Stdout: framed JSON lines
            let stdoutTask = Task {
                let bufSize = 4096
                while !Task.isCancelled {
                    let data = stdoutHandle.availableData
                    if data.isEmpty { break }
                    await self.appendStdout(data)
                }
            }
            // Stderr: forward to NSLog with the [bridge] prefix
            let stderrTask = Task {
                while !Task.isCancelled {
                    let data = stderrHandle.availableData
                    if data.isEmpty { break }
                    if let s = String(data: data, encoding: .utf8) {
                        NSLog("[bridge] %@", s)
                    }
                }
            }
            _ = await stdoutTask.value
            _ = await stderrTask.value
        }
    }

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
            NSLog("[bridge] could not parse line: %@",
                  String(data: data, encoding: .utf8) ?? "<binary>")
            return
        }
        guard let id = envelope.id, let continuation = pendingResponses.removeValue(forKey: id) else {
            return
        }
        if let err = envelope.error {
            continuation.resume(throwing: BridgeError.rpcError(code: err.code, message: err.message))
        } else {
            continuation.resume(returning: data)
        }
    }

    private struct ResponseEnvelope: Decodable {
        var id: Int?
        var error: ErrorBody?
        struct ErrorBody: Decodable { var code: String; var message: String }
    }

    /// Wraps `{ "id": ..., "result": T }` for decoding. Declared at file
    /// scope (not inside `call`) because Swift can't nest a generic type
    /// declaration inside a generic function.
    private struct ResultWrapper<T: Decodable>: Decodable {
        var id: Int
        var result: T
    }

    // MARK: - Public RPC

    func call<R: Decodable>(_ method: String, params: [String: Any]) async throws -> R {
        let id = nextID; nextID += 1
        let envelope: [String: Any] = ["id": id, "method": method, "params": params]
        let payload = try JSONSerialization.data(withJSONObject: envelope, options: [])

        let line = payload + Data([0x0A])
        try stdinHandle.write(contentsOf: line)

        let body: Data = try await withCheckedThrowingContinuation { continuation in
            pendingResponses[id] = continuation
        }

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
