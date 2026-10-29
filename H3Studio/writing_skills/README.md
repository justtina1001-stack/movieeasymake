# H3 Studio writing Skills

## Official generation format

The creative methods below are separate from the mandatory generation-format
contract. Every AI storyboard conversion uses the shared rules in
`h3_official_policy.py`; quick, short-film and script-to-video compilation share
the same shot, dialogue and reference validation. A selected creative method
does not disable that contract.

The complete upstream [h3-prompt-writing Skill](official_h3_prompt/SKILL.md),
[base guide](official_h3_prompt/references/base-en.txt) and
[full-reference guide](official_h3_prompt/references/ref-en.txt) are bundled
unchanged at revision `199b7d0e1fd95e7b3eafa8c15eca692534142427`. Their pinned
URLs and SHA-256 values are recorded in [sources.json](official_h3_prompt/sources.json).
The existing [MiniMax H3 license notice](licenses/minimax-h3-community.txt)
applies. These documents are reviewed reference material; the application does
not execute upstream agent tools or call the hosted H3-Context-IR service.

These sixteen methods are local adaptations written for H3 Studio, informed
by the open-source craft references below. They are not eleven upstream plugins
and do not run their code, install dependencies or call their paid APIs. The
bundled methods work offline after the local writing model has been installed.

Select one method in the script workspace. It supplements outlines, screenplays,
rewrites and H3 storyboards. The project, browser draft and generation input
snapshot retain that selection. The existing manuscript, reference pictures,
user-supplied dialogue, visual style, duration and zero-BGM requirement take
priority. Unselected legacy projects retain their previous writing behavior.
Changing a Skill during generation affects a later request, not the running one.

The default for a new draft remains `cinematic-story`. The eleven genre methods are suspense /
horror, comedy / reaction, emotional drama, fantasy adventure, action / chase,
product commercial, product demonstration, educational explainer, documentary /
lifestyle and game / character trailer. Recommendations are suggestions and only
change the selected method when the user chooses them.

## H3 storyboard library (reviewed 2026-10-06)

Five H3-specific methods supplement the eleven genre methods. Choose them in
the script workspace's **創作 Skill** menu. They apply to the current writing
request and its later storyboard conversion; existing projects keep their choice.

| Studio selection | Reviewed upstream | Repository stars / forks | Useful for |
| --- | --- | ---: | --- |
| H3 官方格式／分鏡 | MiniMax-AI/MiniMax-H3 · h3-prompt-writing | 9,584 / 709 | Modes, local cut times, reference roles and dialogue |
| H3 3D 動畫故事分鏡 | MiniMax-AI/MiniMax-H3 · 3d-animation-short-generator | 9,584 / 709 | Readable animation poses and spatial handoffs |
| H3 角色表演／節拍 | phileiny/h3-storyboard-skill | 175 / 4 | Observable acting and manageable beat density |
| H3 導演／鏡頭銜接 | TFboy1/oh-my-minimaxh3-director | 139 / 19 | Motivated cuts, gaze, direction and state continuity |
| H3 多模態素材／分鏡規劃 | penposs/minimax-h3-video-prompt | 65 / 6 | Asset responsibilities and identity preservation |

Popularity is a GitHub repository snapshot, **not a Skill rating or a measured
video-quality score**. The two official methods share one repository count.
Selection also considers explicit licensing, H3 relevance, maintenance and
compatibility with this Studio. The source panel shows the checked date.

Pinned sources and complete notices:

