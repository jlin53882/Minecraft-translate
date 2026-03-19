from pathlib import Path
p = Path('translation_tool/core/lang_merge_content_copy.py')
text = p.read_text(encoding='utf-8')
start = text.index('    hit = get_patchouli_book_root(normalized_path)')
marker = '\n    log_prefix = f\"處理內容檔案 \'{input_path}\':\"'
end = text.index(marker, start)
new_block = '''    hit = get_patchouli_book_root(normalized_path)
    book_root, matched_dir_name = hit if hit else (None, None)

    if book_root:
        merger_cfg = load_config_fn().get("lang_merger", {})
        allow_zh_cn = bool(merger_cfg.get("patchouli_skip_en_us_when_zh_cn_exists", False))
        threshold = float(merger_cfg.get("patchouli_effective_translation_threshold", 0.5))

        eff = _compute_patchouli_lang_effectiveness(
            zf,
            book_root,
            threshold=threshold,
            json_module=json_module,
        )
        has_eff_zh_tw = bool(eff.get("zh_tw", False))
        has_eff_zh_cn = bool(eff.get("zh_cn", False))

        rel_path = normalized_path[len(book_root):]
        rel_low = rel_path.lower()
        normalized_root = normalize_patchouli_book_root_fn(book_root).strip("/")
        pending_name = merger_cfg.get("pending_folder_name", "待翻譯")
        patchouli_dirs_cfg = load_config_fn().get("lm_translator", {}).get("patchouli", {}).get("dir_names", ["patchouli_books"])
        patchouli_root_dir = matched_dir_name if isinstance(patchouli_dirs_cfg, list) and patchouli_dirs_cfg else patchouli_dirs_cfg

        if rel_low.startswith("en_us/") and (has_eff_zh_tw or (allow_zh_cn and has_eff_zh_cn)):
            return {"success": True, "log": f"[Patchouli] 跳過已有有效翻譯的英文原件: {normalized_path}"}

        if rel_low.startswith("zh_cn/"):
            rel_path = "zh_tw/" + rel_path[len("zh_cn/"):]
            action_log = "轉換中文化"
        elif rel_low.startswith("zh_tw/"):
            action_log = "寫入譯文"
        else:
            action_log = "歸檔至待翻譯"

        target = os.path.join(output_dir, patchouli_root_dir, normalized_root, rel_path)
        os.makedirs(os.path.dirname(target), exist_ok=True)

        ext = os.path.splitext(input_path)[1].lower()
        if ext in [".json", ".md", ".txt"]:
            try:
                raw_text = read_text_from_zip_fn(zf, input_path)
                tw_content = recursive_translate_dict_fn(raw_text, rules)
                with open(target, "w", encoding="utf-8") as f:
                    f.write(tw_content)
            except Exception as e:
                log_error(f"[Patchouli] 寫入失敗: {e}")
                with zf.open(input_path) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
        else:
            with zf.open(input_path) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)

        return {"success": True, "log": f"[Patchouli] {action_log}: {target}"}
'''
text = text[:start] + new_block + text[end:]
p.write_text(text, encoding='utf-8')
print('patched')
