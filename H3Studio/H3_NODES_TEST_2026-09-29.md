# H3 影片加速節點實測（2026-09-29）

H3-Optimizations 0.2.45 在本機通過 11 次生成與輸出一致性檢查，但**沒有足夠證據顯示升級後會穩定加速或降低記憶體用量**。新版確實讓目前 ComfyUI 的 embedding 提前釋放功能生效。建議視為相容性修正候選；這次先完成測試，未更新正式節點或推送升級。

**測試範圍與環境**

| 項目 | 固定條件 |
| --- | --- |
| 舊版 | H3-Optimizations 0.2.42，`52b5f1e2345c2d06b9c7457131dbada2872ae7d2` |
| 新版 | H3-Optimizations 0.2.45，`862774944a331bc1a66cee1cf805b94bd138ebbe` |
| 電腦 | RTX 5060 Ti 16 GB、64 GB RAM、Windows、模型放在 E 槽 HDD |
| 執行環境 | ComfyUI 0.37.0／`73c9bad4d21e7addbe1d13bc92eee0f1431b017d`；Torch 2.13.0+cu130；comfy-kitchen 0.2.35；aimdo 0.5.5 |
| 模型 | Ref2VA／FL2VA pruned INT8 ConvRot、Qwen3VL 32B NVFP4 AWQ、2,811,065,184 bytes INT8 video VAE、FP32 audio VAE |
| 影片 | 864 × 480，124 frames，24 fps，約 5.17 秒，同一張參考圖與提示詞 |
| Ref2VA | Studio 穩定 Turbo、4 steps，固定 seeds 290901／290902／290903 |
| FL2VA | Studio 穩定 Turbo、8 steps，固定 seed 290901，使用起始圖 |
| 節點設定 | H3MemoryOptimization 開啟，Studio Auto 預設，4096-row chunks、preserve_precision=True |
| 啟動參數 | lowvram、reserve-vram 1.5、TAESD preview，獨立 base directory 與記憶體內 SQLite |

兩版使用完全相同的生成 graph；額外輸出全部 PNG 供品質分析。沒有更換模型、Torch、Kitchen、attention kernel，也沒有測試 0.2.45 的 FastH3VSA 或 sparse attention。這次結論只適用於原有 H3MemoryOptimization 工作流的版本更新。

**速度與資源結果**

測試順序為 A 舊版 4 次 → B 新版 4 次 → A 舊版 Ref2VA 補測 3 次，共 11 次成功。各組第一支包含模型載入，後兩支為模型已載入的暖執行。採樣、影片 VAE、音訊 VAE 均確認實際執行，未拿快取結果代替生成。暖執行允許共用相同的文字／圖片 conditioning。

下表以 B 與 A 補測的兩次暖執行比較；兩個測試服務都存在，但只有其中一個執行 GPU 工作，另一個已卸載模型。這減少了初次 A 少一個閒置程序的差異，仍不是隨機排序、全新程序或獨占主機的嚴格效能實驗。

| 指標：2 次暖執行中位數 | 舊版 0.2.42 補測 | 新版 0.2.45 | 判讀 |
| --- | ---: | ---: | --- |
| 採樣時間 | 76.45 秒 | 75.02 秒 | 少 1.42 秒／1.86%，不足以證明穩定改善 |
| 採樣時間範圍 | 70.14–82.75 秒 | 71.80–78.25 秒 | 同 seed 比較，一次變慢、一次變快 |
| 引擎時間扣除診斷 PNG 存檔時間 | 89.88 秒 | 88.63 秒 | 估計差 1.39%；不是另測的正常 Studio 完整延遲 |
| 採樣階段整張 GPU 用量峰值 | 15.36 GiB | 15.32 GiB | 約相同；包含桌面與其他程序 |
| 採樣階段程序 RAM 駐留量峰值（RSS） | 23.64 GiB | 27.18 GiB | 沒有重現 RAM 節省 |
| 採樣階段程序 private commit 峰值 | 38.13 GiB | 40.71 GiB | 不是實體 RAM 用量；同樣未見下降 |

記憶體每 0.5 秒取樣；表格為「各次採樣節點的取樣峰值，再取兩次中位數」，不是整個 graph 的最高值。最初 A/B 看似可省約 5–6 GiB 的 RAM，A 補測後方向反轉，**因此撤回把該初步數字視為升級效益的判讀**。程序歷史、allocator、OS 記憶體壓力仍會影響結果；也不能憑此斷言新版必然比較耗 RAM。

FL2VA 單次相容性測試的採樣時間為舊版 246.66 秒、新版 246.88 秒，幾乎相同。其完整 graph 分別 289.82／315.25 秒，包含模型切換與 conditioning 差異；樣本不足，不能當作穩定速度回歸或提升。首支 Ref2VA 的載入時間也受到磁碟快取與程序狀態影響，沒有用來宣稱冷啟動加速。

**功能與品質驗證**

