"""Caption parsing and fidelity checks. No network access or model calls."""
from __future__ import annotations

import hashlib
import html
import json
import math
import re
from dataclasses import dataclass, field

# Keep contractions, quantities and compound words intact. Punctuation edits must not
# silently turn 1,000 into 1.000, can't into can, or $25 into 25.
WORD = re.compile(
    r"[$£€¥]?[+-]?\d+(?:[.,:/]\d+)*(?:%|[A-Za-z]+)?"
    r"|[^\W\d_]+(?:['’\-&/][^\W\d_]+)*(?:\+\+|#)?"
    r"|[&=<>+%$£€¥]",
    re.UNICODE,
)
TAG = re.compile(r"<[^>]*>")
TIMING = re.compile(
    r"(?P<start>(?:\d+:)?\d{2}:\d{2}[.,]\d{3})\s+-->\s+"
    r"(?P<end>(?:\d+:)?\d{2}:\d{2}[.,]\d{3})"
)


class CaptionError(ValueError):
    pass


def canonical(word: str) -> str:
    return word.replace("’", "'").casefold()


def words(text: str) -> list[str]:
    return WORD.findall(text)


def word_keys(text: str) -> list[str]:
    return [canonical(w) for w in words(text)]


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def clean_text(text: str) -> str:
    # Strip presentation markup before unescaping: spoken '<script>' remains text.
    return re.sub(r"\s+", " ", html.unescape(TAG.sub("", text))).strip()


def sound_only(text: str) -> bool:
    return bool(re.fullmatch(r"[\[(](?:music|applause|laughter)[\])]", text, re.IGNORECASE))


def finite_number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CaptionError(f"{label} must be a finite number")
    if not math.isfinite(value):
        raise CaptionError(f"{label} must be a finite number")
    return float(value)


@dataclass
class Cue:
    id: int
    start: float
    end: float
    text: str
    window: int | None = None
    append: bool = False
    precision: str = "cue"
    word_times: list[float] = field(default_factory=list)


def parse_json3(data: dict) -> list[Cue]:
    if not isinstance(data, dict) or not isinstance(data.get("events"), list):
        raise CaptionError("JSON3 must contain an events array")
    cues = []
    for event in data["events"]:
        if not isinstance(event, dict):
            raise CaptionError("Invalid JSON3 event")
        segs = event.get("segs")
        if not segs:  # Window definitions and clear events do not contain spoken words.
            continue
        if not isinstance(segs, list) or not all(isinstance(s, dict) for s in segs):
            raise CaptionError("Invalid JSON3 segments")
        start = finite_number(event.get("tStartMs", 0), "tStartMs") / 1000
        duration = finite_number(event.get("dDurationMs", 0), "dDurationMs") / 1000
        if start < 0 or duration < 0:
            raise CaptionError("Negative caption timing")
        raw = "".join(str(s.get("utf8", "")) for s in segs)
        text = clean_text(raw)
        if not words(text) or sound_only(text):
            continue
        # Offsets usually describe individual ASR words. Only claim word precision
        # when every non-whitespace segment actually supplies an offset.
        timed = all("tOffsetMs" in s for s in segs if words(clean_text(str(s.get("utf8", "")))))
        times = []
        for seg in segs:
            offset = finite_number(seg.get("tOffsetMs", 0), "tOffsetMs") / 1000
            if offset < 0:
                raise CaptionError("Negative word timing")
            times.extend([start + offset] * len(words(clean_text(str(seg.get("utf8", ""))))))
        if len(times) != len(words(text)):
            timed = False
            times = [start] * len(words(text))
        window = event.get("wWinId")
        if window is not None and (not isinstance(window, int) or isinstance(window, bool)):
            raise CaptionError("Invalid caption window ID")
        cues.append(Cue(len(cues), start, start + duration, text, window,
                        bool(event.get("aAppend")), "word" if timed else "cue", times))
    return cues


def timestamp(value: str) -> float:
    parts = value.replace(",", ".").split(":")
    if len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])


