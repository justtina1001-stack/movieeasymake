# MiniMax H3 Studio

MiniMax H3 Studio 是 ComfyUI 的簡化操作介面，可使用本機 ComfyUI，也可連線可信任網路中的遠端 ComfyUI。遠端生成完成後，影片會回存到面板電腦。

## 啟動

第一次使用先執行專案根目錄的 `setup_h3_studio.bat`，再雙擊 `start_h3_studio.bat`。瀏覽器會開啟 <http://127.0.0.1:8787>。右上角「引擎設定」可切換本機與遠端模式。

本機沒有 ComfyUI 時，可在「引擎設定」展開一鍵安裝器。安裝前會檢查 NVIDIA GPU、Git、Python、記憶體與磁碟空間，並要求使用者閱讀 MiniMax H3 Community License。基礎模型與壓縮 Turbo LoRA 約 60.2 GiB，建議保留至少 81 GiB。

新安裝預設是「一般使用者」工作站，不顯示共享金鑰管理。GPU 主機可執行根目錄的 `set_h3_admin_mode.bat` 切換成「管理主機」；角色只保存在 Git 忽略的本機 `config.json`，不會隨專案分享。

## 功能

- 文生影片、首尾圖片、多模態參考、角色替換、圖騰循環、續接影片、彈窗面板動畫與 MG 動畫
- 長片角色替換：自動探測來源影片，超過 15 秒時以轉場／低動態畫面智慧分段，保留前後 0.5 秒動作上下文，逐段生成後裁切合併並可保留完整原聲。
- 共用影片比例、解析度、時長、Seed 和採樣設定
- 原生品質／Turbo 快速預覽雙模式，自動配對 LoRA、步數、Euler、Sigma Shift 與參考圖精度
- 參考素材名稱代號、角色多張形象圖片、動作參考影片與選填聲音
- 形象圖片、動作影片與聲音支援直接拖放上傳，圖片可一次拖入多張
- 動作影片可選擇是否採用同步原聲，適合打擊、舞蹈、追逐與特效時序
- 自動編譯 `<Subject n>`、`<Picture n>`、`<Video n>`、`<Audio n>`
- 動態導演預設：自然、打擊、動作、舞蹈、追逐與特效，可調強度、物理表現和鏡頭反應
- 文字分鏡時間軸；可逐鏡填寫動態節拍與特效時序，多模態模式可加入分鏡構圖參考圖
- 工作佇列、進度、取消、影片預覽和下載
- 本機／遠端 ComfyUI 切換；遠端輸出自動回存面板電腦
- 共享 GPU Gateway：每位同事各自執行 H3 Studio，以個人金鑰共用主機 ComfyUI；素材、歷史、輸出與取消操作均依所有權隔離
- 一鍵安裝本機 ComfyUI、CUDA PyTorch、五個 H3 基礎模型與三個壓縮 Turbo LoRA，支援中斷後續裝
- MiniMax Music 3 音樂工作室：歌曲／純音樂、官方三段式 Caption、自訂歌詞、時長、Seed、MP3／FLAC、試聽、下載、歷史與我的最愛

## MiniMax Music 3

右上角「Music 3」會開啟獨立音樂工作室。第一次使用請按「安裝 Music 3 模型」，工具會下載 Comfy 官方重新封裝的 INT8 低顯存模型，合計約 11.9 GB，並支援暫停後續傳。模型儲存在既有 ComfyUI 的 `models/diffusion_models`、`models/text_encoders` 與 `models/vae`。

- 純音樂：工具會自動禁止人聲，並建立 Intro、Instrumental、Bridge、Outro 結構標籤。
- 歌曲：使用者填入含 `[Intro]`、`[Verse]`、`[Chorus]`、`[Bridge]`、`[Outro]` 的歌詞。
- Caption 會自動整合為官方建議的 `Global Metadata`、`Vocal Details`、`Arrangement` 三段結構。
- 16GB 顯存預設開啟分塊音訊解碼；音樂與影片共用同一條 GPU 佇列，避免同時生成造成顯存不足。
- 本機開源模型不按次計費，但生成期間會使用這台電腦的 GPU、電力與儲存空間。

## Turbo 快速預覽

- 文生、首尾、循環與續接：一般解析度使用 FL2VA 8-step；1344 × 768 使用 FL2VA 768p 4-step。
- 多模態、角色替換、彈窗面板與 MG：使用 Ref2VA 4-step，參考圖精度固定為 `match`。
- Turbo 會鎖定相容的 Euler、`simple` Scheduler 及 Sigma Shift；原生品質則保留手動採樣設定。

## 分鏡限制

目前分鏡圖片在單次 R2V 生成中作為構圖與鏡位參考，出現時間屬於提示詞近似控制。首尾模式只支援第一幀與最後一幀；若要讓多張中間分鏡精確落在指定時間，需要後續加入分段生成和影片串接。

## 動作參考建議

- 一般參考影片建議 2–15 秒、24 FPS；角色替換模式可直接上傳超過 15 秒的影片，由母任務依序處理各子片段。
- 長片子片段與進度會持久化；工具重開或單段失敗後，可從第一個未完成片段繼續，不必重做已完成部分。
- 角色身份以清楚的形象圖片為主，影片只負責動態；工具會自動加入防止服裝、背景和其他人物被複製的限制。
- 需要碰撞聲或特效聲跟動作同步時，勾選「同時參考影片原聲」；角色音色仍可另外上傳。
- H3 原生上限為 9 張參考圖片、3 支參考影片、3 段獨立參考聲音。

## 本機資料

- 上傳素材：`H3Studio/data/assets/`
- 工作紀錄與 API 工作流：`H3Studio/data/jobs/`
- ComfyUI 啟動紀錄：`H3Studio/data/comfyui.log`
- 回存影片：`H3Studio/data/outputs/`
- 音樂工作紀錄：`H3Studio/data/music_jobs/`
- 回存音樂：`H3Studio/data/music_outputs/`

## 測試

```powershell
cd "E:\MINIMAX H3\H3Studio"
..\ComfyUI\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## MG 動畫模式

「MG 動畫」使用 MiniMax H3 Ref2VA 工作流，預設提供三個不可刪除、但可補充額外素材的基礎欄位：

- `背景圖`：MG 底板與場景空間。
- `轉輪帶`：可見轉輪窗、停輪格與圖騰配置；不代表數學轉輪表。
- `角色`：畫面中的主要角色形象。

分層動態導演可獨立設定角色方位與表演、轉輪運動模型、圖騰移動方向、停輪順序、停輪間隔、停輪後圖騰表演、背景動態及鏡頭方式。編譯時會依 MiniMax H3 官方 Ref2VA 提示詞結構產生 `subject_definitions`、`summary`、`retention_analysis`、`detailed_description`、`overall_soundscape` 與 `non_diegetic_music` 六個區段。

AI 生成無法保證數學停輪結果、精確文字或逐像素版面。正式製作仍應保留引擎端的數學轉輪表、動態數值與 UI 疊圖，並在生成後檢查停輪格幾何、角色遮擋、文字可讀性與最終收勢。
