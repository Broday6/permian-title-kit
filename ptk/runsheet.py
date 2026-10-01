"""Runsheet template, file-naming key, and red-flag checks.

One row per recorded instrument. The checks are the red-flag table from the
playbook turned into rules. A rule *raises* a question with a severity; it
never answers it.

Severity:
    stop    the row can't be relied on until fixed (no image, unreadable fraction)
    review  a person must look (double fraction, term interest, capacity)
    info    a reminder that changes what to search next
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .dates import DateError, parse_date
from .fracs import fmt, read_fraction_text

COLUMNS = [
    "row", "state", "county", "record_series", "volume", "page", "instrument_no",
    "instrument_type", "instrument_date", "filed_date", "grantor", "grantee",
    "legal_description", "interest_conveyed", "reservations_exceptions",
    "lease_royalty", "lease_primary_term", "depth", "acknowledged", "spouse_joined",
    "image_file", "notes", "reviewed_by", "reviewed_on",
]


@dataclass
class RowFlag:
    row: str
    code: str
    severity: str
    message: str


def image_key(state: str, county: str, series: str, volume: str = "", page: str = "",
              instrument_no: str = "", year: str = "") -> str:
    """Consistent image file name: TX-REEVES-OPR-2019-012345.pdf or TX-MIDLAND-DR-0512-0033.pdf."""
    base = f"{state.upper()}-{re.sub(r'[^A-Z]', '', county.upper())}-{re.sub(r'[^A-Z&]', '', series.upper()) or 'REC'}"
    digits = lambda v: re.sub(r"\D", "", v)  # noqa: E731
    if instrument_no:
        dated = re.match(r"^\s*(\d{4})\s*[-/]\s*(\d+)\s*$", instrument_no)
        if dated:
            year, instrument_no = year or dated.group(1), dated.group(2)
        return f"{base}-{year or 'YYYY'}-{digits(instrument_no).zfill(6)}.pdf"
    if volume and page:
        return f"{base}-{digits(volume).zfill(4)}-{digits(page).zfill(4)}.pdf"
    raise ValueError("need an instrument number, or a volume and page")


def _date(v: str) -> date | None:
    try:
        return parse_date(v)
    except DateError:
        return None


def _plus_years(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:  # Feb 29
        return d.replace(year=d.year + years, month=3, day=1)


_FRACTION_PAREN = re.compile(
    r"((?:an?\s+undivided\s+)?(?:[a-z]+[-\s]){0,3}?(?:half|halves|thirds?|fourths?|quarters?|fifths?|sixths?|eighths?|"
    r"ninths?|tenths?|twelfths?|sixteenths?|[a-z]+-?(?:second|fourth|eighth|sixth)s?|[a-z]+ieths?|hundredths?))\s*\(([^)]*\d[^)]*)\)",
    re.I,
)
RULES: list[tuple[str, str, re.Pattern[str], str]] = [
    ("DOUBLE_FRACTION", "review",
     re.compile(r"\d+\s*/\s*\d+\s*(?:th)?\s+of\s+(?:the\s+)?(?:usual\s+)?(?:an?\s+)?\d+\s*/\s*\d+|"
                r"\b(?:one|two|three)[-\s]\w+\s+of\s+(?:the\s+)?(?:usual\s+)?one[-\s]eighth", re.I),
     "Double fraction. Under Van Dyke (Tex. 2023) a 1/8 double fraction is presumed to mean the whole estate. Send to the examiner."),
    ("TERM_INTEREST", "review",
     re.compile(r"for\s+a\s+(?:period|term)\s+of\s+[\w\s()-]{1,30}?years|"
                r"\byears?\b[^.;]{0,100}?\b(?:as|so)\s+long\s+(?:thereafter|as)\b", re.I),
     "Term interest. Pull production for the whole term window to decide whether it expired."),
    ("BLANKET_CONVEYANCE", "review",
     re.compile(r"all\s+(?:of\s+)?(?:my|our|its|grantors?'?s?|the\s+grantors?'?)\b.{0,80}?\b(?:interest|lands?|minerals?)\b.{0,100}?\bin\s+[\w\s]{2,30}?count(?:y|ies)", re.I | re.S),
     "Conveys all of the grantor's interest in a county. Apply it to every tract the grantor owned there."),
    ("MOTHER_HUBBARD", "info",
     re.compile(r"strips?\s+(?:and|or)\s+gores?|any\s+and\s+all\s+other\s+lands?|adjacent\s+or\s+contiguous", re.I),
     "Mother Hubbard or strip-and-gore language. It usually reaches only small overlooked strips."),
    ("NPRI_LANGUAGE", "review",
     re.compile(r"non[-\s]?participating|without\s+the\s+(?:right|power)\s+to\s+(?:lease|execute)|executive\s+right|"
                r"bonus(?:es)?\s+(?:and|or)\s+(?:delay\s+)?rentals?", re.I),
     "NPRI or executive-right language. Record which mineral attributes each party holds."),
    ("POWER_OF_ATTORNEY", "review",
     re.compile(r"attorney[-\s]in[-\s]fact|by\s+power\s+of\s+attorney", re.I),
     "Signed under a power of attorney. Confirm it's recorded and covers this act (a Relinquishment Act lease needs express authority)."),
    ("FIDUCIARY_CAPACITY", "review",
     re.compile(r"\b(?:independent\s+)?(?:executor|executrix|administrator|administratrix|trustee|guardian|receiver)\b", re.I),
     "Signed in a fiduciary capacity. Confirm the letters, trust or court order, and the authority to convey minerals."),
    ("RELINQUISHMENT_ACT", "review",
     re.compile(r"relinquishment\s+act|mineral\s+classified", re.I),
     "Relinquishment Act land. Run the GLO checklist: approved form, certified copy filed, assignments within 90 days, State's half paid."),
    ("CORRECTION", "info",
     re.compile(r"\bcorrect(?:ion|ive)\b|re-?recorded", re.I),
     "Correction or re-recorded instrument. Tie it to the instrument it corrects."),
    ("TOP_LEASE", "review",
     re.compile(r"top\s+lease|subject\s+to\s+(?:the\s+)?(?:existing|prior|outstanding)\s+(?:oil\s+(?:and|&)\s+gas\s+)?lease", re.I),
     "Top lease or a lease subject to an existing lease. Determine when the base lease ended before relying on it."),
    ("DEPTH_LIMITED", "info",
     re.compile(r"\b(?:depths?|feet|ft\.?)\s+(?:below|above)|base\s+of\s+the\s+\w+|stratigraphic\s+equivalent|"
                r"from\s+the\s+surface\s+(?:of\s+the\s+earth\s+)?(?:down\s+)?to", re.I),
     "Depth limitation. Carry it as its own depth interval in the ledger."),
]
LIEN_TYPES = re.compile(r"deed\s+of\s+trust|mortgage|vendor'?s\s+lien|judgment|abstract\s+of\s+judgment|"
                        r"tax\s+lien|lis\s+pendens|mechanic|ucc|financing\s+statement", re.I)


def check_row(row: dict[str, str], images_dir: Path | None = None) -> list[RowFlag]:
    rid = row.get("row") or "?"
    state = (row.get("state") or "").strip().upper()
    itype = (row.get("instrument_type") or "").strip()
    text = " ".join(row.get(k) or "" for k in ("interest_conveyed", "reservations_exceptions", "notes", "legal_description"))
    flags: list[RowFlag] = []

    def add(code: str, sev: str, msg: str) -> None:
        flags.append(RowFlag(rid, code, sev, msg))

    image = (row.get("image_file") or "").strip()
    if not image:
        add("NO_IMAGE", "stop", "No image linked. If there's no image, the row hasn't been verified.")
    elif images_dir is not None and not (images_dir / image).exists():
        add("IMAGE_MISSING", "stop", f"Image {image} isn't in {images_dir}.")
    if not ((row.get("volume") and row.get("page")) or row.get("instrument_no")):
        add("NO_RECORDING_REF", "stop", "No volume/page or instrument number.")

    executed, filed = _date(row.get("instrument_date", "")), _date(row.get("filed_date", ""))
    for col, parsed in (("instrument_date", executed), ("filed_date", filed)):
        raw = (row.get(col) or "").strip()
        if raw and parsed is None:
            add("BAD_DATE", "stop", f"Can't read {col} {raw!r}. Use 1948-05-10 or 5/10/1948 (full year).")
    if executed and filed and filed < executed:
        add("FILED_BEFORE_EXECUTED", "review", "Filed before it was signed. Check both dates against the image.")

    for m in _FRACTION_PAREN.finditer(text):
        reading = read_fraction_text(f"{m.group(1)} ({m.group(2)})")
        if reading.consistent is False:
            add("WORD_NUMERAL_MISMATCH", "stop",
                f"'{m.group(0).strip()}': words say {fmt(reading.words_value)}, numerals say {fmt(reading.numeral_value)}.")

    # Deeds write "one-half (1/2) of the one-eighth (1/8)": match with and without the numerals.
    plain = re.sub(r"\s*\([^)]*\d[^)]*\)", "", text)
    for code, sev, pattern, message in RULES:
        if pattern.search(text) or pattern.search(plain) or (
            code == "FIDUCIARY_CAPACITY" and pattern.search(row.get("grantor") or "")
        ):
            add(code, sev, message)

    if re.search(r"quit\s*claim", itype + " " + text, re.I):
        add("QUITCLAIM", "info", "Quitclaim: no warranty, so Duhig doesn't apply, and the grantee generally can't be a bona fide purchaser.")
    if LIEN_TYPES.search(itype) and not re.match(r"\s*(?:partial\s+)?release", itype, re.I):
        add("LIEN_OR_LITIGATION", "review", "Lien, judgment or lis pendens. Search for the release or the outcome.")
    if (row.get("acknowledged") or "").strip().upper() in ("N", "NO"):
        add("UNACKNOWLEDGED", "review",
            "No acknowledgment. It may not give constructive notice (TX: cured by limitations after 2 years under CPRC §16.033; NM: not 'of record' under §14-8-4).")
    if state == "NM" and (row.get("spouse_joined") or "").strip().upper() in ("N", "NO"):
        add("SPOUSE_NOT_JOINED", "stop", "New Mexico: a conveyance of community real property without both spouses is void (NMSA 40-3-13).")

    if re.search(r"heirship", itype, re.I):
        if state == "TX" and filed:
            prima = _plus_years(filed, 5)
            status = "now prima facie evidence" if prima <= date.today() else f"prima facie evidence from {prima.isoformat()}"
            add("AFFIDAVIT_OF_HEIRSHIP", "info", f"Texas affidavit of heirship: {status} (Est. Code §203.001). Record it in every county with minerals.")
        elif state == "NM":
            add("AFFIDAVIT_OF_HEIRSHIP", "review", "New Mexico examiners generally want probate. Treat the affidavit as supporting evidence only.")
    if re.search(r"small\s+estate", itype, re.I):
        add("SMALL_ESTATE_AFFIDAVIT", "stop",
            "A small estate affidavit doesn't pass non-homestead minerals (TX) or real property (NM). Get probate or a heirship judgment.")
    if re.search(r"\blease\b", itype, re.I) and not re.search(r"release|assign|ratif|memorand", itype, re.I):
        if not (row.get("lease_royalty") or "").strip():
            add("LEASE_ROYALTY_MISSING", "review", "Lease with no royalty abstracted.")
        if not (row.get("lease_primary_term") or "").strip():
            add("LEASE_TERM_MISSING", "review", "Lease with no primary term abstracted.")
    return flags


def check_file(path: str | Path, images_dir: str | Path | None = None) -> tuple[list[dict[str, str]], list[RowFlag]]:
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    missing = [c for c in ("row", "state", "county", "instrument_type", "grantor", "grantee") if rows and c not in rows[0]]
    flags: list[RowFlag] = []
    if missing:
        flags.append(RowFlag("-", "MISSING_COLUMNS", "stop", f"Runsheet is missing columns: {', '.join(missing)}"))
    img = Path(images_dir) if images_dir else None
    for row in rows:
        flags.extend(check_row(row, img))
    return rows, flags


def write_template(path: str | Path) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerow(COLUMNS)
