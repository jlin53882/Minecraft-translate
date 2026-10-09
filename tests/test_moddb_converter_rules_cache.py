"""ModDB converter behavior and compiled-rule cache coverage."""

from concurrent.futures import ThreadPoolExecutor

from translation_tool.translation_db.models import ScanItem
from translation_tool.translation_db.repository import TranslationDB
from translation_tool.translation_db.scanner import make_converter
from translation_tool.translation_db.schema import (
    KIND_LANG,
    SRC_JAR_CN,
    SRC_JAR_TW,
)
from translation_tool.utils import text_processor as tp


def test_converter_keeps_outer_snapshot_and_tracks_inner_rule_mutation():
    rules = tp.ReplaceRules([{"from": "wire", "to": "FIRST"}])
    convert = make_converter(rules)

    # The old converter snapshots list membership, so newly appended rules do
    # not enter an in-flight scan's behavior.
    rules.append({"from": "assembled", "to": "NEW RULE"})
    assert convert("wire assembled") == "FIRST assembled"

    # The shallow snapshot still shares the tracked rule rows. Mutating one of
    # those rows has historically affected the converter and must invalidate it.
    rules[0]["to"] = "SECOND"
    assert convert("wire assembled") == "SECOND assembled"


def test_converter_plain_list_keeps_snapshot_semantics():
    rules = [{"from": "wire", "to": "FIRST"}]
    convert = make_converter(rules)

    rules.append({"from": "assembled", "to": "NEW RULE"})
    assert convert("wire assembled") == "FIRST assembled"

    rules[0]["to"] = "SECOND"
    assert convert("wire assembled") == "SECOND assembled"


def test_converter_none_and_empty_rules_keep_opencc_behavior():
    assert make_converter(None)("wire") == "wire"
    assert make_converter([])("wire") == "wire"


def test_converter_preserves_long_first_chained_regex_and_invalid_regex_rules():
    convert = make_converter(
        tp.ReplaceRules(
            [
                {"from": "capacitor block", "to": "long"},
                {"from": "capacitor", "to": "short"},
                {"from": "source", "to": "first"},
                {"from": "first", "to": "second"},
                {"from": r"(gear)-(\d+)", "to": "$2_$1"},
                {"from": "[", "to": "left-bracket"},
            ]
        )
    )

    assert convert("capacitor block source gear-12 [") == (
        "long second 12_gear left-bracket"
    )


def test_converter_uses_constant_time_revision_signature_for_replace_rules(
    monkeypatch,
):
    rules = tp.ReplaceRules(
        [{"from": f"needle-{index}", "to": f"value-{index}"} for index in range(200)]
    )
    signatures = []
    original_signature = tp._rules_signature

    def record_signature(active_rules):
        signature = original_signature(active_rules)
        signatures.append(signature[0])
        return signature

    monkeypatch.setattr(tp, "_rules_signature", record_signature)
    convert = make_converter(rules)

    for _ in range(8):
        assert convert("no rule matches this text") == "no rule matches this text"

    assert signatures == ["revision"] * 8


def test_converter_per_task_rule_snapshots_do_not_contaminate_each_other():
    first_rules = tp.ReplaceRules([{"from": "wire", "to": "OLD CONFIG"}])
    first_converter = make_converter(first_rules)

    # A config reload gives the next task its own rules object and converter.
    second_rules = tp.ReplaceRules([{"from": "wire", "to": "NEW CONFIG"}])
    second_converter = make_converter(second_rules)

    assert first_converter("wire") == "OLD CONFIG"
    assert second_converter("wire") == "NEW CONFIG"


def test_concurrent_moddb_converters_keep_their_own_rules():
    def convert_many(index):
        rules = tp.ReplaceRules([{"from": "wire", "to": f"OUTPUT-{index}"}])
        convert = make_converter(rules)
        return [convert("wire") for _ in range(40)]

    with ThreadPoolExecutor(max_workers=8) as pool:
        outputs = list(pool.map(convert_many, range(8)))

    assert outputs == [[f"OUTPUT-{index}"] * 40 for index in range(8)]


def test_converter_preserves_opencc_text_and_rule_replacement():
    convert = make_converter(tp.ReplaceRules([{"from": "電線", "to": "特製導線"}]))

    assert convert("简体中文 §a{0} 電線") == "簡體中文 §a{0} 特製導線"


def test_moddb_ingest_converts_only_simplified_chinese_and_keeps_tw(tmp_path):
    db = TranslationDB(tmp_path / "synthetic-mod-translation.db")
    convert = make_converter(tp.ReplaceRules([{"from": "wire", "to": "自訂電線"}]))
    try:
        stats = db.ingest(
            "1.21.1",
            [
                ScanItem(KIND_LANG, "synthetic", "wire", "", "", "wire"),
                ScanItem(KIND_LANG, "synthetic", "tw", "", "人工繁中", "wire"),
            ],
            convert,
        )

        rows = {row.key: row for row in db.list_entries("1.21.1")[0]}
        assert stats.new_entries == 2
        assert (rows["wire"].zh_tw, rows["wire"].source) == (
            "自訂電線",
            SRC_JAR_CN,
        )
        assert (rows["tw"].zh_tw, rows["tw"].source) == ("人工繁中", SRC_JAR_TW)
        zh_cn = db._one(
            "SELECT t.zh_cn FROM translation t JOIN entry e ON e.id=t.entry_id "
            "WHERE e.mc_version=? AND e.key=? AND t.source=?",
            ("1.21.1", "wire", SRC_JAR_CN),
        )
        assert zh_cn == ("wire",)
    finally:
        db.close()
