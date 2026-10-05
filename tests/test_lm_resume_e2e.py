"""機器翻譯「中斷 → 重開 → 續跑」端到端測試（#151）。

用真實的掃描、抽取、快取層、批次迴圈與輸出寫入，只把 API 換成確定性的假翻譯。
以 BaseException 模擬行程被強制結束（不會被 ``except Exception`` 吞掉），中斷後
重設記憶體內的快取狀態（等同重開 App），再重新執行並與「不中斷」的結果逐檔比對。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from translation_tool.core import (
    lm_resume,
    lm_translator,
    lm_translator_shared_loop,
)
from translation_tool.utils import cache_manager, cache_store
from translation_tool.utils import config_manager as _config_manager

BATCH_SIZE = 2


class SimulatedCrash(BaseException):
    """模擬行程被強制結束（沒有任何 cleanup 機會）。"""


def _lang_entries(prefix: str, count: int) -> dict[str, str]:
    return {f"{prefix}.key{i:02d}": f"Source {prefix} {i}" for i in range(count)}


@pytest.fixture
def env(tmp_path, monkeypatch):
    """隔離的輸入資料夾、設定、checkpoint 與快取根目錄。"""
    input_root = tmp_path / "input"
    for modid, count in (("moda", 5), ("modb", 4)):
        f = input_root / "assets" / modid / "lang" / "en_us.json"
        f.parent.mkdir(parents=True)
        f.write_text(
            json.dumps(_lang_entries(modid, count), ensure_ascii=False),
            encoding="utf-8",
        )

    config = {
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
            monkeypatch.setattr(module, "load_config", lambda: config)
    monkeypatch.setattr(_config_manager, "load_config", lambda: config)

    monkeypatch.setattr(
        lm_translator, "CHECKPOINT_FILE", str(tmp_path / "logs" / "checkpoint.json")
    )
    monkeypatch.setattr(lm_translator, "validate_api_keys", lambda: None)
    monkeypatch.setattr(
        lm_translator_shared_loop,
        "_get_default_batch_size",
        lambda *_a, **_k: BATCH_SIZE,
    )
    monkeypatch.setattr(
        lm_translator_shared_loop, "select_batch_size", lambda r, p, n, c: n
    )

    class Env:
        pass

    e = Env()
    e.tmp = tmp_path
    e.input = input_root
    e.config = config
    e.calls: list[list[str]] = []
    e.crash_at_translate_call: int | None = None
    e.crash_at_checkpoint_call: int | None = None
    e.checkpoint_calls = 0

    def fake_translate(batch, total=None):
        if (
            e.crash_at_translate_call is not None
            and len(e.calls) + 1 == e.crash_at_translate_call
        ):
            raise SimulatedCrash
        e.calls.append([it["path"] for it in batch])
        return (
            [{**it, "text": f"譯:{it['source_text']}"} for it in batch],
            "DONE",
        )

    monkeypatch.setattr(lm_translator, "translate_batch_smart", fake_translate)

    real_save = lm_translator.save_checkpoint

    def save_checkpoint(*args, **kwargs):
        e.checkpoint_calls += 1
        if e.crash_at_checkpoint_call == e.checkpoint_calls:
            raise SimulatedCrash
        return real_save(*args, **kwargs)

    monkeypatch.setattr(lm_translator, "save_checkpoint", save_checkpoint)

    def restart_app():
        """等同重開 App：記憶體內的快取狀態全部丟掉（磁碟上的保留）。"""
        cache_store.reset_runtime_state(cache_manager.CACHE_TYPES)
        e.calls = []
        e.checkpoint_calls = 0
        e.crash_at_translate_call = None
        e.crash_at_checkpoint_call = None

    e.monkeypatch = monkeypatch
    e.restart_app = restart_app
    restart_app()
    yield e
    # 快取層的 runtime state 是模組層級全域：不可把指向暫存資料夾的狀態留給後面的測試
    cache_store.reset_runtime_state(cache_manager.CACHE_TYPES)


def run(env, output_name: str, **kwargs) -> Path:
    out = env.tmp / output_name
    list(
        lm_translator.translate_directory_generator(str(env.input), str(out), **kwargs)
    )
    return out


def read_outputs(out: Path) -> dict[str, dict]:
    return {
        p.relative_to(out).as_posix(): json.loads(p.read_text("utf-8"))
        for p in sorted(out.rglob("en_us.json")) + sorted(out.rglob("zh_tw.json"))
    }


def baseline_outputs(env, tmp_path_factory_name: str = "baseline") -> dict[str, dict]:
    """不中斷、乾淨快取的完整結果。"""
    out = run(env, tmp_path_factory_name, write_new_cache=True)
    result = read_outputs(out)
    # 清掉本次寫入的快取與 checkpoint，讓後續情境從乾淨狀態開始
    import shutil

    shutil.rmtree(env.tmp / "cache", ignore_errors=True)
    env.restart_app()
    return result


ALL_PATHS = {
    *(f"moda.key{i:02d}" for i in range(5)),
    *(f"modb.key{i:02d}" for i in range(4)),
}


class TestInterruptAndResume:
    def test_uninterrupted_run_translates_everything_once(self, env):
        out = run(env, "out", write_new_cache=True)

        translated = [p for batch in env.calls for p in batch]
        assert sorted(translated) == sorted(ALL_PATHS)  # 不漏、不重
        outputs = read_outputs(out)
        for entries in outputs.values():
            assert all(v.startswith("譯:") for v in entries.values())
        assert not Path(lm_translator.CHECKPOINT_FILE).exists()

    @pytest.mark.parametrize("crash_call", [1, 2, 3, 5])
    def test_crash_before_a_batch_resumes_without_loss_or_retranslation(
        self, env, crash_call
    ):
        expected = baseline_outputs(env)

        env.crash_at_translate_call = crash_call
        with pytest.raises(SimulatedCrash):
            run(env, "out", write_new_cache=True)
        done_before_crash = [p for batch in env.calls for p in batch]
        assert len(done_before_crash) == (crash_call - 1) * BATCH_SIZE

        env.restart_app()
        out = run(env, "out", write_new_cache=True)

        resumed = [p for batch in env.calls for p in batch]
        assert set(resumed).isdisjoint(done_before_crash), "已完成的項目不應重翻"
        assert sorted(done_before_crash + resumed) == sorted(ALL_PATHS), "不得漏翻"
        assert read_outputs(out) == expected, "輸出必須與不中斷一致"

    @pytest.mark.parametrize("crash_checkpoint_call", [1, 3])
    def test_crash_after_cache_flush_before_checkpoint(
        self, env, crash_checkpoint_call
    ):
        """快取與輸出已落盤、checkpoint 還沒寫：重開後不可漏翻或錯位。"""
        expected = baseline_outputs(env)

        env.crash_at_checkpoint_call = crash_checkpoint_call
        with pytest.raises(SimulatedCrash):
            run(env, "out", write_new_cache=True)
        done_before_crash = [p for batch in env.calls for p in batch]

        env.restart_app()
        out = run(env, "out", write_new_cache=True)

        resumed = [p for batch in env.calls for p in batch]
        assert sorted(set(done_before_crash) | set(resumed)) == sorted(ALL_PATHS)
        assert read_outputs(out) == expected

    def test_crash_at_the_very_start_leaves_nothing_to_resume(self, env):
        expected = baseline_outputs(env)
        env.crash_at_translate_call = 1
        with pytest.raises(SimulatedCrash):
            run(env, "out", write_new_cache=True)
        env.restart_app()

        out = run(env, "out", write_new_cache=True)

        assert sorted(p for b in env.calls for p in b) == sorted(ALL_PATHS)
        assert read_outputs(out) == expected

    def test_resume_into_a_fresh_output_folder_is_complete(self, env):
        """續跑時輸出資料夾已被清掉／換掉，也要得到完整結果（不能只含後半段）。"""
        expected = baseline_outputs(env)
        env.crash_at_translate_call = 3
        with pytest.raises(SimulatedCrash):
            run(env, "out", write_new_cache=True)
        env.restart_app()

        out = run(env, "out_new", write_new_cache=True)

        assert read_outputs(out) == expected

    def test_cache_saving_disabled_still_resumes_correctly(self, env):
        """關閉快取儲存時，已完成的批次只存在輸出檔；續跑不得讓它們變回原文。"""
        env.config["translator"]["enable_cache_saving"] = False
        expected = baseline_outputs(env)

        env.crash_at_translate_call = 3
        with pytest.raises(SimulatedCrash):
            run(env, "out", write_new_cache=True)
        env.restart_app()
        out = run(env, "out", write_new_cache=True)

        assert read_outputs(out) == expected


class TestMarkerLifecycle:
    def test_marker_survives_a_crash_and_describes_the_task(self, env):
        env.crash_at_translate_call = 3
        with pytest.raises(SimulatedCrash):
            run(env, "out", write_new_cache=True, export_lang=False)

        data = json.loads(Path(lm_translator.CHECKPOINT_FILE).read_text("utf-8"))
        assert data["version"] == lm_translator.CHECKPOINT_VERSION
        assert data["input_dir"] == str(env.input)
        assert data["total"] == len(ALL_PATHS)  # 抽取總數，不受快取進度影響
        assert data["completed_count"] == 2 * BATCH_SIZE  # 已完成兩批
        assert data["write_new_cache"] is True and data["export_lang"] is False

    def test_marker_is_cleared_when_the_resumed_run_finishes(self, env):
        env.crash_at_translate_call = 2
        with pytest.raises(SimulatedCrash):
            run(env, "out", write_new_cache=True)
        assert Path(lm_translator.CHECKPOINT_FILE).exists()
        env.restart_app()

        run(env, "out", write_new_cache=True)

        assert not Path(lm_translator.CHECKPOINT_FILE).exists()

    def test_user_flow_peek_check_then_resume_with_recorded_options(self, env):
        """偵測 → 檢查 → 以標記記錄的選項續跑：結果與不中斷一致，且只翻剩餘項目。"""
        expected = baseline_outputs(env)
        env.crash_at_translate_call = 3
        with pytest.raises(SimulatedCrash):
            run(env, "out", write_new_cache=True)
        done_before = [p for b in env.calls for p in b]
        env.restart_app()

        task = lm_resume.peek_interrupted_task()
        assert task is not None
        assert env.calls == [], "偵測階段不得啟動翻譯／消耗額度"
        assert lm_resume.check_resume_feasibility(task).ok
        assert env.calls == [], "檢查階段也不得翻譯"

        out = run(
            env,
            "out",
            write_new_cache=task.write_new_cache,
            export_lang=task.export_lang,
        )

        resumed = [p for b in env.calls for p in b]
        assert set(resumed).isdisjoint(done_before)
        assert sorted(done_before + resumed) == sorted(ALL_PATHS)
        assert read_outputs(out) == expected

    def test_input_changed_after_crash_cannot_resume(self, env):
        env.crash_at_translate_call = 2
        with pytest.raises(SimulatedCrash):
            run(env, "out", write_new_cache=True)
        env.restart_app()
        edited = env.input / "assets" / "moda" / "lang" / "en_us.json"
        data = json.loads(edited.read_text("utf-8"))
        data["moda.key00"] = "Edited after the crash"
        edited.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        check = lm_resume.check_resume_feasibility(lm_resume.peek_interrupted_task())

        assert check.ok is False
        assert "不同" in check.reason

    def test_stale_marker_of_other_input_is_reported_and_discarded(
        self, env, monkeypatch
    ):
        warnings: list[str] = []
        monkeypatch.setattr(lm_translator, "log_warning", warnings.append)
        lm_translator.save_checkpoint(
            1,
            1,
            9,
            [],
            "out",
            input_dir="elsewhere",
            fingerprint="not-this-input",
            export_lang=False,
            write_new_cache=True,
        )

        run(env, "out", write_new_cache=True)

        assert any("無法接續" in w for w in warnings), "不可靜默忽略"
        assert not Path(lm_translator.CHECKPOINT_FILE).exists()

    def test_cache_saving_disabled_writes_no_marker(self, env, monkeypatch):
        """沒有快取就沒有可還原的來源：不寫標記，避免提示一個實際上救不回來的續跑。"""
        env.config["translator"]["enable_cache_saving"] = False
        env.crash_at_translate_call = 3
        with pytest.raises(SimulatedCrash):
            run(env, "out", write_new_cache=True)

        assert not Path(lm_translator.CHECKPOINT_FILE).exists()
        assert env.checkpoint_calls == 0

    def test_cache_saving_disabled_discards_existing_marker_with_warning(
        self, env, monkeypatch
    ):
        warnings: list[str] = []
        monkeypatch.setattr(lm_translator, "log_warning", warnings.append)
        env.crash_at_translate_call = 3
        with pytest.raises(SimulatedCrash):
            run(env, "out", write_new_cache=True)
        env.restart_app()
        env.config["translator"]["enable_cache_saving"] = False

        run(env, "out", write_new_cache=True)

        assert any("快取儲存已停用" in w for w in warnings)
        assert not Path(lm_translator.CHECKPOINT_FILE).exists()


class TestCheckpointNeverRunsAheadOfDurableCache:
    """checkpoint 宣稱完成的批次，快取一定要已經 durable（review：cache save 失敗不得推進 checkpoint）。"""

    def _fail_cache_save_on(self, env, call_number: int) -> list[int]:
        """第 ``call_number`` 次快取存檔失敗（回傳 False、沒有落盤，與真實的失敗行為一致）。"""
        calls: list[int] = []
        real_save = lm_translator.save_translation_cache

        def save(*args, **kwargs):
            calls.append(1)
            if len(calls) == call_number:
                return False
            return real_save(*args, **kwargs)

        env.monkeypatch.setattr(lm_translator, "save_translation_cache", save)
        return calls

    def _checkpoint(self) -> dict:
        return json.loads(Path(lm_translator.CHECKPOINT_FILE).read_text("utf-8"))

    def test_cache_save_failure_does_not_advance_the_checkpoint(self, env):
        self._fail_cache_save_on(env, 2)  # 第 2 批的快取存檔失敗

        out = env.tmp / "out"
        events = list(
            lm_translator.translate_directory_generator(
                str(env.input), str(out), write_new_cache=True
            )
        )

        assert env.checkpoint_calls == 1, "第 2 批快取沒落盤，不得寫 checkpoint"
        assert self._checkpoint()["completed_count"] == BATCH_SIZE
        assert any("FAILED" in str(e.get("log", "")) for e in events)
        # 失敗後不再繼續送後面的批次
        assert len([p for b in env.calls for p in b]) == 2 * BATCH_SIZE

    def test_add_to_cache_rejection_also_blocks_the_checkpoint(self, env):
        real_add = lm_translator.add_to_cache
        seen: list[str] = []

        def add(cache_type, key, src, dst, **kwargs):
            seen.append(key)
            if len(env.calls) == 2:  # 第 2 批的項目被快取拒絕
                return False
            return real_add(cache_type, key, src, dst, **kwargs)

        env.monkeypatch.setattr(lm_translator, "add_to_cache", add)

        run(env, "out", write_new_cache=True)

        assert env.checkpoint_calls == 1
        assert self._checkpoint()["completed_count"] == BATCH_SIZE

    def test_failed_batch_is_not_committed_and_resume_retranslates_only_it(self, env):
        """crash 前最後一個 checkpoint 與磁碟快取一致：重開後只重翻沒有 durable 的批次。"""
        expected = baseline_outputs(env)
        self._fail_cache_save_on(env, 2)
        run(env, "out", write_new_cache=True)
        committed = [p for b in env.calls[:1] for p in b]  # 只有第 1 批 durable
        env.restart_app()  # 等同重開 App：記憶體快取全部消失

        task = lm_resume.peek_interrupted_task()
        assert task is not None and task.completed == BATCH_SIZE
        assert lm_resume.check_resume_feasibility(task).ok
        out = run(env, "out", write_new_cache=True)

        resumed = [p for b in env.calls for p in b]
        assert set(resumed).isdisjoint(committed), "已 durable 的批次不得重翻"
        assert sorted(committed + resumed) == sorted(ALL_PATHS)
        assert read_outputs(out) == expected

    def test_successful_runs_still_checkpoint_every_batch(self, env):
        env.crash_at_translate_call = 4

        with pytest.raises(SimulatedCrash):
            run(env, "out", write_new_cache=True)

        assert env.checkpoint_calls == 3
        assert self._checkpoint()["completed_count"] == 3 * BATCH_SIZE
