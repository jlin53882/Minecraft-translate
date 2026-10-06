"""以真實的合併服務驗證：畫面日誌與後台日誌一致、畫面沒有重複行。"""

from __future__ import annotations

import collections
import json
import logging

from app.services_impl.pipelines.merge_service import run_merge_folder_batch_service
from app.tasks.task_session import TaskSession, tag_session


def _make_input(root):
    lang = root / "in" / "assets" / "demo" / "lang"
    lang.mkdir(parents=True)
    (lang / "zh_cn.json").write_text(
        json.dumps({"item.demo": "物品", "block.demo": "方块"}, ensure_ascii=False),
        encoding="utf-8",
    )
    (lang / "en_us.json").write_text(
        json.dumps({"item.demo": "Item", "block.demo": "Block"}), encoding="utf-8"
    )
    return root / "in", root / "out"


def test_real_merge_keeps_ui_and_backend_logs_in_sync(tmp_path, caplog):
    inp, out = _make_input(tmp_path)
    session = tag_session(TaskSession(), "E2E 合併", "merge")

    with caplog.at_level(logging.INFO):
        session.start()
        for _ in run_merge_folder_batch_service(
            str(inp), str(out), session, only_process_lang=True
        ):
            pass

    ui = [e.text for e in session.snapshot()["logs"]]
    backend = [r.getMessage() for r in caplog.records]
    assert ui, "服務沒有產生任何畫面日誌"

    # 畫面不重複顯示同一行（鏡像記錄不可回灌 UI）
    repeated = [t for t, n in collections.Counter(ui).items() if n > 1]
    assert not repeated, repeated

    # 每一行畫面日誌，後台都找得到（去掉 UI 專用的等級前綴）
    def in_backend(text: str) -> bool:
        first = text.split("\n")[0].removeprefix("[WARN] ").removeprefix("[ERROR] ")
        return any(first.strip() in line for line in backend)

    missing = [t for t in ui if not in_backend(t)]
    assert not missing, missing

    # 核心流程自己寫過的訊息，後台不會因為轉送到 UI 而重複
    analyzing = [m for m in backend if "偵測到統一包裝前綴" in m]
    assert len(analyzing) == 1

    # 任務邊界也在後台
    assert any(m.startswith("[E2E 合併] 任務開始") for m in backend)
    assert any(m.startswith("[E2E 合併] 任務結束") for m in backend)
