# Tripo asset plan — first dress-up test

> The first test, kept for its notes. For adding clothes, bodies and body types now, see
> [adding-clothes.md](adding-clothes.md).

Goal: one base body you can dress, to see customization working. Not many
items, enough to switch between a couple of tops, bottoms, shoes, hairstyles
and faces. We perfect and add more after this works.

Every prompt and Tripo setting is in [`Tools/tripo/assets.json`](../Tools/tripo/assets.json).
That file is the source of truth; this page explains it.

## What we generate

| Kind | Items | How it attaches |
|---|---|---|
| Base body | `base_female`: bald, barefoot, plain gray tank top and shorts, A-pose | Rigged to our skeleton |
| Faces | `face_classic` (the base), `face_freckles`, `face_deep` | New texture on the same body mesh |
| Tops | `top_tee_white`, `top_crop_black` | Fitted to the body, follows its bones |
| Bottoms | `bottom_jeans_wide`, `bottom_jeans_skinny` | Fitted to the body, follows its bones |
| Shoes | `shoes_sneakers_white`, `shoes_boots_black` | One shoe on each foot bone |
| Hair | `hair_long_wavy`, `hair_bob`, `hair_ponytail` | On the head bone |

That's 1 body, 2 extra face textures and 9 items.

All of it is female for now. A male base and his items follow the same recipe.

## Rules every asset must follow

These are what make items fit each other. Anything added later follows them too.

1. **One character, one pose.** Every item is made by editing the base body's
   image ("same character, now wearing X"), never generated from scratch. A
   from-scratch pair of jeans comes out in its own size and shape and won't fit.
2. **A-pose.** Arms straight at about 45° down and away from the body with a
   clear gap at the armpits; legs apart with a clear gap; hands open; facing
   the camera. Parts that touch get fused into one surface, which is what tore
   the jeans on `female_v2`.
3. **The base is bare where items go.** Bald (hair is an item), barefoot (shoes
   are items), plain tight clothing (tops and bottoms cover it).
4. **Same style as `female_v2`.** The first image edit uses
   `Tools/tripo/reference/female_v2_front.png` as the style reference, and
   everything after is derived from the base body.
5. **Plain white background, front view, full body.** Generation works from
   images; clutter or a cut-off head or feet breaks it.
6. **Long hair goes behind the shoulders.** Hair rides on the head bone, so hair
   lying on the shoulders would cut through them when the arms move.
7. **Files:** GLB, any triangle count (the pipeline reduces it), PBR textures
   (we keep base color and normal maps, downscaled to 1024).

## How each kind is made

**Base body**
1. Image edit of the style reference into the base description (bald, barefoot, gray basics, A-pose).
2. Image to multiview (front, left, back, right), so the body is consistent all around.
3. Multiview to 3D model.
4. Reduce and rig with `Tools/rig-generated-body.py`, so all our animations play on it.
   Tripo's auto-rig (Unreal bone names) is tried as a comparison.

**Tops, bottoms, shoes, hair**
1. Image edit of the base front image: "same character, now wearing X".
2. Image to multiview, then multiview to 3D: the base body wearing X.
3. Tripo segmentation splits that model into parts; we keep the X part.
4. Fit it to the base body (new script):
   - Line it up with the base.
   - Push it just outside the skin.
   - Attach it to the skeleton: clothes copy the bone weights of the nearest
     skin, shoes go on the foot bones, hair goes on the head bone.
   - Record which body regions it covers, so they're hidden under it.

**Faces**
1. Image edit of the base: "same character, only the face and skin change".
2. Tripo re-texture of the base body mesh, guided by that image. The geometry stays
   identical, so every item still fits.

## Order, with stop points

Credits are only charged for successful tasks. Each step still costs credits,
so we check before moving on:

1. **Base body, then check.** Arms and legs clear of the body, bald, barefoot,
   rig survives all moves (the offline pose renders plus the app). If it fails,
   adjust the prompt and redo before generating anything else.
2. **One item: `bottom_jeans_wide`, then check.** This proves the
   dress → segment → fit steps. If segmentation doesn't give clean jeans, we
   switch to the fallback (generate the jeans alone from an edited image of just
   the jeans) before spending on the rest.
3. **One hair and one pair of shoes, then check.** Proves the head and foot attachment.
4. **The rest of the list**, then the faces.
5. **App wiring:** Face tab, items in the catalog, try everything together.

## What I build when the key arrives

- `Tools/tripo/generate.mjs`
  - Runs the manifest against the Tripo API: image edits, multiview, 3D,
    segmentation, re-texture.
  - Saves task IDs, so a rerun resumes instead of paying twice.
  - Shows the credit balance before starting and asks before each paid stage.
- `Tools/fit-item.py`
  - Fits a segmented part to the base body: aligned, outside the skin, attached
    to the skeleton, covered regions recorded.
  - Clothes, hair and shoes each attach their own way.
- Catalog and app:
  - Face variants as a new tab (they swap the body's texture).
  - The generated items listed for `base_female`.

The key goes in `Tools/tripo/.env` as `TRIPO_API_KEY=...` (gitignored) or the `TRIPO_API_KEY` environment variable, never into the repo.

## Risks we'll learn from the first steps

- **The base body changes between edits.** The image edits might drift (a
  slightly different face or proportions) and items wouldn't line up. We'll
  see it at step 2.
  - *Fix:* tighter "keep everything else" wording, or editing with the
    fidelity-tier image model.
- **Segmentation cuts badly.** It may give a garment with a hole or with skin
  attached.
  - *Fix:* the fallback in step 2 (generate the garment alone).
- **Tight items clip.** Skinny jeans and tees sit close to the skin.
  - *Fix:* push them a bit further out, and hide the covered body regions
    (already supported).
- **Re-texturing changes more than the face.** The gray basics may come out
  different too. Hidden under clothes anyway; only matters with nothing on.

## Adding more later

New items follow the same recipe: add an entry to `items` in `assets.json`
with a slot, how it attaches, the segment label and a prompt ("Now she
wears …"), then run the generator for that ID.

## What we generated (first batch, 2026-10-28)

What changed from the plan above:
- **Outfits instead of single items.** Each generation dresses the base in a whole
  outfit (top, bottom, shoes, hair) and is cut into items afterwards: about 65
  credits for four items instead of about 260. Prompts make sure no item hides
  another (jeans end above the shoes, tops don't cover the waistband).
- **Our own cutting, not Tripo segmentation.** `Tools/fit-outfits.py` lines each
  outfit up with the base and takes whatever differs from the base in color
  (perceptual, CIE Lab) as clothing, split by height bands and color. It's free,
  and it also records which patches of the base each item covers.
- **Faces baked onto the base.** Tripo's re-texture returns the mesh with new UVs,
  so `Tools/bake-texture.py` copies its colors onto the base's own texture layout.
  A face is then just another texture for the same body.

| Asset | Credits |
|---|---|
| `base_female` (image, views, model) | 65 |
| `outfit_a`: black crop tee, wide jeans, white sneakers, ponytail | 65 |
| `outfit_b`: white tee, skinny jeans, black boots, bob | 65 |
| `outfit_hair_long`: long waves (first prompt was refused by the content filter) | 65 |
| `face_freckles`, `face_deep` (image + re-texture each) | 90 |

Rebuild everything with `cd Tools && npm run build-assets`. Generation steps are
in `Tools/tripo/generate.mjs` and resume from `.assets-source/Tripo/gen/state.json`.

Known rough edges: the white tee's neckline shows a few skin specks, items are
not recolorable yet, and long hair rests on the shoulders, so the shoulders can
cut into it in big arm moves.
