# MiniMax H3 Studio

MiniMax H3 Studio 是 ComfyUI MiniMax H3 工作流的中文操作面板。專案只包含面板程式，不包含模型權重、個人素材、生成紀錄或影片。

## 同事第一次安裝

1. 安裝 Python 3.11 或 3.12。
2. 下載或 `git clone` 本專案。
3. 雙擊 `setup_h3_studio.bat` 安裝面板環境。
4. 雙擊 `start_h3_studio.bat`，開啟 <http://127.0.0.1:8787>。
5. 點右上角「引擎設定」，選擇本機或遠端模式。

若電腦完全沒有 ComfyUI，選「使用這台電腦」，展開「這台電腦還沒有引擎？」即可執行一鍵安裝。安裝器會先檢查 NVIDIA GPU、Python、Git、記憶體及磁碟空間，並要求使用者自行閱讀和接受 MiniMax H3 授權。

## 兩種生成架構

### 使用同事電腦的模型

選「使用這台電腦」，網址維持 `http://127.0.0.1:8188`，並指定同事自己的 ComfyUI 資料夾。若勾選自動啟動，面板會尋找 ComfyUI 的 `.venv`、`venv` 或 portable `python_embeded` 環境後啟動引擎。

同事的 ComfyUI 必須自行安裝 MiniMax H3 節點與所需模型；模型檔不會從 Git 專案下載。

也可以使用面板內的一鍵安裝器，自動安裝經本專案驗證的 ComfyUI、CUDA 13.0 PyTorch 及五個 H3 模型。模型下載約 59.1 GiB，連同程式、環境與暫存，建議安裝磁碟至少保留 80 GiB。下載可重複執行，已完成的模型檔會略過。

### 使用你的電腦的模型

選「連線遠端電腦」，填入你的 ComfyUI 網址，例如 `http://100.x.x.x:8188`。面板與操作紀錄留在同事電腦，參考素材會送到你的 ComfyUI 執行，生成完成的影片會自動存回同事的 `H3Studio/data/outputs/`。

建議用公司內網或 Tailscale／WireGuard 類 VPN。ComfyUI 本身不應把 8188 連接埠直接公開到網際網路。遠端主機需以可被同事連到的位址啟動，例如：

```powershell
python main.py --listen 0.0.0.0 --lowvram --reserve-vram 1.5
```

還需要在 Windows 防火牆與 VPN 中只允許可信任的同事裝置連線。

## 主要功能

- 文生影片、首尾圖片、多模態參考、角色替換、圖騰循環、續接影片與彈窗面板動畫
- 共用比例、解析度、時長、隨機 Seed 與進階採樣設定
- 角色、背景、物件、圖片、影片與選填聲音的名稱代號
- 素材拖曳上傳、透明圖自動補螢光綠底、首尾圖擴邊適配
- 任務命名、生成歷史、折疊預覽、搜尋與每頁 20 筆分頁
- 遠端結果自動回存面板端，續接影片不依賴遠端檔案路徑

## 不會進入 Git 的資料

`.gitignore` 已排除 `ComfyUI/`、模型快取、`H3Studio/config.json`、`H3Studio/data/`、虛擬環境及生成簡報。請勿強制加入任何 `.safetensors` 或同事不應取得的素材。

## 測試

```powershell
cd H3Studio
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

模型使用 MiniMax H3 Community License；模型與節點的散布、公開服務或商用條件，需另外依各自授權確認。
