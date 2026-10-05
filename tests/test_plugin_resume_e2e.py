"""FTB／KubeJS／MD 的「中斷 → 重開 → 續跑」端到端測試（#164）。

走真實的服務層（``resume_task``）、各流程的真實 pipeline、真實快取層與批次迴圈；只把 API 換成
確定性的假翻譯。以 ``BaseException`` 模擬行程被強制結束，中斷後重設記憶體內的快取狀態
（等同重開 App）。每個情境都與「不中斷」的結果逐檔比對，並驗證不重翻、不漏翻。
"""

from __future__ import annotations

import contextlib
import importlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any, ClassVar

import pytest

from app.services_impl.pipelines import (
    _task_runner,
    ftb_service,
    kubejs_service,
    md_service,
)
from app.tasks.task_session import TaskSession
from translation_tool.core import (
    lm_resume,
    lm_translator_shared_loop,
    plugin_resume,
)
from translation_tool.core.lm_translator_skeleton import JsonCheckpointAdapter
from translation_tool.plugins.ftbquests import ftbquests_lmtranslator
from translation_tool.plugins.kubejs import kubejs_tooltip_lmtranslator
from translation_tool.plugins.md import md_lmtranslator
from translation_tool.utils import cache_manager, cache_store
from translation_tool.utils import config_manager as _config_manager

BATCH_SIZE = 2


class SimulatedCrash(BaseException):
    """模擬行程被強制結束（沒有任何 cleanup 機會）。"""


def _fake_translation(source: str) -> str:
    """確定性的假譯文：只含中日韓字元，快取層才會把它判定為「已完整翻譯」而命中。"""
    return "譯" + "".join(
        chr(0x4E00 + (ord(c) * 7) % 400) if not c.isspace() else c for c in source
    )


class Runtime:
    """隔離的資料根目錄、設定、快取與假翻譯；記錄每一次 API 呼叫送了哪些項目。"""

    def __init__(self, tmp_path: Path, monkeypatch) -> None:
        self.tmp = tmp_path
        self.monkeypatch = monkeypatch
        self.calls: list[list[str]] = []
        self.crash_at_call: int | None = None
        # 「快取已 durable、輸出已刷新，但 checkpoint 還沒更新」的中斷點：
        # 共用迴圈每批順序是 cache_save → on_batch_flushed → on_batch_checkpoint，
        # 在第 N 次 checkpoint callback 進入時（尚未寫入）強制結束，正好落在該視窗。
        self.crash_at_checkpoint: int | None = None
        self.checkpoint_calls = 0
        self.config = {
            "translator": {
                "cache_directory": str(tmp_path / "cache"),
                "enable_cache_saving": True,
                "parallel_execution_workers": 1,
            },
            "lm_translator": {"rate_limit": {"sleep_seconds_between_batches": 0.0}},
        }
        original = _config_manager.load_config
        for module in list(sys.modules.values()):
            if getattr(
                module, "load_config", None
            ) is original and module.__name__.startswith("translation_tool"):
                monkeypatch.setattr(module, "load_config", lambda *a, **k: self.config)
        monkeypatch.setattr(_config_manager, "load_config", lambda *a, **k: self.config)
        monkeypatch.setenv("MCT_DATA_DIR", str(tmp_path / "data"))
        # 不在測試中重新設定全域 logging
        monkeypatch.setattr(_task_runner, "ensure_pipeline_logging", lambda: None)
        monkeypatch.setattr(
            lm_translator_shared_loop,
            "_get_default_batch_size",
            lambda *_a, **_k: BATCH_SIZE,
        )
        monkeypatch.setattr(
            lm_translator_shared_loop, "select_batch_size", lambda r, p, n, c: n
        )

        real_call = JsonCheckpointAdapter.__call__

        def checkpoint_with_fault(adapter, state):
            self.checkpoint_calls += 1
            if self.crash_at_checkpoint == self.checkpoint_calls:
                raise SimulatedCrash
            return real_call(adapter, state)

        monkeypatch.setattr(JsonCheckpointAdapter, "__call__", checkpoint_with_fault)

    def install_fake_translator(self, module) -> None:
        def fake(batch, total=None):
            if (
                self.crash_at_call is not None
                and len(self.calls) + 1 == self.crash_at_call
            ):
                raise SimulatedCrash
            self.calls.append([it["source_text"] for it in batch])
            return (
                [{**it, "text": _fake_translation(it["source_text"])} for it in batch],
                "DONE",
            )

        self.monkeypatch.setattr(module, "translate_batch_smart", fake)
        self.monkeypatch.setattr(module, "validate_api_keys", lambda: None)

    def restart_app(self) -> None:
        """等同重開 App：記憶體內的快取與進行中的任務登記全部丟掉（磁碟上的保留）。"""
        cache_store.reset_runtime_state(cache_manager.CACHE_TYPES)
        with plugin_resume._ACTIVE_LOCK:
            plugin_resume._ACTIVE.clear()
        self.calls = []
        self.crash_at_call = None
        self.crash_at_checkpoint = None
        self.checkpoint_calls = 0

    def wipe_cache_and_markers(self) -> None:
        shutil.rmtree(self.tmp / "cache", ignore_errors=True)
        shutil.rmtree(self.tmp / "data", ignore_errors=True)
        self.restart_app()

    def sent(self) -> list[str]:
        return [text for batch in self.calls for text in batch]


