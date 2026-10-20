"""translation_tool/utils/text_processor.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

import os
import re
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import orjson
from opencc import OpenCC

from .config_manager import resolve_project_path
from .log_unit import log_error, log_info, log_warning
from .replace_rules import (
    ImmutableReplaceRulesSnapshot,
    ReplaceRules,
    apply_replace_rules,
    find_replacement_conflicts,
)


def _resolve_rules_path(path: str):
    """將相對規則路徑解析為專案內的完整絕對路徑。"""
    return resolve_project_path(path)


_thread_local = threading.local()
_WARNED_CONFLICT_SIGNATURES: set[tuple] = set()
_CJK_PATTERN = re.compile(r"([\u4e00-\u9fff]+)")


def get_converter():
    """獲取當前執行緒專用的 OpenCC 實例"""
    if not hasattr(_thread_local, "converter"):
        _thread_local.converter = OpenCC("s2twp")
    return _thread_local.converter


@dataclass(frozen=True, slots=True)
class _RulesCacheEntry:
    """單一 resolved path 的快取內容。"""

    file_revision: tuple[int, int] | None
    snapshot: ImmutableReplaceRulesSnapshot
    fixed_count: int
    regex_count: int


def _file_revision(path: Path) -> tuple[int, int] | None:
    """以 mtime_ns 與檔案大小辨識規則檔版本。"""
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_size


def _format_file_revision(revision: tuple[int, int] | None) -> str:
    if revision is None:
        return "missing"
    return f"mtime_ns={revision[0]},size={revision[1]}"


def _load_rules_uncached(
    resolved_path: Path,
) -> tuple[ReplaceRules, int, int]:
    """讀取、驗證、排序並檢查一份規則檔；呼叫端負責快取與鎖。"""
    if not resolved_path.exists():
        log_warning("找不到替換規則檔案: %s，將略過替換處理。", resolved_path)
        return ReplaceRules(), 0, 0

    try:
        with resolved_path.open("rb") as f:
            rules = orjson.loads(f.read())
    except Exception as e:  # noqa: BLE001
        log_error("讀取替換規則檔案 %s 失敗: %s", resolved_path, e)
        return ReplaceRules(), 0, 0

    if not isinstance(rules, list):
        log_error("替換規則檔案格式錯誤（需為 list）: %s", resolved_path)
        return ReplaceRules(), 0, 0

    fixed_rules: list[dict[str, str]] = []
    regex_rules: list[dict[str, str]] = []

    for rule in rules:
        if not isinstance(rule, dict):
            continue
        if "from" not in rule or "to" not in rule:
            continue

        src = rule["from"]
        if not isinstance(src, str) or not isinstance(rule["to"], str):
            continue
        looks_like_regex = any(ch in src for ch in ".?*[]()\\")
        if looks_like_regex:
            regex_rules.append(rule)
        else:
            fixed_rules.append(rule)

    fixed_rules.sort(key=lambda r: len(r["from"]), reverse=True)
    # ReplaceRules：規則被修改時會遞增 revision，讓 apply_replace_rules 的編譯快取失效
    sorted_rules = ReplaceRules(fixed_rules + regex_rules)

    conflicts = find_replacement_conflicts(sorted_rules)
    # 同一份規則每次載入都會重算；相同內容的衝突警告整個程式只記一次，避免洗版。
    signature = tuple(conflicts)
    if conflicts and signature not in _WARNED_CONFLICT_SIGNATURES:
        _WARNED_CONFLICT_SIGNATURES.add(signature)
        examples = "、".join(
            f"{source!r}->{destination!r}"
            for _source_index, source, _destination_index, destination in conflicts[:3]
        )
        log_warning(
            "替換規則有 %d 個輸出包含來源詞的衝突；固定文字預設只套用一次（例：%s）",
            len(conflicts),
            examples,
        )
    return sorted_rules, len(fixed_rules), len(regex_rules)


class ReplaceRulesStore:
    """執行緒安全的替換規則快取與異動通知中心。"""

    def __init__(
        self,
        path_resolver: Callable[[str], Path] | None = None,
        loader: Callable[[Path], tuple[ReplaceRules, int, int]] | None = None,
    ) -> None:
        # 以 lambda 動態查找 _resolve_rules_path，保留測試與執行期路徑覆寫能力。
        self._path_resolver = path_resolver or (lambda path: _resolve_rules_path(path))
        self._loader = loader or _load_rules_uncached
        self._lock = threading.RLock()
        self._cache: dict[str, _RulesCacheEntry] = {}
        self._listeners: set[Callable[[Path | None], None]] = set()
        self._generation = 0

    def _resolved_path(self, path: str | os.PathLike[str]) -> Path:
        return Path(self._path_resolver(os.fspath(path))).expanduser().resolve()

    @staticmethod
    def _cache_key(resolved_path: Path) -> str:
        """在 Windows 上合併大小寫不同但指向同一檔案的路徑。"""
        return os.path.normcase(os.fspath(resolved_path))

    def _load_locked(
        self,
        resolved_path: Path,
        file_revision: tuple[int, int] | None,
        *,
        source: str,
        reason: str,
    ) -> ImmutableReplaceRulesSnapshot:
        started = time.perf_counter()
        rules, fixed_count, regex_count = self._loader(resolved_path)
        self._generation += 1
        snapshot = ImmutableReplaceRulesSnapshot.from_rules(
            rules, revision=self._generation
        )
        key = self._cache_key(resolved_path)
        self._cache[key] = _RulesCacheEntry(
            file_revision=file_revision,
            snapshot=snapshot,
            fixed_count=fixed_count,
            regex_count=regex_count,
        )
        log_info(
            "已載入自動替換規則：source=%s，cache_hit=false，reason=%s，"
            "path=%s，revision=%s，一般文字 %d 條，正規表示式 %d 條，"
            "實際讀取耗時=%.3f s",
            source,
            reason,
            resolved_path,
            _format_file_revision(file_revision),
            fixed_count,
            regex_count,
            time.perf_counter() - started,
        )
        return snapshot

    def get_snapshot(
        self,
        path: str | os.PathLike[str] = "replace_rules.json",
        *,
        source: str = "replace_rules_store",
    ) -> ImmutableReplaceRulesSnapshot:
        """取得同一份 path 的共享唯讀快照；檔案版本變更時才重新載入。"""
        resolved_path = self._resolved_path(path)
        key = self._cache_key(resolved_path)
        with self._lock:
            # revision 必須與 cache 檢查使用同一把鎖，避免寫檔／invalidate
            # 與讀取交錯時，把舊 revision 寫回新內容的快取。
            file_revision = _file_revision(resolved_path)
            cached = self._cache.get(key)
            if cached is not None and cached.file_revision == file_revision:
                return cached.snapshot
            reason = "file_revision_changed" if cached is not None else "initial_load"
            return self._load_locked(
                resolved_path,
                file_revision,
                source=source,
                reason=reason,
            )

    def get_count(
        self,
        path: str | os.PathLike[str] = "replace_rules.json",
        *,
        source: str = "replace_rules_store",
    ) -> int:
        """取得規則數量，不建立可變複本，也不在快取命中時讀檔。"""
        return len(self.get_snapshot(path, source=source))

    def invalidate(
        self,
        path: str | os.PathLike[str] | None = None,
        *,
        notify: bool = True,
    ) -> None:
        """使指定 path（或全部 path）的快取失效，並通知訂閱者。"""
        resolved_path = self._resolved_path(path) if path is not None else None
        with self._lock:
            if resolved_path is None:
                self._cache.clear()
            else:
                self._cache.pop(self._cache_key(resolved_path), None)
            listeners = list(self._listeners)
        if notify:
            self._notify(listeners, resolved_path)

    def force_reload(
        self,
        path: str | os.PathLike[str] = "replace_rules.json",
        *,
        source: str = "replace_rules_store",
    ) -> ImmutableReplaceRulesSnapshot:
        """忽略既有快取，實際重新讀取一次並通知規則異動。"""
        resolved_path = self._resolved_path(path)
        key = str(resolved_path)
        with self._lock:
            file_revision = _file_revision(resolved_path)
            self._cache.pop(key, None)
            snapshot = self._load_locked(
                resolved_path,
                file_revision,
                source=source,
                reason="force_reload",
            )
            listeners = list(self._listeners)
        self._notify(listeners, resolved_path)
        return snapshot

    def subscribe(self, callback: Callable[[Path | None], None]) -> Callable[[], None]:
        """訂閱規則快取失效／強制重載事件，回傳取消訂閱函式。"""
        with self._lock:
            self._listeners.add(callback)

        def unsubscribe() -> None:
            with self._lock:
                self._listeners.discard(callback)

        return unsubscribe

    @staticmethod
    def _notify(
        listeners: list[Callable[[Path | None], None]], path: Path | None
    ) -> None:
        for callback in listeners:
            try:
                callback(path)
            except Exception:  # noqa: BLE001 - 訂閱者失敗不可阻斷規則寫入
                log_warning("替換規則異動通知失敗", exc_info=True)


replace_rules_store = ReplaceRulesStore()


# --- 檔案讀寫與文字處理工具函式 ---
def load_replace_rules(
    path: str = "replace_rules.json",
    *,
    force_reload: bool = False,
    source: str = "load_replace_rules",
) -> ReplaceRules:
    """以 Store 載入規則，並回傳不會污染共用快照的相容複本。"""
    snapshot = (
        replace_rules_store.force_reload(path, source=source)
        if force_reload
        else replace_rules_store.get_snapshot(path, source=source)
    )
    return ReplaceRules(dict(rule) for rule in snapshot)


def get_replace_rules_count(
    path: str = "replace_rules.json", *, source: str = "get_replace_rules_count"
) -> int:
    """取得規則數量的共用入口。"""
    return replace_rules_store.get_count(path, source=source)


def save_replace_rules(
    path: str, rules: list[dict[str, str]], *, raise_on_error: bool = False
):
    """將替換規則儲存到指定的 JSON 檔案（orjson 版）。

    ``raise_on_error`` keeps the historical log-and-continue behavior for
    best-effort callers while allowing durable user actions to observe failure.
    """
    resolved_path = _resolve_rules_path(path)
    try:
        resolved_path.parent.mkdir(parents=True, exist_ok=True)
        with resolved_path.open("wb") as f:
            f.write(
                orjson.dumps(
                    rules, option=orjson.OPT_INDENT_2 | orjson.OPT_APPEND_NEWLINE
                )
            )
    except Exception as e:
        log_error("儲存替換規則到 %s 失敗: %s", resolved_path, e)
        if raise_on_error:
            raise
    else:
        replace_rules_store.invalidate(resolved_path)


def load_custom_translations(folder_path: str, filename="table.tsv") -> dict[str, str]:
    """從指定資料夾載入自訂的翻譯表 (TSV 格式)。"""
    custom_map = {}
    file_path = resolve_project_path(folder_path) / filename
    if not file_path.exists():
        log_info(f"自訂翻譯檔 {file_path} 不存在，略過。")
        return custom_map
    try:
        import pandas as pd

        df = pd.read_csv(
            file_path, sep="\t", header=None, names=["source", "translation"]
        )
        for _, row in df.iterrows():
            if pd.notna(row["source"]) and pd.notna(row["translation"]):
                custom_map[str(row["source"])] = str(row["translation"])
        log_info(f"成功從 {file_path} 載入 {len(custom_map)} 條自訂翻譯。")
    except Exception as e:  # noqa: BLE001
        log_error(f"讀取自訂翻譯檔 {file_path} 失敗: {e!r}")
    return custom_map


def safe_convert_text(text: str) -> str:
    """安全的文字轉換，處理空值與例外。"""
    if not text:
        return text
    conv = get_converter()
    return _CJK_PATTERN.sub(lambda m: conv.convert(m.group(1)), text)


def convert_text(text: str, rules: list[dict[str, str]] | None = None) -> str:
    """
    統一的「純文字」處理入口：
    - 安全簡轉繁（CJK-only s2twp）
    - 套用 replace rules（如果有）
    用途：.snbt / .md / .js / 任何純文字
    """
    if not isinstance(text, str) or not text:
        return text

    out = safe_convert_text(text)
    if rules:
        out = apply_replace_rules(out, rules)
    return out


def convert_snbt_file_inplace(
    path: str, rules: list[dict[str, str]] | None = None
) -> bool:
    """
    就地轉換單一 .snbt（或任何純文字檔）內容。
    回傳：是否有變更。
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        dst = convert_text(src, rules)
        if dst != src:
            with open(path, "w", encoding="utf-8") as f:
                f.write(dst)
            return True
        return False
    except Exception as e:  # noqa: BLE001
        log_error("convert_snbt_file_inplace 失敗: %s (%s)", path, e)
        return False


