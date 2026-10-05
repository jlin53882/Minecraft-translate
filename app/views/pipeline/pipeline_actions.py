"""流水線各步驟的「動作」：把 View 的請求轉成對 service 層的呼叫（#114）。

這一層**不碰 UI、不開執行緒、不建立 session**：它只接收 ``TaskSession``（由 ``PipelineRunner``
建立並在背景執行緒傳入）與參數，呼叫對應的 service。因此：

- ``PipelineView`` 只依賴這個穩定的邊界（``PipelineActions`` 的公開方法），不再 import ``run_*``；
- 測試可以注入 ``FakePipelineActions``（View 行為），或以 ``PipelineServices`` 注入假的 service
  （動作本身的委派行為），不需要 patch 任何 module-level 名稱或 ``threading.Thread``。

回傳 generator 的動作（merge／bundle）要由呼叫端完整迭代（``PipelineRunner.run_step`` 會做）。
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from functools import partial
from typing import Any

from app.services_impl.pipelines.bundle_service import (
    build_bundle_staging,
    run_bundling_service,
)
from app.services_impl.pipelines.extract_service import (
    run_book_extraction_service,
    run_lang_extraction_service,
)
from app.services_impl.pipelines.lm_service import run_lm_translation_service
from app.services_impl.pipelines.merge_service import (
    run_merge_folder_batch_service,
    run_merge_zip_batch_service,
)
from app.tasks.task_session import TaskSession
from app.views.pipeline.pipeline_config import PipelineConfig, _has_files
from translation_tool.utils.cancellation import is_cancelled
from translation_tool.utils.log_unit import log_error, log_info


@dataclass(frozen=True)
class PipelineServices:
    """動作依賴的 service 函式（預設為真正的 service；測試可整組或部分替換）。"""

    extract_lang: Callable[..., Any] = run_lang_extraction_service
    extract_book: Callable[..., Any] = run_book_extraction_service
    merge_folder: Callable[..., Any] = run_merge_folder_batch_service
    merge_zip: Callable[..., Any] = run_merge_zip_batch_service
    translate: Callable[..., Any] = run_lm_translation_service
    bundle: Callable[..., Any] = run_bundling_service
    build_staging: Callable[..., Any] = build_bundle_staging


def _staging_reuse_note(stats: dict[str, Any]) -> str:
    """增量 staging 的補充說明；舊版 / 測試替身沒有這些欄位時不顯示。"""
    unchanged, removed = stats.get("unchanged"), stats.get("removed")
    if not unchanged and not removed:
        return ""
    return f"（沿用未變更 {unchanged or 0} 個、移除殘留 {removed or 0} 個）"


def session_failed(session: TaskSession) -> bool:
    """任務失敗：session 標記錯誤，或摘要中有失敗項目。"""
    if session.error:
        return True
    summary = session.snapshot().get("summary") or {}
    return any(
        summary.get(key, 0)
        for key in ("failed_zips", "failed_folders", "errored_files")
    )


class PipelineActions:
    """流水線步驟動作；View／Runner 透過這些公開方法呼叫 service。"""

    def __init__(self, services: PipelineServices | None = None) -> None:
        self.services = services or PipelineServices()

    # ------------------------------------------------------------------ 單一步驟

    def extract(
        self,
        session: TaskSession,
        mods_dir: str,
        output_dir: str,
        mode: str,
        lang_codes: list[str],
    ) -> None:
        """抽取資源：lang／book／dual；dual 時 lang 失敗就不執行 book。"""
        cfg = PipelineConfig(mods_dir, output_dir)
        self._extract_into(session, cfg, mode, lang_codes)

    def merge(
        self,
        session: TaskSession,
        input_src,
        output_dir: str,
        input_mode: str,
        *,
        only_lang: bool,
        process_zh_cn: bool,
        patchouli_skip: bool,
        patchouli_threshold: float,
        zh_en_threshold: int,
    ) -> Iterator:
        """語系比對：資料夾模式走 folder service，ZIP 模式走 zip service。"""
        options = {
            "output_dir": output_dir,
            "only_process_lang": only_lang,
            "process_zh_cn": process_zh_cn,
            "patchouli_skip": patchouli_skip,
            "patchouli_threshold": patchouli_threshold,
            "zh_en_threshold": zh_en_threshold,
        }
        session.start()
        os.makedirs(output_dir, exist_ok=True)
        if input_mode == "folder":
            return self.services.merge_folder(
                input_dir=input_src, session=session, **options
            )
        zip_paths = input_src if isinstance(input_src, list) else [input_src]
        return self.services.merge_zip(zip_paths=zip_paths, session=session, **options)

    def translate(
        self,
        session: TaskSession,
        input_dir: str,
        output_dir: str,
        *,
        dry_run: bool,
        write_new_cache: bool,
    ) -> None:
        """啟動翻譯（Gemini 批次）。"""
        os.makedirs(output_dir, exist_ok=True)
        self.services.translate(
            input_dir=input_dir,
            output_dir=output_dir,
            session=session,
            dry_run=dry_run,
            export_lang=False,
            write_new_cache=write_new_cache,
        )

    def bundle(
        self, session: TaskSession, *, manage_session: bool = True, **kwargs
    ) -> Iterator:
        """打包資源：把 service 的 generator 更新寫進 session（日誌／進度／錯誤）。

        ``manage_session=True`` 時負責完整的 session 生命週期（``start()`` 登記為 active，
        結束或被取消關閉時 ``finish()``；已 ``set_error()`` 的維持 ERROR），讓全域
        ``TaskManager``（topbar／取消）看得到這個任務。一鍵流程的步驟 4 已先寫入暫存日誌，
        由步驟自己管理生命週期，所以傳 ``manage_session=False``。
        """
        if manage_session:
            session.start()
        try:
            os.makedirs(
                os.path.dirname(kwargs["output_zip_path"]) or ".", exist_ok=True
            )
            for update_dict in self.services.bundle(**kwargs):
                if update_dict.get("log"):
                    session.add_log(update_dict["log"])
                if update_dict.get("progress") is not None:
                    session.set_progress(update_dict["progress"])
                if update_dict.get("error"):
                    session.set_error()
                    return
                yield update_dict
        except Exception:
            session.set_error()  # 順序一定是 set_error() → finish()，TaskManager 才會記成 ERROR
            raise
        finally:
            if manage_session:
                session.finish()

    # ------------------------------------------------------------------ 一鍵製作

    def one_click_steps(
        self, config: dict, cfg: PipelineConfig, mode, lang_codes, merge_options
    ) -> list[tuple[int, str, Callable]]:
        """一鍵製作的四個步驟 ``(步驟編號, 名稱, fn(session))``：抽取、語系比對、翻譯、打包。

        每個步驟 = 一個 session = 一次 ``start()`` + 一次 terminal ``finish()``（失敗先 ``set_error()``）。
        """

        def extract(session):
            self._extract_into(session, cfg, mode, lang_codes, mods_dir=cfg.input_dir)

        return [
            (1, "抽取資源", extract),
            (
                2,
                "語系比對",
                partial(self._step_merge, config, cfg, mode, merge_options),
            ),
            (3, "啟動翻譯", partial(self._step_translate, config, cfg)),
            (4, "打包資源", partial(self._step_bundle, config, cfg)),
        ]

    def _step_merge(self, config, cfg, mode, merge_options, session):
        # 各抽取結果分別合併（lang 只處理語言檔，book 需處理 Patchouli 內容）
        # 兩個來源共用同一個 session（finish_session=False），由這個步驟擁有生命週期：
        # 任何結束路徑（成功／來源失敗／例外／取消）都只 finish 一次，失敗時先 set_error()
        session.start()
        try:
            os.makedirs(cfg.merge_output_dir, exist_ok=True)
            sources = []
            if mode in ("lang", "dual"):
                # 「只處理 lang 檔案」開關（一鍵對話框步驟 2）；book 來源固定要處理 Patchouli 內容
                sources.append(
                    (cfg.extract_lang_output_dir, config.get("only_lang", True))
                )
            if mode in ("book", "dual"):
                sources.append((cfg.extract_book_output_dir, False))
            total_sources = len(sources)
            for source_index, (src, only_lang) in enumerate(sources):
                yield from self.services.merge_folder(
                    input_dir=src,
                    session=session,
                    only_process_lang=only_lang,
                    progress_start=source_index / total_sources,
                    progress_end=(source_index + 1) / total_sources,
                    finish_session=False,
                    **merge_options,
                )
                if session_failed(session) or is_cancelled():
                    return
        except Exception:
            session.set_error()
            raise
        finally:
            session.finish()

    def _step_translate(self, config, cfg, session):
        # 一個步驟 = 一個 session = 一次 start() + 一次 terminal finish()：
        # 多個來源（lang／patchouli）依序翻譯但共用 session（manage_session=False），
        # 所以不會清掉前一個來源的日誌，也不會在 TaskManager 留下重複的完成紀錄
        session.start()
        try:
            inputs = [d for d in cfg.translate_input_dirs if _has_files(d)]
            if not inputs:
                message = "[系統] 沒有待翻譯內容，略過翻譯"
                session.add_log(message)
                log_info(message, extra={"ui_mirrored": True})
                return
            os.makedirs(cfg.translate_output_dir, exist_ok=True)
            for src in inputs:
                self.services.translate(
                    input_dir=src,
                    output_dir=cfg.translate_output_dir,
                    session=session,
                    dry_run=config.get("dry_run", False),
                    export_lang=False,
                    write_new_cache=config.get("write_new_cache", True),
                    manage_session=False,
                )
                if session.error or is_cancelled():  # 失敗或取消：不跑下一個來源
                    return
        except Exception:
            session.set_error()
            raise
        finally:
            session.finish()

    def _step_bundle(self, config, cfg, session):
        session.start()
        try:
            stats = self.services.build_staging(
                cfg.bundle_sources, cfg.bundle_staging_dir
            )
            message = (
                f"[系統] 打包暫存完成：複製 {stats['copied']} 個、合併 {stats['merged']} 個檔案"
                + _staging_reuse_note(stats)
            )
            session.add_log(message)
            log_info(message, extra={"ui_mirrored": True})
            if not stats["copied"] and not stats["merged"]:
                message = "❌ 沒有可打包的翻譯檔案"
                session.add_log(message, level="error")
                log_error(message, extra={"ui_mirrored": True})
                session.set_error()
                return
            yield from self.bundle(
                session,
                manage_session=False,
                input_root_dir=cfg.bundle_staging_dir,
                output_zip_path=config.get("zip_output") or cfg.bundle_output_zip,
                description=config.get("description", ""),
                min_format=config.get("min_format") or 0,
                max_format=config.get("max_format") or 0,
                pack_image_path=config.get("pack_image"),
                extra_folders=config.get("extra_folders", []),
            )
        except Exception:
            session.set_error()
            raise
        finally:
            session.finish()

    # ------------------------------------------------------------------ 內部

    def _extract_into(
        self,
        session: TaskSession,
        cfg: PipelineConfig,
        mode: str,
        lang_codes: list[str],
        mods_dir: str | None = None,
    ) -> None:
        source = mods_dir if mods_dir is not None else cfg.input_dir
        # dual：兩段抽取共用同一個 session 生命週期與 0~1 進度區間，由這裡擁有 start()／finish()：
        # 任何結束路徑（成功／lang 失敗／例外／取消）都 finish 一次，失敗時先 set_error()
        dual = mode == "dual"
        if dual:
            session.start()
        try:
            if mode in ("lang", "dual"):
                os.makedirs(cfg.extract_lang_output_dir, exist_ok=True)
                self.services.extract_lang(
                    source,
                    cfg.extract_lang_output_dir,
                    session,
                    lang_codes=lang_codes,
                    manage_session=not dual,
                    progress_start=0.0,
                    progress_end=0.5 if dual else 1.0,
                )
                if session.error or is_cancelled():  # lang 失敗或取消：不跑 book
                    return
            if mode in ("book", "dual"):
                os.makedirs(cfg.extract_book_output_dir, exist_ok=True)
                self.services.extract_book(
                    source,
                    cfg.extract_book_output_dir,
                    session,
                    lang_codes=lang_codes,
                    manage_session=not dual,
                    progress_start=0.5 if dual else 0.0,
                    progress_end=1.0,
                )
        except Exception:
            if dual:
                session.set_error()
            raise
        finally:
            if dual:
                session.finish()