@pytest.fixture
def rt(tmp_path, monkeypatch):
    runtime = Runtime(tmp_path, monkeypatch)
    cache_store.reset_runtime_state(cache_manager.CACHE_TYPES)
    yield runtime
    cache_store.reset_runtime_state(cache_manager.CACHE_TYPES)
    with plugin_resume._ACTIVE_LOCK:
        plugin_resume._ACTIVE.clear()


def _read_tree(root: Path, pattern: str = "*") -> dict[str, str]:
    """輸出檔案內容；``translation_map.*`` 是報表（cache_hit 欄位本來就會因續跑而不同），不比對。"""

    def content(path: Path) -> str:
        text = path.read_text(encoding="utf-8")
        if path.suffix != ".json":
            return text
        try:
            data = json.loads(text)
        except ValueError:
            return text
        # MD 的待翻譯／譯文 JSON 帶有 *_global 統計欄位（快取命中數等），續跑後本來就會不同
        return json.dumps(_without_stats(data), ensure_ascii=False, sort_keys=True)

    return {
        p.relative_to(root).as_posix(): content(p)
        for p in sorted(root.rglob(pattern))
        if p.is_file() and not p.name.startswith("translation_map")
    }


def _without_stats(value):
    if isinstance(value, dict):
        return {
            k: _without_stats(v)
            for k, v in value.items()
            if not str(k).endswith("_global")
        }
    if isinstance(value, list):
        return [_without_stats(v) for v in value]
    return value


# ------------------------------------------------------------------- 三個流程


class Flow:
    """一個流程的差異：建立輸入、執行服務、輸出位置、可被關掉的步驟。"""

    kind: str
    module = None  # 被換成假翻譯的 plugin 模組
    default_options: ClassVar[dict[str, Any]]
    edit_marker: tuple[
        str, str, str
    ]  # (相對檔案, 舊字串, 新字串)：改動輸入以讓指紋改變
    inject_option: str

    def make_input(self, root: Path) -> Path:
        raise NotImplementedError

    def run(self, rt: Runtime, input_dir: Path, *, session=None, **overrides):
        raise NotImplementedError

    def output_root(self, input_dir: Path) -> Path:
        return input_dir.parent / "out"

    # 流程中「翻譯之前」的步驟（抽取／清理）；測試用它模擬在翻譯開始前被強制結束
    early_step: ClassVar[tuple[str, str]]
    cache_type: ClassVar[str]

    @contextlib.contextmanager
    def early_step_patch(self, monkeypatch, replacement=None):
        """在 ``with`` 範圍內把「翻譯之前的步驟」換成會強制結束（或自訂）的版本，離開後還原。"""
        module_path, name = self.early_step
        module = importlib.import_module(module_path)

        def crash(*_a, **_k):
            raise SimulatedCrash

        with monkeypatch.context() as scoped:
            scoped.setattr(module, name, replacement or crash)
            yield


