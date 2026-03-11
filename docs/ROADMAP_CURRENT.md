# ROADMAP_CURRENT
# Minecraft_translator_flet 當前 Roadmap（2026-03-11）

目的：整理目前已完成、已設計、待執行、與中期可能展開的 PR / 工作主題，讓後續規劃有一致參考，不需要每次重新回顧整段對話。

---

# 1. 已完成區

## PR1 — baseline / review bundle
狀態：✅ 已完成

內容：
- 建立 `.agentlens/INDEX.md`
- 整理 / 修正 review bundle
- 建立 pytest baseline
- 確認重構前就存在的 collection error

類型：盤點型

---

## PR2 — low-risk cleanup
狀態：✅ 已完成

內容：
- 移除 `_last_log_flush`
- 移除 `preview_jar_extraction_service` import
- 移除 `preview_jar_extraction_service()` wrapper
- 清理 `main.py` 雜訊註解
- 已補齊刪除說明六欄位回填

類型：驗證型

---

## PR3 — boundary cleanup
狀態：✅ 已完成

內容：
- 移除 `config_manager.py` import-time side effect
- 收斂 `main.py` 啟動責任
- `app/services.py` 改成 config wrapper 存取
- 已補齊刪除說明六欄位回填

類型：邊界型

---

## PR4 — maintainability comments
狀態：✅ 已完成

內容：
- 補 `services.py` / `task_session.py` / `extractor_view.py` / `main.py` / `config_manager.py` 的維護性註解
- 不改功能，只補意圖 / 邊界 / 風險說明

類型：文件型

---

## PR5 — restore cache compatibility imports
狀態：✅ 已完成

內容：
- 補回舊 import 路徑：
  - `app.views.cache_controller`
  - `app.views.cache_presenter`
  - `app.views.cache_types`
- 最終方案採 `app/views/__init__.py` 的 `sys.modules` alias
- 不新增 root-level shim 檔
- full pytest 已回到 **27 passed**
- 中途失敗方案與最終 `Rejected approach` 已留文件

關鍵經驗：
- 若 guard test 明確禁止 root-level shim 檔存在，優先考慮 package-level alias，不要先加實體 shim 檔

類型：邊界型 + 驗證型

---

# 2. 已完成的規範 / 工作基礎建設

## ITERATION_SOP.md v1.1
狀態：✅ 已建立

內容：
- Phase 1 / Phase 2 分離
- 沒有 Validation checklist 就停下
- PR / 設計稿 / 設計討論一律保存成 `docs/pr/*.md`
- 刪除說明六欄位標準
- 若有刪除項目，章節固定放在 `## Phase 1 完成清單` 後面

---

## PR execution types guide
狀態：✅ 已建立

檔案：
- `docs/PR_EXECUTION_TYPES.md`

內容：
- PR1～PR4 的執行類型與時間花費來源
- 分成：盤點型 / 驗證型 / 邊界型 / 文件型
- 可作為之後估算難度與準備方式的參考

---

# 3. 已設計完成、待執行區

## PR6 — rename `cache_manger` to `cache_manager`
狀態：🟡 設計完成，待進 Phase 1

設計檔：
- `docs/pr/2026-03-11_0932_PR_pr6-cache-manager-rename-design.md`
- `docs/pr/2026-03-11_0129_PR_pr6-phase0-cache-manger-inventory.md`

目標：
- 將 canonical package 名稱改成 `cache_manager`
- 保留 legacy import 相容性
- 更新 guard test
- 僅改 code + test，不碰 docs

已確認決策：
1. compatibility bridge 放在 `app/views/__init__.py`
2. PR6 只改 code + test
3. guard test 必須同 PR 一起改
4. guard test function 名稱也要一起改，不然 `pytest -k ...cache_manager` 可能靜默跳過

Phase 1 預計範圍：
- rename `app/views/cache_manger/` → `app/views/cache_manager/`
- 更新 `app/views/cache_view.py` import
- 更新 `app/views/__init__.py` alias
- 更新 `app/views/cache_manager/__init__.py` docstring
- 更新 `tests/test_ui_refactor_guard.py`

類型：邊界型 / 結構型

---

## PR7 — populate cache search metadata for `mod` / `path`
狀態：🟡 有設計稿，待確認 Phase 1 前置條件

設計來源：
- `docs/pr/2026-03-11_0015_PR_pr5-pr7-design-drafts.md`

目標：
- 補 `translation_tool/utils/cache_manager.py` 內 search result 的 `mod` / `path`
- 讓搜尋結果不只命中，還有可追來源的上下文

Phase 1 前必須先確認：
1. rebuild index / cache 的實際指令是什麼
2. 驗證時要貼哪種 search result / JSON 片段作為證據

類型：功能型 / 驗證型

---

# 4. 中期高機率會需要的 follow-up

## PR6 docs follow-up
狀態：🔵 尚未設計，但高機率需要

內容：
- README 內 `cache_manger` 正名
- changelog 舊文同步更新
- 舊 PR 文件若有必要可補充說明
- `.agentlens` 分析檔同步正名

這顆不應與 PR6 code rename 混在一起，避免範圍膨脹。

類型：文件型

---

## `cache_manager.py` 分層重構 PR
狀態：🔵 尚未立項，但中期高機率出現

依據：
- `.agentlens/03-translation-tool-review.md`
- `.agentlens/code-review.md`

問題：
- `translation_tool/utils/cache_manager.py` 責任太雜
- storage / shard / search entry / overview 混在一起

可能方向：
- 拆 storage
- 拆 shard 管理
- 保留 search 入口轉接
- 避免重複發明平行 search module

這類 PR 建議等 PR6、PR7 收乾淨後再設計。

類型：邊界型 + 重構型

---

# 5. 建議執行順序

## 短期
1. PR6
2. PR7

## 中期
3. PR6 docs follow-up
4. `cache_manager.py` 分層重構 PR

---

# 6. 目前工作的重點判讀

現在最值得做的不是再開很多新 PR，而是：
- 先把 PR6 這顆 naming debt 收乾淨
- 再做 PR7，把 cache metadata 補完整
- 等這兩顆穩了，再進更大的 cache 分層重構

換句話說，現在 roadmap 的主線很清楚：

> **先收命名債，再補搜尋 metadata，最後才拆更深的結構。**

## 目前所處階段

如果用階段來看，現在專案處在：

> **「重構前置收斂期」的尾聲**

更細一點說：
- **PR1～PR6**：主要都屬於前置基底 / 驗證 / 補強
  - 建 baseline
  - 補分析索引
  - 清 dead code
  - 收 import / side effect / naming debt
  - 修 test 與 import 相容性
- **PR7**：開始半隻腳跨進功能品質層
  - 不只是補地基
  - 還會直接改善 cache search 結果的可用性與上下文品質

所以目前不是還在亂修零碎問題，而是：
- 前置地基已經補到尾聲
- PR6 是這段的最後一顆大結構整理
- PR7 會是第一顆真正往功能結果品質推進的 PR

---

# 7. 一句話總結

目前整體進度健康：
- PR1～PR5 已收尾
- 規範與文件治理已到位
- PR6 已設計完成且前置盤點完成
- PR7 已有方向

下一步真正的主線就是：
**PR6 → PR7 → docs follow-up → cache_manager 深層重構**
