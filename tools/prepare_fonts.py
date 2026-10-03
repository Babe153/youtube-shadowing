"""Rebuild the bundled static font from a pinned Google Fonts revision (developer tool)."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.request import urlopen

from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

ROOT = Path(__file__).resolve().parents[1]
REVISION = "9710da1eacb3be272583c3224dcb70f9da6eadbb"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", default=REVISION)
    args = parser.parse_args()
    destination = ROOT / "skills/youtube-shadowing/assets/fonts"
    destination.mkdir(parents=True, exist_ok=True)
    scratch = ROOT / "work/fonts"
    scratch.mkdir(parents=True, exist_ok=True)
    base = f"https://raw.githubusercontent.com/google/fonts/{args.revision}/ofl/notosanssc/"
    variable = scratch / "NotoSansSC-variable.ttf"
    with urlopen(base + "NotoSansSC%5Bwght%5D.ttf", timeout=60) as response:
        variable.write_bytes(response.read())
    with urlopen(base + "OFL.txt", timeout=30) as response:
        (destination / "OFL.txt").write_bytes(response.read())
    font = TTFont(variable)
    instance = instantiateVariableFont(font, {"wght": 400}, inplace=False)
    instance.save(destination / "NotoSansSC-Regular.ttf")
    (destination / "provenance.json").write_text(json.dumps({
        "source": "google/fonts", "revision": args.revision,
        "file": "ofl/notosanssc/NotoSansSC[wght].ttf", "instance": {"wght": 400},
        "license": "SIL Open Font License 1.1",
    }, indent=2) + "\n", encoding="utf-8")
    print(destination)


if __name__ == "__main__":
    main()
