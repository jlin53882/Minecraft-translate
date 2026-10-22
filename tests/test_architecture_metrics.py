"""架構指標防護欄：只對「新的循環 import」與「新增的超高複雜度函式」失敗。

行數防護欄（``tests/test_view_size_limits.py``）保留；本檔補上與行數無關的結構指標：
模組複雜度、公開 API 數量、class method 數量、import 深度、頂層責任數量、型別註解覆蓋率。

- 循環 import：不是 ``tests/data/architecture_baseline.json`` 已記錄循環子集合者即失敗。
- 複雜度 > 20 的函式：不在基線內才失敗（既有的只在變複雜時警告）。
- 其他指標變差只輸出警告。
- 確認合理後用 ``UPDATE_ARCH_BASELINE=1 pytest tests/test_architecture_metrics.py`` 更新基線，
  並在 PR 說明原因。規則同 size_baseline：只有變差才失敗，改善不會失敗。
"""

from __future__ import annotations

import ast
import os
import warnings

from tests.test_view_size_limits import _complexity as size_limits_complexity
from tools import architecture_metrics as am


def _update_requested() -> bool:
    return bool(os.environ.get("UPDATE_ARCH_BASELINE"))


def test_no_new_import_cycles():
    if _update_requested():
        am.write_baseline()
    metrics, cycles, _high = am.collect()
    assert metrics, "沒有掃描到任何模組"
    new = am.new_cycles(cycles, am.load_baseline()["import_cycles"])
    assert new == [], (
        "出現新的模組層級循環 import；請把共用部分移到不被雙方依賴的模組，"
        "或把其中一邊改成函式內延遲 import 並說明原因：\n"
        + "\n".join("  " + " <-> ".join(c) for c in new)
    )


def test_no_new_very_high_complexity_functions():
    _metrics, _cycles, high = am.collect()
    baseline = am.load_baseline()["high_complexity"]
    new = am.new_high_complexity(high, baseline)
    assert new == {}, (
        f"新增複雜度 > {am.FAIL_COMPLEXITY} 的函式；請依控制流程拆出有獨立語義的函式"
        "（不要機械切段）。確實合理時用 UPDATE_ARCH_BASELINE=1 更新基線並在 PR 說明：\n"
        + "\n".join(f"  {key}: {cx}" for key, cx in new.items())
    )
    grown = {
        key: (baseline[key], cx)
        for key, cx in high.items()
        if key in baseline and cx > baseline[key]
    }
    if grown:
        warnings.warn(
            f"既有超高複雜度函式變得更複雜 {len(grown)} 個（僅提醒）：\n"
            + "\n".join(f"  {k}: {old} → {new}" for k, (old, new) in grown.items()),
            stacklevel=1,
        )


def test_other_metric_regressions_only_warn():
    metrics, _cycles, _high = am.collect()
    problems = am.regression_warnings(metrics, am.load_baseline()["modules"])
    if problems:
        warnings.warn(
            f"架構指標相對基線變差 {len(problems)} 項（僅提醒）：\n"
            + "\n".join(problems[:30]),
            stacklevel=1,
        )


def test_baseline_covers_scanned_roots():
    """基線不是空檔：避免誤刪基線使所有既有項目被當成『新增』或讓檢查失效。"""
    baseline = am.load_baseline()
    assert baseline["modules"], (
        "tests/data/architecture_baseline.json 為空；請用 UPDATE_ARCH_BASELINE=1 重建"
    )


def test_complexity_matches_size_limit_algorithm():
    source = (
        "def f(a, b):\n"
        "    if a and b or a:\n"
        "        for x in [i for i in range(3) if i]:\n"
        "            try:\n"
        "                pass\n"
        "            except ValueError:\n"
        "                pass\n"
        "    return 1 if a else 2\n"
    )
    func = ast.parse(source).body[0]
    assert am.complexity(func) == size_limits_complexity(func)


def test_cycle_detection_and_depth_on_synthetic_graph():
    graph = {
        "a": {"b"},
        "b": {"c"},
        "c": {"a"},
        "d": {"a"},
        "e": {"e"},
        "f": set(),
    }
    assert am.find_cycles(graph) == [["a", "b", "c"], ["e"]]
    depths = am.import_depths(graph)
    assert depths["f"] == 0
    assert depths["a"] == depths["b"] == depths["c"] == 0
    assert depths["d"] == 1


def test_depth_is_longest_path():
    graph = {"a": {"b", "c"}, "b": {"c"}, "c": {"d"}, "d": set()}
    assert am.import_depths(graph) == {"a": 3, "b": 2, "c": 1, "d": 0}


def test_new_cycle_rules():
    baseline = [["a", "b"]]
    assert am.new_cycles([["a", "b"]], baseline) == []
    assert am.new_cycles([["a", "b", "c"]], baseline) == [["a", "b", "c"]]
    assert am.new_cycles([["x", "y"]], baseline) == [["x", "y"]]
    assert am.new_cycles([], baseline) == []


def test_import_resolution_ignores_lazy_and_type_checking_imports():
    source = (
        "from typing import TYPE_CHECKING\n"
        "import pkg.a\n"
        "from . import sibling\n"
        "from .sub import thing\n"
        "from pkg import other\n"
        "if TYPE_CHECKING:\n"
        "    import pkg.typing_only\n"
        "try:\n"
        "    import pkg.optional\n"
        "except ImportError:\n"
        "    pass\n"
        "def f():\n"
        "    import pkg.lazy\n"
    )
    known = {
        "pkg",
        "pkg.a",
        "pkg.mod",
        "pkg.sibling",
        "pkg.sub",
        "pkg.sub.thing",
        "pkg.other",
        "pkg.typing_only",
        "pkg.optional",
        "pkg.lazy",
    }
    edges = am._resolve_imports("pkg.mod", False, ast.parse(source).body, known)
    # ``from . import x`` 與 ``from pkg import x`` 都會載入 package 本身（pkg/__init__）。
    assert edges == {
        "pkg",
        "pkg.a",
        "pkg.sibling",
        "pkg.sub",
        "pkg.sub.thing",
        "pkg.other",
        "pkg.optional",
    }


def test_module_metrics_counts_public_api_methods_and_annotations():
    tree = ast.parse(
        "def public(a: int) -> int: ...\n"
        "def _private(a): ...\n"
        "class C:\n"
        "    def m(self, x: int): ...\n"
        "    def n(self) -> None: ...\n"
    )
    assert am._public_api(tree) == 2
    assert am._class_methods(tree) == 2
    # 槽位：public(a, return)、_private(a, return)、m(x, return)、n(return) = 7；已註解 a、return、x、return = 4
    assert am._annotation_coverage(tree) == round(4 / 7, 3)
    tree_all = ast.parse("__all__ = ['x', 'y', 'z']\ndef a(): ...\n")
    assert am._public_api(tree_all) == 3
