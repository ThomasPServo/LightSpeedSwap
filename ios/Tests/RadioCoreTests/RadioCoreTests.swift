import XCTest
@testable import RadioCore

final class RadioCoreTests: XCTestCase {
    func report(stable: Bool = true, sa: Bool = true, nsa: Bool = false) -> [String: Any] {
        ["state":"completed", "stable_data_service":stable, "data_slot":"One",
         "data":["state":"returned", "newRadioSaDataBearer":sa, "newRadioNsaDataBearer":nsa],
         "qos":["state":"returned", "apn":"example.data", "flows":[["flowId":7, "bearerId":5, "txQos":["qci":9], "rxQos":["qci":8]]]],
         "cells":[["state":"returned", "slot":"Two", "cells":[["BandInfo":41, "CellType":"kCTCellMonitorCellTypeServing", "CellRadioAccessTechnology":"kCTCellMonitorRadioAccessTechnologyNR", "RSRP":-95]]]]]
    }
    func testOnDeviceQoSUsesTheClassNotTheFlowIdentifier() {
        let s = ProbeProjection.make(report())
        XCTAssertEqual(s.observations.first?.tx, 9)
        XCTAssertEqual(s.observations.first?.rx, 8)
        XCTAssertEqual(s.observations.first?.flow, 7)
        XCTAssertEqual(s.observations.first?.qos_kind, "5QI")
        XCTAssertEqual(s.observations.last?.slot, "Two")
        XCTAssertEqual(s.observations.last?.bandLabel, "n41")
    }
    func testNSAIsQCIAndChangingServiceRemainsUnattributed() {
        XCTAssertEqual(ProbeProjection.make(report(sa:false, nsa:true)).observations.first?.qos_kind, "QCI")
        let changed = ProbeProjection.make(report(stable:false))
        XCTAssertEqual(changed.observations.first?.qos_kind, "QCI / 5QI")
        XCTAssertEqual(changed.observations.first?.slot, "Unknown")
        XCTAssertEqual(ProbeProjection.make(report(sa:true, nsa:true)).observations.first?.qos_kind, "QCI / 5QI")
    }
    func testNeighborAndOldMeasurementsAreNotConnected() {
        let now = Date()
        var s = ProbeProjection.make(report(), now: now)
        XCTAssertEqual(s.currentServing(at: now).count, 1)
        XCTAssertTrue(s.currentServing(at: now.addingTimeInterval(31)).isEmpty)
        s.observations[1].role = "Neighbor"
        XCTAssertTrue(s.currentServing(at: now).isEmpty)
    }
    func testImportCannotPretendToBeLive() throws {
        var s = try RadioSnapshot.decodeReport(JSONEncoder().encode(ProbeProjection.make(report())))
        s.makeHistorical()
        XCTAssertTrue(s.currentServing().isEmpty)
        XCTAssertEqual(s.source, "historical")
        XCTAssertThrowsError(try RadioSnapshot.decodeReport(Data("{}".utf8)))
    }
    func testDenialsProduceNoInventedMeasurements() {
        let s = ProbeProjection.make(["state":"completed", "qos":["state":"unavailable_or_denied", "error_domain":"CTError", "error_code":13]])
        XCTAssertTrue(s.observations.isEmpty)
        XCTAssertEqual(s.capabilities?.first?.detail, "CTError / 13")
    }
    func testInvalidSignalAndQoSValuesAreDiscarded() {
        var raw = report()
        raw["qos"] = ["state":"returned", "flows":[["txQos":["qci":0], "rxQos":["qci":999]]]]
        raw["cells"] = [["state":"returned", "cells":[["BandInfo":71, "RSRP":-3276.8, "RSRQ":0]]]]
        let s = ProbeProjection.make(raw)
        XCTAssertEqual(s.observations.count, 1)
        XCTAssertNil(s.observations.first?.rsrp)
        XCTAssertNil(s.observations.first?.rsrq)
        XCTAssertEqual(s.observations.first?.role, "Unknown")
    }
    func testBooleanQoSFromChangedPrivateABIIsNotClassOne() {
        var raw = report()
        raw["cells"] = []
        raw["qos"] = ["state":"returned", "flows":[["txQos":["qci":true], "rxQos":["qci":false]]]]
        XCTAssertTrue(ProbeProjection.make(raw).observations.isEmpty)
    }
    func testConflictingModeAndDuplicateImportStayUnambiguous() throws {
        var raw = report(sa:true, nsa:true)
        raw["data"] = ["newRadioSaDataBearer":true, "newRadioNsaDataBearer":true, "dataMode":14]
        var s = ProbeProjection.make(raw)
        XCTAssertNil(s.observations.first?.network_mode)
        var last = s.observations[0]
        last.tx = 6
        s.observations.append(last)
        let decoded = try RadioSnapshot.decodeReport(JSONEncoder().encode(s))
        XCTAssertEqual(decoded.observations.count, 2)
        XCTAssertEqual(decoded.observations.first(where: { $0.kind == "qos" })?.tx, 6)
    }
    func testCollectorEndpointKeepsAccessTokenOutOfRequestURL() throws {
        let token = String(repeating:"a", count:43)
        let endpoint = try CollectorEndpoint("http://192.168.1.7:8765/#token=\(token)")
        XCTAssertEqual(endpoint.url.absoluteString, "http://192.168.1.7:8765/api/state")
        XCTAssertEqual(endpoint.token, token)
        for host in ["example.com", "8.8.8.8", "192.168.1.7.evil.test", "172.32.1.2", "192.168.999.2"] {
            XCTAssertThrowsError(try CollectorEndpoint("http://\(host):8765/#token=\(token)"))
        }
        XCTAssertThrowsError(try CollectorEndpoint("http://192.168.1.7/"))
    }
}
