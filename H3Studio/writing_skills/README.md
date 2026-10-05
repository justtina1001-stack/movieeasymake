# H3 Studio writing Skills

These eleven genre methods are local adaptations written for H3 Studio, informed
by the open-source craft references below. They are not eleven upstream plugins
and do not run their code, install dependencies or call their paid APIs. The
bundled methods work offline after the local writing model has been installed.

Select one method in the script workspace. It supplements outlines, screenplays,
rewrites and H3 storyboards. The project, browser draft and generation input
snapshot retain that selection. The existing manuscript, reference pictures,
user-supplied dialogue, visual style, duration and zero-BGM requirement take
priority. Unselected legacy projects retain their previous writing behavior.
Changing a Skill during generation affects a later request, not the running one.

The default for a new draft is `cinematic-story`. The other methods are suspense /
horror, comedy / reaction, emotional drama, fantasy adventure, action / chase,
product commercial, product demonstration, educational explainer, documentary /
lifestyle and game / character trailer. Recommendations are suggestions and only
change the selected method when the user chooses them.

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
