# main.py 程式分析報告

> 專案：`Minecraft_translator_flet`
> 範圍：只分析 `main.py` 的啟動責任、模組依賴、結構可讀性
> 不包含：Flet UI 版面/元件優化
> 分析日期：2026-03-10

---

## 一句話結論

`main.py` 目前不是壞掉，而是**責任開始外溢**：它同時負責啟動、視圖註冊、視窗尺寸規則、主題切換、以及啟動後背景索引重建。這種寫法短期可用，但長期會讓入口檔越來越胖、越來越難維護。

---

## 目前 main.py 的角色

`main.py` 現在實際上同時扮演：

1. **Flet 應用程式入口**
2. **視圖註冊表**
3. **視窗尺寸規則表**
4. **主題切換控制點**
5. **啟動後背景任務入口**（重建搜尋索引）
6. **日誌初始化入口**

這代表它已經不只是單純 bootstrap，而是半個「總控檔」。

---

## 優點

### 1. 入口集中，找功能很快
- `main.py` 目前還是很好找入口的檔案。
- 你要看程式怎麼啟動、有哪些頁面、啟動時會做什麼，幾乎都能在這支直接看到。

### 2. 啟動後索引重建有放背景執行
- `main.py:185`
- `main.py:190`

這點是正確的。`cache_rebuild_index_service()` 用背景 thread 跑，不會讓 UI 啟動直接卡死。

### 3. 視圖切換邏輯直接、好追
- `main.py:66`
- `main.py:107`

`nav_destinations` + `content_area.content = target_view` 這種寫法雖然陽春，但可讀性高，問題很容易定位。

---

## 這次不列為問題的地方

### 註解停用頁面的實際狀態（依你的補充修正）
位置：
- `main.py:70`：**學名查詢工具**，目前是暫停功能，不列為死碼
- `main.py:71`：**預計刪除**，可列為後續清理候選，但現階段不算誤留 bug
- `main.py:76`：**先保留**，因為對應的 core 內容尚未寫好，屬於延後設計中的功能
- `main.py:77`：**傾向保留但尚未定案**，因格式種類太多，未來可能保留也可能刪除

因此這裡更精確的判定應該是：
- `70`：deferred feature（保留）
- `71`：planned removal（預計刪除）
- `76`：blocked by incomplete core（核心未完成，先保留）
- `77`：under evaluation（保留可能性高，但方案未定）

### 建議做法
不要把這四個混成同一種註解狀態，最好補上原因：

1. **保留型（70 / 76 / 77）**
   - 用註解標明原因，例如：
     - `disabled: feature paused`
     - `disabled: core not ready`
     - `disabled: format scope not decided`
2. **預計刪除型（71）**
   - 可在註解中直接寫 `planned removal`
3. **之後若要繼續長期管理**
   - 建議改成 registry 的 `enabled` / `status` 欄位，而不是只靠註解停用

你現在這種註解停用不是大問題，但這四個其實狀態不同，報告裡應該分開看。

---

## 主要結構問題

### 1. `main.py` 混了太多「啟動以外」的責任
關鍵位置：
- 視圖 import：`main.py:14-20`
- 視圖清單：`main.py:66`
- 視窗尺寸表：`main.py:79`
- 啟動任務：`main.py:181-190`

這使得 `main.py` 不是純啟動檔，而是同時管理：
- 啟動流程
- 功能註冊
- 視窗規則
- 背景任務

### 為什麼這會痛
當你未來繼續增加頁面、啟動檢查、feature toggle、啟動前驗證時，`main.py` 會越長越像神檔。

### 建議
把以下內容拆出去，但**不動 Flet UI 結構本身**：

#### 可抽出到 `app/view_registry.py`
- `nav_destinations`
- `view_window_sizes`

#### 可抽出到 `app/startup_tasks.py`
- `_rebuild_index_on_startup()`

#### `main.py` 保留的責任
- page 初始化
- 套用 theme
- 建立 file picker
- 載入 registry
- 掛上 layout
- 啟動 app

---

### 2. logging 初始化責任重複
關鍵位置：
- `main.py:9`
- `main.py:203`
- `translation_tool/utils/config_manager.py:283`
- `translation_tool/utils/config_manager.py:284`

現在的狀況是：
- `main.py` 會自己呼叫 `setup_logging(config)`
- 但 `config_manager.py` 在 import 時又會先 `load_config()` + `setup_logging(config)` 一次

### 問題
這是一種 **import side effect**：
- 只要 import `config_manager`，就可能先初始化 logging
- `main.py` 又再初始化一次

這會造成：
- 啟動責任不乾淨
- 測試 import 時容易出現副作用
- CLI / 背景腳本 / 單元測試很難控制初始化時機

