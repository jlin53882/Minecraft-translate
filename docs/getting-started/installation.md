# 安裝與設定

## 環境需求

- Python `>=3.12`；精確的專案與依賴宣告以根目錄 [`pyproject.toml`](../../pyproject.toml) 為準。
- [uv](https://github.com/astral-sh/uv) 用來建立環境、安裝依賴與執行命令。
- Gemini API Key 只放在本機 `config.json`；不要把它提交到 Git 或貼到聊天內容。

## 安裝

```bash
git clone https://github.com/jlin53882/Minecraft-translate.git
cd Minecraft-translate
uv sync
```

從設定範本建立本機設定檔。PowerShell 可使用：

```powershell
Copy-Item config.example.json config.json
```

在 `config.json` 的 `lm_translator.keys` 填入 API Key；設定欄位和預設值由
[`translation_tool/utils/config_schema.py`](../../translation_tool/utils/config_schema.py) 產生，完整說明見
[`../reference/configuration.md`](../reference/configuration.md)。

## 啟動

```bash
uv run python main.py
```

啟動後會開啟 Flet 桌面 GUI。README 的[功能與畫面清單](../../README.md)可用來確認主要入口；
第一次使用建議接著閱讀 [`quickstart.md`](quickstart.md)。
