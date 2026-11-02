import AvatarKit
import SwiftUI

struct EditorScreen: View {
    @StateObject private var viewModel: AvatarEditorViewModel
    @Binding var notice: String?
    @State private var savedJSON: String?
    @State private var isSelfiePresented = false
    private let bakeoff: Bakeoff?

    init(catalog: AvatarCatalog, config: AvatarConfig, bakeoff: Bakeoff?, notice: Binding<String?>) {
        _viewModel = StateObject(wrappedValue: AvatarEditorViewModel(config: config, catalog: catalog) { config in
            DemoStorage.save(config)
        })
        self.bakeoff = bakeoff
        _notice = notice
    }

    var body: some View {
        NavigationStack {
            AvatarEditorView(viewModel: viewModel)
                .navigationTitle("Avatar")
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .topBarLeading) {
                        Button {
                            isSelfiePresented = true
                        } label: {
                            Image(systemName: "camera")
                        }
                    }
                    if let bakeoff {
                        ToolbarItem(placement: .topBarTrailing) {
                            GeneratorMenu(bakeoff: bakeoff, viewModel: viewModel)
                        }
                    }
                    ToolbarItem(placement: .topBarTrailing) {
                        Button("Save") {
                            viewModel.save()
                            savedJSON = viewModel.config.jsonString
                        }
                    }
                }
                .safeAreaInset(edge: .top) {
                    if let notice {
                        Text(notice)
                            .font(.footnote)
                            .padding(10)
                            .frame(maxWidth: .infinity)
                            .background(.yellow.opacity(0.25))
                            .onTapGesture { self.notice = nil }
                    }
                }
                .alert("Saved config", isPresented: Binding(get: { savedJSON != nil }, set: { if !$0 { savedJSON = nil } })) {
                    Button("OK") {}
                } message: {
                    Text(savedJSON ?? "")
                }
                .sheet(isPresented: $isSelfiePresented) {
                    NavigationStack {
                        SelfieScreen(catalog: viewModel.catalog) { result in
                            isSelfiePresented = false
                            guard let result else { return }
                            notice = DemoStorage.notice(for: result)
                            viewModel.replace(with: result.config)
                        }
                        .toolbar { APIKeyButton() }
                    }
                }
        }
        .task {
            await DemoLaunchOptions.run(on: viewModel, bakeoff: bakeoff)
        }
    }
}

/// Dresses the avatar in outfit A as one generator made it.
struct GeneratorMenu: View {
    let bakeoff: Bakeoff
    @ObservedObject var viewModel: AvatarEditorViewModel

    var body: some View {
        Menu {
            Section("Outfit A made by") {
                ForEach(bakeoff.generators) { generator in
                    Button {
                        dress(in: generator)
                    } label: {
                        if generator.isWorn(in: viewModel.config) {
                            Label(generator.name, systemImage: "checkmark")
                        } else {
                            Text(generator.name)
                        }
                    }
                }
            }
        } label: {
            Image(systemName: "cube.transparent")
        }
    }

    private func dress(in generator: BakeoffGenerator) {
        guard let body = viewModel.catalog.bodies.first(where: { $0.id == bakeoff.body }) else { return }
        viewModel.replace(with: generator.dress(viewModel.config, body: body))
    }
}
