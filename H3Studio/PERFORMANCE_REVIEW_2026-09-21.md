# H3 Studio 影片生成效能檢查（2026-09-21）

有明確優化空間，優先順序是：確認同事實際使用的引擎與工作設定、降低參考素材的解碼負擔、測試現成注意力與 VAE 加速，再改善素材快取與後處理排程。使用者已確認同事各自在自己的電腦生成；下列硬體與歷史時間來自目前這台電腦，不能直接當成同事電腦的測速結果。

初步檢查涵蓋程式、安裝版本、歷史工作與上游第一手資料，沒有執行完整生成流程的 GPU A/B 測試。後續依使用者要求安裝並實測 INT8 VAE：兩種解析度的連續解碼時間減少約 63%，解碼期間整卡最高取樣占用減少約 2.3GiB，詳見 [INT8 VAE 實測](</E:/MINIMAX H3/H3Studio/INT8_VAE_TEST_2026-09-21.md>)。正式生成程式、VAE 預設與啟動參數未更動。初步匿名統計存於 [檢查資料](</E:/MINIMAX H3/H3Studio/data/diagnostics/performance_review_2026-09-21.json>)，不包含提示詞、素材名稱、作品名稱或金鑰。

## 已確認的本機現況

| 項目 | 結果 |
| --- | --- |
| GPU／RAM | RTX 5060 Ti，16GB VRAM；64GB RAM |
| 模型磁碟 | E:，WDC WD4005FZBX SATA 機械硬碟；C: 為 NVMe |
| 核心 | ComfyUI 0.37.0，73c9bad4；PyTorch 2.13.0+cu130 |
| 執行套件 | comfy-kitchen 0.2.35、comfy-aimdo 0.5.5；DynamicVRAM 已啟用 |
| 注意力 | 現行日誌為 PyTorch attention；已安裝 Comfy Kitchen CUDA backend，但未選 CK dense attention |
| 加速節點 | H3-Optimizations 0.2.42，52b5f1e |
| H3 基礎模型 | 已使用 pruned INT8 ConvRot，每套約 20.97GB；文字編碼器約 15.69GB |
| 影片 VAE | 正式工作流仍指定 FP16，約 5.21GB；後續已安裝並完成 2.81GB INT8 ConvRot VAE 的獨立對照 |

INT8 主模型本身仍比這張卡的 VRAM 大，還要容納運算暫存，因此 RAM 與 GPU 間交換權重是合理預期。機械硬碟對冷載入、換模型及 RAM 不足時的重讀可能不利；沒有負載中的磁碟量測，不能認定每一步都從 HDD 讀取。ComfyUI 本身有模型快取，不能說每筆必然重新載入所有權重。

Windows 顯示本次開機以來 pagefile 峰值約 34.2GiB，但這是全系統累計，不能歸因於 H3。檢查時 GPU 閒置，無法据此評斷生成中的溫度、功耗或峰值資源。

## 歷史紀錄顯示的負載

解析出 82 筆工作：78 完成、2 取消、2 顯存不足失敗。另有 2 個無法解析的 JSON 被略過。可讀的 80 份工作流均未含 H3MemoryOptimization 節點。

最近 24 筆（9/11–9/18）全部明確保存 `memory_optimization: false`；其中 20 筆使用 Ref Turbo 品質比較的 8 步，4 筆使用原生 20 步。這也由實際保存的工作流確認，並非只看介面標籤。

| 歷史設定 | 筆數 | 引擎執行時間中位數 | 範圍 |
| --- | ---: | ---: | ---: |
| Ref2VA、8 步、1344×768、15.083 秒 | 15 | 68.1 分鐘 | 60.0–92.0 分鐘 |
| Ref2VA、8 步、1280×736、15.083 秒 | 5 | 57.4 分鐘 | 53.1–69.4 分鐘 |

