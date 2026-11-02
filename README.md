# AvatarKit

A Swift package for 3D dress-up avatars on iOS. Pick a body, a body type, a face and
clothes, and the avatar idles, waves, dances and so on. It renders with SceneKit and
loads glTF models through [GLTFKit2](https://github.com/warrenm/GLTFKit2).

The 3D models are generated with [Tripo](https://www.tripo3d.ai) and turned into the
app's assets by the tools in `Tools/`. This README covers both halves: using the
package, and making new assets.

**What's in it today**

- Two bodies, a woman and a man, each with four body types: very skinny, skinny, normal
  and heavy. The woman also has two extra faces.
- 17 clothing items (tops, bottoms, shoes, hair) and 15 moves.
- A demo app with the full editor (`Demo/`).

## Quick start

Requirements: Xcode 16 or later, iOS 17, and [XcodeGen](https://github.com/yonaskolb/XcodeGen)
for the demo app (`brew install xcodegen`).

```sh
# The demo app (the Xcode project is generated, not checked in)
cd Demo && xcodegen generate && open AvatarDemo.xcodeproj

# The package's tests
xcodebuild test -scheme AvatarKit -destination 'platform=iOS Simulator,name=iPhone 17 Pro'
```

The built assets are checked in (`Sources/AvatarKit/Resources/Avatar`), so the package
and the demo work without running the asset tools.

## Using the package

```swift
import AvatarKit
import SwiftUI

let catalog = try AvatarCatalog.bundled()

// The whole editor: 3D preview, tabs for body, face, hair, tops and so on.
let editor = AvatarEditorViewModel(config: nil, catalog: catalog) { config in
    save(config.jsonString)   // AvatarConfig is Codable; AvatarConfig(jsonString:) reads it back
}
AvatarEditorView(viewModel: editor)

// Or just the avatar, driven by your own UI.
let stage = AvatarStage(catalog: catalog)
stage.show(catalog.defaultConfig(for: .female))
stage.play(.wave)
AvatarView(stage: stage)
```

- **`AvatarConfig`** is what a user picked: gender, body type, skin tone, face, and an
  item (with an optional color) per slot. Store it with `jsonString`.
- **`AvatarCatalog`** is everything that exists. It's read from `Resources/Avatar/catalog.json`,
  which the asset build writes.
- **`AvatarMove`** lists the animations.
- **`AvatarSelfieView`** is optional. It suggests a starting avatar from a photo, using the
  Claude API with a key the user enters (see the demo).

## How an avatar is put together

An avatar is built in layers, each one fitting the one under it:

1. **Skeleton.** The Quaternius *Universal Base Characters* skeleton. Every move, from the
   Quaternius *Universal Animation Library* 1 and 2, plays on it.
2. **Body.** One base body per gender, generated with Tripo: bald, barefoot, A-pose. The
   build attaches it to the skeleton (`Tools/rig-generated-body.py`).
3. **Body types.** Tripo generates the base again thinner or heavier. The build turns each
   one into a shape of the base mesh (`Tools/add-body-shapes.py`), and every item gets the
   same shapes, so **one item fits every body type**. Nothing is generated per body type.
4. **Faces.** A face is a new Tripo texture on the same body mesh.
5. **Clothes.** Each item is attached to the same skeleton and carries the body-type
   shapes. The body is split into patches, named by which items cover them; the app hides
   the patches under what's worn, so skin never pokes through.

## Repository layout

```
Sources/AvatarKit/
  Models/       catalog, config, moves, body types
  Rendering/    SceneKit: loading models, the rig, animation
  UI/           AvatarView, the editor, the selfie screen
  Resources/Avatar/   built assets: catalog.json, Bodies/, Items/, Moves/ (written by the build; don't edit)
Tests/          package tests
Demo/           demo app (XcodeGen project.yml)
Tools/
  tripo/assets.json     what to generate with Tripo: prompts, settings, every body, face and garment
  tripo/generate.mjs    runs Tripo and saves what it makes in .assets-source/Tripo/gen
  build-assets.mjs      builds Resources/Avatar from the generated models
  rig-generated-body.py, add-body-shapes.py, fit-garments.py, fit-outfits.py, ...   the build's steps
  fetch-assets.sh       downloads the free Quaternius packs
Docs/adding-clothes.md  step by step for new clothes
```

## Making assets with Tripo

### Setup

1. **Node 20+**, then `cd Tools && npm install`.
2. **Python 3.9+** with numpy and Pillow: `pip3 install numpy pillow`.
3. **A Tripo API key** (global region) in `Tools/tripo/.env`:
   ```
   TRIPO_API_KEY=tsk_...
   ```
   The file is git-ignored. Check the balance with `node generate.mjs balance`.
4. **Source assets in `.assets-source/`** at the repo root. It's git-ignored, about 1 GB:

   | Folder | What | Where from |
   |---|---|---|
   | `Universal Base Characters`, `Universal Animation Library`, `Universal Animation Library 2` | the skeleton and the moves | `Tools/fetch-assets.sh` (free, CC0) |
   | `Universal Animation Library Source` | 8 extra moves (walk, celebrate, sit…) | bought from Quaternius; the build skips those moves without it |
   | `Tripo/gen` | every model Tripo has made so far, plus `state.json` (its task IDs) | **ask the project owner for a copy**; regenerating costs credits and won't match the current look |
   | `Designer` | the designer's garment pictures | the project owner |

### Commands

All from `Tools/tripo`. Each step saves its Tripo task ID first, so a rerun picks up the
same task instead of paying again; add `--force` to redo a step.

| Command | What it does |
|---|---|
| `node generate.mjs make <id>` | every step an asset needs: a body, body type, face or garment |
| `node generate.mjs garment <id>...` | garments: front and back pictures, then the 3D garment |
| `node generate.mjs image <id>` / `multiview <id>` / `model <id>` | single steps, to check the image before paying for 3D |
| `node generate.mjs status` | what's done, with credits spent per step |
| `node generate.mjs balance` | credits left |

IDs come from `assets.json`: a body's base (`base_female`), its body types (`base_female_heavy`:
base ID + `_` + a `bodyShapes` ID), its faces and its garments.

Then build the app's assets (about 10 minutes on an Apple Silicon Mac):

```sh
cd Tools && npm run build-assets
```

It writes `Sources/AvatarKit/Resources/Avatar` and prints a summary like `2 bodies …, 17 items,
15 moves`. Build the app again to see the changes.

### Credits

Roughly, as of the last runs:

| Asset | Steps | Credits |
|---|---|---|
| Base body | image edit 25 + multiview 10 + 3D 30 | 65 |
| Body type | the same | 65 per body |
| Face | image edit 25 + retexture 20 | 45 |
| Garment | 3D from front and back 60, plus 50 if Tripo redraws the pictures first | 60–110 |

### Adding clothes

See [Docs/adding-clothes.md](Docs/adding-clothes.md). In short:

1. Put the designer's front and back pictures in `.assets-source/Designer/`.
2. Add a garment to the body in `assets.json` (`bodies[].garments`).
3. Run `node generate.mjs garment <id>`, then `npm run build-assets`.

Each garment is made **on its own** from the designer's pictures, then fitted onto the body
by `Tools/fit-garments.py`:
- turned to face forward;
- rested on the shoulders, or hung at the waist for bottoms;
- sized so it has enough cloth to go around the body;
- wrapped over it.

### Adding a body type, face or move

- **Body type:** `bodyShapes` in `assets.json` applies to every body. Generate it with
  `node generate.mjs make base_female_<shape>` (and the male one). The app's
  `AvatarBodyType` has fixed cases, so a new type also needs a Swift case and a tile in the editor.
- **Face:** add it to the body's `faces` in `assets.json`, then `make <face id>`.
- **Move:** add a case to `AvatarMove`, and its clip to `buildMoves()` in
  `Tools/build-assets.mjs`. The clip names are the Quaternius ones (`Walk_Loop`, `Celebration`…).

## Where things stand

The clothes are mid-way through a change of method. The way they were made at first:
generate the whole character wearing them, then cut the clothes out of that model.
It gives ragged edges, holes and smudged prints. Making each garment on its own is much
better, and it's the way to go for every new item. Today's catalog still mixes the two:

| Items | Made by |
|---|---|
| `top_tee_cau_tripo` ("CAU tee · Tripo") | a garment on its own: the new way |
| `top_crop_black`, `top_tee_white`, `bottom_jeans_wide`, `bottom_jeans_skinny`, shoes, hair | cut out of Tripo characters (`outfits` in `assets.json`) |
| the CAU hoodie and tee, baggy jeans and pleated skirt, for both genders | cut out of characters made with another 3D generator (`"generator": "hunyuan"`); their models are in `.assets-source`, so the build still works |

### Next steps

- **Remake the clothes as garments**, then drop the cut-out items and `outfits`. Bottoms,
  shoes, hair and the men's body haven't been tried as garments yet. The fitter handles
  bottoms (it hangs them at the waist), but that part is untested.
- **Heavy body type:** garments tear around the belly. Items copy how the nearest body
  point moves, so loose cloth gets pulled two ways. Fix by smoothing that movement over each
  garment and keeping it outside the body on every body type, not just normal.
- **Tops over bottoms:** each item is fitted to the body alone, so baggy jeans can cover a
  tee's hem. Tops need to sit over any bottom's waistband.
- **Frayed edges** at garment cuffs and hems.
- **Base bodies wear gray basics** (a tank top and shorts painted into the skin), which
  show under short skirts. Plan: repaint them as skin in the build, but keep the original
  texture for fitting.
- **`"redraw": false`** (send the designer's pictures straight to the 3D step and skip
  Tripo's 50-credit redraw) is written but untested. It should work when the designer's
  pictures are clean front and back product shots, like the tee's.
- Pinch to zoom in the demo; face presets (eyes, brows, nose, mouth); arms that touch the
  hips on the heavy body during some moves.

## Asset rules

Anything new has to follow these, or it won't fit the rest:

- One base per gender. Body types and faces are made **from that base**: its image for
  body types, its model for faces. So they line up with it.
- Bodies stand in an A-pose: arms about 45° down, clear of the body; feet apart.
- Generated models face `+x` (`"forward"` in `assets.json`); the build turns them to face `+z`.
- Item IDs are unique across genders (add `_male` to men's).
- Slots: `top`, `bottom`, `shoes`, `hair` (the editor knows more, but only these are built).
