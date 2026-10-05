# 本地劇本模型選型與部署筆記

查核日期：2026-10-02（Asia/Taipei）。只採用模型開發者、Ollama、Google 的官方資料；模型權重可下載，不等於完整訓練資料與訓練流程開源。

## 結論

**後續更新：使用者要求改用適合成年角色戀愛、曖昧與成熟劇情的候選，已將預設切換為 Hermes 4 14B Q5_K_M。** 本機名稱 `h3-script-hermes4:14b-q5_k_m`，約 10.51 GB，Apache 2.0。模型作者列出創意寫作、JSON 格式遵循及較低拒答率；這些是選型依據，不能保證所有內容都不拒答，也不能直接推論繁體中文劇本品質勝過較大的模型。[Hermes 官方模型卡](https://huggingface.co/NousResearch/Hermes-4-14B)、[量化發布者](https://huggingface.co/bartowski/NousResearch_Hermes-4-14B-GGUF)

最初的品質優先候選為 **Qwen3.8-27B**，速度優先候選為 **Qwen3.5-9B**，並提供 **Gemma 4 12B** 供同題比較。已下載的 Qwen3.8-27B 保留供切換。Qwen3.8 27B 官方評測的指令遵循與一般推理優於上一代 27B，但尚未證實它是劇本寫作的全球第一名。[Qwen3.8-27B 官方模型卡](https://huggingface.co/Qwen/Qwen3.8-27B)

大型旗艦的智慧上限更高，但這台 16 GB 顯示記憶體、64 GB RAM 的電腦不適合部署完整旗艦。最新已公開權重的強模型包括 Kimi K3、Qwen3.8-2.4T-A95B、DeepSeek-V4.1-Flash、GLM-5.3。不同模型在不同測試領先；目前查到的官方資料沒有用相同條件評比繁體中文劇本品質，不能把程式或科學推理分數直接當成劇本排名。

## 本機只讀檢查

| 項目 | 結果 |
| --- | --- |
| 電腦 | Dell Pro Max Tower T2 FCT2250 |
| CPU | Intel Core Ultra 9 285，24 核心 / 24 執行緒 |
| RAM | 系統可見約 63.46 GiB，標稱 64 GB |
| GPU | NVIDIA GeForce RTX 5060 Ti，16311 MiB，標稱 16 GB |
| NVIDIA 驅動 | 591.86，nvidia-smi 顯示 CUDA 13.1 |
| GPU 當下負載 | 15533 MiB 已使用、100% 使用率，現有 Python 程序正在使用 GPU |
| E: 可用空間 | 約 1.06 TiB |
| C: 可用空間 | 約 495.8 GiB |
| D: 可用空間 | 約 2.29 TiB |
| Ollama / LM Studio | PATH、一般使用者安裝路徑、程序列表中均未找到 |
| 本地服務 | 11434 `/api/tags` 與 1234 `/v1/models` 在 4 秒內未回應，亦查無這兩個埠的 Listen 狀態 |

RAM 與磁碟由 Windows CIM/Get-Volume 取得，VRAM 以 nvidia-smi 為準（Win32_VideoController 的 AdapterRAM 對這張顯卡回報約 4 GB，不能採用）。只讀檢查不能排除安裝在其他路徑的可攜版本。本節記錄選型時的檢查，後續安裝與實測結果另見文末。

## 本機模型清單

下載量為 Ollama 查核時顯示的約值；VRAM 規劃含推理暫存的粗估，實際值取決於引擎、上下文長度、批量與其他 GPU 工作。256K 是模型能力，並不表示這台電腦適合配置 256K。

| 用途 | 已核實的 Ollama 名稱 | 下載量 | 這台電腦的部署方式 |
| --- | --- | --- | --- |
| 目前劇本預設 | `h3-script-hermes4:14b-q5_k_m`（本機建立的別名） | Hermes 4 14B Q5_K_M，10.51 GB | 原始來源 `hf.co/bartowski/NousResearch_Hermes-4-14B-GGUF:Q5_K_M`；16 GB VRAM 較有運算餘裕，實際 GPU 速度未測 |
| 品質優先 | `qwen3.8:27b` | 約 18 GB；llama.cpp 權重為 Q4_K_M | 超過可用 VRAM，需 CPU/RAM 部分卸載；64 GB RAM 可容納短上下文的量化權重，但速度待實測 |
| 速度優先 | `qwen3.5:9b` | 約 6.6 GB，Q4_K_M | 先規劃約 8–12 GB 可用 VRAM，8192 token 上下文起步，適合互動草稿與改寫 |
| 同題比較 | `gemma4:12b` | 約 7.7–8.0 GB，依引擎不同 | Google 官方以 16 GB VRAM / unified memory 為目標；在本機需讓出足夠顯示記憶體後實測 |

以上模型均採 Apache 2.0 授權。來源：[Qwen3.8:27b 名稱、大小與量化](https://ollama.com/library/qwen3.8:27b)、[Qwen3.5:9b 名稱、大小與量化](https://ollama.com/library/qwen3.5:9b)、[Gemma4:12b 名稱與大小](https://ollama.com/library/gemma4:12b)、[Google Gemma 4 12B 發布與硬體目標](https://blog.google/innovation-and-ai/technology/developers-tools/introducing-gemma-4-12b/)。

## 最新大型旗艦與限制

| 模型 | 官方確認規模與用途 | 本機判斷 |
| --- | --- | --- |
| Kimi K3 | 2.8T 總參數、104B 啟用，原生多模態與 1M 上下文；Kimi K3 License | 官方 GPQA Diamond 93.5，但這不是劇本分數；4-bit 純權重理論量級約 1.4 TB，遠超本機 RAM/VRAM。[官方模型卡](https://huggingface.co/moonshotai/Kimi-K3) |
| Qwen3.8-2.4T-A95B | 2.4T 總參數、95B 啟用；通用任務與長程代理 | 4-bit 純權重理論量級約 1.2 TB，不適合這台電腦。[官方模型卡](https://huggingface.co/Qwen/Qwen3.8-2.4T-A95B) |
| DeepSeek-V4.1-Flash | 552B 主幹，另有 196B Engram 記憶參數；1M 上下文，MIT | 每 token 只啟用 8B/16B 不代表只需 8B/16B 權重記憶體；總權重仍遠超本機。[官方模型卡](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash) |
| GLM-5.3 | 官方模型倉庫約 753B；提供 SGLang/vLLM 等部署方式，GLM-5.3 自訂授權 | 4-bit 純權重理論量級約 376 GB，遠超 64 GB RAM。[官方模型卡](https://huggingface.co/zai-org/GLM-5.3) |

理論量級以「參數數量 × 4 bit ÷ 8」計算，只用來說明硬體差距；實際下載、量化輔助資料、快取與執行記憶體需求會更多。旗艦排序的官方測試條件各異，沒有一致的單一「最聰明」冠軍。

## Windows 本地部署

Ollama 官方支援 Windows、NVIDIA GPU，本地 API 預設為 `http://localhost:11434`。使用最新版 Windows 安裝程式或最新版官方 Windows amd64 可攜套件；若嵌入工具，可用 `ollama serve` 啟動。模型儲存位置可用 `OLLAMA_MODELS` 指定。若改變已有服務的環境設定，需讓服務重新啟動後才會生效。[Ollama Windows 官方文件](https://docs.ollama.com/windows)

以下是新服務的設定範例，模型資料夾請由工具設定管理：

```powershell
$env:OLLAMA_MODELS = 'E:\MINIMAX H3\models\ollama'
$env:OLLAMA_HOST = '127.0.0.1:11434'
ollama serve
```

在另一個終端下載需要的模型：

```powershell
# 使用本專案安裝器建立已驗證的 Hermes 本機別名：
.\setup_scriptwriting.bat
# 可選的原有模型：
ollama pull qwen3.5:9b
ollama pull qwen3.8:27b
# 選用同題比較模型
ollama pull gemma4:12b
ollama list
```

本工具已接入 Ollama 原生 `/api/chat`，URL 用 `http://127.0.0.1:11434`，模型名稱不可使用 `:cloud`。LM Studio 是其他自行部署時可考慮的引擎，提供本地 OpenAI 相容 API，但本次工具未接入該介面。[LM Studio 官方相容 API 文件](https://lmstudio.ai/docs/developer/openai-compat) 下載權重需要網路，完成後本地推理可在離線環境執行。

Ollama 小於 24 GiB VRAM 時預設 4K 上下文。工具建議明確從 8192 token 起步，長劇本使用人物設定、劇情摘要與逐場生成，評估後再提高上下文。設定越長會占用越多記憶體；用 `ollama ps` 查上下文與 CPU/GPU 分配。[Ollama 上下文官方文件](https://docs.ollama.com/context-length)

劇本草稿可以使用 `think: false`；需要深入結構分析時再啟用思考。自訂模型要以 `/api/show` 確認支援的思考選項。程式只顯示 `message.content`，將 `message.thinking` 視為模型推理輸出，避免混入成品。[Ollama chat API 官方文件](https://docs.ollama.com/api/chat)

## 劇本品質驗收

模型安裝後，以同一份繁體中文 brief，要求固定角色、場次與時長，對 Qwen3.8-27B / Qwen3.5-9B / Gemma4-12B 各寫一版，再比較：

1. 角色動機與關係是否前後一致。
2. 每一場是否推動情節，伏筆與結局能否呼應。
3. 對白是否自然，有角色差異與潛台詞。
4. 是否遵守場次、秒數、製作限制與繁體中文格式。
5. 是否能依回饋改寫指定場景，保留其他既定情節。

實測應記錄模型名稱、量化、上下文、首字延遲與 tokens/s，分開評分故事品質與等待時間。更高的通用推理分數只能作初選，劇本成品與這台機器的實測才是最終依據。


## 已完成本機部署與實測

### Hermes 4 模型更換

2026-10-02 已下載 `NousResearch_Hermes-4-14B-Q5_K_M.gguf`，大小 10,514,570,176 bytes，SHA-256 `aebc57a6319d0486a5adb0c6891d51f383ea5c29281d764a87085b474a4af06b`。Ollama 檢查 blob 內容，安裝器再核對已選 GGUF 的雜湊與 Q5_K_M，建立 `h3-script-hermes4:14b-q5_k_m`。原 HF 模型及 Qwen 權重保留，別名共用同一份 GGUF。[實際 GGUF 檔案](https://huggingface.co/bartowski/NousResearch_Hermes-4-14B-GGUF/blob/main/NousResearch_Hermes-4-14B-Q5_K_M.gguf)

為一般編劇設定 plain ChatML 對話模板；Ollama 0.35.0 可以改選 GGUF 內附的 Hermes 官方 Jinja 模板。安裝器核對已保存 Go 模板，實際模板僅接受該 Go 或固定官方 Jinja 雜湊；使用 `think:false`。模板差異沒有修改模型權重或內容限制。[Hermes 官方模板](https://huggingface.co/NousResearch/Hermes-4-14B/raw/main/chat_template.jinja)

透過執行中的 Studio `/api/scripts/settings` 已切換並儲存新模型，狀態回報 ready/local_only。原有 8192 上下文、4096 最大輸出、0.8 溫度維持原值，不需停止 Studio 或正在執行的影片工作；在腳本頁按「重新檢查」即可讀取新選擇。

因 GPU 正在生成使用者影片，兩項模型相容性測試明確使用 **CPU（num_gpu=0、8 threads、4096 上下文）**，不是 GPU 速度測試。兩名成年角色的非露骨戀愛短片成功生成 588 字，303.53 秒，557 個輸出 token；JSON 分鏡成功生成，216.45 秒，424 個輸出 token。兩句指定台詞完整保留，均以 stop 結束，沒有輸出截斷或混入 `<think>` 標籤；JSON 通過分鏡驗證及實際匯入／提示詞編譯，總片長 15 秒、單一片段，配樂 `N/A`。測試完成後 `/api/ps` 為空。

測試稿仍需校稿：要求 250 字內實際輸出 588 字，焦段欄有時以拍攝角度代替 mm，兩人從門外／吧台到握手的位置連續性可補強。JSON 雖可匯入，但沒有依 brief 產生三段內部切鏡標記，動作欄使用中文，說話者標記仍需檢查；現有提示詞編譯器會補上對白語言標籤。此測試確認一般成年戀愛題材與工具相容性，不能保證繁體中文寫作品質、所有成熟題材都不拒答或正式 GPU 生成速度。紀錄在本機 `data/scriptwriting/verification/hermes-cpu-verification.json`，正式使用前仍需檢閱預覽。

### 初次 Qwen 部署紀錄

2026-10-02 已安裝官方可攜 Ollama `0.35.0`；執行檔套件固定大小及 SHA-256 校驗通過。下載 Qwen3.8-27B Q4_K_M，本地模型約 17.74 GB，Ollama manifest digest `aaee06c39dcf2437cde036998d960e1fc1494b8191be7cc9657d01e509097813`。服務綁定 `127.0.0.1:11434`、設定 `OLLAMA_NO_CLOUD=true`；程式與權重位於 `H3Studio/data/scriptwriting/`。

透過與正式功能相同的 API，以隔離測試專案實際生成 662 字中文短片稿。上下文 8192、最大輸出 4096、溫度 0.8、關閉模型思考輸出；首次載入與生成合計 162.5 秒，其中引擎日誌顯示模型初次載入約 93 秒。專案儲存 revision 2、Markdown 匯出與卸載均成功，後續 `/api/ps` 回報 0 個載入模型。這是單次功能驗證，不能當作所有長度的速度或劇本品質保證。

原始測試稿仍有少量簡體字、片長與日夜連戲需人工修訂，不能把「成功生成」等同正式可拍稿；編劇提示已補上片長對白預算與時間／光線自查要求，仍應在預覽後校稿。後續可使用「潤飾編輯稿」指定需修正的場次，確認後再套用。

紀錄與範例保存在本機 `data/scriptwriting/verification/real-model-verification.json`、`sample-screenplay.md`。介面以隔離模擬服務驗證建立／儲存、生成／套用及分鏡建立；初次部署時需要重新啟動 Studio 載入新 API，後續模型更換已透過執行中的 API 完成。