### 建議
保留單一責任：
- `config_manager.py`：只提供函式
- `main.py`：決定什麼時候初始化 logging

### 具體建議
把 `config_manager.py` 底部這兩行移除：
- `config = load_config()`
- `setup_logging(config)`

這是很值得優先整理的一點。

---

### 3. `main.py` 直接依賴重量級 service facade
位置：
- `main.py:21`

```python
from app.services import cache_rebuild_index_service
```

這讓 `main.py` 啟動時直接拉進 `app.services`。
而 `app/services.py` 本身非常大，且混了大量業務流程與 service wrapper。

### 問題
入口檔不應該直接依賴重量級 service façade，否則：
- 啟動耦合過高
- 測試 main.py 時會連帶拉很多不必要依賴
- 之後要抽 CLI / 背景任務會更痛

### 建議
改成：
- `main.py` 只 import `app.startup_tasks`
- 由 `app.startup_tasks` 內部再去呼叫 service

也就是讓依賴方向變成：

`main.py -> startup_tasks -> services`

而不是：

`main.py -> services`

---

### 4. 視圖與視窗尺寸是兩份並行資料，容易未來不同步
位置：
- `main.py:66-77`
- `main.py:79-87`

現在有兩份資料：
1. `nav_destinations`
2. `view_window_sizes`

兩者靠 index 對齊。

### 問題
這種設計現在還能撐，但擴充時很容易出現：
- 新增頁面卻忘了補尺寸
- reorder 頁面後尺寸對錯頁
- 關掉某頁後 index 全部偏移

### 建議
之後可以整理成同一份 registry，例如：

```python
{
  "key": "config",
  "icon": ft.Icons.SETTINGS,
  "label": "設定",
  "view": config_view,
  "window": (1280, 960),
  "enabled": True,
}
```

這樣資料會比較穩。

---

### 5. 有少量殘留註解與雜訊
位置：
- `main.py:96` `# ??????`
- `main.py:219-222` 註解掉的 web mode

#### `# ??????`
這種註解是應該清掉的雜訊，沒有保留價值。

#### web mode 註解區塊
如果你短期不打算支援網頁版，可以先保留在文件，不要掛在主檔尾端。
如果之後會回來做，建議移到：
- `docs/notes-web-mode.md`
- 或 `main_web_example.py` 之類的示例檔

---

## 可整理項目清單

### A. 建議保留，但改用更乾淨結構
1. `main.py:70`
   - 學名查詢工具，屬於暫停功能
   - 建議保留，但註解補上原因

2. `main.py:76`
   - 對應功能先保留，因為 core 尚未完成
   - 建議標註 `core not ready`

3. `main.py:77`
   - 目前傾向保留，但格式範圍未定
   - 建議標註 `under evaluation`

4. `main.py:66-87`
   - 視圖註冊與視窗尺寸資料
   - 建議搬到 registry 模組

5. `main.py:181-190`
   - 啟動後背景索引任務
   - 建議搬到 startup_tasks 模組

### B. 建議後續清理
1. `main.py:71`
   - 你已標註為預計刪除
   - 建議等相關依賴確認後移除

2. `main.py:96`
   - `# ??????`
   - 直接刪除

3. `main.py:219-222`
   - 註解掉的 web 啟動區塊
   - 建議移出主檔

4. `translation_tool/utils/config_manager.py:283-284`
   - import side effect
   - 建議移除

---

## 建議重構順序（只針對 main.py 關聯範圍）

### 第一優先
**移除 logging import side effect**
- 影響最廣
- 但整理收益也最大
- 可以讓後續模組邊界更乾淨

### 第二優先
**把 view registry / window size table 抽出去**
- 不改 UI 行為
- 但會大幅提升 main.py 可讀性

### 第三優先
**把 startup task 抽出去**
- 降低 `main.py -> services` 的直接耦合

### 第四優先
**清除小型殘留註解與示例碼**
- 包含 `# ??????` 和 web mode 註解區塊

---

## 最後結論

`main.py` 目前最大問題不是功能錯，而是：

> **入口檔承擔了太多非入口責任。**

它現在還在可控範圍內，屬於「很好救」的狀態。最適合的方向不是大砍大改，而是做**邊界整理**：

- logging 初始化責任單一化
- registry 外移
- startup task 外移
- 保留暫停功能，但不要長期靠註解停用維持

這樣做的好處是：
- 不碰 Flet UI 結構
- 不增加 bug 風險
- 但會讓整個入口檔乾淨非常多

---

## 本檔建議狀態

- **可直接討論**：是
- **建議立刻動手改**：是，但先從 logging 初始化開始
- **風險**：低到中（只要不碰 UI layout，本段可安全整理）
