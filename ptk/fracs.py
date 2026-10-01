"""Exact fractions for title work.

Every interest is carried as a ``fractions.Fraction`` from the instrument to the
pay deck. Floating point never touches ownership; it appears only when a value
is printed, rounded half-up to eight places the way division orders carry it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from fractions import Fraction

__all__ = [
    "FractionReading",
    "parse_fraction",
    "read_fraction_text",
    "to_decimal",
    "fmt",
]

_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90,
}
_ORDINAL_UNITS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6,
    "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10, "eleventh": 11,
    "twelfth": 12, "thirteenth": 13, "fourteenth": 14, "fifteenth": 15,
    "sixteenth": 16, "seventeenth": 17, "eighteenth": 18, "nineteenth": 19,
    "half": 2, "halve": 2, "quarter": 4,
}
_ORDINAL_TENS = {
    "twentieth": 20, "thirtieth": 30, "fortieth": 40, "fiftieth": 50,
    "sixtieth": 60, "seventieth": 70, "eightieth": 80, "ninetieth": 90,
}
_SCALES = {"hundred": 100, "thousand": 1000}
_ORDINAL_SCALES = {"hundredth": 100, "thousandth": 1000}


class FractionError(ValueError):
    """A value that can't be read as an exact fraction."""


def _cardinal(words: list[str]) -> int:
    total, current = 0, 0
    if not words:
        raise FractionError("empty number")
    for w in words:
        if w in ("and", "a", "an"):
            if w in ("a", "an") and current == 0 and total == 0:
                current = 1
            continue
        if w in _UNITS:
            current += _UNITS[w]
        elif w in _TENS:
            current += _TENS[w]
        elif w in _SCALES:
            current = max(current, 1) * _SCALES[w]
            if _SCALES[w] >= 1000:
                total, current = total + current, 0
        else:
            raise FractionError(f"not a number word: {w!r}")
    return total + current


def _ordinal(words: list[str]) -> int:
    """'sixty-fourth' -> 64, 'one hundred twenty-eighth' -> 128, 'halves' -> 2."""
    last = words[-1]
    if last.endswith("s") and last not in ("halves",):
        last = last[:-1]  # sixteenths -> sixteenth
    if last == "halves":
        last = "half"
    head = words[:-1]
    if last in _ORDINAL_UNITS:
        base = _ORDINAL_UNITS[last]
    elif last in _ORDINAL_TENS:
        base = _ORDINAL_TENS[last]
    elif last in _ORDINAL_SCALES:
        return (_cardinal(head) if head else 1) * _ORDINAL_SCALES[last]
    else:
        raise FractionError(f"not an ordinal: {words[-1]!r}")
    return (_cardinal(head) if head else 0) + base


def _words_to_fraction(text: str) -> Fraction:
    """Read 'one-half', 'three-sixteenths', 'an undivided one sixty-fourth'."""
    t = text.lower().replace("-", " ")
    t = re.sub(r"\b(an?\s+)?undivided\b", " ", t)
    t = re.sub(r"\b(interest|of the|th)\b", " ", t)
    words = [w for w in re.split(r"\s+", t.strip()) if w]
    if not words:
        raise FractionError("empty fraction")
    if words in (["half"], ["one", "half"], ["a", "half"], ["one", "halves"]):
        return Fraction(1, 2)
    # Find the split between numerator (cardinal) and denominator (ordinal).
    # Try the longest ordinal tail that parses, so "one hundred twenty-eighth"
    # is 1/128 rather than 100/28.
    for split in range(1, len(words)):
        num_words, den_words = words[:split], words[split:]
        try:
            num = _cardinal(num_words)
            den = _ordinal(den_words)
        except FractionError:
            continue
        if num_words in (["a"], ["an"]):
            num = 1
        if den and num:
            # Prefer the reading with the smallest numerator (longest denominator).
            return Fraction(num, den)
    raise FractionError(f"can't read fraction words {text!r}")


_NUMERIC = re.compile(
    r"^\s*(?P<a>\d+(?:\.\d+)?)\s*(?:/\s*(?P<b>\d+(?:\.\d+)?)\s*(?:st|nd|rd|th|ths)?)?\s*(?P<pct>%)?\s*$"
)


