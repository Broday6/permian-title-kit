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
    "fraction_words",
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


def _kind(w: str) -> str:
    if w in _UNITS:
        return "unit" if _UNITS[w] < 10 else "teen"
    if w in _TENS:
        return "tens"
    return "scale"


def _cardinal(words: list[str]) -> int:
    """'twenty seven' -> 27, 'two thousand one hundred thirteen' -> 2113. Refuses sequences that aren't a
    number ('nine two', 'seven sixty'), so a phrase can't be split into a wrong but valid-looking reading."""
    total, current = 0, 0
    prev = None
    if not words:
        raise FractionError("empty number")
    for w in words:
        if w in ("and", "a", "an"):
            if w in ("a", "an") and current == 0 and total == 0:
                current = 1
            continue
        if w not in _UNITS and w not in _TENS and w not in _SCALES:
            raise FractionError(f"not a number word: {w!r}")
        kind = _kind(w)
        if (kind in ("unit", "teen") and prev in ("unit", "teen") or kind == "teen" and prev == "tens"
                or kind == "tens" and prev in ("unit", "teen", "tens")):
            raise FractionError(f"not a number: {' '.join(words)!r}")
        if w in _UNITS:
            current += _UNITS[w]
        elif w in _TENS:
            current += _TENS[w]
        else:
            current = max(current, 1) * _SCALES[w]
            if _SCALES[w] >= 1000:
                total, current = total + current, 0
        prev = kind
    return total + current


def _ordinal(words: list[str]) -> int:
    """'sixty-fourth' -> 64, 'one hundred twenty-eighth' -> 128, 'halves' -> 2."""
    last = words[-1]
    if last.endswith("s") and last not in ("halves",):
        last = last[:-1]  # sixteenths -> sixteenth
    if last == "halves":
        last = "half"
    head = words[:-1]
    if last in _ORDINAL_SCALES:
        return (_cardinal(head) if head else 1) * _ORDINAL_SCALES[last]
    if last in _ORDINAL_UNITS:
        base, kind = _ORDINAL_UNITS[last], "unit"
    elif last in _ORDINAL_TENS:
        base, kind = _ORDINAL_TENS[last], "tens"
    else:
        raise FractionError(f"not an ordinal: {words[-1]!r}")
    if head:
        tail = _kind(head[-1]) if head[-1] in _UNITS or head[-1] in _TENS or head[-1] in _SCALES else None
        if kind == "unit" and tail in ("unit", "teen") or kind == "tens" and tail in ("unit", "teen", "tens"):
            raise FractionError(f"not an ordinal: {' '.join(words)!r}")  # 'five sixth', 'seven sixtieth'
    return (_cardinal(head) if head else 0) + base


def _readings(tokens: list[str]) -> list[Fraction]:
    """Every numerator/denominator split of the tokens that reads as numbers, in order."""
    out: list[Fraction] = []
    for split in range(1, len(tokens)):
        num_tokens, den_tokens = tokens[:split], tokens[split:]
        num_words = [w for tok in num_tokens for w in tok.split("-") if w]
        den_words = [w for tok in den_tokens for w in tok.split("-") if w]
        try:
            num = 1 if num_words in (["a"], ["an"]) else _cardinal(num_words)
            den = _ordinal(den_words)
        except FractionError:
            continue
        if den and num:
            out.append(Fraction(num, den))
    return out


def _words_to_fraction(text: str) -> Fraction:
    """Read 'one-half', 'three-sixteenths', 'an undivided one sixty-fourth', 'twenty-seven sixty-fourths'.

    Hyphenated words are read as one number first ('twenty-seven sixty-fourths' = 27/64), then as
    separate words ('one-half'). A phrase that still reads two ways as a proper fraction is refused,
    never guessed: write the numerals beside it.
    """
    t = text.lower()
    t = re.sub(r"\b(an?\s+)?undivided\b", " ", t)
    t = re.sub(r"\b(interest|of the|the|usual|said|th)\b", " ", t)
    pct = re.search(r"\s*(?:percent|per\s+cent|%)\s*$", t)
    if pct:  # "twenty-five percent", "twelve and one-half percent"
        body = t[:pct.start()].strip()
        whole, _, rest = body.partition(" and ")
        words = [w for tok in whole.split() for w in tok.split("-") if w]
        value = Fraction(_cardinal(words))
        if rest:
            part = _words_to_fraction(rest)
            if not 0 < part < 1:
                raise FractionError(f"can't read percent words {text!r}")
            value += part
        return value / 100
    tokens = [w for w in re.split(r"\s+", t.strip()) if w]
    if not tokens:
        raise FractionError("empty fraction")
    flat = [w for tok in tokens for w in tok.split("-") if w]
    if flat in (["half"], ["one", "half"], ["a", "half"], ["one", "halves"]):
        return Fraction(1, 2)
    readings = _readings(tokens) if len(tokens) > 1 else []
    if not readings:
        readings = _readings(flat)
    if not readings:
        raise FractionError(f"can't read fraction words {text!r}")
    proper = list(dict.fromkeys(r for r in readings if 0 < r <= 1))
    if len(proper) > 1:
        raise FractionError(f"{text!r} reads more than one way ({', '.join(fmt(r) for r in proper)}); "
                            "write the numerals beside the words")
    return proper[0] if proper else readings[0]


