"""Read-only adapter descriptions, separate from saved user selections.

Known means a filename has an identified upstream description; it is not a
claim that a particular local or remote base model has passed inference tests.
"""
from __future__ import annotations

from copy import deepcopy


WUSHU_FILE = "wushu_h3_r2v_v7_2000.safetensors"
COMBAT_FILE = "H3_Combat_V2.safetensors"
CAMERA_FILE = "camera_motion_h3_lora_v1_3000_pruned.safetensors"
PHYSICS_FILE = "wushu_spatial_physics_clean_3000_pruned.safetensors"
ORBIT_FILE = "minimax_h3_flf2v_lora_v1.safetensors"
SWAP_FILE = "h3_character_swap_pro4500_1000.safetensors"
REALISM_FILE = "h3-realism-people-t2v-i2v-r2v.safetensors"
ANIMATION_FILE = "3dsrx_minimax_h3_rank16-step00001250.safetensors"

_CUSTOM = {
    WUSHU_FILE.casefold(): {
        "title": "Wushu 武術動作 v7 · Ref2VA",
        "purpose": "作者提供的武術動作 LoRA，用於描述攻防招式、兵器動作與動作銜接。",
        "family": "ref2va", "trigger": "wushu_action", "trigger_required": True,
        "usage": "只選擇需要武術表現的影片；啟用且套用 Ref2VA 時會帶入 wushu_action。每個鏡頭集中描述 1～2 個可讀動作，明確指名角色與招式。可自行調整強度或停用。",
        "limits": "這份是 r2v v7 權重，不能當作 FL2VA 版本。尚未以本機底模推論驗證；不保證修正所有解剖、角色數量或接觸錯誤。模型卡授權為 other，請查閱來源條款。",
        "source_url": "https://huggingface.co/Jojocodex/wushu-action-v7-minimax-h3-fl2va-ref2va-lora",
        "known": True, "license": "other",
        "revision": "1aab496db1c70df44b3edb2cf4e90d8ae3c5b26d",
        "sha256": "f070becda73c65a19a8204384db51bb8204d2303055852172c25aa699589326e",
        "size": 310167928,
        "default_strength": 0.5,
    },
    COMBAT_FILE.casefold(): {
        "title": "Combat Base V2 · 戰鬥動態",
        "purpose": "作者定位為連續攻防、受擊反應及動作因果的 H3 戰鬥 LoRA。",
        "family": "both", "trigger": "", "trigger_required": False,
        "usage": "作者建議先不加觸發詞，以比較身體結構。需要更強動態時可手動加入 prfight2；重擊特技可手動加入 prfight2, prfin1。作者建議 res_multistep/simple 或 euler/beta；可自行調整強度或停用。",
        "limits": "作者未明確區分 FL2VA／Ref2VA 適用版本；兩者可選不代表已驗證相容。本機尚未推論測試，動作與畫質仍需比較；模型卡未聲明授權，不推定為 Apache 或可無條件商用。",
        "source_url": "https://huggingface.co/JOKER141/MiniMax-H3-Combat-Base-V2",
        "known": True, "license": "undeclared",
        "revision": "233a183fb7d28b3b10d777b00d9b3ed3c691af81",
        "sha256": "5b3edb09e9d6029439badf9bc9db4ba4a355ae679b0810888de82c4793c0bac8",
        "size": 155110280,
        "default_strength": 0.5,
    },
    CAMERA_FILE.casefold(): {
        "title": "Camera Motion · 攝影機運動",
        "purpose": "補強推近、拉遠、跟拍、環繞與航拍的運鏡表現。",
        "family": None, "trigger": "camera motion", "trigger_required": True,
        "trigger_placement": "prefix", "default_strength": 0.8,
        "usage": "生成時將 camera motion 放在視覺敘述開頭，再描述主體、場景與單一主要運鏡。作者建議 0.8～1.0；可自由調整或停用。",
        "limits": "作者使用 H3 pruned／INT8／ConvRot，未明確區分 FL2VA 與 Ref2VA；需手動指定適用家族並比較結果。超過 1.2 可能不穩，不能保證修正角色分身。尚未在本機推論驗證。",
        "source_url": "https://huggingface.co/Jojocodex/minimax-h3-Camera-Motion-lora",
        "known": True, "license": "apache-2.0",
        "revision": "fac88d6196ca84f3cee014ec79c71dc73572ab0a",
        "sha256": "9d7d98d7377f56efed3aa7d507f767936112d208906f49908ec9a8ae912be88b",
        "size": 155109680,
    },
    PHYSICS_FILE.casefold(): {
        "title": "Spatial Physics · 空間物理",
        "purpose": "補強物體碰撞、掉落、反彈、遮擋與空間關係。",
        "family": None, "trigger": "", "trigger_required": False,
        "default_strength": 0.3,
        "usage": "使用 clean 3000 pruned 版本，從作者最新建議的 0.3～0.5 開始；描述清楚物件數量、碰撞對象、運動方向與結果。作者說明可與 Turbo 比較搭配。",
        "limits": "仍在訓練與測試階段，效果不穩；主要學習物體物理，不能保證解決人物重複或腳印數量。作者未明確區分 FL2VA／Ref2VA；需手動指定家族。尚未在本機推論驗證。",
        "source_url": "https://huggingface.co/Jojocodex/minimax-h3-spatial-physics-lora",
        "known": True, "license": "apache-2.0",
        "revision": "476b24df28b9b7cc5481b750469698f5cc4b0558",
        "sha256": "7d14f3701560068e7004159c8b2a7278bd2dbfc9e5e3b60d0bc9aef6c049919d",
        "size": 155109672,
    },
    ORBIT_FILE.casefold(): {
        "title": "360 Orbit · 定格環繞",
        "purpose": "主體維持定格姿勢，由攝影機繞行一圈；適合角色或道具展示。",
        "family": "fl2va", "trigger": "", "trigger_required": False,
        "default_strength": 1.0,
        "usage": "在首尾圖片模式使用同一張圖作首幀與尾幀，描述時間凍結、只有攝影機連續環繞。作者測試 768×768、約 3 秒、28 步、強度 1.0、關閉聲音；請依來源範例比較。",
        "limits": "只適用 FL2VA，不可用於腳本自動影片的 Ref2VA。不是一般人物表演 LoRA；作者資料是 28 段以人物為主的短方形片段，本工具 5～15 秒及其他主題尚未推論驗證。",
        "source_url": "https://huggingface.co/pablodawson/MiniMax-H3-360-Orbit-LoRA",
        "known": True, "license": "minimax-h3-community-license-agreement",
        "revision": "5ddbc2dbbe95edbbdaf5017c3e934b1d01791697",
        "sha256": "14f13e3effaf3e729fdc0c97680344aa63f473be0c55963f963d718b3db2a4d4",
        "size": 155111424,
    },
    SWAP_FILE.casefold(): {
        "title": "Character Swap · 影片角色替換",
        "purpose": "依角色圖片替換來源影片的指定人物，嘗試保留原場景。",
        "family": "ref2va", "trigger": "", "trigger_required": False,
        "default_strength": 1.0, "requires_source_video": True,
        "usage": "到影片工作室的角色替換模式，提供原始表演影片與新角色圖片，指名要替換的人物；作者起始強度為 1.0。不需要額外觸發詞。",
        "limits": "實驗性 Ref2VA adapter，必須有來源影片；只有圖片的腳本自動影片不能套用。長片、切鏡、動作時序、表情及音訊仍可能漂移，不能當成通用身份鎖定。尚未在本機推論驗證。",
        "source_url": "https://huggingface.co/akatz-ai/MiniMax-H3-Character-Swap-LoRA",
        "known": True, "license": "minimax-h3-community-license-agreement",
        "revision": "62407e0cc8089c363abd9ce4b0b27662abb237af",
        "sha256": "4b2a3f420ae804c0aa3422761ff84dbd1bf52eef6900ffab6d2e66df63cb4e79",
        "size": 155110320,
    },
    REALISM_FILE.casefold(): {
        "title": "Realism People · 寫實人物",
        "purpose": "補強真人近景、皮膚紋理、表情、手勢與電影式光線。",
        "family": "both", "trigger": "r34l1sm", "trigger_required": True,
        "trigger_placement": "prefix", "default_strength": 0.8,
        "usage": "作者支援文生、圖生與參考生影片；生成時在視覺敘述開頭帶入 r34l1sm。作者完整效果強度 1.0，較輕效果 0.6～0.8；可自行調整。",
        "limits": "偏向真人與寫實畫風，可能改變 3D CG 或卡通角色的質感；不要預設所有題材都套用。作者宣稱支援兩個家族，本機尚未推論驗證，仍需比較畫面與聲音。",
        "source_url": "https://huggingface.co/fal/MiniMax-H3-Realism-People-LoRA",
        "known": True, "license": "minimax-h3-community-license",
        "revision": "039cc8579d7aa357a882d7f4111b25da4f72dccc",
        "sha256": "acc529601d2da117fb81179e76c56e488a3beab1171659d305f04fa3655b787e",
        "size": 131229656,
    },
    ANIMATION_FILE.casefold(): {
        "title": "3D Animation Style · 3D 動畫風格",
        "purpose": "強化 3D 動畫畫風；適合探索明亮 CG、動物與奇幻角色題材。",
        "family": None, "trigger": "3dsrx", "trigger_required": True,
        "default_strength": 0.4, "gated": True,
        "usage": "先到作者 Hugging Face 頁面登入並自行接受存取條款，取得檔案後放入引擎自訂 LoRA 資料夾並重新掃描。作者建議文生 0.8、圖生 0.4；更強風格可自行比較。",
        "limits": "作者要求登入接受存取條款。模型卡只明列文生與圖生，沒有確認 Ref2VA；取得檔案後仍需手動指定家族並測試。這是風格 adapter，不保證角色數量或身份一致。",
        "source_url": "https://huggingface.co/SOLRICKS/3D-Animation-Style-MiniMax-H3",
        "known": True, "license": "minimax-h3-community-license-agreement",
        "revision": "c06b8d8d44cb6ba0e5224549b3d243d1728f7019",
    },
}

