import Foundation

public enum AvatarEditorTab: Hashable, Identifiable, Sendable {
    case body
    case face
    case skin
    case slot(AvatarSlot)

    public var id: String {
        switch self {
        case .body: "body"
        case .face: "face"
        case .skin: "skin"
        case .slot(let slot): slot.rawValue
        }
    }

    public var title: String {
        switch self {
        case .body: "Body"
        case .face: "Face"
        case .skin: "Skin"
        case .slot(let slot): slot.title
        }
    }
}

@MainActor
public final class AvatarEditorViewModel: ObservableObject {
    @Published public private(set) var config: AvatarConfig
    @Published public var selectedTab: AvatarEditorTab = .body

    public let stage: AvatarStage
    private let onSave: (AvatarConfig) -> Void

    public init(config: AvatarConfig?, catalog: AvatarCatalog, onSave: @escaping (AvatarConfig) -> Void) {
        let saved = config.flatMap { catalog.body(for: $0.gender) == nil ? nil : $0 }
        let initial = saved ?? catalog.defaultConfig(for: config?.gender ?? .female)
        self.config = initial
        self.stage = AvatarStage(catalog: catalog)
        self.onSave = onSave
        stage.show(initial)
    }

    public var catalog: AvatarCatalog { stage.catalog }

    /// Body, skin, then only the slots that have items for the current gender.
    public var tabs: [AvatarEditorTab] {
        let body = catalog.body(for: config.gender)
        let recolorsSkin = body?.skinMaterials.isEmpty == false
        let hasFaces = body?.faces?.isEmpty == false
        return [.body] + (hasFaces ? [.face] : []) + (recolorsSkin ? [.skin] : []) + AvatarSlot.allCases
            .filter { !catalog.items(in: $0, for: config).isEmpty }
            .map { .slot($0) }
    }

    public func items(in slot: AvatarSlot) -> [AvatarItem] {
        catalog.items(in: slot, for: config)
    }

    public var bodyTypes: [AvatarBodyType] {
        catalog.bodyTypes(for: config.gender)
    }

    public var genders: [AvatarGender] {
        catalog.genders
    }

    public func colors(for slot: AvatarSlot) -> [AvatarColor] {
        catalog.colors(for: slot)
    }

    public func isSelected(_ item: AvatarItem) -> Bool {
        config.items[item.slot]?.itemID == item.id
    }

    /// The item in a slot that can be recolored, if any.
    public func tintableSelection(in slot: AvatarSlot) -> (item: AvatarItem, color: AvatarColor?)? {
        guard let selection = config.items[slot],
              let item = catalog.item(id: selection.itemID),
              item.isTintable
        else { return nil }
        return (item, selection.color ?? item.defaultColor)
    }

    // MARK: - Edits

    /// Clothes are made per gender, so switching starts from that gender's
    /// default outfit and keeps the skin tone.
    public func setGender(_ gender: AvatarGender) {
        guard gender != config.gender else { return }
        var updated = catalog.defaultConfig(for: gender)
        updated.skinTone = config.skinTone
        apply(updated)
    }

    /// Keeps everything worn: items take on the body type too.
    public func setBodyType(_ bodyType: AvatarBodyType) {
        guard bodyType != config.bodyType else { return }
        var updated = config
        updated.bodyType = bodyType
        apply(updated)
    }

    /// The body's own face first, then its alternatives.
    public var faces: [AvatarFaceAsset] {
        catalog.body(for: config.gender)?.faces ?? []
    }

    public func setFace(_ faceID: String?) {
        var updated = config
        updated.face = faceID
        apply(updated)
    }

    public func setSkinTone(_ color: AvatarColor) {
        var updated = config
        updated.skinTone = color
        apply(updated)
    }

    /// Selecting the worn item again takes it off.
    public func toggle(_ item: AvatarItem) {
        var updated = config
        if isSelected(item) {
            updated.items[item.slot] = nil
        } else {
            let keptColor = updated.items[item.slot]?.color
            updated.items[item.slot] = AvatarItemSelection(itemID: item.id, color: item.isTintable ? keptColor : nil)
        }
        apply(updated)
    }

    public func setColor(_ color: AvatarColor, in slot: AvatarSlot) {
        guard var selection = config.items[slot] else { return }
        selection.color = color
        var updated = config
        updated.items[slot] = selection
        apply(updated)
    }

    public func randomize() {
        var updated = config
        updated.skinTone = catalog.skinTones.randomElement() ?? updated.skinTone
        updated.bodyType = bodyTypes.randomElement() ?? updated.bodyType
        updated.items = [:]
        for slot in AvatarSlot.allCases {
            guard let item = catalog.items(in: slot, for: updated).randomElement(), Bool.random() || slot != .headwear else { continue }
            updated.items[slot] = AvatarItemSelection(
                itemID: item.id,
                color: item.isTintable ? colors(for: slot).randomElement() : nil
            )
        }
        apply(updated)
    }

    /// Replaces the whole avatar, e.g. with one made from a selfie.
    public func replace(with config: AvatarConfig) {
        apply(config)
    }

    public func play(_ move: AvatarMove) {
        stage.play(move)
    }

    public func save() {
        onSave(config)
    }

    private func apply(_ updated: AvatarConfig) {
        config = updated
        stage.show(updated)
        if !tabs.contains(selectedTab) {
            selectedTab = .body
        }
    }
}
