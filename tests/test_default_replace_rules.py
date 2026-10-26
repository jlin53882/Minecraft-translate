"""預設 replace_rules.json 與替換引擎：短規則不可改壞較長詞，且結果不會被重複替換。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.audit_replace_rules import (
    OFFICIAL_TW,
    convert_all,
    corpus_metrics,
    load_official_pairs,
    static_checks,
)
from translation_tool.utils.replace_rules import apply_replace_rules, exact_source
from translation_tool.utils.replace_rules_diagnostics import (
    CROSS_REFERENCE,
    diagnose_rules,
)
from translation_tool.utils.text_processor import convert_text, load_replace_rules

_REPO_RULES = Path(__file__).resolve().parents[1] / "replace_rules.json"


@pytest.fixture(scope="module")
def default_rules():
    return load_replace_rules(str(_REPO_RULES))


@pytest.mark.parametrize(
    ("simplified", "expected"),
    [
        ("深色橡木椅子", "黑橡木椅子"),
        (
            "深色橡木",
            "黑橡木塊",
        ),  # 單獨名稱由整段規則處理（block.minecraft.dark_oak_wood）
        ("深色橡木原木", "黑橡木原木"),
        ("去皮橡木柱子", "剝皮橡木柱子"),
        ("去皮橡木原木", "剝皮橡木原木"),
        ("红树木椅子", "紅樹林木椅子"),
        ("去皮红树木柱", "剝皮紅樹林木柱"),
        ("去皮云杉木桿", "剝皮杉木桿"),
        ("红砖窗户", "紅磚窗戶"),
        ("下界砖柱", "地獄磚柱"),
    ],
)
def test_short_wood_rules_do_not_corrupt_longer_words(
    default_rules, simplified, expected
):
    assert convert_text(simplified, default_rules) == expected


def test_material_prefix_rules_do_not_append_a_suffix(rule_list):
    # 規則是子字串替換：以「木」「磚」結尾的材料詞會套用在模組的複合詞上（紅樹木椅子、紅磚窗戶），
    # 譯詞若多加「塊」「頭」就會變成「紅樹林木塊椅子」「紅磚頭窗戶」。單獨名稱的差異由更長的規則處理。
    bad = [
        (rule["from"], rule["to"])
        for rule in rule_list
        if rule["from"][-1:] in ("木", "磚") and rule["to"][-1:] != rule["from"][-1:]
    ]
    assert bad == []


def test_default_rules_are_idempotent_on_their_own_output(default_rules):
    for source in ("深色橡木椅子", "去皮橡木柱子", "騾子刷怪蛋", "紅磚塊"):
        once = convert_text(source, default_rules)
        assert apply_replace_rules(once, default_rules) == once


def test_exact_rules_only_match_the_whole_text():
    rules = [
        {"from": "^紅樹木$", "to": "紅樹林木塊"},
        {"from": "紅樹木", "to": "紅樹林木"},
        {"from": "林木", "to": "森林"},
    ]
    assert (
        apply_replace_rules("紅樹木", rules) == "紅樹林木塊"
    )  # 結果不再被其他規則替換
    assert apply_replace_rules("紅樹木椅子", rules) == "紅樹林木椅子"
    assert apply_replace_rules(" 紅樹木", rules) == " 紅樹林木"
    # 中間含正則符號的不是整段規則
    assert exact_source("^紅.木$") is None
    assert exact_source("^$") is None


@pytest.mark.parametrize(
    ("simplified", "expected"),
    [
        ("红树木", "紅樹林木塊"),
        ("去皮云杉木", "剝皮杉木塊"),
        (
            "红砖",
            "紅磚",
        ),  # 簡中物品「紅磚」與繁中方塊「紅磚」同字，見 test_rules_keep_official_zh_tw_unchanged
        ("红砖块", "紅磚"),
        ("红砖墙", "紅磚牆"),
        ("蜜脾", "蜂巢"),
        ("钟", "鐘"),
    ],
)
def test_official_standalone_names_use_exact_rules(default_rules, simplified, expected):
    assert convert_text(simplified, default_rules) == expected


def test_engine_is_longest_match_single_pass():
    rules = [
        {"from": "深色橡木", "to": "黑橡木"},
        {"from": "深色橡木椅子", "to": "黑橡木長椅"},
        {"from": "桌", "to": "桌子"},
    ]
    assert apply_replace_rules("深色橡木椅子", rules) == "黑橡木長椅"
    assert apply_replace_rules("深色橡木桌", rules) == "黑橡木桌子"
    assert apply_replace_rules("深色橡木桌子", rules) == "黑橡木桌子"  # 不重複替換


# ---------------------------------------------------------------- 結構檢查
# 譯詞含有其他規則原詞（交叉引用）的規則。單次掃描、最長匹配下是合法的：較長的原詞先匹配，
# 目的詞受保護；每一筆都已依官方用語定案，新增時必須在這裡寫明原因。
_LONGER_WINS = "長原詞先匹配且目的詞受保護，結果等於官方用語"
_PROTECTION = "from==to 的保護型規則：擋住較短規則（例如 迅捷→加速）改壞官方完整名稱"
_AMBIGUOUS = (
    "同一簡體詞在官方有不同譯名，引擎把目的詞收斂成較短規則的譯詞（需使用者判斷）"
)
_VERB = "動詞用法的保護型規則：擋住名詞規則（命令→指令）改到「命令你的小兵」"
ALLOWED_CROSS_REFERENCES = {
    "去皮緋紅菌核": _LONGER_WINS,
    "去皮詭異菌核": _LONGER_WINS,
    "苔石磚樓梯": _LONGER_WINS,
    "蟲蝕苔石磚": _LONGER_WINS,
    "皮革外套": _LONGER_WINS,
    "緋紅菌巖": _LONGER_WINS,
    "緋紅菌核": _LONGER_WINS,
    "詭異菌核": _LONGER_WINS,
    "苔石磚": _LONGER_WINS,
    "皮革褲子": _PROTECTION,
    "最大傷害吸收值": _PROTECTION,
    "墨囊噴濺": _PROTECTION,
    "隱身的隊友": _PROTECTION,
    "向上飄浮": _PROTECTION,
    "動態暫時無法使用": _PROTECTION,
    "捕獲一條魚": _PROTECTION,
    "配置給": _PROTECTION,
    "命令你": _VERB,
    "命令他": _VERB,
    "命令她": _VERB,
    "命令牠": _VERB,
    "命令它": _VERB,
    "命令其": _VERB,
}

# 譯詞含空白：官方譯名本身有空白（含英文、數字或標點），不是多餘空白。
ALLOWED_SPACE_IN_TO = {
    "Minecraft：試煉版",
    "結束了…再一次…",
    "被啟用的TNT",
    "TNT礦車",
    "花費：1顆青金石",
}

# 輸出與 to 不同：to 恰好等於另一條規則的原詞，編譯時被收斂（見 docs/developer-guide/replace-rules.md）。
ALLOWED_OUTPUT_DRIFT = {}


@pytest.fixture(scope="module")
def rule_list():
    return json.loads(_REPO_RULES.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def static_report(rule_list):
    return static_checks(rule_list)


def test_rules_have_no_edge_whitespace_or_duplicates(static_report):
    assert static_report.get("edge_whitespace", []) == []
    assert static_report.get("duplicate_from", []) == []


def test_no_unexpected_space_in_destinations(static_report):
    unexpected = [
        item
        for item in static_report.get("inner_space_in_to", [])
        if item["from"] not in ALLOWED_SPACE_IN_TO
    ]
    assert unexpected == []


def test_every_from_can_appear_after_s2twp(static_report):
    assert static_report.get("unreachable_from", []) == []


def test_rule_output_matches_its_destination(static_report):
    drift = {item["from"] for item in static_report.get("output_differs_from_to", [])}
    assert drift == set(ALLOWED_OUTPUT_DRIFT)


def test_cross_references_are_all_decided(rule_list):
    issues = diagnose_rules(rule_list)
    found = {
        rule_list[idx]["from"]
        for idx, items in issues.items()
        if any(i.kind == CROSS_REFERENCE for i in items)
    }
    assert found - set(ALLOWED_CROSS_REFERENCES) == set(), "未定案的交叉引用"
    assert set(ALLOWED_CROSS_REFERENCES) - found == set(), "允許清單有已不存在的項目"


# ---------------------------------------------------------------- 官方語料一致率
# 固定門檻：目前結果向下取整（官方名稱類 99.65%、官方全部 57.99%）；改善規則後請調高。
MIN_OFFICIAL_NAME_EXACT_RATE = 0.99
MIN_OFFICIAL_ALL_EXACT_RATE = 0.57


def test_rules_keep_official_zh_tw_unchanged(default_rules):
    # 規則也會套用在模組自帶的繁中（語系合併、Mod DB 掃描），所以已經是官方繁中的文字不能被改動。
    # 簡中與繁中同字不同義的詞（簡中物品「紅磚」＝繁中「紅磚頭」，繁中「紅磚」卻是方塊）因此不寫規則。
    tw = json.loads(OFFICIAL_TW.read_text(encoding="utf-8"))
    changed = {
        key: (text, apply_replace_rules(text, default_rules))
        for key, text in tw.items()
        if isinstance(text, str) and apply_replace_rules(text, default_rules) != text
    }
    assert changed == {}


def test_official_consistency_does_not_regress(default_rules):
    pairs = load_official_pairs()
    metrics = corpus_metrics(pairs, convert_all(pairs, default_rules))["official"]
    assert metrics["name"]["exact_rate"] >= MIN_OFFICIAL_NAME_EXACT_RATE
    assert metrics["all"]["exact_rate"] >= MIN_OFFICIAL_ALL_EXACT_RATE


@pytest.mark.parametrize(
    ("simplified", "expected"),
    [
        ("喷溅型迅捷药水", "飛濺迅捷藥水"),
        ("滞留型力量药水", "滯留力量藥水"),
        ("铁傀儡", "鐵魔像"),
        ("铁魔像", "鐵魔像"),
        ("白色挽具", "白色帽鞍"),
        ("苦力怕刷怪蛋", "苦力怕生怪蛋"),
        ("磨制花岗岩", "拋光花崗岩"),
        ("迅捷之箭", "迅捷之箭"),
    ],
)
def test_official_names_from_audit(default_rules, simplified, expected):
    assert convert_text(simplified, default_rules) == expected
