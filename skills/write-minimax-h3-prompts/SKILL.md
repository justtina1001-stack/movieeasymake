---
name: write-minimax-h3-prompts
description: Write, rewrite, audit, and optimize production-ready prompts for MiniMax H3 video generation using MiniMax's official T2VA, I2VA, FL2VA, L2VA, and Ref2VA formats. Use when a user asks for MiniMax H3 prompts, H3 Studio prompt help, text-to-video, first/last-frame animation, multimodal character or scene references, video continuation/editing, dialogue and synchronized sound, slot-symbol loops, popup panels, transitions, or character performance planning.
---

# Write MiniMax H3 Prompts

Turn a user's creative intent and reference assets into an H3 prompt that follows MiniMax's official structure. Support both raw H3 prompts and paste-ready text for this repository's H3 Studio.

## Required References

Read `references/base-format.md` before drafting any prompt.

Also read:

- `references/ref2va-format.md` for multimodal reference, video editing, continuation, character replacement, voice reference, or any `<Subject N>`, `<Picture N>`, `<Video N>`, or `<Audio N>` usage.
- `references/h3-studio.md` when the user will paste the result into MiniMax H3 Studio or asks about its named assets, storyboards, symbol-loop, popup-panel, continuation, or character-replacement modes.

## Workflow

1. Extract the generation mode, intended duration, aspect ratio, reference assets, names/aliases, spoken lines, visible text, camera behavior, and required ending state.
2. Infer harmless missing details. Ask only when a missing choice changes the mode, asset mapping, dialogue, or required final state materially.
3. Select the correct output profile:
   - **Raw H3**: emit the complete official prompt structure.
   - **H3 Studio**: emit only the paste-ready narrative requested by `references/h3-studio.md`; let the Studio compiler assign asset labels and wrappers.
4. Build the timeline from visible and audible events. Describe concrete pose changes, object interactions, reactions, camera movement, sound cues, and the final settled state.
5. Validate the result against the checklist below.
6. Reply in the user's language, but keep the generated H3 structural fields and descriptive body in English. Preserve dialogue, lyrics, and visible text in their original language.

## Mode Selection

- Use **T2VA** when no image is a frame anchor.
- Use **I2VA** when one image is the exact first frame.
- Use **FL2VA** when images anchor both the exact first and final frames.
- Use **L2VA** when one image is the exact final frame.
- Use **Ref2VA** when images, videos, or audio define reusable subjects, scenes, styles, actions, edits, continuations, or audio relationships.

Do not treat a character appearance image as a keyframe unless the user intends it to be an exact frame. In Ref2VA, define reusable visible content as `<Subject N>`; use standalone `<Picture N>` only for a concrete frame or storyboard/composition anchor.

## Writing Rules

- Follow the official section names and section order exactly.
- Make every prompt detail visible or audible. Prefer an observable action chain over abstract intent.
- Keep identity, clothing, color, props, spatial layout, lighting, and object state consistent unless a change is explicitly described.
- Do not timestamp `[Shot 1]`. Start later shots with strictly increasing cut times inside the actual duration: `[Shot 2] At 00:03.500, ...`.
- Express camera motion as a natural sentence. Specify type, amplitude, and speed only where meaningful.
- Keep each physical speaker's `(S1)`, `(S2)`, and later IDs stable across all shots.
- Put only the language tag and exact spoken content inside `<d>...</d>`. Never translate, paraphrase, or silently correct user-provided dialogue.
- Put visible on-screen text in English double quotation marks and preserve it verbatim.
- Keep dialogue and singing in the main description. Do not repeat them in `overall_soundscape`.
- Use `N/A` for `non_diegetic_music` when there is no audience-only score. Use `N/A` for `overall_soundscape` only if the user explicitly requests complete silence.
- For locked-camera game assets, explicitly prohibit camera motion, background movement, crop changes, scale drift, parallax, flicker, and lighting drift while allowing only the named foreground animation.
- Never promise exact physics, perfect identity, pixel-perfect locking, exact alpha, or seamless looping as guaranteed model behavior. Phrase them as explicit generation constraints and recommend post-production validation when precision matters.

## Output Contract

Unless the user requests a different format, provide:

1. A one-line Traditional Chinese summary of the selected mode and assumptions.
2. `素材對應` only when references exist, listing each user alias and its intended role.
3. `可直接使用的提示詞` in one plain text code block, without commentary inside the block.
4. At most three concise `生成提醒` items for genuine limitations or settings that materially affect the result.

Do not expose a second alternative prompt unless the user asks for variants.

## Final Validation

Confirm all of the following before responding:

- The schema matches the selected mode.
- Image alignment times use the actual effective duration with exactly two decimals.
- Shot numbers and cut times are sequential and valid.
- Every reference label has one stable meaning and is used consistently.
- Every speaking subject has one stable speaker ID.
- Dialogue and visible text are preserved exactly.
- First-frame, last-frame, continuation, replacement, background-lock, and loop constraints do not contradict each other.
- The final visible state is stated explicitly.
- Ambient sound, physical sound, dialogue, diegetic music, and non-diegetic music are placed in the correct sections.