- [Official H3 Prompt Writing](https://github.com/MiniMax-AI/MiniMax-H3/blob/d21241f0a4b3acbb34c97dae47fa417b7065e438/skills/h3-prompt-writing/SKILL.md)
  and [3D Animation Short Generator](https://github.com/MiniMax-AI/MiniMax-H3/blob/d21241f0a4b3acbb34c97dae47fa417b7065e438/skills/3d-animation-short-generator/SKILL.md).
  MiniMax's repository points to its H3 Community License; these are not marked
  MIT. Full notice: [licenses/minimax-h3-community.txt](licenses/minimax-h3-community.txt),
  obtained from [release revision 42ed227ee7df40d41602854ae760620d6eb651fe](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/42ed227ee7df40d41602854ae760620d6eb651fe/LICENSE).
- [H3 Storyboard](https://github.com/phileiny/h3-storyboard-skill/blob/ce732bae6e05d2e4c4943e3e1fda2d515f9a2ff3/skills/h3-storyboard/SKILL.md),
  MIT, copyright 2026 Ray; [full notice](licenses/h3-storyboard.txt).
- [Oh My MiniMax H3 Director](https://github.com/TFboy1/oh-my-minimaxh3-director/blob/911266137075a0b9fabf6268dc1c91ae05138ad9/SKILL.md),
  MIT, copyright 2026 TFboy1; [full notice](licenses/h3-director.txt).
- [MiniMax H3 Video Prompt](https://github.com/penposs/minimax-h3-video-prompt/blob/2e2bb0e745c178c4db26f99b7ba7c4c53040a961/SKILL.md),
  MIT, copyright 2026 penposs; [full notice](licenses/h3-video-prompt.txt).

The upstream files were installed with `skill-installer` into the local,
ignored `data/skill_sources/h3-20261006` review cache. Runtime loads only the
bounded, reviewed adaptations in this directory. It does not execute upstream
tools, scripts, provider APIs, tunnels, installers, model-selection menus or
MiniMax Hub canvas operations. Official full prompt sections remain owned by
the existing Studio compiler; storyboard output remains valid Studio JSON.

All five methods preserve supplied dialogue, references, duration, visual style,
silent acting and zero-BGM. We do not adopt fake dialogue to trigger expression,
fixed dense-cut formulas, automatic turnaround-image creation, or small-sample
claims about guaranteed facial movement. This addition does not change the
automatic-video requirement to bind referenced characters to images.

Other reviewed candidates were not selected: gnipbao's video-reverse Skill
(213 stars, MIT) requires source-video observation not currently provided by
the writing workspace; chiphoton's drama suite (54 stars, MIT) overlaps existing
scheduling and editing; sjh00's collection (35 stars) had no standalone license
file. These are scope and evidence decisions, not judgments of video quality.

Restart Studio and refresh with Ctrl+F5 after updating. The full review and
source hashes are recorded in [review-20261006.json](review-20261006.json).

A Skill guides the writing model; it is not a new video model and does not
guarantee generation quality. For a sparse idea, the methods help establish a
goal, obstacle, causal change and visible ending. For 15 seconds they favor a few
readable beats instead of many simultaneous actions. Explicit user timelines are
preserved. Short narrative cuts remain inside valid 5–15 second H3 clips.

## Reviewed sources (2026-10-03)

- [Cinematic Director](https://github.com/cajias/agentic-video-skills/blob/204cefd08bbb87fd4681f1f2b0dccbc444a978c1/plugins/cinematic-director/skills/cinematic-director/SKILL.md),
  MIT; copyright 2026 wangzhang-wu. Reference for blocking, shot planning,
  continuity and visible start/end states. Full notice, including its additional
  director-style note: [licenses/cinematic-director.txt](licenses/cinematic-director.txt).
- [AgentWorks Video Storytelling](https://github.com/manishiitg/agentworks/blob/8e4c387f55f3d487f3dc5bce656b4db42db9383d/agent_go/internal/videoproduct/skills/video-storytelling/SKILL.md)
  and [Video Cinematography](https://github.com/manishiitg/agentworks/blob/8e4c387f55f3d487f3dc5bce656b4db42db9383d/agent_go/internal/videoproduct/skills/video-cinematography/SKILL.md),
  MIT; copyright 2025–2026 Manish Prakash and AgentWorks contributors. Reference
  for hook, development and payoff, and explicit subject, motion, scene, space
  and camera direction. Full notice: [licenses/agentworks.txt](licenses/agentworks.txt).
- [Drama Skills Write](https://github.com/zenstory-ai/drama-skills/blob/4e48ccbf0f77da757d7cacc6937b1cc59c124845/skills/short-drama-write/SKILL.md),
  [Storyboard](https://github.com/zenstory-ai/drama-skills/blob/4e48ccbf0f77da757d7cacc6937b1cc59c124845/skills/short-drama-storyboard/SKILL.md)
  and [Video Prompts](https://github.com/zenstory-ai/drama-skills/blob/4e48ccbf0f77da757d7cacc6937b1cc59c124845/skills/short-drama-video-prompts/SKILL.md),
  MIT; copyright 2026 drama-skills contributors. Reference for dramatic causality,
  dialogue timing, character/reference locks and across-shot state handoffs.
  Full notice: [licenses/drama-skills.txt](licenses/drama-skills.txt).

All source links are pinned to reviewed commits. The H3 adapter uses this
project's existing compiler and the [MiniMax official reference prompt guide](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/docs/VIDEO_PROMPT_WRITING_GUIDE_ref_en.md).
Exact source text, external execution instructions, tool chains and foreign
provider formats are not inserted into the writing request. Public source
instructions are reference material, not runtime instructions for this agent.

## Maintenance

Metadata is defined in `script_skills.py`; each fixed ID's local `SKILL.md`
contains the bounded runtime writing rules under `## Writing rules`. Editing a
bundled method requires restarting Studio. The startup fingerprint includes this
directory's Markdown/JSON so a launcher will reject reuse of an old process.
Do not add arbitrary downloaded executable Skills or unreviewed prompt files to
the catalog. Evaluate the license, adapt the craft rules and test duration,
reference and output-format preservation before adding a method.
