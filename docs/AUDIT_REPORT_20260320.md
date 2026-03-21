# Minecraft Translation Flet — 程式稽核報告

**日期**：2026-03-20
**版本**：jlin53882/Minecraft-translate @ main
**稽核方法**：Multi-Agent 攻防分析（Code Reader + Dependency Mapper + Attacker + Defender/Judge）
**報告產出**：Review Claw（稽核 Agent）

---

## 執行摘要

本專案是一個基於 Flet UI 的 Minecraft 翻譯工具，核心業務為語言檔合併（lang_merger）、Gemini API 翻譯、與 KubeJS/FTB/MD 等多模組翻譯支援。掃描 175 個模組、463 個函式，發現 **2 個 Critical 問題**、**8 個 High 問題**、**5 個 Medium 問題**、**2 個 Low 問題**，共計 18 個需要修復的問題，以及 3 個確認的架構風險。

---

## 專案架構概覽

### 模組分布

| 區域 | 模組數 | 說明 |
|------|--------|------|
| `app/` | 75 | Flet UI 應用層（View + Service） |
| `translation_tool/` | 84 | 核心業務邏輯（Python 非 Flet） |
| `tools/` | 3 | 工具腳本 |
| 主程式 | 1 | `main.py` |
| **總計** | **175** | 含 `__init__` |

### 依賴架構

```
translation_tool.core.*
    ↑ 依賴
translation_tool.utils.*（log_unit, config_manager, text_processor — 三個 God Module）
    ↑ 依賴
app/services_impl/pipelines/*.py（Façade 層）
    ↑ 依賴
app/views/*.py（Flet UI，動態載入 via view_registry）
```

### 三個 God Module（高耦合風險）

| 模組 | 被 import 次數 | 職責 |
|------|---------------|------|
| `translation_tool.utils.log_unit` | 13 | 全域日誌 |
| `translation_tool.utils.config_manager` | 11 | 全域設定讀寫 |
| `translation_tool.utils.text_processor` | 8 | opencc + 正規化 + replace_rules |

---

## 單元測試覆蓋率分析

### 測試現況

| 指標 | 數值 |
|------|------|
| 測試檔案數 | **110+** |
| 主要測試區域 | lang_merger、lm_translator、cache、ftb_translator、kubejs、jar_processor |
| 測試覆蓋類型 | 單元測試、characterization test、guard test |

### 確認的測試缺口（需補充）

| 缺口 | 說明 | 優先度 |
|------|------|--------|
| `text_processor` replace_rules 多執行緒安全 | ATK-003 發現的 race condition 無對應測試 | 高 |
| `config_manager` schema 驗證 | 目前無 schema 驗證，但從未針對錯誤輸入測試 | 高 |
| `KNOWN_ZIP_PACKAGING_PREFIXES` 新前綴 | 沒有測試自訂前綴被錯誤處理的場景 | 高 |
| `translation_actions.py` threading UI 安全 | `page.update()` 跨執行緒呼叫未有對應 mock 測試 | 高 |
| SQLite WAL corruption 恢復 | `cache_search.py` 在 daemon crash 後的 DB recovery 未測試 | 中 |
| `lru_cache` OOM 邊界 | 未測試 cache eviction 行為與記憶體回收 | 低 |

---

## 程式邏輯問題清單

---

### P0 Critical（阻擋，須立即修復）

#### 【ATK-001】`translate_batch_smart` 參數順序錯亂，dry_run/export_cache_only 完全失效

| 欄位 | 內容 |
|------|------|
| **位置** | `translation_tool/core/lm_translator_main.py:67` |
| **攻擊類型** | 業務邏輯 |
| **重現條件** | 呼叫 `translate_batch_smart(batch_items, total=N, dry_run=True, export_cache_only=True)` |
| **預期行為** | `dry_run=True` 不呼叫 API；`export_cache_only=True` 只輸出快取 |
| **實際行為** | 位置參數 `(items, batch_size, batch_profile, total, ...)` 與呼叫端 `(...dry_run, export_cache_only, ...)` 完全錯配。`dry_run` 被當成 `batch_profile`（字串比對失敗），`export_cache_only` 被當成 `total`。dry_run 和 cache-only 行為**完全失效**，正常呼叫 API 且無視快取 |
| **修復方向** | 將所有 `translate_batch_smart` 的 caller 改用 keyword argument，或重新設計函式簽名 |
| **驗證方式** | `pytest tests/test_lm_translator_main.py -k dry_run` |

#### 【ATK-002】BOM 檔案導致 UI 模組完全無法解析

