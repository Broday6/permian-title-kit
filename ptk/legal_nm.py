"""Parse New Mexico PLSS descriptions into sections, quarter-quarters and lots.

    Section 15, T23S, R31E, NMPM: NE/4, N/2 SE/4 and Lots 1-4
    SE/4 of NW/4 and W/2 W/2 of Section 6, Township 24 South, Range 32 East
    Sec. 22: All; Sec. 27: N2NE4, T. 19 S., R. 33 E., N.M.P.M.

Aliquots are resolved right to left, so "N/2 SE/4" is the north half of the
southeast quarter = {NESE, NWSE}, and "W/2 W/2" = {NWNW, SWNW, NWSW, SWSW}.
Results are BLM-style quarter-quarter labels ("NESE" = NE/4 of SE/4) that
join to CadNSDI ``PLSSIntersected``. Lots are listed by number; their
geometry and acreage come from CadNSDI, never from a 40-acre assumption.
Divisions finer than a quarter-quarter are kept as labels like "N2NESE".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from fractions import Fraction

QUARTERS = ("NE", "NW", "SW", "SE")
HALVES = {"N": ("NE", "NW"), "S": ("SE", "SW"), "E": ("NE", "SE"), "W": ("NW", "SW")}

_TWP = re.compile(r"\b(?:T(?:ownship|wp|sp)?\.?)\s*-?\s*(\d+)\s*-?\s*(N|S)(?:orth|outh)?\b\.?", re.I)
_RNG = re.compile(r"\b(?:R(?:ange|ng|ge)?\.?)\s*-?\s*(\d+)\s*-?\s*(E|W)(?:ast|est)?\b\.?", re.I)
_SEC = re.compile(r"\b(?:sec(?:tion)?s?)\.?\s*(\d+(?:\s*(?:,|and|&)\s*\d+\b(?!\s*/))*)", re.I)
_PART = re.compile(r"(NE|NW|SE|SW|N|S|E|W)(?:\s*/\s*[24]|\s*1/[24]|[24½¼])?", re.I)
_ALIQUOT_ONLY = re.compile(r"^(?:(?:NE|NW|SE|SW|N|S|E|W)(?:\s*/\s*[24]|\s*1/[24]|[24½¼])?\s*)+$", re.I)
_WORDS = [
    (r"\b(north|south)\s*(east|west)\s+(?:one[-\s])?(?:quarter|fourth)\b", lambda m: f"{m.group(1)[0]}{m.group(2)[0]}/4"),
    (r"\b(north|south|east|west)\s+(?:one[-\s])?half\b", lambda m: f"{m.group(1)[0]}/2"),
]
_LOTS = re.compile(r"\blots?\s+((?:\d+(?:\s*(?:-|through|thru|to)\s*\d+)?(?:\s*(?:,|and|&)\s*)?)+)", re.I)


@dataclass
class SectionPart:
    section: int
    whole: bool = False
    quarter_quarters: set[str] = field(default_factory=set)
    sub_divisions: set[str] = field(default_factory=set)  # finer than a QQ, e.g. "N2NESE"
    lots: list[int] = field(default_factory=list)
    aliquot_text: list[str] = field(default_factory=list)
    unread: list[str] = field(default_factory=list)  # text that wasn't an aliquot or lot

    def regular_area(self) -> Fraction:
        """Area as a fraction of a regular 640-acre section, ignoring lots."""
        if self.whole:
            return Fraction(1)
        return sum((_area(label) for label in self.quarter_quarters | self.sub_divisions), Fraction(0))


@dataclass
class PlssDescription:
    text: str
    township: str | None = None  # "23S"
    range: str | None = None  # "31E"
    meridian: str = "NMPM"
    sections: list[SectionPart] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)

    def keys(self) -> list[str]:
        """One key per QQ, lot or whole section: NM|23S|31E|15|NESE."""
        out = []
        for s in self.sections:
            base = f"NM|{self.township}|{self.range}|{s.section}"
            if s.whole:
                out.append(f"{base}|ALL")
            out += [f"{base}|{qq}" for qq in sorted(s.quarter_quarters)]
            out += [f"{base}|{sd}" for sd in sorted(s.sub_divisions)]
            out += [f"{base}|LOT {n}" for n in s.lots]
        return out


def _normalize_words(text: str) -> str:
    for pattern, repl in _WORDS:
        text = re.sub(pattern, repl, text, flags=re.I)
    return text


def _area(label: str) -> Fraction:
    """'NESE' -> 1/16, 'N2NESE' -> 1/32: product of each division's share."""
    area = Fraction(1)
    for token in _PART.findall(label):
        area *= Fraction(1, 4) if token.upper() in QUARTERS else Fraction(1, 2)
    return area


def is_aliquot(phrase: str) -> bool:
    cleaned = re.sub(r"\b(?:of|the)\b", " ", _normalize_words(phrase), flags=re.I).strip()
    return bool(cleaned) and bool(_ALIQUOT_ONLY.match(cleaned))


def expand_aliquot(phrase: str) -> tuple[set[str], set[str]]:
    """Expand one aliquot phrase to (quarter-quarters, finer divisions).

    The phrase is read smallest-to-largest as written ("N/2 SE/4" = N/2 of SE/4),
    so it's applied largest-first.
    """
    cleaned = re.sub(r"\b(?:of|the)\b", " ", _normalize_words(phrase), flags=re.I).strip()
    if not _ALIQUOT_ONLY.match(cleaned):
        raise ValueError(f"not an aliquot description: {phrase!r}")
    parts = [p.upper() for p in _PART.findall(cleaned)]
    parts.reverse()

    def pick(token: str) -> tuple[str, ...]:
        return (token,) if token in QUARTERS else HALVES[token]

    quarters = pick(parts[0])
    if len(parts) == 1:
        return {qq + q for q in quarters for qq in QUARTERS}, set()
    qqs = {qq + q for q in quarters for qq in pick(parts[1])}
    if len(parts) == 2:
        return qqs, set()
    prefix = "".join(t if t in QUARTERS else f"{t}2" for t in reversed(parts[2:]))
    return set(), {prefix + qq for qq in qqs}