# Profile and quality-mode keys match H3Studio's existing quality compiler.
# These records explain automatic adapters; they are never custom selections.
_BUILTINS = (
    ("fl2v_544", "turbo", "FL2VA Turbo 穩定 · 8 步", "fl2va",
     "一般文生、首尾與續接的少步數預覽；此設定使用 8 步。",
     "Euler、simple、Shift 12/3，由生成品質選項自動配置。",
     ("minimax_h3_fl2v_lightx2v_turbo_8step_v1.0_resized_avg_rank_24_bf16.safetensors",
      "minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors")),
    ("fl2v_768", "turbo", "FL2VA Turbo 768p · 4 步", "fl2va",
     "1344×768 穩定版快速預覽；此設定使用 4 步。",
     "Euler、simple、Shift 6/3；Turbo 穩定版依影片尺寸自動選擇。",
     ("minimax_h3_fl2v_lightx2v_turbo_4step_v1.0_768p_resized_avg_rank_31_bf16.safetensors",
      "minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors")),
    ("fl2v_768_fast_v11", "turbo_fast", "FL2VA Turbo v1.1 · 4 步", "fl2va",
     "新版 768p 快速預覽；亦供現有實驗性稀疏模式使用。",
     "Euler、simple、Shift 6/3；從 Turbo 新快速或實驗性稀疏選項使用。",
     ("minimax_h3_fl2v_turbo_4step_v1.1_768p_comfyui_bf16.safetensors",)),
    ("fl2v_768_quality_v10", "turbo_quality", "FL2VA Turbo 品質 · 8 步", "fl2va",
     "用 8 步比較畫面與音訊品質；相較 4 步會增加運算量。",
     "Euler、simple、Shift 6/3；從 Turbo 新高品質選項使用。",
     ("minimax_h3_fl2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors",)),
    ("ref2v_544", "turbo", "Ref2VA Turbo 穩定 · 4 步", "ref2va",
     "多模態與角色參考的少步數預覽；此設定使用 4 步。",
     "Euler、simple、Shift 12/3、參考圖 match；依 Ref2VA 模式自動選擇。",
     ("minimax_h3_ref2v_lightx2v_turbo_4step_v0.1_resized_avg_rank_20_bf16.safetensors",
      "minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors")),
    ("fl2v_768_sla", "turbo_sla", "FL2VA Turbo-SLA · 實驗性 4 步", "fl2va",
     "搭配現有 SLA 稀疏注意力流程的實驗性加速。",
     "Euler、simple、Shift 6/3；需 SLA 節點，專用品質選項會配置注意力設定。",
     ("minimax_h3_fl2v_turbo_4step_v0.1_768p_sla_comfyui_bf16.safetensors",)),
    ("fl2v_768_audio_v12", "turbo_audio", "FL2VA Turbo v1.2 · 聲音比較", "fl2va",
     "現有聲音改善品質選項的 768p 4 步版本，供音訊與畫面比較。",
     "Euler、simple、Shift 6/3；從 Turbo 聲音改善選項使用。",
     ("minimax_h3_fl2v_turbo_4step_v1.2_768p_comfyui_bf16.safetensors",)),
    ("ref2v_768_quality_v10", "turbo_ref_quality", "Ref2VA Turbo 品質 · 8 步", "ref2va",
     "以 8 步比較多模態參考品質；相較 4 步會增加運算量。",
     "Euler、simple、Shift 12/3、參考圖 match；從 Ref Turbo 品質比較選項使用。",
     ("minimax_h3_ref2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors",)),
)


