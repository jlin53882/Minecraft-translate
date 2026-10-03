from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RulesTableState:
    page_size: int = 50
    current_page: int = 1
    total_pages: int = 1
    rid_seq: int = 0
