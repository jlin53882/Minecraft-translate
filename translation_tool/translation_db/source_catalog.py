"""Stable source identities and database-scoped display names."""

from __future__ import annotations

from dataclasses import dataclass

from translation_tool.translation_db.schema import (
    BUILTIN_SOURCE_NAMES,
    CUSTOM_SOURCE_BASE,
    SRC_AI,
    SRC_CUSTOM,
    SRC_I18N,
    SRC_JAR_CN,
    SRC_JAR_TW,
    SRC_MANUAL,
    SRC_SUBTITLE,
)


@dataclass(frozen=True)
class SourceDefinition:
    code: int
    slug: str
    display_name: str
    legacy_aliases: tuple[str, ...] = ()
    source_kind: str = "builtin"


BUILTIN_SOURCES = (
    SourceDefinition(SRC_AI, "ai", "AI 機翻"),
    SourceDefinition(SRC_JAR_TW, "jar-tw", "模組自帶繁中"),
    SourceDefinition(SRC_JAR_CN, "jar-cn", "簡中轉繁"),
    SourceDefinition(
        SRC_SUBTITLE,
        "subtitle",
        "釘宮翻譯組",
        legacy_aliases=("町宮字幕組",),
    ),
    SourceDefinition(SRC_I18N, "i18n", "i18n 轉換"),
    SourceDefinition(SRC_CUSTOM, "custom-supplement", "自訂補充"),
    SourceDefinition(SRC_MANUAL, "manual", "人工"),
)
_BUILTIN_BY_SLUG = {source.slug: source for source in BUILTIN_SOURCES}
_BUILTIN_BY_CODE = {source.code: source for source in BUILTIN_SOURCES}


@dataclass(frozen=True)
class SourceCatalog:
    """A built-in catalog plus custom identities registered by one database.

    Custom names are scoped to this immutable value. A database containing a
    historical custom source named like a newer built-in keeps the custom code;
    its display label and serialized token remain unambiguous.
    """

    custom_sources: tuple[tuple[str, int], ...] = ()

    @classmethod
    def from_registry(cls, registry: dict[str, int] | None = None) -> SourceCatalog:
        rows = (
            (str(name), int(code))
            for name, code in (registry or {}).items()
            if isinstance(code, int) and code >= CUSTOM_SOURCE_BASE
        )
        return cls(tuple(sorted(rows, key=lambda pair: (pair[1], pair[0]))))

    @property
    def custom_codes(self) -> tuple[int, ...]:
        return tuple(
            sorted(
                {code for _, code in self.custom_sources if code >= CUSTOM_SOURCE_BASE}
            )
        )

    @property
    def codes(self) -> tuple[int, ...]:
        return tuple(sorted(set(BUILTIN_SOURCE_NAMES) | set(self.custom_codes)))

    def token_for(self, code: int) -> str:
        source = _BUILTIN_BY_CODE.get(int(code))
        if source is not None:
            return f"builtin:{source.slug}"
        if int(code) in self.custom_codes:
            return f"custom:{int(code)}"
        raise ValueError(f"未知來源代碼：{code}")

    def resolve(self, value: object) -> int | None:
        """Resolve a stable token or compatible display/legacy name."""
        text = str(value or "").strip()
        if text.startswith("builtin:"):
            source = _BUILTIN_BY_SLUG.get(text.partition(":")[2])
            return source.code if source else None
        if text.startswith("custom:"):
            try:
                code = int(text.partition(":")[2])
            except ValueError:
                return None
            return code if code in self.custom_codes else None

        # The retired name always remains an alias of the built-in source 3.
        for source in BUILTIN_SOURCES:
            if text in source.legacy_aliases:
                return source.code

        # A database-local legacy collision takes precedence over the new
        # built-in display name. Stable tokens can explicitly select either.
        for name, code in self.custom_sources:
            if text == name:
                return code
            suffix = f"{name}（自訂 #{code}）"
            if text == suffix:
                return code
        for source in BUILTIN_SOURCES:
            if text == source.display_name:
                return source.code
        return None

    def label(self, code: int | None) -> str:
        if code is None:
            return "—"
        code = int(code)
        source = _BUILTIN_BY_CODE.get(code)
        if source is not None:
            return source.display_name
        for name, registered_code in self.custom_sources:
            if registered_code == code:
                if name in BUILTIN_SOURCE_NAMES.values():
                    return f"{name}（自訂 #{code}）"
                return name
        return f"來源 {code}"

    def ordered_codes(
        self, priority: tuple[int, ...] | list[int] | None = None
    ) -> tuple[int, ...]:
        """Return configured codes followed by every remaining known source."""
        requested = tuple(priority or ())
        return tuple(dict.fromkeys((*requested, *self.codes)))


DEFAULT_SOURCE_CATALOG = SourceCatalog()