def _basename(name):
    return name.replace("\\", "/").rsplit("/", 1)[-1].casefold() if isinstance(name, str) else ""


def known_lora_family(name):
    """Only constrain an author-explicit adapter family, not unknown adapters."""
    family = _CUSTOM.get(_basename(name), {}).get("family")
    return family if family in {"ref2va", "fl2va"} else None


def required_trigger(name):
    metadata = _CUSTOM.get(_basename(name), {})
    return metadata.get("trigger", "") if metadata.get("trigger_required") else ""


def trigger_placement(name):
    return _CUSTOM.get(_basename(name), {}).get("trigger_placement", "visual")


def requires_source_video(name):
    return _CUSTOM.get(_basename(name), {}).get("requires_source_video") is True


def describe_lora(name, *, image=False):
    if not image and _basename(name) in _CUSTOM:
        return dict(name=name, **deepcopy(_CUSTOM[_basename(name)]))
    if image:
        return dict(name=name, title=name.replace("\\", "/").rsplit("/", 1)[-1].removesuffix(".safetensors") or "Qwen 圖片 LoRA",
                    purpose="引擎 Qwen-Image-2.1 專用資料夾中的自訂圖片 LoRA；作者用途尚未收錄。",
                    family="qwen-image-2.1", trigger="", trigger_required=False,
                    usage="依作者說明選擇用途、觸發詞與強度；可選擇不套用或將強度設為 0。",
                    limits="資料夾分類不代表已驗證與 Qwen-Image-2.1 相容。未確認訓練版本、效果與授權；不會推測或自動加入觸發詞。",
                    source_url="", known=False)
    return dict(name=name, title=name.replace("\\", "/").rsplit("/", 1)[-1].removesuffix(".safetensors"),
                purpose="自訂影片 LoRA；作者用途尚未收錄。", family="both", trigger="", trigger_required=False,
                usage="依作者說明指定 FL2VA／Ref2VA、觸發詞與強度；可自由停用。",
                limits="用途、底模相容性及授權尚未確認；可選兩個家族不表示已驗證相容，也不保證改善動作。",
                source_url="", known=False)


