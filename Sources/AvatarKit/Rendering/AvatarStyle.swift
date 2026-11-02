import SceneKit
import UIKit

/// One look for every part, whatever file it came from: soft studio lights,
/// a matte material model and a blob shadow under the feet.
enum AvatarStyle {
    static let fieldOfView: CGFloat = 28
    /// Below 1 keeps a little of the painted skin's own color variation.
    static let skinRecolorAmount: Float = 0.85

    static func setUpStage(in scene: SCNScene, cameraNode: SCNNode) {
        let camera = SCNCamera()
        camera.fieldOfView = fieldOfView
        camera.zNear = 0.05
        camera.zFar = 50
        cameraNode.camera = camera
        cameraNode.position = SCNVector3(0, 1, 6)
        scene.rootNode.addChildNode(cameraNode)

        let lights: [(SCNLight.LightType, CGFloat, UIColor, SCNVector3)] = [
            (.ambient, 420, UIColor(white: 1, alpha: 1), SCNVector3Zero),
            (.directional, 850, UIColor(red: 1, green: 0.97, blue: 0.92, alpha: 1), SCNVector3(-0.55, 0.45, 0)),
            (.directional, 300, UIColor(red: 0.85, green: 0.9, blue: 1, alpha: 1), SCNVector3(-0.25, -0.9, 0)),
            (.directional, 450, UIColor.white, SCNVector3(-0.35, .pi, 0)),
        ]
        for (type, intensity, color, angles) in lights {
            let light = SCNLight()
            light.type = type
            light.intensity = intensity
            light.color = color
            let node = SCNNode()
            node.light = light
            node.eulerAngles = angles
            scene.rootNode.addChildNode(node)
        }

        let shadow = SCNNode(geometry: SCNPlane(width: 1.1, height: 1.1))
        shadow.eulerAngles.x = -.pi / 2
        shadow.position.y = 0.002
        let material = SCNMaterial()
        material.diffuse.contents = blobShadowImage
        material.lightingModel = .constant
        material.writesToDepthBuffer = false
        shadow.geometry?.firstMaterial = material
        shadow.renderingOrder = -1
        scene.rootNode.addChildNode(shadow)
    }

    /// Matte shading for every material, so parts from different packs match.
    /// The recolor shader goes in now, before the part is first drawn, so later
    /// color changes only update its values.
    static func apply(to node: SCNNode) {
        node.enumerateHierarchy { child, _ in
            for material in child.geometry?.materials ?? [] {
                material.lightingModel = .blinn
                material.specular.contents = UIColor(white: 0.08, alpha: 1)
                material.shininess = 0.2
                material.recolor(to: nil, reference: 0.5, amount: 0)
            }
        }
    }

    /// Points the camera at the whole body, leaving room at the bottom
    /// for controls drawn over the preview.
    static func frame(_ cameraNode: SCNNode, on body: SCNNode) {
        let (minBounds, maxBounds) = body.boundingBox
        let height = max(Float(maxBounds.y - minBounds.y), 1)
        let halfFOV = Float(fieldOfView * .pi / 180) / 2
        let distance = (height * 0.5 * 1.45) / tan(halfFOV)
        let target = SCNVector3(0, minBounds.y + height * 0.4, 0)
        cameraNode.position = SCNVector3(0, target.y + height * 0.05, distance)
        cameraNode.look(at: target)
    }

    private static var blobShadowImage: UIImage {
        let size = CGSize(width: 256, height: 256)
        return UIGraphicsImageRenderer(size: size).image { context in
            let colors = [UIColor(white: 0, alpha: 0.32).cgColor, UIColor(white: 0, alpha: 0).cgColor] as CFArray
            guard let gradient = CGGradient(colorsSpace: CGColorSpaceCreateDeviceRGB(), colors: colors, locations: [0, 1]) else { return }
            let center = CGPoint(x: size.width / 2, y: size.height / 2)
            context.cgContext.drawRadialGradient(
                gradient,
                startCenter: center, startRadius: 0,
                endCenter: center, endRadius: size.width / 2,
                options: []
            )
        }
    }
}

extension SCNNode {
    /// Recolors materials with the given names anywhere under this node.
    /// `nil` restores the original texture.
    func recolor(materials names: [String], to color: AvatarColor?, luminance: [String: Double], amount: Float = 1) {
        guard !names.isEmpty else { return }
        let wanted = Set(names)
        enumerateHierarchy { child, _ in
            for material in child.geometry?.materials ?? [] {
                guard let name = material.name, wanted.contains(name) else { continue }
                material.recolor(to: color, reference: luminance[name] ?? 0.5, amount: amount)
            }
        }
    }
}

extension SCNMaterial {
    /// Replaces the hue of the base color but keeps its light and dark detail:
    /// each pixel becomes `color * (pixel brightness / texture's mean brightness)`.
    /// Works in linear space, where SceneKit shades.
    func recolor(to color: AvatarColor?, reference: Double, amount: Float) {
        installRecolorShader()
        let (red, green, blue) = color?.components ?? (1, 1, 1)
        setValue(NSValue(scnVector3: SCNVector3(red.linear, green.linear, blue.linear)), forKey: "avatarTint")
        setValue(NSNumber(value: max(reference.linear, 0.02)), forKey: "avatarReference")
        setValue(NSNumber(value: color == nil ? 0 : amount), forKey: "avatarAmount")
    }

    /// GLTFKit2 already sets a surface modifier (it un-premultiplies alpha), so the
    /// recolor code runs after it in the same modifier.
    private func installRecolorShader() {
        var modifiers = shaderModifiers ?? [:]
        let existing = modifiers[.surface] ?? ""
        guard !existing.contains("avatarTint") else { return }

        var pragmas = ""
        var body = existing
        if let range = existing.range(of: "#pragma body") {
            pragmas = String(existing[..<range.lowerBound])
            body = String(existing[range.upperBound...])
        }
        modifiers[.surface] = pragmas + Self.recolorArguments + "#pragma body\n" + body + "\n" + Self.recolorBody
        shaderModifiers = modifiers
    }

    private static let recolorArguments = """
    #pragma arguments
    float3 avatarTint;
    float avatarReference;
    float avatarAmount;

    """

    private static let recolorBody = """
    float brightness = dot(_surface.diffuse.rgb, float3(0.2126, 0.7152, 0.0722));
    float3 recolored = avatarTint * clamp(brightness / avatarReference, 0.0, 2.2);
    _surface.diffuse.rgb = mix(_surface.diffuse.rgb, recolored, avatarAmount);
    """
}

private extension Double {
    /// sRGB to linear.
    var linear: Float {
        Float(self <= 0.04045 ? self / 12.92 : pow((self + 0.055) / 1.055, 2.4))
    }
}

extension UIColor {
    convenience init(_ color: AvatarColor) {
        let (red, green, blue) = color.components
        self.init(red: red, green: green, blue: blue, alpha: 1)
    }
}
