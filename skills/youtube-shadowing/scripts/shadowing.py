"""CLI used by the host assistant. It never calls an LLM service."""
from __future__ import annotations

import argparse
import ctypes
import importlib.metadata
import json
import os
import re
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from subtitles import (
    CaptionError,
    digest,
    make_chunks,
    normalize,
    parse_json3,
    parse_timed_text,
    validate_chunk,
)

SKILL_ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.1.0"
VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


class WorkflowError(Exception):
    def __init__(self, kind: str, message: str, **details):
        super().__init__(message)
        self.kind = kind
        self.details = details


def atomic_json(path: Path, value: object):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".shadowing-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise WorkflowError("missing_file", f"Missing file: {path}") from exc
    except (ValueError, UnicodeError) as exc:
        raise WorkflowError("invalid_json", f"Invalid JSON: {path}", detail=str(exc)) from exc


def video_id(url: str) -> str:
    if VIDEO_ID.fullmatch(url):
        return url
    parsed = urlparse(url)
    if parsed.scheme not in ("https", "http") or parsed.username or parsed.password:
        raise WorkflowError("invalid_url", "Provide a YouTube video URL or 11-character video ID")
    host = (parsed.hostname or "").lower()
    if parsed.port not in (None, 80, 443):
        raise WorkflowError("invalid_url", "Custom URL ports are not supported")
    if host == "youtu.be":
        identifier = parsed.path.strip("/")
    elif host in ("youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"):
        if parsed.path == "/watch":
            values = parse_qs(parsed.query).get("v", [])
            identifier = values[0] if len(values) == 1 else ""
        else:
            match = re.fullmatch(r"/(?:shorts|embed|live)/([A-Za-z0-9_-]{11})/?", parsed.path)
            identifier = match[1] if match else ""
    else:
        identifier = ""
    if not VIDEO_ID.fullmatch(identifier):
        raise WorkflowError("invalid_url", "A single YouTube video is required; playlists are not supported")
    return identifier


def documents_dir() -> Path:
    if os.name == "nt":
        buffer = ctypes.create_unicode_buffer(32768)
        # CSIDL_PERSONAL honors redirected Documents folders, including OneDrive.
        if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buffer) == 0:
            return Path(buffer.value)
    if sys.platform.startswith("linux"):
        config_home = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
        config_file = config_home / "user-dirs.dirs"
        if config_file.is_file():
            match = re.search(r'^XDG_DOCUMENTS_DIR="([^"\n]+)"',
                              config_file.read_text(encoding="utf-8"), re.MULTILINE)
            if match:
                candidate = Path(match[1].replace("$HOME", str(Path.home())))
                if candidate.is_absolute():
                    return candidate
    return Path.home() / "Documents"


