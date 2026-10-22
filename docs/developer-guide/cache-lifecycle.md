# 模組級快取生命週期

程序內的模組級快取（lru_cache、模組層 dict／set）都把清除函式登錄到 `translation_tool/utils/cache_registry.py`。
需要重置程序內狀態時（例如測試之間）呼叫 `clear_all_caches()`；它依登錄順序呼叫每個清除函式，單一失敗只記錄警告並列在回傳值，不阻斷其他快取。

## API

| 函式 | 用途 |
|---|---|
| `register_cache(name, clear_fn)` | 登錄清除函式；同名重複登錄以最後一次為準 |
| `registered_cache_names()` | 目前已登錄的名稱（依登錄順序） |
| `clear_all_caches()` | 清除全部，回傳失敗的名稱 |
| `register_smart_review_caches()` | 呼叫時才 import 智慧校對模組，登錄其清除函式（缺少者略過）；`clear_all_caches()` 會先呼叫它 |

模組在 import 時登錄自己的快取，所以 `clear_all_caches()` 只會清除已載入模組的快取。

## 已登錄的快取

| 名稱 | 位置 | 內容 |
|---|---|---|
| `translation_db.settings.registry` | `translation_db/settings.py` `clear_registry_cache` | 自訂來源登錄表（依檔案簽章） |
| `translation_db.repository.quality_comparison` | `translation_db/repository.py` `clear_quality_comparison_cache` | 批次取代預覽的品質比對 `lru_cache(4096)` |
| `core.official_locale.index` | `core/official_locale.py` `clear_official_locale_cache` | 官方語系索引 `lru_cache` |
| `core.lang_merge_content_copy.patchouli_eff` | `core/lang_merge_content_copy.py` `clear_patchouli_eff_cache` | Patchouli 有效翻譯 fallback 快取（保留進行中的計算） |
| `utils.text_processor.conflict_warnings` | `utils/text_processor.py` `clear_conflict_warning_cache` | 已警告過的替換規則衝突簽章 |
| `utils.text_processor.replace_rules` | `utils/text_processor.py` `clear_replace_rules_cache` | 全域 `replace_rules_store` 的規則快取（`invalidate(notify=False)`，不通知訂閱者） |
| `utils.config_manager.config` | `utils/config_manager.py` `clear_config_cache` | `load_config` 共用設定快取 |
| `utils.app_paths.migrated_roots` | `utils/app_paths.py` `clear_migrated_roots` | 已做過舊版資料遷移的根目錄 |
| `utils.text_matching.normalize_term` | `utils/text_matching.py` `clear_term_normalize_cache` | 術語正規化 `lru_cache(200_000)`；`term_normalize_cache_info()` 供觀測 |
| `icon_preview.model_index_memo` | `app/views/icon_preview/icon_cache.py` `clear_model_index_memo` | JAR model index 記憶 |

## 未登錄的純函式 memo

`core/lang_merge_dict.py`、`core/lang_merge_pipeline.py` 的 `_contains_cjk_str`、`core/icon_resolver.py`、`core/pack_format_lookup.py` 的 `lru_cache` 只依輸入決定結果，沒有外部狀態，不在登錄範圍。
