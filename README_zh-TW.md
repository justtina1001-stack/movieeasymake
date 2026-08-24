# MiniMax H3 Studio

MiniMax H3 Studio 是 ComfyUI MiniMax H3 工作流的中文操作面板。專案只包含面板程式，不包含模型權重、個人素材、生成紀錄或影片。

## 同事第一次安裝

1. 安裝 Python 3.11 或 3.12。
2. 下載或 `git clone` 本專案。
3. 雙擊 `setup_h3_studio.bat` 安裝面板環境。
4. 雙擊 `start_h3_studio.bat`，開啟 <http://127.0.0.1:8787>。
5. 點右上角「引擎設定」，選擇本機或遠端模式。

### 直接複製完整資料夾到另一台電腦

Windows 的 `.venv` 會記住原電腦 Python 的絕對路徑，因此不能直接跨電腦使用。新版 `start_h3_studio.bat` 會實際執行環境健康檢查，不再只判斷 `python.exe` 是否存在：

1. 同事電腦先安裝 64 位元 Python 3.12（或 3.11），並啟用 `Add Python to PATH`。
2. 完整資料夾複製完成後直接執行 `start_h3_studio.bat`。
3. 若 H3 Studio 環境缺失或來自其他電腦，啟動器會自動建立同事自己的 `H3Studio/.venv` 並安裝面板依賴。
4. 面板開啟後若偵測到 `ComfyUI/.venv` 無法執行，在「引擎設定」的一鍵安裝區會顯示「修復這台電腦的引擎環境」。修復只重建 `.venv` 並安裝 CUDA PyTorch／ComfyUI 依賴，會保留現有 ComfyUI 程式、模型、LoRA、輸入素材和輸出影片。

若同事電腦完全沒有 Python，啟動器會停留在錯誤畫面並顯示安裝指引，不會再像雙擊後沒有反應。Git for Windows 與 NVIDIA 驅動仍是本機引擎安裝／生成所需；只連遠端 Gateway 的同事不需要本機 H3 模型或 NVIDIA GPU。

若電腦完全沒有 ComfyUI，選「使用這台電腦」，展開「這台電腦還沒有引擎？」即可執行一鍵安裝。安裝器會先檢查 NVIDIA GPU、Python、Git、記憶體及磁碟空間，並要求使用者自行閱讀和接受 MiniMax H3 授權。

## 工作站角色

H3 Studio 會在上方工具列清楚顯示目前角色：

- **管理主機**：顯示橘色標章及「共享引擎」，可以啟用 Gateway、建立／停用使用者與換發金鑰。
- **一般使用者**：顯示藍色標章，沒有共享引擎入口；只能使用本機引擎，或輸入管理者提供的 Gateway 網址與個人金鑰。

新 Git 安裝一律預設為一般使用者。你的 GPU 主機已在不進入 Git 的 `H3Studio/config.json` 設為管理主機，因此同事 clone 後不會取得管理角色或任何既有金鑰。管理 API 同樣會檢查角色，一般使用者即使自行呼叫也會得到 `403`。

若未來要把另一台電腦改成獨立管理主機，可在那台電腦執行 `set_h3_admin_mode.bat` 後重新啟動；要還原一般使用者則執行 `set_h3_user_mode.bat`。這只改變該電腦自己的角色，不會取得或影響你的管理主機權限。

## 兩種生成架構

### 使用同事電腦的模型

選「使用這台電腦」，網址維持 `http://127.0.0.1:8188`，並指定同事自己的 ComfyUI 資料夾。若勾選自動啟動，面板會尋找 ComfyUI 的 `.venv`、`venv` 或 portable `python_embeded` 環境後啟動引擎。

同事的 ComfyUI 必須自行安裝 MiniMax H3 節點與所需模型；模型檔不會從 Git 專案下載。

也可以使用面板內的一鍵安裝器，自動安裝經本專案驗證的 ComfyUI、CUDA 13.0 PyTorch、五個 H3 基礎模型及三個壓縮 Turbo LoRA。模型下載約 60.2 GiB，連同程式、環境與暫存，建議安裝磁碟至少保留 81 GiB。下載可重複執行，已完成的模型檔會略過。

### 使用你的電腦的模型

GPU 主機先啟動自己的 H3 Studio，在右上角開啟「共享引擎」：

1. 勾選「允許區域網路共用 GPU」，預設連接埠為 `8190`。
2. 為每位同事建立一個使用者，立即複製只顯示一次的個人金鑰。
3. 以系統管理員身分執行 `configure_h3_gateway_firewall.bat`，只在私人網路允許本機子網路連入 8190。
4. 把畫面列出的區網 Gateway 網址與該同事自己的金鑰交給本人。

同事端點「引擎設定」→「連線遠端電腦」，ComfyUI 網址填 GPU 主機的 Gateway 網址，例如 `http://192.168.1.20:8190`，再貼上個人金鑰。不要填 `127.0.0.1`，也不要填原始 ComfyUI 的 8188。

