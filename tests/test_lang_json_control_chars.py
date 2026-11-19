"""語言檔字串裡夾未跳脫的控制字元（tab／換行）時仍能讀取。

模組自帶的語言檔偶爾在值裡直接放 tab（例如 RootsClassic 的 ``zh_cn.json``），
嚴格 JSON 不接受，但遊戲照樣讀得到；語系整理要和 Mod DB 掃描器一樣容許，
真正壞掉的 JSON 則照常隔離。
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from translation_tool.core.lang_merge_io import FolderReader, ZipReader
from translation_tool.core.lang_merge_pipeline import _process_single_mod
from translation_tool.utils.safe_json_loader import load_json_auto_encoding

# 值裡夾一個未跳脫的 tab 與換行（json.dumps 不會產生這種寫法，所以手寫）
_TAB_ZH_CN = (
    '{\n  "item.demo.twig": "在收获成熟的农作物时，\t有一定概率获得嫩枝。\n第二行"\n}\n'
)
_BROKEN = '{\n  "item.demo.twig": "缺少結尾引號\n}\n'


def test_read_json_accepts_unescaped_control_chars(tmp_path: Path) -> None:
    (tmp_path / "zh_cn.json").write_bytes(_TAB_ZH_CN.encode("utf-8"))

    data = FolderReader(str(tmp_path)).read_json("zh_cn.json")

    assert data == {
        "item.demo.twig": "在收获成熟的农作物时，\t有一定概率获得嫩枝。\n第二行"
    }


def test_read_json_still_rejects_broken_json(tmp_path: Path) -> None:
    (tmp_path / "zh_cn.json").write_bytes(_BROKEN.encode("utf-8"))

    with pytest.raises(RuntimeError, match="無法讀取 JSON"):
        FolderReader(str(tmp_path)).read_json("zh_cn.json")


def test_load_json_auto_encoding_accepts_unescaped_control_chars(
    tmp_path: Path,
) -> None:
    path = tmp_path / "zh_cn.json"
    path.write_bytes(_TAB_ZH_CN.encode("utf-8"))

    assert load_json_auto_encoding(path) == {
        "item.demo.twig": "在收获成熟的农作物时，\t有一定概率获得嫩枝。\n第二行"
    }


def test_load_json_auto_encoding_still_returns_none_for_broken_json(
    tmp_path: Path,
) -> None:
    path = tmp_path / "zh_cn.json"
    path.write_bytes(_BROKEN.encode("utf-8"))

    assert load_json_auto_encoding(path) is None


def _merge(tmp_path: Path, zh_cn_text: str) -> tuple[dict, Path, Path]:
    zip_path = tmp_path / "mod.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("assets/demo/lang/zh_cn.json", zh_cn_text)
        zf.writestr(
            "assets/demo/lang/en_us.json",
            json.dumps({"item.demo.twig": "Harvesting crops may drop a twig."}),
        )
    output_dir = tmp_path / "output"
    errordata_dir = tmp_path / "errordata"
    with zipfile.ZipFile(zip_path) as zf:
        result = _process_single_mod(
            reader=ZipReader(zf),
            paths={
                "zh_cn": "assets/demo/lang/zh_cn.json",
                "en_us": "assets/demo/lang/en_us.json",
            },
            rules=[],
            output_dir=str(output_dir),
            must_translate_dir=str(tmp_path / "pending"),
            errordata_dir=str(errordata_dir),
        )
    return result, output_dir / "assets" / "demo" / "lang" / "zh_tw.json", errordata_dir


def test_merge_uses_official_zh_cn_with_unescaped_tab(tmp_path: Path) -> None:
    result, zh_tw_path, errordata_dir = _merge(tmp_path, _TAB_ZH_CN)

    assert result["success"] is True
    assert not errordata_dir.exists() or not any(errordata_dir.rglob("*"))
    text = zh_tw_path.read_text(encoding="utf-8")
    # 輸出是標準 JSON：tab 寫成跳脫的 \t，值與原檔相同（簡轉繁）
    value = json.loads(text)["item.demo.twig"]
    assert "\t" in value and "\n" in value
    assert "收穫" in value and "農作物" in value


def test_merge_still_quarantines_broken_zh_cn(tmp_path: Path) -> None:
    _result, _zh_tw_path, errordata_dir = _merge(tmp_path, _BROKEN)

    quarantined = [p.name for p in errordata_dir.rglob("*") if p.is_file()]
    assert "zh_cn.json" in quarantined
