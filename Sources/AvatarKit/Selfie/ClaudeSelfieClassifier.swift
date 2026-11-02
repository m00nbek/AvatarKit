import Foundation

/// Asks Claude to pick hairstyle, hair color, facial hair and body from the
/// catalog's options. Calls the Messages API directly, which ships the API key
/// inside the app: fine for a demo, but production should send the photo to
/// your backend and make this call there.
public struct ClaudeSelfieClassifier: AvatarSelfieClassifier {
    private let apiKey: String
    private let model = "claude-opus-5-5"
    private let endpoint = URL(string: "https://api.anthropic.com/v1/messages")!

    public init(apiKey: String) {
        self.apiKey = apiKey
    }

    public func traits(of faceJPEG: Data, choices: AvatarSelfieChoices) async throws -> AvatarSelfieTraits {
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.timeoutInterval = 60
        request.setValue("application/json", forHTTPHeaderField: "content-type")
        request.setValue(apiKey, forHTTPHeaderField: "x-api-key")
        request.setValue("2023-06-01", forHTTPHeaderField: "anthropic-version")
        // On a safety decline, re-run the request on Anthropic's recommended fallback model.
        request.setValue("server-side-fallback-2026-07-01", forHTTPHeaderField: "anthropic-beta")
        request.httpBody = try JSONSerialization.data(withJSONObject: body(faceJPEG: faceJPEG, choices: choices))

        let (data, response) = try await URLSession.shared.data(for: request)
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        let json = try JSONSerialization.jsonObject(with: data) as? [String: Any] ?? [:]
        guard status == 200 else {
            let message = (json["error"] as? [String: Any])?["message"] as? String
            throw AvatarSelfieError.classifierFailed("HTTP \(status): \(message ?? "no details")")
        }
        guard json["stop_reason"] as? String != "refusal" else {
            throw AvatarSelfieError.classifierFailed("The model declined this photo.")
        }
        let blocks = json["content"] as? [[String: Any]] ?? []
        guard let text = blocks.first(where: { $0["type"] as? String == "text" })?["text"] as? String,
              let answer = try? JSONDecoder().decode(Answer.self, from: Data(text.utf8))
        else { throw AvatarSelfieError.classifierFailed("Unexpected response.") }

        return AvatarSelfieTraits(
            gender: AvatarGender(rawValue: answer.body) ?? .male,
            skinTone: AvatarColor(hex: answer.skinTone),
            hairItemID: answer.hairStyle == Self.none ? nil : answer.hairStyle,
            hairColor: AvatarColor(hex: answer.hairColor),
            facialHairItemID: answer.facialHair == Self.none ? nil : answer.facialHair
        )
    }

    private static let none = "none"

    private struct Answer: Decodable {
        let body: String
        let skinTone: String
        let hairStyle: String
        let hairColor: String
        let facialHair: String
    }

    private func body(faceJPEG: Data, choices: AvatarSelfieChoices) -> [String: Any] {
        func describe(_ options: [AvatarSelfieChoices.Option]) -> String {
            options.map { "- \($0.id): \($0.name) (\($0.genders.map(\.rawValue).joined(separator: ", ")))" }
                .joined(separator: "\n")
        }
        let prompt = """
        This selfie was taken to set up a 3D avatar. Pick the options that best match the person.

        body: the avatar body ("male" or "female") that best matches how the person presents.
        skinTone: the closest skin tone from the list (lightest to darkest), allowing for the photo's lighting.
        hairStyle: the closest hairstyle, or "none" if bald. Prefer styles listed for the chosen body. If the hair is \
        covered, guess from what is visible.
        \(describe(choices.hairStyles))
        hairColor: the closest natural hair color from the list.
        facialHair: the beard option if they have a beard or a clearly visible stubble, otherwise "none".
        \(describe(choices.facialHair))
        """
        let schema: [String: Any] = [
            "type": "object",
            "properties": [
                "body": ["type": "string", "enum": AvatarGender.allCases.map(\.rawValue)],
                "skinTone": ["type": "string", "enum": choices.skinTones.map(\.hex)],
                "hairStyle": ["type": "string", "enum": choices.hairStyles.map(\.id) + [Self.none]],
                "hairColor": ["type": "string", "enum": choices.hairColors.map(\.hex)],
                "facialHair": ["type": "string", "enum": choices.facialHair.map(\.id) + [Self.none]],
            ],
            "required": ["body", "skinTone", "hairStyle", "hairColor", "facialHair"],
            "additionalProperties": false,
        ]
        return [
            "model": model,
            "max_tokens": 4096,
            "fallbacks": "default",
            "output_config": [
                "effort": "low",
                "format": ["type": "json_schema", "schema": schema],
            ],
            "messages": [[
                "role": "user",
                "content": [
                    ["type": "image", "source": ["type": "base64", "media_type": "image/jpeg", "data": faceJPEG.base64EncodedString()]],
                    ["type": "text", "text": prompt],
                ],
            ]],
        ]
    }
}
