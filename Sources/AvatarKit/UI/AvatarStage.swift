import SceneKit
import SwiftUI

/// Owns one 3D avatar. Show it with `AvatarView(stage:)`, change it with
/// `show(_:)` and animate it with `play(_:)`.
///
/// Config changes are coalesced: while one is loading, only the latest
/// pending config is applied next.
@MainActor
public final class AvatarStage: ObservableObject {
    @Published public private(set) var isLoading = false
    @Published public private(set) var error: AvatarError?

    public let catalog: AvatarCatalog
    let controller: AvatarSceneController

    private var pendingConfig: AvatarConfig?
    private var applyTask: Task<Void, Never>?

    public init(catalog: AvatarCatalog) {
        self.catalog = catalog
        self.controller = AvatarSceneController(catalog: catalog)
    }

    deinit {
        applyTask?.cancel()
    }

    public func show(_ config: AvatarConfig) {
        pendingConfig = config
        guard applyTask == nil else { return }
        applyTask = Task { [weak self] in
            await self?.drainPendingConfigs()
        }
    }

    public func play(_ move: AvatarMove) {
        Task { await controller.play(move) }
    }

    private func drainPendingConfigs() async {
        isLoading = true
        while let config = pendingConfig, !Task.isCancelled {
            pendingConfig = nil
            do {
                try await controller.apply(config)
                error = nil
            } catch let avatarError as AvatarError {
                error = avatarError
            } catch {
                self.error = .loadingFailed(error.localizedDescription)
            }
        }
        isLoading = false
        applyTask = nil
    }
}
