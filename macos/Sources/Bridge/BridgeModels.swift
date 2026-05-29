//
//  BridgeModels.swift
//
//  Codable mirrors of the Python service.py dataclasses. The JSON-RPC
//  bridge serializes those dataclasses with dataclasses.asdict, so the
//  field names here match the Python field names exactly.
//

import Foundation

// MARK: - Save list

struct SaveSummary: Codable, Identifiable, Hashable {
    var uuid: String
    var folder: String
    var sav_path: String
    var thumbnail_path: String?
    var game: String
    var supported: Bool
    var display_name: String?
    var char_name: String?
    var time_utc: Int64?
    var scene_name: String?

    var id: String { sav_path }

    var gameDisplayName: String {
        switch game {
        case "dragonfall": return "Shadowrun: Dragonfall — Director's Cut"
        case "returns":    return "Shadowrun Returns"
        case "hongkong":   return "Shadowrun: Hong Kong"
        default:            return "Shadowrun (unknown variant)"
        }
    }

    var saveDate: Date? {
        guard let t = time_utc else { return nil }
        // The game stores .NET DateTime.Ticks (100-ns intervals since
        // 0001-01-01). Convert to a Unix Date.
        let unixTicks: Int64 = 621355968000000000
        let secondsSinceEpoch = Double(t - unixTicks) / 1e7
        return Date(timeIntervalSince1970: secondsSinceEpoch)
    }
}

// MARK: - Character editor view model

/// One inventory stack. `prefab` is the engine id edits target; the rest is
/// presentation derived by the Python catalog (no .cpack data is bundled, so
/// these are heuristics — see catalog.py).
struct InventoryItem: Codable, Hashable, Identifiable {
    var prefab: String
    var display_name: String
    var category: String
    var subtype: String?
    var quantity: Int
    var description: String?

    var id: String { prefab }

    /// SF Symbol for the item's category. Falls back to a generic cube.
    var systemImage: String {
        switch category {
        case "weapon":     return "scope"
        case "grenade":    return "burst.fill"
        case "spell":      return "sparkles"
        case "foci":       return "wand.and.stars"
        case "medkit":     return "cross.case.fill"
        case "consumable": return "pills.fill"
        case "drone":      return "airplane"
        case "outfit":     return "tshirt.fill"
        case "service":    return "phone.fill"
        case "totem":      return "pawprint.fill"
        case "kit":        return "shippingbox.fill"
        default:            return "cube.box.fill"
        }
    }

    /// Human-friendly section header for the category.
    var categoryTitle: String {
        switch category {
        case "weapon":     return "Weapons"
        case "grenade":    return "Grenades"
        case "spell":      return "Spells"
        case "foci":       return "Foci"
        case "medkit":     return "Medkits"
        case "consumable": return "Consumables"
        case "drone":      return "Drones"
        case "outfit":     return "Outfits"
        case "service":    return "Services"
        case "totem":      return "Totems"
        case "kit":        return "Starter Kits"
        default:            return "Other Items"
        }
    }
}

struct CharacterView: Codable, Hashable {
    var name: String?
    var prefab: String?
    var archetype: String?
    var portrait_code: String?
    var karma: Int?
    var unspent_karma: Int?
    var nuyen: Int?
    var alice_fund: Int?
    var attributes: [String: Int]
    var skills: [String: Int]
    var etiquettes: [String: Int]
    var inventory: [InventoryItem]
    // Game-specific editor surfaces. Returns excludes paranormal/infected
    // etiquettes and chi_casting/drone_combat/drain_resistance skills;
    // Dragonfall excludes paranormal/infected and chi_casting. The UI
    // iterates these instead of any hardcoded list.
    var available_etiquettes: [String]
    var available_attributes: [String]
    var available_skills: [String]
    var snapshot_count: Int
}

// MARK: - World flags

/// A world flag's value can be any of int / bool / float / string. To keep
/// the Codable surface ergonomic we model that as a tagged enum and
/// decode `value` based on `kind`.
enum WorldFlagValue: Hashable {
    case int(Int)
    case bool(Bool)
    case double(Double)
    case string(String)
    case empty
}

struct WorldFlagView: Codable, Identifiable, Hashable {
    var name: String
    var kind: String
    var value: WorldFlagValue
    var scope_name: String?

    var id: String { name }

