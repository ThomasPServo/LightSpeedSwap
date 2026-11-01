import SwiftUI
import UniformTypeIdentifiers

private let ink = Color(red: 0.035, green: 0.055, blue: 0.09)
private let panel = Color(red: 0.075, green: 0.10, blue: 0.15)
private let mint = Color(red: 0.43, green: 0.95, blue: 0.75)

struct RadioView: View {
    @EnvironmentObject private var store: MonitorStore
    @State private var importer = false
    @State private var showMac = false
    @State private var exportURL: URL?
    @State private var showHelp = false
    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 22) {
                    header
                    controls
                    if let error = store.error {
                        Label(error, systemImage: "exclamationmark.circle").font(.subheadline).foregroundStyle(.orange)
                    }
                    if store.slots.count > 2 {
                        Picker("SIM", selection: $store.selectedSlot) {
                            ForEach(store.slots, id: \.self) { Text($0 == "All" ? "All SIMs" : "SIM \($0)").tag($0) }
                        }.pickerStyle(.segmented)
                    }
                    TimelineView(.periodic(from: .now, by: 1)) { context in
                        readings(now: context.date)
                    }
                    capabilities
                    Text(store.snapshot.coverage ?? "Read-only diagnostics. No backup, restore, carrier installation or Find My change.")
                        .font(.footnote).foregroundStyle(.secondary)
                }.padding(20).frame(maxWidth: 760)
            }
            .background(ink)
            .navigationTitle("LightSpeed Radio")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Menu {
                        Button("Open saved report", systemImage: "doc") { importer = true }
                        Button("Export readings", systemImage: "square.and.arrow.up") { exportURL = store.exportReport() }
                        Button("Connect to Mac", systemImage: "desktopcomputer") { showMac = true }
                        Button("How readings work", systemImage: "info.circle") { showHelp = true }
                    } label: { Image(systemName: "ellipsis.circle") }
                }
            }
            .fileImporter(isPresented: $importer, allowedContentTypes: [.json]) { result in
                switch result {
                case .success(let url): store.importReport(url)
                case .failure(let error): store.error = error.localizedDescription
                }
            }
            .sheet(isPresented: $showMac) { macSheet }
            .sheet(isPresented: $showHelp) { helpSheet }
            .sheet(isPresented: Binding(get: { exportURL != nil }, set: { if !$0 { exportURL = nil } })) {
                if let url = exportURL {
                    VStack(spacing: 24) {
                        Image(systemName: "waveform.path").font(.system(size: 48)).foregroundStyle(mint)
                        Text("Your radio readings").font(.title2.bold())
                        Text("Only projected readings and capability results are included. Subscriber identifiers and raw system logs are excluded.")
                            .foregroundStyle(.secondary).multilineTextAlignment(.center)
                        ShareLink(item: url).buttonStyle(.borderedProminent)
                    }.padding(30).presentationDetents([.medium])
                }
            }
        }.preferredColorScheme(.dark).tint(mint)
    }
    private var header: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Label("ON-DEVICE READER", systemImage: "antenna.radiowaves.left.and.right").font(.caption.weight(.semibold)).tracking(1.4).foregroundStyle(mint)
                Spacer()
                Text(store.reading ? "READING" : store.snapshot.source.uppercased())
                    .font(.caption2.monospaced().bold()).padding(.horizontal, 10).padding(.vertical, 6)
                    .background(mint.opacity(0.1), in: Capsule()).foregroundStyle(mint)
            }
            Text("See your connection.").font(.system(size: 32, weight: .bold, design: .rounded))
            Text(store.snapshot.detail).font(.subheadline).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            HStack {
                Text("Registered radio").foregroundStyle(.secondary)
                Spacer()
                Text(store.publicRadio).fontWeight(.medium)
            }.font(.caption)
        }
    }
    private var controls: some View {
        HStack(spacing: 12) {
            Button { store.readPhone() } label: {
                HStack { if store.reading { ProgressView().tint(ink) }; Text("Read this iPhone").fontWeight(.semibold) }
                    .frame(maxWidth: .infinity).padding(.vertical, 7)
            }.buttonStyle(.borderedProminent).disabled(store.reading)
            Button { store.toggleAutomatic() } label: {
                Image(systemName: store.autoRead ? "pause.fill" : "arrow.clockwise")
                    .frame(width: 32, height: 32)
            }.buttonStyle(.bordered).accessibilityLabel(store.autoRead ? "Stop automatic reads" : "Read every five seconds")
        }
    }
    @ViewBuilder private func readings(now: Date) -> some View {
        let cells = store.visible.filter { $0.kind == "cell" }
        let qos = store.visible.filter { $0.kind == "qos" }
        VStack(alignment: .leading, spacing: 18) {
            sectionTitle("Reported serving bands", subtitle: "Only cells explicitly marked as serving")
            if cells.filter(\.isServing).isEmpty {
                empty("No serving-band measurement", detail: "A permission error, an idle connection or a changed iOS interface can leave this empty.")
            } else {
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 145))], spacing: 12) {
                    ForEach(cells.filter(\.isServing)) { cell in
                        VStack(alignment: .leading, spacing: 12) {
                            HStack { Text(cell.rat ?? "Unknown").font(.caption.monospaced()); Spacer(); freshness(cell, now: now) }
                            Text(cell.bandLabel).font(.system(size: 35, weight: .bold, design: .rounded)).foregroundStyle(mint)
                            Text("SIM \(cell.slot) · \(cell.role ?? "Unknown")").font(.caption).foregroundStyle(.secondary)
                            HStack(alignment: .firstTextBaseline, spacing: 4) {
                                Text(cell.rsrp.map { String(format: "%.0f", $0) } ?? "—").font(.title3.monospaced())
                                Text("dBm RSRP").font(.caption2).foregroundStyle(.secondary)
                            }
                            if let arfcn = cell.arfcn { Text("ARFCN \(arfcn)").font(.caption2.monospaced()).foregroundStyle(.secondary) }
                            HStack {
                                metric("RSRQ", cell.rsrq)
                                Spacer()
                                metric("SINR", cell.sinr)
                            }
                        }.padding(16).frame(maxWidth: .infinity, alignment: .leading).background(panel, in: RoundedRectangle(cornerRadius: 18))
                    }
                }
            }
            sectionTitle("QoS flows", subtitle: "QCI for LTE/EPC · 5QI for 5G core")
            if qos.isEmpty {
                empty("No QoS class returned", detail: "The app never estimates QCI from your carrier bundle, speed or signal bars.")
            }
            ForEach(qos) { flow in
                VStack(alignment: .leading, spacing: 14) {
                    HStack {
                        VStack(alignment: .leading, spacing: 4) {
                            Text(flow.apn == "--" ? "APN hidden by iOS" : flow.apn ?? "Unknown APN").font(.headline)
                            Text("SIM \(flow.slot) · \(flow.network_mode ?? "Mode unresolved")").font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer(); freshness(flow, now: now)
                    }
                    HStack(alignment: .firstTextBaseline) {
                        Text(flow.qos_kind ?? "QCI / 5QI").font(.subheadline).foregroundStyle(mint)
                        Spacer()
                        Text("TX \(flow.tx.map(String.init) ?? "—")  /  RX \(flow.rx.map(String.init) ?? "—")")
                            .font(.title3.monospaced().bold())
                    }
                    Text("Bearer \(flow.bearer.map(String.init) ?? "—") · Flow \(flow.flow.map(String.init) ?? "—")")
                        .font(.caption2.monospaced()).foregroundStyle(.secondary)
                }.padding(18).background(panel, in: RoundedRectangle(cornerRadius: 18))
            }
            if !cells.filter({ !$0.isServing }).isEmpty {
                DisclosureGroup("Other observed cells") {
                    ForEach(cells.filter { !$0.isServing }) { cell in
                        HStack { Text(cell.bandLabel); Spacer(); Text(cell.role ?? "Unknown").foregroundStyle(.secondary) }.font(.subheadline).padding(.vertical, 8)
                    }
                }.font(.subheadline)
            }
        }
    }
    private var capabilities: some View {
        DisclosureGroup("On-device capability results") {
            VStack(alignment: .leading, spacing: 14) {
                ForEach(store.snapshot.capabilities ?? []) { capability in
                    VStack(alignment: .leading, spacing: 4) {
                        HStack {
                            Text(capability.name)
                            Spacer()
                            Text(capability.state.replacingOccurrences(of: "_", with: " ")).foregroundStyle(.secondary)
                        }
                        if let detail = capability.detail { Text(detail).font(.caption.monospaced()).foregroundStyle(.orange) }
                    }
                }
                if store.snapshot.capabilities?.isEmpty != false { Text("Run an on-device read to test the available interfaces.").foregroundStyle(.secondary) }
                Text("Private APIs may reject an ordinary signed app. Returned access errors remain visible; the app does not try to grant itself Apple permissions.")
                    .font(.caption).foregroundStyle(.secondary)
            }.font(.caption).padding(.top, 12)
        }.font(.subheadline).padding(18).background(panel, in: RoundedRectangle(cornerRadius: 18))
    }
    private func freshness(_ row: Observation, now: Date) -> some View {
        let recent = store.snapshot.isLiveSource && row.isRecent(at: now)
        return Text(recent ? "READ RECENTLY" : "LAST OBSERVED")
            .font(.system(size: 9, weight: .semibold, design: .monospaced))
            .foregroundStyle(recent ? mint : .secondary)
    }
    private func metric(_ name: String, _ value: Double?) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(name).font(.caption2).foregroundStyle(.secondary)
            Text(value.map { String(format: "%.1f dB", $0) } ?? "—").font(.caption.monospaced())
        }
    }
    private func sectionTitle(_ title: String, subtitle: String) -> some View {
        VStack(alignment: .leading, spacing: 4) { Text(title).font(.headline); Text(subtitle).font(.caption).foregroundStyle(.secondary) }
    }
    private func empty(_ title: String, detail: String) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Label(title, systemImage: "waveform.path").font(.subheadline.weight(.medium))
            Text(detail).font(.caption).foregroundStyle(.secondary)
        }.padding(20).frame(maxWidth: .infinity, alignment: .leading).background(panel, in: RoundedRectangle(cornerRadius: 18))
    }
    private var macSheet: some View {
        NavigationStack {
            Form {
                Section("Optional Mac collector") {
                    Text("Start LightSpeedSwap with dashboard --share, then paste its phone link. Both devices must be on the same trusted Wi-Fi.")
                    SecureField("http://192.168…/#token=…", text: $store.link).textInputAutocapitalization(.never).autocorrectionDisabled()
                    Button("Connect") { store.connectMac(); if store.error == nil { showMac = false } }
                }
                Text("On-device reads work independently of the Mac collector when iOS permits the required interfaces.").font(.footnote)
            }.navigationTitle("Mac connection").toolbar { Button("Done") { showMac = false } }
        }.preferredColorScheme(.dark)
    }
    private var helpSheet: some View {
        NavigationStack {
            List {
                Section("Direct on-device reading") {
                    Text("Read this iPhone queries CoreTelephony on this phone. Use cellular data during the read. The private interfaces are experimental on iOS 27.2.")
                    Text("The analyzed firmware exposes an internet-QoS request with public server access. Its SPI check hides the APN name. Serving-cell requests require SPI access, so a normally signed app may show QoS while bands remain unavailable.")
                    Text("A missing value means the measurement was not returned. Bands supported by a modem are never substituted for serving bands.")
                }
                Section("Interpreting QoS") {
                    Text("QoS belongs to a bearer or flow. Voice/IMS and internet data can have different classes. A flow ID is not its QoS class, and lower class numbers do not universally mean faster service.")
                    Text("5G NSA can use LTE/EPC QCI. 5G standalone uses 5QI. When the data service or mode cannot be established, the app preserves the raw class and marks its type unresolved.")
                }
                Section("Freshness") {
                    Text("On-device timestamps record when the system answered, not when the modem measured the radio. iOS can return cached data. Readings age out after 30 seconds and polling stops in the background.")
                }
            }.navigationTitle("Reading guide").toolbar { Button("Done") { showHelp = false } }
        }.preferredColorScheme(.dark)
    }
}
