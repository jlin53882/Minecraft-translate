"""``app.log`` 每一行標示它屬於哪個任務（``[task=名稱/識別]``）。"""

from __future__ import annotations

import json
import logging
import re
import threading

import pytest

from app.services_impl.pipelines.merge_service import run_merge_folder_batch_service
from app.tasks.task_session import TaskSession
from translation_tool.utils import ui_mirror
from translation_tool.utils.redaction import RedactingFormatter, with_task_tag
from translation_tool.utils.ui_mirror import (
    ContextThreadPoolExecutor,
    format_task_tag,
    task_scope,
)

DEFAULT_FORMAT = "%(asctime)s - %(levelname)s - [%(name)s] - %(message)s"


@pytest.fixture
def app_log(tmp_path):
    """真實的檔案 handler（與 setup_logging 相同的 RedactingFormatter）掛在 root logger。"""
    ui_mirror.install_task_record_factory()
    path = tmp_path / "app.log"
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(RedactingFormatter(DEFAULT_FORMAT))
    root = logging.getLogger()
    previous = root.level
    root.addHandler(handler)
    root.setLevel(logging.INFO)

    def lines():
        handler.flush()
        return path.read_text(encoding="utf-8").splitlines()

    yield lines
    root.removeHandler(handler)
    root.setLevel(previous)
    handler.close()


# ---------------------------------------------------------------- 格式


@pytest.mark.parametrize(
    ("fmt", "expected"),
    [
        (
            DEFAULT_FORMAT,
            "%(asctime)s - %(levelname)s - [%(name)s] - %(task_tag)s%(message)s",
        ),
        ("%(message)s", "%(task_tag)s%(message)s"),
        ("CUSTOM %(message)s", "CUSTOM %(task_tag)s%(message)s"),
        ("%(task_tag)s%(message)s", "%(task_tag)s%(message)s"),  # 已含：不重複
        ("%(levelname)s only", "%(levelname)s only"),  # 沒有 %(message)s：不動
    ],
)
def test_with_task_tag(fmt, expected):
    assert with_task_tag(fmt) == expected


def test_format_task_tag():
    assert format_task_tag(None, "名稱") == ""
    assert format_task_tag("abc123", "語系合併") == "[task=語系合併/abc123] "
    assert format_task_tag("abc123", None) == "[task=abc123] "
    assert (
        format_task_tag("abc123", "含\n換行  的名稱") == "[task=含 換行 的名稱/abc123] "
    )


def test_formatter_adds_the_tag_for_records_without_the_field():
    """工廠安裝之前建立的記錄（或第三方直接建立的 LogRecord）沒有 task_tag 欄位也不能格式化失敗。"""
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "訊息", None, None)
    assert RedactingFormatter(DEFAULT_FORMAT).format(record).endswith(" - 訊息")


# ---------------------------------------------------------------- 實際寫進檔案


def test_lines_carry_the_task_of_the_thread_that_wrote_them(app_log):
    log = logging.getLogger("core.sample")
    log.info("沒有任務")
    with task_scope("abc123", "語系合併"):
        log.info("任務內")
        with ContextThreadPoolExecutor(2) as pool:
            pool.submit(log.info, "池內").result()
    log.info("任務之後")

    lines = {
        m: line
        for m in ("沒有任務", "任務內", "池內", "任務之後")
        for line in app_log()
        if line.endswith(m)
    }
    assert "[task=" not in lines["沒有任務"] and "[task=" not in lines["任務之後"]
    assert "[task=語系合併/abc123] 任務內" in lines["任務內"]
    assert "[task=語系合併/abc123] 池內" in lines["池內"]


