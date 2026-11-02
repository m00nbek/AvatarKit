import AvatarKit
import Foundation

/// Outfit A as other 3D generators made it, cut into items for the same base body as
/// Tripo's (`Tools/bakeoff/build-bakeoff.mjs`). Only in the demo: the files are large.
struct Bakeoff: Decodable {
    let body: String
    let base: String
    let generators: [BakeoffGenerator]
    let items: [AvatarItem]

    static func bundled() -> (bakeoff: Bakeoff, folder: URL)? {
        guard let url = Bundle.main.url(forResource: "bakeoff", withExtension: "json", subdirectory: "Bakeoff"),
              let data = try? Data(contentsOf: url),
              let bakeoff = try? JSONDecoder().decode(Bakeoff.self, from: data) else { return nil }
        return (bakeoff, url.deletingLastPathComponent())
    }
}

struct BakeoffGenerator: Decodable, Identifiable, Hashable {
    let id: String
    let name: String
    /// Item ID per slot (`AvatarSlot` raw value).
    let items: [String: String]

    func dress(_ config: AvatarConfig, body: AvatarBodyAsset) -> AvatarConfig {
        var config = config
        config.gender = body.gender
        for (slot, itemID) in items {
            guard let slot = AvatarSlot(rawValue: slot) else { continue }
            config.items[slot] = AvatarItemSelection(itemID: itemID)
        }
        return config
    }

    func isWorn(in config: AvatarConfig) -> Bool {
        items.allSatisfy { slot, itemID in
            AvatarSlot(rawValue: slot).flatMap { config.items[$0]?.itemID } == itemID
        }
    }
}

extension AvatarCatalog {
    /// The catalog with the bake-off's items, and its base body (which has patches for them) in place of the bundled one.
    func adding(_ bakeoff: Bakeoff, from folder: URL) -> AvatarCatalog {
        var catalog = self
        if let index = catalog.bodies.firstIndex(where: { $0.id == bakeoff.body }) {
            catalog.bodies[index].file = folder.appending(path: bakeoff.base).path
        }
        catalog.items += bakeoff.items.map { item in
            var item = item
            item.file = folder.appending(path: item.file).path
            return item
        }
        return catalog
    }
}
