import AvatarKit
import SwiftUI

@main
struct AvatarDemoApp: App {
    var body: some Scene {
        WindowGroup {
            RootView()
        }
    }
}

struct RootView: View {
    private let bakeoff: (bakeoff: Bakeoff, folder: URL)?
    private let catalog: Result<AvatarCatalog, Error>

    init() {
        let bakeoff = Bakeoff.bundled()
        self.bakeoff = bakeoff
        catalog = Result {
            let catalog = try AvatarCatalog.bundled()
            return bakeoff.map { catalog.adding($0.bakeoff, from: $0.folder) } ?? catalog
        }
    }

    var body: some View {
        switch catalog {
        case .success(let catalog):
            DemoFlowView(catalog: catalog, bakeoff: bakeoff?.bakeoff)
        case .failure(let error):
            Text(error.localizedDescription)
                .padding()
        }
    }
}

/// Selfie first when there's no saved avatar, then the editor.
struct DemoFlowView: View {
    let catalog: AvatarCatalog
    let bakeoff: Bakeoff?

    @State private var config: AvatarConfig? = DemoStorage.savedConfig
    @State private var notice: String?

    var body: some View {
        if let config {
            EditorScreen(catalog: catalog, config: config, bakeoff: bakeoff, notice: $notice)
        } else {
            NavigationStack {
                SelfieScreen(catalog: catalog) { result in
                    notice = result.flatMap(DemoStorage.notice(for:))
                    config = result?.config ?? catalog.defaultConfig(for: .female)
                }
                .toolbar { APIKeyButton() }
            }
        }
    }
}

struct SelfieScreen: View {
    @StateObject private var viewModel: AvatarSelfieViewModel

    init(catalog: AvatarCatalog, onFinish: @escaping (AvatarSelfieResult?) -> Void) {
        let classifier = DemoStorage.apiKey.map { ClaudeSelfieClassifier(apiKey: $0) }
        let analyzer = AvatarSelfieAnalyzer(catalog: catalog, classifier: classifier)
        _viewModel = StateObject(wrappedValue: AvatarSelfieViewModel(analyzer: analyzer, onFinish: onFinish))
    }

    var body: some View {
        AvatarSelfieView(viewModel: viewModel)
            .task {
                if let image = DemoLaunchOptions.selfieImage {
                    viewModel.analyze(image)
                }
            }
    }
}

/// Lets you paste an Anthropic API key in the demo. Production apps should call
/// the model from their backend instead of shipping a key.
struct APIKeyButton: View {
    @State private var isPresented = false
    @State private var key = DemoStorage.apiKey ?? ""

    var body: some View {
        Button {
            isPresented = true
        } label: {
            Image(systemName: DemoStorage.apiKey == nil ? "key" : "key.fill")
        }
        .alert("Anthropic API key", isPresented: $isPresented) {
            SecureField("sk-ant-...", text: $key)
            Button("Save") { DemoStorage.apiKey = key }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("Used to pick hairstyle, hair color and beard from the selfie. Without it, only skin tone is matched.")
        }
    }
}

enum DemoStorage {
    private static let configKey = "avatarConfig"
    private static let apiKeyKey = "anthropicAPIKey"

    static var savedConfig: AvatarConfig? {
        DemoLaunchOptions.config
            ?? UserDefaults.standard.string(forKey: configKey).flatMap(AvatarConfig.init(jsonString:))
    }

    static func save(_ config: AvatarConfig) {
        UserDefaults.standard.set(config.jsonString, forKey: configKey)
    }

    static var apiKey: String? {
        get {
            let value = ProcessInfo.processInfo.environment["ANTHROPIC_API_KEY"]
                ?? UserDefaults.standard.string(forKey: apiKeyKey)
            return value?.trimmingCharacters(in: .whitespacesAndNewlines).nilIfEmpty
        }
        set { UserDefaults.standard.set(newValue?.trimmingCharacters(in: .whitespacesAndNewlines), forKey: apiKeyKey) }
    }

    static func notice(for result: AvatarSelfieResult) -> String? {
        result.usedClassifier ? nil : "Matched skin tone only. Add an API key (key button) to also match hair and beard."
    }
}

private extension String {
    var nilIfEmpty: String? { isEmpty ? nil : self }
}
