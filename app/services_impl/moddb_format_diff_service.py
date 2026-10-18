"""Compatibility facade for the shared Mod DB format diagnostics contract."""

from __future__ import annotations

from translation_tool.core.format_diagnostics import (
    FormatDiagnostic as FormatDiffIssue,
)
from translation_tool.core.format_diagnostics import (
    FormatDiagnosticsResult as FormatDiffResult,
)
from translation_tool.core.format_diagnostics import (
    FormatSpan as FormatTokenOccurrence,
)
from translation_tool.core.format_diagnostics import (
    analyze_format_diagnostics,
)
from translation_tool.core.format_diagnostics import (
    format_identifier_label as format_token_label,
)


def compare_format_diff(
    source_text: str,
    target_text: str,
    *,
    kind: str | None = None,
) -> FormatDiffResult:
    """Compare format contracts through the canonical diagnostics analyzer."""
    return analyze_format_diagnostics(
        source_text,
        target_text,
        kind=kind or "lang",
    )


__all__ = [
    "FormatDiffIssue",
    "FormatDiffResult",
    "FormatTokenOccurrence",
    "compare_format_diff",
    "format_token_label",
]