每位同事的 H3 Studio、素材、工作紀錄與回存影片都留在自己的電腦。Gateway 會另外驗證每一筆素材、工作歷史、輸出下載和取消操作的所有權，並在共用 ComfyUI 內以使用者 ID 分資料夾，因此不同使用者無法透過 H3 Studio 看到或操作彼此的工作。GPU 仍是共用的一張卡，工作會依 ComfyUI 佇列等待，不會同時吃滿顯存。

建議只在公司內網或 Tailscale／WireGuard 類 VPN 使用。原始 ComfyUI 必須維持 `127.0.0.1:8188`，不可用 `--listen 0.0.0.0`，也不可把 8188 或 Gateway 8190 直接轉發到公開網際網路。某位同事不再使用時，在「共享引擎」停用帳號；金鑰疑似外洩時按「換發金鑰」，舊金鑰會立即失效。

## 主要功能

- 文生影片、首尾圖片、多模態參考、角色替換、圖騰循環、續接影片、彈窗面板動畫與 MG 動畫
- 角色替換支援完整長片：超過 15 秒會智慧切段、加入 0.5 秒動作重疊、逐段替換並自動合併；預設重新掛回完整原聲，失敗後可從未完成片段接續。
- 共用比例、解析度、時長、隨機 Seed 與進階採樣設定
- 原生品質與 Turbo 快速預覽雙模式；自動配對 LoRA、步數、Euler、Sigma Shift 與參考圖精度
- 角色、背景、物件、圖片、影片與選填聲音的名稱代號
- 素材拖曳上傳、透明圖自動補螢光綠底、首尾圖擴邊適配
- 任務命名、生成歷史、折疊預覽、搜尋與每頁 20 筆分頁
- 遠端結果自動回存面板端，續接影片不依賴遠端檔案路徑
- 共用 GPU 安全 Gateway：個人金鑰、任務所有權、素材與輸出路徑隔離、帳號停用與金鑰換發
- 工具列內建 MiniMax H3 官方提示詞指南，可依目前模式查閱素材代號、鏡頭、對白、聲音及老虎機動畫寫法

## Turbo 快速預覽

「生成品質」可選擇原生品質或 Turbo 快速預覽。Turbo 會鎖定互相相容的設定，避免只改步數造成失敗：

- 文生、首尾、循環與續接：一般解析度使用 FL2VA 8-step；精確的 1344 × 768 使用 FL2VA 768p 4-step。
- 多模態、角色替換、彈窗面板與 MG：使用 Ref2VA 4-step，參考圖精度固定為 `match`。
- 原生品質不載入 LoRA，保留原本採樣設定，建議用於最後正式輸出。

本機一鍵安裝器會安裝 Kijai 轉換的低秩壓縮版本，以降低 16 GB 顯卡的額外負擔。遠端 ComfyUI 也必須在 `models/loras/` 具備對應的壓縮版或 LightX2V 完整版 LoRA，面板才會允許 Turbo 工作送出。

## MiniMax H3 官方提示詞 Skill

專案內附 `skills/write-minimax-h3-prompts/`，依 MiniMax H3 官方的 Base 與 Ref2VA 提示詞指南整理，可協助 Codex 撰寫文生影片、首尾幀、多模態參考、影片續接、角色替換，以及老虎機圖騰循環與彈窗面板提示詞。

安裝到 Codex 時，將整個 `write-minimax-h3-prompts` 資料夾複製到 `%USERPROFILE%\.codex\skills\`，然後重新啟動 Codex。使用時可直接說「用 MiniMax H3 官方提示詞 Skill 幫我寫……」，或明確輸入 `$write-minimax-h3-prompts`。

## 不會進入 Git 的資料

`.gitignore` 已排除 `ComfyUI/`、模型快取、`H3Studio/config.json`、`H3Studio/data/`、虛擬環境及生成簡報。請勿強制加入任何 `.safetensors` 或同事不應取得的素材。

## 模型更新中心

H3 Studio 啟動時會讀取受 Git 追蹤的 `H3Studio/model_manifest.json`，並比對目前本機 ComfyUI 的模型檔案。當同事 `git pull` 取得較新的版本清單後，若本機缺少新版本檔案，介面會顯示更新提示；模型不會因為 Git 更新而自動下載或被刪除。

- 「立即更新」才會開始下載，並在完成檔案大小驗證後啟用。
- 「明天再提醒」與「略過此版本」只記錄在該電腦的 `H3Studio/data/model_update_preferences.json`，不會提交至 Git。
- 下載取消或失敗時保留既有完整模型，之後可接續執行。
- 遠端引擎使用者只會看到由 GPU 主機管理者更新的說明，不能從自己的電腦修改共享主機模型。
- 一鍵安裝器與模型更新中心共用同一份版本清單，新安裝會直接取得目前清單指定的模型。

發布新模型時應新增或修改版本化檔名、更新版本號／日期／說明／來源 revision／精確檔案大小，測試工作流後再提交 `model_manifest.json`。不要把 `.safetensors` 加入 Git。

## 測試

```powershell
cd H3Studio
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

模型使用 MiniMax H3 Community License；模型與節點的散布、公開服務或商用條件，需另外依各自授權確認。
