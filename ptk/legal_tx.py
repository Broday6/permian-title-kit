"""Parse Texas abstract/survey/block/section descriptions.

Texas never used the PLSS. A Permian tract is located by county, abstract
number (unique only within a county), survey (often a railroad grant),
block, the T&P "township" inside reservation blocks, and section. The same
section can be written a dozen ways across a century of deeds:

    Section 12, Block 33, T-2-S, T&P RR Co. Survey, A-123, Midland County
    All of Sec. 12, Blk. 33, Tsp. 2 South, T. & P. Ry. Co. Sur., Abst. No. 123
    N/2 of Section 5, Block C-21, PSL

This parser pulls the parts out deterministically and produces canonical keys
for joining to RRC survey polygons (county + abstract, or county + survey +
block + township + section). Anything it can't find is listed in ``issues``
so a person or an LLM fallback can finish the job. The text as written always
controls; a parse is a locator, not a construction of the deed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .counties import COUNTIES

# Canonical survey codes: (pattern, code, name). Order matters: longer codes first.
SURVEYS: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(p, re.I), code, name)
    for p, code, name in [
        (r"c\.?\s*c\.?\s*s\.?\s*d\.?\s*&\s*r\.?\s*g\.?\s*n\.?\s*g", "CCSD&RGNG", "Corpus Christi, San Diego & Rio Grande Narrow Gauge RR Co"),
        (r"g\.?\s*c\.?\s*&\s*s\.?\s*f\.?|gulf,?\s+colorado\s+(?:and|&)\s+santa\s+fe", "GC&SF", "Gulf, Colorado & Santa Fe Ry Co"),
        (r"g\.?\s*h\.?\s*&\s*s\.?\s*a\.?|galveston,?\s+harrisburg", "GH&SA", "Galveston, Harrisburg & San Antonio Ry Co"),
        (r"h\.?\s*&\s*t\.?\s*c\.?|houston\s+(?:and|&)\s+texas\s+central", "H&TC", "Houston & Texas Central RR Co"),
        (r"h\.?\s*&\s*g\.?\s*n\.?|houston\s+(?:and|&)\s+great\s+northern", "H&GN", "Houston & Great Northern RR Co"),
        (r"(?<![a-z])i\.?\s*&\s*g\.?\s*n\.?|international\s+(?:and|&)\s+great\s+northern", "I&GN", "International & Great Northern RR Co"),
        (r"t\.?\s*&\s*st\.?\s*l\.?", "T&STL", "Texas & St. Louis Ry Co"),
        (r"(?<![a-z])t\.?\s*&\s*p\.?(?![a-z])|texas\s+(?:and|&)\s+pacific", "T&P", "Texas & Pacific Ry Co"),
        (r"w\.?\s*&\s*n\.?\s*w\.?|waco\s+(?:and|&)\s+north\s*western", "W&NW", "Waco & Northwestern RR Co"),
        (r"\bp\.?\s*s\.?\s*l\.?(?![a-z])|public\s+school\s+lands?", "PSL", "Public School Land"),
        (r"\bc\.?\s*s\.?\s*l\.?(?![a-z])|county\s+school\s+lands?", "CSL", "County School Land"),
        (r"\bu\.?\s*l\.?(?![a-z])|university\s+lands?", "UL", "University Lands"),
    ]
]

_SECTION = re.compile(
    r"\bsec(?:tion|t)?s?\.?\s*(?:nos?\.?\s*)?(\d+[A-Z]?(?:\s*1/2|½)?(?:\s*(?:,|and|&)\s*\d+[A-Z]?(?![\d/]))*)",
    re.I,
)
_SURVEY_NO = re.compile(r"\bsurvey\s+(?:no\.?\s*)?(\d+[A-Z]?)\b", re.I)
_BLOCK = re.compile(r"\b(?:block|blk)\.?\s*(?:no\.?\s*)?(?!(?:of|in|no|on|or|at)\b)([A-Z]{0,2}-?\d+(?:-[A-Z0-9]+)?|[A-Z]{1,2})\b", re.I)
_TOWNSHIP = re.compile(r"\b(?:t(?:wp|sp|ownship)?)\.?\s*-?\s*(\d+)\s*-?\s*(n(?:orth)?|s(?:outh)?)\b", re.I)
_ABSTRACT_WORD = re.compile(r"\b(?:abstract|abst|abs)\.?\s*(?:no\.?\s*)?-?\s*(\d+[A-Z]?)\b", re.I)
_ABSTRACT_A = re.compile(r"\bA-\s?(\d+[A-Z]?)\b")  # case-sensitive: "a 160 acre tract" must not match
_COUNTY = re.compile(r"\b([A-Z][a-z]+(?:\s[A-Z][a-z]+)?)\s+County\b")
_ACRES = re.compile(r"(\d[\d,]*(?:\.\d+)?)[\s-]*(?:gross\s+)?acres?\b", re.I)
_SAVE = re.compile(r"\b(?:save|less)\s*(?:and|&)\s*except(?:ing)?\b[:,]?\s*(.+?)(?=;|\bsave\s*(?:and|&)\s*except|$)", re.I | re.S)
_ALIQUOT = re.compile(
    r"\b(NE|NW|SE|SW|N|S|E|W)\s*(?:/\s*([24])|1/([24])|([½¼]))"
    r"|\b(north|south|east|west|northeast|northwest|southeast|southwest)\s+one[-\s](half|quarter|fourth)\b",
    re.I,
)
_WORD_DIR = {"north": "N", "south": "S", "east": "E", "west": "W",
             "northeast": "NE", "northwest": "NW", "southeast": "SE", "southwest": "SW"}


@dataclass
class TxDescription:
    text: str
    county: str | None = None
    abstract: str | None = None
    survey_code: str | None = None
    survey_name: str | None = None
    survey_no: str | None = None
    block: str | None = None
    township: str | None = None  # e.g. "2S"
    section: str | None = None  # the first section; ``sections`` holds all of them
    sections: list[str] = field(default_factory=list)
    aliquots: list[str] = field(default_factory=list)
    acres: str | None = None
    save_and_except: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)

    def abstract_key(self) -> str | None:
        if self.county and self.abstract:
            return f"TX|{self.county.upper()}|A-{self.abstract}"
        return None

    def survey_key(self) -> str | None:
        if not (self.county and (self.section or self.survey_no)):
            return None
        parts = ["TX", self.county.upper(), self.survey_code or (self.survey_name or "?").upper()]
        if self.block:
            parts.append(f"BLK {self.block.upper()}")
        if self.township:
            parts.append(f"T{self.township}")
        parts.append(f"SEC {self.section}" if self.section else f"SUR {self.survey_no}")
        return "|".join(parts)

    def survey_keys(self) -> list[str]:
        """One survey key per section named (``Sections 12 and 13`` gives two)."""
        first = self.survey_key()
        if not first or len(self.sections) < 2:
            return [first] if first else []
        stem = first.rsplit("|", 1)[0]
        return [f"{stem}|SEC {sec}" for sec in self.sections]


def _first(pattern: re.Pattern[str], text: str) -> str | None:
    m = pattern.search(text)
    return next((g for g in m.groups() if g), None) if m else None


def parse(text: str, county: str | None = None) -> TxDescription:
    """Parse one Texas description. ``county`` fills in when the text omits it."""
    d = TxDescription(text=text)
    clean = re.sub(r"\s+", " ", text)

    found = _COUNTY.search(clean)
    if found and found.group(1).upper() in COUNTIES:
        d.county = found.group(1).title()
    elif found:
        d.county = found.group(1).title()
        d.issues.append(f"county {d.county!r} isn't in the Permian county table")
    elif county:
        d.county = county.title()

    d.abstract = _first(_ABSTRACT_WORD, clean) or _first(_ABSTRACT_A, clean)

    for pattern, code, name in SURVEYS:
        if pattern.search(clean):
            d.survey_code, d.survey_name = code, name
            break
    if not d.survey_code:
        named = re.search(r"([A-Z][\w.&' -]{1,60}?)\s+(?:Survey|Sur\.)", clean)
        if named:
            d.survey_name = named.group(1).strip(" ,")

    sec = _SECTION.search(clean)
    if sec:
        d.sections = [x.replace(" ", "").replace("1/2", "½")
                      for x in re.split(r"\s*(?:,|and|&)\s*", sec.group(1)) if x.strip()]
        d.section = d.sections[0]
    if not d.section:
        d.survey_no = _first(_SURVEY_NO, clean)
    blk = _BLOCK.search(clean)
    d.block = blk.group(1).upper() if blk else None
    twp = _TOWNSHIP.search(clean)
    if twp:
        d.township = f"{twp.group(1)}{twp.group(2)[0].upper()}"

    # Aliquots are only the part before "Section"/"of"; save-and-except text is excluded.
    head = _SAVE.split(clean)[0] if _SAVE.search(clean) else clean
    for m in _ALIQUOT.finditer(head):
        if m.group(1):
            denom = m.group(2) or m.group(3) or ("2" if m.group(4) == "½" else "4")
            d.aliquots.append(f"{m.group(1).upper()}{denom}")
        else:
            denom = "2" if m.group(6).lower() == "half" else "4"
            d.aliquots.append(f"{_WORD_DIR[m.group(5).lower()]}{denom}")

    acres = _ACRES.search(head)
    d.acres = acres.group(1).replace(",", "") if acres else None
    d.save_and_except = [m.group(1).strip(" .,") for m in _SAVE.finditer(clean)]

    if not d.county:
        d.issues.append("no county")
    if not d.abstract:
        d.issues.append("no abstract number: look it up from the survey/block/section in the GLO Land Grant Database")
    if not (d.section or d.survey_no):
        d.issues.append("no section or survey number")
    if not (d.survey_code or d.survey_name):
        d.issues.append("no survey named")
    if d.save_and_except:
        d.issues.append("save-and-except carve-outs: the tract is less than the whole section")
    if len(d.sections) > 1 and d.aliquots:
        d.issues.append("several sections with aliquots: confirm which part of each section is described")
    return d
