# MiniMax H3 INT8 VAE 實測紀錄（2026-09-21）

**實測完成：這台 RTX 5060 Ti 16GB 上，INT8 VAE 的連續解碼時間減少約 63%，約為 FP16 的 2.67 倍速度；解碼期間整卡最高取樣占用減少約 2.30–2.35 GiB。** 兩種解析度的 PSNR 為 55.39／55.84 dB、SSIM 約 0.9995，抽查畫面未見明顯劣化。20 個工作流全部成功，16 次 decode 均實際執行、未命中解碼快取。

收益集中在 VAE 解碼，不能把約 63% 套用到整支影片生成時間；本次沒有執行完整生成流程的 A/B 測速。測試當時正式工作流仍指定 FP16，測試完已釋放閒置模型與快取；既有使用者影片正常完成後才開始測試。2026-09-22 已依此結果加入「自動／INT8／FP16」選項，自動優先選擇相容引擎上的 INT8，保留 FP16 回退，更新方式見 [README](README.md)。

## 模型與環境

| 模型 | 本機檔案 | 實際位元組 | 十進位 GB／GiB |
| --- | --- | ---: | ---: |
| FP16 基準 | `minimax_h3_video_vae_fp16.safetensors` | 5,207,808,496 | 5.208 GB／4.850 GiB |
| INT8 ConvRot 候選 | `minimax_h3_video_vae_int8_convrot.safetensors` | 2,811,065,184 | 2.811 GB／2.618 GiB |

INT8 檔案大小較基準減少 **46.02%**；此數字只代表磁碟檔案大小，不能當成解碼時間、總顯存或整支影片的節省比例。

