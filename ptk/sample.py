"""A fictional typewritten deed page, for testing OCR and extraction end to end.

Every name and recording reference is invented, and the page says so.
Requires Pillow.
"""

from __future__ import annotations

from pathlib import Path

SAMPLE_LINES = [
    "MINERAL DEED",
    "",
    "THE STATE OF TEXAS",
    "COUNTY OF REEVES",
    "",
    "KNOW ALL MEN BY THESE PRESENTS: That ADAMS EXAMPLE, a single man,",
    "hereinafter called Grantor, for and in consideration of Ten Dollars",
    "($10.00) cash in hand paid, does hereby grant, sell and convey unto",
    "BAKER EXAMPLE an undivided one-half (1/2) interest in and to all of",
    "the oil, gas and other minerals in and under Section 12, Block 33,",
    "T-2-S, T&P RR Co. Survey, A-123, Reeves County, Texas, containing",
    "640 acres, more or less.",
    "",
    "Grantor reserves unto himself one-half (1/2) of the one-eighth (1/8)",
    "royalty for a term of fifteen (15) years and as long thereafter as",
    "oil, gas or other minerals are produced from said land.",
    "",
    "Grantor does hereby bind himself, his heirs and assigns, to WARRANT",
    "AND FOREVER DEFEND the title to said interest.",
    "",
    "EXECUTED this 10th day of February, 1948.",
    "",
    "                                   ADAMS EXAMPLE",
    "",
    "Filed for record February 15, 1948, in Vol. 88, Page 77,",
    "Deed Records of Reeves County, Texas.",
    "",
    "SAMPLE DOCUMENT - FICTIONAL - FOR SOFTWARE TESTING ONLY",
]

_FONTS = [
    "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/Library/Fonts/Courier New.ttf",
    "C:/Windows/Fonts/cour.ttf",
]


def render_sample_page(out: str | Path, width: int = 1700, height: int = 2200) -> Path:
    """Write a 200-dpi letter-size grayscale PNG of the sample deed."""
    from PIL import Image, ImageDraw, ImageFont

    font = None
    for candidate in _FONTS:
        if Path(candidate).exists():
            font = ImageFont.truetype(candidate, 30)
            break
    if font is None:
        font = ImageFont.load_default()
    img = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(img)
    y = 160
    for line in SAMPLE_LINES:
        draw.text((140, y), line, fill=0, font=font)
        y += 52
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return out
