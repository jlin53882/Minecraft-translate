"""共用的額外打包來源契約。

這個模組只負責描述與正規化來源，不負責翻譯或資料庫操作。舊版設定中的
``list[str]`` 仍代表 Resource Pack 來源；新版可以用 mapping 明確指定
``raw_attachment``，讓 ZIP 打包器以原始 bytes 複製一般檔案。
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

RESOURCE_PACK_MODE = "resource_pack"
RAW_ATTACHMENT_MODE = "raw_attachment"
EXTRA_SOURCE_MODES = (RESOURCE_PACK_MODE, RAW_ATTACHMENT_MODE)


@dataclass(frozen=True, slots=True)
class ExtraBundleSource:
    """一個額外打包來源。

    ``target`` 是 ZIP 內的目標目錄前綴；空白時使用來源檔案／資料夾名稱。
    舊版字串會由 :func:`normalize_extra_bundle_sources` 轉成
    ``resource_pack``，因此不會改變既有資源包行為。
    """

    source: str
    mode: str = RESOURCE_PACK_MODE
    recursive: bool = True
    target: str = ""

    def __post_init__(self) -> None:
        source = self.source.strip() if isinstance(self.source, str) else ""
        mode = self.mode.strip().lower() if isinstance(self.mode, str) else ""
        target = self.target.strip() if isinstance(self.target, str) else ""
        if not source:
            raise ValueError("額外來源路徑不可空白")
        if mode not in EXTRA_SOURCE_MODES:
            raise ValueError(
                f"不支援的額外來源模式：{mode!r}，可用值為 "
                f"{', '.join(EXTRA_SOURCE_MODES)}"
            )
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "recursive", bool(self.recursive))
        object.__setattr__(self, "target", target)

    @property
    def is_raw_attachment(self) -> bool:
        return self.mode == RAW_ATTACHMENT_MODE

    def to_dict(self) -> dict[str, Any]:
        """轉成可放入設定／流水線 action 的 JSON 物件。"""
        return {
            "source": self.source,
            "mode": self.mode,
            "recursive": self.recursive,
            "target": self.target,
        }


def _mapping_value(value: Mapping[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        if name in value:
            return value[name]
    return default


def coerce_extra_bundle_source(value: object) -> ExtraBundleSource:
    """將舊字串或新版 mapping 轉成共用來源模型。"""
    if isinstance(value, ExtraBundleSource):
        return value
    if isinstance(value, str):
        return ExtraBundleSource(value)
    if isinstance(value, Mapping):
        source = _mapping_value(value, "source", "path")
        mode = _mapping_value(value, "mode", default=RESOURCE_PACK_MODE)
        recursive = _mapping_value(value, "recursive", default=True)
        target = _mapping_value(value, "target", "target_root", default="")
        if not isinstance(source, str):
            raise TypeError("額外來源設定必須包含字串 source 或 path")
        return ExtraBundleSource(
            source=source,
            mode=mode,
            recursive=recursive,
            target=target,
        )
    raise ValueError("額外來源設定必須是路徑字串或物件")


def normalize_extra_bundle_sources(
    values: Sequence[object] | None,
) -> list[ExtraBundleSource]:
    """正規化額外來源；``None`` 與空值代表沒有額外來源。"""
    return [coerce_extra_bundle_source(value) for value in (values or ())]


def serialize_extra_bundle_sources(values: Sequence[object] | None) -> list[object]:
    """序列化來源，同時保留舊字串輸出以相容舊 action／設定測試。

    UI 新增的 ``ExtraBundleSource`` 會輸出明確 mapping；舊呼叫者傳入的字串
    則原樣保留，讀取端仍會套用 Resource Pack 預設模式。
    """
    serialized: list[object] = []
    for value in values or ():
        if isinstance(value, str):
            serialized.append(value)
        else:
            serialized.append(coerce_extra_bundle_source(value).to_dict())
    return serialized


def source_identity(value: object) -> tuple[str, str, bool, str]:
    """回傳適合 UI 去重與測試比對的來源 identity。"""
    spec = coerce_extra_bundle_source(value)
    return (
        os.path.normcase(os.path.abspath(spec.source)),
        spec.mode,
        spec.recursive,
        spec.target.replace("\\", "/").strip("/"),
    )
