"""Bundled, reviewed writing methods; these Skills never execute tools or code."""
from __future__ import annotations

import copy
from pathlib import Path

SKILLS_VERSION = "20261006-h3"
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


def _h3_source(name, repo, commit, path, stars, forks, *, official=False):
    root = f"https://github.com/{repo}"
    return {
        "name": name,
        "url": f"{root}/blob/{commit}/{path}",
        "license": "MiniMax H3 Community License" if official else "MIT",
        "license_url": f"{root}/blob/{commit}/README.md#license" if official else f"{root}/blob/{commit}/LICENSE",
        "commit": commit, "repo_url": root,
        "repository_stars": stars, "repository_forks": forks,
        "checked_at": "2026-10-06",
        "provenance": "official" if official else "community",
    }


# Stars describe the source repository, never the quality of an individual Skill.
# These snapshots are reviewed locally; generation never fetches upstream files.
_SOURCES.update({
    "h3-official": _h3_source("MiniMax 官方 · H3 Prompt Writing", "MiniMax-AI/MiniMax-H3",
        "d21241f0a4b3acbb34c97dae47fa417b7065e438", "skills/h3-prompt-writing/SKILL.md", 9584, 709, official=True),
    "h3-animation": _h3_source("MiniMax 官方 · 3D Animation Short Generator", "MiniMax-AI/MiniMax-H3",
        "d21241f0a4b3acbb34c97dae47fa417b7065e438", "skills/3d-animation-short-generator/SKILL.md", 9584, 709, official=True),
    "h3-beats": _h3_source("H3 Storyboard · 角色表演與節拍", "phileiny/h3-storyboard-skill",
        "ce732bae6e05d2e4c4943e3e1fda2d515f9a2ff3", "skills/h3-storyboard/SKILL.md", 175, 4),
    "h3-director": _h3_source("Oh My MiniMax H3 Director · WenWu", "TFboy1/oh-my-minimaxh3-director",
        "911266137075a0b9fabf6268dc1c91ae05138ad9", "SKILL.md", 139, 19),
    "h3-reference": _h3_source("MiniMax H3 Video Prompt · 素材規劃", "penposs/minimax-h3-video-prompt",
        "2e2bb0e745c178c4db26f99b7ba7c4c53040a961", "SKILL.md", 65, 6),
})

# Metadata and genre adaptations are written for H3 Studio. Sources supply
# general craft methods, rather than eleven upstream genre plug-ins.
_PROFILES = [
    {"id": "h3-official-storyboard", "name": "H3 官方格式／分鏡", "category": "H3 分鏡",
     "description": "依官方 H3 模式與時軸規範規劃鏡頭，交由工具編譯成可用提示詞。",
     "recommended_for": ["H3 分鏡", "時間碼與對白", "角色參考圖對應"],
     "guide": ["區分角色外觀參考與首尾幀，依現有模式規劃。", "每鏡列出構圖、位置、動作、運鏡、聲音及終態。", "短切鏡放在有效影片片段內，保留原台詞及穩定說話角色。"],
     "sources": ["h3-official"]},
    {"id": "h3-3d-animation", "name": "H3 3D 動畫故事分鏡", "category": "H3 動畫",
     "description": "整理故事節拍、角色表演與空間錨點，保持動畫短片的動作與前後交接。",
     "recommended_for": ["3D 動畫短片", "角色喜劇與冒險", "有角色圖的 CG 故事"],
     "guide": ["先建立角色目標與故事因果，再安排必要素材。", "以預備、主動作、反應與收勢呈現角色表演。", "逐鏡追蹤地標、人物位置、道具與光位，避免換鏡後重置。"],
     "sources": ["h3-animation", "h3-official"]},
    {"id": "h3-performance-beats", "name": "H3 角色表演／節拍", "category": "H3 表演",
     "description": "降低單鏡動作負荷，以視線、呼吸及姿態變化呈現情緒，保留無對白表演。",
     "recommended_for": ["人物情緒變化", "無對白表演", "角色特寫"],
     "guide": ["先確定每鏡要讓觀眾理解的變化，再選景別。", "每個主要節拍聚焦一個可見反應，避免同時塞入大量動作。", "只使用原有台詞；沉默角色保持沉默，不插入假對白。"],
     "sources": ["h3-beats", "h3-official"]},
    {"id": "h3-director-continuity", "name": "H3 導演／鏡頭銜接", "category": "H3 導演",
     "description": "以觀察目的與交接錨點安排切鏡，連續追蹤視線、方向、位置與物件狀態。",
     "recommended_for": ["多鏡頭敘事", "動作與追逐", "鏡頭衔接與空間連續"],
     "guide": ["每鏡揭露新資訊，避免換景別重演同一動作。", "將上一鏡的末態接到下一鏡的起態，保持位置與因果。", "運鏡跟隨具體刺激與動作；指定一鏡到底時保持連續。"],
     "sources": ["h3-director", "h3-official"]},
    {"id": "h3-reference-planning", "name": "H3 多模態素材／分鏡規劃", "category": "H3 參考",
     "description": "為每份角色、物件與場景素材分配明確職責，減少外觀、姿勢及背景互相混用。",
     "recommended_for": ["多角色圖片", "商品與場景參考", "固定外觀的分鏡"],
     "guide": ["整理必須出現、必須保持及允許變化的內容。", "每份實際素材有明確職責，只使用已有的外觀描述與綁定。", "核對素材名稱、台詞歸屬、數量與最後可見狀態。"],
     "sources": ["h3-reference", "h3-official"]},
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