| 欄位 | 內容 |
|------|------|
| **位置** | `app\ui\components.py:1` 和 `app\ui\quick_jump.py:1` |
| **攻擊類型** | 型別 |
| **重現條件** | 任何嘗試 import 這兩個模組的時刻（Flet 啟動） |
| **預期行為** | Python 可以正常解析並 import |
| **實際行為** | 這兩個檔案以 BOM（U+FEFF）開頭，Python 在解析時報 `SyntaxError: invalid non-printable character`。Flet 啟動時若接觸到這些模組會直接崩潰 |
| **修復方向** | 移除 BOM：`sed -i '1s/^\xef\xbb\xbf//' components.py quick_jump.py` 或重新儲存為 UTF-8 without BOM |
| **驗證方式** | `python -m py_compile app/ui/components.py && python -m py_compile app/ui/quick_jump.py` |

---

### P1 High（影響核心功能正確性）

#### 【ATK-003】`text_processor` 全域快取在多執行緒下 race condition

| 欄位 | 內容 |
|------|------|
| **位置** | `translation_tool/utils/text_processor.py:42-43` |
| **攻擊類型** | 邊界 / 狀態 |
| **重現條件** | 同時執行多個翻譯工作，各自載入不同的 replace_rules |
| **實際行為** | `_LITERAL_RULES` 等模組級全域變數在 Thread A 初始化後，Thread B 的不同 rules 被 `if _LITERAL_RULES is not None: return` 短路忽略。翻譯結果**錯亂**（A 工作吃到 B 工作的規則） |
| **修復方向** | 將 replace_rules 快取改為 `threading.local()` 或每個翻譯工作獨立執行期，不共享模組級快取 |
| **驗證方式** | 寫一個多執行緒翻譯測試，確認不同 rules 的結果不會互相污染 |

#### 【ATK-004】`translation_actions.py` threading 對 Flet View 的非同步存取

| 欄位 | 內容 |
|------|------|
| **位置** | `app/views/translation/translation_actions.py:38-50` |
| **攻擊類型** | 狀態 |
| **重現條件** | 使用者點擊翻譯後馬上切換分頁或關閉，worker thread 的 `view.session.add_log()` 在 view 已解除參照時拋例外 |
| **實際行為** | Daemon thread 持有 view 參照，但 Flet main thread 可隨時移除該 view。`view.session.add_log()` 與 `view.page.update()` 的跨執行緒競爭可能導致 `AttributeError` 或 Flet 例外，且未有任何包圍 |
| **修復方向** | Worker thread 不直接持有 view，改用 `queue.Queue` 將 log entry 傳回 main thread，由 main thread 負責 UI 更新 |
| **驗證方式** | Mock view 的生命週期，快速切換分頁，確認翻譯錯誤不會導致整個 UI 崩潰 |

#### 【ATK-007】`KNOWN_ZIP_PACKAGING_PREFIXES` 白名單導致新前綴結構被靜默錯誤處理

| 欄位 | 內容 |
|------|------|
| **位置** | `translation_tool/core/lang_merge_pipeline.py:27` |
| **攻擊類型** | 業務邏輯 |
| **重現條件** | 使用者上傳使用新前綴結構的 ZIP（如 `custom_out/assets/.../lang/zh_cn.json`） |
| **實際行為** | 前綴不在白名單時，`final_output_rel` 會變成 `custom_out/assets/...`，導致輸出目錄多一層奇怪的目錄。`pending.json` 也會有同樣的路徑錯誤。**無任何警告或日誌** |
| **修復方向** | 當前綴不在白名單時，輸出警告日誌，並提供 `--allow-custom-prefix` 參數讓使用者明確授權 |
| **驗證方式** | 建立 `custom_out/` 前綴的 ZIP，確認輸出路徑正確 |

#### 【ATK-009】`lm_api_client.py` API key 輪替在高並發下不安全

| 欄位 | 內容 |
|------|------|
| **位置** | `translation_tool/core/lm_config_rules.py:61-70` + `lm_translator_main.py:405` |
| **攻擊類型** | 狀態 |
| **重現條件** | 10 個翻譯工作同時執行，觸發 API 429 錯誤 |
| **實際行為** | `_get_all_keys()` 每次都重新讀取並解析 `config.json`（無快取）。若使用者在 worker 執行期間修改 API key，`set_key_count` 和 `get_current` 的 race 導致 key index 飄移。多個批次同時收到 429 時，可能同時輪替到**同一個 key** |
| **修復方向** | 在 `KeyIndexTracker` 內部對 `get_current_api_key()` 和 `rotate_api_key()` 全部加鎖；`load_config()` 加入快取並設定 TTL |
| **驗證方式** | 多執行緒同時呼叫 API key 輪替，確認每個 thread 拿到不同的 key |

