"""Install a personal skill copy; refuse to overwrite an existing installation."""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

from build_packages import copy_skill


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=("codex", "claude-code"), required=True)
    parser.add_argument("--destination", type=Path, help="Override the parent skills directory")
    args = parser.parse_args()
    default = Path.home() / (".agents/skills" if args.target == "codex" else ".claude/skills")
    parent = (args.destination or default).expanduser().resolve()
    destination = parent / "youtube-shadowing"
    if destination.exists():
        parser.error(f"Installation already exists at {destination}; it was left untouched")
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".shadowing-install-", dir=parent) as temporary:
        staged = Path(temporary) / "youtube-shadowing"
        copy_skill(staged)
        # os.rename refuses an existing nonempty destination on supported platforms.
        os.rename(staged, destination)
    print(json.dumps({"installed": str(destination), "target": args.target}))


if __name__ == "__main__":
    main()