舊版在目前 ComfyUI 的 `_forward` 上使用 `Embedding memory: stock lifetime`；新版日誌實際出現 `released dead embedding tensors before block 0`，節點回報 `Embedding memory: released before block 0`。兩版實際使用相同的 Kitchen INT8 prequantized attention、streamed dense BF16 QKV、ConvRot INT8 two-slice MLP，因此新版沒有帶來另一條更快的 attention 路徑。

這符合上游的 [0.2.44 相容性修正](https://github.com/Zironic/H3-Optimizations/commit/64979f6cab34952424eb4ccfceca7839fee04f5f)；本次固定測試 [0.2.45 commit](https://github.com/Zironic/H3-Optimizations/commit/862774944a331bc1a66cee1cf805b94bd138ebbe)，未追隨後續浮動版本。

- 舊版初次 A 與新版 B：4 組 × 124 frames，共 496 組配對的原尺寸 8-bit RGB 影格，像素與 PNG 檔案內容全部一致；MAE = 0、SSIM = 1。
- 4 組 MP4 解碼後的音訊樣本完全一致：32 kHz、雙聲道，每聲道 165,888 samples（含編碼 padding）。
- 舊版補測的 372 個 RGB 影格也逐一確認與舊版初次輸出完全一致。
- Ref2VA 與 FL2VA 的首／中／尾影格已目視檢查；沒有發現版本間畫面差異。
- 全部生成成功，沒有 OOM、生成例外或正式工作插入；正式 ComfyUI 與 Studio 工作佇列持續由測試器監看。

以上品質結果限定於已輸出的 8-bit 影格及解碼音訊，不能證明模型內部浮點張量完全相同，也不是絕對畫質評分。測試只涵蓋這台 GPU、5 秒 480p、單張參考圖；尚未涵蓋同事的不同硬體、長片、768p、多參考圖或 FastH3VSA。

**保留的證據與重現方式**

本機完整資料位於 [測試設定](<E:/MINIMAX H3/H3Studio/data/diagnostics/h3_nodes_20260929/setup.json>) 同一目錄，模型與影片資料未加入 Git。

- [主要效能摘要](<E:/MINIMAX H3/H3Studio/data/diagnostics/h3_nodes_20260929/confirmed_performance_summary.json>)：補測 A 與 B，採樣節點量測。
- [初次 A/B 摘要](<E:/MINIMAX H3/H3Studio/data/diagnostics/h3_nodes_20260929/performance_summary.json>)：保留初步結果；其中資源峰值是整個 graph，勿與主表混用。
- [音訊比對](<E:/MINIMAX H3/H3Studio/data/diagnostics/h3_nodes_20260929/audio_comparison.json>)、[最終驗證及清理狀態](<E:/MINIMAX H3/H3Studio/data/diagnostics/h3_nodes_20260929/final_verification.json>)。
- [Ref2VA 比較影片](<E:/MINIMAX H3/H3Studio/data/diagnostics/h3_nodes_20260929/analysis/r2v_trial0_seed290901/comparison_side_by_side.mp4>)、[FL2VA 比較影片](<E:/MINIMAX H3/H3Studio/data/diagnostics/h3_nodes_20260929/analysis/fl2va_trial0_seed290901/comparison_side_by_side.mp4>)；預覽影片無音訊，品質指標使用原始 PNG。
- [API 測試器](<E:/MINIMAX H3/H3Studio/tests/benchmark_h3_nodes_api.py>)、[CPU 畫質分析器](<E:/MINIMAX H3/H3Studio/tests/analyze_h3_nodes_benchmark.py>)，兩者的自我檢查已通過。

重新分析既有結果（在專案根目錄執行，不會重新生成影片）：

```powershell
.\ComfyUI\.venv\Scripts\python.exe H3Studio\tests\analyze_h3_nodes_benchmark.py --directory h3_nodes_20260929
.\ComfyUI\.venv\Scripts\python.exe H3Studio\data\diagnostics\h3_nodes_20260929\analyze_audio.py
.\ComfyUI\.venv\Scripts\python.exe H3Studio\data\diagnostics\h3_nodes_20260929\summarize_confirmed.py
```

**正式環境與測試清理**

正式 H3-Optimizations 仍是 0.2.42，ComfyUI 與節點 Git 工作目錄乾淨；已核對的模型檔大小、修改時間未變。測試開始前在正式服務閒置時卸載過模型快取，沒有修改正式設定或重新啟動正式服務。

預檢第一次啟動曾觸發 ComfyUI 的舊資料庫搬移嘗試，但檔案鎖使搬移失敗，未搬走正式資料庫；有效量測改用明確的記憶體內 SQLite。舊版有效日誌為 `baseline/server2.stderr.log`。

自動核准審查拒絕了「結束已完成的舊版測試程序並啟動新版」的複合操作，只回傳 `blocked by policy`，沒有更詳細原因。未改用其他方式重試結束程序，而是改用不同測試連接埠。測試結束後 8199／8200 皆已呼叫 `/free` 卸載模型與釋放快取，佇列清空；兩個測試服務仍保持閒置，合計仍有約 4.7–4.8 GiB 程序駐留 RAM。GPU 整體用量已回落到約 2.7 GiB，並非零占用。
