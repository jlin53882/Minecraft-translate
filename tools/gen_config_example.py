"""由設定 schema 產生 config.example.json（#134）。

``config.example.json`` 是打包版第一次啟動時複製成 ``config.json`` 的範本，也是文件上的
「完整預設值」。它的內容完全來自 ``translation_tool/utils/config_schema.py``
（``DEFAULT_CONFIG``），不要手動編輯；新增或修改設定後執行：

    python tools/gen_config_example.py          # 覆寫 config.example.json
    python tools/gen_config_example.py --check  # 只檢查是否一致（不一致回傳 1）

``tests/test_config_example_generated.py`` 在 CI 檢查檔案與 schema 一致。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from translation_tool.utils.config_schema import build_default_config  # noqa: E402

OUT = REPO_ROOT / "config.example.json"


def render() -> str:
    return json.dumps(build_default_config(), ensure_ascii=False, indent=4) + "\n"


def main(argv: list[str]) -> int:
    text = render()
    if "--check" in argv:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current != text:
            sys.stderr.write(
                "config.example.json 與設定 schema 不一致；請執行 python tools/gen_config_example.py\n"
            )
            return 1
        return 0
    OUT.write_text(text, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
