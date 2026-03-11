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

# 3. 已完成、可視為落檔的近期 PR

## PR6 — rename `cache_manager` package and clean up typo remnants
狀態：✅ 已完成

設計檔：
- `docs/pr/2026-03-11_0932_PR_pr6-cache-manager-rename-design.md`
- `docs/pr/2026-03-11_0129_PR_pr6-phase0-cache-manger-inventory.md`

完成內容：
- 將 canonical package 名稱改成 `cache_manager`
- 保留 legacy root-level import 相容性
- 更新 guard test
- 僅改 code + test，不碰 docs

關鍵決策：
1. compatibility bridge 放在 `app/views/__init__.py`
2. 不保留 `app.views.cache_manger.*` 舊 package path
3. guard test function 名稱與斷言一併更新

類型：邊界型 / 結構型

---

## PR7 — populate cache search metadata for `mod` / `path`
狀態：✅ 已完成

設計來源：
- `docs/pr/2026-03-11_0015_PR_pr5-pr7-design-drafts.md`
- `docs/pr/2026-03-11_PR_pr7-cache-search-metadata-design.md`

完成內容：
- 補 `translation_tool/utils/cache_manager.py` 內 search result 的 `mod` / `path`
- 讓搜尋結果不只命中，還有可追來源的上下文
- metadata 於 rebuild search index 後生效

關鍵結論：
1. search result schema 本來就有 `mod` / `path`
2. 真正缺口是 rebuild index 時把它們硬塞空字串
3. 這輪採 backward-compatible inference，不做 cache shard migration

類型：功能型 / 驗證型

---

## PR8 — replace `os.getcwd()` with project-root-based paths in `app/services.py`
狀態：✅ 已完成

設計 / 記錄檔：
- `docs/pr/2026-03-11_PR_pr8-services-project-root-paths.md`

完成內容：
- `CONFIG_PATH` / `REPLACE_RULES_PATH` 不再依賴目前 shell 的 `cwd`
- 改為基於專案根目錄的穩定路徑
- 與測試中的 `Path(__file__).resolve().parents[...]` 慣例對齊

類型：穩定性修復 / 邊界型

---

# 4. 接下來的中期主線（重新編號）

## PR9 — `cache_manager.py` 分層重構（Phase 0 / 設計）
狀態：🔵 尚未立項

依據：
- `.agentlens/03-translation-tool-review.md`
- `.agentlens/code-review.md`

問題：
- `translation_tool/utils/cache_manager.py` 責任太雜
- storage / shard / search entry / overview 混在一起

預期目標：
- 先完成 Phase 0 盤點與設計稿
- 明確切出 boundary，避免一次性大爆改

類型：邊界型 + 重構型

---

## PR10 — `cache_manager.py` 分層重構第一顆實作 PR
狀態：🔵 尚未立項

可能方向：
- 拆 storage
- 拆 shard 管理
- 保留 search 入口轉接
- 避免重複發明平行 search module

這顆應在 PR9 設計完成後再開始。

類型：邊界型 + 重構型

---

# 5. 建議執行順序

## 已完成
1. PR6
2. PR7
3. PR8

## 接下來
4. PR9
5. PR10

---

# 6. 目前工作的重點判讀

現在最值得做的不是再補零碎小修，而是：
- PR6 已收掉 naming debt
- PR7 已把 cache search metadata 補完整
- PR8 已補掉 `cwd` 路徑耦合
- 下一步該正式進入 `cache_manager.py` 的結構切分設計

換句話說，現在 roadmap 的主線已經變成：

> **前置地基已收斂，下一步是把 `cache_manager.py` 拆成可維護的邊界。**

## 目前所處階段

如果用階段來看，現在專案處在：

> **「重構前置收斂期」已完成，正要進入結構拆分期**

更細一點說：
- **PR1～PR6**：前置基底 / 驗證 / 補強 / naming debt 收斂
- **PR7**：開始跨進功能品質層，改善 search result 上下文
- **PR8**：補掉基礎路徑穩定性風險
- **PR9 之後**：才是真正的大型結構重整

所以現在不是還在清理地基，而是：
- 前置整理已經足夠
- 功能品質也有第一輪補強
- 接下來可以開始設計 `cache_manager.py` 的結構拆分

---

# 7. 一句話總結

目前整體進度健康：
- PR1～PR5 已收尾
- PR6 已完成
- PR7 已完成
- PR8 已完成
- 規範與文件治理已到位

下一步真正的主線就是：
**PR9（分層重構設計）→ PR10（第一顆結構拆分實作）**