#### 【ATK-010】ZIP 極端壓縮比導致 ZIP bomb（資源耗盡）

| 欄位 | 內容 |
|------|------|
| **位置** | `translation_tool/core/lang_merge_zip_io.py` + `ftb_translator.py` |
| **攻擊類型** | 邊界 |
| **重現條件** | 使用者上傳一個精心構造的高度壓縮 ZIP（10GB 文字壓縮成 1KB） |
| **實際行為** | `zipfile.ZipExtFile.read()` 無限 expand，會直接 OOM。`process_quest_folder` 的 `_load_json_dict` 直接 `f.read()` 無任何保護 |
| **修復方向** | 使用 `zipfile.ZipFile.extract()` 並對展開大小設限；或在 read 前用 `zefile.read()` 預先檢查壓縮比；參考 `zipfile` 的 `allowZip64=False` |
| **驗證方式** | 用 `zipfile` 對極限壓縮檔進行單元測試，確認拋出例外而非 OOM |

#### 【ATK-011】`CacheSearchEngine` SQLite WAL 在 daemon thread 關閉後 corruption

| 欄位 | 內容 |
|------|------|
| **位置** | `translation_tool/utils/cache_search.py:58-65` |
| **攻擊類型** | 狀態 |
| **重現條件** | Daemon thread 正在寫入 search index，使用者快速關閉 Flet 應用 |
| **實際行為** | `PRAGMA synchronous = OFF` 讓 WAL 在 daemon 被 kill 時 WAL header 可能寫一半，重啟後 SQLite 認為 WAL 有 dirty data 但無法 recovery，導致 **DB corruption** |
| **修復方向** | 改用 `PRAGMA synchronous = NORMAL`；在 Flet app 的 `page.on_disconnect` 明確關閉 DB 連線；加入 WAL recovery 邏輯 |
| **驗證方式** | Mock daemon thread crash 情境，驗證 DB 重啟後有正確的 recovery 或 fallback 機制 |

#### 【ATK-013】`config_manager` 無 schema 驗證，錯誤輸入導致全域 crash

| 欄位 | 內容 |
|------|------|
| **位置** | `translation_tool/utils/config_manager.py` 全域 |
| **攻擊類型** | 型別 |
| **重現條件** | 使用者編輯 `config.json` 打錯（如 `"keys": "token"` 而非 list、`"initial_batch_size_lang": "abc"` 而非數字） |
| **實際行為** | 無 schema 驗證，錯誤型別在翻譯到一半時才爆炸，輸出處於不一致狀態。`keys[safe_index]` 若 keys 是字串會取第一個字元（幾乎不可能匹配 API key format），`ThreadPoolExecutor(max_workers="4")` 會拋 `TypeError` |
| **修復方向** | 引入 `pydantic` 或 `datamodel` 做 config schema 驗證，啟動時對所有欄位型別做驗證 |
| **驗證方式** | 對 `config_manager.py` 寫負向測試（錯誤型別輸入），確認有明確的 `ConfigValidationError` |

#### 【ATK-017】`page.update()` 在元件未掛載時呼叫導致 InvalidArgument

| 欄位 | 內容 |
|------|------|
| **位置** | `app/views/translation/translation_actions.py:30` |
| **攻擊類型** | Flet |
| **重現條件** | 翻譯剛啟動，使用者快速切離翻譯 Tab，worker thread 的 `page.update()` 在 view 已移除後執行 |
| **實際行為** | Flet 在已卸載的元件上呼叫 `update()` 會直接拋 `Exception`（非 `AttributeError`），且**未有任何包圍**。整個翻譯流程中斷，使用者看到「Unexpected error」SnackBar |
| **修復方向** | 在所有 `page.update()` 前檢查 `view.page is not None`；或改用 `try/except` 包圍所有 UI 更新操作 |
| **驗證方式** | Mock view 生命週期，快速切換分頁，確認翻譯流程不會因 view 卸載而崩潰 |

---

### P2 Medium（影響次要功能或使用體驗）

#### 【ATK-005】`lru_cache` 無上限，長期執行記憶體只增不減

| 欄位 | 內容 |
|------|------|
| **位置** | `translation_tool/core/lang_merge_pipeline.py:37` |
| **攻擊類型** | 邊界 |
| **實際行為** | `maxsize=4096` 的 `lru_cache` 在 Python 中 eviction 後記憶體**不會自動釋放**。長期執行（伺服器模式）記憶體只增不減，最終 OOM |
| **修復方向** | 定期清除快取；或在 `cache_manager.py` 層級管理 cache eviction；或改用 `cachetools` 的 `TTLCache` |
| **驗證方式** | 長時間執行或模擬大量 ZIP 處理，監控記憶體增長曲線 |

