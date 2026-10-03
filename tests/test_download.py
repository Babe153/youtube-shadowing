
import pytest
from shadowing import WorkflowError, download


def test_extraction_skips_translation_and_does_not_download_video(monkeypatch):
    import yt_dlp

    recorded = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def read(self, limit):
            assert limit == 20 * 1024 * 1024 + 1
            return b'{"events": []}'

    class FakeYDL:
        def __init__(self, options):
            recorded.update(options)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def extract_info(self, url, download):
            assert url == "https://www.youtube.com/watch?v=BaW_jenozKc"
            assert download is False
            return {"id": "BaW_jenozKc", "title": "Practice", "channel": "Demo",
                    "subtitles": {"en": [{"ext": "json3", "url": "https://www.youtube.com/captions"}]}}

        def urlopen(self, request):
            assert "captions" in request.url
            return Response()

    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)
    metadata, payload, extension = download("BaW_jenozKc")
    assert metadata["caption_source"] == "manual"
    assert extension == "json3"
    assert payload
    assert recorded["skip_download"] and recorded["noplaylist"]
    assert "translated_subs" in recorded["extractor_args"]["youtube"]["skip"]
    assert "cookiesfrombrowser" not in recorded


def test_bot_check_is_not_retried_or_reported_as_missing_subtitles(monkeypatch):
    import yt_dlp

    calls = []

    class BlockedYDL:
        def __init__(self, _options):
            calls.append(1)

        def __enter__(self):
            raise RuntimeError("Sign in to confirm you're not a bot")

        def __exit__(self, *_args):
            pass

    monkeypatch.setattr(yt_dlp, "YoutubeDL", BlockedYDL)
    with pytest.raises(WorkflowError) as error:
        download("BaW_jenozKc")
    assert error.value.kind == "bot_check"
    assert len(calls) == 1
