// swift-tools-version: 5.10

import PackageDescription

let package = Package(
    name: "AvatarKit",
    platforms: [.iOS(.v17)],
    products: [
        .library(
            name: "AvatarKit",
            targets: ["AvatarKit"]
        ),
    ],
    dependencies: [
        .package(url: "https://github.com/warrenm/GLTFKit2.git", from: "0.5.15"),
    ],
    targets: [
        .target(
            name: "AvatarKit",
            dependencies: ["GLTFKit2"],
            resources: [.copy("Resources/Avatar")]
        ),
        .testTarget(
            name: "AvatarKitTests",
            dependencies: ["AvatarKit"]
        ),
    ]
)
