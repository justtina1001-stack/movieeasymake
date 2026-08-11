# MiniMax H3 Full-Reference (Ref2VA) Format

Source of truth: MiniMaxAI, `MiniMax-H3/docs/VIDEO_PROMPT_WRITING_GUIDE_ref_en.md`, revision `b8b09e34f8d2b9d1b7a51982ccb26ae2b8b9ef08`.

Official source: https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/docs/VIDEO_PROMPT_WRITING_GUIDE_ref_en.md

This reference summarizes the official guide. When this summary and the linked source differ, follow the official source.

## Language and Detail

Write all six sections in English. Preserve original language only inside dialogue or lyrics `<d>` blocks and for text visibly present in the scene. Make `detailed_description` explicit about composition, appearance, position, environment, lighting, actions, state changes, camera movement, sound, and the exact point at which each reference takes effect.

## Required Six-Section Order

```text
subject_definitions:
...

summary:
...

retention_analysis:
...

detailed_description:
...

overall_soundscape:
...

non_diegetic_music:
...
```

For generation tasks, the official guide normally targets 350-500 English words in `detailed_description`; scale editing descriptions to the source video's complexity. Completeness and timeline fit matter more than mechanically hitting the range.

## Reference Labels

- `<Subject N>`: reusable visible content, including people, animals, objects, scenes, backgrounds, clothing, props, interfaces, effects, styles, actions, expressions, or poses.
- `<Picture N>`: a concrete first frame, keyframe, last frame, edited keyframe, storyboard, or composition anchor.
- `<Video N>`: a whole-video editing source, continuation source, camera/cut/rhythm reference, or temporal structure.
- `<Audio N>`: copied or referenced audio. Video and audio numbering are independent.

If an image only defines a character, scene, costume, or style, cite it inside the corresponding `<Subject N>` definition instead of creating an extra standalone picture definition. A single subject may combine appearance from a picture and motion from a video.

## Summary Task Prefixes

Start the one-paragraph summary with the applicable unique task types joined by ` + `:

- `keyframe completion`
- `reference generation`
- `video editing`
- `video continuation`
- `audio reuse`
- `audio reference`

The presence of a video or audio file alone does not imply editing, continuation, reuse, or reference. Classify it by its actual role. For direct video edits, begin after the prefix with `The target video is an edited version of <Video 1>.`

## Retention Markers

For visible references, use exactly one of:

- `fully_preserved`
- `partially_preserved`
- `attribute_transfer`
- `weak_reference`

For audio, use exactly one of:

- `fully_copy`
- `partially_copy`
- `reference`
- `weak_reference`

Use one line per tracked label. State shot appearances and the concrete preserved/transferred/copy relationship. Do not count newly added target actions or plot events as fidelity losses.

## Detailed Description

- Establish the overall style in one or two English sentences before `[Shot 1]`.
- Use `[Shot 1]` without a timestamp. Use `[Shot N] At MM:SS.mmm, ...` for later cuts.
- Insert labels naturally where their roles first appear and where they take effect.
- Use phrases such as `the shot begins from <Picture 1>`, `the shot's keyframe corresponds to <Picture 2>`, or `the shot ends on <Picture 3>` for concrete frame anchors.
- Cite `<Video N>` where its edit, continuation, structure, or source state applies.
- Cite `<Audio N>` in the shot or audio phase where its copy/reference relationship is active.

When a referenced subject speaks, keep both identifiers: `<Subject 2> (S1)`. `<Subject N>` identifies visual reference content; `(S1)` identifies the actual vocal source. If audio only supplies voice timbre or delivery, do not copy its original words. If dialogue is reused or explicitly reperformed, preserve the original words and language inside `<d>`; use `[unclear]` instead of guessing unintelligible spans.

## Sound Sections

Keep dialogue, lyrics, and shot-synchronized sound in `detailed_description`. Put ambience and physical effects in `overall_soundscape`; put audience-only score in `non_diegetic_music`. When reference audio contributes to either layer, state whether it is copied or referenced in the matching section.
