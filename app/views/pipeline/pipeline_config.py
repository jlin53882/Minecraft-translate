"""一鍵流水線的路徑設定（由輸入／輸出根目錄推算各步驟的資料夾）。"""

import os

from translation_tool.utils.config_manager import load_config


def _has_files(path: str) -> bool:
    """資料夾存在且至少含有一個檔案。"""
    if not os.path.isdir(path):
        return False
    return any(files for _, _, files in os.walk(path))


def normalize_extract_mode(mode: str | None) -> str:
    """抽取模式的邊界正規化：UI 的「全部執行」(``both``) 在引擎／actions 一律是 ``dual``。"""
    return "dual" if mode == "both" else (mode or "lang")


class PipelineConfig:
    """一鍵製作路徑設定檔"""

    def __init__(self, input_dir: str, output_dir: str):
        cfg = load_config()
        self.input_dir = input_dir
        self.output_dir = output_dir

        lang_merger = cfg.get("lang_merger", {})
        bundler = cfg.get("output_bundler", {})

        self.jar_mod_extract = "jar_mod_extract"
        self.lang_output_subfolder = "_提取lang_輸出"
        self.book_output_subfolder = "_提取book_輸出"

        self.locale_sort = "locale_sort"
        self.sort_output_subfolder = "_整理輸出"
        self.pending_folder = lang_merger.get("pending_folder_name", "待翻譯")
        self.organized_folder = lang_merger.get(
            "pending_organized_folder_name", "待翻譯整理需翻譯"
        )

        self.lm_translate = "lm_translate"
        self.translate_output_subfolder = lang_merger.get(
            "lm_translate_folder_name", "_翻譯輸出"
        )

        self.output_zip_name = bundler.get("output_zip_name", "可使用翻譯.zip")

    @property
    def extract_lang_output_dir(self):
        return os.path.join(
            self.output_dir, self.jar_mod_extract, self.lang_output_subfolder
        )

    @property
    def extract_book_output_dir(self):
        return os.path.join(
            self.output_dir, self.jar_mod_extract, self.book_output_subfolder
        )

    @property
    def merge_input_dir(self):
        return os.path.join(self.output_dir, self.jar_mod_extract)

    @property
    def merge_output_dir(self):
        return os.path.join(
            self.output_dir, self.locale_sort, self.sort_output_subfolder
        )

    @property
    def translate_input_dir(self):
        """語言檔待翻譯清單（語系合併輸出於 lang_output/ 底下）。"""
        return os.path.join(self.merge_output_dir, "lang_output", self.organized_folder)

    @property
    def patchouli_pending_dir(self):
        """Patchouli 書本的待翻譯內容。"""
        return os.path.join(
            self.merge_output_dir, "patchouli_output", self.pending_folder
        )

    @property
    def translate_input_dirs(self):
        return [self.translate_input_dir, self.patchouli_pending_dir]

    @property
    def translate_output_dir(self):
        return os.path.join(
            self.output_dir, self.lm_translate, self.translate_output_subfolder
        )

    @property
    def bundle_input_dir(self):
        return os.path.join(
            self.output_dir, self.lm_translate, self.translate_output_subfolder
        )

    @property
    def bundle_staging_dir(self):
        return os.path.join(self.output_dir, "_打包暫存")

    @property
    def bundle_sources(self):
        """打包來源（優先序由低到高）：合併後的既有譯文 → LLM 新譯文。"""
        return [
            os.path.join(self.merge_output_dir, "lang_output"),
            os.path.join(self.merge_output_dir, "patchouli_output"),
            self.translate_output_dir,
        ]

    @property
    def bundle_output_zip(self):
        return os.path.join(self.output_dir, self.output_zip_name)
