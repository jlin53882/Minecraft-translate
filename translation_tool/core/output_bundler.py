"""translation_tool/core/output_bundler.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

# /minecraft_translator_flet/translation_tool/core/output_bundler.py (新檔案)

import hashlib
import json
import os
import re
import time
import zipfile
from collections.abc import Generator, Iterator
from typing import Any

from translation_tool.core.pack_mcmeta import (
    DEFAULT_PACK_FORMAT,
    build_pack_mcmeta,
    repair_source_pack_mcmeta,
)
from translation_tool.utils.config_manager import (
    load_config,  # noqa: F401 - 測試以 monkeypatch 取代的 seam
)
from translation_tool.utils.file_replace import replace_with_retry
from translation_tool.utils.log_unit import log_debug, log_error, log_info, log_warning

# ZIP 壓縮等級（zlib 1~9）。量測（150 個 mod、約 124 MB 的語言檔與貼圖）：
# level 9 耗時 8.4 s、8.72 MB；level 6 耗時 3.7 s、8.70 MB——大小幾乎相同、快 2.3 倍。
# 解壓後的檔案內容與等級無關，Minecraft 載入不受影響（#165）。
ZIP_COMPRESS_LEVEL = 6

# 打包狀態 sidecar（#165 方向 2）：放在 ZIP 旁，記錄「上次成功打包」的輸入指紋與 ZIP 雜湊。
# 絕不會進入資源包；格式版本不符一律視為無效並重建。
BUNDLE_STATE_SUFFIX = ".bundle-state.json"
BUNDLE_STATE_VERSION = 2
_HASH_CHUNK = 1024 * 1024


def _hash_file(path: str) -> str:
    """以 blake2b 串流計算檔案內容雜湊。"""
    h = hashlib.blake2b(digest_size=20)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_HASH_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


DEFAULT_OUTPUT_ZIP_NAME = "可使用翻譯.zip"
_WINDOWS_ILLEGAL_NAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)


def normalize_output_zip_name(value: object) -> str:
    """設定檔的輸出檔名 → 可安全接在資料夾後的 ZIP 檔名。

    空白、不是字串、含路徑分隔字元或 Windows 不允許的字元／保留名稱時用預設；
    缺少 ``.zip`` 副檔名時補上。
    """
    name = value.strip() if isinstance(value, str) else ""
    if not name or _WINDOWS_ILLEGAL_NAME_CHARS.search(name):
        return DEFAULT_OUTPUT_ZIP_NAME
    if not name.lower().endswith(".zip"):
        name += ".zip"
    stem = name[: -len(".zip")]
    if stem.endswith(".") or stem.split(".")[0].upper() in _WINDOWS_RESERVED_NAMES:
        return DEFAULT_OUTPUT_ZIP_NAME
    return name


def _read_custom_pack_mcmeta(
    path: str | None, generated: dict[str, Any]
) -> tuple[str | None, list[str]]:
    """讀取來源資料夾的 pack.mcmeta，回傳 (要寫入 ZIP 的內容, 說明)。

    沒有來源檔：(None, [])。無法讀取、不是有效 JSON 或沒有 pack 物件：(None, [原因])，
    由呼叫端改用 ``generated``（UI 設定產生的內容）。其他情況回傳可用的內容；format 欄位
    寫成字串等問題會被修正，說明列出修正了什麼（見 ``repair_source_pack_mcmeta``）。
    """
    if not path:
        return None, []
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except (OSError, UnicodeDecodeError) as ex:
        return None, [f"讀取失敗：{ex}"]
    return repair_source_pack_mcmeta(content, generated)


def _output_path_problem(output_zip_path: str) -> str | None:
    """輸出路徑必須是 ZIP「檔案」路徑；是資料夾或空白時回傳原因，否則 None。"""
    if not (output_zip_path or "").strip():
        return "輸出 ZIP 路徑不可空白"
    if output_zip_path.endswith(("/", "\\")) or os.path.isdir(output_zip_path):
        return (
            f"輸出 ZIP 路徑是資料夾而不是檔案: {output_zip_path}"
            f"（請指定檔名，例如 {DEFAULT_OUTPUT_ZIP_NAME}）"
        )
    return None


def _output_artifacts(output_zip_path: str) -> frozenset[str]:
    """打包自己產生的檔案（ZIP、暫存 ZIP、狀態檔與其暫存）。

    輸出 ZIP 放在來源資料夾內時（預設就是），這些檔案不得被當成來源掃描或寫進 ZIP：
    暫存 ZIP 正在被寫入，掃到它會把自己再壓一次（卡住或壞掉），也會讓指紋每次都不同。
    """
    state = _state_path(output_zip_path)
    return frozenset(
        _norm(p)
        for p in (output_zip_path, output_zip_path + ".tmp", state, state + ".tmp")
    )


# 打包留下的暫存／狀態檔，不論屬於哪個輸出 ZIP 都不是來源（例如以前用別的檔名輸出、
# 失敗時殘留的 other.zip.tmp）。
_STRAY_SUFFIXES = (".zip.tmp", BUNDLE_STATE_SUFFIX, BUNDLE_STATE_SUFFIX + ".tmp")


def _is_artifact(path: str, exclude: frozenset[str] | None) -> bool:
    if os.path.basename(path).lower().endswith(_STRAY_SUFFIXES):
        return True
    return bool(exclude) and _norm(path) in exclude


def _walk_files(
    folder: str, exclude: frozenset[str] | None = None
) -> list[tuple[str, str]]:
    """遞迴列出資料夾內的 (相對路徑, 絕對路徑)，排序以確保指紋穩定。"""
    found: list[tuple[str, str]] = []
    for root, dirs, files in os.walk(folder):
        dirs.sort()
        for name in sorted(files):
            full = os.path.join(root, name)
            if _is_artifact(full, exclude):
                continue
            found.append((os.path.relpath(full, folder).replace("\\", "/"), full))
    return found


def _collect_sources(
    input_root_dir: str,
    extra_folders: list[str] | None,
    exclude: frozenset[str] | None = None,
) -> list[tuple[str, str, str]]:
    """列出所有可能進入 ZIP 的來源檔：(來源標籤, 相對路徑, 絕對路徑)。"""
    sources = [("in", rel, full) for rel, full in _walk_files(input_root_dir, exclude)]
    for index, extra in enumerate(extra_folders or []):
        label = f"x{index}"
        if os.path.isfile(extra):
            if not _is_artifact(extra, exclude):
                sources.append((label, os.path.basename(extra), extra))
        elif os.path.isdir(extra):
            sources.extend(
                (label, rel, full) for rel, full in _walk_files(extra, exclude)
            )
    return sources


def _compute_fingerprint(
    sources: list[tuple[str, str, str]],
    description: str,
    pack_format: int,
    max_pack_format: int | None,
    pack_image_path: str | None,
) -> str | None:
    """計算打包輸入指紋；涵蓋內容（非 mtime）、相對路徑、pack 參數與壓縮設定。

    任一來源檔讀取失敗時回傳 None（呼叫端必須重建，且不記錄狀態）。
    """
    h = hashlib.blake2b(digest_size=32)

    def feed(record: object) -> None:
        h.update(json.dumps(record, ensure_ascii=False).encode("utf-8"))
        h.update(b"\n")

    feed(
        [
            "bundle-fingerprint",
            BUNDLE_STATE_VERSION,
            zipfile.ZIP_DEFLATED,
            ZIP_COMPRESS_LEVEL,
            description,
            pack_format,
            max_pack_format,
        ]
    )
    try:
        if pack_image_path and os.path.exists(pack_image_path):
            feed(
                [
                    "pack_image",
                    os.path.splitext(pack_image_path)[1].lower(),
                    _hash_file(pack_image_path),
                ]
            )
        else:
            feed(["pack_image", None])
        for label, rel, full in sources:
            feed([label, rel, os.path.getsize(full), _hash_file(full)])
    except OSError:
        return None
    return h.hexdigest()


def _state_path(output_zip_path: str) -> str:
    return output_zip_path + BUNDLE_STATE_SUFFIX


def _load_reusable_state(output_zip_path: str, fingerprint: str) -> bool:
    """判斷輸出 ZIP 是否可沿用：狀態有效、指紋相同，且 ZIP 內容未被刪除／改動。"""
    try:
        with open(_state_path(output_zip_path), "r", encoding="utf-8") as f:
            state = json.load(f)
        if (
            not isinstance(state, dict)
            or state.get("version") != BUNDLE_STATE_VERSION
            or state.get("fingerprint") != fingerprint
            or not os.path.isfile(output_zip_path)
            or state.get("zip_size") != os.path.getsize(output_zip_path)
        ):
            return False
        # 輸出 ZIP 是使用者成品：每次沿用都以雜湊確認內容未被改動（讀取 + BLAKE2，
        # 遠比重新掃描／壓縮／寫出便宜），即使大小與 mtime 被刻意保持相同也能偵測。
        return state.get("zip_hash") == _hash_file(output_zip_path)
    except (OSError, ValueError):
        return False


def _remove_quietly(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError as ex:
        log_warning(f"無法移除 {path}: {ex!r}")


def _commit_state(output_zip_path: str, fingerprint: str) -> None:
    """ZIP 已完整落地後才呼叫：以暫存檔＋原子替換寫入狀態 sidecar。"""
    state = {
        "version": BUNDLE_STATE_VERSION,
        "fingerprint": fingerprint,
        "zip_size": os.path.getsize(output_zip_path),
        "zip_hash": _hash_file(output_zip_path),
    }
    final = _state_path(output_zip_path)
    tmp = final + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(tmp, final)


class _ProgressTracker:
    """以「已處理檔案數 / 總檔案數」回報壓縮進度（約每 5% 一則，避免洗版）。"""

    def __init__(self, total: int) -> None:
        self.total = max(total, 1)
        self.done = 0
        self._every = max(1, self.total // 20)

    @property
    def progress(self) -> float:
        """目前進度（0.15~0.85）；所有階段共用，確保單調不倒退。"""
        return 0.15 + 0.7 * min(self.done, self.total) / self.total

    def advance(self) -> None:
        """記錄一個已寫入的檔案（不產生更新；用於已自行 yield 的單檔寫入）。"""
        self.done += 1

    def tick(self) -> dict[str, Any] | None:
        self.advance()
        if self.done % self._every and self.done < self.total:
            return None
        done = min(self.done, self.total)
        return {
            "progress": self.progress,
            "log": f"正在建立資源包：{done} / {self.total} 個檔案（{done * 100 // self.total}%）",
        }

    def run(self, inner: Iterator[Any]) -> Generator[dict[str, Any], None, Any]:
        """逐檔消耗內層 generator 並轉成進度更新；回傳內層的回傳值。"""
        while True:
            try:
                next(inner)
            except StopIteration as stop:
                return stop.value
            update = self.tick()
            if update:
                yield update


def _iter_add_folder_to_zip(
    zip_file: zipfile.ZipFile,
    folder_path: str,
    base_path_in_zip: str,
    seen_files: dict | None = None,
    exclude: frozenset[str] | None = None,
) -> Generator[None, None, tuple[int, dict]]:
    """逐檔寫入 ZIP 的 generator 版本：每寫完一個檔案 yield 一次，回傳 (檔案數, seen_files)。"""
    added_count = 0
    if seen_files is None:
        seen_files = {}

    if not os.path.exists(folder_path):
        log_warning(f"打包時找不到來源資料夾: {folder_path}，將略過。")
        return 0, seen_files

    for root, _, files in os.walk(folder_path):
        for file in files:
            file_path = os.path.join(root, file)
            if _is_artifact(file_path, exclude):
                continue
            relative_path = os.path.relpath(file_path, folder_path)
            archive_name = os.path.join(base_path_in_zip, relative_path).replace(
                "\\", "/"
            )

            if archive_name in seen_files:
                base, ext = os.path.splitext(archive_name)
                counter = 1
                while f"{base}_{counter}{ext}" in seen_files:
                    counter += 1
                archive_name = f"{base}_{counter}{ext}"

            seen_files[archive_name] = 1
            zip_file.write(file_path, archive_name)
            added_count += 1
            yield

    return added_count, seen_files


def _add_folder_to_zip(
    zip_file: zipfile.ZipFile,
    folder_path: str,
    base_path_in_zip: str,
    seen_files: dict | None = None,
) -> tuple[int, dict]:
    """將資料夾內容寫入 ZIP，並處理檔案名稱衝突。

    Args:
        zip_file: 目標 ZIP 檔案物件。
        folder_path: 要打包的來源資料夾路徑。
        base_path_in_zip: 檔案在 ZIP 內的根路徑（空字串表示直接放在根目錄）。
        seen_files: 已存在於 ZIP 的檔案名稱對照表，用於偵測命名衝突。
            若發生衝突，會自動在檔名後缀 `_1`、`_2`... 進行區分。
            格式為 `{archive_name: 1}`。

    Returns:
        tuple[int, dict]: (新增檔案數量, 更新後的 seen_files)。

    Raises:
        不拋出例外，僅記錄警告。呼叫端負責處理例外的包裝。
    """
    inner = _iter_add_folder_to_zip(zip_file, folder_path, base_path_in_zip, seen_files)
    while True:
        try:
            next(inner)
        except StopIteration as stop:
            return stop.value


def _write_pack_mcmeta(
    zip_file: zipfile.ZipFile,
    description: str,
    pack_format: int,
    max_pack_format: int | None = None,
) -> None:
    """將 pack.mcmeta 寫入 ZIP 檔案根目錄（內容規則見 ``pack_mcmeta``）。"""
    zip_file.writestr(
        "pack.mcmeta",
        json.dumps(
            build_pack_mcmeta(description, pack_format, max_pack_format),
            ensure_ascii=False,
            indent=2,
        ),
    )


def bundle_outputs_generator(
    input_root_dir: str,
    output_zip_path: str,
    description: str = "",
    pack_format: int = DEFAULT_PACK_FORMAT,
    max_pack_format: int | None = None,
    pack_image_path: str | None = None,
    extra_folders: list[str] | None = None,
    force_rebuild: bool = False,
) -> Generator[dict[str, Any], None, None]:
    """Generator that bundles output folders into a ZIP archive.

    Args:
        input_root_dir: Root folder containing source subfolders
        output_zip_path: Output path for ZIP file
        description: pack.mcmeta description field
        pack_format: pack.mcmeta 的 pack_format（整數）；1.20.2 以上另寫 supported_formats
        max_pack_format: 最大使用版本（supported_formats.max_inclusive 或 max_format）；None 用預設
        pack_image_path: Optional path to pack.png to copy into ZIP root
        extra_folders: Optional list of extra folder paths to merge into ZIP root
        force_rebuild: True 時忽略上次打包狀態，一律重建 ZIP

    輸入（內容、路徑、pack 參數、壓縮設定）與上次成功打包相同、且輸出 ZIP 未被改動時，
    直接沿用既有 ZIP。ZIP 先寫到暫存檔、完整關閉後才原子替換，最後才記錄新狀態。
    """
    start_time = time.time()

    problem = _output_path_problem(output_zip_path)
    if problem:
        yield {"progress": 1.0, "log": f"錯誤：{problem}", "error": True}
        log_error(problem)
        return

    if not os.path.exists(input_root_dir):
        yield {
            "progress": 1.0,
            "log": f"錯誤：輸入目錄不存在: {input_root_dir}",
            "error": True,
        }
        return

    subfolders = [
        d
        for d in os.listdir(input_root_dir)
        if os.path.isdir(os.path.join(input_root_dir, d))
    ]
    log_info(f"輸入目錄: {input_root_dir}，找到子資料夾: {subfolders}")
    if not subfolders:
        yield {
            "progress": 1.0,
            "log": f"錯誤：輸入目錄中沒有子資料夾: {input_root_dir}",
            "error": True,
        }
        log_error(f"輸入目錄中沒有子資料夾: {input_root_dir}")
        return

    total_files_added = 0
    yield {"progress": 0.0, "log": f"開始建立 ZIP 檔案於: {output_zip_path}"}

    seen_files: dict = {}
    tmp_zip_path = output_zip_path + ".tmp"
    exclude = _output_artifacts(output_zip_path)
    committed = False

    try:
        sources = _collect_sources(input_root_dir, extra_folders, exclude)
        fingerprint = _compute_fingerprint(
            sources, description, pack_format, max_pack_format, pack_image_path
        )
        if (
            fingerprint is not None
            and not force_rebuild
            and _load_reusable_state(output_zip_path, fingerprint)
        ):
            log_info(f"打包輸入未變動，沿用既有 ZIP: {output_zip_path}")
            yield {
                "progress": 1.0,
                "log": f"--- 來源與設定皆未變動，沿用上次的 ZIP：{output_zip_path} ---",
            }
            return
        # 重建期間不得留下舊的有效狀態：任何中途失敗／取消，下次都必須重建。
        _remove_quietly(_state_path(output_zip_path))
        tracker = _ProgressTracker(len(sources))

        # Pre-check for pack.png and pack.mcmeta in folders
        skipped_pack_mcmeta = None
        skipped_pack_png = None
        pack_mcmeta_source = None
        pack_png_source = None

        # Check input_root_dir for pack.png/pack.mcmeta
        for entry in os.listdir(input_root_dir):
            full_path = os.path.join(input_root_dir, entry)
            if os.path.isfile(full_path):
                if entry.lower() == "pack.mcmeta":
                    pack_mcmeta_source = full_path
                elif entry.lower() == "pack.png":
                    pack_png_source = full_path

        # Check extra_folders for pack.png/pack.mcmeta
        if extra_folders:
            for extra_path in extra_folders:
                if os.path.isfile(extra_path):
                    entry = os.path.basename(extra_path)
                    if entry.lower() == "pack.mcmeta":
                        pack_mcmeta_source = extra_path
                    elif entry.lower() == "pack.png":
                        pack_png_source = extra_path

        # If pack.png/pack.mcmeta found in folders, skip UI settings and warn
        if pack_mcmeta_source:
            skipped_pack_mcmeta = pack_mcmeta_source
            yield {
                "progress": 0.01,
                "log": f"警告：pack.mcmeta 已存在於 '{pack_mcmeta_source}'，跳過 UI 設定",
            }
            log_warning(f"pack.mcmeta 已存在於 '{pack_mcmeta_source}'，跳過 UI 設定")

        if pack_png_source:
            skipped_pack_png = pack_png_source
            yield {
                "progress": 0.02,
                "log": f"警告：pack.png 已存在於 '{pack_png_source}'，跳過 UI 設定",
            }
            log_warning(f"pack.png 已存在於 '{pack_png_source}'，跳過 UI 設定")

        with zipfile.ZipFile(
            tmp_zip_path,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=ZIP_COMPRESS_LEVEL,
        ) as zf:
            # Write pack.mcmeta from folder if exists, otherwise from UI
            custom_content, custom_problems = _read_custom_pack_mcmeta(
                pack_mcmeta_source,
                build_pack_mcmeta(description, pack_format, max_pack_format),
            )
            if custom_content is not None:
                seen_files["pack.mcmeta"] = 1
                zf.writestr("pack.mcmeta", custom_content)
                total_files_added += 1
                tracker.advance()
                yield {"progress": 0.05, "log": "已寫入 pack.mcmeta（來自資料夾）"}
                log_info(f"寫入 pack.mcmeta（來自：{pack_mcmeta_source}）")
            else:
                if custom_problems:
                    yield {
                        "progress": 0.05,
                        "log": f"警告：資料夾內的 pack.mcmeta 無法使用，改用 UI 設定產生（{custom_problems[0]}）",
                    }
                seen_files["pack.mcmeta"] = 1  # 來源的壞檔不能再被當一般檔案加入
                _write_pack_mcmeta(zf, description, pack_format, max_pack_format)
                total_files_added += 1
                yield {"progress": 0.05, "log": "已寫入 pack.mcmeta"}
                log_info("已寫入 pack.mcmeta")
            for problem in custom_problems if custom_content is not None else ():
                yield {
                    "progress": 0.05,
                    "log": f"警告：資料夾內的 pack.mcmeta 已修正：{problem}",
                }

            # Write pack.png from folder if exists, otherwise from UI
            if pack_png_source:
                try:
                    ext = os.path.splitext(pack_png_source)[1].lower()
                    if ext in (".png", ".jpg", ".jpeg"):
                        seen_files["pack.png"] = 1
                        with open(pack_png_source, "rb") as src:
                            zf.writestr("pack.png", src.read())
                        total_files_added += 1
                        tracker.advance()
                        yield {"progress": 0.1, "log": "已寫入 pack.png（來自資料夾）"}
                        log_info(f"寫入 pack.png（來自：{pack_png_source}）")
                except Exception as ex:  # noqa: BLE001 - 錯誤已記錄或回報給呼叫端，不中斷整批流程
                    yield {"progress": 0.1, "log": f"讀取 pack.png 失敗: {ex}"}
            elif pack_image_path and os.path.exists(pack_image_path):
                try:
                    ext = os.path.splitext(pack_image_path)[1].lower()
                    if ext in (".png", ".jpg", ".jpeg"):
                        dest_name = "pack.png"
                        if dest_name in seen_files:
                            dest_name = "pack_1.png"
                        seen_files[dest_name] = 1
                        with open(pack_image_path, "rb") as src:
                            zf.writestr(dest_name, src.read())
                        total_files_added += 1
                        yield {"progress": 0.1, "log": f"已複製資源包圖片: {dest_name}"}
                except Exception as ex:  # noqa: BLE001 - 錯誤已記錄或回報給呼叫端，不中斷整批流程
                    yield {"progress": 0.1, "log": f"複製 pack.png 失敗: {ex}"}

            for folder_name in subfolders:
                full_source_path = os.path.join(input_root_dir, folder_name)
                yield {
                    "progress": tracker.progress,
                    "log": f"正在掃描來源: '{folder_name}'...",
                }
                log_info(f"掃描資料夾: {full_source_path}")

                base = "" if folder_name.lower() == "root" else folder_name
                count, seen_files = yield from tracker.run(
                    _iter_add_folder_to_zip(
                        zf, full_source_path, base, seen_files, exclude
                    )
                )

                if count > 0:
                    total_files_added += count
                    yield {
                        "progress": tracker.progress,
                        "log": f"成功從 '{folder_name}' 加入 {count} 個檔案。",
                    }
                    log_info(f"從 '{folder_name}' 加入 {count} 個檔案")
                else:
                    yield {
                        "progress": tracker.progress,
                        "log": f"警告：'{folder_name}' 中沒有可打包的檔案。",
                    }
                    log_warning(f"'{folder_name}' 中沒有可打包的檔案")

            for entry in os.listdir(input_root_dir):
                full_path = os.path.join(input_root_dir, entry)
                if os.path.isfile(full_path) and not _is_artifact(full_path, exclude):
                    archive_name = entry
                    if archive_name in seen_files:
                        base, ext = os.path.splitext(archive_name)
                        counter = 1
                        while f"{base}_{counter}{ext}" in seen_files:
                            counter += 1
                        archive_name = f"{base}_{counter}{ext}"
                    seen_files[archive_name] = 1
                    zf.write(full_path, archive_name)
                    total_files_added += 1
                    tracker.advance()
                    yield {
                        "progress": tracker.progress,
                        "log": f"額外檔案: +1 ({entry})",
                    }
                    log_debug(f"加入根目錄檔案: {entry}")

            if extra_folders:
                for extra_path in extra_folders:
                    yield {
                        "progress": tracker.progress,
                        "log": f"正在處理額外項目: '{extra_path}'...",
                    }
                    log_debug(f"處理額外項目: {extra_path}")

                    if not os.path.exists(extra_path):
                        yield {
                            "progress": tracker.progress,
                            "log": f"額外項目不存在: '{extra_path}'",
                        }
                        log_warning(f"額外項目不存在: {extra_path}")
                        continue

                    if os.path.isfile(extra_path):
                        if _is_artifact(extra_path, exclude):
                            continue
                        file_name = os.path.basename(extra_path)
                        archive_name = file_name
                        if archive_name in seen_files:
                            base, ext = os.path.splitext(archive_name)
                            counter = 1
                            while f"{base}_{counter}{ext}" in seen_files:
                                counter += 1
                            archive_name = f"{base}_{counter}{ext}"
                        seen_files[archive_name] = 1
                        zf.write(extra_path, archive_name)
                        total_files_added += 1
                        tracker.advance()
                        yield {
                            "progress": tracker.progress,
                            "log": f"額外檔案: +1 ({file_name})",
                        }
                        log_info(f"加入額外檔案: {file_name}")
                    elif os.path.isdir(extra_path):
                        parent_name = os.path.basename(extra_path.rstrip("/\\"))
                        for entry in os.listdir(extra_path):
                            src = os.path.join(extra_path, entry)
                            if os.path.isdir(src):
                                count, seen_files = yield from tracker.run(
                                    _iter_add_folder_to_zip(
                                        zf, src, "", seen_files, exclude
                                    )
                                )
                                total_files_added += count
                                yield {
                                    "progress": tracker.progress,
                                    "log": f"額外資料夾 '{parent_name}/{entry}': +{count} 個檔案",
                                }
                                log_debug(
                                    f"額外資料夾 '{parent_name}/{entry}': +{count} 個檔案"
                                )
                            elif os.path.isfile(src) and not _is_artifact(src, exclude):
                                archive_name = entry
                                if archive_name in seen_files:
                                    base, ext = os.path.splitext(archive_name)
                                    counter = 1
                                    while f"{base}_{counter}{ext}" in seen_files:
                                        counter += 1
                                    archive_name = f"{base}_{counter}{ext}"
                                seen_files[archive_name] = 1
                                zf.write(src, archive_name)
                                total_files_added += 1
                                tracker.advance()
                                yield {
                                    "progress": tracker.progress,
                                    "log": f"額外檔案: +1 ({entry})",
                                }
                                log_debug(f"額外資料夾內檔案: {entry}")

        with zipfile.ZipFile(tmp_zip_path) as check:
            check.namelist()  # 中央目錄必須可讀，才允許替換正式 ZIP
        replace_with_retry(tmp_zip_path, output_zip_path)
        committed = True
        state_warning = None
        if fingerprint is not None:
            # 狀態只是最佳化用的中繼資料：寫入失敗不得影響已成功的 ZIP，下次僅需重建。
            try:
                _commit_state(output_zip_path, fingerprint)
            except OSError as ex:
                _remove_quietly(_state_path(output_zip_path))
                _remove_quietly(_state_path(output_zip_path) + ".tmp")
                state_warning = f"無法保存打包狀態（下次會重新打包）: {ex}"
                log_warning(state_warning)

        duration = time.time() - start_time
        log_parts = [
            f"打包完成！總共 {total_files_added} 個檔案被加入 ZIP。耗時 {duration:.2f} 秒"
        ]
        if skipped_pack_mcmeta:
            log_parts.append(f"跳過 pack.mcmeta（已存在於：{skipped_pack_mcmeta}）")
        if skipped_pack_png:
            log_parts.append(f"跳過 pack.png（已存在於：{skipped_pack_png}）")
        if state_warning:
            log_parts.append(state_warning)
        yield {"progress": 1.0, "log": "--- " + "；".join(log_parts) + " ---"}

    except Exception as e:  # noqa: BLE001 - 錯誤已記錄或回報給呼叫端，不中斷整批流程
        log_error(f"打包時發生嚴重錯誤: {e!r}", exc_info=True)
        yield {"progress": 1.0, "log": f"打包時發生嚴重錯誤: {e!r}", "error": True}
        # 失敗時保留既有的有效 ZIP（尚未 os.replace）；僅讓狀態失效，下次必定重建。
        _remove_quietly(_state_path(output_zip_path))
    finally:
        # 取消（GeneratorExit）或例外：暫存檔不得殘留，狀態不得生效。
        if not committed:
            _remove_quietly(tmp_zip_path)
