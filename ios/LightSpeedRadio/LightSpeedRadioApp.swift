import SwiftUI

@main struct LightSpeedRadioApp: App {
    @StateObject private var store = MonitorStore()
    @Environment(\.scenePhase) private var scenePhase
    var body: some Scene {
        WindowGroup {
            RadioView().environmentObject(store)
                .onChange(of: scenePhase) { _, phase in
                    if phase != .active { store.pause() }
                    else { store.updatePublicRadio() }
                }
        }
    }
}
