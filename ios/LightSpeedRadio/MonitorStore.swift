import Foundation
import CoreTelephony
import SwiftUI

private final class LocalSessionDelegate: NSObject, URLSessionTaskDelegate {
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(nil) // Never forward the collector access key.
    }
}

@MainActor final class MonitorStore: ObservableObject {
    @Published var snapshot = RadioSnapshot.empty()
    @Published var reading = false
    @Published var autoRead = false
    @Published var publicRadio = "Not reported"
    @Published var error: String?
    @Published var link = ""
    @Published var selectedSlot = "All"
    private let probe = RadioProbe()
    private let telephony = CTTelephonyNetworkInfo()
    private var timer: Timer?
    private var generation = UUID()
    private var endpoint: CollectorEndpoint?
    private var pollTask: Task<Void, Never>?
    private let delegate = LocalSessionDelegate()
    private lazy var session: URLSession = {
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 8
        config.timeoutIntervalForResource = 10
        config.httpCookieStorage = nil; config.urlCache = nil
        return URLSession(configuration: config, delegate: delegate, delegateQueue: nil)
    }()

    var visible: [Observation] {
        snapshot.observations.filter { selectedSlot == "All" || $0.slot == selectedSlot }
    }
    var slots: [String] { ["All"] + Array(Set(snapshot.observations.map(\.slot))).sorted() }

    func updatePublicRadio() {
        let technologies = telephony.serviceCurrentRadioAccessTechnology ?? [:]
        let names = Set(technologies.values.map { $0.replacingOccurrences(of: "CTRadioAccessTechnology", with: "") })
        publicRadio = names.isEmpty ? "Not reported" : names.sorted().joined(separator: " + ")
    }
    func readPhone() {
        guard !reading else { return }
        pollTask?.cancel(); endpoint = nil
        updatePublicRadio()
        let current = UUID(); generation = current
        let dataServiceBefore = telephony.dataServiceIdentifier
        reading = true; error = nil
        probe.read { [weak self] result in
            Task { @MainActor in
                guard let self, self.generation == current else { return }
                self.reading = false
                var raw = result
                if dataServiceBefore == nil || dataServiceBefore != self.telephony.dataServiceIdentifier {
                    raw["stable_data_service"] = false
                }
                self.snapshot = ProbeProjection.make(raw)
                if !self.slots.contains(self.selectedSlot) { self.selectedSlot = "All" }
            }
        }
    }
    func toggleAutomatic() {
        autoRead.toggle()
        timer?.invalidate()
        if autoRead {
            readPhone()
            timer = Timer.scheduledTimer(withTimeInterval: 5, repeats: true) { [weak self] _ in
                Task { @MainActor in self?.readPhone() }
            }
        }
    }
    func pause() {
        timer?.invalidate(); timer = nil; autoRead = false
        pollTask?.cancel(); pollTask = nil
        generation = UUID(); reading = false; endpoint = nil
        if snapshot.source == "on-device" || snapshot.source == "live" {
            snapshot.source = "historical"; snapshot.status = "paused"
            snapshot.detail = "Paused. These are the last observations, not current readings."
        }
    }
    func connectMac() {
        pause()
        do {
            let endpoint = try CollectorEndpoint(link)
            self.endpoint = endpoint; error = nil
            link = "" // Access token remains only in memory while connected.
            let current = UUID(); generation = current
            pollTask = Task { [weak self] in
                while !Task.isCancelled {
                    guard let self, self.generation == current else { return }
                    do {
                        var request = URLRequest(url: endpoint.url)
                        request.setValue("Bearer " + endpoint.token, forHTTPHeaderField: "Authorization")
                        let (data, response) = try await self.session.data(for: request)
                        guard (response as? HTTPURLResponse)?.statusCode == 200 else { throw RadioError.connectionFailed }
                        let report = try RadioSnapshot.decodeReport(data)
                        guard self.generation == current else { return }
                        self.snapshot = report; self.error = nil
                    } catch {
                        guard self.generation == current, !Task.isCancelled else { return }
                        self.snapshot.source = "historical"
                        self.snapshot.status = "disconnected"
                        self.snapshot.detail = "Mac connection lost. Last observations are historical."
                        self.error = RadioError.connectionFailed.localizedDescription
                        return
                    }
                    try? await Task.sleep(nanoseconds: 2_000_000_000)
                }
            }
        } catch { self.error = error.localizedDescription }
    }
    func importReport(_ url: URL) {
        pause()
        let access = url.startAccessingSecurityScopedResource()
        defer { if access { url.stopAccessingSecurityScopedResource() } }
        do {
            let file = try FileHandle(forReadingFrom: url)
            defer { try? file.close() }
            let data = try file.read(upToCount: 8 * 1024 * 1024 + 1) ?? Data()
            var result = try RadioSnapshot.decodeReport(data)
            result.makeHistorical(); snapshot = result; error = nil; selectedSlot = "All"
        } catch { self.error = error.localizedDescription }
    }
    func exportReport() -> URL? {
        do {
            let data = try JSONEncoder().encode(snapshot)
            let url = FileManager.default.temporaryDirectory.appendingPathComponent("LightSpeed-Radio.json")
            try data.write(to: url, options: [.atomic, .completeFileProtection])
            return url
        } catch { self.error = error.localizedDescription; return nil }
    }
}
