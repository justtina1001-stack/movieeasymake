# H3 Studio 模型與開源加速更新檢查

查核日期：2026-09-29，台灣時間上午。涵蓋目前專案的影片、圖片、音樂、語音、放大模型與執行引擎；版本日期依上游標示，精確提交時間以 UTC 記錄。比較對象是這台 Windows／RTX 5060 Ti 16GB／64GB RAM；同事各自電腦的結果需要分別驗證。

有值得測試的新版本。最接近現有架構的是 H3-Optimizations 0.2.45、comfy-kitchen 0.2.36，以及 Qwen-Image-2.1 的少步數蒸餾與較小文字編碼器。這次完成版本、模型檔案與相容性研究，未安裝新候選、未執行新 GPU 測速；作者的加速數字不代表這台電腦的結果。

## 本機基準與判讀方法

| 項目 | 2026-09-29 本機狀態 |
| --- | --- |
| Studio | main，2df6d2286567d16906f2cf4520c2c90843a2632c；先前 INT8 VAE 選擇與下載支援已推送 |
| ComfyUI | v0.37.0，73c9bad4d21e7addbe1d13bc92eee0f1431b017d |
| 核心套件 | torch 2.13.0+cu130、comfy-kitchen 0.2.35、comfy-aimdo 0.5.5、frontend 1.52.7、transformers 5.14.1 |
| H3 節點 | H3-Optimizations 0.2.42，52b5f1e；FaceRefine d8521d14 |
| H3 權重 | FL／Ref pruned INT8 ConvRot，各約 20.97GB；Qwen3-VL 32B NVFP4 AWQ 編碼器約 15.69GB；FP16 與 2.81GB INT8 VAE 均保留 |
| 圖片 | Qwen-Image-2.1，固定 revision ace0edeb3791a594ddfa36ed5f41a178a394e921；DiT INT8 7.257GB、文字編碼器 INT8 9.351GB、VAE 0.676GB |
| 音樂 | MiniMax Music3 INT8 DiT＋pruned INT8 文字編碼器，Comfy 工作流 |
| 語音 | 獨立 voiceenv；Qwen3-TTS，qwen-tts 0.1.1、torch／torchaudio 2.11.0+cu130、transformers 4.57.3 |
| 放大 | RealESRGAN_x4plus、RealESRGAN_x4plus_anime_6B |
| 其他 | 本機未安裝 SageAttention、Triton、Diffusers；模型主要位於 E: 機械硬碟 |

版本號、權重更新、文件更新分開核對。GitHub REST 部分查詢遇到公開 API 額度限制，改以公開 release、commit Atom、原始碼及 Hugging Face／PyPI 中繼資料交叉確認。中繼資料保存在 `data/diagnostics/upstream_review_20260929/`，不含提示詞或使用者素材。

先前實測的 INT8 VAE 在兩種解析度下，連續解碼時間約減少 63%，高解析測例的整卡最高取樣占用約少 2.3GiB；這是解碼階段結果，不是整支影片生成減少 63%。詳見 [INT8 VAE 實測](INT8_VAE_TEST_2026-09-21.md)。H3 主模型仍大於 16GB VRAM，因此即使解碼改善，採樣時的權重搬移仍可能是主要成本。

## 影片：H3 與開源加速

