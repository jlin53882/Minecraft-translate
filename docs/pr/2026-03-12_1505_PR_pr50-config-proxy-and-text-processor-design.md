# PR50 設計稿：`config_manager` / `text_processor` 相容層整理

## Summary
PR50 不是把 `config` proxy 立刻砍掉，而是先把『哪些地方真的需要 lazy proxy，哪些地方其實應該顯式 `load_config()`』盤清楚。這顆 PR 的核心是降低隱式設定依賴，尤其是 `text_processor.py` 這種實際工作模組。

---

## Phase 0 盤點
- `translation_tool/utils/config_manager.py` 已改成不在 import time 初始化 logging，但仍保留 `LazyConfigProxy` 作舊介面相容。
- `translation_tool/utils/text_processor.py` 目前直接 `from .config_manager import config, resolve_project_path`，屬於明確 proxy 依賴。
- 全 repo 已有多處改成顯式 `load_config()`，代表 proxy 已不再是唯一做法。
- 若不先整理，後面 dead code cleanup 很容易誤砍相容層，或讓新模組繼續偷偷長出 `config` 依賴。

---

## 設計範圍
- 先做 inventory：列出所有仍依賴 `config` proxy 的 caller，分成『必須 lazy』與『應改顯式 load』兩類。
- 優先調整 `text_processor.py`：把 replace rules path / project path 解析改成顯式 helper，減少 module-level proxy 依賴。
- 必要時新增 `translation_tool/utils/config_access.py` 或 `get_runtime_config()` 類 helper，專門包裝常見讀取點，避免新 caller 直接抓 proxy。
- `config = LazyConfigProxy()` 暫時保留，但註明只作 legacy compatibility；真正刪除或縮限留給 PR57。
- 新增 `tests/test_config_proxy_compat.py`、`tests/test_text_processor_config_resolution.py`。

---

## Validation checklist
- [ ] `rg -n "from .*config_manager import config|config_manager\.config|LazyConfigProxy|load_config\(" translation_tool app --glob "*.py"`
- [ ] `uv run pytest -q tests/test_config_proxy_compat.py tests/test_text_processor_config_resolution.py tests/test_path_resolution.py --basetemp=.pytest-tmp\pr50 -o cache_dir=.pytest-cache\pr50`
- [ ] `uv run pytest -q --basetemp=.pytest-tmp\pr50-full -o cache_dir=.pytest-cache\pr50-full`

---

## Rejected approaches
1) 試過：直接刪掉 `config = LazyConfigProxy()`，逼所有 caller 一次改完。
2) 為什麼放棄：這做法太硬，任何漏網之魚都會變 runtime import error，而且這顆 PR 的重點本來就不是 caller migration 大清洗。
3) 最終改採：先盤點、先替換高價值 caller（尤其 text_processor），再把 proxy 限縮成明確 legacy seam。

---

## Not included in this PR
- 不改 config.json schema。
- 不改 logging config 格式。
- 不順手處理 unrelated path bugs。

---

## Next step
- PR51 進入 UI 前置測試工程，先補大 view characterization tests。