def parse_timed_text(text: str) -> list[Cue]:
    """Read VTT or SRT; ignore headers, NOTE, STYLE and REGION blocks."""
    cues = []
    text = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    for block in re.split(r"\n\s*\n", text):
        lines = block.splitlines()
        if not lines or lines[0].startswith(("NOTE", "STYLE", "REGION")):
            continue
        for index, line in enumerate(lines):
            match = TIMING.search(line)
            if not match:
                continue
            start, end = timestamp(match["start"]), timestamp(match["end"])
            if end < start:
                raise CaptionError("Caption ends before it starts")
            # Inline VTT word timestamps also demonstrate rolling presentation.
            raw = " ".join(lines[index + 1:])
            cleaned = clean_text(raw)
            if words(cleaned) and not sound_only(cleaned):
                cues.append(Cue(len(cues), start, end, cleaned))
            break
    return cues


def overlap_size(previous: list[str], current: list[str]) -> int:
    for count in range(min(len(previous), len(current)), 0, -1):
        if previous[-count:] == current[:count]:
            return count
    return 0


def normalize(cues: list[Cue], automatic: bool) -> dict:
    """Remove only evidenced display overlap; never deduplicate the whole transcript."""
    tokens, pieces, removals = [], [], []
    prior: Cue | None = None
    previous_words: list[str] = []
    for cue in cues:
        matches = list(WORD.finditer(cue.text))
        keys = [canonical(m[0]) for m in matches]
        remove = 0
        if automatic and prior is not None and cue.start >= prior.start:
            same_window = cue.window is not None and cue.window == prior.window
            time_overlap = cue.start < prior.end - 0.02
            size = overlap_size(previous_words, keys)
            # JSON3 aAppend events contain new speech. A refresh of the same
            # window, or overlapping VTT cues, can retain the previous display.
            rolling = not cue.append and (same_window or (cue.window is None and time_overlap))
            # A single shared word is too ambiguous ('very, very'). Require a
            # phrase, and require timing overlap even with a known window.
            if rolling and time_overlap and size >= 3:
                extends_display = size < len(keys)
                identical_refresh = abs(cue.start - prior.start) < .02
                time_evidence = True
                if cue.precision == prior.precision == "word" and cue.word_times and prior.word_times:
                    time_evidence = all(abs(old - new) < .15 for old, new in zip(
                        prior.word_times[-size:], cue.word_times[:size], strict=True
                    ))
                if (extends_display or identical_refresh) and time_evidence:
                    remove = size
        if remove:
            removals.append({"cue_id": cue.id, "removed_words": remove,
                             "reason": "overlapping_display_phrase"})
        if remove < len(matches):
            beginning = matches[remove].start()
            # Preserve punctuation before the first word for non-overlap cues.
            text = cue.text if remove == 0 else cue.text[beginning:]
            pieces.append(text)
            for local_index in range(remove, len(matches)):
                at = cue.word_times[local_index] if cue.word_times else cue.start
                tokens.append({"id": len(tokens), "word": matches[local_index][0],
                               "start": at, "cue_id": cue.id, "precision": cue.precision})
        previous_words = keys
        prior = cue
    if not tokens:
        raise CaptionError("Caption file contains no spoken words")
    source = {"schema_version": 1, "tokens": tokens, "text": " ".join(pieces),
              "cues": [{"id": c.id, "start": c.start, "end": c.end, "text": c.text}
                       for c in cues], "deduplication": removals}
    source["source_hash"] = digest(source)
    return source