以上是不同素材、Seed、快取狀態及舊引擎版本的歷史資料，不能用兩列相除推算降解析度的加速比，也不是 0.37.0 的新測速。`execution_seconds` 使用 Comfy 執行起迄；目前不能再分解文字編碼、採樣、VAE 的時間。最近兩筆的引擎結束至工作更新僅約 3–5 秒，顯示這兩筆的主要等待發生在引擎端，非下載收尾。

## 優先改善項目

| 優先 | 發現與程式證據 | 建議 | 主要改善對象 |
| --- | --- | --- | --- |
| 1 | 新表單的省顯存預設勾選，但舊設定／歷史配方仍可恢復為 false；預設品質仍是 native 20 步。[表單](</E:/MINIMAX H3/H3Studio/static/index.html:137>)、[省顯存工作流](</E:/MINIMAX H3/H3Studio/domain.py:971>) | 增加清楚的「預覽／正式輸出」配方與實際步數、負載提示。試動作用 5 秒、0.4MP；Ref 用穩定 Turbo 4 步。FL 的低解析穩定 Turbo 是 8 步，不要一律手改成 4。舊專案提示使用者選擇省顯存，不靜默改舊成品配方。 | 單次時間、VRAM 峰值、重試成本 |
| 2 | 參考影片經 LoadVideo→GetVideoComponents 完整解碼，H3 才縮圖、裁幀。[建圖](</E:/MINIMAX H3/H3Studio/domain.py:1032>)、[解碼堆疊](</E:/MINIMAX H3/ComfyUI/comfy_api/latest/_input_impl/video_types.py:595>)、[H3 縮圖裁幀](</E:/MINIMAX H3/ComfyUI/comfy_extras/nodes_minimax_h3.py:319>) | 建立獨立的參考代理影片：只取需要片段、按時間重取樣 24fps、按輸出尺寸縮小，保留原素材及音軌。代理快取包含素材內容、片段、尺寸及轉換版本。 | 使用影片參考時的 RAM、CPU、I/O、傳輸、VAE 負載 |
| 3 | 目前 dense attention 使用 PyTorch，但核心已有 CK INT8 attention。[選項](</E:/MINIMAX H3/ComfyUI/comfy/cli_args.py:152>)、[後端選擇](</E:/MINIMAX H3/ComfyUI/comfy/ldm/modules/attention.py:879>) | 先以獨立設定測 `--use-ck-attention`，確認實際後端與同 Seed 品質。已有 CUDA 套件可做候選，不必先引入多套新依賴。 | 採樣時間／顯存；收益待測 |
| 4 | 工作流與安裝清單都固定 FP16 影片 VAE。[工作流](</E:/MINIMAX H3/H3Studio/domain.py:1015>)、[清單](</E:/MINIMAX H3/H3Studio/model_manifest.json:59>) | 增加可回退的 INT8 ConvRot VAE 選項，配合當前核心測試編碼、解碼、臉部細節及色彩；不能只下載檔案而不更新工作流選擇。 | VAE 載入、權重占用、解碼時間 |
| 5 | 每筆工作將同素材上傳至新 job 路徑，改變 conditioning 的祖先輸入。[上傳](</E:/MINIMAX H3/H3Studio/app.py:1884>) | 按引擎／使用者／內容雜湊／轉換參數重用穩定素材路徑。主要服務「同提示、同參考、只換 Seed」的反覆試片；改提示仍可能重算 conditioning。 | 重複試片時間、磁碟重複檔 |
| 6 | gpu_lock 包住素材準備、引擎運算、下載、合併、變速及 PNG 匯出。[鎖定](</E:/MINIMAX H3/H3Studio/app.py:1854>)、[收尾](</E:/MINIMAX H3/H3Studio/app.py:1923>) | 引擎完成後釋放 GPU 排程鎖，CPU 後處理改有併發上限的佇列；可依 RAM 壓力限制同時處理。 | 批次吞吐；不直接加速單片去噪 |
| 7 | 長片每段從頭解碼到目標位置，並以來源解析度重編碼。[解碼](</E:/MINIMAX H3/H3Studio/app.py:593>)、[編碼](</E:/MINIMAX H3/H3Studio/app.py:845>) | seek 至附近關鍵幀後精準裁切，或一次掃描產生所有片段；驗證 VFR、重疊幀、PTS 與音畫同步。 | 長片角色替換的 CPU／準備時間 |

