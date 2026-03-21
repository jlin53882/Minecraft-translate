"""
測試 text_processor 的執行緒安全行為（ATK-003 / Gap 2）。

驗證 apply_replace_rules / _init_replace_rules_cache 在多執行緒環境下，
各執行緒的 rules 不會互相污染。

# 背景
text_processor.py 已改用 threading.local() 實作執行緒隔離快取。
本測試確認該實作對以下情境有效：
1. 兩個執行緒使用不同 rules，結果各自正確。
2. 10 個執行緒同時初始化不同 rules，各自結果正確。
"""

import sys
import threading
from pathlib import Path

# 確保 translation_tool 在 sys.path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from translation_tool.utils.text_processor import apply_replace_rules, _init_replace_rules_cache


# =============================================================================
# ATK-003 / Gap 2：執行緒隔離測試
# =============================================================================

def test_two_threads_different_rules_isolation():
    """
    情境：兩個執行緒同時使用不同 rules，確認結果不互相污染。

    Arrange：
        - Thread A 用 rules [hello→HI_A]
        - Thread B 用 rules [hello→HI_B]
    Act：
        - 兩執行緒並發呼叫 _init_replace_rules_cache + apply_replace_rules
    Assert：
        - A 的結果是 "HI_A world"（不是 "HI_B world"）
        - B 的結果是 "HI_B world"（不是 "HI_A world"）
    """
    results = {}
    lock = threading.Lock()

    def worker(label: str, rules):
        # 每個執行緒獨立初始化自己的 rules 快取
        _init_replace_rules_cache(rules)
        # 用同一段文字測試
        output = apply_replace_rules("hello world", rules)
        with lock:
            results[label] = output

    t1 = threading.Thread(target=worker, args=("A", [{"from": "hello", "to": "HI_A"}]))
    t2 = threading.Thread(target=worker, args=("B", [{"from": "hello", "to": "HI_B"}]))

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert results["A"] == "HI_A world", (
        f"A 吃到錯誤規則，預期 'HI_A world'，實際：'{results['A']}'"
    )
    assert results["B"] == "HI_B world", (
        f"B 吃到錯誤規則，預期 'HI_B world'，實際：'{results['B']}'"
    )


def test_ten_threads_concurrent_rules_initialization():
    """
    情境：10 個執行緒同時以不同 rules 初始化，確認各自結果正確。

    Arrange：
        - 10 個執行緒各自持有不同 rules（替換同一段文字的不同部分）
    Act：
        - 所有執行緒並發執行 _init_replace_rules_cache + apply_replace_rules
    Assert：
        - 每個執行緒的輸出都符合自己攜帶的 rules，不被其他執行緒影響
    """
    results = {}
    lock = threading.Lock()

    # 10 個不同的 label → rule 對應表
    labels_and_rules = [
        (f"T{i}", [{"from": "hello", "to": f"HI_{i:02d}"}])
        for i in range(10)
    ]

    def worker(label: str, rules):
        _init_replace_rules_cache(rules)
        output = apply_replace_rules("hello world", rules)
        with lock:
            results[label] = output

    threads = [
        threading.Thread(target=worker, args=(label, rules))
        for label, rules in labels_and_rules
    ]

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # 驗證每個執行緒結果都符合自己的規則
    all_pass = True
    for i, (label, rules) in enumerate(labels_and_rules):
        expected = f"HI_{i:02d} world"
        actual = results[label]
        if actual != expected:
            all_pass = False

    assert all_pass, (
        "至少有一個執行緒的結果不符合預期。"
        f" 結果：{results}"
    )


def test_thread_isolation_with_complex_rules():
    """
    情境：兩個執行緒各自使用多條規則（含正規表示式），確認互不污染。

    Arrange：
        - Thread X：規則為 "red"→"紅色", "blue"→"藍色"
        - Thread Y：規則為 "red"→"RED", "green"→"綠色"
    Act：
        - 並發執行
    Assert：
        - X 結果含 "紅色" 和 "藍色"，不含 "RED"
        - Y 結果含 "RED" 和 "綠色"，不含 "紅色"
    """
    results = {}
    lock = threading.Lock()

    def worker_x():
        rules = [
            {"from": "red", "to": "紅色"},
            {"from": "blue", "to": "藍色"},
        ]
        _init_replace_rules_cache(rules)
        out = apply_replace_rules("red blue green", rules)
        with lock:
            results["X"] = out

    def worker_y():
        rules = [
            {"from": "red", "to": "RED"},
            {"from": "green", "to": "綠色"},
        ]
        _init_replace_rules_cache(rules)
        out = apply_replace_rules("red blue green", rules)
        with lock:
            results["Y"] = out

    t_x = threading.Thread(target=worker_x)
    t_y = threading.Thread(target=worker_y)

    t_x.start()
    t_y.start()
    t_x.join()
    t_y.join()

    # X 的預期結果：red→紅色, blue→藍色, green 不變
    assert "紅色" in results["X"], f"X 缺少 '紅色'：{results['X']}"
    assert "藍色" in results["X"], f"X 缺少 '藍色'：{results['X']}"
    assert "RED" not in results["X"], f"X 意外出現 'RED'（被 Y 污染）：{results['X']}"

    # Y 的預期結果：red→RED, green→綠色, blue 不變
    assert "RED" in results["Y"], f"Y 缺少 'RED'：{results['Y']}"
    assert "綠色" in results["Y"], f"Y 缺少 '綠色'：{results['Y']}"
    assert "紅色" not in results["Y"], f"Y 意外出現 '紅色'（被 X 污染）：{results['Y']}"


def test_reinit_same_thread_does_not_overwrite():
    """
    驗證同一執行緒重複呼叫 _init_replace_rules_cache 不會覆寫既有快取。

    Arrange：
        - 同一執行緒連續初始化兩組不同的 rules
    Act：
        - 第一次：hello→FIRST
        - 第二次：hello→SECOND（預期不覆寫）
        - 套用 rules
    Assert：
        - 結果仍是 FIRST（因為快取已被第一次初始化鎖定）
    """
    # 先初始化第一組規則
    _init_replace_rules_cache([{"from": "hello", "to": "FIRST"}])
    # 嘗試用第二組規則覆寫（預期被 guard 跳過）
    _init_replace_rules_cache([{"from": "hello", "to": "SECOND"}])

    # 套用時應使用第一組（因為 cache 已被初始化）
    result = apply_replace_rules("hello world", [{"from": "hello", "to": "FIRST"}])

    assert result == "FIRST world", (
        f"快取被意外覆寫，預期 'FIRST world'，實際：'{result}'"
    )
