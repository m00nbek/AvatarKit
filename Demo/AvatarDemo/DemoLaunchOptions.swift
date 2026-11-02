import AvatarKit
import UIKit

/// Launch arguments for checking states without tapping, e.g.
/// `-demoTab top -demoMove wave -demoSelfie /path/to/photo.jpg -demoGenerator pixal3d_mv`.
/// A config can be set with `defaults write <bundle id> demoConfig -string '{...}'`.
enum DemoLaunchOptions {
    static var config: AvatarConfig? {
        UserDefaults.standard.string(forKey: "demoConfig").flatMap(AvatarConfig.init(jsonString:))
    }

    static var selfieImage: UIImage? {
        UserDefaults.standard.string(forKey: "demoSelfie").flatMap(UIImage.init(contentsOfFile:))
    }

    @MainActor
    static func run(on viewModel: AvatarEditorViewModel, bakeoff: Bakeoff?) async {
        let defaults = UserDefaults.standard
        if let bakeoff,
           let generator = bakeoff.generators.first(where: { $0.id == defaults.string(forKey: "demoGenerator") }),
           let body = viewModel.catalog.bodies.first(where: { $0.id == bakeoff.body }) {
            viewModel.replace(with: generator.dress(viewModel.config, body: body))
        }
        if let slot = defaults.string(forKey: "demoTab").flatMap(AvatarSlot.init(rawValue:)) {
            viewModel.selectedTab = .slot(slot)
        }
        if let move = defaults.string(forKey: "demoMove").flatMap(AvatarMove.init(rawValue:)) {
            try? await Task.sleep(for: .seconds(defaults.double(forKey: "demoDelay").nonZero ?? 2))
            viewModel.play(move)
        }
    }
}

private extension Double {
    var nonZero: Double? { self == 0 ? nil : self }
}
