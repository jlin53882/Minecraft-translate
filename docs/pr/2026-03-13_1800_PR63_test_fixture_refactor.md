# PR63 設計稿：測試重構 - 消滅重複 Fixture

> 版本：v1.0  
> 適用：Minecraft_translator_flet v0.6.0+  
> 編寫日期：2026-03-13

---

## 1. 目標與動機

### 目標
統一測試 fixtures，降低維護成本，消除重複的測試 setup code。

### 動機
- 現有測試檔案中可能存在重複的 fixture 定義
- 統一 fixture 可提高測試一致性与可維護性
- 方便未來修改測試時只需改一處

---

## 2. 現狀分析

### 當前 conftest.py 狀態
```
tests/conftest.py - 現有 fixtures（需檢視）
```

### 重複 fixture 可能的來源
- 各 test file 內的 `@pytest.fixture` 裝飾器
- 重複的 mock setup
- 重複的 test data

### 審視範圍
- `tests/test_cache_*.py` 系列
- `tests/test_lm_translator_*.py` 系列
- 其他 test file

---

## 3. 預期改動

### 3.1 審視現有 Fixtures

**執行指令（使用 Python 而非 findstr）**：
```python
# 列出所有 fixture 定義
python -c "
import pathlib
for p in pathlib.Path('tests').rglob('*.py'):
    content = p.read_text(encoding='utf-8')
    for i, line in enumerate(content.splitlines(), 1):
        if '@pytest.fixture' in line:
            print(f'{p.name}:{i}: {line.strip()}')
"
```

**預期結果**：找出所有 fixture 位置

### 3.2 提取共用 fixtures

**判斷標準**：
- 超過 2 個測試檔案使用相同 fixture
- Fixture 邏輯超過 5 行
- Fixture 涉及共用的 test data

**預期提取的 fixtures**（視分析結果）：
- cache 相關的 mock fixtures
- LM API 相關的 mock fixtures
- 共通的 temp directory fixtures

### 3.3 更新測試檔案使用 shared fixtures

**預期修改**：
- `tests/conftest.py` - 新增共用 fixtures
- 各 test file - 改用 `@pytest.fixture(scope="module")` 或從 conftest 引入

---

## 4. 風險評估

| 風險 | 等級 | 緩解措施 |
|------|------|----------|
| 改動測試導致測試失敗 | 中 | 每個改動後執行 `pytest -q` 確認 |
| Fixture scope 錯誤 | 低 | 測試執行時會立即發現 |
| 意外改變測試行為 | 中 | 保持原有 fixture 邏輯，只做搬移 |

---

## 5. Validation checklist

- [ ] Python 腳本列出所有 fixtures（上面指令）
- [ ] 識別重複 fixture（定義：≥2 個檔案使用、邏輯 ≥5 行）
- [ ] `uv run pytest -q` - 確認改動後測試通過
- [ ] `python -c "import subprocess; r=subprocess.run(['uv','run','pytest','--co','-q'], capture_output=True, text=True); print(len([l for l in r.stdout.splitlines() if l.strip()]))"` - 確認測試數量不減少
- [ ] `ruff check tests/` - 確認程式碼品質

---

## 6. Rejected approaches

- 試過：不動現有 fixtures，各自保留
- 為什麼放棄：長期維護成本高，重複 code 需要處處修改
- 最終改採：提取共用 fixtures 到 conftest.py，統一管理

- 試過：一次把所有 fixture 都搬到 conftest
- 為什麼放棄：風險過高，萬一有 scope 問題難以排查
- 最終改採：先分析哪些真正共用，再逐步搬移

---

## 7. 刪除/移除/替換說明

本 PR 不刪除任何功能。主要是重構與搬移。

### 預期搬移的 fixture（需確認）

| Fixture 名稱 | 來源 | 目的地 | 理由 |
|--------------|------|--------|------|
| （待確認） | 各 test file | tests/conftest.py | 超過 2 處使用 |

---

## 8. 預期產出

- Phase 1 完成清單（分析結果與搬移清單）
- 更新後的 `tests/conftest.py`
- 修改的測試檔案
- PR 文件：`docs/pr/2026-03-13_PR63_test_fixture_refactor.md`

---

## 9. 執行順序

1. 先列出所有 fixtures
2. 識別重複且值得提取的 fixtures
3. 逐步搬移到 conftest.py
4. 更新引用這些 fixtures 的測試檔案
5. 執行測試驗證
