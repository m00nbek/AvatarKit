import Foundation

/// Where an item is worn. One item per slot.
public enum AvatarSlot: String, Codable, CaseIterable, Identifiable, Sendable, CodingKeyRepresentable {
    case hair
    case facialHair
    case headwear
    case top
    case bottom
    case shoes
    case accessory

    public var id: String { rawValue }

    public var title: String {
        switch self {
        case .hair: "Hair"
        case .facialHair: "Beard"
        case .headwear: "Hats"
        case .top: "Tops"
        case .bottom: "Bottoms"
        case .shoes: "Shoes"
        case .accessory: "Extras"
        }
    }

    /// Hair-like slots use the hair color list instead of the clothing palette.
    public var usesHairColors: Bool {
        self == .hair || self == .facialHair
    }
}

public struct AvatarItemSelection: Codable, Equatable, Hashable, Sendable {
    public var itemID: String
    public var color: AvatarColor?

    public init(itemID: String, color: AvatarColor? = nil) {
        self.itemID = itemID
        self.color = color
    }
}

/// What the server stores for a user's avatar. Small, readable JSON;
/// the 3D files are assembled from the catalog on the device.
public struct AvatarConfig: Codable, Equatable, Hashable, Sendable {
    public var gender: AvatarGender
    public var bodyType: AvatarBodyType
    public var skinTone: AvatarColor
    public var items: [AvatarSlot: AvatarItemSelection]
    /// A face from the body's `faces`; `nil` is the body's own.
    public var face: String?

    public init(
        gender: AvatarGender,
        bodyType: AvatarBodyType,
        skinTone: AvatarColor,
        items: [AvatarSlot: AvatarItemSelection] = [:],
        face: String? = nil
    ) {
        self.gender = gender
        self.bodyType = bodyType
        self.skinTone = skinTone
        self.items = items
        self.face = face
    }

    public var jsonString: String? {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        guard let data = try? encoder.encode(self) else { return nil }
        return String(data: data, encoding: .utf8)
    }

    public init?(jsonString: String) {
        guard let data = jsonString.data(using: .utf8),
              let config = try? JSONDecoder().decode(AvatarConfig.self, from: data)
        else { return nil }
        self = config
    }
}
