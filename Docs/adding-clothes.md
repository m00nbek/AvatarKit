# Adding clothes

Each piece of clothing is a **garment**, made on its own from the designer's pictures with
Tripo, then fitted onto the body by the asset build. The older way (generating the whole
character wearing the clothes, then cutting them out) is still in the code for the items it
made, but don't add new items that way. See the README for setup.

## What the designer sends, per garment

- **Ghost-mannequin pictures:** the garment shaped as if an invisible person wears it,
  with the neck and sleeve openings visible. Not laid flat, not on a hanger.
- **Front and back as separate images** (a side view is a bonus). The same garment in every
  view: colors, print placement and size.
- **A plain white background**, the whole garment in frame, nothing cropped, no model or props.
- **Soft, even lighting**, without strong shadows or shine.
- **2048 px or larger, square.**
- **The print or logo as its own flat file**, with the text spelled right. Small text often
  comes out garbled in 3D.
- **A separate set for men and women** only if the two versions differ.

Pictures that show several views in one image work too; crop them into one file per view.

## Steps

1. **Put the pictures in `.assets-source/Designer/`**, for example `tee_cau_front.png` and
   `tee_cau_back.png`.

2. **Add a garment to the body in `Tools/tripo/assets.json`** (`bodies[].garments`):

   ```json
   {
     "id": "garment_tee_cau",
     "describe": "T-shirt",
     "pictures": { "front": "Designer/tee_cau_front.png", "back": "Designer/tee_cau_back.png" },
     "item": { "id": "top_tee_cau", "name": "CAU tee", "slot": "top", "fit": "skinned", "segment": "shirt" }
   }
   ```

   - `describe` goes into the prompt ("Dress the character in the T-shirt…").
   - `item.id` must be unique across genders (add `_male` for men's).
   - `slot` is `top`, `bottom`, `shoes` or `hair`.
   - Add the same garment to the male body for a men's version, with its own IDs.

3. **Generate it:**

   ```sh
   cd Tools/tripo && node generate.mjs garment garment_tee_cau
   ```

   - By default, Tripo first redraws the designer's front and back as ghost-mannequin
     pictures on our body (25 credits each). They land in
     `.assets-source/Tripo/gen/<id>/front.png` and `back.png`; check them.
   - Then it makes the 3D garment from the two (60 credits).
   - If the designer's pictures are already clean front and back product shots, add
     `"redraw": false` to skip the redraw. This sends them straight to the 3D step and saves
     50 credits. It hasn't been tried yet.

4. **Build:** `cd Tools && npm run build-assets`. The item shows up in the app on every body type.

5. **Check it in the demo app**, on all four body types and a few moves (wave, celebrate,
   walk), before making more of the same kind.

## How a garment is fitted

`Tools/fit-garments.py` puts the 3D garment on the body:

1. **Facing:** it tries the four turns and keeps the one whose front view looks most like the
   front picture.
2. **Height:** a top rests on the shoulders; a bottom hangs at the waist.
3. **Size:** the smallest size whose girth goes around the body at every height, with 8% ease.
   Girth, not shape: cloth bends around the body but doesn't stretch.
4. **Openings:** Tripo closes the neck, cuffs and hem and may add an inner layer. Only faces
   that point away from the body are kept, which drops both.
5. **Wrap:** wherever the body is inside it (the bust, the shoulder blades), the garment is
   pushed out a little at a time and smoothed, like cloth over it.
6. **Finish:** it's attached to the skeleton, gets the body-type shapes, and the body
   patches under it are marked to hide.

The settings (`EASE_GAP`, `EASE_GIRTH`, `FACING_BODY`) are at the top of the file.

## Known issues

- The **heavy** body type tears garments around the belly.
- **Baggy bottoms can cover a top's hem**, because each item is fitted on its own.
- **Cuffs and hems are slightly frayed.**
- **Bottoms, shoes and hair haven't been tried as garments yet.** Neither has the men's body.

The README's "Where things stand" has the fixes planned for each.
