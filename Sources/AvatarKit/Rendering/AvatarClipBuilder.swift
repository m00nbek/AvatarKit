import GLTFKit2
import SceneKit
import simd

/// Turns a glTF animation into a SceneKit animation for one specific rig.
///
/// Clips are authored on a mannequin with its own proportions, and they key every
/// bone's position. Applying those positions would squash every body into the
/// mannequin's shape, so only rotations are kept. The hips keep their movement,
/// scaled to the target body's hip height.
enum AvatarClipBuilder {
    static func animation(
        named clipName: String,
        in asset: GLTFAsset,
        for rig: AvatarRig,
        loops: Bool
    ) throws -> SCNAnimation {
        guard let clip = asset.animations.first(where: { $0.name == clipName }) else {
            throw AvatarError.missingClip(clipName)
        }

        var channels: [CAKeyframeAnimation] = []
        var duration: Float = 0

        for channel in clip.channels {
            guard let boneName = channel.target.node?.name, let bone = rig.bone(named: boneName) else { continue }
            let times = try channel.sampler.input.floats(perElement: 1)
            guard let lastTime = times.last else { continue }
            duration = max(duration, lastTime)

            switch channel.target.path {
            case GLTFAnimationPath.rotation.rawValue:
                let values = try channel.sampler.output.floats(perElement: 4)
                let rotations = stride(from: 0, to: values.count, by: 4).map {
                    simd_quatf(ix: values[$0], iy: values[$0 + 1], iz: values[$0 + 2], r: values[$0 + 3])
                }
                channels.append(keyframeAnimation(path: "/\(boneName).orientation", times: times, rotations: rotations))

            case GLTFAnimationPath.translation.rawValue where bone === rig.hips:
                guard let sourceRest = channel.target.node?.translation else { continue }
                let values = try channel.sampler.output.floats(perElement: 3)
                let targetRest = rig.restPosition(of: bone)
                let scale = simd_length(sourceRest) > 0 ? simd_length(targetRest) / simd_length(sourceRest) : 1
                let keyframes = stride(from: 0, to: values.count, by: 3).map {
                    let offset = SIMD3(values[$0], values[$0 + 1], values[$0 + 2]) - sourceRest
                    return NSValue(scnVector3: SCNVector3(targetRest + offset * scale))
                }
                channels.append(keyframeAnimation(path: "/\(boneName).position", times: times, values: keyframes))

            default:
                continue
            }
        }

        guard !channels.isEmpty, duration > 0 else { throw AvatarError.missingClip(clipName) }
        channels.forEach { $0.duration = CFTimeInterval(duration) }
        return groupAnimation(channels, duration: CFTimeInterval(duration), loops: loops)
    }

    static func keyframeAnimation(path: String, times: [Float], values: [NSValue]) -> CAKeyframeAnimation {
        let animation = CAKeyframeAnimation(keyPath: path)
        let end = max(times.last ?? 1, .ulpOfOne)
        animation.keyTimes = times.map { NSNumber(value: $0 / end) }
        animation.values = values
        animation.calculationMode = .linear
        return animation
    }

    /// `q` and `-q` are the same rotation, but interpolating between keys of
    /// opposite sign swings the long way around. Keeps neighbors on the same side.
    static func keyframeAnimation(path: String, times: [Float], rotations: [simd_quatf]) -> CAKeyframeAnimation {
        var previous: simd_quatf?
        let values = rotations.map { rotation -> NSValue in
            var rotation = rotation
            if let previous, simd_dot(previous.vector, rotation.vector) < 0 {
                rotation = simd_quatf(vector: -rotation.vector)
            }
            previous = rotation
            return NSValue(scnVector4: SCNVector4(rotation.vector))
        }
        return keyframeAnimation(path: path, times: times, values: values)
    }

    static func groupAnimation(_ channels: [CAAnimation], duration: CFTimeInterval, loops: Bool) -> SCNAnimation {
        let group = CAAnimationGroup()
        group.animations = channels
        group.duration = duration
        let animation = SCNAnimation(caAnimation: group)
        animation.repeatCount = loops ? .greatestFiniteMagnitude : 1
        animation.blendInDuration = 0.3
        animation.blendOutDuration = 0.3
        animation.isRemovedOnCompletion = true
        return animation
    }
}

extension GLTFAccessor {
    /// Reads a float accessor, returning `count * perElement` values.
    func floats(perElement: Int) throws -> [Float] {
        guard componentType == .float,
              let view = bufferView,
              let data = view.buffer.data
        else { throw AvatarError.unsupportedAccessor }

        let elementSize = perElement * MemoryLayout<Float>.size
        let stride = view.stride > 0 ? view.stride : elementSize
        let start = view.offset + offset
        guard count > 0, start + (count - 1) * stride + elementSize <= data.count else {
            throw AvatarError.unsupportedAccessor
        }

        var result = [Float]()
        result.reserveCapacity(count * perElement)
        data.withUnsafeBytes { bytes in
            for element in 0..<count {
                for component in 0..<perElement {
                    let byteOffset = start + element * stride + component * MemoryLayout<Float>.size
                    result.append(bytes.loadUnaligned(fromByteOffset: byteOffset, as: Float.self))
                }
            }
        }
        return result
    }
}