    enum CodingKeys: String, CodingKey {
        case name, kind, value, scope_name
    }

    init(name: String, kind: String, value: WorldFlagValue, scope_name: String?) {
        self.name = name; self.kind = kind; self.value = value; self.scope_name = scope_name
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        name = try c.decode(String.self, forKey: .name)
        // Lenient decoding: a malformed or partial flag entry (missing
        // kind/value) should degrade to "unknown / —" rather than fail the
        // entire response. The picker has hundreds of flags per save and a
        // single weird one shouldn't blank out the whole list.
        kind = (try? c.decode(String.self, forKey: .kind)) ?? "unknown"
        scope_name = (try? c.decodeIfPresent(String.self, forKey: .scope_name)) ?? nil
        switch kind {
        case "int":    value = .int((try? c.decode(Int.self, forKey: .value)) ?? 0)
        case "bool":   value = .bool((try? c.decode(Bool.self, forKey: .value)) ?? false)
        case "float":  value = .double((try? c.decode(Double.self, forKey: .value)) ?? 0)
        case "string": value = .string((try? c.decode(String.self, forKey: .value)) ?? "")
        default:       value = .empty
        }
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(name, forKey: .name)
        try c.encode(kind, forKey: .kind)
        try c.encodeIfPresent(scope_name, forKey: .scope_name)
        switch value {
        case .int(let v):    try c.encode(v, forKey: .value)
        case .bool(let v):   try c.encode(v, forKey: .value)
        case .double(let v): try c.encode(v, forKey: .value)
        case .string(let v): try c.encode(v, forKey: .value)
        case .empty:         try c.encodeNil(forKey: .value)
        }
    }

    var displayValue: String {
        switch value {
        case .int(let v):    return String(v)
        case .bool(let v):   return v ? "true" : "false"
        case .double(let v): return String(v)
        case .string(let v): return "\"\(v)\""
        case .empty:         return "—"
        }
    }
}

// MARK: - Pending edits

struct PendingEdit: Codable, Identifiable, Hashable {
    var op: String
    var description: String

    var id: String { description }

    enum CodingKeys: String, CodingKey {
        case op, description, args
    }

    init(op: String, description: String) {
        self.op = op
        self.description = description
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        op = try c.decode(String.self, forKey: .op)
        description = try c.decode(String.self, forKey: .description)
        // `args` is opaque to the UI; we don't decode it
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(op, forKey: .op)
        try c.encode(description, forKey: .description)
    }
}

// MARK: - Bridge response shape

struct OpenSaveResponse: Codable {
    var handle: Int
    var summary: SaveSummary
    var character: CharacterView?
    var world_flags: [WorldFlagView]
    var pending_edits: [PendingEdit]
    var diff: [String]
}

struct RefreshResponse: Codable {
    var summary: SaveSummary
    var character: CharacterView?
    var world_flags: [WorldFlagView]
    var pending_edits: [PendingEdit]
    var diff: [String]
}

struct CommitResponse: Codable {
    var written: [String]
    var summary: SaveSummary
    var character: CharacterView?
    var world_flags: [WorldFlagView]
    var pending_edits: [PendingEdit]
    var diff: [String]
}

struct ScanResponse: Codable {
    var saves: [SaveSummary]
}

// MARK: - Character template import / export

struct ExportCharacterResponse: Codable {
    var json: String        // the template, pretty-printed; written to disk verbatim
    var name: String?
    var game: String
}

struct ImportReport: Codable {
    var applied: [String]
    var skipped: [String]
}

/// Same shape as RefreshResponse plus the import report. Import queues the
/// template's edits; they show in the pending list like any other edit.
struct ImportCharacterResponse: Codable {
    var summary: SaveSummary
    var character: CharacterView?
    var world_flags: [WorldFlagView]
    var pending_edits: [PendingEdit]
    var diff: [String]
    var import_report: ImportReport?
}

struct DiscoverResponse: Codable {
    var folders: [String: [String]]
}

struct DiagnosticsCandidate: Codable, Identifiable {
    var game: String
    var raw: String
    var expanded: String
    var exists: Bool
    var is_dir: Bool
    var sav_count: Int
    var error: String?

    var id: String { expanded }
}

struct DiagnosticsResponse: Codable {
    var home: String
    var cwd: String
    var candidates: [DiagnosticsCandidate]
}
