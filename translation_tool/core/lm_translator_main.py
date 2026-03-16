"""translation_tool/core/lm_translator_main.py æ¨¡ç???

?¨é€”ï??ä??¬æ?æ¡ˆå?ç¾©ç??Ÿèƒ½?‡æ?ç¨‹ï?ä¾›å?æ¡ˆå…¶ä»–æ¨¡çµ„å‘¼?«ã€?
ç¶­è­·æ³¨æ?ï¼šæœ¬æª”æ??„å‡½å¼?docstring ?¨æ–¼ç¶­è­·èªªæ?ï¼Œä?ä»?¡¨è¡Œç‚ºè®Šæ›´??
"""

import logging
import time

import requests

from translation_tool.core.lm_api_client import call_gemini_requests
from translation_tool.core.lm_config_rules import (
    _current_key_index,  # ?®å?ä½¿ç”¨??API Key ç´¢å?
    get_current_api_key,  # ?–å?æ­?œ¨ä½¿ç”¨??key
    rotate_api_key,  # ?‡æ? key
)
from translation_tool.core.lm_response_parser import safe_json_loads
from translation_tool.utils.config_manager import load_config

logger = logging.getLogger(__name__)

# è¨­å??€

DRY_RUN = False  # True = ä¸é€?APIï¼Œåª?šå???/ ?è¦½ æ¸¬è©¦ä½¿ç”¨
EXPORT_CACHE_ONLY = True  # True = ?ˆè¼¸??cache ?½ä¸­?§å®¹

