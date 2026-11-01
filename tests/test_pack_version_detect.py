"""模組包遊戲版本偵測：從資料夾與 zip 的描述檔讀出版本，無法判斷時回傳 None。"""

import json
import zipfile
from pathlib import Path

from translation_tool.core.pack_version_detect import (
    detect_minecraft_version,
    known_minecraft_versions,
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_curseforge_manifest_in_the_pack_root(tmp_path):
    _write(tmp_path / "manifest.json", json.dumps({"minecraft": {"version": "1.20.1"}}))
    assert detect_minecraft_version(str(tmp_path)) == "1.20.1"


def test_modrinth_index_in_a_subfolder(tmp_path):
    _write(
        tmp_path / "ATM10" / "modrinth.index.json",
        json.dumps({"dependencies": {"minecraft": "1.21.1"}}),
    )
    assert detect_minecraft_version(str(tmp_path)) == "1.21.1"


def test_prism_instance_cfg_and_mmc_pack(tmp_path):
    _write(tmp_path / "instance.cfg", "InstanceType=OneSix\nIntendedVersion=1.18.2\n")
    assert detect_minecraft_version(str(tmp_path)) == "1.18.2"

    other = tmp_path / "other"
    _write(
        other / "mmc-pack.json",
        json.dumps({"components": [{"uid": "net.minecraft", "version": "1.19.2"}]}),
    )
    assert detect_minecraft_version(str(other)) == "1.19.2"


def test_zip_archive_is_read_without_extracting(tmp_path):
    archive = tmp_path / "pack.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("overrides/readme.txt", "hi")
        zf.writestr("manifest.json", json.dumps({"minecraft": {"version": "1.21"}}))
    assert detect_minecraft_version(str(archive)) == "1.21"
    assert not (tmp_path / "manifest.json").exists()


def test_unknown_or_broken_descriptors_return_none(tmp_path):
    assert detect_minecraft_version(str(tmp_path)) is None
    _write(tmp_path / "manifest.json", "{not json")
    assert detect_minecraft_version(str(tmp_path)) is None
    _write(tmp_path / "manifest.json", json.dumps({"minecraft": {"version": "latest"}}))
    assert detect_minecraft_version(str(tmp_path)) is None
    assert detect_minecraft_version("") is None
    assert detect_minecraft_version(str(tmp_path / "missing")) is None


def test_version_options_come_from_the_table_newest_first():
    options = known_minecraft_versions()
    assert "1.21.1" in options and "1.21" in options
    assert options == sorted(
        options, key=lambda v: tuple(int(x) for x in v.split(".")), reverse=True
    )
