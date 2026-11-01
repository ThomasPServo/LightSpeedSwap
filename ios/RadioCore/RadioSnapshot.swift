import Foundation
import CoreFoundation

public struct Observation: Codable, Identifiable, Equatable {
    public var kind: String
    public var timestamp: String?
    public var slot: String = "Unknown"
    public var rat: String?
    public var band: Int?
    public var role: String?
    public var rsrp: Double?
    public var rsrq: Double?
    public var sinr: Double?
    public var arfcn: Int?
    public var bandwidth: String?
    public var apn: String?
    public var interface: String?
    public var flow: Int?
    public var bearer: Int?
    public var tx: Int?
    public var rx: Int?
    public var qos_kind: String?
    public var network_mode: String?
    public var state: String?
    public var id: String { [kind, slot, rat ?? "", String(band ?? -1), String(arfcn ?? -1), role ?? "", apn ?? "", interface ?? "", String(flow ?? -1), String(bearer ?? -1)].joined(separator: "|") }
    public var bandLabel: String {
        guard let band, (1...1024).contains(band) else { return "Unknown" }
        return rat == "NR" ? "n\(band)" : rat == "LTE" ? "B\(band)" : "Band \(band) · RAT unknown"
    }
    public var isServing: Bool { ["Serving", "Primary", "Secondary"].contains(role ?? "") }
    public var validQoS: Bool { [tx, rx].contains { $0.map { (1...255).contains($0) } ?? false } }
    public func isRecent(at now: Date = Date(), seconds: Double = 30) -> Bool {
        guard let timestamp, let date = RadioSnapshot.date(timestamp) else { return false }
        let age = now.timeIntervalSince(date)
        return age >= -2 && age <= seconds
    }
}

public struct Capability: Codable, Identifiable, Equatable {
    public var name: String
    public var state: String
    public var detail: String?
    public var id: String { name }
}

public struct RadioSnapshot: Codable {
    public var schema = "lightspeedswap.radio.v1"
    public var source: String
    public var status: String
    public var detail: String
    public var generated_at: String
    public var observations: [Observation]
    public var capabilities: [Capability]?
    public var coverage: String?

    public static func date(_ string: String) -> Date? {
        let fractional = ISO8601DateFormatter()
        fractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return fractional.date(from: string) ?? ISO8601DateFormatter().date(from: string)
    }
    public static func iso(_ date: Date = Date()) -> String { ISO8601DateFormatter().string(from: date) }
    public var isLiveSource: Bool { source == "on-device" || (source == "live" && status == "streaming") }
    public func currentServing(at now: Date = Date()) -> [Observation] {
        guard isLiveSource else { return [] }
        return observations.filter { $0.kind == "cell" && $0.isServing && $0.isRecent(at: now) && ["LTE", "NR"].contains($0.rat ?? "") }
    }
    public static func empty() -> RadioSnapshot {
        RadioSnapshot(source: "none", status: "idle", detail: "Read this iPhone to check which measurements iOS exposes.", generated_at: iso(), observations: [])
    }
    public mutating func makeHistorical() {
        source = "historical"; status = "imported"
        detail = "Saved observations. They do not describe the current connection."
    }
    public static func decodeReport(_ data: Data) throws -> RadioSnapshot {
        guard data.count <= 8 * 1024 * 1024 else { throw RadioError.invalidReport }
        var result = try JSONDecoder().decode(Self.self, from: data)
        guard result.schema == "lightspeedswap.radio.v1", result.observations.count <= 1024 else { throw RadioError.invalidReport }
        result.observations = result.observations.filter {
            if $0.kind == "qos" { return $0.validQoS }
            if $0.kind == "cell" { return $0.band.map { (1...1024).contains($0) } ?? false }
            return false
        }
        var seen = Set<String>()
        result.observations = result.observations.reversed().filter { seen.insert($0.id).inserted }.reversed()
        return result
    }
}

public enum RadioError: LocalizedError {
    case invalidReport, invalidEndpoint, missingToken, connectionFailed
    public var errorDescription: String? {
        switch self {
        case .invalidReport: return "This is not a supported LightSpeed Radio report (maximum 8 MB)."
        case .invalidEndpoint: return "Use the local IPv4 sharing link printed by the Mac dashboard."
        case .missingToken: return "The sharing link needs its #token= access key."
        case .connectionFailed: return "The Mac collector is unavailable. Check its sharing link and the local-network permission."
        }
    }
}

public struct CollectorEndpoint {
    public let url: URL
    public let token: String
    public init(_ text: String) throws {
        guard var parts = URLComponents(string: text.trimmingCharacters(in: .whitespacesAndNewlines)),
              ["http", "https"].contains(parts.scheme ?? ""), parts.user == nil, parts.password == nil,
              parts.query == nil, let host = parts.host else { throw RadioError.invalidEndpoint }
        let octets = host.split(separator: ".").compactMap { Int($0) }
        guard octets.count == 4, octets.allSatisfy({ (0...255).contains($0) }),
              octets.map(String.init).joined(separator: ".") == host,
              octets[0] == 10 || (octets[0] == 192 && octets[1] == 168) ||
                (octets[0] == 172 && (16...31).contains(octets[1])) || octets[0] == 127 else { throw RadioError.invalidEndpoint }
        guard let fragment = parts.fragment, fragment.hasPrefix("token=") else { throw RadioError.missingToken }
        token = String(fragment.dropFirst(6))
        guard token.count >= 32 && token.count <= 128,
              token.allSatisfy({ $0.isASCII && ($0.isLetter || $0.isNumber || $0 == "-" || $0 == "_") }) else { throw RadioError.missingToken }
        parts.fragment = nil; parts.path = "/api/state"
        guard let result = parts.url else { throw RadioError.invalidEndpoint }
        url = result
    }
}

