# 回退指令（如果有問題）

## 如果新功能有問題，執行以下指令：

### Windows PowerShell:
```powershell
cd C:\Users\admin\Desktop\minecraft_translator_flet\translation_tool\utils

# 備份目前版本
Copy-Item cache_manager.py cache_manager.py.with_search

# 還原舊版本
Copy-Item cache_manager.py.bak_20260213_230924 cache_manager.py

# 刪除新增檔案（可選）
Remove-Item ..\..\translation_tool\utils\exceptions.py
Remove-Item ..\..\translation_tool\utils\cache_search.py
```

### 執行後：
- cache_manager.py 會回到修改前的狀態
- 所有功能恢復原樣
- 新增的搜尋功能會失效，但不影響翻譯功能
