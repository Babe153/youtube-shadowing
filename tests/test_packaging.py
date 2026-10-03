import json
import sys
from zipfile import ZipFile

from conftest import ROOT

sys.path.insert(0, str(ROOT / "tools"))
from build_packages import build


def test_archives_are_self_contained_and_workbuddy_metadata_is_added(tmp_path):
    archives = build(tmp_path)
    assert len(archives) == 3
    for archive in archives:
        with ZipFile(archive) as package:
            names = package.namelist()
            prefix = "skills/youtube-shadowing/" if "claude-code" in archive.name else "youtube-shadowing/"
            for file in ("SKILL.md", "pyproject.toml", "uv.lock", "scripts/shadowing.py",
                         "scripts/subtitles.py", "scripts/pdf_layout.py", "references/editing-rules.md",
                         "references/data-contract.md", "assets/fonts/NotoSansSC-Regular.ttf",
                         "assets/fonts/OFL.txt", "LICENSE"):
                assert prefix + file in names
            assert not any(".venv/" in name or "__pycache__/" in name for name in names)
            assert not any(name.startswith("/") or ".." in name.split("/") for name in names)
            if "workbuddy" in archive.name:
                text = package.read(prefix + "SKILL.md").decode()
                for field in ("description_zh:", "description_en:", "version:", "author:"):
                    assert field in text
            if "claude-code" in archive.name:
                manifest = json.loads(package.read(".claude-plugin/plugin.json"))
                assert manifest["name"] == "youtube-shadowing"
