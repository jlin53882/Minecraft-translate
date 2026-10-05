"""圖示預覽的 Phase 進度顯示輔助（只改記錄值，賦值由 view 在 event loop 上完成）。"""


def _make_progress_callback(obj, phase: str, total: int):
    """建立 Phase 進度 callback。

    參數：
        obj: IconPreviewView 例項（需有 progress_bar, progress_text, update 方法）
        phase: Phase 顯示文字
        total: 總步數
    """

    def callback(processed: int, total: int):
        # 安全檢查：測試環境或 UI 未初始化時不拋例外
        if not hasattr(obj, "progress_text") or not hasattr(obj, "progress_bar"):
            return
        _set_progress(
            obj,
            f"[{phase}] {processed} / {total}",
            processed / total if total > 0 else 0,
        )

    return callback


def _set_progress(obj, text: str, value: float, *, visible: bool | None = None) -> None:
    """更新進度顯示。

    掃描在背景執行緒時不可直接改 Flet 控制項（#114 的 worker-thread 契約）：view 提供
    ``_set_progress`` 時只記下最新值，實際賦值與刷新由 event loop 上的 ``_refresh_progress`` 完成；
    沒有這個方法的物件（測試替身）維持直接賦值。
    """
    setter = getattr(type(obj), "_set_progress", None)
    if setter is not None:
        setter(obj, text, value, visible)
        return
    obj.progress_text.value = text
    obj.progress_bar.value = value
    if visible is not None:
        obj.progress_bar.visible = visible
    _refresh_progress(obj)


def _refresh_progress(obj) -> None:
    """刷新進度顯示；掃描在背景執行緒時改由 view 節流後交給 event loop。"""
    refresh = getattr(obj, "_refresh_progress", None)
    if refresh is not None:
        refresh()
    else:
        obj.update()


def _show_progress_phase(obj, phase: str, current: int, total: int):
    """更新 Phase 進度顯示（並墊底一次）。"""
    # 安全檢查：測試環境或 UI 未初始化時不拋例外
    if not hasattr(obj, "progress_text") or not hasattr(obj, "progress_bar"):
        return
    _set_progress(
        obj,
        f"[{phase}] {current} / {total}",
        current / total if total > 0 else 0,
        visible=True,
    )
