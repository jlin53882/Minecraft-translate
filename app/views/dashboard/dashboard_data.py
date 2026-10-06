"""app/views/dashboard/dashboard_data.py：工作台要顯示的資料（純邏輯，不依賴 Flet）。

資料來源都是「真的」：快取概覽、替換規則數、API Key 健康度（#113）、本次執行的任務紀錄（TaskManager）。
不顯示沒有來源的假數字。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, field

from app.services_impl.key_health_service import KeyHealth, ModelQuotaHealth
from app.shell.task_manager import STATUS_ERROR, TaskInfo
from app.shell.topbar import KeySummary, summarize_keys

# 翻譯流程五步：(頁面 key, 標題, 說明)
PIPELINE_STEPS = (
    ("extractor", "提取資源", "從 JAR 取出語言檔與手冊"),
    ("merge", "語系合併", "整理出真正需要翻譯的條目"),
    ("lm", "機器翻譯", "Gemini 批次翻譯"),
    ("qc", "QC 檢驗", "找出缺漏與簡繁不一致"),
    ("bundler", "打包輸出", "產生資源包 ZIP"),
)

STEP_PENDING = "pending"
STEP_RUNNING = "running"
STEP_DONE = "done"
STEP_FAILED = "failed"

# 快取類型的顯示名稱
CACHE_TYPE_LABELS = {
    "lang": "Lang 語言檔",
    "patchouli": "Patchouli 手冊",
    "ftbquests": "FTB Quests",
    "kubejs": "KubeJS",
    "md": "Markdown",
}


@dataclass(frozen=True)
class StepStatus:
    key: str
    title: str
    desc: str
    status: str
    detail: str = ""


@dataclass(frozen=True)
class CacheBar:
    label: str
    entries: int
    share: float  # 佔最大值的比例，給長條圖用


@dataclass
class DashboardData:
    total_entries: int = 0
    cache_bars: list[CacheBar] = field(default_factory=list)
    rules_count: int | None = None
    keys: KeySummary | None = None
    key_rows: list[KeyHealth] = field(default_factory=list)
    model_rows: list[ModelQuotaHealth] = field(
        default_factory=list
    )  # 今日配額用盡的模型
    steps: list[StepStatus] = field(default_factory=list)
    activity: list[TaskInfo] = field(default_factory=list)
    tasks_done: int = 0
    tasks_failed: int = 0
    tasks_running: int = 0


def greeting(now: dt.datetime | None = None) -> str:
    """依時段的問候語。"""
    hour = (now or dt.datetime.now().astimezone()).hour
    if 5 <= hour < 12:
        return "早安"
    if 12 <= hour < 18:
        return "午安"
    return "晚安"


def format_count(value: int | None) -> str:
    """數字加千分位；None 顯示破折號。"""
    return "—" if value is None else f"{value:,}"


def format_ago(seconds: float) -> str:
    """距今多久，例如「剛剛」「5 分鐘前」「2 小時前」。"""
    if seconds < 30:
        return "剛剛"
    if seconds < 3600:
        return f"{max(1, int(seconds // 60))} 分鐘前"
    if seconds < 86400:
        return f"{int(seconds // 3600)} 小時前"
    return f"{int(seconds // 86400)} 天前"


def build_cache_bars(overview: dict | None) -> list[CacheBar]:
    """快取概覽 → 各類型的長條（依筆數由多到少，沒有資料的類型也列出）。"""
    types = (overview or {}).get("types") or {}
    rows = [
        (CACHE_TYPE_LABELS.get(name, name), int((info or {}).get("entries_count", 0)))
        for name, info in types.items()
    ]
    rows.sort(key=lambda r: -r[1])
    top = max((count for _label, count in rows), default=0)
    return [
        CacheBar(label, count, count / top if top else 0.0) for label, count in rows
    ]


def build_step_statuses(
    active: Sequence[TaskInfo], recent: Sequence[TaskInfo]
) -> list[StepStatus]:
    """依本次執行的任務紀錄，決定五個流程步驟的狀態。

    進行中 > 最近一次結果（完成 / 失敗）> 待執行。「一鍵流水線」與「任務翻譯」不屬於單一步驟，
    不影響這裡。
    """
    steps = []
    for key, title, desc in PIPELINE_STEPS:
        running = next((t for t in active if t.view_key == key), None)
        last = next((t for t in recent if t.view_key == key), None)
        if running is not None:
            steps.append(
                StepStatus(key, title, desc, STEP_RUNNING, f"{running.percent}%")
            )
        elif last is not None:
            failed = last.status == STATUS_ERROR
            steps.append(
                StepStatus(
                    key,
                    title,
                    desc,
                    STEP_FAILED if failed else STEP_DONE,
                    "上次失敗" if failed else "本次完成",
                )
            )
        else:
            steps.append(StepStatus(key, title, desc, STEP_PENDING))
    return steps


def build_dashboard_data(
    *,
    cache_overview: dict | None,
    rules_count: int | None,
    key_snapshot: Sequence[KeyHealth],
    model_quota: Sequence[ModelQuotaHealth] = (),
    active: Sequence[TaskInfo],
    recent: Sequence[TaskInfo],
    activity_limit: int = 6,
) -> DashboardData:
    """把各來源的資料整理成工作台要顯示的樣子。"""
    overview = cache_overview or {}
    activity = sorted([*active, *recent], key=lambda t: t.started_at, reverse=True)[
        :activity_limit
    ]
    return DashboardData(
        total_entries=int(overview.get("total_entries", 0) or 0),
        cache_bars=build_cache_bars(overview),
        rules_count=rules_count,
        keys=summarize_keys(key_snapshot),
        key_rows=list(key_snapshot),
        model_rows=list(model_quota),
        steps=build_step_statuses(active, recent),
        activity=activity,
        tasks_done=sum(1 for t in recent if t.status != STATUS_ERROR),
        tasks_failed=sum(1 for t in recent if t.status == STATUS_ERROR),
        tasks_running=len(active),
    )
