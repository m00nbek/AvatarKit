import Foundation

public enum AvatarGender: String, Codable, CaseIterable, Identifiable, Sendable {
    case male
    case female

    public var id: String { rawValue }

    public var title: String {
        switch self {
        case .male: "Man"
        case .female: "Woman"
        }
    }
}

/// Body builds. Each is a shape of the gender's one base body (a morph target named
/// like the case; `normal` is the base itself), so every item fits every build.
public enum AvatarBodyType: String, Codable, CaseIterable, Identifiable, Sendable {
    case verySkinny
    case skinny
    case normal
    case heavy

    public var id: String { rawValue }

    public var title: String {
        switch self {
        case .verySkinny: "Very skinny"
        case .skinny: "Skinny"
        case .normal: "Normal"
        case .heavy: "Heavy"
        }
    }

    /// Unknown values (the old `regular`, `athletic`, `teen`) read as `normal`, so saved avatars still load.
    public init(from decoder: Decoder) throws {
        let value = try decoder.singleValueContainer().decode(String.self)
        self = AvatarBodyType(rawValue: value) ?? .normal
    }
}

/// Parts of the body mesh. Clothing hides the regions it covers so the
/// body doesn't poke through.
public enum AvatarBodyRegion: String, Codable, CaseIterable, Sendable {
    case head
    case torso
    case pelvis
    case upperArms
    case forearms
    case wrists
    case hands
    case thighs
    case calves
    case ankles
    case feet
}