_NUMERIC = re.compile(
    r"^\s*(?:(?P<whole>\d+)(?:\s+|-)(?=\d+\s*/))?(?P<a>\d*\.\d+|\d+(?:\.\d+)?)\s*"
    r"(?:/\s*(?P<b>\d+(?:\.\d+)?)\s*(?:st|nd|rd|th|ths)?)?\s*(?P<pct>%|percent|per\s+cent)?\s*$",
    re.I,
)
_VULGAR = {"½": "1/2", "¼": "1/4", "¾": "3/4", "⅛": "1/8", "⅜": "3/8", "⅝": "5/8", "⅞": "7/8",
           "⅓": "1/3", "⅔": "2/3", "⅙": "1/6", "⅚": "5/6", "⅕": "1/5", "⅖": "2/5", "⅗": "3/5", "⅘": "4/5"}
_TRAILING_NOUNS = re.compile(
    r"\s+(?:(?:non[-\s]?participating\s+|overriding\s+|net\s+profits?\s+|term\s+)?royalty(?:\s+interest)?|"
    r"(?:working|mineral|net\s+revenue|leasehold|royalty)\s+interest|interest|NPRI|ORRI|NRI|WI)\s*$", re.I)
# "the usual one-eighth", "an undivided 1/4": words around a value that don't change it
_LEADING = re.compile(r"^\s*(?:the\s+)?(?:usual\s+|said\s+|customary\s+)?(?:an?\s+(?=undivided\b))?(?:undivided\s+)?", re.I)


def _normalize(text: str) -> str:
    """'12½%' -> '12 1/2%', '1⁄8' -> '1/8', '3/16 royalty' -> '3/16'."""
    t = text.replace("\u2044", "/").replace(",", "")
    for glyph, frac in _VULGAR.items():
        t = re.sub(rf"(\d)\s*{glyph}", rf"\1 {frac}", t)
        t = t.replace(glyph, frac)
    return _TRAILING_NOUNS.sub("", t).strip()


def _numeric(text: str) -> Fraction:
    m = _NUMERIC.match(_normalize(text))
    if not m:
        raise FractionError(f"not a numeric fraction: {text!r}")
    a = Fraction(m.group("a"))
    if m.group("b"):
        b = Fraction(m.group("b"))
        if b == 0:
            raise FractionError("zero denominator")
        a = a / b
    if m.group("whole"):
        a += int(m.group("whole"))
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
    outside = _TRAILING_NOUNS.sub("", _LEADING.sub("", re.sub(r"\([^)]*\)", " ", part))).strip()
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
    parts = [p for p in re.split(r"\s+(?:of|x)\s+|\s*[×*]\s*", _normalize(raw), flags=re.I) if p.strip()]
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


_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
         "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
_TENS_WORDS = {2: "twenty", 3: "thirty", 4: "forty", 5: "fifty", 6: "sixty", 7: "seventy", 8: "eighty", 9: "ninety"}
_ORDINAL_OF = {"one": "first", "two": "second", "three": "third", "five": "fifth", "eight": "eighth",
               "nine": "ninth", "twelve": "twelfth"}


def _cardinal_words(n: int) -> str:
    if n < 20:
        return _ONES[n]
    if n < 100:
        tens, unit = divmod(n, 10)
        return _TENS_WORDS[tens] + (f"-{_ONES[unit]}" if unit else "")
    if n < 1000:
        hundreds, rest = divmod(n, 100)
        return f"{_ONES[hundreds]} hundred" + (f" {_cardinal_words(rest)}" if rest else "")
    if n < 1_000_000:
        thousands, rest = divmod(n, 1000)
        return f"{_cardinal_words(thousands)} thousand" + (f" {_cardinal_words(rest)}" if rest else "")
    raise FractionError(f"{n} is too large to write in words")


def _ordinal_words(n: int, plural: bool) -> str:
    if n == 2:
        return "halves" if plural else "half"
    if n == 4:
        return "fourths" if plural else "fourth"
    words = _cardinal_words(n).removeprefix("one ") if n in (100, 1000) else _cardinal_words(n)
    head, sep, last = max(((words[:i], words[i], words[i + 1:]) for i in range(len(words)) if words[i] in " -"),
                          key=lambda t: len(t[0]), default=("", "", words))
    if last in _ORDINAL_OF:
        last = _ORDINAL_OF[last]
    elif last.endswith("y"):
        last = last[:-1] + "ieth"
    else:
        last += "th"
    return f"{head}{sep}{last}" + ("s" if plural else "")


def fraction_words(value: Fraction) -> str | None:
    """Deed words for a fraction between 0 and 1: 1/16 -> 'one-sixteenth', 3/64 -> 'three sixty-fourths'.

    Returns None when the words wouldn't read back as the same value through
    ``parse_fraction`` (very large terms), so a draft never states words that
    disagree with its numerals. Use the numerals alone in that case.
    """
    if not 0 < value < 1:
        return None
    try:
        num = _cardinal_words(value.numerator)
        den = _ordinal_words(value.denominator, plural=value.numerator > 1)
    except FractionError:
        return None
    num, den = num.replace(" ", "-"), den.replace(" ", "-")  # one token each: "one one-hundred-twenty-eighth"
    words = f"{num}-{den}" if "-" not in num + den else f"{num} {den}"
    reading = read_fraction_text(f"{words} ({fmt(value)})")
    return words if reading.consistent and reading.value == value else None
