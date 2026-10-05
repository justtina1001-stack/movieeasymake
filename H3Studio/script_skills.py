"""Bundled, reviewed writing methods; these Skills never execute tools or code."""
from __future__ import annotations

import copy
from pathlib import Path

SKILLS_VERSION = "20261003"
SKILLS_DIRECTORY = Path(__file__).resolve().parent / "writing_skills"
MAX_SKILL_PROMPT_CHARS = 2400

_SOURCES = {
    "director": {
        "name": "Cinematic Director", "license": "MIT",
        "url": "https://github.com/cajias/agentic-video-skills/blob/204cefd08bbb87fd4681f1f2b0dccbc444a978c1/plugins/cinematic-director/skills/cinematic-director/SKILL.md",
        "license_url": "https://github.com/cajias/agentic-video-skills/blob/204cefd08bbb87fd4681f1f2b0dccbc444a978c1/plugins/cinematic-director/skills/cinematic-director/LICENSE",
        "commit": "204cefd08bbb87fd4681f1f2b0dccbc444a978c1",
    },
    "storytelling": {
        "name": "AgentWorks · Video Storytelling / Cinematography", "license": "MIT",
        "url": "https://github.com/manishiitg/agentworks/blob/8e4c387f55f3d487f3dc5bce656b4db42db9383d/agent_go/internal/videoproduct/skills/video-storytelling/SKILL.md",
        "license_url": "https://github.com/manishiitg/agentworks/blob/8e4c387f55f3d487f3dc5bce656b4db42db9383d/LICENSE",
        "commit": "8e4c387f55f3d487f3dc5bce656b4db42db9383d",
    },
    "drama": {
        "name": "Drama Skills", "license": "MIT",
        "url": "https://github.com/zenstory-ai/drama-skills/blob/4e48ccbf0f77da757d7cacc6937b1cc59c124845/skills/short-drama-write/SKILL.md",
        "license_url": "https://github.com/zenstory-ai/drama-skills/blob/4e48ccbf0f77da757d7cacc6937b1cc59c124845/LICENSE",
        "commit": "4e48ccbf0f77da757d7cacc6937b1cc59c124845",
    },
}

# Metadata and genre adaptations are written for H3 Studio. Sources supply
# general craft methods, rather than eleven upstream genre plug-ins.
_PROFILES = [
    {"id": "cinematic-story", "name": "通用電影敘事", "category": "劇情",
     "description": "把一句想法補成有目標、阻礙、轉折與收尾的短故事。",
     "recommended_for": ["只有初步想法", "角色小故事", "一般短片"],
     "guide": ["確定主角想得到什麼，以及什麼阻止他。", "用可見事件串起開場、變化、選擇與結果。", "讓每個切鏡揭露新資訊，最後留下一個明確的畫面。"],
     "sources": ["storytelling", "director", "drama"]},
    {"id": "suspense-horror", "name": "懸疑／恐怖", "category": "懸疑",
     "description": "靠視線、距離、遮擋與環境音逐步建立危機，避免只有不停回頭。",
     "recommended_for": ["跟蹤與逃離", "神祕訪客", "靈異與驚嚇"],
     "guide": ["先建立平常環境，再出現一個具體異常。", "區分角色知道與觀眾知道的資訊，用空間距離升高壓力。", "安排察覺、確認、反應、逃離或揭露，讓每段危機有所改變。", "把腳步、停頓與聲源位置寫清楚，不依賴配樂營造恐懼。"],
     "sources": ["director", "drama", "storytelling"]},
    {"id": "comedy-reaction", "name": "喜劇／角色反應", "category": "喜劇",
     "description": "建立觀眾預期，再用意外結果、停頓與角色反應完成笑點。",
     "recommended_for": ["意外誤會", "動畫搞笑", "角色互動"],
     "guide": ["先讓觀眾看懂角色的目的與道具。", "用一次可見的誤判或反差造成意外。", "保留笑點後的反應時間，以眼神、姿勢與動作收勢完成笑點。"],
     "sources": ["storytelling", "director", "drama"]},
    {"id": "emotional-drama", "name": "情感／人物劇情", "category": "劇情",
     "description": "用具體物件、選擇與細微表演呈現情感，而非只寫悲傷或感動。",
     "recommended_for": ["重逢與告別", "親情友情", "關係轉折"],
     "guide": ["確定兩人關係與這一刻的情感阻礙。", "用一件道具或一個動作承載記憶與選擇。", "寫出視線、呼吸、手勢與距離的變化，收尾呈現關係已改變。"],
     "sources": ["drama", "director", "storytelling"]},
    {"id": "fantasy-adventure", "name": "奇幻／探險", "category": "奇幻",
     "description": "把未知世界、發現、危險與角色抉擇安排成清楚的探險段落。",
     "recommended_for": ["叢林與遺跡", "神祕入口", "魔法世界"],
     "guide": ["先交代探索者的位置、目標與前進方向。", "透過角色的觸碰或靠近逐步揭露奇觀與規則。", "安排一個可見障礙及相應決定，限制特效數量以保留人物動作。"],
     "sources": ["director", "storytelling", "drama"]},
    {"id": "action-chase", "name": "動作／追逐", "category": "動作",
     "description": "保持追逐方向、前後距離與動作因果，讓觀眾知道誰在追誰。",
     "recommended_for": ["逃跑追捕", "障礙穿越", "短動作戲"],
     "guide": ["先建立追逐者、逃離者、出口與障礙的相對位置。", "每個動作依準備、移動、接觸、反應與落地描寫。", "每段只安排少量可完成的動作，切鏡保持同一前進方向與道具狀態。"],
     "sources": ["director", "storytelling"]},
    {"id": "product-commercial", "name": "商品／品牌廣告", "category": "廣告",
     "description": "用一個使用情境與可見好處完成短廣告，保持商品外型與品牌資訊。",
     "recommended_for": ["商品形象", "品牌短片", "情境式廣告"],
     "guide": ["開場呈現一個清楚的使用需求或視覺吸引點。", "用商品實際可見的使用方式證明一個賣點。", "收尾讓商品停穩且可辨識；精確標語與價格留給後製，不憑空新增效果或數據。"],
     "sources": ["storytelling", "director"]},
    {"id": "product-demo", "name": "產品／操作展示", "category": "展示",
     "description": "把操作拆成看得清楚的少量步驟，呈現前後差異與結果。",
     "recommended_for": ["功能示範", "開箱操作", "道具機構展示"],
     "guide": ["先交代產品初始狀態、操作者與工作區。", "逐步寫出手部接觸、操作及物件狀態變化。", "關鍵機構保持在畫面內，最後停留足夠時間供觀眾確認結果。"],
     "sources": ["director", "storytelling"]},
    {"id": "educational-explainer", "name": "知識／教學解說", "category": "教學",
     "description": "一次解釋一個概念，用具體例子與可見變化讓觀眾理解。",
     "recommended_for": ["科普小知識", "步驟教學", "原理解說"],
     "guide": ["確定觀眾要理解的一個問題與已知條件。", "用一個具體示例呈現原因、變化與結果。", "控制旁白長度，資訊逐步出現；缺乏的事實標示待確認，不編造數據。"],
     "sources": ["storytelling", "director"]},
    {"id": "documentary-lifestyle", "name": "紀錄／生活風格", "category": "紀錄片",
     "description": "以觀察、細節與自然聲音串起生活或環境，避免虛構真實人物經歷。",
     "recommended_for": ["日常紀錄", "自然風景", "旅行與手作"],
     "guide": ["從環境、人物活動與一項細節建立觀察重點。", "以自然動作與現場聲音帶出節奏，攝影機有明確觀察位置。", "用結果或環境變化收尾；真實地點、人物與數據只使用提供的資訊。"],
     "sources": ["director", "storytelling"]},
    {"id": "game-trailer", "name": "遊戲／角色宣傳", "category": "遊戲",
     "description": "呈現角色特色、一次招牌動作與清楚的宣傳收勢。",
     "recommended_for": ["遊戲角色登場", "技能展示", "CG 宣傳短片"],
     "guide": ["先建立角色輪廓、服裝、道具與場景尺度。", "挑一個有準備與收勢的招牌動作，限制粒子與鏡頭變化。", "以辨識度高的定姿或結果收尾，避免生成未提供的 UI、標誌或遊戲玩法。"],
     "sources": ["director", "storytelling"]},
]

