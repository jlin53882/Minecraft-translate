from __future__ import annotations

from dataclasses import dataclass, field

@dataclass
class CacheQueryState:
    query_results: list[dict] = field(default_factory=list)
    query_selected_result: dict | None = None
    query_original_dst: str = ""
    query_page: int = 1
    query_page_size: int = 50
    query_total_pages: int = 1

@dataclass
class CacheShardState:
    selected_type: str = ""
    selected_file: str = ""
    selected_key: str = ""
    keys: list[str] = field(default_factory=list)
    page: int = 1
    page_size: int = 50
    total_pages: int = 1
    src_mode: str = "preview"
    dst_loaded_sig: tuple[str, str, str] | None = None
    dst_original: str = ""

@dataclass
class CacheHistoryState:
    history_window_source: str | None = None
    query_records: list[dict] = field(default_factory=list)
    query_selected_event: dict | None = None
    shard_records: list[dict] = field(default_factory=list)
    shard_selected_event: dict | None = None
