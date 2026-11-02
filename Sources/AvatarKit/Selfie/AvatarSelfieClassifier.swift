import Foundation

/// The options a classifier may pick from, taken from the catalog.
public struct AvatarSelfieChoices: Sendable {
    public struct Option: Sendable {
        public let id: String
        public let name: String
        public let genders: [AvatarGender]
    }

    public let hairStyles: [Option]
    public let facialHair: [Option]
    public let hairColors: [AvatarColor]
    public let skinTones: [AvatarColor]

    init(catalog: AvatarCatalog) {
        func options(_ slot: AvatarSlot) -> [Option] {
            catalog.items.filter { $0.slot == slot }.map { Option(id: $0.id, name: $0.name, genders: $0.genders) }
        }
        hairStyles = options(.hair)
        facialHair = options(.facialHair)
        hairColors = catalog.hairColors
        skinTones = catalog.skinTones
    }
}

/// What a classifier saw in the selfie. `nil` item IDs mean none (bald, clean-shaven).
public struct AvatarSelfieTraits: Sendable, Equatable {
    public let gender: AvatarGender
    public let skinTone: AvatarColor?
    public let hairItemID: String?
    public let hairColor: AvatarColor?
    public let facialHairItemID: String?

    public init(gender: AvatarGender, skinTone: AvatarColor?, hairItemID: String?, hairColor: AvatarColor?, facialHairItemID: String?) {
        self.gender = gender
        self.skinTone = skinTone
        self.hairItemID = hairItemID
        self.hairColor = hairColor
        self.facialHairItemID = facialHairItemID
    }
}

/// Looks at a cropped face photo and picks avatar options. Implementations call
/// a vision model; in production that call belongs on your server, not in the app.
public protocol AvatarSelfieClassifier: Sendable {
    func traits(of faceJPEG: Data, choices: AvatarSelfieChoices) async throws -> AvatarSelfieTraits
}
