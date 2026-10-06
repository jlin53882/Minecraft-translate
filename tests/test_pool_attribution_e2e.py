"""真實服務（資料夾合併，核心流程用執行緒池處理各模組）：池內後台記錄帶著任務歸屬。"""

from __future__ import annotations

import json
import logging

from app.services_impl.pipelines.merge_service import run_merge_folder_batch_service
from app.tasks.task_session import TaskSession
from translation_tool.utils import ui_mirror


class _Collector(logging.Handler):
    """記下每筆後台記錄是在哪條執行緒、以哪個任務歸屬寫出的（emit 與寫 log 的呼叫端同一條執行緒）。"""

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.rows: list[tuple[str, object, str]] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.rows.append(
            (record.threadName or "", ui_mirror.current_task(), record.getMessage())
        )


def _make_input(root, count=8):
    for i in range(count):
        lang = root / "assets" / f"mod{i}" / "lang"
        lang.mkdir(parents=True)
        (lang / "zh_cn.json").write_text(
            json.dumps({"k": "值"}, ensure_ascii=False), encoding="utf-8"
        )
        (lang / "en_us.json").write_text(json.dumps({"k": "v"}), encoding="utf-8")
    return root


def test_records_written_by_pool_workers_carry_the_task_attribution(tmp_path):
    inp = _make_input(tmp_path / "in")
    session = TaskSession(name="池內歸屬")
    session.start()
    collector = _Collector()
    root = logging.getLogger()
    root.addHandler(collector)
    previous_level = root.level
    root.setLevel(logging.INFO)
    try:
        for _ in run_merge_folder_batch_service(
            str(inp), str(tmp_path / "out"), session, only_process_lang=True
        ):
            pass
    finally:
        root.removeHandler(collector)
        root.setLevel(previous_level)

    pool_rows = [r for r in collector.rows if r[0].startswith("ThreadPoolExecutor")]
    assert pool_rows, "核心流程沒有在執行緒池內寫任何後台記錄，測試失去意義"
    unattributed = [r for r in pool_rows if r[1] != session.task_id]
    assert not unattributed, unattributed[:3]


def test_two_real_merges_in_parallel_threads_keep_their_attribution_separate(tmp_path):
    """兩個真實合併同時在各自的執行緒跑：池內記錄各自屬於自己的任務，不會混在一起。"""
    import threading

    sessions = {}
    errors = []

    def run(name):
        try:
            inp = _make_input(tmp_path / name / "in", count=6)
            session = TaskSession(name=name)
            session.start()
            sessions[name] = session
            for _ in run_merge_folder_batch_service(
                str(inp), str(tmp_path / name / "out"), session, only_process_lang=True
            ):
                pass
        except Exception as exc:  # noqa: BLE001 - 測試執行緒：收集後在主執行緒斷言
            errors.append(exc)

    collector = _Collector()
    root = logging.getLogger()
    root.addHandler(collector)
    previous_level = root.level
    root.setLevel(logging.INFO)
    try:
        threads = [threading.Thread(target=run, args=(n,)) for n in ("甲", "乙")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        root.removeHandler(collector)
        root.setLevel(previous_level)

    assert not errors, errors
    ids = {s.task_id for s in sessions.values()}
    assert len(ids) == 2
    pool_rows = [r for r in collector.rows if r[0].startswith("ThreadPoolExecutor")]
    assert pool_rows
    # 池內記錄的歸屬一定是兩個任務其中之一（不是 None，也不會是別的值）
    assert {r[1] for r in pool_rows} <= ids
    # 兩個任務的模組名稱不重疊判斷不了時，至少要兩個任務都有池內記錄
    assert {r[1] for r in pool_rows} == ids
