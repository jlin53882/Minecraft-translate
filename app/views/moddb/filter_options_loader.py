"""條目篩選選項（模組／來源）的第二階段背景載入。

搜尋等條件會讓模組／來源下拉選單的內容跟著縮小，算它要掃過整個版本（數十萬筆時數百毫秒），
比取得清單第一頁還慢。清單（與總筆數）先顯示，選項由這裡在背景補上；
同一時間只跑一個查詢，期間再來的請求只保留最新一個，過期的結果直接丟棄。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.services_impl.moddb_service import QueryCancelled, QueryInterrupt
from app.views.moddb.background_read import run_read
from translation_tool.utils.log_unit import log_debug

# 條件穩定（沒有更新的請求）這麼久之後才開始查選項：輸入中的每個字都會換條件，
# 選項下拉選單（上千個模組）重畫的成本比清單高，不為中途的條件算也不重畫。
SETTLE_SECONDS = 0.25


class FilterOptionsLoader:
    """同一時間最多一個背景查詢，結果只在仍是最新請求時交給 ``apply``。"""

    def __init__(
        self,
        page,
        load: Callable[[Any, QueryInterrupt], dict],
        apply: Callable[[Any, dict], None],
    ) -> None:
        """``load(payload, interrupt)`` 在 worker 執行；``apply(payload, snapshot)`` 在畫面執行緒執行。

        有較新的請求（或 ``cancel()``）時，進行中的查詢會被 ``interrupt`` 中斷。
        """
        self._page = page
        self._load = load
        self._apply = apply
        self._ticket = 0
        self._running = False
        self._latest: Any = None
        self._interrupt: QueryInterrupt | None = None

    @property
    def running(self) -> bool:
        """目前是否有背景查詢進行中。"""
        return self._running

    def request(self, payload: Any) -> None:
        """要求載入選項；進行中的查詢結束後會改用最新的 payload 再查一次。"""
        self._ticket += 1
        self._latest = payload
        if self._running:
            self._interrupt_running()
        else:
            self._launch()

    def cancel(self) -> None:
        """丟棄進行中與等待中的請求（頁面卸載、資料庫換掉時）。"""
        self._ticket += 1
        self._latest = None
        self._interrupt_running()

    def _interrupt_running(self) -> None:
        if self._interrupt is not None:
            self._interrupt.cancel()

    def _launch(self) -> None:
        payload = self._latest
        ticket = self._ticket
        interrupt = self._interrupt = QueryInterrupt()
        self._running = True

        def done(snapshot: dict | None, error: BaseException | None) -> None:
            self._running = False
            if ticket != self._ticket:
                if self._latest is not None:
                    self._launch()
                return
            self._latest = None
            if error is not None or snapshot is None:
                if not isinstance(error, QueryCancelled):
                    log_debug(f"Mod DB 篩選選項載入失敗：{error!r}")
                return
            self._apply(payload, snapshot)

        def work() -> dict:
            if interrupt.wait(SETTLE_SECONDS):
                raise QueryCancelled
            return self._load(payload, interrupt)

        launched = run_read(
            self._page,
            work,
            done,
            name="Mod 資料庫篩選選項載入",
            owner="moddb-entries-filter-options",
        )
        if not launched:
            self._running = False
