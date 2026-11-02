import Foundation

public enum AvatarError: LocalizedError, Equatable {
    case missingCatalog
    case missingAsset(String)
    case missingBody(AvatarGender)
    case missingItem(String)
    case missingClip(String)
    case missingSkeleton(String)
    case unsupportedAccessor
    case loadingFailed(String)

    public var errorDescription: String? {
        switch self {
        case .missingCatalog: "Avatar catalog not found."
        case .missingAsset(let file): "Avatar asset \(file) not found."
        case .missingBody(let gender): "No \(gender.rawValue) body."
        case .missingItem(let id): "Avatar item \(id) not found."
        case .missingClip(let name): "Animation \(name) not found."
        case .missingSkeleton(let file): "\(file) has no skinned mesh."
        case .unsupportedAccessor: "Unsupported glTF data layout."
        case .loadingFailed(let reason): "Avatar failed to load: \(reason)"
        }
    }
}
