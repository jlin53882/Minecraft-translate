"""Markdown 流水線：抽取階段保護的色碼／物品 ID，經翻譯後必須在最終文件還原。"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

from app.tasks.task_session import TaskSession
from translation_tool.core import lm_api_client, lm_config_rules
from translation_tool.core.md_translation_assembly import run_md_pipeline
from translation_tool.plugins.md import md_inject_qa
from translation_tool.utils import cache_manager, cache_store, config_manager


class _Resp:
    ok = True
    status_code = 200

    def __init__(self, payload):
        self._payload = payload
        self.text = ""

    def json(self):
        return self._payload


def _fake_post(url, **kwargs):
    prompt = json.loads(kwargs["json"]["contents"][-1]["parts"][0]["text"])
    items = [
        {"id": str(it["id"]), "value": "譯：" + str(it.get("text") or it.get("value"))}
        for it in prompt["items"]
    ]
    body = {"items": items}
    return _Resp(
        {
            "candidates": [
                {
                    "content": {
                        "parts": [{"text": json.dumps(body, ensure_ascii=False)}]
                    },
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {},
        }
    )


@pytest.fixture
def isolated_config(monkeypatch, tmp_path):
    fake_key = "AIzaFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAK1"
    monkeypatch.setattr(lm_config_rules, "_get_all_keys", lambda: [fake_key])
    monkeypatch.setattr(lm_api_client.requests, "post", _fake_post)
    # 翻譯會初始化快取：指向暫存資料夾，結束後清掉模組層級的 runtime state
    config = copy.deepcopy(config_manager.load_config())
    config.setdefault("translator", {})["cache_directory"] = str(tmp_path / "cache")
    original = config_manager.load_config
    for module in list(sys.modules.values()):
        if getattr(
            module, "load_config", None
        ) is original and module.__name__.startswith("translation_tool"):
            monkeypatch.setattr(module, "load_config", lambda: config)
    cache_store.reset_runtime_state(cache_manager.CACHE_TYPES)
    yield
    cache_store.reset_runtime_state(cache_manager.CACHE_TYPES)


def test_color_codes_survive_translation_and_inject(tmp_path: Path, isolated_config):
    src = tmp_path / "in" / "docs" / "en_us"
    src.mkdir(parents=True)
    (src / "page.md").write_text(
        "# Title here\n\n&6Craft the golden press carefully today.&r\n",
        encoding="utf-8",
    )

    run_md_pipeline(
        input_dir=str(tmp_path / "in"),
        output_dir=str(tmp_path / "out"),
        session=TaskSession(name="md"),
        dry_run=False,
    )

    final = next((tmp_path / "out" / "md" / "完成").rglob("page.md")).read_text(
        encoding="utf-8"
    )
    assert "&6" in final and "&r" in final
    assert "$C" not in final


def test_translation_map_is_not_read_as_a_pending_document(tmp_path: Path):
    (tmp_path / "translation_map_md.json").write_text("[]", encoding="utf-8")
    (tmp_path / "a.md.json").write_text("{}", encoding="utf-8")
    names = [p.name for p in md_inject_qa.iter_json_files(tmp_path)]
    assert names == ["a.md.json"]
