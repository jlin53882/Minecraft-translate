"""共用的 LM 格式契約、payload 建構與結構種類判斷。"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any

from translation_tool.core.guide_format import format_spec

# These are stable DB wire values.  Keep this low-level policy module free of
# ``translation_db`` imports: that package eagerly imports its repository and
# quality modules, which would create a cycle through the shared LM budget.
KIND_LANG = "lang"
KIND_GUIDE = "guide"
KIND_PATCHOULI = "patchouli"
KIND_FTB_QUESTS = "ftb_quests"
KIND_FTB_QUEST_LANG = "ftb_quest_lang"
STRUCTURED_KINDS = frozenset(
    {KIND_GUIDE, KIND_PATCHOULI, KIND_FTB_QUESTS, KIND_FTB_QUEST_LANG}
)

# These are the exact legacy lines emitted by the previous config schema.  The
# normalizer is intentionally line-scoped so a user's larger custom paragraph
# is never rewritten just because it contains similar wording.
_LEGACY_FORMAT_RULES = frozenset(
    {
        "保留每筆 value 原有的實際換行數量；可依譯文語意調整換行位置，但不可新增或刪除實際換行",
        "字面上的反斜線+n（\\n）必須原樣保留，不可轉成實際換行；實際換行也不可改成字面上的反斜線+n。",
    }
)
_LEGACY_RULE_PREFIX = re.compile(r"^(?:(?:[-*+]\s+)|(?:\d+[.)]\s+))?(.*)$")

FORMAT_POLICY_PROMPT = r"""以下格式契約優先於前文任何互相衝突的換行或格式規則。
【格式契約（程式驗證的硬性規則）】
- 空白行、段落分隔、<br>、文字形式的 \n、Markdown 標題／清單／引用必須保留。
- 同一段落內、沒有空白行或顯式硬換行的英文排版折行，可以依繁體中文語意自然重排。
- <link;分類;顯示文字> 與 <link;分類;顯示文字;start> 只有「顯示文字」要翻譯；link、分類、start 與欄位之間的分號必須原樣保留，顯示文字本身的內容與標點可以翻譯。
- 不可自行新增大量換行，也不可刪除或合併硬結構。
- 不可把實際換行轉成文字形式的 \n，也不可把文字形式的 \n 轉成實際換行。
- 只回傳結構化 JSON，不要輸出說明文字。
"""

STRUCTURED_FORMAT_POLICY_PROMPT = r"""【Guide/Patchouli 結構補充契約】
- 空白行、段落分隔、<br>、文字形式的 \n、Markdown 標題／清單／引用與 Patchouli 元件結構必須保留（文字開頭與結尾的空行不計）。
- <ItemLink ... /> 的標籤名稱、屬性名稱、id、冒號、括號與斜線必須原樣保留。
- <link;分類;顯示文字> 與 <link;分類;顯示文字;start> 的「顯示文字」要翻譯；其餘欄位（link、分類、start）與欄位之間的分號必須原樣保留，顯示文字本身的內容與標點可以翻譯。
- 除 <link> 的顯示文字外，只翻譯 Patchouli 標籤以外的人類語言文字。
"""

FTB_FORMAT_POLICY_PROMPT = r"""【FTB Quests 結構補充契約】
- 每個任務／語系 description slot 是獨立項目；不可合併、拆分或重新排序陣列元素。
- ``{@pagebreak}``、``{image:...}``、``{translation.key}``、資源 ID 與其他 FTB 結構標記必須原樣保留。
- ``{translation.key}`` 是語言鍵引用，不可當成人類語言翻譯；JSON Component 的 translate／with／樣式欄位也必須保留。
- 不確定的 Rich Text、Component 或動態內容應保留原文並標記待處理，不可猜測改寫。
"""

PATCHOULI_SEMANTIC_FORMAT_POLICY_PROMPT = r"""【Patchouli 語意換行契約】
- 先將原文中位於句子或詞語中間的排版折行視為同一段落內容，翻譯時必須合併，不可在對應的中文詞語或短語中間換行。
- 原文若在完整句子結束處已有換行，可以保留該換行；原文若沒有在句子結束處換行，不要自行新增換行。
- 換行只能放在完整句子、完整段落或其他明確硬格式邊界，不得依固定字數、英文原始欄寬或模型輸出長度切行。
- 每一行都必須是完整的繁體中文語意單位。
- 禁止出現「了解\n這個」、「護甲和\n武器」這類詞語或短語中間斷行。
- 保留空白行、段落分隔、Markdown 結構、Patchouli 元件、<br> 與其他硬格式。
- 不要為了湊足英文原文的行數新增換行。
"""

LANG_SEMANTIC_FORMAT_POLICY_PROMPT = r"""【lang 語意換行契約】
- 先將原文中位於句子或詞語中間的排版折行視為同一段落內容；在保留 lang value 實際換行數量的前提下調整換行位置，不可在對應的中文詞語或短語中間換行。
- 原文若在完整句子結束處已有換行，可以保留該換行；原文若沒有在句子結束處換行，不要自行新增換行。
- 換行只能放在完整句子、完整段落或其他明確硬格式邊界，不得依固定字數、英文原始欄寬或模型輸出長度切行。
- 每一行都必須是完整的繁體中文語意單位。
- 禁止出現「了解\n這個」、「護甲和\n武器」這類詞語或短語中間斷行。
- 保留空白行、段落分隔、文字形式的 \n、格式符號與其他硬格式。
- 不要為了湊足英文原文的行數新增換行。
"""


def normalize_legacy_prompt(prompt: str) -> str:
    """從設定提示詞移除精確匹配的舊版換行規則。"""
    if not isinstance(prompt, str) or not prompt:
        return prompt

    kept: list[str] = []
    changed = False
    for line in prompt.splitlines(keepends=True):
        content = line.rstrip("\r\n").strip()
        match = _LEGACY_RULE_PREFIX.fullmatch(content)
        if match and match.group(1).strip() in _LEGACY_FORMAT_RULES:
            changed = True
            continue
        kept.append(line)
    if not changed:
        return prompt
    return "".join(kept).strip()


def structured_kind(kind: object) -> bool:
    """判斷資料種類是否具有 Markdown 或 Patchouli 的硬性結構。"""
    return str(kind or "") in STRUCTURED_KINDS


def format_policy_for_items(
    items: Iterable[dict[str, Any]], *, profile: str | None = None
) -> str:
    """回傳共用及依資料種類套用的語意格式規則。"""
    values = list(items)
    kinds = {kind_for_item(item) for item in values}
    normalized_profile = str(profile or "").lower()
    has_guide_route = any(kind in {KIND_GUIDE, KIND_PATCHOULI} for kind in kinds) or (
        not any(kind is not None for kind in kinds)
        and normalized_profile in {"md", "patch"}
    )
    has_ftb_route = any(kind in {KIND_FTB_QUESTS, KIND_FTB_QUEST_LANG} for kind in kinds)
    has_lang_route = KIND_LANG in kinds or normalized_profile == KIND_LANG

    sections = [FORMAT_POLICY_PROMPT]
    if has_guide_route:
        sections.extend(
            [STRUCTURED_FORMAT_POLICY_PROMPT, PATCHOULI_SEMANTIC_FORMAT_POLICY_PROMPT]
        )
    if has_ftb_route:
        sections.append(FTB_FORMAT_POLICY_PROMPT)
    if has_lang_route:
        sections.append(LANG_SEMANTIC_FORMAT_POLICY_PROMPT)
    return "\n".join(sections)


def kind_for_item(item: dict[str, Any]) -> str | None:
    """解析項目所帶的資料庫種類，包含獨立 Markdown 項目。"""
    kind = item.get("_kind")
    if not kind:
        ident = item.get("db_ident")
        kind = ident[0] if isinstance(ident, (tuple, list)) and ident else None
    if kind:
        return str(kind)
    cache_type = str(item.get("cache_type") or "")
    if cache_type == "md":
        return KIND_GUIDE
    if cache_type == "patchouli":
        return KIND_PATCHOULI
    if cache_type == KIND_LANG:
        return KIND_LANG
    return None


def format_spec_for_item(item: dict[str, Any]) -> dict[str, object] | None:
    """只為結構化翻譯種類建立請求負載格式規格。"""
    kind = kind_for_item(item)
    if not structured_kind(kind):
        return None
    source = str(item.get("source_text") or item.get("text") or "")
    return format_spec(source)


def build_payload_item(
    item: dict[str, Any],
    item_id: str,
    *,
    retry_reasons: Iterable[str] = (),
    format_issues: Iterable[str] = (),
    terminology_issues: Iterable[str] = (),
) -> dict[str, object]:
    """建立單筆品質重試 payload，保留格式、重試原因與術語問題資訊。"""
    payload: dict[str, object] = {
        "id": str(item_id),
        "value": str(item.get("text") or ""),
    }
    spec = format_spec_for_item(item)
    if spec is not None:
        payload["format_spec"] = spec
    reasons = list(retry_reasons)
    issues = list(format_issues)
    if reasons:
        payload["retry_reasons"] = reasons
    if issues:
        payload["format_issues"] = issues
    term_issues = list(terminology_issues)
    if term_issues:
        payload["terminology_issues"] = term_issues
    return payload


def build_translation_payload(
    items: Iterable[dict[str, Any]],
    *,
    item_ids: Iterable[str] | None = None,
) -> dict[str, list[dict[str, object]]]:
    """使用與重試相同的項目建立器產生一般翻譯負載。"""
    values = list(items)
    ids = (
        list(item_ids) if item_ids is not None else [str(i) for i in range(len(values))]
    )
    if len(ids) != len(values):
        raise ValueError("payload item IDs and items must have the same length")
    return {
        "items": [
            build_payload_item(item, item_id)
            for item, item_id in zip(values, ids, strict=True)
        ]
    }


def estimate_format_spec_tokens(item: dict[str, Any], estimate_text_tokens) -> float:
    """估算格式規格額外加入的輸入 Token 數量。"""
    spec = format_spec_for_item(item)
    if spec is None:
        return 0.0
    return estimate_text_tokens(json.dumps({"format_spec": spec}, ensure_ascii=False))


__all__ = [
    "FORMAT_POLICY_PROMPT",
    "KIND_LANG",
    "LANG_SEMANTIC_FORMAT_POLICY_PROMPT",
    "PATCHOULI_SEMANTIC_FORMAT_POLICY_PROMPT",
    "STRUCTURED_FORMAT_POLICY_PROMPT",
    "STRUCTURED_KINDS",
    "build_payload_item",
    "build_translation_payload",
    "estimate_format_spec_tokens",
    "format_policy_for_items",
    "format_spec_for_item",
    "kind_for_item",
    "normalize_legacy_prompt",
    "structured_kind",
]