#### 【ATK-006】`startup_tasks` daemon thread 在 Flet page 關閉後繼續執行

| 欄位 | 內容 |
|------|------|
| **位置** | `app/startup_tasks.py:20-25` |
| **攻擊類型** | 狀態 |
| **實際行為** | Daemon thread 不阻擋程序退出，但舊程序正在執行的 `cache_rebuild_index_service()` 可能與新程序競爭同一份 cache 目錄的檔案鎖 |
| **修復方向** | 在 Flet app 的 lifecycle handler 註冊 shutdown hook，明確中斷 background tasks；將 `daemon=True` 改為非 daemon 並妥善管理生命週期 |
| **驗證方式** | Mock 快速重啟情境，驗證舊 background task 是否正確終止 |

#### 【ATK-008】`replace_rules` 衝突時行為不穩定

| 欄位 | 內容 |
|------|------|
| **位置** | `translation_tool/utils/text_processor.py:76-78` |
| **攻擊類型** | 業務邏輯 |
| **實際行為** | 正則規則依檔案讀取順序生效（取決於 OS 目錄遍歷），不同作業系統可能導致**不同翻譯結果** |
| **修復方向** | 明確宣告 regex rule 的優先順序；對規則衝突寫單元測試 |
| **驗證方式** | 在不同 OS 環境（Linux/macOS/Windows）執行同一 rule 集，對比輸出 |

#### 【ATK-012】BOM 無法被 `lang_merge_zip_io` 的 `lstrip` 完全清除

| 欄位 | 內容 |
|------|------|
| **位置** | `translation_tool/core/lang_merge_zip_io.py:57-58` |
| **攻擊類型** | 型別 |
| **實際行為** | `.lstrip('\ufeff')` 只移除 Unicode 字元，不移除 UTF-8 位元組序列 `EF BB BF`。若 BOM 在行中（非開頭），lang parser 可能匹配失敗，整行被 quarantine |
| **修復方向** | 改用 `text.encode('utf-8-sig').decode('utf-8')` 一次處理 BOM；或用 `codecs.getdecoder('utf-8-sig')` |
| **驗證方式** | 建立含 BOM 的 .lang 檔案，確認解析正確且不進入 quarantine |

#### 【ATK-014】`_process_output` 對 tuple vs list 行為不一致

| 欄位 | 內容 |
|------|------|
| **位置** | `translation_tool/core/lm_translator_main.py:68-78` |
| **攻擊類型** | 業務邏輯 |
| **實際行為** | 若 `results` 是 tuple，直接返回不套用 `status`；若 `results` 是 list，則用傳入的 `status`。隱藏的型別切換，單元測試若只測 list output 就測不到 tuple path |
| **修復方向** | 統一 `_process_output` 回傳格式；將 tuple path 移除或明確標記為 deprecated |
| **驗證方式** | 對 `_process_output(tuple_result, "ERROR")` 呼叫，確認回傳值的 `status` 欄位 |

#### 【ATK-015】FTB Quests SNBT injector 路徑迴圈（symbolic link / junction）

| 欄位 | 內容 |
|------|------|
| **位置** | `translation_tool/plugins/ftbquests/ftbquests_snbt_inject.py:55-68` |
| **攻擊類型** | 邊界 |
| **實際行為** | Windows Junction point 視為目錄，`os.walk(followlinks=False)` 仍會追蹤，陷入無窮迴圈 |
| **修復方向** | 加入 `os.path.realpath()` 檢查是否重複走過相同目錄；對 `shutil.copytree` 加 `dirs_exist_ok=False` 並在覆蓋前例外 |
| **驗證方式** | 在測試環境建立迴圈目錄結構，確認 pipeline 不會 hang |

#### 【ATK-016】`icon_preview_view.py` 直接耦合翻譯層核心模組

| 欄位 | 內容 |
|------|------|
| **位置** | `app/views/icon_preview_view.py` |
| **攻擊類型** | 架構 |
| **實際行為** | 直接 import `translation_tool.core.lang_item_row` 和 `safe_json_loader`，違反分層架構 |
| **修復方向** | 透過 `app/services_impl/` 包裝業務邏輯；UI 層只允許 import `app.*` + `flet` + 標準庫 |
| **驗證方式** | 確認 `app/views/` 下所有模組的 import 靜態分析是否乾淨 |

