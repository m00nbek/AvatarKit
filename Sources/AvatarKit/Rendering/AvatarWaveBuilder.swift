import SceneKit
import simd

/// Builds a hand wave from the rig's rest pose, since the free animation
/// libraries don't include one.
///
/// Works with directions instead of bone axes, so it fits any skeleton:
/// the upper arm points out to the side, the forearm points up with the palm
/// facing forward, then swings side to side around the elbow.
enum AvatarWaveBuilder {
    static let duration: Float = 2.0
    static let cycles: Float = 3
    static let keyframeCount = 37
    /// Forearm lean from vertical, in radians. Positive leans away from the head.
    static let centerAngle: Float = 0.2
    static let swingAngle: Float = 0.3

    static func animation(for rig: AvatarRig) -> SCNAnimation? {
        let names = rig.names
        guard let hips = rig.hips,
              let head = rig.bone(named: names.head),
              let leftUpper = rig.bone(named: names.leftUpperArm),
              let upper = rig.bone(named: names.rightUpperArm),
              let forearm = rig.bone(named: names.rightForearm),
              let hand = rig.bone(named: names.rightHand),
              let index = rig.bone(named: names.rightIndex),
              let pinky = rig.bone(named: names.rightPinky),
              let upperParent = upper.parent
        else { return nil }

        func position(_ bone: SCNNode) -> SIMD3<Float> { rig.restTransform(of: bone).translation }
        func rotation(_ node: SCNNode) -> simd_quatf { simd_quatf(rig.restTransform(of: node).rotationMatrix) }

        let up = simd_normalize(position(head) - position(hips))
        let side = position(upper) - position(leftUpper)
        let right = simd_normalize(side - simd_dot(side, up) * up)
        let forward = simd_normalize(simd_cross(right, up))

        let parentRotation = rotation(upperParent)
        let upperRest = upper.simdOrientation
        let forearmRest = forearm.simdOrientation
        let upperDirection = simd_normalize(position(forearm) - position(upper))
        let forearmDirection = simd_normalize(position(hand) - position(forearm))
        let palmNormal = simd_normalize(simd_cross(position(index) - position(hand), position(pinky) - position(hand)))

        let outward: SIMD3<Float> = right * 0.9 + up * 0.15
        let upperTarget: SIMD3<Float> = simd_normalize(outward + forward * 0.3)
        let raise = simd_quatf(from: upperDirection, to: upperTarget)
        let upperWorld = raise * parentRotation * upperRest
        let upperLocal = parentRotation.inverse * upperWorld

        // Forearm straight up with the palm turned forward.
        let vertical: SIMD3<Float> = simd_normalize(up + forward * 0.2)
        let bend = simd_quatf(from: raise.act(forearmDirection), to: vertical)
        let armRotation: simd_quatf = bend * raise
        let twist = twistRotation(around: vertical, from: armRotation.act(palmNormal), to: forward)
        let raisedForearm = twist * bend * upperWorld * forearmRest

        // Swinging around the forward axis leans the forearm toward the side.
        let times = (0..<keyframeCount).map { Float($0) / Float(keyframeCount - 1) }
        let forearmKeys = times.map { time in
            let angle = centerAngle + swingAngle * sin(2 * .pi * cycles * time)
            let swing = simd_quatf(angle: -angle, axis: forward)
            return upperWorld.inverse * swing * raisedForearm
        }

        let upperKeys = Array(repeating: upperLocal, count: keyframeCount)
        // Idle clips curl the fingers; the rest pose has an open hand to wave with.
        let fingers = hand.childNodes { node, _ in node.name.flatMap(rig.bone(named:)) === node }
        let channels = [
            AvatarClipBuilder.keyframeAnimation(path: "/\(names.rightUpperArm).orientation", times: times, rotations: upperKeys),
            AvatarClipBuilder.keyframeAnimation(path: "/\(names.rightForearm).orientation", times: times, rotations: forearmKeys),
        ] + fingers.compactMap { finger in
            finger.name.map {
                AvatarClipBuilder.keyframeAnimation(path: "/\($0).orientation", times: [0, 1], rotations: [finger.simdOrientation, finger.simdOrientation])
            }
        }
        channels.forEach { $0.duration = CFTimeInterval(duration) }
        let animation = AvatarClipBuilder.groupAnimation(channels, duration: CFTimeInterval(duration), loops: false)
        animation.blendInDuration = 0.35
        animation.blendOutDuration = 0.45
        return animation
    }

    /// Rotation around `axis` that turns `vector` toward `target`, both projected
    /// onto the plane perpendicular to the axis.
    private static func twistRotation(around axis: SIMD3<Float>, from vector: SIMD3<Float>, to target: SIMD3<Float>) -> simd_quatf {
        let from = vector - simd_dot(vector, axis) * axis
        let to = target - simd_dot(target, axis) * axis
        guard simd_length(from) > 1e-4, simd_length(to) > 1e-4 else { return simd_quatf(angle: 0, axis: axis) }
        let a = simd_normalize(from)
        let b = simd_normalize(to)
        let angle = atan2(simd_dot(simd_cross(a, b), axis), simd_dot(a, b))
        return simd_quatf(angle: angle, axis: axis)
    }
}

extension simd_float4x4 {
    var translation: SIMD3<Float> {
        SIMD3(columns.3.x, columns.3.y, columns.3.z)
    }

    /// Upper-left 3x3 with scale removed.
    var rotationMatrix: simd_float3x3 {
        simd_float3x3(
            simd_normalize(SIMD3(columns.0.x, columns.0.y, columns.0.z)),
            simd_normalize(SIMD3(columns.1.x, columns.1.y, columns.1.z)),
            simd_normalize(SIMD3(columns.2.x, columns.2.y, columns.2.z))
        )
    }
}
