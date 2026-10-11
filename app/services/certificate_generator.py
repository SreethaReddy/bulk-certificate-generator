"""Renders ONE certificate PDF from the single predefined template.

This module knows nothing about HTTP or the database, which keeps it easy to
test. It raises CertificateGenerationError for expected, user-explainable
failures; the message of that exception is safe to show to API clients.
"""
import math
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import reportlab
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from app.services.storage import delete_quietly

PAGE_W, PAGE_H = landscape(A4)  # ~841.9 x 595.3 points
CENTER_X = PAGE_W / 2

# Palette
NAVY = HexColor("#1F2A44")
GOLD = HexColor("#B8924A")
CREAM = HexColor("#FBF7EE")
INK = HexColor("#2B2B2B")
GREY = HexColor("#777777")

# Fonts. Vera (bundled inside the reportlab package) is a Unicode TrueType font
# used for all USER-SUPPLIED text. Built-in Times/Helvetica are used only for
# fixed ASCII labels.
SANS, SANS_BOLD, SANS_ITALIC = "CertSans", "CertSans-Bold", "CertSans-Italic"
_MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)

TEXT_MAX_WIDTH = 640  # keeps text well inside the inner border


def _register_fonts() -> None:
    font_dir = Path(reportlab.__file__).parent / "fonts"
    for name, filename in (
        (SANS, "Vera.ttf"),
        (SANS_BOLD, "VeraBd.ttf"),
        (SANS_ITALIC, "VeraIt.ttf"),
    ):
        pdfmetrics.registerFont(TTFont(name, str(font_dir / filename)))


_register_fonts()


class CertificateGenerationError(Exception):
    """Expected failure; str(exc) is safe to store and return to the client."""


@dataclass(frozen=True)
class CertificateData:
    recipient_name: str
    event_name: str
    certificate_title: str
    issue_date: date
    certificate_id: str


def find_unsupported_characters(text: str) -> list[str]:
    """Characters of `text` that the certificate font cannot draw (de-duplicated)."""
    glyphs = pdfmetrics.getFont(SANS).face.charToGlyph
    seen: list[str] = []
    for ch in text:
        if ord(ch) not in glyphs and ch not in seen:
            seen.append(ch)
    return seen


def _format_date(d: date) -> str:
    # Not strftime("%B"): that depends on the server's locale.
    return f"{d.day} {_MONTHS[d.month - 1]} {d.year}"


# --------------------------------------------------------------------------
# Text fitting: long names / event names must never overflow the layout
# --------------------------------------------------------------------------
def _wrap_text(text: str, font: str, size: int, max_width: float) -> list[str]:
    """Greedy word wrap; words wider than the line are broken by character."""

    def width(s: str) -> float:
        return pdfmetrics.stringWidth(s, font, size)

    lines: list[str] = []
    current = ""
    for word in text.split(" "):
        while width(word) > max_width:  # a single 'word' that cannot fit
            cut = len(word)
            while cut > 1 and width(word[:cut]) > max_width:
                cut -= 1
            if current:
                lines.append(current)
                current = ""
            lines.append(word[:cut])
            word = word[cut:]
        candidate = f"{current} {word}" if current else word
        if width(candidate) <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or [""]


def _fit_text(
    text: str,
    font: str,
    max_size: int,
    min_size: int,
    max_width: float,
    max_block_height: float | None = None,
):
    """Choose a font size and line breaks so the text fits its slot.

    1. Prefer ONE line: shrink from max_size down to min_size.
    2. Otherwise wrap. If a height budget is given, keep shrinking (down to a
       hard floor of 12pt) until the wrapped block fits inside it.
    """
    for size in range(max_size, min_size - 1, -1):
        lines = _wrap_text(text, font, size, max_width)
        if len(lines) == 1:
            return size, lines
    if max_block_height is not None:
        for size in range(min_size, 11, -1):
            lines = _wrap_text(text, font, size, max_width)
            if (len(lines) - 1) * size * 1.2 <= max_block_height:
                return size, lines
    return min_size, _wrap_text(text, font, min_size, max_width)


# --------------------------------------------------------------------------
# Drawing
# --------------------------------------------------------------------------
def _diamond(c: canvas.Canvas, x: float, y: float, r: float) -> None:
    path = c.beginPath()
    path.moveTo(x, y + r)
    path.lineTo(x + r, y)
    path.lineTo(x, y - r)
    path.lineTo(x - r, y)
    path.close()
    c.drawPath(path, fill=1, stroke=0)


def _star(c: canvas.Canvas, x: float, y: float, outer: float, inner: float) -> None:
    path = c.beginPath()
    for i in range(10):
        radius = outer if i % 2 == 0 else inner
        angle = math.pi / 2 + i * math.pi / 5
        px, py = x + radius * math.cos(angle), y + radius * math.sin(angle)
        path.moveTo(px, py) if i == 0 else path.lineTo(px, py)
    path.close()
    c.drawPath(path, fill=1, stroke=0)


