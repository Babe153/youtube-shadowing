"""Searchable PDF handouts, source links and collision-safe publication."""
from __future__ import annotations

import hashlib
import html
import os
import re
import tempfile
import threading
from collections import Counter
from datetime import datetime
from pathlib import Path

from pypdf import PdfReader
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
from subtitles import CaptionError, word_keys

ROOT = Path(__file__).resolve().parents[1]
FONT_FILES = [ROOT / "assets/fonts/NotoSansSC-Regular.ttf"]
FONT_NAME = "ShadowingNoto"
FONT_LOCK = threading.Lock()
INK = colors.HexColor("#1C2733")
MUTED = colors.HexColor("#627080")
ACCENT = colors.HexColor("#217A72")


def safe_name(value: str, limit: int = 80) -> str:
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    text = re.sub(r"\s+", " ", text)
    if not text:
        text = "Untitled"
    if re.fullmatch(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", text, re.IGNORECASE):
        text = "_" + text
    # Windows counts UTF-16 code units; supplementary symbols consume two.
    while len(text.encode("utf-16-le")) // 2 > limit:
        text = text[:-1]
    return text.rstrip(" .")


def register_font():
    with FONT_LOCK:
        if FONT_NAME not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(FONT_NAME, str(FONT_FILES[0])))


def ensure_glyphs(text: str):
    font = pdfmetrics.getFont(FONT_NAME)
    unsupported = sorted({ord(c) for c in text if not c.isspace() and ord(c) not in font.face.charWidths})
    if unsupported:
        codes = ", ".join(f"U+{code:04X}" for code in unsupported[:8])
        raise CaptionError(f"Bundled font does not support {codes}; choose supported text or extend fonts")


def display_metadata(text: str) -> str:
    """Keep unsupported title symbols visible as code points, never blank squares."""
    font = pdfmetrics.getFont(FONT_NAME)
    return "".join(c if c.isspace() or ord(c) in font.face.charWidths else f"[U+{ord(c):04X}]"
                   for c in text)


def format_duration(seconds) -> str:
    if not isinstance(seconds, (int, float)) or seconds < 0:
        return "Duration unavailable"
    minutes, seconds = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"


def generate_pdf(study: dict, destination: Path):
    register_font()
    metadata = study["metadata"]
    title = display_metadata(str(metadata["title"]))
    channel = display_metadata(str(metadata["channel"]))
    for text in (s["text"] for s in study["sentences"]):
        ensure_glyphs(text)
    destination.parent.mkdir(parents=True, exist_ok=True)
    page_width, page_height = A4
    styles = {
        "title": ParagraphStyle("title", fontName=FONT_NAME, fontSize=22, leading=30,
                                textColor=INK, spaceAfter=10, wordWrap="CJK"),
        "meta": ParagraphStyle("meta", fontName=FONT_NAME, fontSize=10, leading=16,
                               textColor=MUTED, spaceAfter=6, wordWrap="CJK"),
        "source": ParagraphStyle("source", fontName=FONT_NAME, fontSize=9, leading=14,
                                 textColor=ACCENT, spaceAfter=8, splitLongWords=True),
        "sentence": ParagraphStyle("sentence", fontName=FONT_NAME, fontSize=14, leading=23,
                                   textColor=INK, leftIndent=13 * mm, firstLineIndent=-13 * mm,
                                   spaceBefore=4, spaceAfter=15, alignment=TA_LEFT,
                                   allowWidows=0, allowOrphans=0),
    }
    url = html.escape(metadata["url"], quote=True)
    caption_type = "Human captions" if metadata["caption_source"] == "manual" else "Automatic captions"
    if metadata.get("demo"):
        caption_type = "DEMO - original practice text, not captions of the linked video"
    generated = datetime.fromisoformat(study["generated_at"]).astimezone().strftime("%Y-%m-%d")
    story = [Paragraph(html.escape(title), styles["title"]),
             Paragraph(f"{html.escape(channel)} | {format_duration(metadata.get('duration'))}"
                       f" | {caption_type}", styles["meta"]),
             Paragraph(f"Created {generated} | {len(study['sentences'])} practice sentences",
                       styles["meta"]),
             Paragraph(f'<a href="{url}">WATCH ORIGINAL VIDEO</a><br/>{url}', styles["source"]),
             Paragraph("Click a sentence number to replay near its beginning. Caption timing may be approximate.",
                       styles["meta"])]
    if any(s["uncertain"] for s in study["sentences"]):
        story.append(Paragraph("A question mark after a sentence indicates uncertain caption wording.", styles["meta"]))
    story.append(Spacer(1, 7 * mm))
    for sentence in study["sentences"]:
        number = f"{sentence['id']:02d}"
        link = html.escape(sentence["replay_url"], quote=True)
        text = html.escape(sentence["text"])
        uncertainty = ' <super><font color="#627080">?</font></super>' if sentence["uncertain"] else ""
        story.append(Paragraph(
            f'<a href="{link}" color="#217A72">{number}</a>&nbsp;&nbsp;&nbsp;{text}{uncertainty}',
            styles["sentence"]))

    def footer(canvas, _doc):
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor("#DCE4E7"))
        canvas.line(19 * mm, 17 * mm, page_width - 19 * mm, 17 * mm)
        canvas.setFillColor(MUTED)
        canvas.setFont(FONT_NAME, 8)
        canvas.drawString(19 * mm, 12 * mm, "YOUTUBE SHADOWING")
        canvas.drawRightString(page_width - 19 * mm, 12 * mm, str(canvas.getPageNumber()))
        canvas.restoreState()

    document = SimpleDocTemplate(str(destination), pagesize=(page_width, page_height),
                                 leftMargin=19 * mm, rightMargin=19 * mm,
                                 topMargin=19 * mm, bottomMargin=24 * mm,
                                 title=title, author="YouTube Shadowing", pageCompression=1)
    document.build(story, onFirstPage=footer, onLaterPages=footer)


