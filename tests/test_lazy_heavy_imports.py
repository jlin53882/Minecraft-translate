"""開啟頁面不該為了很少用到的重量級套件卡住畫面：這些套件要等真的用到才載入。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _modules_loaded_after(import_stmt: str) -> set[str]:
    code = f"import sys; sys.path.insert(0, {str(ROOT)!r}); {import_stmt}; print(' '.join(sorted(m for m in sys.modules if m.split('.')[0] in ('pandas',))))"
    result = subprocess.run(
        [sys.executable, "-I", "-c", code], capture_output=True, text=True, check=True
    )
    return set(result.stdout.split())


def test_qc_view_dependencies_do_not_import_pandas():
    """pandas 約 0.35 秒；QC 頁只有比對 TSV 時才需要。"""
    assert _modules_loaded_after("import app.services") == set()


def test_variant_comparator_tsv_still_works_with_lazy_pandas(tmp_path):
    from translation_tool.checkers.variant_comparator_tsv import (
        compare_variants_tsv_generator,
    )

    source = tmp_path / "in.tsv"
    source.write_text("key\tzh_cn\tzh_tw\nk1\t软件\t軟件\n", encoding="utf-8")
    output = tmp_path / "diff.csv"

    updates = list(compare_variants_tsv_generator(str(source), str(output)))

    assert updates[-1]["progress"] == 1.0
    assert not any(u.get("error") for u in updates)
