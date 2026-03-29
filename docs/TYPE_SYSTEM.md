# 型別系統文檔

## 一、型別策略

本專案採用 **漸進式型別標註（Gradual Typing）** 策略：

- **核心模組**：已完成完整的型別標註（函式簽名、類別屬性、返回值）
- **遷移目標**：逐步為所有模組添加型別標註，提升靜態分析覆盖率
- **配置工具**：使用 `mypy` 進行靜態型別檢查，`py.typed` marker 表明此為**含義式分發套件（PEP 561）**

## 二、已完成的型別標註模組

> 隨著專案演進，此列表持續更新。

| 模組 | 說明 | 狀態 |
|------|------|------|
| `translation_tool/` | 主套件目錄，含 `py.typed` marker | ✅ 含型別標註 |

**狀態標記含義**：
- ✅ 已完成標註
- 🔄 部分標註
- ⏳ 待處理

## 三、`py.typed` Marker 的意義

`translation_tool/py.typed` 是 **PEP 561** 規範的 marker 檔案，功能如下：

- 告訴型別檢查工具（如 mypy、pyright）此套件**已宣告為含型別的分發套件**
- 消費者在靜態分析時可正常對此套件執行 `mypy --ignore-missing-imports` 等檢查
- 若缺少此檔案，mypy 會假設此套件「無型別資訊」並回報 `Cannot find implementation or library stub`

## 四、驗證方式

### 4.1 運行 mypy 靜態檢查

```bash
cd C:\Users\admin\.openclaw\workspace\repos\Minecraft-translate
uv run mypy translation_tool
```

預期輸出：若無 `py.typed` marker 或模組漏標，會有 `Skipping analyzing ...: inferred from PEP 561` 等提示。

### 4.2 運行 py_compile 語法驗證

```bash
uv run python -m py_compile translation_tool/<module>.py
```

### 4.3 驗證 py.typed marker 存在

```bash
# Windows PowerShell
Test-Path translation_tool/py.typed

# 或 Python
import os
print(os.path.exists("translation_tool/py.typed"))
```

## 五、mypy 配置說明

`pyproject.toml` 中的 mypy 配置項：

```toml
[tool.mypy]
python_version = "3.12"       # 指定 Python 版本
strict = false                 # 不開嚴格模式（寬鬆，逐步提升）
warn_return_any = true         # 警告隱式返回 any 的函式
warn_unused_configs = true     # 警告未使用的設定
ignore_missing_imports = true  # 對無法解析的第三方套件不报错
exclude = ["tests/", "tools/", ".venv/"]  # 排除干擾目錄
```

`ruff.lint` 設定：

```toml
[tool.ruff.lint]
select = ["I", "E", "F", "W"]  # import排序、錯誤、格式化、警告
line-length = 120               # 行長限制
```

## 六、持續整合建議

新模組加入時：
1. 為所有公開 API 添加型別標註
2. 更新本文件第二節的模組列表
3. 運行 `uv run mypy translation_tool` 確認無新錯誤
4. 運行 `uv run python -m py_compile` 確認語法正確