參考影片的資料量可直接估算：15 秒×24fps×1920×1080×RGB×float32，單份張量約 8.34GiB；若先縮至 864×480，單份約 1.67GiB。這不是實測峰值，轉換、堆疊及模型還需要其他空間。程式目前先收集原圖格式資料，再堆疊及轉 float32，不能把中間所有副本都當作 float32 計算。

另一個問題是 `ref_image_size=match` 只管圖片。參考影片依來源大小適配至約 1344×768 的上限，沒有使用輸出像素上限；因此低解析輸出不一定同時降低影片參考成本。上述代理影片只針對影片參考路徑；近期 20 筆 Ref8 工作使用圖片參考，不能把這個問題當成它們全部慢速的原因。

較次要的改善包括串流下載與 Range 播放、進度寫檔節流、分頁隱藏時降低輪詢、關閉逐步 TAESD 預覽。PNG 目前已逐幀處理、低壓縮、ZIP 不重複壓縮且預設关闭；前端已有分頁與影片延後載入。這些不應被誤報為尚未實作。

## 近期第一手更新與適用性

| 日期／來源 | 已核對內容 | 判斷 |
| --- | --- | --- |
| 9/15，[H3 VAE 核心優化](https://github.com/Comfy-Org/ComfyUI/pull/16187)、[CUDA kernels 與測例](https://github.com/Comfy-Org/comfy-kitchen/pull/167) | 融合部分 norm、SiLU、padding 與線性運算，分塊解碼可按記憶體批次執行；INT8 解碼走相應路徑。 | 這台 0.37 已含核心更新；同事的舊核心未必有。作者 RTX5090、1344×768、362 幀測例中 INT8 decode 18.7→12.9 秒；這是 VAE 階段與指定硬體的數字，非整支影片倍速。 |
| [Comfy-Org INT8 VAE](https://huggingface.co/Comfy-Org/MiniMax-H3/blob/main/vae/minimax_h3_video_vae_int8_convrot.safetensors) | 官方 Comfy 格式檔案約 2.81GB；後續已下載、核對 SHA256 並與 5.21GB FP16 實測。 | 本機 5.167 秒片段、兩種解析度的連續 decode 約 2.67×速度，PSNR 55–56dB；不是完整生成流程的加速比。詳細方法與限制見 INT8 VAE 實測報告，保留 FP16 回退。 |
| 9/21，[ComfyUI 0.37.0](https://github.com/Comfy-Org/ComfyUI/releases/tag/v0.37.0) | Aimdo 0.5.5、逐模型 fast-disk 偵測、Qwen CUDA graph／W4A8 等更新。 | 這台已更新；同事只更新 Studio Git，不會自動更新被排除的 ComfyUI 核心與依賴。先查實際執行版本。 |
| 9/15 合入，[fast-disk 作者說明](https://github.com/Comfy-Org/ComfyUI/pull/16333) | 高速磁碟可減少部分 RAM buffer 準備，作者指定 PCIe4 NVMe 等級；新核心會逐模型辨識。 | 模型仍在 HDD 時不要強制加 `--fast-disk`。若同事也用 HDD，優先用現有 NVMe 空間做冷／暖載入比較，不能保證採樣成倍加速。 |
| 9/10，[H3-Optimizations 相容修正](https://github.com/Zironic/H3-Optimizations/pull/80) | 0.2.43／2c5b1a1 補上 optional attention override 相容性；本機仍 0.2.42。 | 值得納入相容性更新；不能宣稱版本升級就大幅加速。 |
| 9/12，[FirstBlockCache 作者文件](https://github.com/duckyshell/ComfyUI-MiniMaxH3-FirstBlockCache) | 有 memory compiler 相容修正與 experimental deep-reuse；作者約 30% 省時的基準是 RTX5090、20 步。 | 適合原生多步數候選測試；不能外推到 4 步 Turbo，也不要疊多套快取。 |
| 9/16，[Ref2VA-VSA 記憶體修正](https://github.com/Kablex/ComfyUI-Ref2VA-VSA/commit/99a02996b481da79f552308a097aff5a856825c8) | 提早釋放長參考路徑不再需要的張量。 | 專案仍列 24GB 等級需求，16GB 工作站先列實驗；不能用作者某個低峰值測例當通用保證。 |
| 近期 [SPEED V2](https://github.com/StanLukuvka/ComfyUI-MiniMax-H3-SPEED) | 初期低解析、後期恢復全解析，新增採樣器支援與 I2V 修正。 | 改變取樣結果，且作者明列非商業授權；不列為公司工作站預設升級。 |

現有 LightX2V FL 4-step v1.2 是聲音品質改善，Ref8 是品質比較選項；它們已在專案內，並非本次新增加速。Ref8 官方設定確為 video shift 12、audio shift 3，目前程式正確。[Ref8 作者設定](https://huggingface.co/lightx2v/Minimax-h3-Turbo/discussions/51)、[FL v1.2 作者說明](https://huggingface.co/lightx2v/Minimax-h3-Turbo/discussions/52)。

## 相容性與效能判讀

- H3Memory 的 embedding 提早釋放有來源結構檢查。現行核心 `_forward` 與 0.2.42 白名單不符；日誌已有回退原生生命週期的警告。只代表其中一個子功能未啟用，其餘 MLP／QKV 優化不應一概視為失效。[守門條件](</E:/MINIMAX H3/ComfyUI/custom_nodes/H3-Optimizations/h3_optimizations/memory/embedding.py:266>)。需適配與測試，不能只替換 hash；0.2.43 的 attention 修正也不能視為解決此問題。
- 啟動器雖有 `--lowvram`，但目前 DynamicVRAM 已啟用，核心明示此旗標在這種情況不生效；不能把移除它當作已確認加速。[核心參數](</E:/MINIMAX H3/ComfyUI/comfy/cli_args.py:170>)。
- SLA 與額外 H3Memory 目前被 Studio 禁止疊加，應保留既有相容性限制；省顯存建議適用一般 native／Turbo。[檢查](</E:/MINIMAX H3/H3Studio/domain.py:969>)。
- 「更快」與「生成時電腦更順」需要分別測量。保留額外 VRAM、限制 CPU 後處理併發、減少預覽可改善桌面餘裕，但也可能增加總耗時。避免一個設定同時宣稱最高速與最低負載。

## 建議落地與驗證順序

1. 同事各自記錄 GPU／VRAM、RAM、模型所在磁碟、實際核心與套件版本、解析度／時長／步數／參考數量。先試 5 秒、0.4MP、少量必要參考的預覽配方；一般模式明確開啟省顯存，並顯示其生效狀態。
2. 加上參考代理影片、實際設定與版本檢查、分階段計時。這一階段最直接對應 RAM／CPU 卡頓，也能避免誤把等待或解码視為採樣。
3. 固定素材、提示、Seed、尺寸，依序單獨比較省顯存、CK attention、INT8 VAE；分開測冷載入與暖生成。實際比較暖生成時需要更換 Seed 或其他採樣輸入，使 Comfy 真的重跑採樣；不能把全工作流快取命中當加速。各候選仍使用相同的 Seed 組合。
4. 記錄準備、載入、conditioning、去噪、VAE、回存／後處理時間，以及專屬／共享 GPU 記憶體、RAM／commit、磁碟活動與桌面可操作性。計算品質合格成片的耗時與重試次數，不能只比最快的一次。
5. 確認單片收益後再調整穩定素材快取、GPU 鎖範圍及長片 seek。最後才評估 sparse／cache／新蒸餾權重，分別檢查人物身份、動作、細節、聲音與嘴型。

目前可以確定有可改善的程式路徑與尚未使用的上游能力；各同事的實際收益，需用上述固定條件對照取得。歷史紀錄不足以承諾縮短幾成或幾倍。