def test_ui_only_messages_mirrored_from_the_ui_thread_get_the_owning_tasks_tag(app_log):
    """UI 執行緒沒有任務歸屬，但替某個任務補寫後台的訊息，標籤是「該任務」的。"""
    a, b = TaskSession(name="甲"), TaskSession(name="乙")
    a.add_log("甲的畫面訊息")
    b.add_log("乙的畫面訊息")

    lines = app_log()
    line_a = next(line for line in lines if "甲的畫面訊息" in line)
    line_b = next(line for line in lines if "乙的畫面訊息" in line)
    assert f"[task=甲/{a.task_id}]" in line_a and b.task_id not in line_a
    assert f"[task=乙/{b.task_id}]" in line_b and a.task_id not in line_b


def test_lifecycle_records_are_tagged(app_log):
    session = TaskSession(name="生命週期")
    session.start()
    session.finish()

    tagged = [
        line for line in app_log() if f"[task=生命週期/{session.task_id}]" in line
    ]
    assert any(line.endswith("任務開始") for line in tagged)
    assert any("任務結束：DONE" in line for line in tagged)


def test_two_real_merges_in_parallel_every_line_names_its_own_task(app_log, tmp_path):
    """真實服務同時跑兩個合併（核心流程用執行緒池）：app.log 裡每一行只屬於自己的任務。"""
    sessions = {}
    errors = []
    gate = threading.Barrier(2)

    def make_input(root, prefix):
        for i in range(6):
            lang = root / "assets" / f"{prefix}{i}" / "lang"
            lang.mkdir(parents=True)
            (lang / "zh_cn.json").write_text(
                json.dumps({"k": "值"}, ensure_ascii=False), encoding="utf-8"
            )
            (lang / "en_us.json").write_text(json.dumps({"k": "v"}), encoding="utf-8")
        return root

    def run(name, prefix):
        try:
            inp = make_input(tmp_path / name / "in", prefix)
            session = TaskSession(name=name)
            session.start()
            sessions[name] = session
            gate.wait(timeout=10)
            for _ in run_merge_folder_batch_service(
                str(inp), str(tmp_path / name / "out"), session, only_process_lang=True
            ):
                pass
        except Exception as exc:  # noqa: BLE001 - 收集後在主執行緒斷言
            errors.append(exc)

    threads = [
        threading.Thread(target=run, args=("甲", "alpha")),
        threading.Thread(target=run, args=("乙", "beta")),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors

    id_a, id_b = sessions["甲"].task_id, sessions["乙"].task_id
    lines = app_log()
    alpha = [line for line in lines if "alpha" in line]
    beta = [line for line in lines if "beta" in line]
    assert alpha and beta
    assert all(f"/{id_a}]" in line and id_b not in line for line in alpha), alpha[:3]
    assert all(f"/{id_b}]" in line and id_a not in line for line in beta), beta[:3]
    # 池內（工作執行緒）寫的記錄也帶標籤：模組處理訊息就是在池內寫的
    assert any("處理語言模組" in line and f"/{id_a}]" in line for line in alpha)
    # 每個任務都有開始與結束
    for name, tid in (("甲", id_a), ("乙", id_b)):
        own = [line for line in lines if f"[task={name}/{tid}]" in line]
        assert any(line.endswith("任務開始") for line in own)
        assert any("任務結束" in line for line in own)
    # 標籤格式
    assert re.search(r"\[task=甲/[0-9a-f]{8}\] ", "\n".join(lines))


def test_setup_logging_installs_the_record_factory_and_a_tagging_formatter():
    """應用程式啟動的 ``setup_logging`` 必須安裝記錄工廠，並用 RedactingFormatter（自動加任務標籤）。

    （不直接呼叫它：它會清掉 root logger 的 handlers，影響 pytest 自己的 log 擷取。）
    """
    import ast
    import inspect

    from translation_tool.utils import config_manager

    source = inspect.getsource(config_manager)
    tree = ast.parse(source)
    setup = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "setup_logging"
    )
    called = {
        getattr(c.func, "id", getattr(c.func, "attr", ""))
        for c in ast.walk(setup)
        if isinstance(c, ast.Call)
    }
    assert "install_task_record_factory" in called
    assert "RedactingFormatter" in called