| 優先 | 候選／更新 | 可用性判斷 |
| --- | --- | --- |
| 高 | H3-Optimizations **0.2.45**，9/24，86277494 | 本機仍為 0.2.42。0.2.44 已補上目前 Comfy forward 結構與 attention override 相容性；0.2.45 再加入 FastH3 VSA。先以既有模型測相容修正，不必先換 H3 權重。 |
| 中 | **FastH3 8-Step V2＋新版 VSA 節點** | 8 步、保留 20% video tiles。權重 9/4 已發布，9/24 的新進展是 Comfy 節點整合。模型蒸餾限 T2VA，不能視為現有 FL2VA／Ref2VA 的加速更新。 |
| 低／實驗 | **Veda-Sparse H3 Preview**，9/25 權重、9/26 FP8 predictor | 8 步 T2VA 的稀疏選塊預測器，保留約 10% tiles。作者有 1.57–2.76× 全流程加速結果，但使用 RTX4090、特定幾何與 offload，排除首次編譯；尚無這台 Windows 16GB 的可用性或速度實測。 |
| 功能 | **H3 Fun ControlNet Union 2.0**，9/22；Comfy INT8 權重 9/24 | 新增塗鴉、版面、灰階等控制，INT8 額外約 4.53GB。需 v0.37 之後的核心支援，增加計算及記憶體負擔，屬新功能。 |
| 服務整合 | **vLLM-Omni 0.30.0**，9/25 | 打包 LightX2V、FastH3 V2、VAE 峰值改善及投影快取。是獨立 Linux／WSL 服務路線；Comfy 節點呼叫服務，並不直接加速現有原生 Comfy 執行。 |

