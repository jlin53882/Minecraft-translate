import sys
sys.path.insert(0, 'C:/Users/admin/.openclaw/workspace/skills/claude-api/scripts')
from claude_task import run_claude_task

prompt = """你是 Claude Code，業務邏輯深度分析師。請對以下程式碼執行對抗式稽核。

## 工作目錄
C:\\Users\\admin\\Desktop\\minecraft_translator_flet

## 審查重點
1. 業務邏輯正確性：狀態機轉換、邊界條件、off-by-one 錯誤
2. Checkpoint 狀態一致性：遺失狀態、不正確的恢復邏輯
3. 錯誤處理完整性：所有 error path 是否都有適當處理
4. 文件缺口：複雜邏輯完全沒有說明
5. 測試覆蓋缺口：critical path 無測試保護
6. 隱性假設：函數依賴未文件化的前置條件
7. 翻譯正確性：批次處理、API 錯誤處理、翻譯結果驗證
8. Config 驗證：外部設定檔的類型與範圍驗證

請分析以下核心模組：
- translation_tool/core/lm_translator_main.py（翻譯主循環，translate_batch_smart_old）
- translation_tool/core/lm_translator_shared_loop.py（翻譯迴圈，translate_items_with_cache_loop）
- translation_tool/core/lm_translator_shared_cache.py（快取命中，fast_split_items_by_cache）
- translation_tool/core/lm_config_rules.py（API Key 管理與輪替）
- translation_tool/core/lm_api_client.py（Gemini API 呼叫）
- translation_tool/core/lm_response_parser.py（JSON 解析）
- translation_tool/core/lm_translator.py（翻譯入口，translate_directory_generator）
- translation_tool/utils/cache_shards.py（滾動分片管理）
- translation_tool/core/lm_translator_scan.py（檔案掃描與抽取）

請用繁體中文，用對抗式思維（想到各種錯誤情境）全面分析。
列出所有你找到的問題，格式：

## ⚡ Claude Code — Round 1｜全面初掃
### 🔧 業務邏輯問題
- [描述] | [檔名:行號]
### 📍 Checkpoint 狀態問題
- ...
### ❌ 錯誤處理缺口
- ...
### 📖 文件缺口
- ...
### 🧪 測試覆蓋缺口
- ...
### 🔒 隱性假設
- ...

用繁體中文回覆。
"""

result = run_claude_task(
    prompt=prompt,
    model='minimax-m2.7',
    timeout=300,
)
print('OK:', result.ok)
if result.text:
    print('TEXT:')
    print(result.text)
if result.error:
    print('ERROR:', result.error)
