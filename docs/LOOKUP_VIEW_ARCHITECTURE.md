# LookupView 架構文件

## 定位、功能、使用時機

**定位**：學名（物種名稱）翻譯的快速查詢工具，屬於翻譯前置準備工具。

**功能**：
- **單筆查詢**：輸入學名即時翻譯（背景執行緒，不阻塞 UI）
- **批次查詢**：輸入 JSON 陣列，一次查詢大量學名並顯示進度

**使用時機**：在正式翻譯流程前，先查詢學名對照表；或翻譯過程中需要確認某個學名的正確譯名。

---

## 架構圖

```
LookupView (ft.Column)
├── 單筆查詢 Card
│   ├── single_input (TextField) ──┐
│   ├── single_button (ElevatedButton)──→ single_lookup_worker()
│   └── single_result_text (Text) ◄─────── run_manual_lookup_service()
│
└── 批次查詢 Card
    ├── batch_input (TextField, JSON) ──┐
    ├── batch_result_textfield (TextField, read_only) ◄──┐
    └── batch_button ──→ batch_lookup_worker() ──────────┐
                                                          │
                                                 run_batch_lookup_service() ◄┘
```

---

## 主要 UI 元件

| 元件 | 類型 | 說明 |
|------|------|------|
| `single_input` | TextField | 單筆學名輸入框 |
| `single_button` | ElevatedButton | 觸發單筆查詢 |
| `single_result_text` | Text | 單筆查詢結果顯示，含 loading 狀態 |
| `single_progress_ring` | ProgressRing | 查詢中旋轉指示器 |
| `batch_input` | TextField | JSON 批次輸入（multiline） |
| `batch_result_textfield` | TextField | 批次結果（read_only，含進度條） |
| `batch_progress_bar` | ProgressBar | 批次進度條 |

**UI 模式**：雙 Card 佈局，單筆在上、批次在下；查詢中 disabled + ProgressRing 防止重複提交。
