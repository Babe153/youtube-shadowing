import argparse
from pathlib import Path

import pytest
from shadowing import (
    WorkflowError,
    assemble,
    atomic_json,
    choose_track,
    classify_download_error,
    extract,
    next_chunk,
    prepare,
    read_json,
    video_id,
)
from subtitles import Cue, normalize


@pytest.mark.parametrize("url", [
    "https://www.youtube.com/watch?v=BaW_jenozKc&list=ignored",
    "https://youtu.be/BaW_jenozKc?t=12", "https://m.youtube.com/shorts/BaW_jenozKc",
    "https://www.youtube.com/embed/BaW_jenozKc", "BaW_jenozKc",
])
def test_single_video_url_canonicalization(url):
    assert video_id(url) == "BaW_jenozKc"


@pytest.mark.parametrize("url", [
    "https://evil.example/watch?v=BaW_jenozKc", "https://youtube.com.evil.example/watch?v=BaW_jenozKc",
    "https://youtube.com/playlist?list=x", "file:///tmp/video", "https://user:password@youtube.com/watch?v=BaW_jenozKc",
    "https://youtube.com/watch?v=BaW_jenozKc&v=AAAAAAAAAAA",
])
def test_non_video_or_untrusted_hosts_rejected(url):
    with pytest.raises(WorkflowError):
        video_id(url)


def test_manual_english_is_preferred_and_tracks_are_not_merged():
    info = {"subtitles": {"fr": [{"ext": "json3", "url": "fr"}],
                           "en-US": [{"ext": "vtt", "url": "manual"}]},
            "automatic_captions": {"en-orig": [{"ext": "json3", "url": "auto"}]}}
    assert choose_track(info) == ("en-US", "manual", {"ext": "vtt", "url": "manual"})
    with pytest.raises(WorkflowError, match="English"):
        choose_track({"subtitles": {"fr": [{"ext": "json3", "url": "fr"}]}})


def test_error_categories_do_not_confuse_access_failures_and_absent_captions():
    assert classify_download_error("HTTP Error 429") == "rate_limit"
    assert classify_download_error("Sign in to confirm you're not a bot") == "bot_check"
    assert classify_download_error("Private video") == "unavailable"
    assert classify_download_error("Unable to extract player response") == "extractor"


def seed_run(run: Path, count=130):
    source = normalize([Cue(i, i, i + 1, f"practice sentence number {i}") for i in range(count)], False)
    atomic_json(run / "source.json", source)
    atomic_json(run / "metadata.json", {
        "video_id": "BaW_jenozKc", "url": "https://www.youtube.com/watch?v=BaW_jenozKc",
        "title": "Practice", "channel": "Demo", "caption_source": "manual",
    })
    prepare(argparse.Namespace(run_dir=str(run), chunk_words=50))
    return source


def test_resume_completed_chunks_and_assemble_full_source(tmp_path):
    source = seed_run(tmp_path)
    while True:
        task = next_chunk(argparse.Namespace(run_dir=str(tmp_path)))
        if task["status"] == "all_chunks_valid":
            break
        chunk = read_json(Path(task["input_path"]))
        atomic_json(Path(task["result_path"]), {
            "schema_version": 1, "chunk_id": chunk["chunk_id"], "chunk_hash": chunk["chunk_hash"],
            "sentences": [{"text": chunk["text"] + "."}],
        })
    study = assemble(tmp_path)
    assert study["word_count"] == len(source["tokens"])
    assert study["sentences"][0]["token_start"] == 0
    assert study["sentences"][-1]["token_end"] == len(source["tokens"])
    assert "&t=0s" in study["sentences"][0]["replay_url"]


def test_render_assembly_rejects_missing_and_stale_results(tmp_path):
    seed_run(tmp_path)
    with pytest.raises(WorkflowError, match="Not all"):
        assemble(tmp_path)
    source = read_json(tmp_path / "source.json")
    source["tokens"][0]["word"] = "changed"
    atomic_json(tmp_path / "source.json", source)
    with pytest.raises(WorkflowError, match="Source changed"):
        assemble(tmp_path)


def test_cross_chunk_continuations_are_joined_without_duplication(tmp_path):
    seed_run(tmp_path, 20)
    first = True
    while True:
        task = next_chunk(argparse.Namespace(run_dir=str(tmp_path)))
        if task["status"] == "all_chunks_valid":
            break
        chunk = read_json(Path(task["input_path"]))
        atomic_json(Path(task["result_path"]), {
            "schema_version": 1, "chunk_id": chunk["chunk_id"], "chunk_hash": chunk["chunk_hash"],
            "sentences": [{"text": chunk["text"] + ".", "join_previous": not first}],
        })
        first = False
    study = assemble(tmp_path)
    assert len(study["sentences"]) == 1
    assert study["sentences"][0]["token_end"] == study["word_count"]


def test_offline_import_reuses_source_and_rejects_different_video(tmp_path):
    caption = tmp_path / "input.srt"
    caption.write_text("1\n00:00:01,000 --> 00:00:05,000\nwe can practice together\n", encoding="utf-8")
    args = argparse.Namespace(url="BaW_jenozKc", run_dir=str(tmp_path / "run"), refresh=False,
                              caption_file=str(caption), title="Practice", channel="Demo",
                              caption_source="manual")
    assert extract(args)["status"] == "extracted"
    assert extract(args)["status"] == "cached"
    args.url = "AAAAAAAAAAA"
    with pytest.raises(WorkflowError, match="different video"):
        extract(args)


def test_imported_source_binds_caption_provenance(tmp_path):
    caption = tmp_path / "input.srt"
    caption.write_text("1\n00:00:01,000 --> 00:00:05,000\nwe can practice together\n", encoding="utf-8")
    run = tmp_path / "run"
    args = argparse.Namespace(url="BaW_jenozKc", run_dir=str(run), refresh=False,
                              caption_file=str(caption), title="Practice", channel="Demo",
                              caption_source="manual")
    extract(args)
    metadata = read_json(run / "metadata.json")
    metadata["caption_source"] = "auto"
    atomic_json(run / "metadata.json", metadata)
    with pytest.raises(WorkflowError, match="metadata changed"):
        extract(args)