class FtbFlow(Flow):
    kind = "ftbquests"
    cache_type = "ftbquests"
    early_step = ("translation_tool.core.ftb_translator", "clean_ftbquests_from_raw")
    module = ftbquests_lmtranslator
    default_options: ClassVar[dict[str, Any]] = {
        "step_export": True,
        "step_clean": True,
        "step_translate": True,
        "step_inject": True,
        "write_new_cache": True,
    }
    edit_marker = (
        "config/ftbquests/quests/alpha.snbt",
        "Title alpha 0",
        "Edited title",
    )
    inject_option = "step_inject"

    def make_input(self, root: Path) -> Path:
        quests_root = root / "config" / "ftbquests" / "quests"
        (quests_root / "lang").mkdir(parents=True)
        (quests_root / "lang" / "en_us.snbt").write_text(
            '{ quest.t0: "English title 0" }', encoding="utf-8"
        )
        for chapter in ("alpha", "beta"):
            quests = " ".join(
                f'{{ id: "{chapter}{i}", title: "Title {chapter} {i}", '
                f'description: ["First line {chapter} {i}", "Second line {chapter} {i}"] }}'
                for i in range(5)
            )
            (quests_root / f"{chapter}.snbt").write_text(
                f'{{ id: "{chapter}", quests: [ {quests} ] }}', encoding="utf-8"
            )
        return root

    def run(self, rt, input_dir, *, session=None, **overrides):
        session = session or TaskSession()
        kwargs = {
            "directory_path": str(input_dir),
            "session": session,
            "output_dir": str(self.output_root(input_dir)),
            "dry_run": False,
            **self.default_options,
        }
        kwargs.update(overrides)
        ftb_service.run_ftb_translation_service(**kwargs)
        return session


class KubejsFlow(Flow):
    kind = "kubejs"
    cache_type = "kubejs"
    early_step = ("translation_tool.core.kubejs_translator", "step1_extract_and_clean")
    module = kubejs_tooltip_lmtranslator
    default_options: ClassVar[dict[str, Any]] = {
        "step_extract": True,
        "step_translate": True,
        "step_inject": True,
        "write_new_cache": True,
    }
    edit_marker = ("kubejs/client_scripts/test.js", "Scene text 0", "Edited scene")
    inject_option = "step_inject"

    def make_input(self, root: Path) -> Path:
        scripts = root / "kubejs" / "client_scripts"
        scripts.mkdir(parents=True)
        calls = "\n".join(f"scene.text('scene', 'Scene text {i}')" for i in range(8))
        (scripts / "test.js").write_text(
            calls + "\nItemEvents.tooltip(event => {\n"
            "  event.add('minecraft:stone', [Text.literal('Tooltip text')]);\n"
            "});",
            encoding="utf-8",
        )
        return root / "kubejs"

    def run(self, rt, input_dir, *, session=None, **overrides):
        session = session or TaskSession()
        kwargs = {
            "input_dir": str(input_dir),
            "session": session,
            "output_dir": str(self.output_root(input_dir)),
            "dry_run": False,
            **self.default_options,
        }
        kwargs.update(overrides)
        kubejs_service.run_kubejs_tooltip_service(**kwargs)
        return session


