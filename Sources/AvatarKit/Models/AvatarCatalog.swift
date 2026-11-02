import Foundation

/// Everything the avatar creator can show: bodies, items, animations and palettes.
/// Loaded from `catalog.json`; file paths are relative to that file.
public struct AvatarCatalog: Codable, Sendable {
    public var rig: AvatarRigNames
    public var bodies: [AvatarBodyAsset]
    public var items: [AvatarItem]
    public var moves: [AvatarMoveAsset]
    /// Mean brightness (0...1) of each material's base color texture. Recoloring
    /// scales by it, so a texture keeps its light and dark detail in the new color.
    public var materialLuminance: [String: Double]
    public var skinTones: [AvatarColor]
    public var hairColors: [AvatarColor]
    public var palette: [AvatarColor]
    public var defaults: [AvatarDefault]

    /// Folder the catalog was loaded from. Not part of the JSON.
    public var baseURL: URL = URL(fileURLWithPath: "/")

    enum CodingKeys: String, CodingKey {
        case rig, bodies, items, moves, materialLuminance, skinTones, hairColors, palette, defaults
    }

    public static func load(from url: URL) throws -> AvatarCatalog {
        let data = try Data(contentsOf: url)
        var catalog = try JSONDecoder().decode(AvatarCatalog.self, from: data)
        catalog.baseURL = url.deletingLastPathComponent()
        return catalog
    }

    /// The catalog shipped inside the package.
    public static func bundled() throws -> AvatarCatalog {
        guard let url = Bundle.module.url(forResource: "catalog", withExtension: "json", subdirectory: "Avatar") else {
            throw AvatarError.missingCatalog
        }
        return try load(from: url)
    }

    /// A catalog file path, relative to the catalog; an absolute path is used as is.
    public func url(for file: String) -> URL {
        file.hasPrefix("/") ? URL(fileURLWithPath: file) : baseURL.appending(path: file)
    }

    public func body(for gender: AvatarGender) -> AvatarBodyAsset? {
        bodies.first { $0.gender == gender }
    }

    /// Genders that have a body, in the catalog's order.
    public var genders: [AvatarGender] {
        bodies.map(\.gender)
    }

    public func item(id: String) -> AvatarItem? {
        items.first { $0.id == id }
    }

    /// Items for a slot made for the config's gender. They fit every body type.
    public func items(in slot: AvatarSlot, for config: AvatarConfig) -> [AvatarItem] {
        items.filter { $0.slot == slot && $0.genders.contains(config.gender) }
    }

    public func bodyTypes(for gender: AvatarGender) -> [AvatarBodyType] {
        body(for: gender)?.bodyTypes ?? []
    }

    public func colors(for slot: AvatarSlot) -> [AvatarColor] {
        slot.usesHairColors ? hairColors : palette
    }

    public func move(_ move: AvatarMove) -> AvatarMoveAsset? {
        moves.first { $0.move == move }
    }

    public var availableMoves: [AvatarMove] {
        AvatarMove.allCases.filter { move($0) != nil }
    }

    /// The listed default for this gender. A gender without a body falls back to the
    /// first one that has one; a gender without a listed default starts bare.
    public func defaultConfig(for gender: AvatarGender) -> AvatarConfig {
        let gender = body(for: gender) == nil ? genders.first ?? gender : gender
        return defaults.first { $0.config.gender == gender }?.config
            ?? AvatarConfig(gender: gender, bodyType: .normal, skinTone: skinTones.first ?? .fallbackSkin)
    }
}

/// Bone names the renderer needs to know about. Kept in the catalog because
/// every skeleton names them differently.
public struct AvatarRigNames: Codable, Sendable {
    public var hips: String
    public var head: String
    public var leftUpperArm: String
    public var rightUpperArm: String
    public var rightForearm: String
    public var rightHand: String
    public var rightIndex: String
    public var rightPinky: String
}

/// A gender's base body. Its body types are morph targets of the one mesh, and its
/// items carry the same targets, so they keep fitting.
public struct AvatarBodyAsset: Codable, Sendable, Identifiable {
    public var gender: AvatarGender
    public var file: String
    /// Builds this body has, `normal` included.
    public var bodyTypes: [AvatarBodyType]
    /// Materials recolored by the skin tone.
    public var skinMaterials: [String]
    /// Materials that follow the hair color (eyebrows).
    public var hairMaterials: [String]
    /// Alternative textures for the same mesh: other faces and skin, every item still fits.
    public var faces: [AvatarFaceAsset]?
    /// Materials whose texture a face replaces.
    public var faceMaterials: [String]?

    public var id: String { gender.rawValue }
}

public struct AvatarFaceAsset: Codable, Sendable, Identifiable, Hashable {
    public var id: String
    public var name: String
    /// Base color texture, relative to the catalog.
    public var texture: String
}

public struct AvatarItem: Codable, Sendable, Identifiable, Hashable {
    public var id: String
    public var name: String
    public var slot: AvatarSlot
    public var file: String
    public var genders: [AvatarGender]
    /// Materials recolored by the user's color pick. Empty means the item can't be recolored.
    public var tintMaterials: [String]
    /// Materials recolored by the skin tone (bare arms that come with a top, for example).
    public var skinMaterials: [String]
    /// Body regions this item covers; they're hidden while it's worn.
    public var hides: [AvatarBodyRegion]
    /// Slots hidden while this item is worn (a hood hides the hair).
    public var hidesSlots: [AvatarSlot]
    /// Color used when the user hasn't picked one. `nil` keeps the original texture.
    public var defaultColor: AvatarColor?

    public var isTintable: Bool { !tintMaterials.isEmpty }
}

public struct AvatarMoveAsset: Codable, Sendable {
    public var move: AvatarMove
    /// GLB with the clip. `nil` means the move is generated in code (the wave).
    public var file: String?
    public var clip: String?
    public var loops: Bool
}

public struct AvatarDefault: Codable, Sendable {
    public var config: AvatarConfig
}

extension AvatarColor {
    static let fallbackSkin = AvatarColor(hex: "#E0AC69")!
}
