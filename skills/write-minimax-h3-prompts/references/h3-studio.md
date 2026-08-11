# Using the Skill with MiniMax H3 Studio

This repository's H3 Studio compiles user aliases and uploads into H3 labels. Do not paste a full raw six-section Ref2VA prompt into the `影片敘述` field, because the compiler adds its own outer sections.

## Output Profiles

When the user says the result is for H3 Studio, output a paste-ready English narrative for the `影片敘述` field. Use the user's aliases exactly as named in the UI. H3 Studio will rewrite those aliases to its assigned tags.

When the user asks for a raw ComfyUI/API prompt or a complete official prompt, output the full official base or Ref2VA schema instead.

## H3 Studio Mode Mapping

- `文生影片`: T2VA.
- `首尾圖片`: first image only -> I2VA; both images -> FL2VA; last image only -> L2VA.
- `多模態參考`: Ref2VA.
- `續接影片`: Ref2VA video continuation when the original video is referenced; I2VA-like continuation when only the extracted final frame is used.
- `角色替換`: Ref2VA video editing plus reference generation/attribute transfer.
- `圖騰循環`: FL2VA with the same prepared image at both endpoints and a single closed motion path.
- `彈窗面板`: Ref2VA reference generation with a locked background and one or more foreground panel assets.

## Named Asset Mapping

H3 Studio currently assigns:

- Character, object, and panel aliases to `<Subject N>` with one or more appearance `<Picture N>` or motion `<Video N>` sources.
- Background, style, motion, and effect aliases to reference picture/video roles.
- Uploaded standalone voice samples to `<Audio N>`.
- Enabled source-video soundtracks to independently numbered `<Audio N>` labels.
- Storyboard pictures to `<Picture N>` composition anchors.

Write aliases naturally and consistently in the paste-ready narrative. Do not manually guess tag numbers in H3 Studio input.

## Slot-Game Constraints

### Symbol Loop

Use one continuous shot. Lock canvas, camera, crop, center anchor, symbol scale, silhouette, and margins. Animate one readable action cycle and progressively return pose, material, lighting, particles, and effects to the exact opening state. Avoid a second pause at the seam.

### Popup Panel

Keep the background fixed for the full timeline. Allow only named foreground panels, score text, buttons, decorations, dimming layer, particles, and effects to animate. State the panel's enter, hold/performance, and exit intervals. End with only the unchanged background.

### Transition or Entrance

Describe the subject's off-screen or small-scale starting state, its readable travel/scale path, impact or overshoot, secondary motion, and settled final state. Separate camera shake from object impact and keep it slight unless a strong hit is required.

### Character Performance

Describe anticipation, weight shift, primary action, contact/reaction, follow-through, facial change, and settled pose. Keep named character identity and costume fixed while motion references control only timing and body mechanics.

## Precision Warning

H3 generation is probabilistic. Treat pixel-locked backgrounds, exact UI text, perfect loops, alpha, and frame-exact keyframes as constraints to verify, not guaranteed outcomes. For production slot assets, review the seam and stabilize/matte/composite in post when exact delivery is required.
