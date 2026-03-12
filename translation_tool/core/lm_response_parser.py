from __future__ import annotations

import json
import re
from typing import Iterable


def safe_json_loads(text: str):
    text = text.strip()

    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*", "", text)
        text = re.sub(r"```$", "", text)
        text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    matches = re.findall(r"\{[\s\S]*\}", text)
    for m in matches:
        try:
            return json.loads(m)
        except json.JSONDecodeError:
            continue

    raise RuntimeError("JSON 解析失敗：無法解析模型回傳內容")


def chunked(lst, size):
    for i in range(0, len(lst), size):
        yield lst[i:i + size]
