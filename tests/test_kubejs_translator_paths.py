"""kubejs_translator_paths.py 模組的單元測試。

用途：測試 kubejs_translator_paths 中的路徑解析功能。
"""
from __future__ import annotations

from pathlib import Path
import sys

import pytest

# 確保可以導入 translation_tool
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from translation_tool.core.kubejs_translator_paths import (
    resolve_kubejs_root_impl,
)


class TestResolveKubejsRootImpl:
    """測試 resolve_kubejs_root_impl 函式。"""

    def test_direct_kubejs_dir(self, tmp_path: Path):
        """傳入直接是 kubejs 目錄時應回傳該目錄。"""
        kubejs_dir = tmp_path / "kubejs"
        kubejs_dir.mkdir()

        result = resolve_kubejs_root_impl(str(kubejs_dir))
        assert result == kubejs_dir.resolve()

    def test_direct_kubejs_dir_case_insensitive(self, tmp_path: Path):
        """大小寫不敏感測試。"""
        kubejs_dir = tmp_path / "KUBEJS"
        kubejs_dir.mkdir()

        result = resolve_kubejs_root_impl(str(kubejs_dir))
        assert result == kubejs_dir.resolve()

    def test_nested_kubejs_dir(self, tmp_path: Path):
        """測試巢狀 kubejs 目錄。"""
        kubejs_dir = tmp_path / "pack" / "deep" / "nested" / "kubejs"
        kubejs_dir.mkdir(parents=True)

        result = resolve_kubejs_root_impl(str(tmp_path / "pack"))
        assert result == kubejs_dir.resolve()

    def test_prefers_client_scripts_dir(self, tmp_path: Path):
        """有 client_scripts 的候選目錄應優先選取。"""
        shallow = tmp_path / "pack" / "kubejs"
        deep = tmp_path / "pack" / "nested" / "kubejs"
        deep_client = deep / "client_scripts"

        shallow.mkdir(parents=True)
        deep_client.mkdir(parents=True)

        result = resolve_kubejs_root_impl(str(tmp_path / "pack"))
        # 應該選擇有 client_scripts 的 deep
        assert result == deep.resolve()

    def test_returns_base_when_not_found(self, tmp_path: Path):
        """找不到 kubejs 目錄時應回傳輸入目錄。"""
        base = tmp_path / "some" / "random" / "dir"
        base.mkdir(parents=True)

        result = resolve_kubejs_root_impl(str(base))
        assert result == base.resolve()

    def test_respects_max_depth(self, tmp_path: Path):
        """測試 max_depth 參數。"""
        # 建立超過預設深度的 kubejs
        deep_kubejs = tmp_path / "a" / "b" / "c" / "d" / "e" / "kubejs"
        deep_kubejs.mkdir(parents=True)

        # 使用預設 max_depth=4 應該找不到
        result = resolve_kubejs_root_impl(str(tmp_path / "a"), max_depth=4)
        # 會回傳 base
        assert result == (tmp_path / "a").resolve()

        # 增加 max_depth 應該能找到
        result = resolve_kubejs_root_impl(str(tmp_path / "a"), max_depth=6)
        assert result == deep_kubejs.resolve()

    def test_empty_dir_returns_resolved_path(self, tmp_path: Path):
        """空目錄應回傳解析後的路徑。"""
        base = tmp_path / "empty"
        base.mkdir()

        result = resolve_kubejs_root_impl(str(base))
        assert result == base.resolve()

    def test_multiple_kubejs_chooses_shallower(self, tmp_path: Path):
        """多個 kubejs 目錄時，選擇較淺層的。"""
        shallow = tmp_path / "kubejs"
        deep = tmp_path / "mod" / "kubejs"

        shallow.mkdir(parents=True)
        deep.mkdir(parents=True)

        result = resolve_kubejs_root_impl(str(tmp_path))
        # shallow 深度為 1，deep 深度為 2，應該選 shallow
        assert result == shallow.resolve()

    def test_kubejs_with_client_scripts_overrides_shallow(self, tmp_path: Path):
        """有 client_scripts 的 kubejs 應優於較淺但沒有 client_scripts 的。"""
        shallow = tmp_path / "kubejs"
        deep = tmp_path / "mod" / "kubejs"
        deep_client = deep / "client_scripts"

        shallow.mkdir(parents=True)
        deep_client.mkdir(parents=True)

        result = resolve_kubejs_root_impl(str(tmp_path))
        # 雖然 shallow 較淺，但 deep 有 client_scripts
        assert result == deep.resolve()
