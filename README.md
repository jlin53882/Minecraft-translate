# Minecraft 模組包繁體化工具

基於 [Flet](https://flet.dev/) 的桌面應用程式，用於將 Minecraft 模組包從簡體中文／英文批量翻譯為繁體中文（台灣用語）。

## 功能

- **JAR 提取** — 從模組 JAR 檔中提取語言檔案與 Patchouli 手冊
- **語言合併** — 智慧合併 `en_us.json`、`zh_cn.json`、`zh_tw.json`，保留已翻譯內容
- **簡繁轉換** — 基於 OpenCC S2TW，含自訂替換規則
- **AI 機器翻譯** — 串接 Gemini API，支援多 Key 輪替、批次翻譯、自動重試
- **快取管理** — 翻譯快取分片儲存、全文搜尋、版本歷史
- **品管檢查** — 偵測未翻譯條目、簡繁不一致、英文殘留
- **輸出打包** — 產出可直接使用的資源包 ZIP

## 支援的翻譯來源格式

| 格式 | 說明 |
|------|------|
| `lang/*.json` | Minecraft 標準語言檔 |
| `patchouli_books/` | Patchouli 手冊 JSON |
| `ftbquests/*.snbt` | FTB Quests 任務檔 |
| `kubejs/*.js` | KubeJS Tooltip 腳本 |
| `*.md` | Markdown 文件 |

## 安裝

需要 Python ≥ 3.12 與 [uv](https://github.com/astral-sh/uv) 套件管理工具。

```bash
# 1. Clone
git clone https://github.com/jlin53882/Minecraft-translate.git
cd Minecraft-translate

# 2. 建立虛擬環境並安裝依賴
uv sync

# 3. 設定 config
cp config.example.json config.json
# 編輯 config.json，填入你的 Gemini API Key
```

## 使用

```bash
uv run python main.py
```

啟動後會開啟桌面 GUI，左側導覽列可切換功能頁面。

## 設定說明

`config.json` 主要設定項：

| 區塊 | 說明 |
|------|------|
| `logging` | 日誌等級與輸出目錄 |
| `translator` | 輸出資料夾、快取目錄、平行處理 worker 數 |
| `species_cache` | 生物學名快取（Wikipedia 查詢） |
| `lm_translator` | Gemini API Keys、模型設定、批次大小、System Prompt |
| `output_bundler` | 最終 ZIP 打包路徑 |
| `lang_merger` | 待翻譯與隔離資料夾命名 |

> ⚠️ `config.json` 包含 API Key，已加入 `.gitignore`，請勿上傳。

## 核心處理邏輯

### lang_merger.py — 語言檔合併

根據 ZIP 來源資料夾中存在的檔案組合，決定處理策略：

1. **有 `zh_tw.json`** → 先撈取英文未翻譯部分放入待翻譯，再放入目標資料夾
2. **只有 `zh_cn.json`** → 撈取英文 → 簡繁轉換 → 寫入目標 `zh_tw.json`
3. **只有 `en_us.json`**（目標無 `zh_tw.json`）→ 直接送入待翻譯
4. **三種檔案同時存在** →
   - `zh_tw.json` 撈取英文 → 放入目標資料夾
   - 與 `zh_cn.json` 比對，缺少的條目從 `zh_cn.json` 補入
   - 與 `en_us.json` 比對，移除已翻譯 Key
   - 剩餘未翻譯的 `en_us.json` 寫入待翻譯資料夾

**JSON 語言檔**採用增量更新（保留手動修改）；**內容檔案**（.md, .png, .snbt）採用覆寫策略。

### lm_translator_main.py — AI 翻譯引擎

```
while 還有剩餘資料:
    組一個 batch
    for model in (鎖定的 或 所有 model):
        try:
            呼叫 Gemini API
            JSON 不完整 → 縮小 batch 重試
            漏翻 → 縮小 batch 重試
            成功 → 寫入結果 → 下一批
        except:
            429 (Rate Limit) → 換 Key / 等待 RPM
            503 (Overload)   → 同 model 同 batch 重試
            400/504/Timeout  → 縮小 batch 或跳過
```

## 專案結構

```
├── app/                        # Flet UI 層
│   ├── services.py             # UI ↔ 核心之間的服務層
│   ├── ui/                     # 共用 UI 元件
│   └── views/                  # 各功能頁面
│       └── cache_manger/       # 快取管理子模組
│
├── translation_tool/           # 核心演算法層
│   ├── core/                   # 主要處理器
│   │   ├── ftb_translator.py   # FTB Quests 翻譯
│   │   ├── jar_processor.py    # JAR 提取
│   │   ├── lang_merger.py      # 語言檔合併
│   │   ├── lm_translator*.py   # Gemini AI 翻譯引擎
│   │   ├── output_bundler.py   # 輸出打包
│   │   └── icon_*.py           # Icon 分類與預覽
│   ├── plugins/                # 格式外掛
│   │   ├── ftbquests/          # SNBT 提取/注入/翻譯
│   │   ├── kubejs/             # KubeJS Tooltip
│   │   └── md/                 # Markdown 文件
│   ├── checkers/               # 品管檢查工具
│   └── utils/                  # 通用工具（快取、設定、日誌等）
│
├── tests/                      # 測試
├── docs/                       # 變更紀錄
├── config.example.json         # 設定檔範本（不含 API Key）
├── main.py                     # 應用程式進入點
├── pyproject.toml              # 專案依賴定義
└── uv.lock                     # 鎖定依賴版本
```

## 授權

MIT License
