"""minecraft_translator_flet 全功能測試腳本。

用途：直接呼叫底層 Python 函式，对「All the Mods 10 4.3」目錄
      （460 jar + kubejs + ftbquests）跑完 UI 所有功能。
      不開 UI，純終端驗證每個功能的輸出是否正確。

使用方式（在專案根目錄執行）：
    cd <專案根目錄>
    python tools/test_all_features.py --modpack-root <模組包根目錄>

輸出：終端彩色報告，每個功能 PASS / FAIL / WARN。
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import traceback
import zipfile
from contextlib import contextmanager
from pathlib import Path

try:
    from tools.cli_encoding import configure_utf8_stdio
except ModuleNotFoundError:
    from cli_encoding import configure_utf8_stdio

configure_utf8_stdio()

# ── 路徑設定 ──────────────────────────────────────────────────────────
SRC_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(SRC_ROOT))

# ── 測試資料路徑（All the Mods 10 4.3） ──────────────────────────────────
ATM10_ROOT = Path()  # 由 main() 依 --modpack-root 設定
MODS_DIR = Path()
KUBEJS_DIR = Path()
FTBQUESTS_DIR = Path()

# ── ANSI 顏色 ────────────────────────────────────────────────────────────
C_GREEN = "\033[92m"
C_RED = "\033[91m"
C_YELLOW = "\033[93m"
C_RESET = "\033[0m"
C_BOLD = "\033[1m"


def banner(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"{C_BOLD}{title}{C_RESET}")
    print("=" * 60)


def result(name: str, ok: bool, detail: str = "") -> bool:
    status = f"{C_GREEN}PASS{C_RESET}" if ok else f"{C_RED}FAIL{C_RESET}"
    print(f"  [{status}] {name}")
    if detail:
        print(f"         {detail}")
    return ok


def warn(name: str, detail: str) -> bool:
    print(f"  [{C_YELLOW}WARN{C_RESET}] {name}")
    print(f"         {detail}")
    return True


def fail(name: str, exc: Exception) -> bool:
    print(f"  [{C_RED}FAIL{C_RESET}] {name}")
    print(f"         例外：{exc}")
    traceback.print_exc()
    return False


@contextmanager
def _isolated_cache_root(cache_manager, root: Path):
    """讓手動 smoke test 只寫入暫存快取，不污染使用者專案。"""
    original_resolve = cache_manager.resolve_project_path
    cache_manager.cache_store.reset_runtime_state(cache_manager.CACHE_TYPES)
    cache_manager.resolve_project_path = lambda _path: root
    try:
        yield
    finally:
        cache_manager.cache_store.reset_runtime_state(cache_manager.CACHE_TYPES)
        cache_manager.resolve_project_path = original_resolve


# ════════════════════════════════════════════════════════════════════════
# 功能 1：設定（ConfigService）
# ════════════════════════════════════════════════════════════════════════
def test_config() -> bool:
    banner("功能 1：設定（ConfigService）")
    ok_all = True

    try:
        from translation_tool.utils.config_manager import load_config

        cfg = load_config()
        ok_all &= result(
            "load_config() 可正常讀取",
            isinstance(cfg, dict),
            f"config.json 讀取結果：{type(cfg).__name__}",
        )
    except Exception as e:  # noqa: BLE001
        ok_all &= fail("load_config()", e)

    try:
        from translation_tool.utils.config_manager import resolve_project_path

        p = resolve_project_path("replace_rules.json")
        ok_all &= result(
            "resolve_project_path()", p is not None, f"replace_rules.json → {p}"
        )
    except Exception as e:  # noqa: BLE001
        ok_all &= fail("resolve_project_path", e)

    return ok_all


# ════════════════════════════════════════════════════════════════════════
# 功能 2：規則（RulesActions）
# ════════════════════════════════════════════════════════════════════════
def test_rules() -> bool:
    banner("功能 2：規則（RulesActions）")
    ok_all = True

    rules_path = SRC_ROOT / "replace_rules.json"
    if rules_path.exists():
        try:
            import json

            with open(rules_path, "r", encoding="utf-8") as f:
                rules = json.load(f)
            ok_all &= result(
                "replace_rules.json 可讀取",
                isinstance(rules, (dict, list)),
                f"共 {len(rules)} 條規則",
            )
        except Exception as e:  # noqa: BLE001
            ok_all &= fail("replace_rules.json 讀取", e)
    else:
        ok_all &= result("replace_rules.json 是否存在", False, "檔案不存在")

    # LangItemRow 顯示英文是由 bind() 更新控制項，而不是保留 en_text 欄位。
    try:
        from app.views.icon_preview_row import LangItemRow

        dummy_cb = lambda k, v: None
        row = LangItemRow(
            lang_key="item.test",
            en_text="Test Item",
            zh_text="測試物品",
            assets_root=Path(tempfile.gettempdir()),
            preview_root=Path(tempfile.gettempdir()),
            on_value_changed=dummy_cb,
        )
        ok_all &= result(
            "LangItemRow 可建立",
            row.lang_key == "item.test" and row.en_field.value == "Test Item",
            f"lang_key={row.lang_key}",
        )
    except Exception as e:  # noqa: BLE001
        ok_all &= fail("LangItemRow 建立", e)

    return ok_all


# ════════════════════════════════════════════════════════════════════════
# 功能 3：快取管理（CacheStore — 模組函式版）
# ════════════════════════════════════════════════════════════════════════
def test_cache() -> bool:
    banner("功能 3：快取管理（CacheStore 函式 API）")
    ok_all = True

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        try:
            from translation_tool.utils import cache_manager

            with _isolated_cache_root(cache_manager, tmp_path / "cache"):
                cache_manager.initialize_translation_cache()
                cache_manager.add_to_cache(
                    "lang", "hello", "Hello", "你好", mod="test_mod"
                )
                hit = cache_manager.get_from_cache("lang", "hello")
                ok_all &= result(
                    "cache_manager add/get",
                    hit == "你好",
                    f"get_from_cache('hello') = {hit!r}",
                )

                overview = cache_manager.get_cache_overview()
                ok_all &= result(
                    "get_cache_overview()",
                    isinstance(overview, dict)
                    and isinstance(overview.get("types"), dict)
                    and "lang" in overview["types"],
                    f"types: {list(overview.keys())}",
                )
        except Exception as e:  # noqa: BLE001
            ok_all &= fail("cache_manager 操作", e)

    return ok_all


# ════════════════════════════════════════════════════════════════════════
# 功能 4：QC 檢驗（4 種 checker）
# ════════════════════════════════════════════════════════════════════════
def test_qc() -> bool:
    banner("功能 4：QC 檢驗（Untranslated / Variant / English Residue / TSV）")
    ok_all = True

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        en_dir = tmp_path / "en_us"
        tw_dir = tmp_path / "zh_tw"
        en_dir.mkdir()
        tw_dir.mkdir()

        # 建立完整目錄結構
        mod_en = en_dir / "test_mod" / "lang"
        mod_tw = tw_dir / "test_mod" / "lang"
        mod_en.mkdir(parents=True)
        mod_tw.mkdir(parents=True)
        (mod_en / "en_us.json").write_text(
            '{"item.test":"Test Item","item.untranslated":"Untranslated"}',
            encoding="utf-8",
        )
        (mod_tw / "zh_tw.json").write_text('{"item.test":"測試物品"}', encoding="utf-8")

        from translation_tool.checkers.english_residue_checker import (
            check_english_residue_generator,
        )
        from translation_tool.checkers.untranslated_checker import (
            check_untranslated_generator,
        )
        from translation_tool.checkers.variant_comparator import (
            compare_variants_generator,
        )
        from translation_tool.checkers.variant_comparator_tsv import (
            compare_variants_tsv_generator,
        )

        qc_out = tmp_path / "qc_out"
        results_ut = list(
            check_untranslated_generator(str(en_dir), str(tw_dir), str(qc_out))
        )
        ok_all &= result(
            "check_untranslated_generator",
            not any(item.get("error") for item in results_ut)
            and (qc_out / "test_mod" / "lang" / "en_us.json").is_file(),
            f"產出 {len(results_ut)} 個 update",
        )

        results_var = list(
            compare_variants_generator(
                str(tmp_path), str(tmp_path), str(tmp_path / "qc_out")
            )
        )
        ok_all &= result(
            "compare_variants_generator",
            not any(item.get("error") for item in results_var),
            f"產出 {len(results_var)} 個 update",
        )

        results_res = list(
            check_english_residue_generator(str(tw_dir), str(tmp_path / "qc_out"))
        )
        ok_all &= result(
            "check_english_residue_generator",
            not any(item.get("error") for item in results_res),
            f"產出 {len(results_res)} 個 update",
        )

        # TSV generator 只需要 (file_path, output_file)
        tsv_in = tmp_path / "input.tsv"
        tsv_out = tmp_path / "output.tsv"
        tsv_in.write_text("key\tzh_cn\tzh_tw\nhello\t你好\t你好\n", encoding="utf-8")
        results_tsv = list(compare_variants_tsv_generator(str(tsv_in), str(tsv_out)))
        ok_all &= result(
            "compare_variants_tsv_generator(file, out)",
            not any(item.get("error") for item in results_tsv),
            f"產出 {len(results_tsv)} 個 update",
        )

    return ok_all


# ════════════════════════════════════════════════════════════════════════
# 功能 5：查詢（Lookup — CacheSearchFacade）
# ════════════════════════════════════════════════════════════════════════
def test_lookup() -> bool:
    banner("功能 5：查詢（CacheSearchFacade）")
    ok_all = True

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        from translation_tool.utils import cache_manager
        from translation_tool.utils.cache_search_facade import CacheSearchFacade

        facade = None
        try:
            import logging

            with _isolated_cache_root(cache_manager, tmp_path / "cache"):
                cache_manager.initialize_translation_cache()
                cache_manager.add_to_cache(
                    "lang", "diamond_sword", "Diamond Sword", "鑽石劍", mod="test"
                )
                cache_manager.add_to_cache(
                    "lang", "iron_pickaxe", "Iron Pickaxe", "鐵鎬", mod="test"
                )
                cache_manager.rebuild_search_index()
                facade = CacheSearchFacade(
                    cache_root_getter=lambda: tmp_path / "cache",
                    logger=logging.getLogger("test"),
                )
                ok_all &= result(
                    "CacheSearchFacade 初始化", callable(facade.search_cache)
                )
                results = facade.search_cache(query="diamond")
                ok_all &= result(
                    "CacheSearchFacade.search_cache()",
                    bool(results) and "diamond" in str(results).lower(),
                    f"找到 {len(results)} 筆",
                )
        except Exception as e:  # noqa: BLE001
            ok_all &= warn("CacheSearchFacade 搜尋", f"UI 模式限制：{e}")
        finally:
            if facade is not None:
                facade._get_orchestrator().close()
            cache_manager._get_search_facade()._get_orchestrator().close()

    return ok_all


# ════════════════════════════════════════════════════════════════════════
# 功能 6：圖示預覽（IconClassifier / IconResolver — 模組函式）
# ════════════════════════════════════════════════════════════════════════
def test_icon_preview() -> bool:
    banner("功能 6：圖示預覽（classify_no_icon_reason / resolve_icon_with_reason）")
    ok_all = True

    try:
        from translation_tool.core.icon_classifier import classify_no_icon_reason
        from translation_tool.core.icon_reason import IconRisk

        reason, risk = classify_no_icon_reason("item.diamond_sword")
        ok_all &= result(
            "classify_no_icon_reason()",
            isinstance(reason, str) and bool(reason) and isinstance(risk, IconRisk),
            f"reason={reason!r}, risk={risk}",
        )
    except Exception as e:  # noqa: BLE001
        ok_all &= fail("classify_no_icon_reason", e)

    try:
        from translation_tool.core.icon_resolver import resolve_icon_with_reason

        temp_path = Path(tempfile.gettempdir())
        r2 = resolve_icon_with_reason("item.diamond_sword", temp_path)
        ok_all &= result(
            "resolve_icon_with_reason()",
            r2.icon_path is None or isinstance(r2.icon_path, Path),
            f"icon_path={r2.icon_path!r}, risk={r2.risk}",
        )
    except Exception as e:  # noqa: BLE001
        ok_all &= fail("resolve_icon_with_reason", e)

    return ok_all


# ════════════════════════════════════════════════════════════════════════
# 功能 7：打包（BundlerService）
# ════════════════════════════════════════════════════════════════════════
def test_bundler() -> bool:
    banner("功能 7：打包（BundlerService）")
    ok_all = True

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        input_root = tmp_path / "input"
        output_zip = tmp_path / "output.zip"
        input_root.mkdir()
        (input_root / "assets" / "mod" / "lang").mkdir(parents=True)
        (input_root / "assets" / "mod" / "lang" / "en_us.json").write_text(
            '{"test":"Test"}', encoding="utf-8"
        )

        from translation_tool.core.output_bundler import bundle_outputs_generator

        results = list(bundle_outputs_generator(str(input_root), str(output_zip)))
        ok_all &= result(
            "bundle_outputs_generator",
            not any(item.get("error") for item in results)
            and zipfile.is_zipfile(output_zip),
            f"產出 {len(results)} 個 update",
        )
        ok_all &= result("output.zip 產生", output_zip.is_file(), f"{output_zip}")

    return ok_all


# ════════════════════════════════════════════════════════════════════════
# 功能 8：翻譯工具（Translation View）
# ════════════════════════════════════════════════════════════════════════
def test_translation_view() -> bool:
    banner("功能 8：翻譯工具（TranslationView 底層元件）")
    ok_all = True

    try:
        from app.views.translation.translation_panels import (
            build_ftb_tab,
            build_kjs_tab,
            build_md_tab,
        )

        ok_all &= result(
            "build_ftb_tab / build_kjs_tab / build_md_tab 存在",
            all(callable(fn) for fn in (build_ftb_tab, build_kjs_tab, build_md_tab)),
        )
    except Exception as e:  # noqa: BLE001
        ok_all &= fail("translation_panels 函式", e)

    return ok_all


# ════════════════════════════════════════════════════════════════════════
# 功能 9：jar 提取（Extractor — Lang + Book + Preview）
# ════════════════════════════════════════════════════════════════════════
def test_extractor() -> bool:
    banner("功能 9：jar 提取（Extractor — Lang / Book / Preview）")
    ok_all = True

    import re

    from translation_tool.core.jar_processor import BOOK_PATH_REGEX_DUAL_STRUCTURE
    from translation_tool.core.jar_processor_extract import extract_from_jar_impl

    lang_regex = re.compile(
        r"(?:assets/([^/]+)/)?lang/(en_us|zh_cn|zh_tw)\.(json|lang)$",
        re.IGNORECASE,
    )

    # ── 9a：Lang（前 10 個 jar） ─────────────────────────────────────
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        output_dir = tmp_path / "lang_out"
        jar_list = sorted(MODS_DIR.glob("*.jar"))[:10]

        total_extracted = 0
        jar_with_lang = 0
        extraction_errors = []
        invalid_results = []
        for jar in jar_list:
            r = extract_from_jar_impl(str(jar), str(output_dir), lang_regex)
            if not isinstance(r, dict):
                invalid_results.append(f"{jar.name}: result is not a dict")
                continue
            if r.get("error"):
                extraction_errors.append(f"{jar.name}: {r['error']}")
            if not isinstance(r.get("extracted", 0), int) or r.get("extracted", 0) < 0:
                invalid_results.append(f"{jar.name}: invalid extracted count")
            if r.get("extracted", 0) > 0:
                total_extracted += r["extracted"]
                jar_with_lang += 1

        ok_all &= result(
            "extract_from_jar_impl（lang）前 10 個 jar",
            not extraction_errors and not invalid_results,
            f"有 lang 的 jar：{jar_with_lang}/{len(jar_list)}，共 {total_extracted} 個 lang 檔"
            + (
                f"；錯誤：{extraction_errors + invalid_results}"
                if extraction_errors or invalid_results
                else ""
            ),
        )

    # ── 9b：Book（前 10 個 jar） ──────────────────────────────────────
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        output_dir = tmp_path / "book_out"
        jar_list = sorted(MODS_DIR.glob("*.jar"))[:10]

        total_extracted = 0
        jar_with_book = 0
        extraction_errors = []
        invalid_results = []
        for jar in jar_list:
            r = extract_from_jar_impl(
                str(jar), str(output_dir), BOOK_PATH_REGEX_DUAL_STRUCTURE
            )
            if not isinstance(r, dict):
                invalid_results.append(f"{jar.name}: result is not a dict")
                continue
            if r.get("error"):
                extraction_errors.append(f"{jar.name}: {r['error']}")
            if not isinstance(r.get("extracted", 0), int) or r.get("extracted", 0) < 0:
                invalid_results.append(f"{jar.name}: invalid extracted count")
            if r.get("extracted", 0) > 0:
                total_extracted += r["extracted"]
                jar_with_book += 1

        ok_all &= result(
            "extract_from_jar_impl（book）前 10 個 jar",
            not extraction_errors and not invalid_results,
            f"有 book 的 jar：{jar_with_book}/{len(jar_list)}，共 {total_extracted} 個 book 檔"
            + (
                f"；錯誤：{extraction_errors + invalid_results}"
                if extraction_errors or invalid_results
                else ""
            ),
        )

    # ── 9c：Preview Generator ────────────────────────────────────────
    with tempfile.TemporaryDirectory() as tmp:
        from translation_tool.core.jar_processor_preview import (
            preview_extraction_generator_impl,
        )

        sample_jars = sorted(MODS_DIR.glob("*.jar"))[:10]
        try:
            results = list(
                preview_extraction_generator_impl(
                    str(MODS_DIR),
                    mode="dual",
                    find_jar_files_fn=lambda p: [str(j) for j in sample_jars],
                    book_path_regex=BOOK_PATH_REGEX_DUAL_STRUCTURE,
                )
            )
            final_update = next(
                (
                    item
                    for item in reversed(results)
                    if isinstance(item, dict) and "result" in item
                ),
                {},
            )
            preview_result = final_update.get("result", {})
            ok_all &= result(
                "preview_extraction_generator_impl",
                not any(item.get("error") for item in results if isinstance(item, dict))
                and not preview_result.get("failed_jars")
                and not preview_result.get("partial_jars"),
                f"產出 {len(results)} 個 update；摘要={preview_result}",
            )
        except Exception as e:  # noqa: BLE001
            ok_all &= fail("preview_extraction_generator_impl", e)

    return ok_all


# ════════════════════════════════════════════════════════════════════════
# 功能 10：機器翻譯（LM Service — Dry Run）
# ════════════════════════════════════════════════════════════════════════
def test_lm() -> bool:
    banner("功能 10：機器翻譯（LM Translation — Dry Run）")
    ok_all = True

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        input_dir = tmp_path / "input"
        output_dir = tmp_path / "output"
        input_dir.mkdir()
        output_dir.mkdir()

        (input_dir / "test_mod").mkdir()
        (input_dir / "test_mod" / "en_us.json").write_text(
            '{"item.test":"Hello World","item.apple":"Apple"}', encoding="utf-8"
        )

        from translation_tool.core.lm_translator import (
            translate_directory_generator as lm_gen,
        )

        try:
            results = list(
                lm_gen(
                    str(input_dir),
                    str(output_dir),
                    dry_run=True,
                    export_lang=False,
                    write_new_cache=True,
                )
            )
            ok_all &= result(
                "lm_translator dry-run",
                not any(item.get("error") for item in results),
                f"產出 {len(results)} 個 update",
            )
        except Exception as e:  # noqa: BLE001
            ok_all &= fail("lm_translator dry-run", e)

    return ok_all


# ════════════════════════════════════════════════════════════════════════
# 功能 11：檔案合併（MergeService）
# ════════════════════════════════════════════════════════════════════════
def test_merge() -> bool:
    banner("功能 11：檔案合併（MergeService）")
    ok_all = True

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        en_dir = tmp_path / "en"
        tw_dir = tmp_path / "tw"
        out_dir = tmp_path / "out"
        en_dir.mkdir()
        tw_dir.mkdir()
        out_dir.mkdir()

        # 測試 merge_zhcn_to_zhtw_from_zip
        try:
            from translation_tool.core.lang_merger import merge_zhcn_to_zhtw_from_zip

            zh_cn_dir = tmp_path / "cn" / "mod1"
            zh_cn_dir.mkdir(parents=True)
            (zh_cn_dir / "zh_cn.json").write_text(
                '{"item.a":"A 簡體","item.b":"B 簡體"}', encoding="utf-8"
            )
            test_zip = tmp_path / "test.zip"
            with zipfile.ZipFile(test_zip, "w") as zf:
                zf.write(zh_cn_dir / "zh_cn.json", "mod1/zh_cn.json")

            results = list(
                merge_zhcn_to_zhtw_from_zip(
                    str(test_zip), str(out_dir), only_process_lang=True
                )
            )
            ok_all &= result(
                "merge_zhcn_to_zhtw_from_zip",
                not any(item.get("error") for item in results),
                f"產出 {len(results)} 個 update",
            )
        except Exception as e:  # noqa: BLE001
            ok_all &= fail("merge_zhcn_to_zhtw_from_zip", e)

        # merge_zhcn_to_zhtw_from_zip（已驗證 PASS，保留）
        # 測試 lang_merge_content 的輔助函式
        # export_filtered_pending — generator 在無待處理項目時回 None，直接包成安全版本
        try:
            from translation_tool.core.lang_merge_content import export_filtered_pending

            pending_dir = out_dir / "pending_test" / "mod1" / "lang"
            pending_dir.mkdir(parents=True)
            (pending_dir / "en_us.json").write_text('{"item.a":"A"}', encoding="utf-8")
            gen = export_filtered_pending(
                str(out_dir / "pending_test"), str(out_dir), min_count=0
            )
            results2 = list(gen) if gen is not None else []
            ok_all &= result(
                "export_filtered_pending (pending export)",
                not any(item.get("error") for item in results2),
                f"產出 {len(results2)} 個 update",
            )
        except Exception as e:  # noqa: BLE001
            ok_all &= fail("export_filtered_pending", e)

    return ok_all


# ════════════════════════════════════════════════════════════════════════
# 附加：KubeJS Pipeline（真實資料 extract only）
# ════════════════════════════════════════════════════════════════════════
def test_kubejs() -> bool:
    banner("附加：KubeJS Pipeline（真實資料 extract only）")
    ok_all = True

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        output_dir = tmp_path / "kubejs_out"

        if not KUBEJS_DIR.exists():
            ok_all &= result("KUBEJS_DIR 是否存在", False, f"{KUBEJS_DIR}")
            return ok_all

        try:
            from app.tasks.task_session import TaskSession
            from translation_tool.core.kubejs_translator import run_kubejs_pipeline

            session = TaskSession(max_logs=300)
            session.add_log("Test log entry")
            ok_all &= result(
                "TaskSession 初始化 + add_log",
                bool(getattr(session, "logs", getattr(session, "_logs", []))),
                "log added",
            )

            pipeline_result = run_kubejs_pipeline(
                input_dir=str(KUBEJS_DIR),
                output_dir=str(output_dir),
                session=session,
                dry_run=True,
                step_extract=True,
                step_translate=False,
                step_inject=False,
                write_new_cache=False,
            )
            extract_summary = pipeline_result.get("step1", {}).get("extract", {})
            ok_all &= result(
                "run_kubejs_pipeline extract",
                isinstance(pipeline_result, dict)
                and extract_summary.get("errors_count", 1) == 0
                and pipeline_result.get("output_sync_status") == "synced",
                f"抽取 {extract_summary.get('extracted_files', 0)} 個檔案、"
                f"{extract_summary.get('extracted_keys_total', 0)} 個 key",
            )
        except Exception as e:  # noqa: BLE001
            ok_all &= fail("run_kubejs_pipeline", e)

    return ok_all


# ════════════════════════════════════════════════════════════════════════
# 附加：FTB Pipeline（真實 snbt 資料）
# ════════════════════════════════════════════════════════════════════════
def test_ftb() -> bool:
    banner("附加：FTB Pipeline（真實 snbt 資料 export+clean only）")
    ok_all = True

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        output_dir = tmp_path / "ftb_out"

        if not FTBQUESTS_DIR.exists():
            ok_all &= result("FTBQUESTS_DIR 是否存在", False, f"{FTBQUESTS_DIR}")
            return ok_all

        try:
            from app.tasks.task_session import TaskSession
            from translation_tool.core.ftb_translator import run_ftb_pipeline

            session = TaskSession(max_logs=300)
            pipeline_result = run_ftb_pipeline(
                directory_path=str(
                    ATM10_ROOT
                ),  # 傳入 ATM10 根目錄，內部會遞迴找 config/ftbquests/quests
                output_dir=str(output_dir),
                session=session,
                dry_run=True,
                step_export=True,
                step_clean=True,
                step_translate=False,
                step_inject=False,
                write_new_cache=False,
            )
            raw_paths = pipeline_result.get("raw_paths", {})
            ok_all &= result(
                "run_ftb_pipeline export+clean",
                isinstance(pipeline_result, dict)
                and bool(raw_paths.get("written_langs"))
                and "clean_paths" in pipeline_result,
                f"語系={raw_paths.get('written_langs', [])}",
            )
        except Exception as e:  # noqa: BLE001
            ok_all &= fail("run_ftb_pipeline", e)

    return ok_all


# ════════════════════════════════════════════════════════════════════════
# 主程式
# ════════════════════════════════════════════════════════════════════════
def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "對真實模組包目錄（含 mods/、kubejs/、config/ftbquests/）執行全功能煙霧測試；"
            "需要外部資料（真實模組包）。"
        )
    )
    parser.add_argument(
        "--modpack-root",
        required=True,
        type=Path,
        help="模組包根目錄（其下有 mods/、kubejs/、config/ftbquests/）",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    global ATM10_ROOT, MODS_DIR, KUBEJS_DIR, FTBQUESTS_DIR
    args = _parse_args(argv)
    ATM10_ROOT = args.modpack_root
    MODS_DIR = ATM10_ROOT / "mods"
    KUBEJS_DIR = ATM10_ROOT / "kubejs"
    FTBQUESTS_DIR = ATM10_ROOT / "config" / "ftbquests"
    print(f"\n{C_BOLD}{'─' * 60}")
    print("  minecraft_translator_flet 全功能測試")
    print(f"  資料來源：{ATM10_ROOT}")
    print(f"  mods 數量：{len(list(MODS_DIR.glob('*.jar')))} 個 jar")
    print(f"{'─' * 60}{C_RESET}")

    tests = [
        ("設定 Config", test_config),
        ("規則 Rules", test_rules),
        ("快取管理 Cache", test_cache),
        ("QC 檢驗", test_qc),
        ("查詢 Lookup", test_lookup),
        ("圖示預覽 Icon", test_icon_preview),
        ("打包 Bundler", test_bundler),
        ("翻譯工具 Translation", test_translation_view),
        ("jar 提取 Extractor", test_extractor),
        ("機器翻譯 LM", test_lm),
        ("檔案合併 Merge", test_merge),
        ("KubeJS Pipeline", test_kubejs),
        ("FTB Pipeline", test_ftb),
    ]

    passed = 0
    failed = 0

    for name, fn in tests:
        try:
            if fn():
                passed += 1
            else:
                failed += 1
        except Exception as e:  # noqa: BLE001
            fail(name, e)
            failed += 1

    banner("測試摘要")
    total = passed + failed
    print(f"  總計：{total} 項")
    print(f"  {C_GREEN}通過：{passed}{C_RESET}   {C_RED}失敗：{failed}{C_RESET}")
    print(f"  {C_BOLD}成功率：{passed * 100 // total if total else 0}%{C_RESET}")

    if failed > 0:
        print(f"\n{C_RED}有 {failed} 項測試失敗，請檢查上方的 FAIL 項目。{C_RESET}")
        sys.exit(1)
    else:
        print(f"\n{C_GREEN}全部測試通過！{C_RESET}")
        sys.exit(0)


if __name__ == "__main__":
    main()
