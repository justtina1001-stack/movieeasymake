"""Read-only adapter descriptions, separate from saved user selections.

Known means a filename has an identified upstream description; it is not a
claim that a particular local or remote base model has passed inference tests.
"""
from __future__ import annotations

from copy import deepcopy


WUSHU_FILE = "wushu_h3_r2v_v7_2000.safetensors"
COMBAT_FILE = "H3_Combat_V2.safetensors"

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
    return "ref2va" if _basename(name) == WUSHU_FILE.casefold() else None


def required_trigger(name):
    return "wushu_action" if _basename(name) == WUSHU_FILE.casefold() else ""


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