def data_dir() -> Path:
    override = os.environ.get("YOUTUBE_SHADOWING_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    from platformdirs import user_data_path
    return user_data_path("YouTubeShadowing", appauthor=False)


def settings() -> dict:
    path = data_dir() / "config.json"
    default = {"schema_version": 1, "library_dir": str(documents_dir() / "YouTube Shadowing"),
               "language": "en", "replay_lead_seconds": 1.0}
    if path.exists():
        stored = read_json(path)
        if not isinstance(stored, dict) or stored.get("schema_version") != 1:
            raise WorkflowError("invalid_config", "Unsupported settings schema")
        default.update(stored)
    if not isinstance(default.get("library_dir"), str) or not Path(default["library_dir"]).is_absolute():
        raise WorkflowError("invalid_config", "library_dir must be an absolute path")
    lead = default.get("replay_lead_seconds")
    if type(lead) not in (int, float) or not 0 <= lead <= 10:
        raise WorkflowError("invalid_config", "replay_lead_seconds must be between 0 and 10")
    if default.get("language") != "en":
        raise WorkflowError("invalid_config", "Version 0.1 supports English caption tracks")
    return default


def classify_download_error(message: str) -> str:
    message = message.casefold()
    if any(s in message for s in ("429", "too many requests", "rate limit")):
        return "rate_limit"
    if any(s in message for s in ("not a bot", "sign in to confirm", "captcha")):
        return "bot_check"
    if any(s in message for s in ("private video", "video unavailable", "removed", "not available in your country")):
        return "unavailable"
    if any(s in message for s in ("sign in", "login", "age-restricted", "age restricted")):
        return "login_required"
    if any(s in message for s in ("unable to extract", "no supported javascript", "javascript runtime")):
        return "extractor"
    return "network"


def choose_track(info: dict) -> tuple[str, str, dict]:
    for source, key in (("manual", "subtitles"), ("auto", "automatic_captions")):
        available = info.get(key) or {}
        languages = [lang for lang in available if re.fullmatch(r"en(?:[-_].+)?", lang)]
        # -orig denotes the original ASR language; remaining en variants follow.
        languages.sort(key=lambda lang: (lang not in ("en", "en-orig"), lang != "en-orig", lang))
        for language in languages:
            formats = available[language]
            for extension in ("json3", "vtt", "srt"):
                track = next((f for f in formats if f.get("ext") == extension and f.get("url")), None)
                if track:
                    return language, source, track
    raise WorkflowError("no_english_captions", "No supported English caption track was found")


class QuietLogger:
    def __init__(self):
        self.warnings = []

    def debug(self, message):
        pass

    def warning(self, message):
        # Do not print signed URLs, tokens or cookies in host-facing output.
        self.warnings.append(str(message))

    def error(self, message):
        pass


def download(identifier: str) -> tuple[dict, bytes, str]:
    from yt_dlp import YoutubeDL
    from yt_dlp.networking import Request

    logger = QuietLogger()
    options = {"quiet": True, "no_warnings": True, "logger": logger, "noplaylist": True,
               "skip_download": True, "ignore_no_formats_error": True,
               "socket_timeout": 25, "retries": 0,
               "extractor_args": {"youtube": {"skip": ["translated_subs", "dash", "hls"]}}}
    url = f"https://www.youtube.com/watch?v={identifier}"
    # Three attempts total, only for rate limits or transient network errors.
    for attempt in range(3):
        try:
            with YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=False)
                if not isinstance(info, dict) or info.get("id") != identifier:
                    raise WorkflowError("extractor", "Unexpected extractor result")
                language, provenance, track = choose_track(info)
                with ydl.urlopen(Request(track["url"], headers=track.get("http_headers") or {})) as response:
                    payload = response.read(20 * 1024 * 1024 + 1)
                if len(payload) > 20 * 1024 * 1024:
                    raise WorkflowError("caption_too_large", "Caption response exceeds 20 MB")
                metadata = {"schema_version": 1, "video_id": identifier, "url": url,
                            "title": info.get("title") or identifier,
                            "channel": info.get("channel") or info.get("uploader") or "Unknown channel",
                            "duration": info.get("duration"), "upload_date": info.get("upload_date"),
                            "caption_language": language, "caption_source": provenance,
                            "caption_format": track["ext"], "tool_version": VERSION,
                            "fetched_at": datetime.now(UTC).isoformat()}
                return metadata, payload, track["ext"]
        except WorkflowError:
            raise
        except Exception as exc:
            kind = classify_download_error(str(exc))
            if attempt < 2 and kind in ("rate_limit", "network"):
                time.sleep(2 ** (attempt + 1))
                continue
            hints = {"rate_limit": "Wait before trying again; do not loop requests.",
                     "bot_check": "YouTube challenged this connection. Try later; browser cookies are not read automatically.",
                     "login_required": "This video requires authentication; automatic cookie access is disabled.",
                     "extractor": "Check yt-dlp updates and its JavaScript runtime requirements.",
                     "unavailable": "The video may be private, removed or restricted.",
                     "network": "Check network access to YouTube and try again later."}
            raise WorkflowError(kind, "YouTube caption download failed", hint=hints[kind]) from exc
    raise AssertionError("Unreachable retry state")


def load_source(run: Path) -> dict:
    source = read_json(run / "source.json")
    if not isinstance(source, dict):
        raise WorkflowError("invalid_source", "Source must be an object")
    saved_hash = source.get("source_hash")
    unhashed = {key: value for key, value in source.items() if key != "source_hash"}
    if saved_hash != digest(unhashed):
        raise WorkflowError("invalid_source", "Source changed after preparation; extract and prepare again")
    if source.get("metadata_hash") and source["metadata_hash"] != digest(read_json(run / "metadata.json")):
        raise WorkflowError("invalid_source", "Caption metadata changed; extract and prepare again")
    return source


