"""Execute the 70k benchmark body without pytest's long-lived UI plugins."""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.test_smart_review_70k_performance import run_70k_gate


def main() -> int:
    """Run one isolated benchmark case and let the parent enforce timeout."""
    try:
        directory = os.environ.get("SMART_REVIEW_70K_ROOT")
        if not directory:
            raise RuntimeError("SMART_REVIEW_70K_ROOT is required")
        run_70k_gate(Path(directory))
        exit_code = 0
    except BaseException:  # noqa: BLE001 - convert benchmark failure to exit code
        traceback.print_exc()
        exit_code = 1
    print(f"70k case complete exit={exit_code}", flush=True)
    return exit_code


if __name__ == "__main__":
    exit_code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(exit_code)
