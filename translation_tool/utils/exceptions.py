"""統一的異常處理系統

目的：
- 統一錯誤格式
- 自動化錯誤處理（重試、記錄）
- 更容易追蹤錯誤來源

使用方式：
    from translation_tool.utils.exceptions import handle_translation_errors, APIError

    @handle_translation_errors(log_func=log_error)
    def my_function():
        raise APIError("API 呼叫失敗")
"""

import time
import traceback
from functools import wraps
from pathlib import Path
from datetime import datetime


# =============================================================================
# 自訂異常類別
# =============================================================================


class TranslationError(Exception):
    """翻譯相關錯誤的基底類別"""

    def __init__(self, message: str, context: dict = None):
        """處理此函式的工作（細節以程式碼為準）。

        - 主要包裝：`__init__`

        回傳：None
        """
        self.message = message
        self.context = context or {}
        super().__init__(self.message)

    def __str__(self):
        """處理此函式的工作（細節以程式碼為準）。

        回傳：依函式內 return path。
        """
        if self.context:
            ctx = ", ".join(f"{k}={v}" for k, v in self.context.items())
            return f"{self.message} (context: {ctx})"
        return self.message


class APIError(TranslationError):
    """API 相關錯誤"""

    pass


class RateLimitError(APIError):
    """API 限流錯誤（429 Too Many Requests）"""

    def __init__(self, retry_after: int = 600, **context):
        """處理此函式的工作（細節以程式碼為準）。

        - 主要包裝：`__init__`

        回傳：None
        """
        super().__init__(
            f"API 限流，建議 {retry_after} 秒後重試",
            context={"retry_after": retry_after, **context},
        )
        self.retry_after = retry_after


class OverloadError(APIError):
    """API 過載錯誤（503 Service Unavailable - overload）"""

    def __init__(self, **context):
        """處理此函式的工作（細節以程式碼為準）。

        - 主要包裝：`__init__`

        回傳：None
        """
        super().__init__("API 伺服器過載", context)


class FileFormatError(TranslationError):
    """檔案格式錯誤（JSON、lang 等）"""

    pass


class CacheError(TranslationError):
    """快取相關錯誤"""

    pass


class ConfigError(TranslationError):
    """配置檔錯誤"""

    pass


# =============================================================================
# 錯誤處理裝飾器
# =============================================================================


def handle_translation_errors(log_func=None, auto_retry=True, max_retries=3):
    """統一的錯誤處理裝飾器

    Args:
        log_func: 日誌函式（接收字串參數）
        auto_retry: 是否自動重試（針對 RateLimitError）
        max_retries: 最大重試次數

    Returns:
        裝飾後的函式

    Example:
        @handle_translation_errors(log_func=log_error, auto_retry=True)
        def translate_batch(batch):
            # ...翻譯邏輯
            pass
    """

    def decorator(func):
        """處理此函式的工作（細節以程式碼為準）。

        回傳：依函式內 return path。
        """

        @wraps(func)
        def wrapper(*args, **kwargs):
            """處理此函式的工作（細節以程式碼為準）。

            回傳：依函式內 return path。
            """
            retry_count = 0

            while retry_count <= max_retries:
                try:
                    return func(*args, **kwargs)

                except RateLimitError as e:
                    if log_func:
                        log_func(f"⏱️ API 限流: {e.message}")

                    if auto_retry and retry_count < max_retries:
                        retry_count += 1
                        wait_time = e.retry_after
                        if log_func:
                            log_func(
                                f"🔄 等待 {wait_time} 秒後重試 ({retry_count}/{max_retries})..."
                            )
                        time.sleep(wait_time)
                        continue
                    else:
                        # 不自動重試或已達上限
                        _log_error_to_file(e, func.__name__)
                        raise

                except OverloadError as e:
                    if log_func:
                        log_func(f"⚠️ API 過載: {e.message}")

                    if auto_retry and retry_count < max_retries:
                        retry_count += 1
                        # 過載時使用指數退避
                        wait_time = min(60, 2**retry_count)
                        if log_func:
                            log_func(
                                f"🔄 等待 {wait_time} 秒後重試 ({retry_count}/{max_retries})..."
                            )
                        time.sleep(wait_time)
                        continue
                    else:
                        _log_error_to_file(e, func.__name__)
                        raise

                except APIError as e:
                    if log_func:
                        log_func(f"❌ API 錯誤: {e.message}")
                    _log_error_to_file(e, func.__name__)
                    raise

                except FileFormatError as e:
                    if log_func:
                        log_func(f"📄 檔案格式錯誤: {e.message}")
                    _log_error_to_file(e, func.__name__)
                    raise

                except CacheError as e:
                    if log_func:
                        log_func(f"💾 快取錯誤: {e.message}")
                    _log_error_to_file(e, func.__name__)
                    raise

                except ConfigError as e:
                    if log_func:
                        log_func(f"⚙️ 配置錯誤: {e.message}")
                    _log_error_to_file(e, func.__name__)
                    raise

                except TranslationError as e:
                    if log_func:
                        log_func(f"⚠️ 翻譯錯誤: {e.message}")
                    _log_error_to_file(e, func.__name__)
                    raise

                except Exception as e:
                    # 未預期的錯誤
                    if log_func:
                        log_func(f"💥 未預期錯誤: {str(e)}")
                    _log_error_to_file(e, func.__name__)
                    raise

        return wrapper

    return decorator


# =============================================================================
# 錯誤記錄
# =============================================================================


def _log_error_to_file(error: Exception, func_name: str):
    """將錯誤寫入日誌檔案

    Args:
        error: 異常物件
        func_name: 發生錯誤的函式名稱
    """
    try:
        # 確保日誌目錄存在
        log_dir = Path("logs")
        log_dir.mkdir(exist_ok=True)

        # 日誌檔案路徑（按日期分檔）
        log_file = log_dir / f"errors_{datetime.now().strftime('%Y-%m-%d')}.log"

        # 寫入錯誤資訊
        with open(log_file, "a", encoding="utf-8") as f:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"\n{'=' * 80}\n")
            f.write(f"[{timestamp}] 錯誤發生於: {func_name}\n")
            f.write(f"錯誤類型: {type(error).__name__}\n")
            f.write(f"錯誤訊息: {str(error)}\n")

            if isinstance(error, TranslationError) and error.context:
                f.write(f"錯誤上下文: {error.context}\n")

            f.write("\n堆疊追蹤:\n")
            f.write(traceback.format_exc())
            f.write(f"{'=' * 80}\n")

    except Exception as log_error:
        # 記錄失敗也不應該中斷主流程
        print(f"[WARN] 寫入錯誤日誌失敗: {log_error}")


# =============================================================================
# 便利函式
# =============================================================================


def raise_if_invalid_json(data: dict, required_keys: list, source: str = "unknown"):
    """檢查 JSON 資料是否包含必要欄位

    Args:
        data: JSON 資料（dict）
        required_keys: 必要欄位清單
        source: 資料來源（用於錯誤訊息）

    Raises:
        FileFormatError: 如果缺少必要欄位
    """
    missing = [key for key in required_keys if key not in data]
    if missing:
        raise FileFormatError(
            f"JSON 格式錯誤：缺少必要欄位 {missing}",
            context={"source": source, "missing_keys": missing},
        )


def raise_if_empty(value, name: str):
    """檢查值是否為空

    Args:
        value: 要檢查的值
        name: 值的名稱（用於錯誤訊息）

    Raises:
        TranslationError: 如果值為空
    """
    if not value:
        raise TranslationError(f"{name} 不可為空")
