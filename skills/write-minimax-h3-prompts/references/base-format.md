# MiniMax H3 Base Prompt Format

Source of truth: MiniMaxAI, `MiniMax-H3/docs/VIDEO_PROMPT_WRITING_GUIDE_base_en.md`, revision `b8b09e34f8d2b9d1b7a51982ccb26ae2b8b9ef08`.

Official source: https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/docs/VIDEO_PROMPT_WRITING_GUIDE_base_en.md

This reference summarizes the official guide. When this summary and the linked source differ, follow the official source.

## Complete Schemas

T2VA has no alignment instruction:

```text
integrated_multimodal_description: [Shot 1] ...

overall_soundscape: ...

non_diegetic_music: ...
```

I2VA begins with this exact first-frame instruction and one blank line:

```text
For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.

integrated_multimodal_description: [Shot 1] ...

overall_soundscape: ...

non_diegetic_music: ...
```

FL2VA begins with this alignment instruction. Replace `N` with the actual final-shot index and `S.SS` with the effective duration formatted to exactly two decimals:

```text
How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot N) aligns with the S.SS-second mark of the target video.

integrated_multimodal_description: [Shot 1] ...

overall_soundscape: ...

non_diegetic_music: ...
```

L2VA begins with this final-frame instruction:

```text
How the reference pictures align with the target video — <Picture 1> (from [Shot N]) aligns with the S.SS-second mark of the target video.

integrated_multimodal_description: [Shot 1] ...

overall_soundscape: ...

non_diegetic_music: ...
```

## Keyframe Paths

- I2VA: first-frame anchor -> action onset -> continuous development -> result or reaction.
- FL2VA: first-frame state -> observable intermediate changes -> progressively narrowing differences -> exact last-frame state. Prefer one continuous shot unless cuts are explicitly required.
- L2VA: plausible preceding state -> explicit action/transition -> gradual convergence -> exact last-frame landing.

Describe the path between frames rather than repeating static descriptions of the images.

## Timeline and Cuts

- `[Shot 1]` has no timestamp.
- Later shots begin with an increasing time inside the duration, for example `[Shot 2] At 00:03.500, the camera cuts to...`.
- A cut should reveal a different subject, space, state, viewpoint, or time. Prefer camera motion for a small distance or angle change.
- Use ordinary cut wording unless the user asks for a dissolve, fade, or wipe.

## Camera Vocabulary

Motion types: `Zoom In`, `Zoom Out`, `Push In`, `Pull Out`, `Pan Left`, `Pan Right`, `Truck Left`, `Truck Right`, `Tilt Up`, `Tilt Down`, `Pedestal Up`, `Pedestal Down`, `Arc Shot`, `Tracking Shot`, `Static Shot`, `Shake Slightly`, `Shake Strongly`, `POV`, `Roll Clockwise`, and `Roll Counterclockwise`.

Optional amplitude: `with small amplitude` or `with large amplitude`.

Optional speed: `at slow speed` or `at fast speed`.

Write camera motion naturally inside the shot, for example: `The camera pushes in with small amplitude at slow speed toward the panel.`

## Dialogue and Vocal Sources

- Assign stable `(S1)`, `(S2)`, and later IDs only to subjects that vocalize.
- At first appearance, define enough visual and vocal traits to keep the speaker stable.
- Format speech as `<d>[Language] exact user text</d>`.
- Preserve user dialogue and punctuation verbatim. Do not translate or rewrite it.
- For voiceover, use `says in an off-screen voiceover`, then state that the corresponding on-screen character's lips remain completely closed.
- If a line crosses a cut, use `<scenetrans>` at both connection points and state that the audio continues across the cut.
- Use `<cutoff>` when speech is truncated by the video ending.

## Visible Text

Place visible banners, signs, labels, subtitles, scores, and UI text in English double quotation marks. Preserve the exact original language and punctuation.

## Sound Fields

`overall_soundscape` is one continuous paragraph of 1-4 English sentences covering ambience, physical action sounds, and non-verbal human sounds. Do not repeat dialogue, singing, or diegetic music. Use `N/A` only for explicitly requested complete silence.

`non_diegetic_music` is 1-3 English sentences covering audience-only score. Describe instrumentation, tempo, rhythm, and dynamics rather than abstract emotional purpose. Put music audible to characters in the main description. Use `N/A` when no audience-only score is required.
