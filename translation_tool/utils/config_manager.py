# /minecraft_translator_flet/translator_tool/utils/config_manager.py (最終修正版)

import os
import json
import logging
from datetime import datetime
import copy

# 提供一個最完整的預設設定，確保即使 config.json 不存在，程式也能正常運作
DEFAULT_CONFIG = {
  "logging": {
    "log_level": "INFO",
    "log_format": "%(asctime)s - %(levelname)s - [%(name)s] - %(message)s",
    "log_dir": "logs"
  },
  "translator": {
    "output_dir_name": "zh_tw_generated",
    "replace_rules_path": "replace_rules.json",
    "cache_directory": "快取資料",
    "enable_cache_saving": True,
    "parallel_execution_workers": max(1, os.cpu_count() // 2),
  },
  "species_cache": {
    "cache_directory": "學名資料庫",
    "cache_filename": "species_cache.tsv",
    "wikipedia_language": "zh",
    "wikipedia_rate_limit_delay": 0.5
  },
    "lm_translator": {
        "temperature": 0.2,
        "lm_translate_folder_name": "LM翻譯後",
        "iniital_batch_size_patchouli" : 100 ,  # ⭐ 新增（建議 80~150） patchouli 專用
        "iniital_batch_size_lang" : 300 ,  # 起始 batch（你 TPM 很夠） Lang 專用
        "initial_batch_size_ftb": 100,  # 起始 batch（FTB 專用）
        "initial_batch_size_kubejs": 200,  # 起始 batch（kubejs 專用）
        "initial_batch_size_md": 100,  # 起始 batch（Markdown 專用）
        "min_batch_size" : 50 ,  # 最小 batch
        "batch_shrink_factor" : 0.75 ,  # 發生錯誤時縮小比例

        "rate_limit": {
            "timeout": 600 #request time out set
        },

        "models": {
            "gemini-2.5-flash": {"enabled": True},
            "gemini-3-flash-preview": {"enabled": False}
        },
        "keys":[
            "token",
        ],  
        "patchouli_system_prompt":(
            "你是專業的 Minecraft Patchouli 手冊翻譯員，專精於《當個創世神》繁體中文（台灣）官方譯名或台灣用語的翻譯。\n"
            "規則：\n"
            "1. 只翻譯 items[].text 的內容為繁體中文。\n"
            "2. 絕對不要修改 file、path。\n"
            "3. 保留 §, %, {}, $(...) 等格式。\n"
            "4. 回傳格式必須是 JSON，結構與輸入相同。\n"
            "5. 如果遇到學名則翻譯成台灣常使用的用語（例如：Creeper → 苦力怕）。"
            "6. 如果遇到單位詞(例如 mb 或是 tick 這種都不要翻譯保留原文)"
        ),
        "lang_system_prompt":(
            "你正在翻譯 Minecraft 語言檔案（JSON格式）。\n"
            "規則：\n"
            "1. 必須回傳標準 JSON，格式為: {\"items\": [ {\"file\":..., \"path\":..., \"text\":...}, ... ]}\n"
            "2. 欄位順序與數量必須與輸入完全一致。\n"
            "3. 只翻譯 'text' 的內容為繁體中文（台灣用語）。\n"
            "4. 絕對不可修改 path 內容。\n"
            "5. 禁止輸出任何解釋或 markdown 說明，只輸出 JSON。"
            "6. 如果遇到學名則翻譯成台灣常使用的用語（例如：Creeper → 苦力怕）。"
            "7. 如果遇到單位詞(例如 mb 或是 tick 這種都不要翻譯保留原文)"
        ),
        "translator": {
        #跳過名稱 
        "skip_terms": [ 
            "api documentation",
            "api docs",
            "documentation",
            "discord",
            "github",
            "homepage",
            "mod page",
            "modpack",
            "official website",
            "patreon"
        ] ,
        #要翻譯 key 相對名稱
        "translatable_keywords": [ 
            "text",
            "name",
            "title",
            "description",
            "subtitle",
            "hover",
            "note",
            "warning",
            "quote",
            "paragraph",
            "body",
            "header",
            "footer",
            "heading",
            "effects"
        ],
        },
        #patchouli 讀取資料夾路徑
        "patchouli":{
        "dir_names": [ 
            "patchouli_books",
            "book",
            "manual",
            "guidebook"
        ],
    },

    },
  "output_bundler": {
    "output_zip_name": "可使用翻譯.zip",
    "source_folders": {
        "assets": "zh_tw_generated/assets",
        "root": "zh_tw_generated/pack_mcmeta"
    }
  },
  # ★ 新增一個配置區塊或 Key ★
    "lang_merger": { 
        "pending_folder_name": "待翻譯", # 專門用於 lang_merger 的設定
        "pending_organized_folder_name": "待翻譯整理需翻譯", # 專門用於 lang_merger 的設定
        "filtered_pending_min_count": 2 , # 專門用於 lang_merger 的設定
        "quarantine_folder_name": "skipped_json", # 專門用於 lang_merger  zip 檔案合併錯誤處理的設定
    },
}

def load_config(config_path='config.json'):
    """讀取設定檔，如果檔案不存在或格式錯誤，則回傳預設設定。"""
    if not os.path.exists(config_path):
        print(f"警告：找不到設定檔 {config_path}，將使用預設設定。")
        return DEFAULT_CONFIG

    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            user_config = json.load(f)

        # 深度合併
        config = {}
        #for key, default_value in DEFAULT_CONFIG.items():
        #    user_value = user_config.get(key)
        #    if isinstance(default_value, dict) and isinstance(user_value, dict):
        #        config[key] = deep_merge(default_value, user_value)
        #    else:
        #        config[key] = user_value if key in user_config else default_value
        
        for key, default_value in DEFAULT_CONFIG.items():
            user_value = user_config.get(key)

            # 🚨 models 不允許 deep merge（使用者資料）
            if key == "lm_translator" and isinstance(default_value, dict) and isinstance(user_value, dict):
                lm = deep_merge(default_value, user_value)

                # 覆蓋 models（不使用 default）
                if "models" in user_value:
                    lm["models"] = user_value["models"]

                config[key] = lm
                continue
            
            if isinstance(default_value, dict) and isinstance(user_value, dict):
                config[key] = deep_merge(default_value, user_value)
            else:
                config[key] = user_value if key in user_config else default_value


        return config

    except (json.JSONDecodeError, IOError) as e:
        print(f"錯誤：讀取設定檔 {config_path} 失敗: {e}，將使用預設設定。")
        #return DEFAULT_CONFIG
        return copy.deepcopy(DEFAULT_CONFIG)

def save_config(config, config_path='config.json'):
    """
    儲存設定並檢查是否成功寫入。
    回傳 True = 寫入成功
          False = 寫入失敗
    """
    try:
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=4)

        with open(config_path, 'r', encoding='utf-8') as f:
            written_data = json.load(f)

        # 能 dump 代表結構是乾淨的
        json.dumps(written_data, sort_keys=True)

        logging.info(f"設定已成功儲存並驗證至 {config_path}")
        return True

    except Exception as e:
        logging.error(f"錯誤：儲存或驗證設定檔失敗: {e}")
        return False