class MdFlow(Flow):
    kind = "md"
    cache_type = "md"
    early_step = ("translation_tool.core.md_translation_assembly", "step1_extract")
    module = md_lmtranslator
    default_options: ClassVar[dict[str, Any]] = {
        "step_extract": True,
        "step_translate": True,
        "step_inject": True,
        "write_new_cache": True,
        "lang_mode": "non_cjk_only",
    }
    edit_marker = ("docs/en_us/intro.md", "Paragraph number 0", "Edited paragraph")
    inject_option = "step_inject"

    def make_input(self, root: Path) -> Path:
        docs = root / "docs" / "en_us"
        docs.mkdir(parents=True)
        paragraphs = "\n\n".join(
            f"Paragraph number {i} of the manual." for i in range(8)
        )
        (docs / "intro.md").write_text(f"# Intro\n\n{paragraphs}\n", encoding="utf-8")
        return root

    def run(self, rt, input_dir, *, session=None, **overrides):
        session = session or TaskSession()
        kwargs = {
            "input_dir": str(input_dir),
            "session": session,
            "output_dir": str(self.output_root(input_dir)),
            "dry_run": False,
            **self.default_options,
        }
        kwargs.update(overrides)
        md_service.run_md_translation_service(**kwargs)
        return session


FLOWS = [FtbFlow(), KubejsFlow(), MdFlow()]


@pytest.fixture(params=FLOWS, ids=lambda f: f.kind)
def flow(request, rt, tmp_path):
    f = request.param
    rt.install_fake_translator(f.module)
    f.input_dir = f.make_input(tmp_path / "pack")
    return f


def _marker(flow) -> Path:
    return plugin_resume.marker_path(flow.kind)


def _baseline(rt, flow):
    """不中斷、乾淨快取的完整結果與送出的項目。"""
    flow.run(rt, flow.input_dir)
    out = _read_tree(flow.output_root(flow.input_dir))
    sent = rt.sent()
    rt.wipe_cache_and_markers()
    shutil.rmtree(flow.output_root(flow.input_dir), ignore_errors=True)
    return out, sent