來源：[H3 相容修正 0.2.44](https://github.com/Zironic/H3-Optimizations/commit/64979f6cab34952424eb4ccfceca7839fee04f5f)、[H3 0.2.45](https://github.com/Zironic/H3-Optimizations/commit/862774944a331bc1a66cee1cf805b94bd138ebbe)、[FastH3 V2 模型卡](https://huggingface.co/FastVideo/FastVideo-FastH3-8-Step-V2)、[Veda 模型卡](https://huggingface.co/Veda-Sparse/Minimax-H3-T2VA-Veda-8NFE-600Step-Preview)、[H3 ControlNet Union 2.0](https://huggingface.co/alibaba-pai/MiniMax-H3-Fun-Controlnet-Union-2.0)、[Comfy ControlNet 支援](https://github.com/Comfy-Org/ComfyUI/pull/16471)、[vLLM-Omni 發布](https://github.com/vllm-project/vllm-omni/releases/tag/v0.30.0)。

已做一項直接對應本機的靜態驗證：`MiniMaxH3Model._forward` 的 SHA256 為 `87484f8a01f09a9f20dc20da8287bd1747aee122b0145281cff531d82ddaa6df`。它不在 0.2.42 的 embedding early-release 白名單，卻在 0.2.44 的新白名單。因此新版有機會恢復目前被相容性檢查停用的「提早釋放 embedding」路徑；這不能換算成已測得的 RAM／VRAM 節省量，也不能說舊版所有省顯存功能都失效。

FastH3 新節點拒絕一般 H3 權重，需要對應 learned-gate 模型。上游提供 SM120 binary，但新 VSA kernel 的實機驗證主要是 SM89；自我檢查失敗可回退 BF16 Triton。本機尚未安裝 Triton，且原作者完整配方是四張 B200，故不能承諾 RTX5060 Ti 16GB 直接可跑，或比現有 4-step Turbo 更快。[節點適用條件](https://github.com/Zironic/H3-Optimizations#h3-vsa-attention-fasth3)

Veda 目前使用獨立 Miowtion 執行器及固定幾何，部署文件的 SM120 支援還需額外驗證；24GB 範例仍會 offload 全部 blocks。FP8 predictor 是減少檔案大小，載入時還原為 BF16，不能視為常駐記憶體減半。它很新，但對同事的 Windows 16GB 電腦優先序低。[Veda 部署說明](https://huggingface.co/Veda-Sparse/Minimax-H3-T2VA-Veda-8NFE-600Step-Preview/blob/main/AGENTS.md)

未確認有更晚的 MiniMax 官方 H3 主權重；LightX2V Turbo 最新模型庫改動仍為 9/10，SLA 為 8/20，不能把它們重新算作本週新版。FirstBlockCache 9/12 的版本可列原生多步數測試候選；其約 30% 省時宣稱基於 RTX5090、20 步，不能外推到 4 步 Turbo。FaceRefine d8521d14 與本機相同。[H3 官方歷史](https://huggingface.co/MiniMaxAI/MiniMax-H3/commits/main)、[Turbo 歷史](https://huggingface.co/lightx2v/Minimax-h3-Turbo/commits/main)、[SLA 歷史](https://huggingface.co/lightx2v/Minimax-h3-Turbo-SLA/commits/main)、[FirstBlockCache](https://github.com/duckyshell/ComfyUI-MiniMaxH3-FirstBlockCache)、[FaceRefine](https://github.com/Carasibana/ComfyUI-H3-FaceRefine/commit/d8521d14fe0d721d80cd9417fff5a559cbc21aba)。

## 圖片與放大

| 優先 | 候選 | 可用性與預期改善 |
| --- | --- | --- |
| 高 | Qwen2.1 官方 **W4A8 文字編碼器** | 6.312GB，相較現有 INT8 9.351GB 小約 3.04GB。9/20 就已提供，屬尚未採用的選项。優先驗證記憶體壓力與品質；檔案縮小不等於峰值 VRAM 同幅降低。 |
| 高／需整合 | **Viggle Turbo v0.2.1，6 步**，9/24 | 真正相容 Qwen-Image-2.1；r128 LoRA 約 680MB、r256 約 1.359GB，附 ComfyUI 0.37 範例。需專用 sigmas 與 unmerged LoRA 節點，不能只把現有 25 步改為 6。 |
| 中／需整合 | **Pruna Qwen-Image-2.1 v0.1，8／5 步**，9/24 | 每個 LoRA 約 336MB，作者偏好 8 步品質。官方目前提供 Diffusers 路徑；9/24 明確移除 ComfyUI export，沒有可直接匯入的官方 Comfy workflow。 |
| 中 | **RealESRGAN_x2plus** | 本機 2× 放大先產生 4× 再縮小。原生 x2plus 可避免較大的中間輸出，值得同圖比較；是成熟舊模型，沒有本機速度保證。 |
| 品質 | **Texture Fix VAE for Qwen-Image-2.1** | 權重 9/24 上傳，9/28 是展示 latent 更新。處理細節棋盤紋，並無加速證據；不能當成圖片版 INT8 VAE 效能修正。 |
| 替代路線 | **FLUX.2 klein 4B** | 4-step 蒸餾、官方約 13GB VRAM、有 Comfy 支援及 offload，可測快速構圖／編輯；不是本週新發布，也沒有與 Qwen2.1 相同原生 RGBA 能力的保證。 |

來源：[W4A8 官方檔案](https://huggingface.co/Comfy-Org/Qwen-Image-2.1/blob/main/text_encoders/qwen3vl_8b_w4a8.safetensors)、[Viggle 模型卡](https://huggingface.co/Viggle/Qwen-Image-2.1-viggle-turbo)、[Viggle Comfy 檔案](https://huggingface.co/Viggle/Qwen-Image-2.1-viggle-turbo/tree/main/comfyui)、[Pruna 模型卡](https://huggingface.co/PrunaAI/Pruna-Qwen-Image-2.1)、[Pruna 移除 Comfy export](https://huggingface.co/PrunaAI/Pruna-Qwen-Image-2.1/commit/df1a0458d6883075d45d8977ce8b47ccd73a051f)、[Real-ESRGAN model zoo](https://github.com/xinntao/Real-ESRGAN/blob/master/docs/model_zoo.md)、[Texture Fix VAE](https://huggingface.co/madebyollin/texture-fix-vae-for-qwen-image-2.1)、[FLUX.2 klein 4B](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B)。

Viggle 作者的 1248×832 範例含 prompt enhancer、模型全常駐時約需 26GB VRAM。16GB 需關閉 enhancer、保留 offload，再單獨比較 W4A8；仍須實測是否足夠。一般 LoRA 合併到 BF16／INT8 並不等價於作者的 unmerged 路徑；文字、小細節、多圖身分保持及 RGBA 必須獨立驗證。Pruna 的 H100 80GB 基準包含文字編碼、採樣與 VAE 解碼，但排除模型載入、暖機及 PNG 寫檔，且未啟用 offload，不能直接套用本機。

現有圖片預設是 **25 步、CFG 1、Euler／simple**；歷史工作混有 25／26／30 步，尚無受控 GPU 圖片基準。不能拿作者「40→6 步」的倍率當作本機加速率。[圖片實作](qwen_image.py)

Qwen2.1 的現有 INT8 DiT、INT8 encoder、VAE 三個檔案在目前固定 revision 與最新 main 的 SHA256 相同，近期 README 更新不需要重抓這三個權重。Real-ESRGAN 最新正式 release 仍為 2022-09-20 的 v0.3.0；`realesr-general-x4v3` 可另評估快速、省記憶體模式，但修復能力有取捨。[Qwen Comfy 提交](https://huggingface.co/Comfy-Org/Qwen-Image-2.1/commits/main)、[Real-ESRGAN releases](https://github.com/xinntao/Real-ESRGAN/releases)

若模型用於商業美術產出，Qwen2.1 的 Research License 是實際選擇條件：少步數 LoRA 與 Texture Fix VAE 不會消除基底的商用限制。FLUX.2 klein **4B** 是 Apache 2.0；不能將此結論套到 9B。[Qwen 官方授權](https://huggingface.co/Qwen/Qwen-Image-2.1/blob/main/LICENSE)、[FLUX.2 klein 4B 模型卡](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B)

## 引擎與通用加速

| 候選 | 新近狀態 | 對目前專案的意義與建議 |
| --- | --- | --- |
| comfy-kitchen 0.2.36 | 9/29 發布；本機為 0.2.35 | 包含 CUDA INT8 attention 優化、效能回歸修復與較大 shape 支援；有 Python 3.12 Windows 預編譯 wheel。優先做隔離 A/B，確認 H3、Qwen、Music3 與 INT8 VAE 均正常。升級套件本身不等於所有工作流自動使用 INT8 attention。 |
| ComfyUI | 查核時最新正式 release 仍是 v0.37.0，本機已是此版 | master 已有新功能與修正，但不應稱為新的穩定版。需要新功能時固定測試 commit，通過後才納入同事更新。 |
| Qwen2.1 FP16 修正 | 9/27 合入 master，PR #16608 | 修正 FP16 activation 額外配置引發的 aimdo compiler 錯誤；有助測試 FP16 accumulation。作者 RTX5080 的步速差來自 FP16 accumulation，不是這個修正本身保證的加速。 |
| SeedVR2 優化 | 9/29 合入 master，PR #16530 | VAE 可逐幀處理，INT8 causal cache 放在主記憶體並預取，降低長片解碼的顯存工作集。適合另增高品質放大選項，並非現有 Real-ESRGAN 的直接新版。 |
| W6A8 | 9/29 合入 master，PR #16483 | 增加量化格式讀取支援；還需要相容的 W6A8 權重，不能把現有 INT8 檔案改名後使用，也没有證據可直接保證較快。 |
| triton-windows 3.8.0.post29 | 9/28 UTC 發布，本機未安裝 | RTX50 系列有可用路線，但它是特定 kernel／編譯功能的依賴，不是安裝後就自動加速全部生成。必須配合所選加速器的 Torch／CUDA 相容組合。 |
| SageAttention 2／3 | 已有開源實作，Sage3 面向 Blackwell | 可作後續注意力實驗。官方 Windows 相關 PR 仍有未合入項目；PyPI 的 sageattention 最新標籤仍為舊 1.0.6，不宜直接用未固定來源的安裝指令。優先測現成 Kitchen CUDA 路徑。 |
| PyTorch／Transformers | PyPI 分別有 2.14.0／5.17.0，本機較舊 | 數字較新不代表這些工作流必定更快；與量化、CUDA graph、語音獨立環境分開處理，不作本次第一優先。 |

來源：[Kitchen 0.2.36 發布與 Windows 檔案](https://pypi.org/project/comfy-kitchen/0.2.36/)、[INT8 attention 優化](https://github.com/Comfy-Org/comfy-kitchen/pull/208)、[mask 優化](https://github.com/Comfy-Org/comfy-kitchen/pull/207)、[Kitchen 提交紀錄](https://github.com/Comfy-Org/comfy-kitchen/commits/main/)、[Comfy releases](https://github.com/Comfy-Org/ComfyUI/releases)、[Qwen FP16 修正](https://github.com/Comfy-Org/ComfyUI/pull/16608)、[SeedVR2](https://github.com/Comfy-Org/ComfyUI/pull/16530)、[W6A8](https://github.com/Comfy-Org/ComfyUI/pull/16483)、[Triton Windows](https://github.com/woct0rdho/triton-windows)、[Triton 版本](https://pypi.org/project/triton-windows/)、[SageAttention](https://github.com/thu-ml/SageAttention)、[Windows PR](https://github.com/thu-ml/SageAttention/pull/397)、[PyTorch](https://pypi.org/project/torch/)、[Transformers](https://pypi.org/project/transformers/)。

本機 v0.37 已有 `ModelAttentionBackend` 節點，可逐模型選擇 `comfy kitchen attention`，不必為了 H3 測試就切換所有模型的全域預設。節點仍標示 experimental，需檢查 H3Memory／LoRA／稀疏注意力是否保留 override，並用日誌確認沒有悄悄回退。[本機實作](../ComfyUI/comfy_extras/nodes_model_advanced.py)

查核時 master 快照為 `56c50055cfed1cec453252d32a0ed608dc48e9b4`（9/29 03:04:41 UTC），其 requirements 已要求 Kitchen 0.2.36；本機 v0.37 的 Qwen、Music3 既有優化不能重複算成新收益。comfy-aimdo 0.5.5 已是 PyPI 當時最新版。後續實測前以 `/system_stats` 再確認，本機 frontend 1.52.7 與這份核心要求一致；1.53.6 是查核時 master 的要求，不能混用兩個版本而判定本機依賴不一致。

## 音樂與語音

| 項目 | 查核結果 | 對這台電腦的選擇 |
| --- | --- | --- |
| MiniMax Music3 | Comfy 模型庫 9/23 的改動是 README／工作流連結；INT8 權重仍為 8/12–13 上傳，未找到新版官方 Turbo／蒸餾權重 | 維持目前 INT8 基準。現行下載指向 main 且未保存 revision／hash，不能宣稱已逐位元確認本機與上游完全相同。 |
| Qwen3-TTS | qwen-tts 最新仍為 0.1.1；本機 0.6B Base、0.6B CustomVoice、1.7B VoiceDesign 的下載 revision 與官方 main 相符 | 不需要為追版本重抓相同模型。先改善每筆工作重新建立程序及載入模型的成本。 |
| audio.cpp | v0.8.2 release 為 9/24 UTC，另有 9/25 的 v0.8.2-audio8-perf-hotfix；提供 Windows x64 CPU／Vulkan／CUDA 預編譯包，支援 Qwen3-TTS、Music3 | 最值得在 Windows 另開測試的音訊後端。需 GGUF 等相容模型格式、呼叫與 GPU 排程整合；不是 safetensors 的直接替换。作者 Ubuntu RTX5090 的速度不能套用本機。 |
| vLLM-Omni 0.30.0 | 9/25 release；Qwen3-TTS event-driven orchestration 預設開啟，CUDA ModelRunner V2 仍屬實驗 | 偏向 Linux／WSL 服務與串流 API 的整合；不是原生 Windows voiceenv 的就地升級。多人各自 16GB 電腦不宜直接複製高併發設定。 |
| ACE-Step 1.5 2B Turbo | 開源音樂替代路線，有官方 Windows portable／API 與較低 VRAM 設定；不是本週才出現的新版本 | 適合「快速配樂草稿」另設選項；需比較中文歌詞、音色與品質，不能直接替換 Music3 檔案。4B XL 對 16GB 需考慮 offload，優先測 2B。 |
| MOSS-TTS-Nano 0.1B | 有官方 ONNX CPU 路線、中文及聲音複製 | 若目標是生成語音時保留 GPU 給其他工作，可評估；需要新後端及品質比較。 |
| YuE2-3B | 原作者參考環境為 Linux、24GB NVIDIA GPU；權重為 CC BY-NC 4.0 | 對目前 16GB 與可能商業使用的工作室，不列第一優先。Comfy master 新的 AR 加速也不會自動加速 Music3。 |

來源：[Music3 權重歷史](https://huggingface.co/Comfy-Org/MiniMax-Music-3/commits/main)、[Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS)、[qwen-tts 套件版本](https://pypi.org/project/qwen-tts/)、[audio.cpp 發布](https://github.com/0xShug0/audio.cpp/releases/tag/v0.8.2-audio8-perf-hotfix)、[audio.cpp Music3 文件](https://github.com/0xShug0/audio.cpp/blob/main/docs/community_models/minimax_music3.md)、[vLLM-Omni 0.30.0](https://github.com/vllm-project/vllm-omni/releases/tag/v0.30.0)、[vLLM 平台要求](https://docs.vllm.ai/en/latest/getting_started/installation/gpu/)、[ACE-Step 1.5](https://github.com/ace-step/ACE-Step-1.5)、[MOSS-TTS-Nano](https://github.com/OpenMOSS/MOSS-TTS-Nano)、[YuE2-3B 模型卡](https://huggingface.co/m-a-p/YuE2-3B)。

Music3 的本機文字編碼器約 9.20GB，比 2.50GB DiT 大。audio.cpp 提供分階段載入的 `mem_saver` 及較小量化路線，值得量測；同時常駐所有階段的 overlap 模式會增加顯存，不應當成 16GB 的預設。CFG 重用屬近似計算，必須比較音樂品質。

目前 [voice.py](voice.py) 每筆語音啟動新 subprocess，[voice_worker.py](voice_worker.py) 隨後 `from_pretrained` 載入模型。先量測冷載入占比，再試限時重用、同模型連續處理與官方 `create_voice_clone_prompt` 特徵重用，可能比更換權重更直接；需配合既有共享 GPU lock，讓影片工作可以卸載語音模型。這是程式檢查所得的優化候選，尚未量測收益。

## 驗證與發佈順序

1. 固定現有成功基準，先測 H3-Optimizations 0.2.45 對 v0.37 的相容修正；維持相同步數、種子、素材與 VAE，確認一般 FL／Ref 工作流及省顯存功能。
2. 在獨立測試環境比較 Kitchen 0.2.35／0.2.36，再單獨比較 PyTorch／Kitchen attention。一次只變更一個因素；不要一次疊加所有快取、量化與稀疏外掛。
3. 圖片先測較小編碼器，再接少步數蒸餾的專用工作流；確認中文文字、人物一致性、細節與多圖參考，並記錄全流程時間而非只計採樣。
4. FastH3、Veda、audio.cpp 與 vLLM-Omni 分別作新執行路線試驗；通過功能和資源驗證後再加入 Studio 選項。
5. 通過的版本才固定 commit、wheel、模型 revision／雜湊，提供同事明確更新路徑。Studio 的 Git 更新不會自然涵蓋被排除的 ComfyUI、custom_nodes、虛擬環境與模型檔。

每組記錄冷啟動及至少三次暖執行的總時間、文字編碼／採樣／VAE／後處理時間、峰值 VRAM、程序 RAM、磁碟讀取、桌面操作反應；固定輸出長度與解析度，另外檢查 OOM、取消工作及下一筆是否正常。快取／稀疏方法需要額外檢查運動、臉部、口型與聲音；更快與生成時桌面更順是兩項不同指標。
