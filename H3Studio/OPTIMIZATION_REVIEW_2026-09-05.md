# H3 Studio 優化與模型評估（2026-09-05）

本次為本機程式、已安裝檔案與發布者資料的交叉檢查；未下載新權重，也未執行畫質或速度對照生成。

## 最值得優先改善的地方

| 優先順序 | 本機證據 | 建議與預期用途 |
| --- | --- | --- |
| 1 | domain.py 只在 sparse_experimental 分支插入 H3MemoryOptimization；一般 Ref2VA、角色替換及原生模式均未使用 | 將省顯存與稀疏加速分開。先測省顯存單獨啟用，再決定稀疏比例，尤其針對已發生的長片 INT8 暫存張量 OOM。 |
| 2 | app.py 替換分段門檻固定 15 秒，前端高負載警告只在 replace 模式 | 按 GPU、輸出像素、幀數與參考影片負載決定片段上限；分段重疊也必須算入。失敗時提供保留已完成片段後縮短重試。 |
| 3 | Ref2VA Turbo 固定 4 steps、Euler、simple、Shift 12/3、match | 建立原生／現行 Turbo／候選 8 步的同 Seed 對照，記錄首幀替換率、身份穩定、動作時序和耗時。步數較少不保證整體最快，也不能直接證明先前替換延遲由 Turbo 造成。 |
| 4 | 原生影片、首尾與多模態提示詞組裝路徑不完全一致 | 統一任務意圖、原片保留範圍、角色外觀來源與時間區間；前後段分別檢查。文字規則不能保證像素鎖定，精確背景／分數／UI 應考慮後期合成。 |
| 5 | 工作配方已有參數紀錄，但模型檔案會被更新 | 下一步保存模型與 LoRA 的版本／雜湊、峰值顯存及耗時，避免相同 Seed 卻無法重現。短片批次的瀏覽器端排程也值得移到後端，減少關閉視窗造成的中斷。 |

H3-Optimizations 作者目前建議先使用 Memory Optimization 預設值，Sparse Attention 則是另一個有畫質取捨的選項；其速度表有指定硬體與測試排除項，不能當作這台 RTX 5060 Ti 的完整生成保證。已安裝節點停在 2026-08-31 的 `83149e0`，可評估新版，但應先檢查節點輸入欄位與舊工作流相容性。[發布者文件](https://github.com/Zironic/H3-Optimizations)

## 權重與 LoRA 候選

| 候選 | 對目前工具的意義 | 判斷 |
| --- | --- | --- |
| LightX2V FL2VA 4-step v1.1 768p／8-step v1.0 768p | 目前已安裝，domain.py 也已有品質選项 | 不是本次新發現的缺件；先把已安裝版本用在相同場景對照。 |
| Alibaba-PAI MiniMax-H3-Acc-LoRAs | 同時提供 FL2VA、Ref2VA 的 8-step PDD 加速權重 | 最值得試驗的新增加速候選，尤其 Ref2VA。但發布者範例依賴 apply_pdd_lora 與 Diffusers ModularPipeline，不能只替換現有 Turbo 檔名就宣稱正確。 |
| Jojocodex Spatial & Physics | 碰撞、掉落、堆疊與物件反饋 | 適合老虎機道具／金幣互動的低強度試驗。作者說仍不穩定，新版說明建議 0.3～0.5；舊使用段落仍寫 0.8～1.0，應按檔案版本確認。 |
| Jojocodex Wushu Action v7（FL2VA／Ref2VA） | 武術與打擊動作方向 | 可以列為角色戰鬥／概念表演候選，尚未在本機驗證；不宜作為所有圖騰與面板動畫的共用預設。 |
| MATLOWAI H3 Motion Adapter | 快速動作局部重生成／時間展開 | 作者明說依赖 MAINodes 的 de-rope 流程；不是通用「載入就更自然」LoRA，不適合直接放進普通生成做預設。 |
| H3-World | 按鍵控制角色與攝影機 | 需 directed-attention patch，屬另一種工作流，暫不符合目前影片面板的優先需求。 |

來源：[LightX2V](https://huggingface.co/lightx2v/Minimax-h3-Turbo)、[Alibaba-PAI](https://huggingface.co/alibaba-pai/MiniMax-H3-Acc-LoRAs)、[空間物理](https://huggingface.co/Jojocodex/minimax-h3-spatial-physics-lora)、[武術動作](https://huggingface.co/Jojocodex/wushu-action-v7-minimax-h3-fl2va-ref2va-lora)、[Motion Adapter](https://huggingface.co/MATLOWAI/MiniMax-H3-Motion-Adapter)、[H3-World](https://huggingface.co/DANNY621/H3-World)。

目前查到的 MiniMax 官方開放模型文件仍以 H3-Base-FL2VA／Ref2VA 為主要本地權重，不能把官網產品名稱直接當成可替換的 ComfyUI 權重版本。官方另描述 Context-IR 的指令整理與 2K 再生成流程；使用者選擇不接 API 時，規則式指令編譯仍有優化價值。[MiniMax 官方專案](https://github.com/MiniMax-AI/MiniMax-H3)

## 本次實作

已加入快速生成與短片專案的自訂 LoRA 面板：讀取當前引擎清單、最多四個、強度、停用／移除、家族篩選與配方保存；Windows 子資料夾路徑會按引擎原始檔名送出，避免反斜線差異造成節點驗證失敗。未選自訂 LoRA 時沿用原有工作流。

這台電腦的放置位置為 `E:\MINIMAX H3\ComfyUI\models\loras\h3studio_custom`。資料夾已建立，目前沒有自訂權重，所以掃描回傳 0 個是正常結果。詳細操作見 README「自訂 LoRA」。

測試：47 項 LoRA／domain／shortfilm 測試及 12 項工作紀錄／分段回歸測試通過；JS 語法與 Python 編譯通過；本機面板與清單 API 已驗證。未測新權重實際出片，也未推送 Git。
