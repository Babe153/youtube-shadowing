"""Create a clearly labelled demo from original text, using the real workflow."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/youtube-shadowing/scripts"))

from shadowing import atomic_json, extract, prepare, read_json, render
from subtitles import digest

TEXT = [
    "A small daily practice can make a big difference.",
    "Choose a short video that you actually enjoy watching.",
    "Listen to one sentence before you try to repeat it.",
    "You don't have to understand every word on the first try.",
    "Notice which words the speaker stresses, and where they pause.",
    "Then say the sentence aloud, following the same rhythm.",
    "If it feels difficult, slow down and try again.",
    "Keep the original words, even when the grammar sounds informal.",
    "That's a very, very important part of learning natural speech.",
    "A clear page helps you focus on the next sentence.",
    "Click its number when you want to replay the video.",
    "After a few minutes, save your notes and come back tomorrow.",
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "examples")
    args = parser.parse_args()
    scratch = ROOT / "work/demo"
    scratch.mkdir(parents=True, exist_ok=True)
    os.environ["YOUTUBE_SHADOWING_DATA_DIR"] = str(scratch / "data")
    captions = {"events": [
        {"tStartMs": i * 6000, "dDurationMs": 5000, "segs": [{"utf8": line.lower().rstrip(".")}]}
        for i, line in enumerate(TEXT)
    ]}
    caption_file = scratch / "original.json3"
    atomic_json(caption_file, captions)
    run = scratch / "run"
    extract(argparse.Namespace(url="BaW_jenozKc", run_dir=str(run), refresh=True,
                              caption_file=str(caption_file), caption_source="manual",
                              title="A small daily practice | 影子跟读示例", channel="Original practice text"))
    metadata = read_json(run / "metadata.json")
    metadata["demo"] = True
    metadata["duration"] = 72
    atomic_json(run / "metadata.json", metadata)
    source_data = read_json(run / "source.json")
    source_data["metadata_hash"] = digest(metadata)
    source_data["source_hash"] = digest({key: value for key, value in source_data.items() if key != "source_hash"})
    atomic_json(run / "source.json", source_data)
    prepare(argparse.Namespace(run_dir=str(run), chunk_words=1000))
    chunk = read_json(run / "chunks/chunk-0001.input.json")
    atomic_json(run / "chunks/chunk-0001.result.json", {
        "schema_version": 1, "chunk_id": chunk["chunk_id"], "chunk_hash": chunk["chunk_hash"],
        "sentences": [{"text": line, "uncertain": False} for line in TEXT],
    })
    result = render(argparse.Namespace(run_dir=str(run), library_dir=str(args.output_dir.resolve())))
    source = Path(result["pdf_path"])
    # Publish a stable repo demo name; rendering has already checked its text/links.
    destination = args.output_dir.resolve() / "sample-handout.pdf"
    destination.write_bytes(source.read_bytes())
    source.unlink()
    source.parent.rmdir()
    print(json.dumps({"demo_pdf": str(destination), **{k: v for k, v in result.items() if k != "pdf_path"}}))


if __name__ == "__main__":
    main()
