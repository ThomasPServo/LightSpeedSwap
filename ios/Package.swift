// swift-tools-version: 5.9
import PackageDescription
let package = Package(name: "RadioCore", platforms: [.macOS(.v13), .iOS(.v17)],
    products: [.library(name: "RadioCore", targets: ["RadioCore"])],
    targets: [.target(name: "RadioCore", path: "RadioCore"),
              .testTarget(name: "RadioCoreTests", dependencies: ["RadioCore"], path: "Tests/RadioCoreTests")])