public enum ProbeProjection {
    static func numeric(_ value: Any?, _ range: ClosedRange<Double>) -> Double? {
        guard let value, !(value is NSNull) else { return nil }
        let n: Double?
        if let x = value as? NSNumber {
            guard CFGetTypeID(x) != CFBooleanGetTypeID() else { return nil }
            n = x.doubleValue
        }
        else if let x = value as? String { n = Double(x) }
        else { n = nil }
        guard let n, n.isFinite, range.contains(n) else { return nil }
        return n
    }
    static func integer(_ value: Any?, _ range: ClosedRange<Double>) -> Int? {
        guard let n = numeric(value, range), n.rounded() == n else { return nil }
        return Int(n)
    }
    static func text(_ value: Any?) -> String? {
        guard let s = value as? String, !s.isEmpty else { return nil }
        return String(s.prefix(128))
    }
    static func capability(_ name: String, _ raw: [String: Any]) -> Capability {
        let state = text(raw["state"]) ?? "not_returned"
        let domain = text(raw["error_domain"])
        let code = integer(raw["error_code"], -100000...100000)
        return Capability(name: name, state: state, detail: domain.map { "\($0) / \(code.map(String.init) ?? "unknown")" })
    }
    public static func make(_ raw: [String: Any], now: Date = Date()) -> RadioSnapshot {
        let time = RadioSnapshot.iso(now)
        var rows: [Observation] = []
        var capabilities: [Capability] = []
        let data = raw["data"] as? [String: Any] ?? [:]
        let qos = raw["qos"] as? [String: Any] ?? [:]
        let stable = raw["stable_data_service"] as? Bool == true
        let slot = stable ? (text(raw["data_slot"]) ?? "Unknown") : "Unknown"
        // A 5G flag/coverage icon alone cannot classify a QoS flow. Read the
        // active data-bearer flags and require a stable service descriptor.
        let sa = data["newRadioSaDataBearer"] as? Bool == true
        let nsa = data["newRadioNsaDataBearer"] as? Bool == true
        let mode = integer(data["dataMode"], 0...100)
        let qosName: String = !stable || (sa && nsa) ? "QCI / 5QI" :
            sa ? "5QI" : nsa || mode == 14 ? "QCI" : "QCI / 5QI"
        let networkMode: String? = !stable || (sa && nsa) ? nil : sa ? "NR" : nsa ? "NRNSA" : mode == 14 ? "LTE" : nil
        for flow in (qos["flows"] as? [[String: Any]] ?? []).prefix(64) {
            let tx = integer((flow["txQos"] as? [String: Any])?["qci"], 1...255)
            let rx = integer((flow["rxQos"] as? [String: Any])?["qci"], 1...255)
            guard tx != nil || rx != nil else { continue }
            var row = Observation(kind: "qos", timestamp: time, slot: slot)
            row.apn = text(qos["apn"]); row.interface = text(qos["interface"])
            row.tx = tx; row.rx = rx; row.qos_kind = qosName; row.network_mode = networkMode
            row.flow = integer(flow["flowId"], 0...65535); row.bearer = integer(flow["bearerId"], 0...65535)
            row.state = "System-reported data connection"
            rows.append(row)
        }
        capabilities.append(capability("Internet QoS", qos))
        capabilities.append(capability("Data bearer mode", data))
        capabilities.append(capability("SIM contexts", raw["contexts"] as? [String: Any] ?? [:]))
        for (index, result) in (raw["cells"] as? [[String: Any]] ?? []).prefix(2).enumerated() {
            capabilities.append(capability("Cell measurements \(index + 1)", result))
            for cell in (result["cells"] as? [[String: Any]] ?? []).prefix(64) {
                guard let band = integer(cell["BandInfo"], 1...1024) else { continue }
                let rawRat = text(cell["CellRadioAccessTechnology"]) ?? ""
                let rat = rawRat.replacingOccurrences(of: "kCTCellMonitorRadioAccessTechnology", with: "")
                let role = (text(cell["CellType"]) ?? "Unknown").replacingOccurrences(of: "kCTCellMonitorCellType", with: "")
                var row = Observation(kind: "cell", timestamp: time, slot: text(result["slot"]) ?? "Unknown")
                row.rat = ["LTE", "NR"].contains(rat) ? rat : "Unknown"; row.band = band
                row.role = ["Serving", "Primary", "Secondary", "Neighbor"].contains(role) ? role : "Unknown"
                row.rsrp = numeric(cell["RSRP"], -160 ... -20); row.rsrq = numeric(cell["RSRQ"], -50 ... -0.01)
                row.sinr = numeric(cell["SINR"] ?? cell["SNR"], -50...100)
                row.arfcn = integer(cell["NRARFCN"] ?? cell["ARFCN"], 0...4000000)
                rows.append(row)
            }
        }
        // Duplicate rows can share a SwiftUI identity. Keep one projection.
        var unique = Set<String>()
        rows = rows.filter { unique.insert($0.id).inserted }
        let state = text(raw["state"]) ?? "unavailable"
        let detail: String
        if state == "timeout" || state == "busy" { detail = "The on-device reader is waiting for iOS. Previous readings are not reused." }
        else if rows.isEmpty { detail = "iOS returned no usable QoS or cell measurements. See capability results below." }
        else { detail = "Read directly on this iPhone. The system may return cached measurements; full carrier aggregation is not guaranteed." }
        return RadioSnapshot(source: "on-device", status: state, detail: detail, generated_at: time,
                             observations: rows, capabilities: capabilities,
                             coverage: "Experimental on-device reader. Firmware analysis supports a QoS read path; serving-cell access requires a separate iOS permission. Physical-device access is not yet verified.")
    }
}
