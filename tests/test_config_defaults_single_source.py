"""設定預設值的單一來源：schema 以外不得對這些 key 寫字面值後備。

``translation_tool/utils/config_schema.py`` 的 ``Setting.default`` 是預設值的唯一定義處；
程式需要「設定缺漏時的後備值」時呼叫 ``get_default(path)``。否則預設值會在 schema、
執行端與 UI 之間各自漂移（例如 timeout 600 同時出現在 schema 與多個 ``.get("timeout", 600)``）。

本測試用 AST 掃描 ``app/`` 與 ``translation_tool/``，抓出：

- ``<cfg>.get("<key>", <數字字面值>)``，以及 ``<cfg>.get("<key>") or <數字字面值>``；
- 以數字字面值指定的預設常數（例如 ``DEFAULT_BATCH_WRITE_INTERVAL = 2``）。

確實需要例外時，把項目加進 ``ALLOWED``，並寫明為什麼不是 schema 設定的重複。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from translation_tool.utils.config_schema import (
    SETTINGS_BY_PATH,
    build_default_config,
    get_default,
    get_path,
)

ROOT = Path(__file__).resolve().parents[1]
SCAN_DIRS = ("app", "translation_tool")
SCHEMA_FILE = "translation_tool/utils/config_schema.py"

#: 設定 key 名稱 -> schema 路徑。key 只有在 ``required_receiver`` 出現於接收者運算式時才算
#: （``timeout`` 太通用，只管 ``rate_limit`` 底下的那個）。
GUARDED_KEYS: dict[str, tuple[str, str | None]] = {
    "timeout": ("lm_translator.rate_limit.timeout", "rate_limit"),
    "temperature": ("lm_translator.temperature", None),
    "batch_write_interval": ("lm_translator.batch_write_interval", None),
    "sleep_seconds_between_batches": (
        "lm_translator.rate_limit.sleep_seconds_between_batches",
        None,
    ),
    "initial_batch_size_lang": ("lm_translator.initial_batch_size_lang", None),
    "initial_batch_size_ftb": ("lm_translator.initial_batch_size_ftb", None),
    "initial_batch_size_kubejs": ("lm_translator.initial_batch_size_kubejs", None),
    "initial_batch_size_md": ("lm_translator.initial_batch_size_md", None),
    "initial_batch_size_patchouli": (
        "lm_translator.initial_batch_size_patchouli",
        None,
    ),
}

#: 不得以數字字面值指定的預設常數名稱（值應該呼叫 ``get_default``）。
GUARDED_CONSTANT_NAMES = frozenset(
    {
        "DEFAULT_BATCH_WRITE_INTERVAL",
        "DEFAULT_REQUEST_TIMEOUT",
        "DEFAULT_TIMEOUT_SEC",
        "DEFAULT_TEMPERATURE",
    }
)

#: (相對路徑, key 或常數名稱) -> 為什麼這個字面值不是 schema 預設值的重複。
ALLOWED: dict[tuple[str, str], str] = {}


def _is_number(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, (int, float))
        and not isinstance(node.value, bool)
    )


def _guarded_get(node: ast.AST) -> str | None:
    """若 ``node`` 是 ``<receiver>.get("<guarded key>", ...)``，回傳 key。"""
    if not (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and node.args
        and isinstance(node.args[0], ast.Constant)
    ):
        return None
    key = node.args[0].value
    if key not in GUARDED_KEYS:
        return None
    required_receiver = GUARDED_KEYS[key][1]
    if required_receiver and required_receiver not in ast.unparse(node.func.value):
        return None
    return key


def find_literal_defaults(source: str) -> list[tuple[int, str, str]]:
    """回傳 ``[(行號, key 或常數名稱, 說明)]``：schema 以外的字面值預設。"""
    found: list[tuple[int, str, str]] = []
    for node in ast.walk(ast.parse(source)):
        key = _guarded_get(node)
        if key and len(node.args) >= 2 and _is_number(node.args[1]):
            found.append((node.lineno, key, f'.get("{key}", {node.args[1].value})'))
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
            values = node.values
            for index, value in enumerate(values[:-1]):
                key = _guarded_get(value)
                if key and any(_is_number(rest) for rest in values[index + 1 :]):
                    found.append((node.lineno, key, f'.get("{key}") or <字面值>'))
        if isinstance(node, ast.Assign) and _is_number(node.value):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in GUARDED_CONSTANT_NAMES:
                    found.append((node.lineno, target.id, f"{target.id} = <字面值>"))
    return found


def _scan_repo() -> list[tuple[str, int, str, str]]:
    violations = []
    for directory in SCAN_DIRS:
        for path in sorted((ROOT / directory).rglob("*.py")):
            relative = path.relative_to(ROOT).as_posix()
            if relative == SCHEMA_FILE:
                continue
            source = path.read_text(encoding="utf-8")
            for lineno, name, detail in find_literal_defaults(source):
                if (relative, name) not in ALLOWED:
                    violations.append((relative, lineno, name, detail))
    return violations


def test_no_literal_defaults_for_schema_keys_outside_the_schema():
    violations = _scan_repo()
    assert not violations, (
        "這些設定的預設值只能定義在 config_schema.py，請改用 get_default(path)：\n"
        + "\n".join(f"  {p}:{n} {detail}" for p, n, _name, detail in violations)
    )


def test_allowlist_entries_state_a_reason_and_still_match_something():
    for (relative, name), reason in ALLOWED.items():
        assert reason.strip(), f"{relative} {name} 需要寫明例外原因"
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert name in {n for _line, n, _d in find_literal_defaults(source)}, (
            f"{relative} 已沒有 {name} 的字面值預設，請從 ALLOWED 移除"
        )


@pytest.mark.parametrize(
    "source",
    [
        'x = cfg.get("rate_limit", {}).get("timeout", 600)',
        'x = rate_limit.get("timeout", 600.0)',
        'x = lm_cfg.get("temperature", 0.2)',
        'x = int(lm.get("initial_batch_size_lang", 300) or 300)',
        'x = int(lm.get("batch_write_interval") or 2)',
        "DEFAULT_BATCH_WRITE_INTERVAL = 2",
    ],
)
def test_detector_flags_literal_defaults(source):
    assert find_literal_defaults(source)


@pytest.mark.parametrize(
    "source",
    [
        'x = session.get("timeout", 600)',  # 與 rate_limit 無關的 timeout
        'x = lm_cfg.get("temperature", get_default("lm_translator.temperature"))',
        'x = lm_cfg.get("temperature")',
        'x = lm_cfg.get("model", 600)',
        "DEFAULT_BATCH_WRITE_INTERVAL = get_default('lm_translator.batch_write_interval')",
    ],
)
def test_detector_accepts_schema_references_and_unrelated_keys(source):
    assert not find_literal_defaults(source)


def test_guarded_keys_exist_in_the_schema():
    for key, (path, _receiver) in GUARDED_KEYS.items():
        assert path.endswith(key) or key == "timeout", key
        assert path in SETTINGS_BY_PATH, path


def test_get_default_returns_the_schema_default_as_a_copy():
    for path, _receiver in GUARDED_KEYS.values():
        assert get_default(path) == SETTINGS_BY_PATH[path].default
        assert get_default(path) == get_path(build_default_config(), path)
    with pytest.raises(KeyError):
        get_default("lm_translator.no_such_setting")


def test_runtime_fallbacks_resolve_to_the_schema_defaults():
    """缺設定時的實際後備值就是 schema 預設值（不改變預設行為）。"""
    from translation_tool.core import lm_api_client, lm_key_health
    from translation_tool.core.lm_translator_shared_loop import _get_default_batch_size
    from translation_tool.utils.config_manager import get_batch_write_interval

    timeout = get_default("lm_translator.rate_limit.timeout")
    assert timeout == 600
    assert lm_api_client._request_timeout({}) == timeout
    assert lm_key_health.DEFAULT_PROBE_LEASE_SEC == (
        timeout + lm_key_health.PROBE_LEASE_MARGIN_SEC
    )
    assert get_batch_write_interval({"lm_translator": {}}) == get_default(
        "lm_translator.batch_write_interval"
    )
    assert (
        get_batch_write_interval({"lm_translator": {"batch_write_interval": "x"}}) == 2
    )
    assert get_batch_write_interval({"lm_translator": {"batch_write_interval": 0}}) == 1
    for cache_type, suffix in (
        ("ftbquests", "ftb"),
        ("kubejs", "kubejs"),
        ("patchouli", "patchouli"),
        ("md", "md"),
        ("lang", "lang"),
    ):
        assert _get_default_batch_size(cache_type, {cache_type: 7}) == 7
        assert get_default(f"lm_translator.initial_batch_size_{suffix}") > 0
