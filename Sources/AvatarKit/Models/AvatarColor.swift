import Foundation

/// An sRGB color stored as `#RRGGBB`, so configs stay readable JSON.
public struct AvatarColor: Hashable, Sendable {
    public let hex: String

    public init?(hex: String) {
        let digits = hex.hasPrefix("#") ? String(hex.dropFirst()) : hex
        guard digits.count == 6, UInt32(digits, radix: 16) != nil else { return nil }
        self.hex = "#" + digits.uppercased()
    }

    /// Red, green and blue in 0...1.
    public var components: (red: Double, green: Double, blue: Double) {
        let value = UInt32(hex.dropFirst(), radix: 16) ?? 0
        return (
            Double((value >> 16) & 0xFF) / 255,
            Double((value >> 8) & 0xFF) / 255,
            Double(value & 0xFF) / 255
        )
    }
}

extension AvatarColor: Codable {
    public init(from decoder: any Decoder) throws {
        let container = try decoder.singleValueContainer()
        let raw = try container.decode(String.self)
        guard let color = AvatarColor(hex: raw) else {
            throw DecodingError.dataCorruptedError(in: container, debugDescription: "Invalid color \(raw)")
        }
        self = color
    }

    public func encode(to encoder: any Encoder) throws {
        var container = encoder.singleValueContainer()
        try container.encode(hex)
    }
}
