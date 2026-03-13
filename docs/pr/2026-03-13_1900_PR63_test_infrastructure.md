# PR63 設計稿：測試基礎設施建立

> 版本：v2.0（重新定位版）  
> 適用：Minecraft_translator_flet v0.6.0+  
> 編寫日期：2026-03-13

---

## 1. 目標與動機

### 目標
建立測試基礎設施，為未來的測試擴展做準備。

### 動機
- 經過驗證發現：當前 `tests/` 目錄**沒有使用 `@pytest.fixture`**
- `tests/conftest.py` 僅包含 path 設定
- 現有測試可能是直接使用 setUp/class setUp 或完全沒有 setup
- **重新定位**：從「消滅重複 fixtures」改為「建立共用 fixtures 規範」

---

## 2. 現狀分析

### 2.1 當前測試狀態
- 測試總數：**171 個**
- Fixtures：**0 個**（未使用 `@pytest.fixture`）
- conftest.py：僅含 path 設定

### 2.2 現有測試模式
可能的測試 setup 模式：
- 直接在 test function 內 setup
- 使用 class setUp/tearDown
- 完全沒有共用 setup

### 2.3 驗證指令
```python
# 確認無 fixtures
python -c "
import pathlib
count = 0
for p in pathlib.Path('tests').rglob('*.py'):
    content = p.read_text(encoding='utf-8')
    for line in content.splitlines():
        if '@pytest.fixture' in line:
            count += 1
print(f'Total fixtures: {count}')
"
# 輸出: Total fixtures: 0
```

---

## 3. 預期改動

### 3.1 建立共用 Fixtures 規範

#### 3.1.1 建立基礎 fixtures
在 `tests/conftest.py` 中建立以下基礎 fixtures：

```python
# tests/conftest.py

import pytest
import tempfile
import shutil
from pathlib import Path

@pytest.fixture
def temp_dir():
    """提供臨時目錄，測試結束後自動清理。"""
    tmp = tempfile.mkdtemp()
    yield Path(tmp)
    shutil.rmtree(tmp, ignore_errors=True)

@pytest.fixture
def mock_config():
    """提供測試用的 mock config。"""
    return {"test_mode": True, "mock_api": True}

# ... 其他基礎 fixtures
```

#### 3.1.2 建立測試資料 fixtures
- 提供常用的測試資料（如 sample lang.json）
- 提供 mock 的翻譯結果

### 3.2 建立測試輔助工具

#### 3.2.1 測試 Utility 模組
建立 `tests/utils.py`：
```python
"""測試輔助工具。"""

def create_sample_lang_file(path, entries):
    """建立測試用語言檔。"""
    ...

def mock_translation_result():
    """Mock 翻譯結果。"""
    ...
```

### 3.3 更新現有測試（可選）

原則：不強迫改動現有測試，只在需要時逐步採用新 fixtures

---

## 4. 風險評估

| 風險 | 等級 | 緩解措施 |
|------|------|----------|
| 破壞現有測試 | 🔴 高 | 先備份，逐步採用 |
| 與現有測試風格衝突 | 🟡 中 | 保持相容，不強迫 |
| 新增 fixtures 不被使用 | 🟢 低 | 為未來做準備，無立即影響 |

---

## 5. Validation checklist

- [ ] `python -c "... fixture count ..."` - 再次確認無 fixtures
- [ ] 建立基礎 fixtures 到 `tests/conftest.py`
- [ ] 建立測試輔助工具 `tests/utils.py`（可選）
- [ ] `uv run pytest -q` - 確認不破壞現有測試
- [ ] 記錄可採用新 fixtures 的時機

---

## 6. Rejected approaches

- 試過：消滅重複 fixtures
- 為什麼放棄：**當前無 fixtures 可消滅**（驗證發現 0 個）
- 最終改採：建立共用 fixtures 規範，為未來做準備

- 試過：強迫所有現有測試改用 fixtures
- 為什麼放棄：風險過高，可能破壞 171 個測試
- 最終改採：自願採用，逐步遷移

---

## 7. PR 依賴關係

- **PR63 依賴 PR62**：需等 PR62 完成測試健檢後執行
- **原因**：確保測試穩定後再建立基礎設施

---

## 8. 隱性 BUG 檢查清單

- [ ] 新增 fixtures 是否與現有測試衝突？
- [ ] temp_dir fixture 是否正確清理？
- [ ] 測試執行順序是否影響 fixtures？
- [ ] 是否需要 scope（function/module/session）？

---

## 9. 預期產出

- Phase 1 完成清單
- 更新的 `tests/conftest.py`（含基礎 fixtures）
- 測試輔助工具 `tests/utils.py`（可選）
- PR 文件：`docs/pr/2026-03-13_PR63_test_infrastructure.md`
