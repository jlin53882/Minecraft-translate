"""語言合併：ZIP 累計預算用盡時必須回報不完整輸出（issue #109）。

預算用盡後的狀態是 sticky（PR #105）：之後所有讀取都失敗，會被既有的寬鬆
except 視為「單檔失敗」。若 pack 層級沒有明確回報，結尾仍會顯示
「全部處理完成」，使用者可能拿到不完整的輸出。
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import orjson

from translation_tool.core import (
    lang_merge_content,
    lang_merge_pipeline,
    lang_merge_zip_io,
    lang_merger,
)
from translation_tool.utils.zip_safety import ZipReadBudget

PENDING_DIR = "待翻譯"
FILTERED_DIR = "待翻譯整理需翻譯"


def _fake_config() -> dict:
    return {
        "replace_rules_path": "replace_rules.json",
        "translator": {"parallel_execution_workers": 1},
        "lang_merger": {
            "pending_folder_name": PENDING_DIR,
            "pending_organized_folder_name": FILTERED_DIR,
            "filtered_pending_min_count": 1,
            "quarantine_folder_name": "skipped_json",
        },
        "lm_translator": {"patchouli": {"dir_names": ["patchouli_books"]}},
    }


def _write_pack_zip(zip_path: Path, mods: int = 3) -> None:
    with zipfile.ZipFile(zip_path, "w") as zf:
        for i in range(mods):
            zf.writestr(
                f"assets/mod{i}/lang/en_us.json",
                orjson.dumps({f"item.mod{i}.a": "English A", f"item.mod{i}.b": "B"}),
            )
            zf.writestr(
                f"assets/mod{i}/lang/zh_cn.json",
                orjson.dumps({f"item.mod{i}.a": "简体 A", f"item.mod{i}.b": "B"}),
            )


def _patch_merge_env(monkeypatch, *, budget_factory=None):
    monkeypatch.setattr(lang_merger, "load_config", _fake_config)
    monkeypatch.setattr(lang_merger, "load_replace_rules", lambda _p: [])
    monkeypatch.setattr(lang_merge_content, "load_config", _fake_config)
    monkeypatch.setattr(lang_merge_zip_io, "load_config", _fake_config)
    monkeypatch.setattr(
        lang_merge_content, "recursive_translate_dict", lambda v, _r: v
    )
    monkeypatch.setattr(lang_merge_pipeline, "recursive_translate_dict", lambda v, _r: v)
    monkeypatch.setattr(lang_merge_content, "apply_replace_rules", lambda v, _r: v)
    monkeypatch.setattr(lang_merge_pipeline, "apply_replace_rules", lambda v, _r: v)
    if budget_factory is not None:

        class _FakeBudget:
            @staticmethod
            def for_pack(label: str = ""):
                return budget_factory(label)

        monkeypatch.setattr(lang_merger, "ZipReadBudget", _FakeBudget)


def _capture_logs(monkeypatch):
    errors: list[str] = []
    infos: list[str] = []
    monkeypatch.setattr(lang_merger, "log_error", lambda m, *a, **k: errors.append(str(m)))
    monkeypatch.setattr(lang_merger, "log_info", lambda m, *a, **k: infos.append(str(m)))
    return errors, infos


def test_exhausted_budget_reports_incomplete_output(tmp_path: Path, monkeypatch):
    zip_path = tmp_path / "pack.zip"
    out = tmp_path / "out"
    _write_pack_zip(zip_path)
    # 1 byte 預算：第一個讀取就會用盡，之後全部失敗
    _patch_merge_env(
        monkeypatch,
        budget_factory=lambda label: ZipReadBudget(
            max_bytes=1, max_members=1000, label=label
        ),
    )
    errors, infos = _capture_logs(monkeypatch)

    updates = list(lang_merger.merge_zhcn_to_zhtw_from_zip(str(zip_path), str(out)))

    final = updates[-1]
    assert final["progress"] == 1.0
    assert final["error"] is True
    assert "不完整" in final["log"]  # UI / merge_service 會顯示這條訊息
    assert any("預算已用盡" in m for m in errors)
    assert not any("全部處理完成" in m for m in infos)


def test_exhausted_budget_still_finishes_cleanup_and_progress(tmp_path: Path, monkeypatch):
    """用盡後不應中途崩潰：輸出目錄與進度照常收尾，只是標記為不完整。"""
    zip_path = tmp_path / "pack.zip"
    out = tmp_path / "out"
    _write_pack_zip(zip_path)
    _patch_merge_env(
        monkeypatch,
        budget_factory=lambda label: ZipReadBudget(
            max_bytes=1, max_members=1000, label=label
        ),
    )

    updates = list(lang_merger.merge_zhcn_to_zhtw_from_zip(str(zip_path), str(out)))

    progresses = [u["progress"] for u in updates if "progress" in u]
    assert progresses == sorted(progresses)
    assert (out / "lang_output").is_dir()
    assert sum(1 for u in updates if u.get("error") and "不完整" in u.get("log", "")) == 1


def test_sufficient_budget_keeps_success_behaviour(tmp_path: Path, monkeypatch):
    zip_path = tmp_path / "pack.zip"
    out = tmp_path / "out"
    _write_pack_zip(zip_path)
    _patch_merge_env(monkeypatch)  # 使用真正的 for_pack 預算
    errors, infos = _capture_logs(monkeypatch)

    updates = list(lang_merger.merge_zhcn_to_zhtw_from_zip(str(zip_path), str(out)))

    assert all(not u.get("error", False) for u in updates)
    assert updates[-1]["progress"] == 1.0
    assert any("全部處理完成" in m for m in infos)
    assert not any("預算" in m for m in errors)


def test_merge_service_surfaces_incomplete_message(tmp_path: Path, monkeypatch):
    """merge_service 以 update['log'] 作為失敗詳情（error 為 bool 時不會被吞掉）。"""
    from app.services_impl.pipelines.merge_service import _soft_error_detail

    detail = _soft_error_detail(
        {"progress": 1.0, "error": True, "log": "處理結束但輸出不完整：預算用盡"}
    )
    assert "不完整" in detail