def load_chunks(run: Path, source: dict) -> list[dict]:
    manifest = read_json(run / "prepared.json")
    if not isinstance(manifest, dict) or manifest.get("source_hash") != source["source_hash"]:
        raise WorkflowError("stale_preparation", "Prepared chunks do not match the current source")
    # Regenerate deterministic chunks instead of trusting model-editable input files.
    return make_chunks(source, manifest["chunk_words"])


def result_path(run: Path, chunk: dict) -> Path:
    return run / "chunks" / f"{chunk['chunk_id']}.result.json"


def inspect_results(run: Path, source: dict, chunks: list[dict]) -> tuple[list, list, list]:
    valid, pending, errors = [], [], []
    for chunk in chunks:
        path = result_path(run, chunk)
        if not path.exists():
            pending.append(chunk["chunk_id"])
            continue
        try:
            sentences = validate_chunk(source, chunk, read_json(path))
            valid.extend((chunk["chunk_id"], sentence) for sentence in sentences)
        except (CaptionError, WorkflowError) as exc:
            errors.append({"chunk_id": chunk["chunk_id"], "result_path": str(path), "error": str(exc)})
    return valid, pending, errors


def assemble(run: Path) -> dict:
    source = load_source(run)
    chunks = load_chunks(run, source)
    valid, pending, errors = inspect_results(run, source, chunks)
    if pending or errors:
        raise WorkflowError("incomplete", "Not all transcript chunks passed validation",
                            pending=pending, errors=errors)
    metadata = read_json(run / "metadata.json")
    identifier = video_id(metadata["video_id"])
    config = settings()
    sentences = []
    for chunk_id, sentence in valid:
        if sentence.pop("join_previous", False):
            previous = sentences[-1]
            previous["text"] = previous["text"].rstrip(".!?;:") + " " + sentence["text"]
            previous["token_end"] = sentence["token_end"]
            previous["uncertain"] = previous["uncertain"] or sentence["uncertain"]
            previous["cue_ids"] = sorted(set(previous["cue_ids"]) | {
                t["cue_id"] for t in source["tokens"][sentence["token_start"]:sentence["token_end"]]
            })
            continue
        tokens = source["tokens"][sentence["token_start"]:sentence["token_end"]]
        replay = max(0, int(tokens[0]["start"] - config["replay_lead_seconds"]))
        sentence.update({"id": len(sentences) + 1, "chunk_id": chunk_id,
                         "replay_seconds": replay,
                         "replay_url": f"https://www.youtube.com/watch?v={identifier}&t={replay}s",
                         "precision": tokens[0]["precision"],
                         "cue_ids": sorted({t["cue_id"] for t in tokens})})
        sentences.append(sentence)
    study = {"schema_version": 1, "metadata": metadata, "source_hash": source["source_hash"],
             "sentences": sentences, "word_count": len(source["tokens"]),
             "replay_lead_seconds": config["replay_lead_seconds"],
             "generated_at": datetime.now(UTC).isoformat(), "template_version": 1}
    # Exclude generation time from the render fingerprint.
    study["study_hash"] = digest({k: v for k, v in study.items() if k != "generated_at"})
    return study


