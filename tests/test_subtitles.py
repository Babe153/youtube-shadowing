import pytest
from conftest import ROOT
from subtitles import CaptionError, Cue, normalize, parse_json3, parse_timed_text


def test_rolling_vtt_removes_display_overlap_but_keeps_real_repetitions():
    cues = parse_timed_text((ROOT / "tests/fixtures/rolling.vtt").read_text())
    source = normalize(cues, automatic=True)
    assert source["text"] == (
        "I think we should start with a simple example very very important very very important"
    )
    assert [t["id"] for t in source["tokens"]] == list(range(15))
    assert source["tokens"][4]["start"] == 2
    assert len(source["deduplication"]) == 1


def test_manual_captions_never_use_display_deduplication():
    source = normalize([Cue(0, 0, 4, "we can do this"), Cue(1, 2, 6, "we can do this")], False)
    assert len(source["tokens"]) == 8


def test_one_shared_word_is_preserved_even_when_automatic_cues_overlap():
    source = normalize([Cue(0, 0, 4, "this is very"), Cue(1, 2, 5, "very important")], True)
    assert source["text"] == "this is very very important"


def test_ambiguous_repeated_phrase_is_preserved_even_with_overlapping_cues():
    source = normalize([Cue(0, 0, 4, "we can do this"), Cue(1, 2, 6, "we can do this")], True)
    assert len(source["tokens"]) == 8


def test_distinct_word_times_preserve_real_repeated_speech():
    source = normalize([
        Cue(0, 0, 5, "we should try", window=1, precision="word", word_times=[0, .5, 1]),
        Cue(1, 2, 6, "we should try again", window=1, precision="word", word_times=[2, 2.5, 3, 3.5]),
    ], True)
    assert source["text"] == "we should try we should try again"


def test_json3_append_is_new_speech_not_a_refresh():
    data = {"events": [
        {"tStartMs": 1000, "dDurationMs": 5000, "wWinId": 1,
         "segs": [{"utf8": "we should try ", "tOffsetMs": 0}]},
        {"tStartMs": 2000, "dDurationMs": 2000, "wWinId": 1, "aAppend": 1,
         "segs": [{"utf8": "we should try again", "tOffsetMs": 100}]},
    ]}
    source = normalize(parse_json3(data), True)
    assert source["text"] == "we should try we should try again"
    assert source["tokens"][3]["start"] == 2.1
    assert source["tokens"][3]["precision"] == "word"


def test_json3_window_refresh_removes_evidenced_phrase():
    data = {"events": [
        {"tStartMs": 0, "dDurationMs": 5000, "wWinId": 1, "segs": [{"utf8": "we should try"}]},
        {"tStartMs": 1000, "dDurationMs": 5000, "wWinId": 1,
         "segs": [{"utf8": "we should try again"}]},
    ]}
    source = normalize(parse_json3(data), True)
    assert source["text"] == "we should try again"
    assert source["tokens"][-1]["precision"] == "cue"


def test_vtt_and_srt_markup_headers_and_sound_cues():
    cues = parse_timed_text(
        "WEBVTT\n\nNOTE ignore this\n00:00:00.000 --> 00:00:01.000\nnot speech\n\n"
        "1\n00:00:01,000 --> 00:00:02,000\n<i>I'm</i> &amp; <00:00:01.500>ready\n\n"
        "2\n00:00:02,000 --> 00:00:03,000\n[Music]\n"
    )
    assert len(cues) == 1
    assert cues[0].text == "I'm & ready"


@pytest.mark.parametrize("data", [[], {"events": []}, {"events": [{"tStartMs": -1, "segs": [{"utf8": "no"}]}]}])
def test_empty_and_malformed_caption_data(data):
    with pytest.raises(CaptionError):
        normalize(parse_json3(data), True)