INT8 來源為 [Comfy-Org/MiniMax-H3 官方檔案](https://huggingface.co/Comfy-Org/MiniMax-H3/blob/7a2065e37f5ff9d3c4e605f164d4cac388eff8e8/vae/minimax_h3_video_vae_int8_convrot.safetensors)，固定 revision `7a2065e37f5ff9d3c4e605f164d4cac388eff8e8`。本機 SHA-256 已核對：

```text
52a2c8c73583c86e4f41cdcce3a6ad0ea562987bc0bf3d60a0cef5f5c8e60c0e
```

FP16 使用既有本機檔案，未另外計算完整 SHA-256。INT8 檔案不是每個 tensor 都存成 INT8：decoder 包含 144 個 I8 tensor、144 個 U8 量化 metadata tensor，及 F32 tensor；encoder 116 個 tensor 中為 83 個 F32、33 個 F16。對照 encoder／quant conv／post-quant conv 共 120 個 tensor，在轉成推論 FP16 後全部數值相同。兩種解析度實際 encode 產生的 latent 也逐值完全相同，主要差異確實落在 decoder。

| 項目 | 已確認設定 |
| --- | --- |
| 作業環境 | Windows；同一個既有 ComfyUI 服務與 GPU |
| GPU | NVIDIA RTX 5060 Ti；16GB 級，驅動回報 16,311 MiB |
| RAM | 64GB 級；系統回報 66,538,236 KiB |
| 驅動 | 591.86 |
| 模型所在磁碟 | E:，WDC WD4005FZBX-00K5WB0 SATA HDD |
| ComfyUI／PyTorch | 0.37.0，commit `73c9bad4`／2.13.0+cu130 |
| comfy-kitchen／comfy-aimdo | 0.2.35／0.5.5；DynamicVRAM 已啟用 |
| 注意力 | 服務原有設定為 PyTorch attention；核心讓 H3 INT8 decoder 依可用性選擇 CK INT8 attention。本次未插樁追蹤個別 attention kernel；日誌已確認量化 metadata 被識別、INT8 VAE 成功執行 |

硬體及版本來自本機檢查；測試啟動後另保留 `/system_stats`。沒有修改正式工作流、VAE 預設、啟動參數或既有工作。

## 對照方法

素材為一段已完成的神社／森林鏡頭，含葉片、牌子、角色走入及後段臉部畫面。準備兩份固定輸入，皆為 124 幀、24fps，播放長度約 5.167 秒（末幀時間戳 5.125 秒）。

| 解析度 | 輸入檔案位元組 | 輸入 SHA-256 | 預期 latent 形狀 |
| --- | ---: | --- | --- |
| 864×480 | 44,244,468 | `b8390df79cd420c921bde2949781522cc1ec30e4b7a48ede55f5cd3e6907b59b` | `[1,24,37,30,54]` |
| 1344×768 | 84,239,586 | `37f753e66428f4f45db23e1b84c7a6ea9d507504f196ab4f9f097eadf9152c07` | `[1,24,37,48,84]` |

1. 等現有佇列空閒後，才請引擎釋放閒置模型及快取；沿用現有服務，不另開 GPU 推論程序。
2. 每種解析度分別以 FP16 與 INT8 VAE 執行一次 encode，保存 latent。兩組 decoder 一律使用 **FP16 encode 的同一份 latent**；每次複製檔案後核對 SHA-256。
3. 每顆 VAE 每種解析度執行四次 decode：第 0 次為清除引擎快取後的首次 decode，第 1–3 次用於暖執行中位數與範圍。每次採不同 latent 檔名，使 Comfy 重新執行節點，並檢查 `execution_cached` 與節點計時。
4. VAE 保持核心預設的內部分塊；不另外套用 attention、編譯、快取或 tiling 變更。測試中斷後不能混用先前部分結果；控制器要求新測試目錄重跑。
5. 第 0 次 decode 保存全部 124 幀 RGB PNG 供畫質比較。後續影片輸出另計，不列入 decode 節點時間。

時間由 WebSocket 節點起訖事件計算，屬節點 wall time，含節點內的記憶體搬移及少量排程／事件延遲，並非純 CUDA kernel 時間。**VAELoader 是獨立節點**：首次 decode 的時間可含首次 GPU 搬移及 kernel 準備，不能稱為完整模型的 HDD 載入時間。引擎快取釋放也不會清除 Windows 檔案快取，因此本次不宣稱量到真正冷磁碟載入。

每 0.5 秒記錄整張 GPU 的 NVML 記憶體占用、Comfy 程序 RSS、系統可用 RAM及可取得的 GPU 功耗。它們是取樣值，可能漏掉瞬間尖峰；NVML 整卡占用含桌面與其他程序，不等於 VAE 專屬顯存，程序 RSS也不等於完整 commit 或換頁負擔。

## 實測結果

下表來自實際成功且未命中 decode 快取的 run；暖執行代表連續使用，報三次中位數及最小–最大範圍。encode 僅一次，屬初步檢查，不作穩定效能排名。Comfy 歷史確認 20 個測試之間沒有其他已完成工作插入，計時也沒有使用 history 輪詢 fallback。

| 解析度／VAE | encode 測試的 VAELoader 秒數 | VAEEncode 秒數（1 次） | decode 第 0 次 VAELoader 秒數 | VAEDecode 第 0 次秒數 | 暖 decode 中位數／範圍（3 次） |
| --- | --- | --- | --- | --- | --- |
| 864×480 FP16 | 0.131 | 29.561 | 0.126 | 49.469 | **24.824**／24.751–24.848 |
| 864×480 INT8 | 0.288 | 28.978 | 0.147 | 23.177 | **9.270**／9.258–9.270 |
| 1344×768 FP16 | 0.123 | 50.737 | 0.117 | 46.386 | **46.455**／46.439–46.473 |
| 1344×768 INT8 | 0.133 | 50.858 | 0.136 | 17.521 | **17.389**／17.383–17.466 |

| 解析度 | 暖 decode 時間減少比例 | FP16／INT8 decode 期間整卡占用最高取樣 MiB | FP16／INT8 decode 期間 Comfy RSS 最高取樣 MiB | 失敗／異常 |
| --- | --- | --- | --- | --- |
| 864×480 | **62.66%**（2.68×速度） | 8,292／5,886 | 6,790／4,663 | 0／未見執行異常 |
| 1344×768 | **62.57%**（2.67×速度） | 8,429／6,076 | 7,744／5,468 | 0／未見執行異常 |

記憶體取首次及三次連續 decode 期間的最高取樣，不含存圖／影片節點。以 1344×768 為例，整卡占用約 **8.23→5.93 GiB**，Comfy RSS 約 **7.56→5.34 GiB**。這不代表採樣階段或整個生成流程的峰值也同幅下降。VAELoader 的短時間也不能解讀為讀完整個模型檔案所需時間：動態載入會把實際權重搬移延後到使用時。

時間減少比例＝`1 − INT8 暖 decode 中位數 / FP16 暖 decode 中位數`；只表示 VAE 解碼階段。不能將它套用到採樣、文字編碼或整支影片，也不能拿先前 68 分鐘歷史中位數直接相乘推估總工時。

| 解析度 | 全幀 RGB PSNR dB | 全幀 RGB MAE [0,1] | 全幀 P99 RGB 絕對差 [0,1] | 平均逐幀 luma SSIM | 相鄰幀 luma 變化差 MAE | 人工比較 |
| --- | --- | --- | --- | --- | --- | --- |
| 864×480 | **55.388** | 0.0007069 | 0.0039216（1/255） | **0.999594** | 0.0010065 | 抽查首／中／末／最大差異幀，未見明顯劣化 |
| 1344×768 | **55.838** | 0.0006279 | 0.0039216（1/255） | **0.999536** | 0.0009244 | 同上，另檢查原尺寸人物末幀，未見明顯劣化 |

畫質指標以輸出的 **8-bit PNG** 計算，正規化至 [0,1]；未取得原始 float32 畫面，不能可靠評估小於 1/255 的差異。PSNR／MAE 相對 FP16，不代表有真實原圖參考的品質分數。P99 使用 nearest-rank；SSIM 為 luma、11×11 Gaussian／sigma 1.5、有效視窗中心及 population covariance，並非 RGB SSIM。時域數字是相鄰幀的 luma 變化代理，未作 optical flow 對齊，不能直接稱為閃爍評分。

比較圖包含首幀、中間幀、末幀及 MAE 最大幀，每列 FP16／INT8／放大差異；預覽 MP4 為有損編碼，只供觀看，所有指標都直接使用 PNG。抽查葉片、牌子文字、臉部、髮絲、衣物與色彩，沒有看到明顯新增模糊、偏色或塊狀接縫；兩組最大平均差異都出現在第 1 幀。人工檢查是代表幀比較，未據此宣稱完整動態觀看無閃爍；全片另有上述時域差異代理數值。

兩種解析度的 encoder latent 均為 float32、全有限值，實際形狀與表列預期一致；FP16／INT8 encode 分別產生的所有 latent 數值逐值相等。16 次 decoder 使用的檔案均核對為該解析度同一份 FP16 latent，四組畫質輸出各有完整 124 幀。

**建議：值得納入 H3 Studio 的 VAE 優化選項，保留 FP16 回退。** 本次速度、資源與畫質對照都支持在這台機器上試用 INT8；encoder 沒有明顯加速，長時間的主模型採樣仍需另行優化。接入產品時需讓工作流與模型清單實際選用 INT8，單純下載檔案不會改變現有生成結果。

本次為已完成影片的 5.167 秒片段編解碼對照，不是重新生成整支影片；未量測 15 秒等更長輸出或完整流程峰值。兩組固定依 FP16→INT8 順序，未交錯隨機測試，連續執行各只有三次。本機 Comfy 歷史沒有外來工作插入，但不能排除其他桌面 GPU 活動。結果只涵蓋此 GPU、版本、設定與單段含人物素材，不能直接推及同事各自的硬體、其他膚色／場景／動作或全部模型模式。建議同事保留支援此量化格式的相容引擎，先做小範圍對照再更改預設。

## 可重現資料

以下 `data/diagnostics/`、模型及影片為本機測試資料，未納入 Git；絕對路徑連結供原測試機查閱。同事可使用 [API 測試控制器](tests/benchmark_h3_vae_api.py) 與 [CPU 畫質分析器](tests/analyze_vae_benchmark.py) 重做對照，需自行準備本機素材與測試輸入清單。

- [下載與 checksum 紀錄](</E:/MINIMAX H3/H3Studio/data/diagnostics/vae_int8_20260921/download.json>)、[權重結構](</E:/MINIMAX H3/H3Studio/data/diagnostics/vae_int8_20260921/weight_structure.json>)、[非 decoder FP16 值比較](</E:/MINIMAX H3/H3Studio/data/diagnostics/vae_int8_20260921/encoder_weight_comparison.json>)。
- [輸入與雜湊](</E:/MINIMAX H3/H3Studio/data/diagnostics/vae_int8_20260921/inputs.json>)、[執行記錄](</E:/MINIMAX H3/H3Studio/data/diagnostics/vae_int8_20260921/runner.log>)。
- [API 測試控制器](</E:/MINIMAX H3/H3Studio/tests/benchmark_h3_vae_api.py>)、[CPU 畫質分析器](</E:/MINIMAX H3/H3Studio/tests/analyze_vae_benchmark.py>)；分析器的 3 幀小尺寸合成資料自測已通過，涵蓋數值、比較圖與 MP4 解碼幀數。
- [原始測試紀錄](</E:/MINIMAX H3/H3Studio/data/diagnostics/vae_int8_20260921/benchmark.json>)、[獨立彙總與佇列檢查](</E:/MINIMAX H3/H3Studio/data/diagnostics/vae_int8_20260921/benchmark_summary.json>)、[latent／快取／輸入驗證](</E:/MINIMAX H3/H3Studio/data/diagnostics/vae_int8_20260921/latent_validation.json>)。
- 864×480：[完整畫質數據](</E:/MINIMAX H3/H3Studio/data/diagnostics/vae_int8_20260921/analysis_864x480/metrics.json>)、[比較圖](</E:/MINIMAX H3/H3Studio/data/diagnostics/vae_int8_20260921/analysis_864x480/comparison_contact_sheet.png>)、[FP16 預覽](</E:/MINIMAX H3/H3Studio/data/diagnostics/vae_int8_20260921/analysis_864x480/fp16_preview.mp4>)、[INT8 預覽](</E:/MINIMAX H3/H3Studio/data/diagnostics/vae_int8_20260921/analysis_864x480/int8_preview.mp4>)。
- 1344×768：[完整畫質數據](</E:/MINIMAX H3/H3Studio/data/diagnostics/vae_int8_20260921/analysis_1344x768/metrics.json>)、[比較圖](</E:/MINIMAX H3/H3Studio/data/diagnostics/vae_int8_20260921/analysis_1344x768/comparison_contact_sheet.png>)、[FP16 預覽](</E:/MINIMAX H3/H3Studio/data/diagnostics/vae_int8_20260921/analysis_1344x768/fp16_preview.mp4>)、[INT8 預覽](</E:/MINIMAX H3/H3Studio/data/diagnostics/vae_int8_20260921/analysis_1344x768/int8_preview.mp4>)。