def setup_logging(config):
    """根據設定檔配置 logging"""
    # 🔥 關鍵修正：將 flet 模組的日誌級別提高 🔥
    flet_logger = logging.getLogger("flet")
    flet_logger.setLevel(logging.WARNING) # 或 logging.ERROR

    # 🔥🔥🔥 正確地從 config["logging"] 讀取，而不是 config["log_level"] 🔥🔥🔥
    logging_cfg = config.get("logging", {})

    log_level = getattr(
        logging,
        logging_cfg.get("log_level", "INFO").upper(),
        logging.INFO
    )
    log_format = logging_cfg.get(
        "log_format",
        "%(asctime)s - %(levelname)s - [%(name)s] - %(message)s"
    )
    log_dir = logging_cfg.get("log_dir", "logs")

    # 清理舊 handler
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)

    # 建立 log 資料夾
    today = datetime.now().strftime('%Y%m%d')
    log_folder = os.path.join(log_dir, today)
    os.makedirs(log_folder, exist_ok=True)
    log_file = os.path.join(log_folder, 'app.log')

    handlers = [
        logging.StreamHandler(),
        logging.FileHandler(log_file, encoding='utf-8')
    ]

    logging.basicConfig(level=log_level, format=log_format, handlers=handlers)
    logging.info("日誌系統已成功設定。")


def get_models_config(cfg: dict) -> dict[str, dict]:
    """
    安全取得 models 設定
    - 確保一定回傳 dict[str, dict]
    - 外部亂寫 list / str 都會被忽略
    """
    lm_cfg = cfg.get("lm_translator", {})
    models = lm_cfg.get("models", {})

    if not isinstance(models, dict):
        logging.warning("models 設定型別錯誤，已忽略（需為 dict）")
        return {}

    safe_models: dict[str, dict] = {}

    for model_name, model_cfg in models.items():
        if not isinstance(model_name, str):
            continue
        if not isinstance(model_cfg, dict):
            continue

        safe_models[model_name] = {
            "enabled": bool(model_cfg.get("enabled", False))
        }

    return safe_models

def deep_merge(default: dict, override: dict) -> dict:
    result = default.copy()
    for k, v in override.items():
        if (
            k in result
            and isinstance(result[k], dict)
            and isinstance(v, dict)
        ):
            result[k] = deep_merge(result[k], v)
        else:
            result[k] = v
    return result



# --- 初始化設定 ---
config = load_config()
setup_logging(config)