import SceneKit
import SwiftUI

/// Renders an `AvatarStage`. Drag sideways to turn the avatar.
public struct AvatarView: UIViewRepresentable {
    @ObservedObject var stage: AvatarStage

    public init(stage: AvatarStage) {
        self.stage = stage
    }

    public func makeUIView(context: Context) -> SCNView {
        let view = SCNView(frame: .zero)
        view.scene = stage.controller.scene
        view.pointOfView = stage.controller.cameraNode
        view.backgroundColor = .clear
        view.antialiasingMode = .multisampling4X
        view.isPlaying = true
        view.preferredFramesPerSecond = 60
        view.rendersContinuously = false
        let pan = UIPanGestureRecognizer(target: context.coordinator, action: #selector(Coordinator.didPan(_:)))
        view.addGestureRecognizer(pan)
        return view
    }

    public func updateUIView(_ view: SCNView, context: Context) {
        context.coordinator.turntable = stage.controller.turntable
    }

    public func makeCoordinator() -> Coordinator {
        Coordinator(turntable: stage.controller.turntable)
    }

    @MainActor
    public final class Coordinator: NSObject {
        var turntable: SCNNode
        private var startAngle: Float = 0

        init(turntable: SCNNode) {
            self.turntable = turntable
        }

        @objc func didPan(_ gesture: UIPanGestureRecognizer) {
            switch gesture.state {
            case .began:
                startAngle = turntable.eulerAngles.y
            case .changed:
                let dx = Float(gesture.translation(in: gesture.view).x)
                turntable.eulerAngles.y = startAngle + dx * 0.012
            case .ended, .cancelled:
                let velocity = Float(gesture.velocity(in: gesture.view).x)
                SCNTransaction.begin()
                SCNTransaction.animationDuration = 0.5
                SCNTransaction.animationTimingFunction = CAMediaTimingFunction(name: .easeOut)
                turntable.eulerAngles.y += velocity * 0.0015
                SCNTransaction.commit()
            default:
                break
            }
        }
    }
}
