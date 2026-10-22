# 生成文件與驗證流程

部分檔案由工具從程式碼產生，不手動編輯。每個產生器都有 `--check` 模式（只比對、不寫檔，不一致回傳 1），
並有對應測試，所以產出與原始碼不一致時本機與 CI 都會失敗。

## 產生器一覽

| 產出 | 產生命令 | 檢查命令 | 對應測試 | 何時重新產生 |
|---|---|---|---|---|
| `docs/generated/exception-inventory.md` | `python tools/gen_exception_inventory.py` | `python tools/gen_exception_inventory.py --check` | `tests/test_exception_inventory.py` | 新增、移除或修改帶 `noqa: BLE001`／`S110`／`S112` 的 `except` |
| `config.example.json` | `python tools/gen_config_example.py` | `python tools/gen_config_example.py --check` | `tests/test_config_example_generated.py` | 修改設定 schema（`translation_tool/utils/config_schema.py`） |

`docs/generated/exception-inventory.md` 不含行號，一般的程式碼移動不會讓它過期；
`tests/test_exception_inventory.py` 另有「沒有原因的 noqa 只能減少」的 ratchet。

## 文件連結檢查

`python tools/check_doc_links.py` 檢查 README、根目錄 agent 指示文件與 `docs/**/*.md` 的本地連結與錨點，
測試為 `tests/test_doc_links.py`。文件中的程式路徑與識別字另由 `tests/test_docs_code_paths.py`、
`tests/test_view_docs_identifiers.py` 檢查；刪除或改名程式碼後請執行這兩個測試。

## 提交前流程

```bash
python tools/gen_exception_inventory.py
python tools/gen_config_example.py
python -m pytest tests/test_exception_inventory.py tests/test_config_example_generated.py tests/test_doc_links.py tests/test_docs_code_paths.py tests/test_view_docs_identifiers.py -q
```

## CI

`.github/workflows/ci.yml` 的 `test` job 先執行完整 `pytest`（含上述測試），
再以獨立步驟執行 `gen_exception_inventory.py --check`、`gen_config_example.py --check` 與
`check_doc_links.py`，讓失敗訊息直接指出要重新執行哪個產生命令。
