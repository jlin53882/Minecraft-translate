"""Shared, immutable diagnostics for translation format contracts.

The scanner deliberately understands only the four formats currently supported
by Mod DB. New formats can register a policy and analyzer strategy; no parser
is implied by registration alone. Registered unsupported formats fail
explicitly, while unregistered legacy database kinds use a marked token-only
compatibility policy.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Literal

from translation_tool.core.format_tokens import (
    format_token_spans,
    token_category,
)

DiagnosticDirection = Literal["missing", "extra"]
LocationConfidence = Literal["unknown", "exact"]
NewlinePolicy = Literal["hard", "soft"]


@dataclass(frozen=True)
class FormatSpan:
    """A source span for one format token or structured mark."""

    token: str
    start: int
    end: int
    line: int
    column: int


@dataclass(frozen=True)
class FormatDiagnostic:
    """One count-based mismatch in a format contract.

    ``identifier`` is stable and machine-readable.  ``display_token`` is the
    human-facing label and may be localized without changing batch identities.
    """

    identifier: str
    category: str
    direction: DiagnosticDirection
    source_count: int
    target_count: int
    delta: int
    location_confidence: LocationConfidence = "unknown"
    source_occurrences: tuple[FormatSpan, ...] = ()
    target_occurrences: tuple[FormatSpan, ...] = ()
    display_label: str | None = None

    @property
    def token(self) -> str:
        """Compatibility name used by the original token-only UI."""
        return self.identifier

    @property
    def display_token(self) -> str:
        if self.display_label is not None:
            return self.display_label
        return format_identifier_label(self.identifier, self.category)

    @property
    def legacy_message(self) -> str:
        action = "少了" if self.direction == "missing" else "多了"
        return f"{action} {abs(self.delta)} 個「{self.display_token}」"

    @property
    def summary(self) -> str:
        if self.category == "newline" and self.identifier == "\n":
            return f"換行數量不一致：原文 {self.source_count}、譯文 {self.target_count}"
        action = "缺少" if self.direction == "missing" else "多出"
        location = (
            "位置無法可靠判定"
            if self.location_confidence == "unknown"
            else "位置已定位"
        )
        return f"{action} {self.display_token} × {abs(self.delta)}（{location}）"


@dataclass(frozen=True)
class FormatDiagnosticsResult:
    """Immutable result shared by translation, repair, DB and UI consumers."""

    source_text: str
    target_text: str
    kind: str
    source_counts: tuple[tuple[str, int], ...]
    target_counts: tuple[tuple[str, int], ...]
    source_occurrences: tuple[FormatSpan, ...]
    target_occurrences: tuple[FormatSpan, ...]
    diagnostics: tuple[FormatDiagnostic, ...]
    guide_issues: tuple[str, ...] = ()
    soft_newline_counts: tuple[int, int] | None = None

    @property
    def issues(self) -> tuple[FormatDiagnostic, ...]:
        """Compatibility alias for the old ``FormatDiffResult.issues`` field."""
        return self.diagnostics

    @property
    def has_issues(self) -> bool:
        return bool(self.diagnostics or self.guide_issues)

    @property
    def extra_guide_issues(self) -> tuple[str, ...]:
        """Guide issues not already represented by a structured diagnostic."""
        represented = set(self.legacy_messages)
        return tuple(issue for issue in self.guide_issues if issue not in represented)

    @property
    def summary_lines(self) -> tuple[str, ...]:
        lines = [item.summary for item in self.diagnostics]
        lines.extend(self.extra_guide_issues)
        if self.soft_newline_counts is not None:
            source_count, target_count = self.soft_newline_counts
            lines.append(
                f"一般段落換行可依語意重排：原文 {source_count}、譯文 {target_count}"
            )
        return tuple(lines) or ("格式檢查通過。",)

    @property
    def legacy_messages(self) -> tuple[str, ...]:
        return tuple(item.legacy_message for item in self.diagnostics)


@dataclass(frozen=True)
class FormatAnalysis:
    """The analyzer-owned portion of a common diagnostics result."""

    source_occurrences: tuple[FormatSpan, ...]
    target_occurrences: tuple[FormatSpan, ...]
    diagnostics: tuple[FormatDiagnostic, ...]
    guide_issues: tuple[str, ...] = ()


FormatAnalyzer = Callable[[str, str, NewlinePolicy], FormatAnalysis]
AnalyzerSpec = FormatAnalyzer | str


@dataclass(frozen=True)
class FormatKindPolicy:
    """Format behavior owned by a translation kind."""

    kind: str
    cache_type: str
    prompt_profile: str
    analyzer: AnalyzerSpec
    newline_policy: NewlinePolicy
    analyzer_name: str | None = None
    has_structured_marks: bool = False
    is_legacy_fallback: bool = False


def format_identifier_label(identifier: str, category: str) -> str:
    if category == "guide" and ":" in identifier:
        mark_kind, value = identifier.split(":", 1)
        from translation_tool.core.guide_format import describe_guide_mark

        return describe_guide_mark((mark_kind, value))
    if identifier == "\n":
        return "換行"
    if identifier == "\\n":
        return "字面 \\n"
    if identifier == "$(t:…)":
        return "提示文字 $(t:…)"
    return identifier


def _span(token: str, start: int, end: int, text: str) -> FormatSpan:
    return FormatSpan(
        token=token,
        start=start,
        end=end,
        line=text.count("\n", 0, start) + 1,
        column=start - text.rfind("\n", 0, start),
    )


def _token_occurrences(text: str) -> tuple[FormatSpan, ...]:
    return tuple(
        _span(token, start, end, text) for token, start, end in format_token_spans(text)
    )


def _token_diagnostics(
    source: str,
    translated: str,
    source_occurrences: tuple[FormatSpan, ...],
    target_occurrences: tuple[FormatSpan, ...],
    *,
    newline_policy: Literal["hard", "soft"],
) -> tuple[FormatDiagnostic, ...]:
    source_counts = Counter(item.token for item in source_occurrences)
    target_counts = Counter(item.token for item in target_occurrences)
    if newline_policy == "soft":
        source_counts.pop("\n", None)
        target_counts.pop("\n", None)
    source_by_token: dict[str, list[FormatSpan]] = {}
    target_by_token: dict[str, list[FormatSpan]] = {}
    for item in source_occurrences:
        source_by_token.setdefault(item.token, []).append(item)
    for item in target_occurrences:
        target_by_token.setdefault(item.token, []).append(item)
    diagnostics: list[FormatDiagnostic] = []
    for identifier in sorted(set(source_counts) | set(target_counts)):
        delta = target_counts[identifier] - source_counts[identifier]
        if not delta:
            continue
        diagnostics.append(
            FormatDiagnostic(
                identifier=identifier,
                category=token_category(identifier),
                direction="extra" if delta > 0 else "missing",
                source_count=source_counts[identifier],
                target_count=target_counts[identifier],
                delta=delta,
                source_occurrences=tuple(source_by_token.get(identifier, ())),
                target_occurrences=tuple(target_by_token.get(identifier, ())),
            )
        )
    return tuple(diagnostics)


def _analyze_token_format(
    source: str,
    translated: str,
    newline_policy: NewlinePolicy,
) -> FormatAnalysis:
    source_occurrences = _token_occurrences(source)
    target_occurrences = _token_occurrences(translated)
    return FormatAnalysis(
        source_occurrences=source_occurrences,
        target_occurrences=target_occurrences,
        diagnostics=_token_diagnostics(
            source,
            translated,
            source_occurrences,
            target_occurrences,
            newline_policy=newline_policy,
        ),
    )


def _analyze_guide_format(
    source: str,
    translated: str,
    newline_policy: NewlinePolicy,
) -> FormatAnalysis:
    from translation_tool.core.guide_format import (
        collect_guide_diagnostics,
        collect_guide_marks,
        guide_issues,
    )

    token_result = _analyze_token_format(source, translated, newline_policy)
    if not source:
        # 原文是空的就沒有結構可對照；只檢查一般 token（與舊的 guide_structure_issues 相同）
        return token_result
    source_marks = collect_guide_marks(source)
    target_marks = collect_guide_marks(translated)
    source_occurrences = list(token_result.source_occurrences)
    target_occurrences = list(token_result.target_occurrences)
    source_occurrences.extend(
        _span(mark.identifier, mark.start, mark.end, source) for mark in source_marks
    )
    target_occurrences.extend(
        _span(mark.identifier, mark.start, mark.end, translated)
        for mark in target_marks
    )
    source_occurrences.sort(key=lambda item: (item.start, item.end))
    target_occurrences.sort(key=lambda item: (item.start, item.end))
    guide_diagnostics = tuple(
        collect_guide_diagnostics(
            source,
            translated,
            source_marks=source_marks,
            target_marks=target_marks,
        )
    )
    return FormatAnalysis(
        source_occurrences=tuple(source_occurrences),
        target_occurrences=tuple(target_occurrences),
        diagnostics=token_result.diagnostics + guide_diagnostics,
        guide_issues=tuple(
            guide_issues(
                source,
                translated,
                source_marks=source_marks,
                target_marks=target_marks,
            )
        ),
    )


def _analyze_unimplemented_format(
    _source: str,
    _translated: str,
    _newline_policy: NewlinePolicy,
) -> FormatAnalysis:
    raise NotImplementedError("尚未提供此格式的格式分析器")


_ANALYZERS: dict[str, FormatAnalyzer] = {
    "token": _analyze_token_format,
    "guide": _analyze_guide_format,
    "unimplemented": _analyze_unimplemented_format,
}


def _resolve_analyzer(spec: AnalyzerSpec) -> tuple[FormatAnalyzer, str]:
    if isinstance(spec, str):
        try:
            return _ANALYZERS[spec], spec
        except KeyError as exc:
            raise ValueError(f"未知格式分析器：{spec}") from exc
    if not callable(spec):
        raise TypeError("格式分析器必須是已知名稱或 callable")
    return spec, getattr(spec, "__name__", "custom")


def _normalize_policy(policy: FormatKindPolicy) -> FormatKindPolicy:
    analyzer, default_name = _resolve_analyzer(policy.analyzer)
    return replace(
        policy,
        analyzer=analyzer,
        analyzer_name=policy.analyzer_name or default_name,
        has_structured_marks=policy.has_structured_marks or default_name == "guide",
    )


_POLICIES: dict[str, FormatKindPolicy] = {
    "lang": _normalize_policy(
        FormatKindPolicy(
            "lang",
            "lang",
            "lang",
            _analyze_token_format,
            "hard",
            analyzer_name="token",
        )
    ),
    "guide": _normalize_policy(
        FormatKindPolicy(
            "guide",
            "patchouli",
            "guide",
            _analyze_guide_format,
            "soft",
            analyzer_name="guide",
            has_structured_marks=True,
        )
    ),
    "patchouli": _normalize_policy(
        FormatKindPolicy(
            "patchouli",
            "patchouli",
            "patchouli",
            _analyze_guide_format,
            "soft",
            analyzer_name="guide",
            has_structured_marks=True,
        )
    ),
    # Advancement text follows the Patchouli cache route, but its format
    # contract is the token scanner and real line breaks remain significant.
    "advancement": _normalize_policy(
        FormatKindPolicy(
            "advancement",
            "patchouli",
            "advancement",
            _analyze_token_format,
            "hard",
            analyzer_name="token",
        )
    ),
}

_LEGACY_TOKEN_POLICY = _POLICIES["lang"]


def register_format_kind(policy: FormatKindPolicy) -> None:
    """Register a format policy and its analyzer strategy.

    New kinds provide their analyzer callable here; consumers do not need a
    kind-specific branch.  String analyzer names remain accepted only as a
    migration boundary for existing callers.
    """
    if not policy.kind or not policy.cache_type or not policy.prompt_profile:
        raise ValueError("格式 kind、cache route 與 prompt profile 不可為空")
    if policy.newline_policy not in {"hard", "soft"}:
        raise ValueError(f"未知換行策略：{policy.newline_policy}")
    _POLICIES[policy.kind] = _normalize_policy(policy)


def format_kind_policy(kind: str | None) -> FormatKindPolicy:
    """Return a registered policy or an explicit legacy token fallback.

    Older database rows can contain kinds introduced before the registry.  They
    retain the historical hard-newline token contract, but the returned policy
    marks this fallback explicitly so new kinds can be required to register an
    analyzer rather than silently inheriting one.
    """
    normalized = str(kind or "lang")
    policy = _POLICIES.get(normalized)
    if policy is not None:
        return policy
    return replace(
        _LEGACY_TOKEN_POLICY,
        kind=normalized,
        cache_type=normalized,
        is_legacy_fallback=True,
    )


def analyze_format_diagnostics(
    source: str,
    translated: str,
    *,
    kind: str = "lang",
    repair: bool = False,
) -> FormatDiagnosticsResult:
    """Analyze one translation using the registered kind policy.

    ``repair`` is part of the common contract so callers do not need separate
    token/guide gates.  Current policies share the same diagnostics for repair
    and normal validation; the flag is reserved for future repair-only rules.
    """
    del repair
    source = source or ""
    translated = translated or ""
    policy = format_kind_policy(kind)
    analyzer = policy.analyzer
    if isinstance(analyzer, str):  # Defensive guard for hand-built policies.
        analyzer, _ = _resolve_analyzer(analyzer)
    analysis = analyzer(source, translated, policy.newline_policy)
    source_counts = Counter(item.token for item in analysis.source_occurrences)
    target_counts = Counter(item.token for item in analysis.target_occurrences)
    soft_newline_counts = None
    if policy.newline_policy == "soft":
        # 實際換行數量取自分析器已掃描的 token，不必再掃描一次文字
        lf_counts = (source_counts["\n"], target_counts["\n"])
        if lf_counts[0] != lf_counts[1]:
            soft_newline_counts = lf_counts
    return FormatDiagnosticsResult(
        source_text=source,
        target_text=translated,
        kind=policy.kind,
        source_counts=tuple(sorted(source_counts.items())),
        target_counts=tuple(sorted(target_counts.items())),
        source_occurrences=analysis.source_occurrences,
        target_occurrences=analysis.target_occurrences,
        diagnostics=analysis.diagnostics,
        guide_issues=analysis.guide_issues,
        soft_newline_counts=soft_newline_counts,
    )


__all__ = [
    "DiagnosticDirection",
    "FormatAnalysis",
    "FormatAnalyzer",
    "FormatDiagnostic",
    "FormatDiagnosticsResult",
    "FormatKindPolicy",
    "FormatSpan",
    "LocationConfidence",
    "analyze_format_diagnostics",
    "format_identifier_label",
    "format_kind_policy",
    "register_format_kind",
]
