"""Architecture guards for the shared translator contracts in PR #150."""

from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_shared_skeleton_owns_progress_and_checkpoint_contract() -> None:
    """The shared skeleton remains the sole owner of callback ordering."""
    source = (ROOT / "translation_tool/core/lm_translator_skeleton.py").read_text(
        encoding="utf-8"
    )
    assert "on_batch_checkpoint=self.hooks.on_batch_checkpoint" in source
    assert "on_progress=self.hooks.on_progress" in source
    assert "def make_progress_hook(" in source