class TestPluginResume:
    def test_uninterrupted_run_translates_and_leaves_no_marker(self, rt, flow):
        session = flow.run(rt, flow.input_dir)

        assert not session.error
        assert len(rt.sent()) > 2 * BATCH_SIZE, "測試資料必須產生多個批次"
        assert not _marker(flow).exists()

    @pytest.mark.parametrize("crash_call", [1, 2, 3])
    def test_crash_then_resume_translates_only_the_rest(self, rt, flow, crash_call):
        expected, all_sent = _baseline(rt, flow)

        rt.crash_at_call = crash_call
        with pytest.raises(SimulatedCrash):
            flow.run(rt, flow.input_dir)
        done_before = rt.sent()
        assert len(done_before) == (crash_call - 1) * BATCH_SIZE
        assert _marker(flow).exists(), "中斷後要有續跑標記（含第一次 API 呼叫就被中斷）"
        rt.restart_app()

        (task,) = [t for t in lm_resume.peek_interrupted_tasks() if t.kind == flow.kind]
        assert rt.calls == [], "偵測階段不得呼叫翻譯 API"
        assert lm_resume.check_resume_feasibility(task).ok
        assert rt.calls == [], "檢查階段也不得翻譯"

        session = flow.run(
            rt,
            Path(task.input_dir),
            output_dir=task.output_dir or None,
            **task.options,
        )

        resumed = rt.sent()
        assert not session.error
        # 兩次呼叫送出的項目合起來必須與不中斷時完全一致（多重集合）：
        # 多出來代表重翻、少了代表漏翻。（不能只比文字集合：KubeJS 的同一段文字可出現在不同路徑。）
        assert sorted(done_before + resumed) == sorted(all_sent), "不重翻、不漏翻"
        assert _read_tree(flow.output_root(flow.input_dir)) == expected
        assert not _marker(flow).exists(), "完成後清除標記"

    def test_crash_before_translation_even_starts_is_still_resumable(
        self, rt, flow, monkeypatch
    ):
        """抽取／清理途中被強制結束：磁碟上已經有初始標記，重開後偵測得到並能完整續跑。"""
        expected, all_sent = _baseline(rt, flow)
        with flow.early_step_patch(monkeypatch), pytest.raises(SimulatedCrash):
            flow.run(rt, flow.input_dir)

        assert rt.calls == [], "還沒有任何 API 呼叫"
        assert _marker(flow).exists(), "第一個批次之前就必須有標記"
        rt.restart_app()
        (task,) = [t for t in lm_resume.peek_interrupted_tasks() if t.kind == flow.kind]
        assert lm_resume.check_resume_feasibility(task).ok
        assert rt.calls == []

        session = flow.run(
            rt, Path(task.input_dir), output_dir=task.output_dir or None, **task.options
        )

        assert not session.error
        assert sorted(rt.sent()) == sorted(all_sent), "不重翻、不漏翻"
        assert _read_tree(flow.output_root(flow.input_dir)) == expected
        assert not _marker(flow).exists()

    def test_the_initial_marker_exists_while_the_pipeline_runs_its_early_steps(
        self, rt, flow, monkeypatch
    ):
        seen: list[dict] = []

        def probe(*_a, **_k):
            seen.append(json.loads(_marker(flow).read_text("utf-8")))
            raise SimulatedCrash

        with (
            flow.early_step_patch(monkeypatch, replacement=probe),
            pytest.raises(SimulatedCrash),
        ):
            flow.run(rt, flow.input_dir)

        (data,) = seen
        assert data["status"] == "STARTED" and data["completed_count"] == 0
        assert data["kind"] == flow.kind and data["input_dir"] == str(flow.input_dir)

    @pytest.mark.parametrize("crash_checkpoint", [1, 2])
    def test_crash_after_cache_is_durable_but_before_the_checkpoint_updates(
        self, rt, flow, crash_checkpoint
    ):
        """cache fsync 完成、輸出已刷新，checkpoint 還沒寫入就被強制結束（freshness 邊界）。

        標記只是「沒做完」的記錄、不是續跑位置：它記的進度落後於 durable 快取，重開後仍必須
        靠「重新抽取＋快取分流」讓那批直接命中，不得再送 API。
        """
        expected, all_sent = _baseline(rt, flow)

        rt.crash_at_checkpoint = crash_checkpoint
        with pytest.raises(SimulatedCrash):
            flow.run(rt, flow.input_dir)
        done_before = rt.sent()
        assert len(done_before) == crash_checkpoint * BATCH_SIZE
        marker = json.loads(_marker(flow).read_text("utf-8"))
        rt.restart_app()

        durable = len(cache_manager.get_cache_dict_ref(flow.cache_type))
        assert durable == crash_checkpoint * BATCH_SIZE, (
            "該批的譯文已經 durable 地存在快取"
        )
        assert marker["completed_count"] < durable, (
            "標記落後於 durable 快取（還沒更新到這一批）"
        )
        (task,) = [t for t in lm_resume.peek_interrupted_tasks() if t.kind == flow.kind]
        assert lm_resume.check_resume_feasibility(task).ok
        assert rt.calls == [], "偵測與檢查階段不得呼叫翻譯 API"

        session = flow.run(
            rt, Path(task.input_dir), output_dir=task.output_dir or None, **task.options
        )

        resumed = rt.sent()
        assert not session.error
        assert sorted(done_before + resumed) == sorted(all_sent), (
            "durable 快取裡的那批不得再送 API；其餘也不得漏翻"
        )
        assert _read_tree(flow.output_root(flow.input_dir)) == expected
        assert not _marker(flow).exists(), "完成後清除標記"

    def test_marker_describes_the_task_and_replays_the_options(self, rt, flow):
        rt.crash_at_call = 2
        with pytest.raises(SimulatedCrash):
            flow.run(
                rt,
                flow.input_dir,
                **{flow.inject_option: False, "write_new_cache": False},
            )

        data = json.loads(_marker(flow).read_text("utf-8"))

        assert data["version"] == 2 and data["kind"] == flow.kind
        assert data["input_dir"] == str(flow.input_dir)
        assert data["output_dir"] == str(flow.output_root(flow.input_dir))
        assert data["options"] == {
            **flow.default_options,
            flow.inject_option: False,
            "write_new_cache": False,
        }
        assert data["fingerprint"] and data["completed_count"] >= 1

    def test_input_changed_after_the_crash_cannot_resume(self, rt, flow):
        rt.crash_at_call = 2
        with pytest.raises(SimulatedCrash):
            flow.run(rt, flow.input_dir)
        rt.restart_app()
        rel, old, new = flow.edit_marker
        target = (
            flow.input_dir.parent / rel
            if flow.kind != "ftbquests"
            else flow.input_dir / rel
        )
        if not target.exists():
            target = flow.input_dir / rel
        text = target.read_text("utf-8")
        assert old in text, "測試資料要包含被改動的字串"
        target.write_text(text.replace(old, new), encoding="utf-8")

        (task,) = [t for t in lm_resume.peek_interrupted_tasks() if t.kind == flow.kind]
        check = lm_resume.check_resume_feasibility(task)

        assert check.ok is False and "不同" in check.reason

    def test_dry_run_and_translate_disabled_never_touch_the_marker(self, rt, flow):
        rt.crash_at_call = 2
        with pytest.raises(SimulatedCrash):
            flow.run(rt, flow.input_dir)
        before = _marker(flow).read_text("utf-8")
        rt.restart_app()

        flow.run(rt, flow.input_dir, dry_run=True)
        flow.run(rt, flow.input_dir, step_translate=False)

        assert _marker(flow).read_text("utf-8") == before

    def test_cancel_keeps_the_marker(self, rt, flow):
        session = TaskSession()
        fake = flow.module.translate_batch_smart

        def cancelling(batch, total=None):
            result = fake(batch, total)
            session.request_cancel()  # 第一批完成後使用者按取消
            return result

        rt.monkeypatch.setattr(flow.module, "translate_batch_smart", cancelling)

        flow.run(rt, flow.input_dir, session=session)

        assert _marker(flow).exists()

    def test_a_failed_loop_keeps_the_marker(self, rt, flow):
        fake = flow.module.translate_batch_smart
        calls = {"n": 0}

        def failing(batch, total=None):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("API exploded")
            return fake(batch, total)

        rt.monkeypatch.setattr(flow.module, "translate_batch_smart", failing)

        flow.run(rt, flow.input_dir)  # 迴圈內的例外讓該迴圈以 FAILED 結束

        assert _marker(flow).exists(), "迴圈沒有 DONE，標記必須保留"

    def test_discard_clears_only_this_flows_marker(self, rt, flow):
        rt.crash_at_call = 2
        with pytest.raises(SimulatedCrash):
            flow.run(rt, flow.input_dir)
        other = plugin_resume.marker_path("someother")
        other.parent.mkdir(parents=True, exist_ok=True)
        other.write_text("{}", encoding="utf-8")
        (task,) = [t for t in lm_resume.peek_interrupted_tasks() if t.kind == flow.kind]

        lm_resume.discard_interrupted_task(task)

        assert not _marker(flow).exists()
        assert other.exists()


class TestFtbMultiFile:
    """FTB 逐檔案翻譯：第一個檔案的迴圈 DONE 時不能把整個任務的標記清掉。"""

    def test_the_marker_survives_the_first_file_finishing(self, rt, tmp_path):
        flow = FtbFlow()
        rt.install_fake_translator(flow.module)
        flow.input_dir = flow.make_input(tmp_path / "pack")
        seen: list[bool] = []
        fake = flow.module.translate_batch_smart

        def probing(batch, total=None):
            result = fake(batch, total)
            seen.append(_marker(flow).exists())
            return result

        rt.monkeypatch.setattr(flow.module, "translate_batch_smart", probing)

        flow.run(rt, flow.input_dir)

        # 從第二次呼叫起（前一批已 durable 並寫過標記）標記必須存在，直到整個任務結束才清除
        assert len(seen) > 2 * BATCH_SIZE and all(seen[1:])
        assert not _marker(flow).exists()
