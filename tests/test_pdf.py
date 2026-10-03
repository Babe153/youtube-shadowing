from pathlib import Path

import pytest
from pdf_layout import generate_pdf, publish_pdf, safe_name, verify_pdf
from pypdf import PdfReader
from subtitles import CaptionError, digest


def study(count=12, text="We can practice together, one sentence at a time."):
    value = {"metadata": {"title": "影子跟读 Practice <&>", "channel": "学习频道",
                           "duration": 100, "caption_source": "manual",
                           "video_id": "BaW_jenozKc", "url": "https://www.youtube.com/watch?v=BaW_jenozKc"},
             "generated_at": "2026-10-03T00:00:00+00:00",
             "sentences": [{"id": i + 1, "text": text, "uncertain": False,
                            "replay_url": f"https://www.youtube.com/watch?v=BaW_jenozKc&t={i}s"}
                           for i in range(count)]}
    value["study_hash"] = digest(value)
    return value


def test_pdf_cjk_text_pagination_and_replay_annotations(tmp_path):
    value = study(75)
    path = tmp_path / "handout.pdf"
    generate_pdf(value, path)
    check = verify_pdf(path, value)
    assert check["page_count"] > 1
    assert check["link_count"] == 76
    reader = PdfReader(path)
    assert "影子跟读" in reader.pages[0].extract_text()
    assert "<&>" in reader.pages[0].extract_text()
    assert "75" in reader.pages[-1].extract_text()


def test_very_long_sentence_can_split_across_pages(tmp_path):
    value = study(1, "We can practice together " * 250 + "today.")
    path = tmp_path / "long.pdf"
    generate_pdf(value, path)
    assert verify_pdf(path, value)["page_count"] > 1


def test_verification_counts_repeated_sentences_instead_of_reusing_one_occurrence(tmp_path):
    path = tmp_path / "incomplete.pdf"
    generate_pdf(study(1), path)
    with pytest.raises(CaptionError, match="missing text"):
        verify_pdf(path, study(2))


def test_unknown_file_and_user_modified_generated_file_are_preserved(tmp_path):
    value = study(2)
    run, library = tmp_path / "run", tmp_path / "library"
    run.mkdir()
    output = library / "学习频道" / "影子跟读 Practice <&> [BaW_jenozKc].pdf"
    # Use the actual sanitized name to force a collision.
    output = output.with_name(f"{safe_name(value['metadata']['title'])} [BaW_jenozKc].pdf")
    output.parent.mkdir(parents=True)
    output.write_bytes(b"User-owned file")
    result = publish_pdf(value, run, library)
    assert output.read_bytes() == b"User-owned file"
    assert result["pdf_path"].endswith("(2).pdf")
    assert publish_pdf(value, run, library)["reused"] is True
    modified = Path(result["pdf_path"])
    modified.write_bytes(b"User edits")
    again = publish_pdf(value, run, library)
    assert modified.read_bytes() == b"User edits"
    assert again["pdf_path"].endswith("(3).pdf")


@pytest.mark.parametrize("name", ["CON", "NUL.txt", "COM1", "LPT9.pdf"])
def test_windows_reserved_names(name):
    assert safe_name(name).startswith("_")


def test_unsupported_glyph_is_reported_instead_of_silent_tofu(tmp_path):
    with pytest.raises(CaptionError, match="font"):
        generate_pdf(study(1, "Missing glyph 🦄"), tmp_path / "missing.pdf")
