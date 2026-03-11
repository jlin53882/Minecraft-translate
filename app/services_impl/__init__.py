"""app.services_impl

此 package 是 `app/services.py` 的後續拆分落點（implementation modules）。

PR13 範圍：
- 先建立骨架與 import 路徑，讓後續 PR（PR14+）可以逐步把 services.py 的內容搬進來。
- 本輪不搬移任何既有邏輯；`app/services.py` 仍是唯一的行為實作來源。

命名理由：使用 `services_impl` 來避免與現有 `app/services.py`（module）同名衝突。

未來預期：
- 各子模組會提供與 services.py 同名的函式，並由 services.py 轉為薄 façade / re-export。
"""

from __future__ import annotations
