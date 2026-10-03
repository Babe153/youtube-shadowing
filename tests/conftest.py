import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/youtube-shadowing/scripts"))


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("YOUTUBE_SHADOWING_DATA_DIR", str(tmp_path / "data"))
