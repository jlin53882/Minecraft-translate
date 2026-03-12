from __future__ import annotations

from pathlib import Path

from translation_tool.core import kubejs_translator


def test_resolve_kubejs_root_prefers_candidate_with_client_scripts(tmp_path: Path) -> None:
    shallow = tmp_path / "pack" / "kubejs"
    deep = tmp_path / "pack" / "nested" / "kubejs"
    deep_client = deep / "client_scripts"

    shallow.mkdir(parents=True)
    deep_client.mkdir(parents=True)

    found = kubejs_translator.resolve_kubejs_root(str(tmp_path / "pack"))

    assert found == deep.resolve()


def test_resolve_kubejs_root_returns_base_when_not_found(tmp_path: Path) -> None:
    base = tmp_path / "pack"
    base.mkdir()

    found = kubejs_translator.resolve_kubejs_root(str(base))

    assert found == base.resolve()
