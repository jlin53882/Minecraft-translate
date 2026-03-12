"""Bundle pipeline service wrappers.

PR18：將 bundle 類 service 從 app.services.py 抽離到 pipelines 子模組，
由 app.services 持續做 façade / re-export，維持 UI import 相容。
"""

from __future__ import annotations

import logging
import traceback

from app.services_impl.logging_service import GLOBAL_LOG_LIMITER
from translation_tool.core.output_bundler import bundle_outputs_generator

logger = logging.getLogger(__name__)


def run_bundling_service(input_root_dir: str, output_zip_path: str):
    """執行此 generator 並逐步回報進度（yield update dict）。
    
    - 主要包裝：`bundle_outputs_generator`
    """
    try:
        for update_dict in bundle_outputs_generator(input_root_dir, output_zip_path):
            filtered = GLOBAL_LOG_LIMITER.filter(update_dict)
            if filtered is not None:
                yield filtered
    except Exception as e:
        full_traceback = traceback.format_exc()
        logger.error(f"[致命錯誤] 打包服務失敗：{e}\n{full_traceback}")
        yield {
            "log": f"[致命錯誤] 打包服務失敗：{e}\n{full_traceback}",
            "error": True,
            "progress": 0,
        }