def describe_loras(names, *, image=False):
    return [describe_lora(name, image=image) for name in names]


def recommended_lora_items(names):
    """Show candidates without treating absent weights as engine selections."""
    available = {}
    for name in names:
        available.setdefault(_basename(name), name.replace("\\", "/"))
    items = []
    for filename, metadata in _CUSTOM.items():
        # Metadata keys are casefolded; recover the exact public filename.
        exact = next(value for value in (WUSHU_FILE, COMBAT_FILE, CAMERA_FILE,
                     PHYSICS_FILE, ORBIT_FILE, SWAP_FILE, REALISM_FILE, ANIMATION_FILE)
                     if value.casefold() == filename)
        installed = filename in available
        details = {"trigger_placement": "visual", "requires_source_video": False, **deepcopy(metadata)}
        items.append(dict(name=available.get(filename, "h3studio_custom/" + exact),
                          **details, installed=installed,
                          manual_selectable=installed,
                          availability="installed" if installed else "gated" if metadata.get("gated") else "missing"))
    return items


def builtin_lora_items(names):
    available = {name.replace("\\", "/").casefold(): name.replace("\\", "/") for name in names}
    items = []
    for key, quality_mode, title, family, purpose, usage, candidates in _BUILTINS:
        chosen = next((available[n.casefold()] for n in candidates if n.casefold() in available), candidates[0])
        source = "Kijai/MiniMax-H3_comfy" if "lightx2v" in chosen else "Comfy-Org/MiniMax-H3"
        items.append(dict(name=chosen, key=key, profile=key, quality_mode=quality_mode,
                          title=title, family=family, purpose=purpose, trigger="", trigger_required=False, usage=usage,
                          limits="只由生成品質選項自動套用，不可再當作自訂 LoRA 疊加。少步數會改變生成結果，速度、畫質與聲音需依鏡頭比較。",
                          source_url="https://huggingface.co/" + source,
                          known=True, manual_selectable=False,
                          installed=any(n.casefold() in available for n in candidates)))
    return items
