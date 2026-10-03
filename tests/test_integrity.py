import copy
from itertools import pairwise

import pytest
from subtitles import CaptionError, Cue, make_chunks, normalize, validate_chunk


def prepared(text="I can't pay $25 because it's 1,000 dollars"):
    source = normalize([Cue(0, 0, 5, text)], False)
    chunk = make_chunks(source)[0]
    return source, chunk


def result(chunk, *sentences):
    return {"schema_version": 1, "chunk_id": chunk["chunk_id"], "chunk_hash": chunk["chunk_hash"],
            "sentences": [{"text": s} for s in sentences]}


def test_punctuation_and_case_allowed_ranges_inferred():
    source, chunk = prepared()
    sentences = validate_chunk(source, chunk, result(chunk, "I can’t pay $25, because it's 1,000 dollars."))
    assert sentences[0]["token_start"] == 0
    assert sentences[0]["token_end"] == len(source["tokens"])


@pytest.mark.parametrize("text", [
    "I can pay $25 because it's 1,000 dollars.",
    "I can't pay 25 because it's 1,000 dollars.",
    "I can't pay $25 because it's 1.000 dollars.",
    "I cannot pay $25 because it's 1,000 dollars.",
    "I can't pay $25.",
    "I can't pay $25 because it's 1,000 dollars I can't pay $25.",
])
def test_changed_dropped_added_words_rejected(text):
    source, chunk = prepared()
    with pytest.raises(CaptionError):
        validate_chunk(source, chunk, result(chunk, text))


def test_repetition_is_part_of_fidelity_contract():
    source, chunk = prepared("it is very very important")
    with pytest.raises(CaptionError):
        validate_chunk(source, chunk, result(chunk, "It is very important."))


def test_stale_input_hash_and_incorrect_supplied_offsets_rejected():
    source, chunk = prepared()
    edited = result(chunk, "I can't pay $25 because it's 1,000 dollars.")
    stale = copy.deepcopy(edited)
    stale["chunk_hash"] = "old"
    with pytest.raises(CaptionError, match="stale"):
        validate_chunk(source, chunk, stale)
    edited["sentences"][0]["token_start"] = 1
    with pytest.raises(CaptionError, match="token_start"):
        validate_chunk(source, chunk, edited)


def test_chunks_cover_long_source_once_with_context_excluded():
    source = normalize([Cue(i, i, i + 1, "word " * 37) for i in range(140)], False)
    chunks = make_chunks(source, 1000)
    assert chunks[0]["token_start"] == 0
    assert chunks[-1]["token_end"] == len(source["tokens"])
    for first, second in pairwise(chunks):
        assert first["token_end"] == second["token_start"]
        assert second["context_before"]
    for chunk in chunks:
        validate_chunk(source, chunk, result(chunk, chunk["text"] + "."))


@pytest.mark.parametrize("original,changed", [("research & development", "research development"),
                                           ("C++ and C#", "C and C"),
                                           ("A/B testing", "A B testing")])
def test_meaningful_symbols_cannot_be_deleted(original, changed):
    source, chunk = prepared(original)
    with pytest.raises(CaptionError):
        validate_chunk(source, chunk, result(chunk, changed))


def test_prepare_keeps_existing_caption_punctuation():
    _, chunk = prepared("Yes, I can. Can you?")
    assert chunk["text"] == "Yes, I can. Can you?"
