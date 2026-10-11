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
     re.compile(r"\d+\s*/\s*\d+\s*(?:th)?\s+(?:interest\s+)?(?:of|in\s+and\s+to)\s+(?:the\s+)?(?:usual\s+)?(?:an?\s+)?"
                r"(?!(\d+)\s*/\s*\1\b)\d+\s*/\s*\d+|"
                r"\b(?:one|two|three)[-\s]\w+\s+(?:interest\s+)?(?:of|in\s+and\s+to)\s+(?:the\s+)?(?:usual\s+)?one[-\s]eighth", re.I),
     "Double fraction. Under Van Dyke (Tex. 2023) a 1/8 double fraction in an older instrument is presumed to mean the whole estate; "
     "the rest of the instrument can rebut it (Clifton v. Johnson, Tex. 2026). Send to the examiner."),
    ("TERM_INTEREST", "review",
     re.compile(r"for\s+a\s+(?:period|term)\s+of\s+[\w\s()-]{1,30}?years|"
                r"\byears?\b[^.;]{0,100}?\b(?:as|so)\s+long\s+(?:thereafter|as)\b", re.I),
     "Term interest. Pull production for the whole term window to decide whether it expired."),
    ("BLANKET_CONVEYANCE", "review",
     re.compile(r"all\s+(?:of\s+)?(?:my|our|its|grantors?'?s?|the\s+grantors?'?)\b.{0,80}?\b(?:interest|lands?|minerals?)\b.{0,100}?\bin\s+[\w\s]{2,30}?count(?:y|ies)|"
                r"\b(?:owned|held|claimed)\s+by\s+(?:the\s+)?grantors?\b.{0,60}?\bin\s+[\w\s]{2,30}?count(?:y|ies)|"
                r"whether\s+or\s+not\s+(?:\w+\s+)?described", re.I | re.S),
     "Conveys all of the grantor's interest in a county. Apply it to every tract the grantor owned there."),
    ("MOTHER_HUBBARD", "info",
     re.compile(r"strips?\s+(?:and|or)\s+gores?|any\s+and\s+all\s+other\s+lands?|adjacent\s+or\s+contiguous", re.I),
     "Mother Hubbard or strip-and-gore language. It usually reaches only small overlooked strips."),
    ("NPRI_LANGUAGE", "review",
     re.compile(r"non[-\s]?participating|\bN\.?P\.?R\.?I\b|non[-\s]?executive|without\s+the\s+(?:right|power)\s+to\s+(?:lease|execute)|executive\s+right|"
                r"bonus(?:es)?\s+(?:and|or)\s+(?:delay\s+)?rentals?", re.I),
     "NPRI, non-executive or executive-right language. Record which mineral attributes each party holds."),
    ("POWER_OF_ATTORNEY", "review",
     re.compile(r"attorney[-\s]in[-\s]fact|by\s+power\s+of\s+attorney", re.I),
     "Signed under a power of attorney. Confirm it's recorded and covers this act (a Relinquishment Act lease needs express authority)."),
    ("FIDUCIARY_CAPACITY", "review",
     re.compile(r"\b(?:independent\s+)?(?:executor|executrix|administrator|administratrix|trustee|guardian|receiver|conservator|"
                r"personal\s+representative|admx|admr|adm'?x|exrx|extx|exr)\b", re.I),
     "Signed in a fiduciary capacity. Confirm the letters, trust or court order, and the authority to convey minerals."),
    ("RELINQUISHMENT_ACT", "review",
     re.compile(r"relinquishment\s+act|mineral\s+classified", re.I),
     "Relinquishment Act land (Tex. Nat. Res. Code §§52.171-.190). Run the GLO checklist: approved form, certified copy filed, "
     "assignments within 90 days, State's half paid. The examiner confirms."),
    ("CORRECTION", "info",
     re.compile(r"\bcorrect(?:ion|ive)\b|re-?recorded", re.I),
     "Correction or re-recorded instrument. Tie it to the instrument it corrects."),
    ("TOP_LEASE", "review",
     re.compile(r"top\s+lease|subject\s+to\s+(?:the\s+)?(?:existing|prior|outstanding)\s+(?:oil\s+(?:and|&)\s+gas\s+)?lease", re.I),
     "Top lease or a lease subject to an existing lease. Determine when the base lease ended before relying on it."),
    ("DEPTH_LIMITED", "info",
     re.compile(r"\b(?:depths?|feet|ft\.?)\s+(?:below|above)|base\s+of\s+the\s+\w+|stratigraphic\s+equivalent|"
                r"from\s+the\s+surface\s+(?:of\s+the\s+earth\s+)?(?:down\s+)?to|"
                r"\b(?:below|above|beneath|deeper\s+than|shallower\s+than)\s+(?:a\s+(?:depth|point)\s+of\s+)?\d[\d,]*\s*(?:feet|ft\b\.?|')|"
                r"\bformations?\b|\b(?:zone|horizon|interval)\s+only\b", re.I),
     "Depth limitation. Carry it as its own depth interval in the ledger."),
]
# A match that sits in a recital of something else isn't the instrument's own term: the lease's
# primary term or a recited lease ("subject to that certain lease ... for a term of five years"),
# the trustee named in a deed of trust, or a specific tract that happens to name its county.
_RECITAL = {
    "TERM_INTEREST": re.compile(r"\b(?:lease|primary\s+term)\b[^;]{0,150}$", re.I),
    "FIDUCIARY_CAPACITY": re.compile(r"\b(?:deed\s+of\s+trust|mortgage|lien)\b[^;]{0,60}$", re.I),
}
_SPECIFIC_TRACT = re.compile(r"\b(?:section|sec\.|survey|abstract|block|lot|tract)\b|\b(?:[NS][EW]?|[EW])\s*/\s*[24]\b", re.I)
_TOP_LEASE_RECITAL = re.compile(r"subject\s+to\s+(?:that\s+certain|the|an?|any)\s+(?:[\w,&-]+\s+){0,6}?lease\b", re.I)
_LEASE_TYPE = re.compile(r"\blease\b", re.I)
_NOT_A_LEASE = re.compile(r"release|assign|ratif|memorand|amend|extension", re.I)