def make_chunks(source: dict, target: int = 1000) -> list[dict]:
    if not 50 <= target <= 2000:
        raise CaptionError("Chunk size must be between 50 and 2000 words")
    tokens = source["tokens"]
    offsets = list(WORD.finditer(source["text"]))
    if len(offsets) != len(tokens):
        raise CaptionError("Source text and timed tokens are inconsistent")
    chunks, beginning = [], 0
    cue_ends = {i for i in range(1, len(tokens))
                if tokens[i]["cue_id"] != tokens[i - 1]["cue_id"]}
    while beginning < len(tokens):
        end = min(beginning + target, len(tokens))
        if end < len(tokens):
            boundaries = [i for i in cue_ends if beginning + int(target * .8) <= i <= end]
            if boundaries:
                end = max(boundaries)
        chunk = {"schema_version": 1, "chunk_id": f"chunk-{len(chunks) + 1:04d}",
                 "source_hash": source["source_hash"], "token_start": beginning,
                 "token_end": end,
                 "text": source["text"][0 if beginning == 0 else offsets[beginning].start():
                                        offsets[end].start() if end < len(offsets) else len(source["text"])].strip(),
                 "context_before": " ".join(t["word"] for t in tokens[max(0, beginning - 30):beginning]),
                 "context_after": " ".join(t["word"] for t in tokens[end:end + 30])}
        chunk["chunk_hash"] = digest(chunk)
        chunks.append(chunk)
        beginning = end
    return chunks


def validate_chunk(source: dict, chunk: dict, result: object) -> list[dict]:
    if not isinstance(result, dict):
        raise CaptionError("Result must be a JSON object")
    if result.get("schema_version") != 1 or result.get("chunk_id") != chunk["chunk_id"]:
        raise CaptionError("Result schema or chunk ID does not match input")
    if result.get("chunk_hash") != chunk["chunk_hash"]:
        raise CaptionError("Result has a missing or stale chunk_hash; use the current input")
    sentences = result.get("sentences")
    if not isinstance(sentences, list) or not sentences:
        raise CaptionError("Result must contain a nonempty sentences array")
    expected = source["tokens"]
    cursor = chunk["token_start"]
    validated = []
    for i, sentence in enumerate(sentences):
        if not isinstance(sentence, dict) or not isinstance(sentence.get("text"), str):
            raise CaptionError(f"Sentence {i + 1} requires text")
        text = sentence["text"].strip()
        if not text or "\n" in text or len(text) > 12000:
            raise CaptionError(f"Sentence {i + 1} must be one nonempty paragraph")
        # Reject invisible/control characters instead of letting them hide changes.
        if any(ord(c) < 32 or c in "\u200b\u202e\u202d\u2066\u2067\u2068\u2069" for c in text):
            raise CaptionError(f"Sentence {i + 1} contains unsupported control characters")
        actual = word_keys(text)
        end = cursor + len(actual)
        if not actual or end > chunk["token_end"]:
            raise CaptionError(f"Sentence {i + 1} exceeds the chunk or contains no words")
        target = [canonical(t["word"]) for t in expected[cursor:end]]
        if actual != target:
            mismatch = next(n for n, (a, b) in enumerate(zip(actual, target)) if a != b)
            raise CaptionError(
                f"Sentence {i + 1} changes source token {cursor + mismatch}: "
                f"expected {target[mismatch]!r}, got {actual[mismatch]!r}. "
                "Preserve all words, contractions, quantities and repetitions."
            )
        uncertain = sentence.get("uncertain", False)
        if not isinstance(uncertain, bool):
            raise CaptionError(f"Sentence {i + 1}: uncertain must be a boolean")
        join_previous = sentence.get("join_previous", False)
        if not isinstance(join_previous, bool) or (join_previous and (i != 0 or cursor == 0)):
            raise CaptionError("join_previous is only allowed on the first sentence of a later chunk")
        for key, value in (("token_start", cursor), ("token_end", end)):
            if key in sentence and (type(sentence[key]) is not int or sentence[key] != value):
                raise CaptionError(f"Sentence {i + 1} has incorrect {key}")
        validated.append({"text": text, "token_start": cursor, "token_end": end,
                          "uncertain": uncertain, "join_previous": join_previous})
        cursor = end
    if cursor != chunk["token_end"]:
        raise CaptionError(f"Result omits {chunk['token_end'] - cursor} source words at the end")
    return validated
