"""重開後續跑：偵測上次沒做完的翻譯，並判斷能不能接續（#151、#164、ADR-0001 方案 C）。

涵蓋機器翻譯頁（``lm_directory``）與 FTB／KubeJS／MD 翻譯（``ftbquests``／``kubejs``／``md``，
標記由 ``plugin_resume`` 寫出）。

設計重點
- 這裡的函式都是**唯讀**，不呼叫任何翻譯 API、不消耗額度；是否續跑由使用者在 UI 決定。
- 已完成批次的譯文存在翻譯快取（每批 fsync），checkpoint 只是「任務沒做完」的標記與選項。
  續跑 = 以同一組輸入與選項重新執行：快取命中的項目直接寫回輸出，只翻剩下的。
- 輸入內容與上次不同（指紋不符）、舊格式標記時回報「無法續跑」與原因，不能靜默忽略。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from translation_tool.core import lm_translator, plugin_resume
from translation_tool.utils.config_manager import load_config

LM_KIND = "lm_directory"
KIND_LABELS = {
    LM_KIND: "機器翻譯",
    "ftbquests": "FTB 任務翻譯",
    "kubejs": "KubeJS 翻譯",
    "md": "Markdown 翻譯",
}


@dataclass(frozen=True)
class InterruptedTask:
    """上次被中斷（取消、失敗、關閉）而未完成的翻譯任務。"""

    input_dir: str
    output_dir: str
    export_lang: bool
    write_new_cache: bool
    completed: int
    total: int
    updated_at: str
    fingerprint: str | None
    version: int | None
    kind: str = LM_KIND
    options: dict[str, Any] = field(default_factory=dict)

    @property
    def label(self) -> str:
        """給使用者看的流程名稱。"""
        return KIND_LABELS.get(self.kind, self.kind)

    @property
    def has_current_format(self) -> bool:
        """舊版 checkpoint（沒有版本與選項）無法驗證輸入是否相同，也無法沿用選項。"""
        return self.version == lm_translator.CHECKPOINT_VERSION


@dataclass(frozen=True)
class ResumeCheck:
    """續跑前檢查結果；``ok`` 為 False 時 ``reason`` 是給使用者看的原因。"""

    ok: bool
    reason: str = ""


def _as_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def peek_interrupted_task() -> InterruptedTask | None:
    """讀取上次未完成的任務；沒有（或檔案損毀已記錄警告）時回傳 None。唯讀、不碰 API。"""
    data = lm_translator.load_checkpoint()
    if not isinstance(data, dict):
        return None
    input_dir = data.get("input_dir")
    if not isinstance(input_dir, str) or not input_dir:
        # 舊格式沒有 input_dir：仍要讓使用者看到並能放棄，不能讓檔案默默留著
        input_dir = ""
    return InterruptedTask(
        input_dir=input_dir,
        output_dir=str(data.get("output_dir") or ""),
        export_lang=bool(data.get("export_lang")),
        write_new_cache=data.get("write_new_cache") is not False,
        completed=_as_int(data.get("completed_count")),
        total=_as_int(data.get("total")),
        updated_at=str(data.get("updated_at") or ""),
        fingerprint=data.get("fingerprint"),
        version=data.get("version") if isinstance(data.get("version"), int) else None,
    )


def _plugin_task(kind: str) -> InterruptedTask | None:
    """FTB／KubeJS／MD 的標記。

    沒有標記回傳 None。標記存在但**不是可驗證的版本 2**（舊版 ``JsonCheckpointAdapter`` 寫入專用的
    格式、``kind`` 不符、缺少輸入資料夾）時，仍回傳一個「無法續跑」的任務：使用者會在啟動對話框
    看到原因並可以放棄（清除），不能靜默忽略（#164 驗收條件）。
    """
    data = plugin_resume.read_marker(kind)
    if not isinstance(data, dict):
        return None
    current = (
        data.get("version") == 2
        and data.get("kind") == kind
        and isinstance(data.get("input_dir"), str)
    )
    if not current:
        # 舊格式沒有輸入資料夾；僅供顯示的欄位盡量沿用（舊檔以 target 記錄輸出、processed 記錄進度）
        return InterruptedTask(
            input_dir=str(data.get("input_dir") or ""),
            output_dir=str(data.get("output_dir") or data.get("target") or ""),
            export_lang=False,
            write_new_cache=True,
            completed=_as_int(data.get("completed_count", data.get("processed"))),
            total=_as_int(data.get("total")),
            updated_at=str(data.get("updated_at") or ""),
            fingerprint=None,
            version=None,
            kind=kind,
        )
    options = data.get("options")
    return InterruptedTask(
        input_dir=data["input_dir"],
        output_dir=str(data.get("output_dir") or ""),
        export_lang=False,
        write_new_cache=True,
        completed=_as_int(data.get("completed_count")),
        total=_as_int(data.get("total")),
        updated_at=str(data.get("updated_at") or ""),
        fingerprint=data.get("fingerprint"),
        version=2,
        kind=kind,
        options=options if isinstance(options, dict) else {},
    )


def peek_interrupted_tasks() -> list[InterruptedTask]:
    """所有未完成的翻譯任務（機器翻譯頁＋FTB／KubeJS／MD）。唯讀、不碰 API。"""
    tasks: list[InterruptedTask] = []
    lm_task = peek_interrupted_task()
    if lm_task is not None:
        tasks.append(lm_task)
    for kind in plugin_resume.PLUGIN_KINDS:
        task = _plugin_task(kind)
        if task is not None:
            tasks.append(task)
    return tasks


def _check_plugin_task(task: InterruptedTask) -> ResumeCheck:
    if not task.has_current_format:
        return ResumeCheck(
            False, "舊版續跑標記缺少來源與版本資訊，無法安全續跑；請放棄此任務"
        )
    if not task.fingerprint:
        return ResumeCheck(False, "標記沒有來源指紋，無法驗證輸入是否與上次相同")
    if not task.input_dir or not Path(task.input_dir).is_dir():
        return ResumeCheck(
            False, f"輸入資料夾已不存在：{task.input_dir or '（未記錄）'}"
        )
    current = plugin_resume.compute_source_fingerprint(
        task.kind, task.input_dir, task.output_dir or None
    )
    if current is None:
        return ResumeCheck(False, "無法讀取來源檔案，無法驗證輸入是否與上次相同")
    if current != task.fingerprint:
        return ResumeCheck(False, "輸入內容與上次不同（檔案已變動）")
    return ResumeCheck(True)


def check_resume_feasibility(task: InterruptedTask) -> ResumeCheck:
    """確認目前的輸入與上次相同、可以續跑（會讀取輸入檔案，建議在背景執行緒呼叫）。

    不呼叫翻譯 API。比對方式與執行翻譯時完全相同：機器翻譯頁對抽取出的全部項目計算指紋；
    FTB／KubeJS／MD 對流程會讀取的來源檔案內容計算指紋。
    """
    if task.kind != LM_KIND:
        return _check_plugin_task(task)
    if not task.has_current_format or not task.fingerprint:
        return ResumeCheck(False, "標記是舊版格式，無法驗證輸入是否與上次相同")
    if not task.input_dir or not Path(task.input_dir).is_dir():
        return ResumeCheck(
            False, f"輸入資料夾已不存在：{task.input_dir or '（未記錄）'}"
        )
    try:
        _patchouli, _lang, files = lm_translator.scan_translatable_files(
            Path(task.input_dir).resolve()
        )
        if not files:
            return ResumeCheck(False, "輸入資料夾中找不到可翻譯的檔案")
        work_thread = (
            load_config().get("translator", {}).get("parallel_execution_workers", 4)
        )
        _file_cache, all_items, _events = lm_translator._extract_directory_items(
            files, export_lang=task.export_lang, work_thread=work_thread
        )
    except Exception as exc:  # noqa: BLE001 - 檢查失敗要回報給使用者，不能讓啟動流程中斷
        return ResumeCheck(False, f"讀取輸入資料夾失敗：{exc}")
    current = lm_translator.compute_checkpoint_fingerprint(task.input_dir, all_items)
    if current != task.fingerprint:
        return ResumeCheck(False, "輸入內容與上次不同（檔案或文字已變動）")
    return ResumeCheck(True)


def discard_interrupted_task(task: InterruptedTask | None = None) -> None:
    """使用者選擇放棄：清除標記，不續跑。已寫入快取的譯文會保留。

    ``task`` 省略時清除機器翻譯頁的標記（相容 #151 的呼叫方式）。
    """
    if task is not None and task.kind != LM_KIND:
        plugin_resume.clear_marker(task.kind)
        return
    lm_translator.clear_checkpoint()