def _hit(code: str, pattern: re.Pattern[str], text: str) -> bool:
    for m in pattern.finditer(text):
        recital = _RECITAL.get(code)
        if recital and recital.search(text[max(0, m.start() - 200):m.start()]):
            continue
        if code == "BLANKET_CONVEYANCE" and not m.group(0).lower().startswith("whether") and _SPECIFIC_TRACT.search(m.group(0)):
            continue
        return True
    return False


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
    is_lease = bool(_LEASE_TYPE.search(itype)) and not _NOT_A_LEASE.search(itype)
    for code, sev, pattern, message in RULES:
        if code == "TERM_INTEREST" and is_lease:
            continue  # a lease's primary term and habendum aren't a term interest
        hit = _hit(code, pattern, text) or _hit(code, pattern, plain)
        if code == "FIDUCIARY_CAPACITY":
            hit = hit or bool(pattern.search(row.get("grantor") or ""))
        elif code in ("TOP_LEASE", "CORRECTION"):
            hit = hit or bool(pattern.search(itype))
        if code == "TOP_LEASE" and is_lease:
            hit = hit or bool(_TOP_LEASE_RECITAL.search(text))  # a new lease made subject to a prior one
        if hit:
            add(code, sev, message)

    if re.search(r"quit\s*claim", itype + " " + text, re.I):
        add("QUITCLAIM", "info", "Quitclaim: no warranty, so Duhig doesn't apply."
            + (" In Texas the grantee generally can't claim bona fide purchaser status." if state == "TX" else ""))
    if LIEN_TYPES.search(itype) and not re.search(r"release|satisf|discharg|cancel|reconvey|withdraw", itype, re.I):
        add("LIEN_OR_LITIGATION", "review", "Lien, judgment or lis pendens. Search for the release or the outcome.")
    if (row.get("acknowledged") or "").strip().upper() in ("N", "NO"):
        add("UNACKNOWLEDGED", "review",
            "No acknowledgment. It may not give constructive notice (TX: may be cured under CPRC §16.033(a)(8) and (c) once of record "
            "2 years, the examiner confirms; NM: not 'of record' under §14-8-4).")
    if state == "NM" and (row.get("spouse_joined") or "").strip().upper() in ("N", "NO"):
        add("SPOUSE_NOT_JOINED", "stop", "New Mexico: if this is community real property, a conveyance without both spouses is void "
            "(NMSA 40-3-13), unless a recorded power of attorney (§47-1-7) or the spouse's written ratification covers it.")

    if re.search(r"heirship", itype, re.I):
        if state == "TX" and filed:
            prima = _plus_years(filed, 5)
            status = "now prima facie evidence" if prima <= date.today() else f"prima facie evidence from {prima.isoformat()}"
            add("AFFIDAVIT_OF_HEIRSHIP", "info", f"Texas affidavit of heirship: {status} (Est. Code §203.001); it's evidence, not a cure. "
                "Record it in every county with minerals and in the decedent's domicile county.")
        elif state == "NM":
            add("AFFIDAVIT_OF_HEIRSHIP", "review", "New Mexico examiners generally want probate. Treat the affidavit as supporting evidence only.")
    if re.search(r"small\s+estate", itype, re.I):
        add("SMALL_ESTATE_AFFIDAVIT", "stop",
            "A small estate affidavit passes only homestead real property in Texas (Est. Code §205.006) and personal property or a residence "
            "in New Mexico (§§45-3-1201, 45-3-1205), not these minerals. Get probate or a heirship judgment.")
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
