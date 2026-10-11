"""Read recording and execution dates the way they appear on runsheets and in instruments.

Accepts 1948-05-10, 5/10/1948, 05-10-1948, 1948/05/10, May 10, 1948, Sept. 3, 1952,
10 May 1948, "the 10th day of May, 1948" and "June 1, A.D. 1925".
Two-digit years are refused: "5/10/48" could be 1948 or 2048, and a wrong
century silently reorders a chain of title. A month and year with no day is refused too.
Numeric dates are read month-first (US order), the way Texas and New Mexico records write them.
"""

from __future__ import annotations

import re
from datetime import date

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "sept": 9,
    "oct": 10, "nov": 11, "dec": 12,
}
_MONTH = r"([A-Za-z]{3,9})\.?"
_YMD = re.compile(r"(\d{4})\s*[-/]\s*(\d{1,2})\s*[-/]\s*(\d{1,2})")
_MDY = re.compile(r"(\d{1,2})\s*[-/]\s*(\d{1,2})\s*[-/]\s*(\d{4})")
_MONTH_DAY_YEAR = re.compile(rf"{_MONTH}\s*(\d{{1,2}})\s*,?\s*(\d{{4}})")
_DAY_MONTH_YEAR = re.compile(rf"(\d{{1,2}})\s+{_MONTH}\s*,?\s*(\d{{4}})")


class DateError(ValueError):
    """A date that can't be read without guessing."""


def _clean(text: str) -> str:
    v = re.sub(r"\s+", " ", (text or "").strip())
    v = re.sub(r"(\d)(?:st|nd|rd|th)\b", r"\1", v, flags=re.I)  # "May 10th, 1948"
    v = re.sub(r"^(?:on\s+)?the\s+", "", v, flags=re.I)  # "the 10th day of May, 1948"
    v = re.sub(r"\bday\s+of\s+", "", v, flags=re.I)
    v = re.sub(r"\bA\.?\s*D\.?\s*(?=\d{4}\b)", "", v, flags=re.I)  # "A.D. 1925"
    return v.strip(" .")


def parse_date(text: str) -> date:
    v = _clean(text)
    if not v:
        raise DateError("no date")
    parts: tuple[int, int, int] | None = None
    if m := _YMD.fullmatch(v):
        parts = int(m.group(1)), int(m.group(2)), int(m.group(3))
    elif m := _MDY.fullmatch(v):
        parts = int(m.group(3)), int(m.group(1)), int(m.group(2))
    elif (m := _MONTH_DAY_YEAR.fullmatch(v)) and m.group(1).lower() in _MONTHS:
        parts = int(m.group(3)), _MONTHS[m.group(1).lower()], int(m.group(2))
    elif (m := _DAY_MONTH_YEAR.fullmatch(v)) and m.group(2).lower() in _MONTHS:
        parts = int(m.group(3)), _MONTHS[m.group(2).lower()], int(m.group(1))
    if parts is None:
        if re.search(r"(?<![\d'])'?\d{2}\s*$", v) and not re.search(r"\d{4}", v):
            raise DateError(f"two-digit year in {text!r}: write the full year")
        raise DateError(f"can't read date {text!r}")
    try:
        return date(*parts)
    except ValueError as exc:
        raise DateError(f"no such date {text!r} ({exc})") from None


def iso(text: str) -> str:
    return parse_date(text).isoformat()
