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
    """`run_bundling_service`
    
    用途：
    - 執行此函式的主要流程（細節以程式碼為準）。
    - 主要包裝/呼叫：`bundle_outputs_generator`
    
    參數：
    - 依函式簽名。
    
    回傳：
    - Generator：逐步 yield update dict（log/progress/error 等）。
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
