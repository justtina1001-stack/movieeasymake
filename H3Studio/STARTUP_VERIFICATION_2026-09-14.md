# 同事從 Git 安裝與啟動驗證（2026-09-14）

## 結論

修正後的工作區版本已在 Windows、Python 3.12.10 64-bit 的獨立資料夾完成全新安裝、實際面板啟動、API 與瀏覽器操作驗證。未複製原機虛擬環境、私人設定、作品或 ComfyUI 模型。

本次驗證以 Git `c74fae245e2c73f488185e062b54da7c5c526ea7` 為舊版下載基準；該版本在有 Python Launcher 的測試環境可啟動，但仍有下列兩個已重現問題。**本報告隨完整新版程式、版本清單、啟動修正及新增測試一同發布。** 同事應下載或更新至包含本報告的版本，以下表格中的「Git 下載版」均指該舊版基準。

面板啟動成功不等於已具備影片生成環境。本次沒有重新下載完整 GPU 模型、在另一台實體電腦安裝引擎或生成正式影片。

## 實測版本與結果

| 項目 | Git `main` 下載版 | 修正後工作區版本 |
| --- | --- | --- |
| 來源 | 從 GitHub 實際 clone | 受追蹤檔案＋H3Studio 新增測試的獨立副本 |
| 模型清單版本 | `2026.08.31-1` | `2026.09.10-1` |
| 全新 `setup_h3_studio.bat --auto` | 通過 | 通過 |
| Python 單元／整合測試 | 117 項通過 | 141 項通過 |
| 前端測試 | 未另跑新版測試 | 14 項通過 |
| `pip check` | 通過 | 通過 |
| 無引擎時面板及主要 API | 可啟動 | 可啟動 |
| 只有 PATH Python、沒有 `py` | 新增回歸測試重現誤判 | 通過 |
| 無引擎時掃描 LoRA | HTTP 500 | HTTP 503 JSON，提示先連線引擎 |

兩份副本各自建立 `H3Studio/.venv`，均依 Git 內的 `requirements.txt` 安裝。實測主要套件為 aiohttp 3.14.3、av 18.1.0、numpy 2.5.3、Pillow 12.3.0、huggingface_hub 1.31.0、hf-xet 1.6.0。這是本次成功解析的版本，並非鎖定檔。

為保留原本執行中的 Studio／Gateway／ComfyUI，測試面板使用 `app.py --no-browser --port 18787` 與 `18788`。副本設定只把引擎網址改為無服務的 `127.0.0.1:18988`，保留一般使用者角色、相對 ComfyUI 目錄及自動啟動設定，以驗證缺少引擎時的行為。未在原本占用中的 8787 上重複測試雙擊及自動開啟瀏覽器。

以下 11 個頁面／API 回傳 HTTP 200：首頁、`/static/app.js`、`/static/styles.css`、`/api/status`、`/api/connection`、`/api/jobs`、`/api/shortfilms`、`/api/model-updates`、`/api/engine-installer/status`、`/api/music/status`、`/api/voice/status`。一般使用者的 `/api/gateway/status` 正確回傳 403；修正後離線 `/api/loras` 正確回傳 503。

瀏覽器確認 Git 版引擎設定視窗能開啟、工作區新版能切換短片創作，顯示一般使用者及引擎未啟動狀態；上述操作沒有瀏覽器 console error。空的素材／工作紀錄不影響啟動。

## 本次修正

1. `setup_h3_studio.bat`：Python 版本比較原本寫成 `^<=`，cmd 在雙引號內保留 caret，造成 Python SyntaxError。移除 caret，保留批次檔 CRLF。新增 `tests/test_windows_launcher.py`，實際隔離 PATH 並移除 Python Launcher 可見性，驗證修正前失敗、修正後成功。
2. `app.py`：LoRA 例外處理使用 `aiohttp.ClientError`，卻未匯入 `aiohttp`。補上匯入。新增 `tests/test_startup_api.py`，驗證連線錯誤回傳可讀 JSON，並檢查新安裝的狀態與管理權限；兩項 LoRA 測試修正前回傳 500，修正後通過。
3. `README.md`：測試指令改用專案自己的面板環境，移除原機磁碟絕對路徑與對 ComfyUI 虛擬環境的依賴。

## 同事安裝步驟

1. 下載或 clone 包含本報告的版本；已有專案時先執行 `git pull` 取得更新。
2. 安裝 64-bit Python 3.12，安裝時勾選 Add Python to PATH；本次實測版本為 3.12.10。
3. 在可寫入的資料夾解壓專案，執行 `setup_h3_studio.bat`，首次安裝需網路下載套件。
4. 執行 `start_h3_studio.bat`，開啟 `http://127.0.0.1:8787`。
5. 在「引擎設定」選擇本機或遠端。使用共享主機時，填入管理者提供的 Gateway 網址與個人金鑰。

只連遠端 GPU 主機時，面板不需要本機 H3 模型或 NVIDIA GPU。Qwen3-TTS 語音仍由面板所在電腦本機執行，不能以遠端影片引擎的成功連線推定語音也可用。

## 本機 GPU 安裝的驗證範圍

工作區版本的模型清單有 13 個檔案，共 69.23 GiB，建議至少保留 90 GiB 空間，語音、音樂及作品另計。已逐一檢查 Hugging Face 公開 metadata：固定 revision、檔名、大小與清單一致；有列 SHA256 的五個檔案亦與 metadata 一致。未下載權重重新計算雜湊。

仍有一項安裝限制：目前前置檢查只看 GPU 名稱／記憶體，未檢查 NVIDIA 驅動版本。安裝器使用 CUDA 13.0 PyTorch，而 CUDA 可用性檢查位於模型下載之後。CUDA 13.x 要求 R580 或更新驅動，因此舊驅動電腦可能通過初步檢查、下載後才失敗；這項行為本次未改動。[NVIDIA 官方相容性表](https://docs.nvidia.com/deploy/cuda-compatibility/minor-version-compatibility.html)

本次結果證明面板安裝與啟動路徑可行；完整本機 GPU 安裝、特定顯卡的生成效果及真實跨機 Gateway 連線仍需在同事環境驗證。

## 重跑測試

在專案根目錄完成 setup 後執行：

```powershell
cd H3Studio
.venv\Scripts\python.exe -m unittest discover -s tests -v
.venv\Scripts\python.exe -m pip check
```

前端測試需要額外安裝 Node.js；一般同事只啟動面板不需要 Node.js：

```powershell
node --test tests/test_frontend_acceleration.js tests/test_frontend_segments.js
```

原始安裝紀錄、套件版本與測試結果保存在本次驗證機的 `%TEMP%/h3-git-startup-20260914-083150/`，不隨 Git 發布。
