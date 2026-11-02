import PhotosUI
import SwiftUI

/// First step of avatar setup: take a selfie (or pick a photo) and get a
/// starting avatar with matching skin tone, hair and beard.
public struct AvatarSelfieView: View {
    @ObservedObject var viewModel: AvatarSelfieViewModel

    public init(viewModel: AvatarSelfieViewModel) {
        self.viewModel = viewModel
    }

    @State private var photoItem: PhotosPickerItem?

    private var isAnalyzing: Bool { viewModel.state == .analyzing }

    public var body: some View {
        VStack(spacing: 24) {
            Spacer()
            header
            Spacer()
            if case .failed(let message) = viewModel.state {
                Text(message)
                    .font(.footnote)
                    .foregroundStyle(.red)
                    .multilineTextAlignment(.center)
            }
            actions
        }
        .padding(24)
        .overlay {
            if isAnalyzing {
                analyzingOverlay
            }
        }
        .fullScreenCover(isPresented: $viewModel.isCameraPresented) {
            CameraPicker { image in
                viewModel.isCameraPresented = false
                if let image { viewModel.analyze(image) }
            }
            .ignoresSafeArea()
        }
        .onChange(of: photoItem) { _, item in
            guard let item else { return }
            Task {
                if let data = try? await item.loadTransferable(type: Data.self) {
                    viewModel.analyzePhoto(data: data)
                }
                photoItem = nil
            }
        }
    }

    // MARK: - Views

    private var header: some View {
        VStack(spacing: 16) {
            Image(systemName: "person.crop.circle.badge.plus")
                .font(.system(size: 72, weight: .light))
                .foregroundStyle(Color.accentColor)
            Text("Make your avatar")
                .font(.title.bold())
            Text("Take a selfie and we'll match your skin tone, hair and beard. You can change everything after. The photo isn't saved.")
                .font(.callout)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
        }
    }

    private var actions: some View {
        VStack(spacing: 12) {
            if viewModel.isCameraAvailable {
                Button {
                    viewModel.isCameraPresented = true
                } label: {
                    Label("Take a selfie", systemImage: "camera.fill")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
            }
            PhotosPicker(selection: $photoItem, matching: .images) {
                Label("Choose a photo", systemImage: "photo")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.bordered)
            .controlSize(.large)
            Button("Skip") {
                viewModel.skip()
            }
            .padding(.top, 4)
        }
        .disabled(isAnalyzing)
    }

    private var analyzingOverlay: some View {
        VStack(spacing: 12) {
            ProgressView()
                .controlSize(.large)
            Text("Creating your avatar…")
                .font(.headline)
        }
        .padding(28)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 20))
    }
}

/// Front camera capture. Calls back with `nil` on cancel.
struct CameraPicker: UIViewControllerRepresentable {
    let onFinish: (UIImage?) -> Void

    func makeUIViewController(context: Context) -> UIImagePickerController {
        let picker = UIImagePickerController()
        picker.sourceType = .camera
        picker.cameraDevice = .front
        picker.delegate = context.coordinator
        return picker
    }

    func updateUIViewController(_ picker: UIImagePickerController, context: Context) {}

    func makeCoordinator() -> Coordinator {
        Coordinator(onFinish: onFinish)
    }

    final class Coordinator: NSObject, UIImagePickerControllerDelegate, UINavigationControllerDelegate {
        let onFinish: (UIImage?) -> Void

        init(onFinish: @escaping (UIImage?) -> Void) {
            self.onFinish = onFinish
        }

        func imagePickerController(_ picker: UIImagePickerController, didFinishPickingMediaWithInfo info: [UIImagePickerController.InfoKey: Any]) {
            onFinish(info[.originalImage] as? UIImage)
        }

        func imagePickerControllerDidCancel(_ picker: UIImagePickerController) {
            onFinish(nil)
        }
    }
}
