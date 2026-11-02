import SceneKit
import UIKit

/// Builds and updates the 3D avatar: loads the body, re-binds clothes to its
/// skeleton, recolors materials and plays moves.
@MainActor
final class AvatarSceneController {
    let scene = SCNScene()
    let cameraNode = SCNNode()
    /// Rotated by the drag gesture.
    let turntable = SCNNode()

    private let catalog: AvatarCatalog
    private let cache = AvatarAssetCache.shared
    private var rig: AvatarRig?
    private var appliedConfig: AvatarConfig?
    private var itemNodes: [AvatarSlot: [SCNNode]] = [:]
    private var clips: [AvatarMove: SCNAnimation] = [:]
    private var loopMove: AvatarMove = .idle
    private var loopKey: String?
    private var faceTextures: [String: UIImage] = [:]
    private var originalTextures: [ObjectIdentifier: Any] = [:]

    init(catalog: AvatarCatalog) {
        self.catalog = catalog
        AvatarStyle.setUpStage(in: scene, cameraNode: cameraNode)
        scene.rootNode.addChildNode(turntable)
    }

    // MARK: - Config

    func apply(_ config: AvatarConfig) async throws {
        // Body types are morph targets of the same mesh, so only a gender change loads another body.
        let bodyChanged = appliedConfig?.gender != config.gender
        if rig == nil || bodyChanged {
            try await rebuild(for: config)
        } else {
            try await updateItems(to: config)
        }
        appliedConfig = config
        refreshAppearance(for: config)
    }

    private func rebuild(for config: AvatarConfig) async throws {
        guard let body = catalog.body(for: config.gender) else {
            throw AvatarError.missingBody(config.gender)
        }
        let container = try await cache.nodes(at: catalog.url(for: body.file))
        let newRig = try AvatarRig(container: container, names: catalog.rig, file: body.file)
        AvatarStyle.apply(to: container)

        var newItemNodes: [AvatarSlot: [SCNNode]] = [:]
        for (slot, selection) in config.items {
            newItemNodes[slot] = try await load(selection, on: newRig)
        }

        let oldContainer = rig?.container
        rig = newRig
        itemNodes = newItemNodes
        clips = [:]
        refreshAppearance(for: config)
        scene.rootNode.removeAllAnimations()
        loopKey = nil
        oldContainer?.removeFromParentNode()
        turntable.addChildNode(container)
        AvatarStyle.frame(cameraNode, on: container)
        await play(loopMove, blendIn: false)
    }

    private func updateItems(to config: AvatarConfig) async throws {
        guard let rig, let applied = appliedConfig else { return }
        for slot in AvatarSlot.allCases where applied.items[slot]?.itemID != config.items[slot]?.itemID {
            var nodes: [SCNNode] = []
            if let selection = config.items[slot] {
                nodes = try await load(selection, on: rig)
            }
            itemNodes[slot]?.forEach { $0.removeFromParentNode() }
            itemNodes[slot] = nodes
        }
    }

    private func load(_ selection: AvatarItemSelection, on rig: AvatarRig) async throws -> [SCNNode] {
        guard let item = catalog.item(id: selection.itemID) else { throw AvatarError.missingItem(selection.itemID) }
        let partRoot = try await cache.nodes(at: catalog.url(for: item.file))
        AvatarStyle.apply(to: partRoot)
        let nodes = rig.attach(partRoot: partRoot)
        // Shown by refreshAppearance once colored, so a new item never flashes its original colors.
        nodes.forEach { $0.isHidden = true }
        return nodes
    }