def verify_pdf(path: Path, study: dict) -> dict:
    reader = PdfReader(path)
    if not reader.pages:
        raise CaptionError("Generated PDF has no pages")
    text = "\n".join(re.sub(r"(?m)^YOUTUBE SHADOWING\s*\n\d+\s*\n", "", page.extract_text() or "")
                     for page in reader.pages)
    if "\ufffd" in text:
        raise CaptionError("Generated PDF contains replacement glyphs")
    # Validate sentence words independent of headers/footers. Numbered paragraphs
    # are extracted with whitespace/newlines between words, so compare word lists.
    keys = word_keys(text)
    cursor = 0
    for sentence in study["sentences"]:
        expected = word_keys(sentence["text"])
        position = next((i for i in range(cursor, len(keys) - len(expected) + 1)
                         if keys[i:i + len(expected)] == expected), None)
        if position is None:
            raise CaptionError(f"PDF is missing text from sentence {sentence['id']}")
        cursor = position + len(expected)
    links = []
    for page in reader.pages:
        for annotation in page.get("/Annots", []):
            action = annotation.get_object().get("/A") or {}
            uri = action.get("/URI")
            if uri:
                links.append(str(uri))
    expected_links = [study["metadata"]["url"], *(s["replay_url"] for s in study["sentences"])]
    actual_counts = Counter(links)
    for link, count in Counter(expected_links).items():
        if actual_counts[link] < count:
            raise CaptionError("Generated PDF is missing a source/replay link")
    return {"page_count": len(reader.pages), "link_count": len(links),
            "sentence_count": len(study["sentences"]), "text_and_links_verified": True,
            "visual_review": "pending"}


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def publish_pdf(study: dict, run: Path, library: Path) -> dict:
    """Never overwrite an unknown file; ownership is a receipt plus a byte hash."""
    from shadowing import atomic_json, read_json
    base = library / safe_name(study["metadata"]["channel"], 50)
    base.mkdir(parents=True, exist_ok=True)
    name = f"{safe_name(study['metadata']['title'])} [{study['metadata']['video_id']}]"
    destination = base / f"{name}.pdf"
    if os.name == "nt" and len(str(destination)) > 240:
        available = 235 - len(str(base)) - len(study["metadata"]["video_id"]) - 8
        if available < 8:
            raise CaptionError("Library directory is too long for portable Windows filenames")
        name = f"{safe_name(study['metadata']['title'], available)} [{study['metadata']['video_id']}]"
        destination = base / f"{name}.pdf"
    receipt_path = run / "published.json"
    receipt = read_json(receipt_path) if receipt_path.exists() else {}
    owned = False
    if receipt.get("pdf_path") and Path(receipt["pdf_path"]).parent == base:
        recorded = Path(receipt["pdf_path"])
        if recorded.is_file() and file_hash(recorded) == receipt.get("pdf_hash"):
            destination, owned = recorded, True
            if receipt.get("study_hash") == study["study_hash"]:
                return {"pdf_path": str(destination), "reused": True, **receipt.get("verification", {})}
    temporary = run / "handout.pending.pdf"
    generate_pdf(study, temporary)
    verification = verify_pdf(temporary, study)
    # Exclusive create protects unknown files even if another process creates a
    # similarly named handout between the existence check and publication.
    if owned:
        handle, staging = tempfile.mkstemp(prefix=".shadowing-", suffix=".pdf", dir=base)
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(temporary.read_bytes())
            if file_hash(destination) != receipt.get("pdf_hash"):
                owned = False
            else:
                os.replace(staging, destination)
        finally:
            if os.path.exists(staging):
                os.unlink(staging)
    if not owned:
        for version in range(1, 10000):
            candidate = destination if version == 1 else base / f"{name} ({version}).pdf"
            created = False
            try:
                with candidate.open("xb") as stream:
                    created = True
                    stream.write(temporary.read_bytes())
                destination = candidate
                break
            except FileExistsError:
                continue
            except OSError:
                # Only delete the partial file this invocation exclusively created.
                if created and candidate.exists():
                    candidate.unlink()
                raise
        else:
            raise CaptionError("Unable to allocate a unique output filename")
    atomic_json(receipt_path, {"pdf_path": str(destination), "pdf_hash": file_hash(destination),
                               "study_hash": study["study_hash"], "verification": verification})
    temporary.unlink(missing_ok=True)
    return {"pdf_path": str(destination), "reused": False, **verification}
