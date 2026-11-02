import Foundation
import GLTFKit2
import SceneKit

/// Parses each GLB once. Every avatar part gets its own fresh node tree,
/// because materials are recolored per instance.
final class AvatarAssetCache: @unchecked Sendable {
    static let shared = AvatarAssetCache()

    private var assets: [URL: GLTFAsset] = [:]
    private let lock = NSLock()

    func asset(at url: URL) async throws -> GLTFAsset {
        if let cached = lock.withLock({ assets[url] }) { return cached }
        guard FileManager.default.fileExists(atPath: url.path) else {
            throw AvatarError.missingAsset(url.lastPathComponent)
        }
        let box = try await Task.detached(priority: .userInitiated) {
            AssetBox(asset: try GLTFAsset(url: url, options: [:]))
        }.value
        lock.withLock { assets[url] = box.asset }
        return box.asset
    }

    /// A new node tree holding every root of the asset's default scene.
    func nodes(at url: URL) async throws -> SCNNode {
        let asset = try await asset(at: url)
        let box = await Task.detached(priority: .userInitiated) {
            let root = SCNNode()
            let scene = GLTFSCNSceneSource(asset: asset).defaultScene
            for child in scene?.rootNode.childNodes ?? [] {
                root.addChildNode(child)
            }
            // Commit this thread's scene changes before the nodes go to the main thread.
            // Otherwise setting a morph weight right away (a body type on a body with no
            // items to load first) can reach SceneKit before its morph data exists, and crash.
            SCNTransaction.flush()
            return NodeBox(node: root)
        }.value
        return box.node
    }
}

private struct AssetBox: @unchecked Sendable {
    let asset: GLTFAsset
}

private struct NodeBox: @unchecked Sendable {
    let node: SCNNode
}
