import UIKit

@MainActor
public final class AvatarSelfieViewModel: ObservableObject {
    public enum State: Equatable {
        case idle
        case analyzing
        case failed(String)
    }

    @Published public private(set) var state: State = .idle
    @Published public var isCameraPresented = false

    private let analyzer: AvatarSelfieAnalyzer
    private let onFinish: (AvatarSelfieResult?) -> Void
    private var task: Task<Void, Never>?

    /// `onFinish` gets `nil` when the user skips.
    public init(analyzer: AvatarSelfieAnalyzer, onFinish: @escaping (AvatarSelfieResult?) -> Void) {
        self.analyzer = analyzer
        self.onFinish = onFinish
    }

    deinit {
        task?.cancel()
    }

    public var isCameraAvailable: Bool {
        UIImagePickerController.isSourceTypeAvailable(.camera)
    }

    public func analyze(_ image: UIImage) {
        task?.cancel()
        state = .analyzing
        task = Task {
            do {
                let result = try await analyzer.avatar(from: image)
                try Task.checkCancellation()
                state = .idle
                onFinish(result)
            } catch is CancellationError {
                state = .idle
            } catch {
                state = .failed(error.localizedDescription)
            }
        }
    }

    public func analyzePhoto(data: Data) {
        guard let image = UIImage(data: data) else {
            state = .failed(AvatarSelfieError.unreadableImage.localizedDescription)
            return
        }
        analyze(image)
    }

    public func skip() {
        task?.cancel()
        onFinish(nil)
    }
}