def doctor(_args):
    versions, missing = {}, []
    for package in ("yt-dlp", "reportlab", "pypdf", "platformdirs"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            missing.append(package)
    if missing:
        raise WorkflowError("missing_dependencies", "Install dependencies in the skill's environment",
                            missing=missing, skill_root=str(SKILL_ROOT))
    from pdf_layout import FONT_FILES
    missing_fonts = [str(path) for path in FONT_FILES if not path.is_file()]
    if missing_fonts:
        raise WorkflowError("missing_fonts", "Skill package is incomplete", missing=missing_fonts)
    return {"status": "ready", "python": sys.version.split()[0], "dependencies": versions,
            "library_dir": settings()["library_dir"], "data_dir": str(data_dir()),
            "rendering": "Use host preview or optional PyMuPDF for visual review"}


def configure(args):
    config = settings()
    if args.library_dir:
        directory = Path(args.library_dir).expanduser().resolve()
        if directory.exists() and not directory.is_dir():
            raise WorkflowError("invalid_directory", "Library path is not a directory")
        config["library_dir"] = str(directory)
    if args.replay_lead is not None:
        if not 0 <= args.replay_lead <= 10:
            raise WorkflowError("invalid_config", "Replay lead must be between 0 and 10 seconds")
        config["replay_lead_seconds"] = args.replay_lead
    if args.library_dir or args.replay_lead is not None:
        atomic_json(data_dir() / "config.json", config)
    return {"status": "configured", "config_path": str(data_dir() / "config.json"), **config}


def extract(args):
    identifier = video_id(args.url)
    run = Path(args.run_dir).expanduser().resolve() if args.run_dir else data_dir() / "videos" / identifier
    if (run / "metadata.json").exists() and (run / "source.json").exists() and not args.refresh:
        source = load_source(run)
        metadata = read_json(run / "metadata.json")
        if metadata.get("video_id") != identifier:
            raise WorkflowError("run_conflict", "This run directory belongs to a different video")
        return {"status": "cached", "run_dir": str(run), "metadata": metadata,
                "word_count": len(source["tokens"]), "hint": "Use --refresh to refetch captions"}
    if (run / "metadata.json").exists():
        metadata = read_json(run / "metadata.json")
        if metadata.get("video_id") != identifier:
            raise WorkflowError("run_conflict", "This run directory belongs to a different video")
    if args.caption_file:
        if not args.title:
            raise WorkflowError("missing_metadata", "Offline import requires --title")
        file = Path(args.caption_file).resolve()
        extension = file.suffix.lstrip(".").lower()
        payload = file.read_bytes()
        metadata = {"schema_version": 1, "video_id": identifier,
                    "url": f"https://www.youtube.com/watch?v={identifier}",
                    "title": args.title, "channel": args.channel or "Imported captions",
                    "duration": None, "caption_source": args.caption_source,
                    "caption_language": "en", "caption_format": extension,
                    "tool_version": VERSION, "fetched_at": datetime.now(UTC).isoformat(),
                    "offline_import": True}
    else:
        metadata, payload, extension = download(identifier)
    text = payload.decode("utf-8-sig")
    if extension == "json3":
        cues = parse_json3(json.loads(text))
    elif extension in ("vtt", "srt"):
        cues = parse_timed_text(text)
    else:
        raise WorkflowError("unsupported_format", "Caption format must be JSON3, VTT or SRT")
    source = normalize(cues, metadata["caption_source"] == "auto")
    source["metadata_hash"] = digest(metadata)
    source["source_hash"] = digest({key: value for key, value in source.items() if key != "source_hash"})
    run.mkdir(parents=True, exist_ok=True)
    # Preserve raw bytes; signed track URLs and cookies are not stored.
    (run / f"raw.{extension}").write_bytes(payload)
    atomic_json(run / "metadata.json", metadata)
    atomic_json(run / "source.json", source)
    return {"status": "extracted", "run_dir": str(run), "metadata": metadata,
            "word_count": len(source["tokens"]), "deduplication_count": len(source["deduplication"])}


def prepare(args):
    run = Path(args.run_dir).resolve()
    source = load_source(run)
    chunks = make_chunks(source, args.chunk_words)
    for chunk in chunks:
        atomic_json(run / "chunks" / f"{chunk['chunk_id']}.input.json", chunk)
    atomic_json(run / "prepared.json", {"schema_version": 1, "source_hash": source["source_hash"],
                                        "chunk_words": args.chunk_words, "chunk_count": len(chunks)})
    _, pending, errors = inspect_results(run, source, chunks)
    return {"status": "prepared", "run_dir": str(run), "chunk_count": len(chunks),
            "pending": pending, "errors": errors,
            "editing_rules": str(SKILL_ROOT / "references" / "editing-rules.md"),
            "data_contract": str(SKILL_ROOT / "references" / "data-contract.md")}


def next_chunk(args):
    run = Path(args.run_dir).resolve()
    source = load_source(run)
    chunks = load_chunks(run, source)
    _, pending, errors = inspect_results(run, source, chunks)
    unfinished = {e["chunk_id"] for e in errors} | set(pending)
    chunk = next((c for c in chunks if c["chunk_id"] in unfinished), None)
    if not chunk:
        return {"status": "all_chunks_valid", "next": "validate"}
    # Reconstruct input if a previous host edited it accidentally.
    input_path = run / "chunks" / f"{chunk['chunk_id']}.input.json"
    atomic_json(input_path, chunk)
    return {"status": "needs_editing", "input_path": str(input_path),
            "result_path": str(result_path(run, chunk)),
            "chunk_id": chunk["chunk_id"], "chunk_hash": chunk["chunk_hash"],
            "error": next((e["error"] for e in errors if e["chunk_id"] == chunk["chunk_id"]), None)}


def validate(args):
    run = Path(args.run_dir).resolve()
    if args.chunk:
        source = load_source(run)
        chunk = next((c for c in load_chunks(run, source) if c["chunk_id"] == args.chunk), None)
        if chunk is None:
            raise WorkflowError("invalid_chunk", "Unknown chunk ID")
        sentences = validate_chunk(source, chunk, read_json(result_path(run, chunk)))
        return {"status": "chunk_valid", "chunk_id": args.chunk, "sentence_count": len(sentences)}
    study = assemble(run)
    atomic_json(run / "study.json", study)
    return {"status": "validated", "study_path": str(run / "study.json"),
            "word_count": study["word_count"], "sentence_count": len(study["sentences"]),
            "uncertain_sentences": [s["id"] for s in study["sentences"] if s["uncertain"]]}


def render(args):
    from pdf_layout import publish_pdf
    run = Path(args.run_dir).resolve()
    study = assemble(run)  # Never rely on a stale 'validated' flag or model-written study.json.
    atomic_json(run / "study.json", study)
    library = Path(args.library_dir).expanduser().resolve() if args.library_dir else Path(settings()["library_dir"])
    result = publish_pdf(study, run, library)
    return {"status": "rendered", **result,
            "uncertain_sentences": [s["id"] for s in study["sentences"] if s["uncertain"]]}


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor").set_defaults(func=doctor)
    config = sub.add_parser("configure")
    config.add_argument("--library-dir")
    config.add_argument("--replay-lead", type=float)
    config.set_defaults(func=configure)
    fetch = sub.add_parser("extract")
    fetch.add_argument("--url", required=True)
    fetch.add_argument("--run-dir")
    fetch.add_argument("--refresh", action="store_true")
    fetch.add_argument("--caption-file", help="Offline import of an existing caption file")
    fetch.add_argument("--caption-source", choices=("manual", "auto"), default="manual")
    fetch.add_argument("--title")
    fetch.add_argument("--channel")
    fetch.set_defaults(func=extract)
    prep = sub.add_parser("prepare")
    prep.add_argument("--run-dir", required=True)
    prep.add_argument("--chunk-words", type=int, default=1000)
    prep.set_defaults(func=prepare)
    next_p = sub.add_parser("next")
    next_p.add_argument("--run-dir", required=True)
    next_p.set_defaults(func=next_chunk)
    check = sub.add_parser("validate")
    check.add_argument("--run-dir", required=True)
    check.add_argument("--chunk")
    check.set_defaults(func=validate)
    pdf = sub.add_parser("render")
    pdf.add_argument("--run-dir", required=True)
    pdf.add_argument("--library-dir")
    pdf.set_defaults(func=render)
    return p


def main():
    # JSON/text files remain UTF-8 even in a Windows console with another code page.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    args = parser().parse_args()
    try:
        result = args.func(args)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0
    except WorkflowError as exc:
        error = {"status": "error", "kind": exc.kind, "message": str(exc), **exc.details}
    except CaptionError as exc:
        error = {"status": "error", "kind": "integrity", "message": str(exc)}
    except ModuleNotFoundError as exc:
        error = {"status": "error", "kind": "missing_dependencies", "message": str(exc)}
    except (OSError, UnicodeError, ValueError, KeyError, TypeError) as exc:
        error = {"status": "error", "kind": "local_error", "message": str(exc)}
    print(json.dumps(error, ensure_ascii=False), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
