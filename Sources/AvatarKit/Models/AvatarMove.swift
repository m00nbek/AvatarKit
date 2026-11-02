import Foundation

/// Animations an avatar can play. Which clip backs each move lives in the catalog.
public enum AvatarMove: String, Codable, CaseIterable, Identifiable, Sendable {
    case idle
    case wave
    case yes
    case no
    case dance
    case crossArms
    case phone
    case walk
    case catwalk
    case celebrate
    case talk
    case lookAround
    case sit
    case backflip
    case cry

    public var id: String { rawValue }

    public var title: String {
        switch self {
        case .idle: "Idle"
        case .wave: "Wave"
        case .yes: "Yes"
        case .no: "No"
        case .dance: "Dance"
        case .crossArms: "Arms crossed"
        case .phone: "Phone call"
        case .walk: "Walk"
        case .catwalk: "Catwalk"
        case .celebrate: "Celebrate"
        case .talk: "Talk"
        case .lookAround: "Look around"
        case .sit: "Sit"
        case .backflip: "Backflip"
        case .cry: "Cry"
        }
    }

    public var symbol: String {
        switch self {
        case .idle: "figure.stand"
        case .wave: "hand.wave"
        case .yes: "hand.thumbsup"
        case .no: "hand.thumbsdown"
        case .dance: "figure.dance"
        case .crossArms: "figure.arms.open"
        case .phone: "phone"
        case .walk: "figure.walk"
        case .catwalk: "figure.walk.motion"
        case .celebrate: "party.popper"
        case .talk: "bubble.left"
        case .lookAround: "eyes"
        case .sit: "figure.seated.side"
        case .backflip: "figure.gymnastics"
        case .cry: "drop"
        }
    }
}
