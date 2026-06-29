"""tests package.

存在的目的：
- 確保 `from tests.conftest import mock_page` 會 import 我們專案的 conftest，
  而非 hermes-agent/tests/conftest.py。
- 避免 sys.path 中其他 tests 目錄造成的命名衝突。

參考：Minecraft-translate 專案結構，hermes-agent 的 tests 目錄
會注入 sys.path 並造成命名衝突。
"""

# 移除 hermes-agent/tests 從 sys.path，避免套件命名衝突
import sys
from pathlib import Path as _Path

_hermes_agent_tests = _Path.home() / "AppData" / "Local" / "hermes" / "hermes-agent" / "tests"
if _hermes_agent_tests.exists():
    _resolved = _hermes_agent_tests.resolve()
    sys.path = [p for p in sys.path if _Path(p).resolve() != _resolved]

# 把專案根目錄加到 sys.path 最前面
_ROOT = _Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
