"""Read recording and execution dates the way they appear on runsheets.

Accepts 1948-05-10, 5/10/1948, 1948/05/10, May 10, 1948 and 10 May 1948.
Two-digit years are refused: "5/10/48" could be 1948 or 2048, and a wrong
century silently reorders a chain of title.
"""

from __future__ import annotations

import re
from datetime import date, datetime

_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d", "%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%d %B %Y", "%b. %d, %Y")


class DateError(ValueError):
    """A date that can't be read without guessing."""


def parse_date(text: str) -> date:
    v = re.sub(r"\s+", " ", (text or "").strip())
    v = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", v)  # "May 10th, 1948"
    if not v:
        raise DateError("no date")
    if re.fullmatch(r"\d{1,2}/\d{1,2}/\d{2}", v):
        raise DateError(f"two-digit year in {text!r}: write the full year")
    for pattern in _FORMATS:
        try:
            return datetime.strptime(v, pattern).date()
        except ValueError:
            continue
    raise DateError(f"can't read date {text!r}")


def iso(text: str) -> str:
    return parse_date(text).isoformat()
