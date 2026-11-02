import SceneKit
import simd

/// The body's skeleton. Every clothing item is re-bound to these bones,
/// so one animation moves the body and everything it wears.
///
/// SceneKit animations only change presentation values, so the model transforms
/// of the bones always hold the rest pose.
final class AvatarRig {
    let container: SCNNode
    let names: AvatarRigNames
    let hips: SCNNode?
    private let meshParent: SCNNode
    private let skeletonRoot: SCNNode?
    private let bones: [String: SCNNode]

    init(container: SCNNode, names: AvatarRigNames, file: String) throws {
        let skinned = container.childNodes { node, _ in node.skinner != nil }
        guard let firstSkinned = skinned.first, let skinner = firstSkinned.skinner else {
            throw AvatarError.missingSkeleton(file)
        }
        var bones: [String: SCNNode] = [:]
        for bone in skinned.flatMap({ $0.skinner?.bones ?? [] }) {
            if let name = bone.name { bones[name] = bone }
        }
        self.container = container
        self.names = names
        self.bones = bones
        self.hips = bones[names.hips]
        self.meshParent = firstSkinned.meshHolder.parent ?? container
        self.skeletonRoot = skinner.skeleton
    }

    func bone(named name: String) -> SCNNode? {
        bones[name]
    }

    func restPosition(of bone: SCNNode) -> SIMD3<Float> {
        bone.simdPosition
    }

    /// Rest transform of a bone in container space.
    func restTransform(of bone: SCNNode) -> simd_float4x4 {
        container.simdConvertTransform(matrix_identity_float4x4, from: bone)
    }

    /// Hides body patches under worn clothes. The asset pipeline names them
    /// `<mesh>__<region>` (kit bodies, hidden by region) or `<mesh>__covers__<id>+<id>`
    /// (generated bodies, hidden while any listed item is worn).
    func hide(regions: Set<AvatarBodyRegion>, wornItems: Set<String>) {
        container.enumerateHierarchy { node, _ in
            guard let name = node.name else { return }
            if let covers = name.range(of: "__covers__") {
                let items = name[covers.upperBound...].split(separator: "+").map(String.init)
                node.isHidden = items.contains(where: wornItems.contains)
            } else if let separator = name.range(of: "__"),
                      let region = AvatarBodyRegion(rawValue: String(name[separator.upperBound...])) {
                node.isHidden = regions.contains(region)
            }
        }
    }

    /// Shows a body type: its morph target fully on, the others off, on the body and
    /// every item attached to it (they carry the same targets). `normal` is all off.
    func setBodyType(_ bodyType: AvatarBodyType) {
        container.enumerateHierarchy { node, _ in
            guard let morpher = node.morpher else { return }
            for target in morpher.targets {
                guard let name = target.name, AvatarBodyType(rawValue: name) != nil else { continue }
                morpher.setWeight(name == bodyType.rawValue ? 1 : 0, forTargetNamed: name)
            }
        }
    }

    // MARK: - Attaching parts

    /// Moves the meshes of a loaded part onto this rig and returns the moved nodes.
    ///
    /// Skinned meshes get a new skinner that points at this rig's bones. Rigid meshes
    /// parented to a bone (a hat on the head) are re-parented to the matching bone.
    func attach(partRoot: SCNNode) -> [SCNNode] {
        let holders = Set(partRoot.childNodes { node, _ in node.geometry != nil }.map(\.meshHolder))
        var attached: [SCNNode] = []

        for holder in holders {
            if ([holder] + holder.childNodes).contains(where: { $0.skinner != nil }) {
                let rebound = reboundCopy(of: holder)
                holder.childNodes.forEach { rebound.addChildNode(reboundCopy(of: $0)) }
                meshParent.addChildNode(rebound)
                attached.append(rebound)
                continue
            } else if let ancestor = holder.ancestors.first(where: { bones[$0.name ?? ""] != nil }),
                      let bone = bones[ancestor.name ?? ""] {
                let local = ancestor.convertTransform(SCNMatrix4Identity, from: holder)
                holder.removeFromParentNode()
                holder.transform = local
                bone.addChildNode(holder)
            } else {
                let local = partRoot.convertTransform(SCNMatrix4Identity, from: holder)
                holder.removeFromParentNode()
                holder.transform = local
                meshParent.addChildNode(holder)
            }
            attached.append(holder)
        }
        return attached
    }

    /// A copy of the node whose skinner points at this rig's bones. A part bone the
    /// body lacks (an extra hair bone) is treated as rigidly attached to its nearest
    /// known ancestor.
    ///
    /// Builds a new node instead of swapping the skinner in place: SceneKit keeps a
    /// skinning deformer per node, and replacing the skinner crashes when the node
    /// joins the scene.
    private func reboundCopy(of node: SCNNode) -> SCNNode {
        let copy = SCNNode()
        copy.name = node.name
        copy.transform = node.transform
        guard let skinner = node.skinner, let baseGeometry = skinner.baseGeometry else {
            copy.geometry = node.geometry
            return copy
        }
        let inverseBinds = skinner.boneInverseBindTransforms ?? []
        var newBones: [SCNNode] = []
        var newInverseBinds: [NSValue] = []

        for (index, partBone) in skinner.bones.enumerated() {
            let inverseBind = index < inverseBinds.count ? inverseBinds[index].scnMatrix4Value : SCNMatrix4Identity
            if let bone = bones[partBone.name ?? ""] {
                newBones.append(bone)
                newInverseBinds.append(NSValue(scnMatrix4: inverseBind))
                continue
            }
            let anchor = partBone.ancestors.first { bones[$0.name ?? ""] != nil }
            let target = anchor.flatMap { bones[$0.name ?? ""] } ?? hips ?? partBone
            let anchorWorld = anchor?.simdWorldTransform ?? partBone.simdWorldTransform
            let adjusted = simd_inverse(anchorWorld) * partBone.simdWorldTransform * simd_float4x4(inverseBind)
            newBones.append(target)
            newInverseBinds.append(NSValue(scnMatrix4: SCNMatrix4(adjusted)))
        }

        // A geometry of its own: SceneKit keeps skinning state on the geometry, and the
        // original GLTFKit2 node still holds this one.
        let geometry = baseGeometry.ownedCopy
        let rebound = SCNSkinner(
            baseGeometry: geometry,
            bones: newBones,
            boneInverseBindTransforms: newInverseBinds,
            boneWeights: skinner.boneWeights,
            boneIndices: skinner.boneIndices
        )
        rebound.skeleton = skeletonRoot
        copy.geometry = geometry
        // Body type targets; the copy needs its own weights.
        copy.morpher = node.morpher?.copy() as? SCNMorpher
        copy.skinner = rebound
        return copy
    }
}

extension SCNGeometry {
    /// A copy with its own materials, so recoloring it leaves the source alone.
    var ownedCopy: SCNGeometry {
        guard let copy = copy() as? SCNGeometry else { return self }
        copy.materials = materials.map { ($0.copy() as? SCNMaterial) ?? $0 }
        return copy
    }
}

extension SCNNode {
    /// GLTFKit2 splits a multi-primitive mesh into `<name>_primitiveNN` children.
    /// The holder is the node that stands for the whole glTF mesh.
    var meshHolder: SCNNode {
        if let parent, parent.geometry == nil, let name, name.hasPrefix((parent.name ?? "") + "_primitive") {
            return parent
        }
        return self
    }

    var ancestors: [SCNNode] {
        var result: [SCNNode] = []
        var current = parent
        while let node = current {
            result.append(node)
            current = node.parent
        }
        return result
    }
}