---

### P3 Low（程式碼品質，不影響正確性）

#### 【ATK-018】`ftb_translator_export.py` 與 `ftb_translator_template.py` 確認是 Dead Code

| 欄位 | 內容 |
|------|------|
| **位置** | `translation_tool/core/ftb_translator_export.py`、`ftb_translator_template.py` |
| **攻擊類型** | 業務邏輯 |
| **實際行為** | Dependency Mapper 確認這兩個模組無任何外部靜態 import caller，且 `lm_translator_*` 系列模組也無外部 import。整個 `ftb_translator_*` 系列可能是已遷移到 `lang_merge_*` 體系後的殘留 dead code |
| **修復方向** | 確認是否有實際使用者依賴這些功能；若確認閒置，刪除並在 CHANGELOG 註記 |
| **驗證方式** | Grep 全域 `import ftb_translator_export` 和 `import ftb_translator_template` |

#### 【ATK-019（補充）】`app\ui\components.py` 和 `quick_jump.py` BOM 檔案問題延伸

| 欄位 | 內容 |
|------|------|
| **說明** | BOM 問題可能也存在於其他非 Python 檔案（JSON、設定檔），導致翻译时对含 BOM 的 mod 语言档处理异常 |
| **修復方向** | 對所有文字檔讀取路徑（`.lang`、`.json`）在解碼時統一使用 `utf-8-sig` |

---

## 最少可行修復方案（Quick Wins — 選 5 件最優先）

選擇標準：Critical 必選，再選直接影響使用者體驗的 High。

| 優先序 | 問題 | 選擇理由 |
|--------|------|----------|
| 1 | **ATK-002** BOM 檔案 | Critical；直接阻擋程式啟動；修復代價極低（去 BOM） |
| 2 | **ATK-001** 參數順序錯亂 | Critical；翻譯邏輯完全錯誤；影響所有翻譯工作 |
| 3 | **ATK-013** config 無驗證 | High；使用者改錯設定就 crash；需引入 schema 驗證 |
| 4 | **ATK-003** text_processor race | High；多執行緒翻譯結果錯亂；需改用 `threading.local()` |
| 5 | **ATK-017** page.update 跨執行緒 | High；導致 UI 崩潰；需加 try 包圍或生命週期檢查 |

---

## 測試補強路線圖

| 優先序 | 測試類型 | 對應問題 |
|--------|----------|----------|
| 1 | 單元測試：`_translate_batch_smart` dry_run + export_cache_only | ATK-001 |
| 2 | 單元測試：錯誤 config 格式（字串 keys、數字 initial_batch_size） | ATK-013 |
| 3 | 單元測試：多執行緒 text_processor 不同 rules 不互相污染 | ATK-003 |
| 4 | 整合測試：含 BOM 的 .lang 檔案解析 | ATK-012 + ATK-002 |
| 5 | Mock 測試：view 卸載後 page.update 不崩潰 | ATK-017 + ATK-004 |
| 6 | 單元測試：自訂前綴 ZIP 的路徑輸出 | ATK-007 |
| 7 | 單元測試：ZIP bomb 保護（壓縮比 > 1000 倍時拋例外） | ATK-010 |
| 8 | 整合測試：daemon thread crash 後 SQLite WAL recovery | ATK-011 |
| 9 | API key 輪替多執行緒安全測試 | ATK-009 |
| 10 | 單元測試：`ftb_translator_export/template` 是否有任何 caller（黑盒） | ATK-018 |

---

## 附錄

### A：完整模組清單

已存於：`C:\Users\admin\Desktop\minecraft_translator_flet\__code_reader_report.txt`
（共 3,201 行，每個模組一張 Markdown 表格）

### B：Attacker 完整發現（20 張攻擊卡）

見上方「程式邏輯問題清單」章節（全部 18 個 ATK-001 至 ATK-019，含裁決結果）。

### C：Phase 1 數據

- **Code Reader**：`__code_reader_report.txt`（175 模組掃描結果）
- **Dependency Mapper**：（已整合進本報告「專案架構概覽」章節）

### D：無法驗證的發現（需要 runtime 環境）

| 發現 | 說明 |
|------|------|
| ATK-009 API key 輪替 | 需要實際 API 429 錯誤才能重現 |
| ATK-010 ZIP bomb | 需要構造惡意 ZIP 檔案 |
| ATK-011 WAL corruption | 需要 daemon crash 模擬 |
| ATK-015 路徑迴圈 | 需要 Windows Junction 環境 |

---

*報告產出：Review Claw | 稽核日期：2026-03-20*