def _draw_frame(c: canvas.Canvas) -> None:
    c.setFillColor(CREAM)
    c.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)

    c.setStrokeColor(NAVY)
    c.setLineWidth(4)
    c.rect(22, 22, PAGE_W - 44, PAGE_H - 44, fill=0, stroke=1)
    c.setStrokeColor(GOLD)
    c.setLineWidth(1.2)
    c.rect(32, 32, PAGE_W - 64, PAGE_H - 64, fill=0, stroke=1)
    c.setStrokeColor(NAVY)
    c.setLineWidth(0.5)
    c.rect(38, 38, PAGE_W - 76, PAGE_H - 76, fill=0, stroke=1)

    for x, y in ((32, 32), (PAGE_W - 32, 32), (32, PAGE_H - 32), (PAGE_W - 32, PAGE_H - 32)):
        c.setFillColor(GOLD)
        _diamond(c, x, y, 11)
        c.setFillColor(CREAM)
        c.circle(x, y, 3, fill=1, stroke=0)


def _draw_centered_lines(c, lines, font, size, first_baseline, leading, color) -> float:
    """Draw lines centred on the page, top to bottom; return the last baseline."""
    c.setFillColor(color)
    c.setFont(font, size)
    y = first_baseline
    for line in lines:
        c.drawCentredString(CENTER_X, y, line)
        y -= leading
    return y + leading


def _draw_seal(c: canvas.Canvas, x: float, y: float) -> None:
    c.setFillColor(GOLD)
    c.circle(x, y, 40, fill=1, stroke=0)
    c.setStrokeColor(NAVY)
    c.setLineWidth(1.5)
    c.circle(x, y, 34, fill=0, stroke=1)
    c.setStrokeColor(CREAM)
    c.setLineWidth(1)
    c.circle(x, y, 28, fill=0, stroke=1)
    c.setFillColor(NAVY)
    _star(c, x, y, 18, 8)


def generate_single_certificate(path: Path, data: CertificateData) -> None:
    """Public wrapper to generate the certificate PDF and clean up on errors."""
    unsupported = find_unsupported_characters(data.recipient_name) + find_unsupported_characters(data.event_name)
    if unsupported:
        chars = ", ".join(f"'{c}'" for c in unsupported)
        raise CertificateGenerationError(f"Font does not support characters: {chars}")

    os.makedirs(path.parent, exist_ok=True)
    try:
        _render(path, data)
    except Exception as e:
        delete_quietly(path)
        raise CertificateGenerationError(f"PDF engine crash: {e}") from e


def _render(path: Path, data: CertificateData) -> None:
    c = canvas.Canvas(str(path), pagesize=landscape(A4))
    c.setTitle(f"{data.certificate_title} - {data.recipient_name}")
    c.setAuthor("Bulk Certificate Generator")
    c.setSubject(data.event_name)

    _draw_frame(c)

    # Title (anchored so its LAST line always sits at y=462)
    size, lines = _fit_text(data.certificate_title, SANS_BOLD, 36, 20, TEXT_MAX_WIDTH)
    leading = size * 1.2
    _draw_centered_lines(
        c, lines, SANS_BOLD, size, 462 + (len(lines) - 1) * leading, leading, NAVY
    )

    # Gold divider with a centre diamond
    c.setStrokeColor(GOLD)
    c.setLineWidth(1)
    c.line(CENTER_X - 150, 442, CENTER_X - 14, 442)
    c.line(CENTER_X + 14, 442, CENTER_X + 150, 442)
    c.setFillColor(GOLD)
    _diamond(c, CENTER_X, 442, 6)

    c.setFillColor(INK)
    c.setFont("Times-Italic", 16)
    c.drawCentredString(CENTER_X, 408, "This is to certify that")

    # Recipient name: dynamic text scaling window
    size, lines = _fit_text(data.recipient_name, SANS_BOLD, 42, 24, TEXT_MAX_WIDTH, max_block_height=75)
    leading = size * 1.2
    _draw_centered_lines(
        c, lines, SANS_BOLD, size, 348 + ((len(lines) - 1) * leading) / 2, leading, GOLD
    )

    c.setFillColor(INK)
    c.setFont("Times-Italic", 16)
    c.drawCentredString(CENTER_X, 290, "has successfully completed")

    # Event Name block
    size, lines = _fit_text(data.event_name, SANS, 22, 14, TEXT_MAX_WIDTH)
    leading = size * 1.3
    _draw_centered_lines(
        c, lines, SANS, size, 252 + (len(lines) - 1) * leading, leading, NAVY
    )

    # Footer elements
    c.setStrokeColor(GOLD)
    c.setLineWidth(0.8)
    c.line(120, 130, 280, 130)
    c.line(PAGE_W - 280, 130, PAGE_W - 120, 130)

    c.setFillColor(GREY)
    c.setFont(SANS, 10)
    c.drawCentredString(200, 114, _format_date(data.issue_date))
    c.drawCentredString(PAGE_W - 200, 114, "Authorized Signature")

    c.setFont("Helvetica", 8)
    c.setFillColor(GREY)
    c.drawString(46, 46, f"ID: {data.certificate_id}")

    _draw_seal(c, CENTER_X, 130)

    c.showPage()
    c.save()
