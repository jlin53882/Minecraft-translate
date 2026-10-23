"""``DbSettings()``、``DEFAULT_CONFIG['translation_db']`` 與 ``load_db_settings`` 內部預設值必須相等。"""

from __future__ import annotations

import copy
import dataclasses

import pytest

from translation_tool.translation_db.schema import DEFAULT_PRIORITY, SRC_CUSTOM
from translation_tool.translation_db.settings import (
    DbSettings,
    load_db_settings,
    parse_priority,
)
from translation_tool.translation_db.source_catalog import DEFAULT_SOURCE_CATALOG
from translation_tool.utils.config_manager import DEFAULT_CONFIG

SCALARS = (
    "enabled",
    "merge_enabled",
    "path",
    "version",
    "cross_version",
    "write_back",
    "sync_manual",
)


def test_scalar_defaults_match_the_config_schema_defaults():
    defaults = DbSettings()
    block = DEFAULT_CONFIG["translation_db"]
    for name in SCALARS:
        assert getattr(defaults, name) == block[name], name


def test_priority_default_matches_the_default_priority_lines():
    block = DEFAULT_CONFIG["translation_db"]
    assert parse_priority(block["priority"]) == DEFAULT_PRIORITY
    assert DbSettings().priority == DEFAULT_PRIORITY


def test_zip_source_default_resolves_to_the_same_effective_source():
    """類別預設 ``None`` 代表「未指定」，消費端（掃描面板）退回自訂補充；
    與設定預設值「自訂補充」解析出的來源相同。"""
    configured = DEFAULT_SOURCE_CATALOG.resolve(
        DEFAULT_CONFIG["translation_db"]["zip_source"]
    )
    assert configured == SRC_CUSTOM
    assert (DbSettings().zip_source or SRC_CUSTOM) == configured


def test_every_default_config_key_is_a_dbsettings_field_or_known_alias():
    block = DEFAULT_CONFIG["translation_db"]
    fields = {f.name for f in dataclasses.fields(DbSettings)}
    # priority 在設定是名稱清單、在 DbSettings 是代碼與 priority_lines
    assert set(block) <= fields


@pytest.mark.parametrize("missing", ["merged", "empty"])
def test_load_db_settings_missing_keys_fall_back_to_the_same_defaults(
    tmp_path, missing
):
    """設定缺欄位（或整段缺失）時，``load_db_settings`` 的內部預設值與完整預設設定一致。"""
    path = str(tmp_path / "defaults.db")
    full = copy.deepcopy(DEFAULT_CONFIG)
    full["translation_db"]["path"] = path
    from_full = load_db_settings(full)

    if missing == "merged":
        sparse = {"translation_db": {"path": path}}
    else:
        sparse = {"translation_db": {"path": path, "priority": None}}
    from_sparse = load_db_settings(sparse)

    for name in SCALARS:
        assert getattr(from_sparse, name) == getattr(from_full, name), name
    assert from_sparse.priority == from_full.priority == DEFAULT_PRIORITY