def _numeric(text: str) -> Fraction:
    m = _NUMERIC.match(text.replace(",", ""))
    if not m:
        raise FractionError(f"not a numeric fraction: {text!r}")
    a = Fraction(m.group("a"))
    if m.group("b"):
        b = Fraction(m.group("b"))
        if b == 0:
            raise FractionError("zero denominator")
        a = a / b
    if m.group("pct"):
        a = a / 100
    return a


def parse_fraction(text: str | int | Fraction) -> Fraction:
    """Parse one value or a product of values into an exact Fraction.

    Accepts ``3/16``, ``3/16ths``, ``0.1875``, ``18.75%``, ``1/2 of 1/8``,
    ``1/2 x 1/8``, ``one-half``, ``an undivided one-sixteenth``, and
    ``one-half (1/2)``. When words and numerals are both present and disagree,
    raises ``FractionError``; use ``read_fraction_text`` to inspect both.
    """
    if isinstance(text, Fraction):
        return text
    if isinstance(text, int):
        return Fraction(text)
    reading = read_fraction_text(str(text))
    if reading.value is None:
        raise FractionError(reading.problem or f"can't read {text!r}")
    if reading.consistent is False:
        raise FractionError(
            f"words say {fmt(reading.words_value)} but numerals say {fmt(reading.numeral_value)} in {text!r}"
        )
    return reading.value


@dataclass(frozen=True)
class FractionReading:
    """Both readings of a fraction as written, so a mismatch can be flagged."""

    text: str
    value: Fraction | None
    words_value: Fraction | None
    numeral_value: Fraction | None
    consistent: bool | None  # None when only one form is present
    problem: str | None = None


def _read_single(part: str) -> tuple[Fraction | None, Fraction | None]:
    """Return (words_value, numeral_value) for one factor like 'one-half (1/2)'."""
    paren = re.search(r"\(([^)]*)\)", part)
    outside = re.sub(r"\([^)]*\)", " ", part).strip()
    if paren:
        numeral = _numeric(paren.group(1))
        if not outside:
            return None, numeral
        try:
            return _numeric(outside), numeral
        except FractionError:
            return _words_to_fraction(outside), numeral
    try:
        return None, _numeric(outside)
    except FractionError:
        return _words_to_fraction(outside), None


def read_fraction_text(text: str) -> FractionReading:
    """Read a fraction the way an abstractor does: words and numerals separately."""
    raw = text.strip()
    parts = [p for p in re.split(r"\s+(?:of|x)\s+|\s*[×*]\s*", raw, flags=re.I) if p.strip()]
    if not parts:
        return FractionReading(raw, None, None, None, None, "empty")
    words_total = num_total = Fraction(1)
    have_words = have_nums = mismatch = False
    try:
        for part in parts:
            w, n = _read_single(part)
            if w is not None and n is not None and w != n:
                mismatch = True
            have_words |= w is not None
            have_nums |= n is not None
            words_total *= w if w is not None else n
            num_total *= n if n is not None else w
    except FractionError as e:
        return FractionReading(raw, None, None, None, None, str(e))
    both = have_words and have_nums
    return FractionReading(
        raw,
        None if mismatch else num_total,
        words_total if have_words else None,
        num_total if have_nums else None,
        (not mismatch) if both else None,
        "words and numerals disagree" if mismatch else None,
    )


def to_decimal(value: Fraction, places: int = 8) -> str:
    """Round half-up to ``places`` decimals (pay-deck convention), exactly."""
    scale = 10**places
    n, d = abs(value.numerator), value.denominator
    q = (n * scale * 2 + d) // (2 * d)
    whole, frac = divmod(q, scale)
    sign = "-" if value < 0 and q else ""
    return f"{sign}{whole}.{frac:0{places}d}" if places else f"{sign}{whole}"


def fmt(value: Fraction | None) -> str:
    if value is None:
        return "—"
    return str(value.numerator) if value.denominator == 1 else f"{value.numerator}/{value.denominator}"