    /// Body type, colors and visibility. Cheap, so it runs after every change.
    private func refreshAppearance(for config: AvatarConfig) {
        guard let rig, let body = catalog.body(for: config.gender) else { return }
        rig.setBodyType(config.bodyType)
        let luminance = catalog.materialLuminance
        let hairColor = config.items[.hair]?.color ?? config.items[.facialHair]?.color

        rig.container.recolor(materials: body.skinMaterials, to: config.skinTone, luminance: luminance, amount: AvatarStyle.skinRecolorAmount)
        applyFace(config.face, of: body, to: rig.container)
        rig.container.recolor(materials: body.hairMaterials, to: hairColor, luminance: luminance)

        let worn = config.items.values.compactMap { catalog.item(id: $0.itemID) }
        let hiddenSlots = Set(worn.flatMap(\.hidesSlots))
        rig.hide(regions: Set(worn.flatMap(\.hides)), wornItems: Set(worn.map(\.id)))

        for (slot, nodes) in itemNodes {
            guard let selection = config.items[slot], let item = catalog.item(id: selection.itemID) else { continue }
            for node in nodes {
                node.isHidden = hiddenSlots.contains(slot)
                node.recolor(materials: item.tintMaterials, to: selection.color ?? item.defaultColor, luminance: luminance)
                node.recolor(materials: item.skinMaterials, to: config.skinTone, luminance: luminance, amount: AvatarStyle.skinRecolorAmount)
            }
        }
    }

    /// Swaps the body's texture for the chosen face, or back to its own.
    private func applyFace(_ faceID: String?, of body: AvatarBodyAsset, to container: SCNNode) {
        guard let names = body.faceMaterials, !names.isEmpty else { return }
        let face = body.faces?.first { $0.id == faceID }
        let texture = face.flatMap { faceTextures[$0.texture] ?? loadFaceTexture($0.texture) }
        let wanted = Set(names)
        container.enumerateHierarchy { node, _ in
            for material in node.geometry?.materials ?? [] where wanted.contains(material.name ?? "") {
                let key = ObjectIdentifier(material)
                if originalTextures[key] == nil {
                    originalTextures[key] = material.diffuse.contents
                }
                material.diffuse.contents = texture ?? originalTextures[key] as Any?
            }
        }
    }

    private func loadFaceTexture(_ file: String) -> UIImage? {
        let image = UIImage(contentsOfFile: catalog.url(for: file).path)
        faceTextures[file] = image
        return image
    }

    // MARK: - Moves

    /// Loops replace the current loop; one-shots play once on top of it.
    /// Players go on the scene root because `/bone.orientation` key paths
    /// resolve from there; each stage has its own scene, so they don't collide.
    func play(_ move: AvatarMove, blendIn: Bool = true) async {
        guard let rig, let asset = catalog.move(move), let animation = await clip(for: move, asset: asset, rig: rig) else {
            return
        }
        // A freshly built body is in its rest pose; blending from it would flash a T-pose.
        let playable = blendIn ? animation : animation.withoutBlendIn
        let player = SCNAnimationPlayer(animation: playable)
        if asset.loops {
            if let loopKey {
                scene.rootNode.removeAnimation(forKey: loopKey, blendOutDuration: 0.3)
            }
            scene.rootNode.removeAnimation(forKey: Self.oneShotKey, blendOutDuration: 0.3)
            let key = "loop-\(UUID().uuidString)"
            scene.rootNode.addAnimationPlayer(player, forKey: key)
            loopKey = key
            loopMove = move
        } else {
            scene.rootNode.removeAnimation(forKey: Self.oneShotKey, blendOutDuration: 0.2)
            scene.rootNode.addAnimationPlayer(player, forKey: Self.oneShotKey)
        }
        player.play()
    }

    private static let oneShotKey = "one-shot"

    private func clip(for move: AvatarMove, asset: AvatarMoveAsset, rig: AvatarRig) async -> SCNAnimation? {
        if let cached = clips[move] { return cached }
        let animation: SCNAnimation?
        if let file = asset.file, let clipName = asset.clip {
            do {
                let gltf = try await cache.asset(at: catalog.url(for: file))
                animation = try AvatarClipBuilder.animation(named: clipName, in: gltf, for: rig, loops: asset.loops)
            } catch {
                assertionFailure("Avatar clip \(clipName): \(error)")
                animation = nil
            }
        } else {
            animation = AvatarWaveBuilder.animation(for: rig)
        }
        // The rig may have changed while the clip file was loading.
        guard self.rig === rig else { return nil }
        clips[move] = animation
        return animation
    }
}

private extension SCNAnimation {
    var withoutBlendIn: SCNAnimation {
        guard let copy = copy() as? SCNAnimation else { return self }
        copy.blendInDuration = 0
        return copy
    }
}