_COMMON_RULES = (
    "Apply this writing method internally; keep the task's required output format. "
    "For a sparse concept, establish the goal, obstacle, a visible change and a decisive ending; "
    "infer only compatible story details, never unsupported real-world facts or product claims. "
    "Use a small, filmable chain of actions: preparation, movement/contact, reaction and settled state. "
    "Each cut must reveal new information. State subject positions, screen direction, gaze, important hands/props, "
    "camera direction/speed/end framing and synchronized sounds. Preserve named references, appearance, "
    "lighting, supplied dialogue, the user's visual style, total duration and zero-BGM rule. "
    "For 15 seconds prefer 3-4 meaningful beats unless the user already supplied a timeline; retain that timeline. "
    "Short narrative cuts stay inside a 5-15 second H3 generation clip; do not pad, repeat or extend the story. "
    "Before output, check cause/effect, spatial continuity, achievable timing and a readable final state."
)


def _read_rules(skill_id: str) -> str:
    source = (SKILLS_DIRECTORY / skill_id / "SKILL.md").read_text(encoding="utf-8")
    marker = "## Writing rules\n"
    if marker not in source:
        raise ValueError(f"創作 Skill {skill_id} 的寫作規則不完整。")
    rules = source.split(marker, 1)[1].strip()
    if not rules or len(rules) > 1300:
        raise ValueError(f"創作 Skill {skill_id} 的寫作規則超出長度限制。")
    return rules


_CATALOG = {}
for _profile in _PROFILES:
    _record = copy.deepcopy(_profile)
    _record["sources"] = [copy.deepcopy(_SOURCES[key]) for key in _profile["sources"]]
    _record["version"] = SKILLS_VERSION
    _record["instructions"] = _read_rules(_record["id"])
    _CATALOG[_record["id"]] = _record


def list_skills() -> list[dict]:
    return [copy.deepcopy({key: value for key, value in record.items() if key != "instructions"})
            for record in _CATALOG.values()]


def normalize_skill_id(value) -> str:
    if value is None or value == "":
        return ""
    if not isinstance(value, str) or value not in _CATALOG:
        raise ValueError("找不到這個創作 Skill，請從工具提供的類型中選擇，或選擇不套用。")
    return value


def get_skill(skill_id: str) -> dict | None:
    key = normalize_skill_id(skill_id)
    return copy.deepcopy(_CATALOG[key]) if key else None


def skill_prompt(skill_id: str, action: str) -> str:
    if action not in {"outline", "screenplay", "rewrite", "storyboard"}:
        return ""
    record = get_skill(skill_id)
    if record is None:
        return ""
    result = f"\nSelected writing method: {record['name']}. User-specific instructions take priority.\n{_COMMON_RULES}\n{record['instructions']}"
    if len(result) > MAX_SKILL_PROMPT_CHARS:
        raise ValueError("創作 Skill 的提示內容超出長度限制。")
    return result