def translate_batch_smart(batch_items, total=None):
    """
    ?ºæ…§?‹å??¹ç¿»è­¯å‡½å¼?
    ?¯æ´?•æ?ç¸®æ? Batch Size?æ¨¡?‹å??›ã€ä»¥?Šè‡ª?•è??†è¼¸?ºæˆª?·å?é¡Œã€?
    """
    # èµ·å? batchï¼ˆä? TPM å¾ˆå?ï¼?Lang å°ˆç”¨
    INITIAL_BATCH_SIZE_LANG = (
        load_config().get("lm_translator", {}).get("initial_batch_size_lang", 300)
    )
    # â­??°å?ï¼ˆå»ºè­?80~150ï¼?Patchoui å°ˆç”¨
    INITIAL_BATCH_SIZE_PATCHOULI = (
        load_config().get("lm_translator", {}).get("initial_batch_size_patchouli", 100)
    )

    lm_cfg = load_config().get("lm_translator", {})  # ???ªè?ä¸€æ¬?

    # èµ·å? batchï¼ˆLangï¼?
    INITIAL_BATCH_SIZE_LANG = lm_cfg.get("initial_batch_size_lang", 200)

    # Patchouli / ?¶ä?ï¼ˆé?è¨­å?ï¼?
    INITIAL_BATCH_SIZE_PATCHOULI = lm_cfg.get("initial_batch_size_patchouli", 100)

    # ???°å?ï¼šFTB / KubeJS å°ˆç”¨ batch ä¸Šé?ï¼ˆä??¯ä»¥??config èª¿æ•´ï¼?
    INITIAL_BATCH_SIZE_FTB = lm_cfg.get("initial_batch_size_ftb", 100)
    INITIAL_BATCH_SIZE_KUBEJS = lm_cfg.get("initial_batch_size_kubejs", 200)
    INITIAL_BATCH_SIZE_MD = lm_cfg.get("initial_batch_size_md", 100)

    remaining_items = list(batch_items)  # å°šæœª?•ç???
    # â­?å·²æ??Ÿé€å‡º??API æ¬¡æ•¸
    completed_calls = 0
    all_results = []  # ç´¯ç??€?‰ç¿»è­¯ç???
    # â­â?â­??°å?ï¼?03 ????è?è¨ˆæ•¸?¨ï??¾åœ¨ while è¿´å?å¤–ï?
    overload_retry_count = 0

    # ?¤æ–·?™æ‰¹æ¬¡é??‹ï?å½±éŸ¿ System Prompt ??batch ä¸Šé?ï¼?
    def _norm_file(item):
        """

    
        """
        return str(item.get("file", "")).replace("\\", "/").lower()

    def detect_batch_profile(items):
        # ???ªå???cache_typeï¼ˆæ??¯é?ï¼?
        """

    
        """
        cache_types = [
            str(i.get("cache_type", "")).lower() for i in items if isinstance(i, dict)
        ]
        cache_types = [c for c in cache_types if c]

        if cache_types:
            # å¦‚æ??´æ‰¹?½æ˜¯?Œä?ç¨?cache_typeï¼Œå°±?´æ¥?¡ç”¨
            uniq = set(cache_types)
            if len(uniq) == 1:
                ct = next(iter(uniq))
                if ct in ("lang", "patchouli", "ftbquests", "kubejs", "md"):
                    return (
                        "lang" if ct == "lang" else ("ftb" if ct == "ftbquests" else ct)
                    )

            # æ··å??¹æ¬¡ï¼šå„ª?ˆç?ï¼ˆä??¯ä»¥èª¿ï?
            if "lang" in cache_types:
                return "lang"
            if "ftbquests" in cache_types:
                return "ftb"
            if "kubejs" in cache_types:
                return "kubejs"
            if "md" in cache_types:
                return "md"
            if "patchouli" in cache_types:
                return "patch"

        # â¬‡ï? fallbackï¼šæ²¿?¨ä??Ÿæœ¬?„æ?æ¡ˆè·¯å¾‘åˆ¤??
        files = [_norm_file(i) for i in items if isinstance(i, dict)]
        if files and all("/lang/" in f for f in files):
            return "lang"
        if any("/ftbquests/" in f for f in files):
            return "ftb"
        if any("/kubejs/" in f for f in files):
            return "kubejs"
        if any("/md/" in f for f in files):
            return "md"
        return "patch"

    """
    batch_profile = detect_batch_profile(batch_items)
    is_lang = batch_profile == "lang"

    if batch_profile == "lang":
        max_bs = INITIAL_BATCH_SIZE_LANG
    elif batch_profile == "ftb":
        max_bs = INITIAL_BATCH_SIZE_FTB
    elif batch_profile == "kubejs":
        max_bs = INITIAL_BATCH_SIZE_KUBEJS
    elif batch_profile == "md":
        max_bs = INITIAL_BATCH_SIZE_MD
    else:
        max_bs = INITIAL_BATCH_SIZE_PATCHOULI

    batch_size = min(len(batch_items), max_bs)

    # è¨˜é??Ÿå?ç¸½é?ï¼Œç”¨?¼é€²åº¦é¡¯ç¤º
    original_total = total  # å¤–éƒ¨ç¸½é?

    # æ¨¡å?å°å…¥è¨­å?
    models_cfg = load_config().get("lm_translator", {}).get("models", {})
    # ?®å?ä½¿ç”¨æ¨¡å?åºå?
    MODEL_POOL = [name for name, cfg in models_cfg.items() if cfg.get("enabled", False)]

    # æ¨¡å?æº«åº¦
    MODEL_TEMP = load_config().get("lm_translator", {}).get("temperature", 0.2)

    # ä½¿ç”¨?ç¤ºè©??‹å?
    PATCHOUI_SYSTEM_PROMPT = (
        load_config()
        .get("lm_translator", {})
        .get("patchouli_system_prompt", {"ä½ æ˜¯å°ˆæ¥­??Minecraft Patchouli ?‹å?ç¿»è­¯??})
    )

    # ä½¿ç”¨?ç¤ºè©?lang
    LANG_SYSTEM_PROMPT = (
        load_config()
        .get("lm_translator", {})
        .get("lang_system_prompt", {"ä½ æ­£?¨ç¿»è­?Minecraft èªè?æª”æ?ï¼ˆJSON?¼å?ï¼‰ã€?})
    )

    pinned_model_index = None  # None = æ­?¸¸æ¨¡å?ï¼Œé? None = ?–å??‡å?æ¨¡å?
    # ?²å…¥?•æ? Batch è¿´å?
    while remaining_items:
        hit_rpm = False  # â­??°å?ï¼šæ˜¯?¦å? RPM ?Œå??›æ¨¡??
        success_this_round = False  # â­??°å?
        hit_overload_retry = False  # â­??°å?ï¼šæ?è¨˜æ˜¯?¦å???503 ?€è¦å??°é?è©?
        current_batch = remaining_items[:batch_size]

        # å»ºç?ä¸€?‹è‡¨?‚å??§è¡¨ï¼Œç”¨ä¾†æ? ID å°æ??å?å§‹ç‰©ä»? ?¹æ? ID å°ç…§
        id_to_item_map = {str(i): item for i, item in enumerate(current_batch)}

        # [DEBUG] è¨˜é??¼é€æ?è¦?
        first_path = current_batch[0]["path"] if current_batch else "N/A"
        logger.debug(
            f"[?? DEBUG] æº–å??¼é€?Payload: ç¸½é?={len(current_batch)} | ID ç¯„å?: 0-{len(current_batch) - 1} | èµ·é?: {first_path}"
        )

        payload = {
            "items": [
                {
                    "id": str(i),  # ä½¿ç”¨ç°¡å–®?„å?ä¸?ID
                    "value": item["text"],
                }
                for i, item in enumerate(current_batch)
            ]
        }

        # ?æ­·?¯ç”¨æ¨¡å?æ±?
        # for model_name in MODEL_POOL:
        model_indices = (
            [pinned_model_index]
            if pinned_model_index is not None
            else range(len(MODEL_POOL))
        )

        for i in model_indices:
            model_name = MODEL_POOL[i]
            try:
                # print(f"[?’] ?—è©¦æ¨¡å? {model_name} | Batch={batch_size}/{original_total} | é¡å?={'Lang' if is_lang else 'Patch'}")
                # logger.info(f"[?’] ?—è©¦æ¨¡å? {model_name} | Batch={batch_size}/{original_total} | é¡å?={'Lang' if is_lang else 'Patch'}")

                profile_name = {
                    "lang": "Lang",
                    "ftb": "FTB",
                    "kubejs": "KubeJS",
                    "md": "MD",
                    "patch": "Patch",
                }.get(batch_profile, batch_profile)
                # logger.info(f"[?’] ?—è©¦æ¨¡å? {model_name} | Batch={batch_size}/{original_total} | é¡å?={profile_name}")
                logger.info(
                    f"[?’] ?—è©¦æ¨¡å? {model_name} | "
                    f"Batch={len(current_batch)}/{max_bs} | "
                    f"ç¿»è­¯ç¸½é?={original_total} | "
                    f"é¡å?={profile_name}"
                )

                # ?¸æ?å°æ???System Prompt
                # prompt = LANG_SYSTEM_PROMPT if is_lang else PATCHOUI_SYSTEM_PROMPT

                # ?¸æ?å°æ???System Prompt
                if batch_profile in ("lang", "kubejs"):
                    prompt = LANG_SYSTEM_PROMPT
                else:
                    # ftb / patchouli / ?¶ä?
                    prompt = PATCHOUI_SYSTEM_PROMPT

                logger.debug(
                    "Batch profile=%s -> System Prompt=%s",
                    batch_profile,
                    "LANG" if prompt is LANG_SYSTEM_PROMPT else "PATCHOUI",
                )

                raw_text = call_gemini_requests(
                    model_name=model_name,
                    system_prompt=prompt,
                    payload=payload,
                    api_key=get_current_api_key(),
                    temperature=MODEL_TEMP,
                ).strip()

                # ??ç©ºå…§å®¹æª¢?¥ï??¹æ? raw_textï¼?
                if not raw_text:
                    # print(f"[!] æ¨¡å? {model_name} ?å‚³ç©ºå…§å®¹ï??‡æ?æ¨¡å?...")
                    logger.info(f"[!] æ¨¡å? {model_name} ?å‚³ç©ºå…§å®¹ï??‡æ?æ¨¡å?...")
                    continue

                # --- ?¸å??¹é€²ï?æª¢æŸ¥è¼¸å‡º?¯å¦è¢«æˆª??---
                if not raw_text.endswith(("}", "]")):
                    # print(f"[!] ?µæ¸¬??JSON ?¯èƒ½è¢«æˆª?·ï?çµå°¾ä¸å??´ï?ï¼Œå?ç¸®å? Batch ?è©¦")
                    overload_retry_count = 0  # â­??ç½®?è?è¨ˆæ•¸??
                    logger.info(
                        "[!] ?µæ¸¬??JSON ?¯èƒ½è¢«æˆª?·ï?çµå°¾ä¸å??´ï?ï¼Œå?ç¸®å? Batch ?è©¦"
                    )
                    break

                # è§?? JSON
                parsed = safe_json_loads(raw_text)
                # print(raw_text) # ?¤éŒ¯?¨ï??°å‡º?Ÿå??å‚³?§å®¹

                # 1. å°‡ä»»ä½•æ¨¡?‹è¼¸?ºæ?æº–å???{id: text}
                # ===============================
                normalized_translations = {}

                if isinstance(parsed, dict):
                    if "items" in parsed:  # æ¨™æ??¼å?
                        for item in parsed["items"]:
                            if "id" in item and "value" in item:
                                normalized_translations[str(item["id"])] = item["value"]

                    elif {"file", "path", "text"} <= parsed.keys():  # ?®ä??©ä»¶
                        normalized_translations["0"] = parsed["text"]

                    else:  # ç°¡å??¼å? {"0":"...", "1":"..."}
                        for k, v in parsed.items():
                            normalized_translations[str(k)] = v

                elif isinstance(parsed, list):
                    for i, item in enumerate(parsed):
                        if isinstance(item, dict):
                            res_id = str(item.get("id", i))
                            normalized_translations[res_id] = item.get(
                                "value", item.get("text", "")
                            )

                # ===============================
                # 2. æ¼ç¿»æª¢æŸ¥
                # ===============================
                sent_count = len(id_to_item_map)
                received_count = len(normalized_translations)

                if received_count < sent_count:
                    missing_ids = list(
                        set(id_to_item_map.keys()) - set(normalized_translations.keys())
                    )
                    logger.warning(
                        f"[??æ¼ç¿»] ?å‡º {sent_count} æ¢ï?å¯¦æ”¶ {received_count} æ¢?
                    )
                    logger.warning(f"[??ç¼ºå¤± ID] {missing_ids[:5]}")
                    if missing_ids:
                        logger.debug(
                            f"[?? ç¼ºå¤±ç¯„ä?] {id_to_item_map[missing_ids[0]]['path']}"
                        )
                    break

                # ===============================
                # 3. ç²¾æ??„å?
                # ===============================
                merged_result = []
                lazy_count = 0

                for temp_id, original_item in id_to_item_map.items():
                    new_item = original_item.copy()
                    translated_text = normalized_translations.get(
                        temp_id, original_item["text"]
                    )

                    if translated_text == original_item["text"] and any(
                        c.isalpha() for c in translated_text
                    ):
                        lazy_count += 1
                        if lazy_count <= 3:
                            logger.debug(f"[? ï? ?‘ä¼¼?ªç¿»] {original_item['path']}")

                    new_item["text"] = translated_text
                    merged_result.append(new_item)

                if lazy_count > 0:
                    logger.info(
                        f"[?? ?¬æ‰¹æ¬¡ç?ä¼¼æœªç¿»ï?å»ºè­°Cache?§å®¹?¥è©¢ {lazy_count}/{sent_count}]"
                    )

                result = merged_result

                logger.info(f"[?“] ?å??–å?ç¿»è­¯ï¼š{model_name}")

                completed_calls += 1

                # â­?ç´¯ç?çµæ?
                all_results.extend(result)

                # â­??ˆæ?å·²è??†ç? batch ç§»é™¤
                remaining_items = remaining_items[batch_size:]

                # â­??è?ç®—å‰©é¤˜æ•¸?ï??™æ??æ?ï¼?
                remaining_count = len(remaining_items)

                # â­??•æ?èª¿æ•´ batch_size
                batch_size = min(batch_size, remaining_count)
                overload_retry_count = 0
                success_this_round = True  # â­â?â­??œéµ ï¼šæ?è¨˜æœ¬è¼ªæ???
                pinned_model_index = None  # â­?è§?? æ¨¡å?

                if remaining_count == 0:
                    # logger.info(f"?? å·²å???API ?¼å«ï¼š{completed_calls} æ¬?| ?€??items å·²å???)
                    logger.info(
                        f"?? ?¬æ‰¹æ¬¡å·²å®Œæ?ï¼šcalls={completed_calls} | ?¬æ‰¹ items={len(batch_items)}"
                    )
                    # ?è²»å±¤ä?è­?
                    logger.info("??ç­‰å? 12 ç§’ä»¥?¿å?è§¸ç™¼ RPM ?åˆ¶??)
                    time.sleep(12)
                # else: #?¬æ‰¹æ¬??²ä?ä¸æ??²ä??™è£¡?•ç?
                #    remaining_calls_estimated = math.ceil(
                #        remaining_count / max(batch_size, 1)
                #    )

                #    eta_sec = int(remaining_calls_estimated * avg_time_per_call)
                #    eta_min = eta_sec // 60
                #    eta_sec = eta_sec % 60

                #    logger.info(
                #        f"?? å·²å???API ?¼å«ï¼š{completed_calls} æ¬?| "
                #        f"?©é? itemsï¼š{remaining_count} | "
                #        f"?®å? batch={batch_size} | "
                #        f"ETA ??{eta_min}m {eta_sec}s"
                #    )
                #    # ?è²»å±¤ä?è­?
                #    logger.info("??ç­‰å? 12 ç§’ä»¥?¿å?è§¸ç™¼ RPM ?åˆ¶??)
                #    time.sleep(12)
                #

                # â­?å¦‚æ?å·²ç?æ²’æ??©é??…ç›®ï¼Œç›´?¥ç???while
                if not remaining_items:
                    logger.info("???€?‰é??®å·²ç¿»è­¯å®Œæ?")
                    return all_results, "AUTO"

                break  # è·³å‡º model loop

            except Exception as e:
                status = None
                if isinstance(e, requests.HTTPError) and e.response is not None:
                    status = e.response.status_code

                # â­â?â­??™ä?è¡Œæ˜¯?œéµ
                if status != 503:
                    overload_retry_count = 0  # ?ç½®?è?è¨ˆæ•¸??
                    pinned_model_index = None  # â­?è§?™¤?–å?

                """
                ?ªæ??Œé€????overloaded?æ?ç´¯ç?
                ä¸­é??ªè??ºç¾?¥ç??¯èª¤å°±è?æ­¸é›¶
                """

                # ========== 404 ==========
                if status == 404:
                    logger.info(f"[?”] æ¨¡å? {model_name} ä¸å??¨æ??¡æ?ä½¿ç”¨ï¼Œè·³?æ­¤æ¨¡å?")
                    break  # â­?è·³é›¢è¿´å?

                # ========== 403 ==========
                if status == 403:
                    logger.info(
                        f"??403 PERMISSION_DENIEDï¼šAPI Key ?¡æ???(index {_current_key_index})"
                    )
                    try:
                        rotate_api_key()
                        continue  # ?›æ¨¡??
                    except RuntimeError:
                        raise RuntimeError("???€??API Key ?‡ç„¡æ¬Šé?")

                # ========== 400 ==========
                if status == 400:
                    msg = str(e).lower()
                    if "failed_precondition" in msg:
                        raise RuntimeError(
                            "??FAILED_PRECONDITIONï¼šæ­¤?°å??ªå???Gemini API ?è²»?¹æ?ï¼Œè??Ÿç”¨ä»˜è²»"
                        )
                    logger.info(
                        "[? ï?] 400 INVALID_ARGUMENTï¼špayload ?¼å??¯èª¤?–é?å¤§ï?ç¸®å? batch"
                    )
                    break  # â­?äº¤çµ¦ batch shrink

                # ========== 429 RESOURCE_EXHAUSTED ==========
                if status == 429:
                    try:
                        # 1. ?—è©¦è§?? JSON ?¯èª¤?¼å?
                        error_json = e.response.json().get("error", {})
                        remote_msg = error_json.get("message", "").upper()

                        # 2. å¾ç´°ç¯€ä¸­æ??–å…·é«”ç? Quota ID (?™æ˜¯?¤æ–· RPD/RPM ?„é???
                        details = error_json.get("details", [])
                        quota_id = ""
                        retry_after = 0

                        for detail in details:
                            # ?¤æ–·?¯å¦?ºæ??¥é?é¡?(Log ä¸­å‡º?¾ç? GENERATEREQUESTSPERDAY...)
                            if (
                                detail.get("@type")
                                == "type.googleapis.com/google.rpc.QuotaFailure"
                            ):
                                quota_id = (
                                    detail.get("violations", [{}])[0]
                                    .get("quotaId", "")
                                    .upper()
                                )

                            # ?“å? Google å»ºè­°?„é?è©¦ç?å¾…ç???(Log ä¸­ç? RETRYDELAY)
                            if (
                                detail.get("@type")
                                == "type.googleapis.com/google.rpc.RetryInfo"
                            ):
                                retry_delay_str = detail.get(
                                    "retryDelay", "0s"
                                ).replace("s", "")
                                retry_after = int(float(retry_delay_str))

                        # 3. ?¹æ? Quota ID ?²è??†é??•ç?
                        if "PERDAY" in quota_id or "DAILY" in remote_msg:
                            # ?…æ? Aï¼šæ??¥é?åº?(RPD) æ»¿ä? (Log é¡¯ç¤ºï¼šGENERATEREQUESTSPERDAY...)
                            logger.warning(
                                f"[?š«] æ¯æ—¥?é?å·²æ»¿ (RPD)ï¼šKey Index {_current_key_index} ä»Šæ—¥å¤±æ?"
                            )
                            hit_rpm = True
                            # â­?æª¢æŸ¥??Key ?¯å¦?å?
                            if not rotate_api_key():
                                return None, "ALL_KEYS_EXHAUSTED"
                            continue

                        elif "PERMINUTE" in quota_id or "RPM" in remote_msg:
                            # ?…æ? Bï¼šæ??†é??»ç? (RPM) å¤ªå¿«
                            wait_time = retry_after if retry_after > 0 else 10
                            logger.info(
                                f"[?³] æ¯å??˜é »?‡é???(RPM)ï¼šç?å¾Œé?è©¦ï??è?ç­‰å? {wait_time} ç§?
                            )
                            time.sleep(wait_time)
                            hit_rpm = True
                            continue

                        else:
                            # ?…æ? Cï¼šå…¶ä»–æ??ªçŸ¥??429 (ä¾‹å? Free Tier ?„ç¸½è«‹æ??åˆ¶)
                            logger.warning(
                                f"[?“] ?µæ¸¬??429 ?åˆ¶ ({quota_id if quota_id else remote_msg})ï¼Œå?è©¦å???Key"
                            )
                            hit_rpm = True
                            # â­?æª¢æŸ¥??Key ?¯å¦?å?
                            if not rotate_api_key():
                                return None, "ALL_KEYS_EXHAUSTED"
                            continue

                    except Exception as parse_err:
                        # ?™æ´æ¯”å??è¼¯
                        err_msg = str(e).upper()
                        logger.error(
                            f"[? ï?] ?¡æ?è§?? 429 JSONï¼Œä½¿?¨å??´ã€‚éŒ¯èª? {parse_err}"
                        )

                        if "QUOTA" in err_msg or "EXCEEDED" in err_msg:
                            # â­??™è£¡ä¹‹å??ƒå´©æ½°ï??¾åœ¨?™æ¨£?¹å°±å®‰å…¨äº?
                            if not rotate_api_key():
                                return None, "ALL_KEYS_EXHAUSTED"
                            continue

                        # ?œå??•ç?
                        if not rotate_api_key():
                            return None, "ALL_KEYS_EXHAUSTED"
                        continue

                    except RuntimeError:
                        # â­??œéµï¼šç•¶ rotate_api_key ?‹å‡º RuntimeErrorï¼Œä»£è¡¨æ? Key äº?
                        # ä¸è??ªç”¨ breakï¼Œè??´æ¥ return ?€?‹çµ¦å¤–å±¤
                        error_final = (
                            "???€??API Key ?‡å·²?—ç›¡æ¯æ—¥?é? (RPD)ï¼Œè?ç­‰å??ç½®?‚é???
                        )
                        logger.error(error_final)
                        return None, "ALL_KEYS_EXHAUSTED"

                # ========== 504 ==========
                if status == 504:
                    logger.info(
                        "[?±ï?] 504 DEADLINE_EXCEEDEDï¼šè?æ±‚é?å¤§æ?æ¨¡å?è¨ˆç?å¤ªä?ï¼Œç¸®å°?batch"
                    )
                    break

                if status == 503:
                    try:
                        error_json = e.response.json()
                        remote_msg = error_json.get("error", {}).get("message", "")
                        remote_status = error_json.get("error", {}).get("status", "")
                    except Exception:
                        remote_msg = e.response.text or ""
                        remote_status = "NON_JSON"

                    logger.error("-" * 60)
                    logger.error("[?š¨ Gemini 503]")
                    logger.error(f"?€?? {remote_status}")
                    logger.error(f"è¨Šæ¯: {remote_msg}")

                    is_overloaded = (
                        "overloaded" in remote_msg.lower()
                        or "too many requests" in remote_msg.lower()
                    )

                    # ===== A. ??overloadï¼šå?ä¸€ batch ?Ÿåœ°ç­?=====
                    if is_overloaded:
                        overload_retry_count += 1  # â­?ç´¯ç??è?æ¬¡æ•¸

                        pinned_model_index = i  # â­?è¨˜ä??¯å“ª??model ?è?

                        # if overload_retry_count >= 3:
                        #    logger.error(f"[?Œ] ?ç? overloadï¼ˆ{overload_retry_count} æ¬¡ï????å‚³ PARTIAL ä¿è­·?²åº¦")
                        #    return all_results, "PARTIAL"
                        if overload_retry_count >= 3:
                            logger.warning(
                                f"[??] æ¨¡å???? overloadï¼ˆ{overload_retry_count} æ¬¡ï?ï¼Œå?è©¦å???API Key"
                            )

                            try:
                                # â­??—è©¦??Key
                                if rotate_api_key():
                                    overload_retry_count = 0  # â­??ç½®?è?è¨ˆæ•¸
                                    pinned_model_index = None  # â­?è§??æ¨¡å?ï¼Œå?è¨±é??°é¸
                                    logger.info(
                                        "[?…] API Key ?‡æ??å? ???Ÿåœ°?é€å?ä¸€ batch,ç­‰å?12ç§?
                                    )
                                    time.sleep(12)  # â­?çµ¦æ–° Key ä¸€é»ç·©è¡?
                                    hit_overload_retry = True  # â­??é€å?ä¸€ batch
                                    break  # ??è·³å‡º model loopï¼Œå? while
                                else:
                                    raise RuntimeError("NO_MORE_KEYS")

                            except RuntimeError:
                                logger.error(
                                    "[?Œ] ?€??API Key ??overload ?€?‹ä??‡ä??¯ç”¨ ???å‚³ PARTIAL ä¿è­·?²åº¦"
                                )
                                return all_results, "PARTIAL"

                        wait_sec = 12
                        logger.warning(
                            f"[? ï?] æ¨¡å??è?ï¼ˆç¬¬ {overload_retry_count} æ¬¡ï?ï¼?
                            f"?Ÿåœ°ç­‰å? {wait_sec}s å¾Œé??ã€å?ä¸€ batch / ?Œä?æ¨¡å???
                        )

                        time.sleep(wait_sec)
                        hit_overload_retry = True
                        break  # ??è·³å‡º model poolï¼Œå???while ?æ–°??

                    # ===== B. ??overload ??503ï¼šæ? key / model =====
                    else:
                        logger.warning(
                            "503 ??overloadï¼ˆå¯?½ç?é»æ??€?Ÿç•°å¸¸ï????—è©¦?‡æ? API key"
                        )
                        try:
                            rotate_api_key()
                            time.sleep(5)
                            continue  # ??key ç¹¼ç? model pool
                        except Exception as err:
                            logger.error(f"API key ?‡æ?å¤±æ?: {err}")
                            break

                # ======== 500 ==========
                if status == 500:
                    logger.info(
                        "[? ï?] 500 INTERNALï¼šGemini å¾Œç«¯?¯èª¤ï¼Œå?è©¦æ?æ¨¡å??–ç¸® batch"
                    )
                    break

                # ========== requests timeout ==========
                if isinstance(e, requests.Timeout):
                    logger.info("[?±ï?] Timeoutï¼šæ¨¡?‹å??ªå??è?ç®—ï?ç¸®å? batch")
                    break

                # ========== fallback ==========
                logger.info(f"[!] ?ªå?é¡éŒ¯èª? {e}")
                break

        # â­â?â­??œéµ?¤å?ï¼šå??œæ˜¯? ç‚º?è??Œè·³?ºï??´æ¥?²å…¥ä¸‹ä?æ¬?while è¿´å?ï¼ˆä??·è?ä¸‹æ–¹?„ç¸®å°é?è¼¯ï?
        if hit_overload_retry:
            continue

        # â­â?â­?å¦‚æ??™ä?è¼ªå·²?å?ï¼Œç›´?¥é€²ä?ä¸€ round
        if success_this_round:
            continue

        # --- å¦‚æ??€?‰æ¨¡?‹éƒ½å¤±æ?ï¼Œæ??…è§¸?¼ä? break (?ªæ–· / ?¸é?ä¸ç¬¦) ---
        # â­?hit_rpm ä»?¡¨?Œé?è¦é?è©¦å?ä¸€?¹ã€ï?ä¸å?ä»?¡¨ç­‰å?ï¼?
        if hit_rpm:
            logger.info("[??] ä½¿ç”¨??API Keyï¼Œé??°å?è©¦å?ä¸€?¹æ¬¡")
            hit_rpm = False  # â­??ç½®?—æ?ï¼Œé¿?ç„¡??continue
            continue
        ## ?¼ç??¯èª¤?‚ç¸®å°æ?ä¾?
        BATCH_SHRINK_FACTOR = (
            load_config().get("lm_translator", {}).get("batch_shrink_factor", 0.75)
        )
        # è¨ˆç??°ç? Batch Size
        new_size = int(batch_size * BATCH_SHRINK_FACTOR)
        ## ?€å°éŒ¯èª?batch
        MIN_BATCH_SIZE = (
            load_config().get("lm_translator", {}).get("min_batch_size", 50)
        )

        # å®‰å…¨ä¸‹é?æª¢æŸ¥
        if is_lang and new_size < 20:  # Lang æª”é€šå¸¸å¾ˆçŸ­ï¼?0 ?¯å?ç·?
            new_size = 20 if batch_size > 20 else 0
        elif new_size < MIN_BATCH_SIZE:
            new_size = MIN_BATCH_SIZE if batch_size > MIN_BATCH_SIZE else 0

        # â­é?è¼¯ï?Batch ç¸®åˆ°æ¥µé? ???´æ¥?¾é??™æ‰¹ï¼Œè??†å?çº?
        if new_size <= 0 or new_size == batch_size:
            # ?…æ? Aï¼šå??œæ˜¯? ç‚º RPM (Rate Limit) ??API è«‹æ?å¤±æ??Œé?è¦é?è©?
            if hit_rpm:
                try:
                    logger.info("?? è§¸ç™¼?»ç??åˆ¶ï¼Œå?è©¦å???API Key...")
                    rotate_api_key()
                    # ä¿æ? hit_rpm = Trueï¼Œä?ä¸€è¼ªæ??¨æ–° Key ?è©¦?™æ‰¹
                    continue
                except RuntimeError:
                    logger.error(
                        f"[?Œ] ?´å‘½?¯èª¤ï¼šAPI Key å·²å…¨?¸è€—ç›¡ï¼Œä??®å? Batch ({batch_size}) ?¡æ??ç¸®å°ã€?
                        "å°‡å„²å­˜ç›®?é€²åº¦ä¸¦ç??Ÿä»»?™ã€?
                    )
                    return all_results, "PARTIAL"

            # ?…æ? Bï¼šå??œæ˜¯? ç‚º JSON ?ªæ–·?–æ¨¡?‹å…§å®¹é???(??RPM ?¯èª¤)
            else:
                logger.warning(
                    f"[? ï?] Batch Size å·²ç¸®?³æ¥µ??({batch_size}) ä»æ?çºŒæˆª?·ã€?
                    "ç­–ç•¥ï¼šè·³?æ­¤?¹ï?è¼¸å‡º?Ÿå€¼ï?ï¼Œç›´?¥è??†ä?ä¸€?¹ï??¿å?æµªè²»?¶ä? API Key??
                )

                # 1. èªè¼¸ï¼šç›´?¥å??å?å§‹æ•¸?šï?ä¿è?çµæ?å®Œæ•´
                all_results.extend(current_batch)

                # 2. ç§»é™¤?‡æ?ï¼šè??‡æ?å¾€å¾Œè·³?é€™æ‰¹
                remaining_items = remaining_items[batch_size:]

                # 3. å¦‚æ??„æ??©ä??„ï??ç½® batch_size
                if remaining_items:
                    batch_size = min(
                        len(remaining_items), MIN_BATCH_SIZE if not is_lang else 20
                    )

                # 4. ç¹¼ç? while è¿´å??•ç?å¾Œé¢?„æ±è¥?
                continue

        logger.info(f"[?“] èª¿æ•´ Batchï¼š{batch_size} ??{new_size}")
        batch_size = new_size

    return all_results, "AUTO"  # ï¼ˆåª?‰ç??„è??¸æ??‚æ? raiseï¼Œä??¾åœ¨?™è??ƒå??‰æ­£å¸¸æ?ç¨‹ï?

