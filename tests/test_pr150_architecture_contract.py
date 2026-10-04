"""Architecture guards for the shared translator contracts in PR #150."""

import ast
from pathlib import Path

ROOT = Path(__file__).parents[1]
PLUGIN_PATHS = (
    ROOT / "translation_tool/plugins/ftbquests/ftbquests_lmtranslator.py",
    ROOT / "translation_tool/plugins/kubejs/kubejs_tooltip_lmtranslator.py",
    ROOT / "translation_tool/plugins/md/md_lmtranslator.py",
)


def _tree(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"))


def test_plugins_delegate_cache_split_to_shared_contract() -> None:
    """Plugins may configure cache rules but cannot duplicate the split engine."""
    for path in PLUGIN_PATHS:
        tree = _tree(path)
        imported_names = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        }
        calls = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert "prepare_translator_items" in imported_names, path
        assert "make_checkpoint_adapter" in imported_names, path
        assert "fast_split_items_by_cache" not in imported_names, path
        assert "fast_split_items_by_cache" not in calls, path


def test_plugins_wire_checkpoint_hook_into_shared_skeleton() -> None:
    """Each adapter must install a durable checkpoint callback, not only import it."""
    for path in PLUGIN_PATHS:
        source = path.read_text(encoding="utf-8")
        assert "make_checkpoint_adapter(" in source, path
        assert "on_batch_checkpoint=checkpoint" in source, path
        assert "run_translator_skeleton(" in source, path


def test_shared_skeleton_owns_progress_and_checkpoint_contract() -> None:
    """The shared skeleton remains the sole owner of callback ordering."""
    source = (ROOT / "translation_tool/core/lm_translator_skeleton.py").read_text(
        encoding="utf-8"
    )
    assert "on_batch_checkpoint=self.hooks.on_batch_checkpoint" in source
    assert "on_progress=self.hooks.on_progress" in source
    assert "def make_progress_hook(" in source
