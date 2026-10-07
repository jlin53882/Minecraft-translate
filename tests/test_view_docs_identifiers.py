"""View 架構文件中以反引號標示的程式碼識別字必須仍存在於「該 View 的程式碼範圍」（#120）。

只檢查「長得像識別字」的片段（含底線或駝峰、至少 5 個字元）；像 ``max_lines=2000`` 這種帶值的不檢查。
過期的方法／屬性名稱會讓文件誤導維護者，所以在 CI 擋下來。

**範圍（locality）**：每份文件只能引用「自己的 View 檔案／套件」或「共用層」（``SHARED_SCOPE``：UI kit、
任務、services、外殼、引擎、日誌元件）裡存在的識別字；其他 View 剛好有同名識別字不算數
（避免某 View 的方法已刪除、文件沒更新，卻因為別的 View 同名而 false-green）。
新增一份 ``*_VIEW_ARCHITECTURE.md`` 時必須在 ``DOC_SCOPES`` 加上對應範圍，否則測試失敗。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = sorted((ROOT / "docs").glob("*_VIEW_ARCHITECTURE.md")) + [
    ROOT / "docs" / "UNTRANSLATED_CHECKER_ARCHITECTURE.md"
]
# 文件刻意提到、但不是程式識別字的詞（例如設定鍵、舊名稱的說明）
NOT_CODE: set[str] = {
    # MERGE 文件明確說明「舊版文件提到的 skip_zh_cn_switch 已不存在」（歷史名稱）
    "skip_zh_cn_switch",
}

# 所有 View 都可能引用的共用層（相對於 repo 根目錄的 glob）
SHARED_SCOPE = (
    "main.py",
    "app/ui/**/*.py",
    "app/tasks/**/*.py",
    "app/services_impl/**/*.py",
    "app/shell/**/*.py",
    "app/views/_log/**/*.py",
    "app/views/qc_base.py",
    "app/config_store.py",
    "app/config_apply.py",
    "app/services.py",
    "translation_tool/**/*.py",
)

# 文件 → 該 View 自己的程式碼範圍
DOC_SCOPES: dict[str, tuple[str, ...]] = {
    "BUNDLER_VIEW_ARCHITECTURE.md": (
        "app/views/bundler_view.py",
        "app/views/bundler/**/*.py",
        "app/views/pipeline/pipeline_view.py",  # 文件說明與 pipeline_view 流程的差異
    ),
    "CACHE_VIEW_ARCHITECTURE.md": (
        "app/views/cache_view.py",
        "app/views/cache_manager/**/*.py",
    ),
    "CONFIG_VIEW_ARCHITECTURE.md": (
        "app/views/config_view.py",
        "app/views/config/**/*.py",
    ),
    "EXTRACTOR_VIEW_ARCHITECTURE.md": (
        "app/views/extractor_view.py",
        "app/views/extractor/**/*.py",
        # 文件說明 pipeline 流程如何使用提取 service／預覽
        "app/views/pipeline/pipeline_view.py",
        "app/views/pipeline/pipeline_extract_dialog.py",
    ),
    "ICON_VIEW_ARCHITECTURE.md": (
        "app/views/icon_preview_view.py",
        "app/views/icon_preview/**/*.py",
        "app/views/icon_preview_row.py",
        "app/icon_index.py",
        "app/icon_reader.py",
    ),
    "LM_VIEW_ARCHITECTURE.md": ("app/views/lm_view.py",),
    "LOOKUP_VIEW_ARCHITECTURE.md": ("app/views/lookup_view.py",),
    "MERGE_VIEW_ARCHITECTURE.md": (
        "app/views/merge_view.py",
        "app/views/merge/**/*.py",
    ),
    "PIPELINE_VIEW_ARCHITECTURE.md": ("app/views/pipeline/**/*.py",),
    "QC_VIEW_ARCHITECTURE.md": (
        "app/views/qc_view.py",
        "app/views/untranslated_checker.py",
    ),
    "RULES_VIEW_ARCHITECTURE.md": (
        "app/views/rules_view.py",
        "app/views/rules/**/*.py",
    ),
    "TRANSLATION_VIEW_ARCHITECTURE.md": (
        "app/views/translation_view.py",
        "app/views/translation/**/*.py",
    ),
    "UNTRANSLATED_CHECKER_ARCHITECTURE.md": (
        "app/views/untranslated_checker.py",
        "app/views/qc_view.py",  # 文件說明 QCView 的 start_task 分派
    ),
}


def _identifiers(globs: tuple[str, ...]) -> set[str]:
    words: set[str] = set()
    for pattern in globs:
        for path in ROOT.glob(pattern):
            if path.is_file():
                words.add(path.stem)  # 模組名稱（例如 pipeline_view）也算識別字
                words.update(
                    re.findall(
                        r"[A-Za-z_][A-Za-z0-9_]*", path.read_text(encoding="utf-8")
                    )
                )
    return words


def _candidates(text: str) -> set[str]:
    out: set[str] = set()
    for token in re.findall(r"`([^`\n]+)`", text):
        token = re.sub(r"\(.*\)$", "", token.strip()).replace("self.", "")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", token):
            continue
        word = token.split(".")[-1]
        if len(word) >= 5 and ("_" in word or re.search(r"[a-z][A-Z]", word)):
            out.add(token)
    return out


def test_every_view_doc_has_a_code_scope():
    missing = [doc.name for doc in DOCS if doc.name not in DOC_SCOPES]
    assert missing == [], (
        "新的 View 文件必須在 DOC_SCOPES 宣告對應的程式碼範圍：" + str(missing)
    )
    for name, globs in DOC_SCOPES.items():
        for pattern in globs:
            assert list(ROOT.glob(pattern)), f"{name} 的範圍 {pattern} 沒有對應檔案"


def test_view_docs_only_mention_identifiers_in_their_own_scope():
    shared = _identifiers(SHARED_SCOPE)
    problems: list[str] = []
    for doc in DOCS:
        own = _identifiers(DOC_SCOPES[doc.name])
        for token in sorted(_candidates(doc.read_text(encoding="utf-8"))):
            if token in NOT_CODE:
                continue
            word = token.split(".")[-1]
            if word not in own and word not in shared:
                problems.append(f"{doc.name}: `{token}`")
    assert problems == [], (
        "文件提到了「自己的 View 範圍或共用層」裡不存在的識別字"
        "（可能只在別的 View 有同名）：\n" + "\n".join(problems)
    )


def test_scope_is_stricter_than_the_global_identifier_set():
    """防止退化回全域比對：另一個 View 的私有識別字不能讓本 View 的文件通過。"""
    lm_scope = _identifiers(DOC_SCOPES["LM_VIEW_ARCHITECTURE.md"])
    shared = _identifiers(SHARED_SCOPE)
    # TranslationView 專屬的方法不在 LM 範圍，也不在共用層
    private = "_on_cancel"
    assert private in _identifiers(DOC_SCOPES["TRANSLATION_VIEW_ARCHITECTURE.md"])
    assert private not in lm_scope and private not in shared