def convert_snbt_tree_inplace(
    root_dir: str, rules: list[dict[str, str]] | None = None
) -> int:
    """
    遞迴掃描資料夾，把所有 .snbt 就地轉繁（CJK-only + rules）。
    回傳：有變更的檔案數。
    用途：inject copy zh_cn -> zh_tw 後，先整包轉繁再 patch
    """
    changed = 0
    for r, _, files in os.walk(root_dir):
        for fn in files:
            if fn.lower().endswith(".snbt"):
                fp = os.path.join(r, fn)
                if convert_snbt_file_inplace(fp, rules):
                    changed += 1
    return changed


def recursive_translate_dict(data: Any, rules: Sequence[dict[str, str]]) -> Any:
    """
    (僅用於簡轉繁) 遞迴地對一個字典或列表中的所有字串值進行 OpenCC 轉換和規則替換。
    """
    if isinstance(data, dict):
        return {k: recursive_translate_dict(v, rules) for k, v in data.items()}
    if isinstance(data, list):
        return [recursive_translate_dict(item, rules) for item in data]
    if isinstance(data, str):
        return apply_replace_rules(safe_convert_text(data), rules)
    return data


def recursive_translate(
    data: Any, rules: Sequence[dict[str, str]], custom_translations: dict[str, str]
) -> Any:
    """
    修改點：
    1. 移除 converter 參數 (不需要再從外部傳入)
    2. 遞迴呼叫時也移除 converter
    3. 字串翻譯改用 safe_convert_text
    """
    if isinstance(data, dict):
        new_dict = {}
        for key, value in data.items():
            # 優先檢查自訂翻譯
            if isinstance(value, str) and value in custom_translations:
                new_dict[key] = custom_translations[value]
            else:
                # ✅ 修改：遞迴時不再傳遞 converter
                new_dict[key] = recursive_translate(value, rules, custom_translations)
        return new_dict

    elif isinstance(data, list):
        # ✅ 修改：遞迴時不再傳遞 converter
        return [recursive_translate(item, rules, custom_translations) for item in data]

    elif isinstance(data, str):
        # ✅ 修改：使用 safe_convert_text 取代 converter.convert
        # 這會自動處理執行緒安全，並確保「内存」變「記憶體」
        translated_text = safe_convert_text(data)
        return apply_replace_rules(translated_text, rules)

    else:
        return data


def orjson_dump_file(obj, fp, *, indent2: bool = True, newline: bool = True):
    """
    用 orjson 寫入檔案物件 fp。
    - indent2=True: 等同 json.dump(..., indent=2)
    - orjson 預設就是 UTF-8 且不會把中文變成 \\uXXXX（等同 ensure_ascii=False）
    """
    option = 0
    if indent2:
        option |= orjson.OPT_INDENT_2
    if newline:
        option |= orjson.OPT_APPEND_NEWLINE

    data = orjson.dumps(obj, option=option)
    fp.write(data)


def orjson_pretty_str(obj) -> str:
    """
    orjson_pretty_str 的 Docstring

    :param obj: 說明
    :return: 說明
    :rtype: str
    """

    return orjson.dumps(
        obj, option=orjson.OPT_INDENT_2 | orjson.OPT_APPEND_NEWLINE
    ).decode("utf-8")