def _lots(text: str) -> list[int]:
    out: list[int] = []
    for m in _LOTS.finditer(text):
        for a, b in re.findall(r"(\d+)(?:\s*(?:-|through|thru|to)\s*(\d+))?", m.group(1)):
            lo = int(a)
            hi = int(b) if b else lo
            out.extend(range(lo, hi + 1))
    return sorted(set(out))


def _apply(part: SectionPart, text: str) -> None:
    lots = _lots(text)
    part.lots = sorted(set(part.lots + lots))
    body = _LOTS.sub(" ", text)
    phrases = [p.strip(" :.") for p in re.split(r"\s*(?:,|;|\band\b|&)\s*", body)]
    phrases = [p for p in phrases if p]
    if any(re.fullmatch(r"all", p, re.I) for p in phrases):
        part.whole = True
        return
    for phrase in phrases:
        if not is_aliquot(phrase):
            part.unread.append(phrase)
            continue
        qqs, finer = expand_aliquot(phrase)
        part.quarter_quarters |= qqs
        part.sub_divisions |= finer
        part.aliquot_text.append(phrase)


_COMPACT = re.compile(r"\b(\d{1,2})-(\d{1,3})\s*([NS])-(\d{1,3})\s*([EW])\b", re.I)  # 10-18S-34E
_NUMBER_WORDS = ("one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|"
                 "seventeen|eighteen|nineteen|twenty")
_WORD_NUMERAL = re.compile(rf"\b(?:{_NUMBER_WORDS})\s*\((\d+)\)", re.I)  # Lots One (1) -> Lots 1
_PAREN_ALIQUOT = re.compile(r"\(\s*(?:(?:NE|NW|SE|SW|N|S|E|W)\s*(?:/\s*[24]|1/[24]|[24½¼])?[\s,]*)+\)", re.I)  # (NE/4 SE/4)


def parse(text: str) -> PlssDescription:
    d = PlssDescription(text=text)
    clean = re.sub(r"\s+", " ", text)
    clean = _COMPACT.sub(lambda m: f"Section {m.group(1)}, T{m.group(2)}{m.group(3).upper()}, R{m.group(4)}{m.group(5).upper()}", clean)
    clean = _WORD_NUMERAL.sub(r"\1", clean)
    clean = _PAREN_ALIQUOT.sub(" ", clean)  # the words beside them say the same thing
    t = _TWP.search(clean)
    r = _RNG.search(clean)
    d.township = f"{int(t.group(1))}{t.group(2).upper()}" if t else None
    d.range = f"{int(r.group(1))}{r.group(2).upper()}" if r else None
    if not d.township:
        d.issues.append("no township")
    if not d.range:
        d.issues.append("no range")
    townships = sorted({f"{int(a)}{b.upper()}" for a, b in _TWP.findall(clean)})
    ranges = sorted({f"{int(a)}{b.upper()}" for a, b in _RNG.findall(clean)})
    if len(townships) > 1 or len(ranges) > 1:
        d.issues.append(f"more than one township or range ({', '.join(townships + ranges)}): every section is keyed to "
                        f"{d.township} {d.range}; split the description by township and range")
    if re.search(r"\bN\.?\s*M\.?\s*P\.?\s*M\b|New Mexico Principal Meridian", clean, re.I) is None:
        d.issues.append("meridian not stated; assumed NMPM")

    # Strip township/range text so "S" and "E" in "T23S" aren't read as aliquots.
    body = _RNG.sub(" ", _TWP.sub(" ", clean))
    body = re.sub(r"\bN\.?\s*M\.?\s*P\.?\s*M\.?|\b\w+\s+County\b|,?\s*New Mexico\b", " ", body, flags=re.I)
    body = re.sub(r"\(?\d[\d,]*(?:\.\d+)?\s*(?:gross\s+)?acres?[^,;]*\)?", " ", body, flags=re.I)

    markers = list(_SEC.finditer(body))
    if not markers:
        d.issues.append("no section number")
        return d
    # Each section takes the aliquots written before it ("NE/4 of Section 6", "E/2 NE/4, Section 33",
    # "Lots 1 thru 4, Section 1"); when nothing comes before it, the text after it up to the next ";"
    # ("Section 15: NE/4, N/2 SE/4"). Styles can mix within one description.
    boundary = 0
    for i, m in enumerate(markers):
        next_start = markers[i + 1].start() if i + 1 < len(markers) else len(body)
        before = re.sub(r"\bof(?:\s+the)?\s*$", " ", body[boundary:m.start()].strip(), flags=re.I)
        before = re.sub(r"^[\s,;:]+|[\s,;:]+$", "", before)
        before = re.sub(r"^(?:and|&)\s+", "", before, flags=re.I)
        if before:
            segment = before
            boundary = m.end()
        else:
            after = body[m.end():next_start]
            cut = after.find(";")
            segment = after if cut < 0 else after[:cut]
            boundary = m.end() + (len(after) if cut < 0 else cut + 1)
        segment = re.sub(r"^[\s,;:]+|[\s,;:]+$", "", segment)
        for number in re.findall(r"\d+", m.group(1)):
            part = SectionPart(int(number))
            if segment:
                _apply(part, segment)
            else:
                part.whole = True
            if part.unread:
                d.issues.append(f"section {part.section}: couldn't read {part.unread}")
            d.sections.append(part)
    return d
