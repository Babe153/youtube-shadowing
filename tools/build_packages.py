"""Build installable archives from one self-contained skill source."""
from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills/youtube-shadowing"
NAME = "youtube-shadowing"
EXCLUDED = {".venv", "__pycache__", ".pytest_cache", ".ruff_cache"}


def copy_skill(destination: Path):
    shutil.copytree(SKILL, destination,
                    ignore=lambda _path, entries: [e for e in entries if e in EXCLUDED or e.endswith(".pyc")])
    shutil.copy2(ROOT / "LICENSE", destination / "LICENSE")


def zip_directory(directory: Path, archive: Path):
    with ZipFile(archive, "w", compression=ZIP_DEFLATED) as output:
        for file in sorted(directory.rglob("*")):
            if file.is_file():
                output.write(file, file.relative_to(directory).as_posix())


def build(destination: Path) -> list[Path]:
    destination.mkdir(parents=True, exist_ok=True)
    archives = []
    with tempfile.TemporaryDirectory(prefix="shadowing-packages-") as temporary:
        staging = Path(temporary)
        for target in ("codex", "claude-code", "workbuddy"):
            root = staging / target
            folder = root / "skills" / NAME if target == "claude-code" else root / NAME
            copy_skill(folder)
            if target == "claude-code":
                (root / ".claude-plugin").mkdir(parents=True)
                shutil.copy2(ROOT / ".claude-plugin/plugin.json", root / ".claude-plugin/plugin.json")
            if target == "workbuddy":
                metadata = json.loads((ROOT / "packaging/workbuddy/metadata.json").read_text(encoding="utf-8"))
                skill_file = folder / "SKILL.md"
                source = skill_file.read_text(encoding="utf-8")
                parts = source.split("---", 2)
                extra = "\n".join(f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in metadata.items())
                skill_file.write_text(f"---{parts[1].rstrip()}\n{extra}\n---{parts[2]}", encoding="utf-8")
            archive = destination / f"{NAME}-{target}-0.1.0.zip"
            zip_directory(root, archive)
            archives.append(archive)
    return archives


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    print(json.dumps({"archives": [str(p) for p in build(args.output_dir.resolve())]}))


if __name__ == "__main__":
    main()
