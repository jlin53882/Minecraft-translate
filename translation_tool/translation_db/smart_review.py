"""模組翻譯資料庫的本地 AI 智慧校對候選流程（facade）。

本模組刻意分離三個階段：

* 從 SQLite 讀取快照與本地上下文；
* 模型回傳經嚴格機器驗證的校對候選；
* 使用者透過 compare-and-set 交易明確套用候選。

任何模型回應都不能直接寫入翻譯。

實作依職責分在同目錄的模組，本檔只組合 ``SmartReviewMixin`` 並 re-export
既有的公開名稱：

* ``smart_review_prompt``：prompt 常數、回應 schema 與請求 payload；
* ``smart_review_validation``：strict 回應解析與逐筆驗證；
* ``smart_review_filters``：候選選取、篩選、同文去重與分頁；
* ``smart_review_context``：local context 與官方術語 evidence；
* ``smart_review_runs``：批次生命週期、候選列表與統計；
* ``smart_review_projection``：JSON／SQLite 套用紀錄對帳；
* ``smart_review_apply``：CAS 套用、拒絕、人工編輯與草稿；
* ``smart_review_provider``／``smart_review_request``／``smart_review_repair``：
  provider 呼叫、請求錯誤處置與驗證修復；
* ``smart_review_run_state``／``smart_review_run``：單次執行的帳本與批次流程。
"""

from __future__ import annotations

from translation_tool.translation_db.smart_review_apply import SmartReviewApplyMixin
from translation_tool.translation_db.smart_review_context import (
    SmartReviewContextMixin,
)
from translation_tool.translation_db.smart_review_filters import (  # noqa: F401
    SmartReviewCandidateSelectionMixin,
    _build_smart_review_selection,
    _dedupe_same_text_rows,
    _effective_review_sql,
    _smart_review_current_snapshot_sql,
)
from translation_tool.translation_db.smart_review_projection import (
    SmartReviewProjectionMixin,
    clear_smart_review_caches,
)
from translation_tool.translation_db.smart_review_prompt import (
    SMART_REVIEW_DECISIONS,
    SMART_REVIEW_DEFAULT_BATCH_SIZE,
    SMART_REVIEW_ISSUE_TYPES,
    SMART_REVIEW_MAX_BATCH_SIZE,
    SMART_REVIEW_REPAIR_SYSTEM_PROMPT,
    SMART_REVIEW_SYSTEM_PROMPT,
    SMART_REVIEW_VALIDATION_REPAIR_LIMIT,
    build_smart_review_payload,
    build_smart_review_validation_repair_payload,
    smart_review_response_schema,
)
from translation_tool.translation_db.smart_review_run import run_smart_review
from translation_tool.translation_db.smart_review_runs import SmartReviewRunStoreMixin
from translation_tool.translation_db.smart_review_validation import (
    SMART_REVIEW_MANUAL_EDIT_STATUSES,
    merge_smart_review_validation_repairs,
    parse_smart_review_response,
)


class SmartReviewMixin(
    SmartReviewCandidateSelectionMixin,
    SmartReviewContextMixin,
    SmartReviewRunStoreMixin,
    SmartReviewProjectionMixin,
    SmartReviewApplyMixin,
):
    """提供給 TranslationDB 使用的智慧校對 SQLite 操作。"""


__all__ = [
    "SMART_REVIEW_DECISIONS",
    "SMART_REVIEW_DEFAULT_BATCH_SIZE",
    "SMART_REVIEW_ISSUE_TYPES",
    "SMART_REVIEW_MANUAL_EDIT_STATUSES",
    "SMART_REVIEW_MAX_BATCH_SIZE",
    "SMART_REVIEW_REPAIR_SYSTEM_PROMPT",
    "SMART_REVIEW_SYSTEM_PROMPT",
    "SMART_REVIEW_VALIDATION_REPAIR_LIMIT",
    "SmartReviewMixin",
    "build_smart_review_payload",
    "build_smart_review_validation_repair_payload",
    "clear_smart_review_caches",
    "merge_smart_review_validation_repairs",
    "parse_smart_review_response",
    "run_smart_review",
    "smart_review_response_schema",
]
