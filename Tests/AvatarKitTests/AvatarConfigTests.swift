//
//  AvatarConfigTests.swift
//  AvatarKit
//
//  Created by Claude on 10/27/26.
//

import XCTest
@testable import AvatarKit

/// The config is what the server stores, so its JSON shape has to stay readable and stable.
final class AvatarConfigTests: XCTestCase {

    func test_color_normalizesHex() {
        XCTAssertEqual(AvatarColor(hex: "e0ac69")?.hex, "#E0AC69")
        XCTAssertEqual(AvatarColor(hex: "#E0AC69")?.hex, "#E0AC69")
    }

    func test_color_rejectsInvalidHex() {
        XCTAssertNil(AvatarColor(hex: "#E0AC6"))
        XCTAssertNil(AvatarColor(hex: "#GGGGGG"))
    }

    func test_color_components() throws {
        let color = try XCTUnwrap(AvatarColor(hex: "#FF8000"))
        XCTAssertEqual(color.components.red, 1, accuracy: 0.001)
        XCTAssertEqual(color.components.green, 128.0 / 255, accuracy: 0.001)
        XCTAssertEqual(color.components.blue, 0, accuracy: 0.001)
    }

    func test_config_roundTripsThroughJSON() throws {
        let config = AvatarConfig(
            gender: .female,
            bodyType: .heavy,
            skinTone: try XCTUnwrap(AvatarColor(hex: "#EBB894")),
            items: [
                .hair: AvatarItemSelection(itemID: "hair_long", color: AvatarColor(hex: "#6A4530")),
                .top: AvatarItemSelection(itemID: "top_peasant_female"),
            ]
        )
        let json = try XCTUnwrap(config.jsonString)
        XCTAssertEqual(AvatarConfig(jsonString: json), config)
    }

    /// Slots must encode as object keys (`"hair": {...}`), not as a flat array.
    func test_config_encodesSlotsAsKeys() throws {
        let config = AvatarConfig(
            gender: .male,
            bodyType: .skinny,
            skinTone: try XCTUnwrap(AvatarColor(hex: "#D99A6C")),
            items: [.hair: AvatarItemSelection(itemID: "hair_parted")]
        )
        let json = try XCTUnwrap(config.jsonString)
        XCTAssertTrue(json.contains(#""items":{"hair":{"itemID":"hair_parted"}}"#), json)
        XCTAssertTrue(json.contains(##""skinTone":"#D99A6C""##), json)
    }

    /// Saved avatars from before body types were presets still load, as `normal`.
    func test_config_readsOldBodyTypesAsNormal() throws {
        let json = ##"{"gender":"female","bodyType":"athletic","skinTone":"#EBB894","items":{}}"##
        XCTAssertEqual(try XCTUnwrap(AvatarConfig(jsonString: json)).bodyType, .normal)
    }

    func test_config_rejectsMalformedJSON() {
        XCTAssertNil(AvatarConfig(jsonString: #"{"gender":"robot"}"#))
    }
}
