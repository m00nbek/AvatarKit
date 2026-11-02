import CoreML
import UIKit
import Vision

public enum AvatarSelfieError: LocalizedError, Equatable {
    case unreadableImage
    case noFace
    case classifierFailed(String)

    public var errorDescription: String? {
        switch self {
        case .unreadableImage: "Couldn't read that photo."
        case .noFace: "No face found. Try a well-lit photo looking at the camera."
        case .classifierFailed(let reason): "Couldn't analyze the photo: \(reason)"
        }
    }
}

public struct AvatarSelfieResult: Sendable {
    public let config: AvatarConfig
    /// False when only on-device analysis ran (skin tone), with no classifier.
    public let usedClassifier: Bool
}

/// Turns a selfie into a starting avatar.
///
/// On the device: Vision finds the face and the skin tone is sampled from the
/// cheeks. A classifier, when given, picks hairstyle, hair color, facial hair
/// and body from the catalog. The photo itself is never stored.
public struct AvatarSelfieAnalyzer: Sendable {
    private let catalog: AvatarCatalog
    private let classifier: (any AvatarSelfieClassifier)?

    public init(catalog: AvatarCatalog, classifier: (any AvatarSelfieClassifier)?) {
        self.catalog = catalog
        self.classifier = classifier
    }

    public func avatar(from image: UIImage) async throws -> AvatarSelfieResult {
        guard let photo = image.uprightCGImage(maxSide: 1280) else { throw AvatarSelfieError.unreadableImage }
        let face = try await Task.detached(priority: .userInitiated) { try photo.largestFace() }.value

        var traits: AvatarSelfieTraits?
        if let classifier, let crop = photo.faceCropJPEG(around: face) {
            traits = try await classifier.traits(of: crop, choices: AvatarSelfieChoices(catalog: catalog))
        }

        // A gender without a body yet falls back to one that has one.
        var config = catalog.defaultConfig(for: traits?.gender ?? catalog.genders.first ?? .female)
        // The classifier sees the whole photo and can allow for lighting; pixels are the fallback.
        if let tone = traits?.skinTone ?? photo.skinColor(in: face).flatMap(catalog.skinTones.closest(to:)) {
            config.skinTone = tone
        }
        if let traits {
            config.items[.hair] = selection(traits.hairItemID, color: traits.hairColor, for: config)
            config.items[.facialHair] = selection(traits.facialHairItemID, color: traits.hairColor, for: config)
        }
        return AvatarSelfieResult(config: config, usedClassifier: traits != nil)
    }

    private func selection(_ itemID: String?, color: AvatarColor?, for config: AvatarConfig) -> AvatarItemSelection? {
        guard let itemID, let item = catalog.item(id: itemID), item.genders.contains(config.gender) else {
            return config.items[catalog.item(id: itemID ?? "")?.slot ?? .hair]
        }
        return AvatarItemSelection(itemID: item.id, color: item.isTintable ? color : nil)
    }
}

// MARK: - Image helpers

extension UIImage {
    /// Pixels rotated to match `imageOrientation`, scaled down to `maxSide`.
    func uprightCGImage(maxSide: CGFloat) -> CGImage? {
        let scale = min(1, maxSide / max(size.width, size.height))
        let target = CGSize(width: (size.width * scale).rounded(), height: (size.height * scale).rounded())
        let format = UIGraphicsImageRendererFormat()
        format.scale = 1
        return UIGraphicsImageRenderer(size: target, format: format).image { _ in
            draw(in: CGRect(origin: .zero, size: target))
        }.cgImage
    }
}

extension CGImage {
    func largestFace() throws -> VNFaceObservation {
        let request = VNDetectFaceRectanglesRequest()
        #if targetEnvironment(simulator)
        // The Simulator can't create Vision's default inference context; the CPU works.
        if let cpu = MLComputeDevice.allComputeDevices.first(where: { if case .cpu = $0 { true } else { false } }) {
            request.setComputeDevice(cpu, for: .main)
        }
        #endif
        try VNImageRequestHandler(cgImage: self).perform([request])
        guard let face = request.results?.max(by: { $0.boundingBox.area < $1.boundingBox.area }) else {
            throw AvatarSelfieError.noFace
        }
        return face
    }

    /// The face with room for hair above it, as a small JPEG for the classifier.
    func faceCropJPEG(around face: VNFaceObservation) -> Data? {
        let box = pixelRect(face.boundingBox)
        let crop = CGRect(
            x: box.midX - box.width * 0.95,
            y: box.minY - box.height * 0.75,
            width: box.width * 1.9,
            height: box.height * 2.0
        ).intersection(CGRect(x: 0, y: 0, width: width, height: height))
        guard let cropped = cropping(to: crop.integral) else { return nil }
        return UIImage(cgImage: cropped).uprightCGImage(maxSide: 768)
            .flatMap { UIImage(cgImage: $0).jpegData(compressionQuality: 0.8) }
    }

