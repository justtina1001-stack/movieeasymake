"""Shared format contract reviewed against the vendored official H3 Skill guides."""
from pathlib import Path
import json

OFFICIAL_PROMPT_REVISION = "199b7d0e1fd95e7b3eafa8c15eca692534142427"
OFFICIAL_PROMPT_VERSION = "20261029-official-format-v1"
OFFICIAL_PROMPT_DIRECTORY = Path(__file__).resolve().parent / "writing_skills" / "official_h3_prompt"


def official_storyboard_rules() -> str:
    """Apply the format contract to every writing style without a second wrapper."""
    return (
        "\nMandatory H3 format contract (independent of creative Skill): "
        "Preserve the supplied timeline, exact speech, visible text and reference relationships. "
        "Write visual directions in English. Use Studio JSON fields; the compiler adds the official "
        "outer sections, so never put a second envelope inside action. Opening action has no timestamp; "
        "later cuts use consecutive [Shot 2] At 00:03.500, markers, with strictly increasing local times "
        "inside each clip. Never pad, extend or replay prescribed events. "
        "Assign one stable (Sx) at each speaker's first actual vocal event; silent subjects receive no ID. "
        "Keep delivery outside <d>; only the language tag and exact original words go inside "
        "<d>[Chinese] 原台詞</d>. Never invent speech or renumber supplied explicit IDs. "
        "Separate appearance, motion, composition, camera, voice timbre and copied-audio reference roles. "
        "Use existing asset aliases; never invent assets or guess label numbers. For every cut, state "
        "composition, positions, visible changes, camera response, synchronized sound and settled ending. "
        "Dialogue stays with action; soundscape covers ambience/physical effects without repeating speech. "
        "Preserve the project's music direction; zero BGM means N/A."
    )


def official_prompt_sources() -> dict:
    return json.loads((OFFICIAL_PROMPT_DIRECTORY / "sources.json").read_text("utf-8"))
