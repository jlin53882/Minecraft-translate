"""中斷續跑必須沿用「這次任務實際使用」的 Mod 資料庫選項（不採用重開後的設定）。

否則原本屬於 1.21.1 的剩餘翻譯，會在續跑時查詢並寫回目前設定的另一個版本。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_lm_resume_e2e import (  # noqa: F401 - env 是 fixture
    SimulatedCrash,
    env,
    run,
)
from translation_tool.core import lm_resume, lm_translator, lm_translator_db
from translation_tool.translation_db import DbSettings


def _settings(monkeypatch, **kw):
    monkeypatch.setattr(lm_translator_db, "load_db_settings", lambda: DbSettings(**kw))


def test_checkpoint_records_the_resolved_database_choice(env, monkeypatch):  # noqa: F811
    """畫面欄位留空、使用設定檔的版本時，checkpoint 存的是解析後的版本，不是 null。"""
    _settings(monkeypatch, enabled=True, version="1.21.1")
    env.crash_at_translate_call = 2
    with pytest.raises(SimulatedCrash):
        run(env, "out", write_new_cache=True)

    saved = json.loads(Path(lm_translator.CHECKPOINT_FILE).read_text("utf-8"))
    assert saved["translation_db"]["enabled"] is True
    assert saved["translation_db"]["version"] == "1.21.1"
    assert Path(saved["translation_db"]["settings"]["path"]).is_absolute()
    assert saved["translation_db"]["settings"]["version"] == "1.21.1"
    task = lm_resume.peek_interrupted_task()
    assert (task.use_translation_db, task.translation_db_version) == (True, "1.21.1")
    assert (
        task.translation_db_settings["path"]
        == saved["translation_db"]["settings"]["path"]
    )

    # 重開後設定改成 1.20.1：checkpoint 仍然記著原本的版本
    _settings(monkeypatch, enabled=True, version="1.20.1")
    task = lm_resume.peek_interrupted_task()
    assert task.translation_db_version == "1.21.1"


def test_checkpoint_records_page_override_and_disabled(env, monkeypatch):  # noqa: F811
    _settings(monkeypatch, enabled=True, version="1.21.1")
    env.crash_at_translate_call = 2
    with pytest.raises(SimulatedCrash):
        run(
            env,
            "out",
            write_new_cache=True,
            use_translation_db=False,
            translation_db_version="1.19.2",
        )
    task = lm_resume.peek_interrupted_task()
    # 這次任務關閉了資料庫：實際生效的選擇是「不使用」（不保留沒用到的版本）
    assert task.use_translation_db is False and task.translation_db_version == ""


def test_legacy_checkpoint_without_database_fields_means_database_unused(
    env,  # noqa: F811
    monkeypatch,
):
    """資料庫功能之前建立的 checkpoint 一定沒用資料庫：明確視為停用，不採用目前設定。"""
    _settings(monkeypatch, enabled=True, version="1.21.1")
    env.crash_at_translate_call = 2
    with pytest.raises(SimulatedCrash):
        run(env, "out", write_new_cache=True)
    path = Path(lm_translator.CHECKPOINT_FILE)
    data = json.loads(path.read_text("utf-8"))
    del data["translation_db"]
    path.write_text(json.dumps(data), encoding="utf-8")

    task = lm_resume.peek_interrupted_task()
    assert task.use_translation_db is False and task.translation_db_version == ""
    assert task.has_current_format  # 版本號不變：其餘續跑行為照舊


def test_resume_with_different_database_choice_is_warned(env, monkeypatch):  # noqa: F811
    _settings(monkeypatch, enabled=True, version="1.21.1")
    env.crash_at_translate_call = 2
    with pytest.raises(SimulatedCrash):
        run(env, "out", write_new_cache=True)
    env.restart_app()

    warnings: list[str] = []
    monkeypatch.setattr(lm_translator, "log_warning", warnings.append)
    _settings(monkeypatch, enabled=True, version="1.20.1")
    run(env, "out", write_new_cache=True)
    text = "\n".join(warnings)
    assert "Mod 資料庫選項" in text and "1.21.1" in text and "1.20.1" in text


@pytest.mark.parametrize(
    ("use_db", "version", "expected"),
    [
        (None, None, (True, "1.21.1")),  # 用設定檔
        (None, "  1.19.2 ", (True, "1.19.2")),  # 頁面覆寫版本
        (False, "1.19.2", (False, "")),  # 停用：版本不算數
        (True, "", (False, "")),  # 啟用但沒有版本＝實際不使用
    ],
)
def test_resolve_db_choice_returns_the_effective_choice(
    monkeypatch, use_db, version, expected
):
    _settings(monkeypatch, enabled=True, version="1.21.1")
    assert lm_translator_db.resolve_db_choice(use_db, version) == expected


def test_database_choice_is_resolved_once_per_task(env, monkeypatch):  # noqa: F811
    """任務中途設定被改掉（設定是「下次任務才套用」）：checkpoint 仍記這次任務實際開的版本。"""
    reads = iter(["1.21.1"] + ["1.20.1"] * 50)

    def changing_settings():
        return DbSettings(enabled=True, version=next(reads))

    monkeypatch.setattr(lm_translator_db, "load_db_settings", changing_settings)
    env.crash_at_translate_call = 2
    with pytest.raises(SimulatedCrash):
        run(env, "out", write_new_cache=True)  # 沒傳選項：走設定檔（一鍵流程）

    task = lm_resume.peek_interrupted_task()
    assert (task.use_translation_db, task.translation_db_version) == (True, "1.21.1")
