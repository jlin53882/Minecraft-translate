"""批次機翻的進度估算：已處理幾批、預估總批數、已用時間、預估剩餘時間與完成時刻。

純計算、不依賴 UI：服務層每批結束時呼叫 ``update``，再用 ``line`` 組日誌、
用 ``live`` 把即時資料交給畫面。預估會隨實際速度調整，遇到錯誤批次縮小時總批數會跟著增加。
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime


def format_duration(seconds: float | None) -> str:
    """秒數 → ``H:MM:SS``（不足 1 小時為 ``M:SS``）；無法估算時回傳 ``—``。"""
    if seconds is None or seconds < 0 or math.isinf(seconds) or math.isnan(seconds):
        return "—"
    total = round(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def estimate_batches(counts: dict[str, int], size_for) -> int:
    """依各類型筆數與每批大小估計批數；``size_for(類型)`` 回傳該類型每批筆數。"""
    return sum(
        math.ceil(count / max(1, int(size_for(kind))))
        for kind, count in counts.items()
        if count > 0
    )


@dataclass
class RunProgress:
    """一次批次機翻的即時進度。"""

    total: int
    planned_batches: int  # 開始前依設定估的批數
    started: float = field(default_factory=time.time)
    batches_done: int = 0
    processed: int = 0

    def update(self, processed: int) -> bool:
        """回報目前已處理筆數；有增加就視為又完成一批，回傳是否有新批次完成。"""
        processed = max(0, min(int(processed), self.total))
        if processed > self.processed:
            self.processed = processed
            self.batches_done += 1
            return True
        return False

    def estimated_batches(self) -> int:
        """預估總批數：已完成的批數＋以「目前平均每批筆數」估計剩下的批數。"""
        if self.batches_done == 0 or self.processed == 0:
            return max(self.planned_batches, 1)
        average = self.processed / self.batches_done
        remaining = self.total - self.processed
        return self.batches_done + math.ceil(remaining / average)

    def elapsed(self, now: float | None = None) -> float:
        return (now if now is not None else time.time()) - self.started

    def eta_seconds(self, now: float | None = None) -> float | None:
        """依目前平均速度估算剩餘秒數；還沒完成任何一批時無法估算。"""
        if self.processed <= 0:
            return None
        elapsed = self.elapsed(now)
        return elapsed / self.processed * (self.total - self.processed)

    def live(self, now: float | None = None) -> dict:
        """給畫面顯示用的即時資料。"""
        now = now if now is not None else time.time()
        eta = self.eta_seconds(now)
        return {
            "batch_done": self.batches_done,
            "batch_est": self.estimated_batches(),
            "processed": self.processed,
            "total": self.total,
            "elapsed_sec": self.elapsed(now),
            "eta_sec": eta,
            "finish_ts": None if eta is None else now + eta,
            "started_ts": self.started,  # 畫面據此每次輪詢重算「已用時間」
            "updated_ts": now,  # eta_sec 是這個時間點的估計，之後每秒遞減
        }

    def start_line(self) -> str:
        return (
            f"📦 共 {self.total:,} 筆，預估約 {self.planned_batches:,} 批"
            "（依設定的每批筆數；遇到錯誤批次縮小時實際批數會增加，"
            "完成時間會在第一批結束後開始估算）"
        )

    def line(self, now: float | None = None) -> str:
        return format_live(self.live(now))


def tick_live(live: dict, now: float | None = None) -> dict:
    """依現在時間更新即時資料的「已用時間」與「預估剩餘」（每批結束才有新資料，畫面卻要每秒變化）。

    已用時間＝現在 − 開始時間；預估剩餘＝上次估計值 − 上次更新後經過的時間（不低於 0）。
    預計完成時刻不變。資料沒有時間戳（舊格式）時原樣回傳。
    """
    started, updated = live.get("started_ts"), live.get("updated_ts")
    if started is None or updated is None:
        return live
    now = now if now is not None else time.time()
    out = dict(live)
    out["elapsed_sec"] = max(0.0, now - started)
    if live.get("eta_sec") is not None:
        out["eta_sec"] = max(0.0, live["eta_sec"] - max(0.0, now - updated))
    return out


def format_live(live: dict) -> str:
    """即時資料 → 一行文字：批次、筆數、已用時間、預估剩餘、預計完成時刻。"""
    total = live["total"] or 1
    percent = 100 * live["processed"] / total
    parts = [
        f"第 {live['batch_done']:,} / 約 {live['batch_est']:,} 批",
        f"已處理 {live['processed']:,} / {live['total']:,} 筆（{percent:.0f}%）",
        f"已用 {format_duration(live['elapsed_sec'])}",
        f"預估剩餘 {format_duration(live['eta_sec'])}",
    ]
    if live.get("finish_ts"):
        # 顯示本機時間：先以 UTC 建立再轉成本機時區
        finish = datetime.fromtimestamp(live["finish_ts"], tz=UTC).astimezone()
        days = (finish.date() - datetime.now(tz=UTC).astimezone().date()).days
        stamp = finish.strftime("%H:%M") + (f"（+{days} 天）" if days > 0 else "")
        parts.append(f"預計 {stamp} 完成")
    return "｜".join(parts)


__all__ = [
    "RunProgress",
    "estimate_batches",
    "format_duration",
    "format_live",
    "tick_live",
]
