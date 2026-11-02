import SwiftUI

/// Full avatar creator: 3D preview on top, category tabs and options below.
public struct AvatarEditorView: View {
    @ObservedObject var viewModel: AvatarEditorViewModel

    public init(viewModel: AvatarEditorViewModel) {
        self.viewModel = viewModel
    }

    private let columns = [GridItem(.adaptive(minimum: 96), spacing: 12)]

    public var body: some View {
        VStack(spacing: 0) {
            preview
            tabBar
            Divider()
            ScrollView {
                panel
                    .padding(16)
            }
        }
        .background(Color(.systemGroupedBackground))
    }

    // MARK: - Preview

    private var preview: some View {
        ZStack(alignment: .bottom) {
            LinearGradient(
                colors: [Color(red: 0.86, green: 0.91, blue: 1), Color(red: 0.97, green: 0.95, blue: 1)],
                startPoint: .top,
                endPoint: .bottom
            )
            AvatarView(stage: viewModel.stage)
            if viewModel.stage.isLoading {
                ProgressView()
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topTrailing)
                    .padding(16)
            }
            if let error = viewModel.stage.error {
                Text(error.localizedDescription)
                    .font(.footnote)
                    .padding(8)
                    .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 8))
                    .frame(maxHeight: .infinity, alignment: .top)
                    .padding(16)
            }
            movesBar
        }
        .frame(maxWidth: .infinity)
        .frame(height: 400)
        .clipped()
    }

    private var movesBar: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                ForEach(viewModel.catalog.availableMoves) { move in
                    Button {
                        viewModel.play(move)
                    } label: {
                        Label(move.title, systemImage: move.symbol)
                            .font(.subheadline.weight(.semibold))
                            .padding(.horizontal, 12)
                            .padding(.vertical, 8)
                            .background(.regularMaterial, in: Capsule())
                    }
                    .buttonStyle(.plain)
                }
                Button {
                    viewModel.randomize()
                } label: {
                    Image(systemName: "dice")
                        .font(.subheadline.weight(.semibold))
                        .padding(8)
                        .background(.regularMaterial, in: Circle())
                }
                .buttonStyle(.plain)
            }
            .padding(.horizontal, 16)
        }
        .padding(.bottom, 12)
    }

    // MARK: - Tabs

    private var tabBar: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 6) {
                ForEach(viewModel.tabs) { tab in
                    tabChip(tab)
                }
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 10)
        }
        .background(Color(.systemBackground))
    }

    @ViewBuilder
    private var panel: some View {
        switch viewModel.selectedTab {
        case .body:
            bodyPanel
        case .face:
            facePanel
        case .skin:
            colorRow(viewModel.catalog.skinTones, selected: viewModel.config.skinTone) {
                viewModel.setSkinTone($0)
            }
        case .slot(let slot):
            slotPanel(slot)
        }
    }

    private var bodyPanel: some View {
        VStack(alignment: .leading, spacing: 20) {
            if viewModel.genders.count > 1 {
                Picker("Gender", selection: Binding(get: { viewModel.config.gender }, set: { viewModel.setGender($0) })) {
                    ForEach(viewModel.genders) { gender in
                        Text(gender.title).tag(gender)
                    }
                }
                .pickerStyle(.segmented)
            }
            if viewModel.bodyTypes.count > 1 {
                HStack(spacing: 8) {
                    ForEach(viewModel.bodyTypes) { bodyType in
                        bodyTypeTile(bodyType)
                    }
                }
            }
        }
    }

    private var facePanel: some View {
        LazyVGrid(columns: columns, spacing: 12) {
            faceTile(id: nil, name: "Original")
            ForEach(viewModel.faces) { face in
                faceTile(id: face.id, name: face.name)
            }
        }
    }

    // MARK: - View functions

    private func faceTile(id: String?, name: String) -> some View {
        let isSelected = viewModel.config.face == id
        return Button {
            viewModel.setFace(id)
        } label: {
            VStack(spacing: 6) {
                Image(systemName: "face.smiling")
                    .font(.title2)
                Text(name)
                    .font(.caption.weight(.medium))
            }
            .frame(maxWidth: .infinity, minHeight: 84)
            .background(Color(.systemBackground), in: RoundedRectangle(cornerRadius: 14))
            .overlay {
                RoundedRectangle(cornerRadius: 14)
                    .strokeBorder(isSelected ? Color.accentColor : Color.clear, lineWidth: 2.5)
            }
        }
        .buttonStyle(.plain)
    }

    private func tabChip(_ tab: AvatarEditorTab) -> some View {
        let isSelected = viewModel.selectedTab == tab
        return Button {
            viewModel.selectedTab = tab
        } label: {
            Text(tab.title)
                .font(.subheadline.weight(.semibold))
                .padding(.horizontal, 14)
                .padding(.vertical, 8)
                .foregroundStyle(isSelected ? Color.white : Color.primary)
                .background(isSelected ? Color.accentColor : Color(.secondarySystemBackground), in: Capsule())
        }
        .buttonStyle(.plain)
    }

    private func slotPanel(_ slot: AvatarSlot) -> some View {
        VStack(alignment: .leading, spacing: 16) {
            if let tintable = viewModel.tintableSelection(in: slot) {
                colorRow(viewModel.colors(for: slot), selected: tintable.color) {
                    viewModel.setColor($0, in: slot)
                }
            }
            LazyVGrid(columns: columns, spacing: 12) {
                ForEach(viewModel.items(in: slot)) { item in
                    itemTile(item)
                }
            }
        }
    }

    private func bodyTypeTile(_ bodyType: AvatarBodyType) -> some View {
        let isSelected = viewModel.config.bodyType == bodyType
        return Button {
            viewModel.setBodyType(bodyType)
        } label: {
            Text(bodyType.title)
                .font(.subheadline.weight(.medium))
                .lineLimit(2)
                .multilineTextAlignment(.center)
                .frame(maxWidth: .infinity, minHeight: 52)
                .padding(.horizontal, 4)
                .background(Color(.systemBackground), in: RoundedRectangle(cornerRadius: 14))
                .overlay {
                    RoundedRectangle(cornerRadius: 14)
                        .strokeBorder(isSelected ? Color.accentColor : Color.clear, lineWidth: 2.5)
                }
        }
        .buttonStyle(.plain)
    }

    private func itemTile(_ item: AvatarItem) -> some View {
        let isSelected = viewModel.isSelected(item)
        return Button {
            viewModel.toggle(item)
        } label: {
            VStack(spacing: 6) {
                Image(systemName: item.slot.symbol)
                    .font(.title2)
                Text(item.name)
                    .font(.caption.weight(.medium))
                    .lineLimit(2)
                    .multilineTextAlignment(.center)
            }
            .frame(maxWidth: .infinity, minHeight: 84)
            .padding(6)
            .background(Color(.systemBackground), in: RoundedRectangle(cornerRadius: 14))
            .overlay {
                RoundedRectangle(cornerRadius: 14)
                    .strokeBorder(isSelected ? Color.accentColor : Color.clear, lineWidth: 2.5)
            }
        }
        .buttonStyle(.plain)
    }

    private func colorRow(_ colors: [AvatarColor], selected: AvatarColor?, onSelect: @escaping (AvatarColor) -> Void) -> some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 10) {
                ForEach(colors, id: \.self) { color in
                    Button {
                        onSelect(color)
                    } label: {
                        Circle()
                            .fill(Color(uiColor: UIColor(color)))
                            .frame(width: 40, height: 40)
                            .overlay {
                                Circle()
                                    .strokeBorder(color == selected ? Color.accentColor : Color.black.opacity(0.1), lineWidth: color == selected ? 3 : 1)
                            }
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(.vertical, 2)
        }
    }
}

extension AvatarSlot {
    var symbol: String {
        switch self {
        case .hair: "scissors"
        case .facialHair: "mustache"
        case .headwear: "crown"
        case .top: "tshirt"
        case .bottom: "figure.walk"
        case .shoes: "shoeprints.fill"
        case .accessory: "sparkles"
        }
    }
}