    /// Median color of the cheekbones and forehead, ignoring shadows and highlights.
    /// Kept away from the nose and mouth, where smile lines cast shadows.
    func skinColor(in face: VNFaceObservation) -> AvatarColor? {
        let box = pixelRect(face.boundingBox)
        let patches = [(0.14, 0.40, 0.16), (0.70, 0.40, 0.16), (0.38, 0.04, 0.24)].map { x, y, size in
            CGRect(x: box.minX + box.width * x, y: box.minY + box.height * y, width: box.width * size, height: box.height * 0.12)
        }
        guard let pixels = rgbaPixels() else { return nil }

        var reds: [Double] = [], greens: [Double] = [], blues: [Double] = []
        for patch in patches {
            for y in Int(patch.minY)..<Int(patch.maxY) where y >= 0 && y < height {
                for x in Int(patch.minX)..<Int(patch.maxX) where x >= 0 && x < width {
                    let i = (y * width + x) * 4
                    let (r, g, b) = (Double(pixels[i]) / 255, Double(pixels[i + 1]) / 255, Double(pixels[i + 2]) / 255)
                    let brightness = 0.299 * r + 0.587 * g + 0.114 * b
                    guard brightness > 0.12, brightness < 0.95 else { continue }
                    reds.append(r); greens.append(g); blues.append(b)
                }
            }
        }
        guard reds.count > 50 else { return nil }
        return AvatarColor(red: reds.median, green: greens.median, blue: blues.median)
    }

    /// Vision's normalized rect (origin bottom-left) in pixel coordinates (origin top-left).
    private func pixelRect(_ normalized: CGRect) -> CGRect {
        CGRect(
            x: normalized.minX * CGFloat(width),
            y: (1 - normalized.maxY) * CGFloat(height),
            width: normalized.width * CGFloat(width),
            height: normalized.height * CGFloat(height)
        )
    }

    private func rgbaPixels() -> [UInt8]? {
        var pixels = [UInt8](repeating: 0, count: width * height * 4)
        let drawn = pixels.withUnsafeMutableBytes { buffer -> Bool in
            guard let context = CGContext(
                data: buffer.baseAddress, width: width, height: height, bitsPerComponent: 8,
                bytesPerRow: width * 4, space: CGColorSpaceCreateDeviceRGB(),
                bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
            ) else { return false }
            context.draw(self, in: CGRect(x: 0, y: 0, width: width, height: height))
            return true
        }
        return drawn ? pixels : nil
    }
}

extension CGRect {
    var area: CGFloat { width * height }
}

extension Array where Element == Double {
    var median: Double {
        let sorted = sorted()
        return sorted.isEmpty ? 0 : sorted[sorted.count / 2]
    }
}

// MARK: - Color matching

extension AvatarColor {
    init?(red: Double, green: Double, blue: Double) {
        func byte(_ value: Double) -> Int { Int((min(max(value, 0), 1) * 255).rounded()) }
        self.init(hex: String(format: "#%02X%02X%02X", byte(red), byte(green), byte(blue)))
    }

    /// CIE L*a*b*, where distances roughly match how different colors look.
    var lab: (l: Double, a: Double, b: Double) {
        func linear(_ c: Double) -> Double { c <= 0.04045 ? c / 12.92 : pow((c + 0.055) / 1.055, 2.4) }
        let (r, g, b) = (linear(components.red), linear(components.green), linear(components.blue))
        let x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
        let y = 0.2126 * r + 0.7152 * g + 0.0722 * b
        let z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883
        func f(_ t: Double) -> Double { t > 0.008856 ? cbrt(t) : 7.787 * t + 16.0 / 116 }
        return (116 * f(y) - 16, 500 * (f(x) - f(y)), 200 * (f(y) - f(z)))
    }

    /// Perceptual distance that weighs lightness most: in photos, white balance
    /// shifts hue far more than it shifts how light or dark the skin is.
    func distance(to other: AvatarColor) -> Double {
        let (a, b) = (lab, other.lab)
        let chroma = (a.a - b.a) * (a.a - b.a) + (a.b - b.b) * (a.b - b.b)
        return ((a.l - b.l) * (a.l - b.l) + 0.35 * chroma).squareRoot()
    }
}

extension Array where Element == AvatarColor {
    func closest(to color: AvatarColor) -> AvatarColor? {
        self.min { $0.distance(to: color) < $1.distance(to: color) }
    }
}
