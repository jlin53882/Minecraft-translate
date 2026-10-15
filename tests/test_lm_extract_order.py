"""extract_items_parallel 的項目順序必須固定（續跑指紋依賴它）。"""


class TestExtractItemsOrder:
    def test_final_items_follow_input_file_order_not_completion_order(
        self, tmp_path, monkeypatch
    ):
        """最後一次 yield 的項目順序必須是輸入檔案順序（續跑指紋依賴它）。"""
        import json
        import threading
        import time

        from translation_tool.core import lm_translator_scan as scan

        files = []
        for index in range(6):
            lang_dir = tmp_path / f"mod{index}" / "lang"
            lang_dir.mkdir(parents=True)
            path = lang_dir / "en_us.json"
            path.write_text(json.dumps({f"k{index}": f"Text {index}"}), "utf-8")
            files.append(path)

        real_loads = json.loads
        lock = threading.Lock()
        started = []

        def slow_first(data, *args, **kwargs):
            text = data.decode("utf-8") if isinstance(data, bytes) else data
            with lock:
                started.append(text)
                position = len(started)
            time.sleep(0.05 * (7 - position))  # 先開始的檔案最後完成
            return real_loads(data, *args, **kwargs)

        monkeypatch.setattr(scan.json, "loads", slow_first)

        file_cache, items = {}, []
        for file_cache, items in scan.extract_items_parallel(
            files=files, export_lang=False, work_thread=6
        ):
            pass

        assert [item["text"] for item in items] == [f"Text {i}" for i in range(6)]
        assert list(file_cache) == [str(f) for f in files]
