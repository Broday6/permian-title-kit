"""Permian county reference data for search planning.

Coverage years are the start of each aggregator's published index or images,
collected Oct 1, 2026 from the vendors' county pages. Vendors keep scanning,
so re-check at project start. ``parents`` lists the counties that hold
records filed before the county was organized. Entries marked ``verify`` come
from county histories rather than a recording office, so confirm them before
relying on them.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field, replace
from pathlib import Path

# When the coverage years below were collected, and how. Re-check with
# `ptk counties --checklist` and feed confirmed years back with --overrides.
COLLECTED_ON = "2026-10-01"
COLLECTED_FROM = "TexasFile and CourthouseDirect county coverage pages, read through search-engine summaries"


@dataclass(frozen=True)
class Parent:
    county: str
    until: int  # search this parent for records filed before this year
    note: str = ""
    verify: bool = False


@dataclass(frozen=True)
class County:
    name: str
    state: str
    fips: str
    organized: int | None
    texasfile_index_from: int | None = None
    chd_images_from: int | None = None
    chd_historical_from: int | None = None
    portal: str = ""
    parents: tuple[Parent, ...] = field(default_factory=tuple)
    notes: str = ""


_C = County
_P = Parent

COUNTIES: dict[str, County] = {c.name.upper(): c for c in [
    _C("Reeves", "TX", "48389", 1884, 1874, 1885, 1876, "https://reeves.tx.publicsearch.us",
       (_P("Pecos", 1884),), "Holds Loving County filings 1897-1931."),
    _C("Loving", "TX", "48301", 1931, 1905, 1998, 1905,
       "https://lovingtx.countygovernmentrecords.com/LovingTXRecorder/web/",
       (_P("Reeves", 1931, "Loving was disorganized 1897-1931"), _P("Tom Green", 1893, verify=True))),
    _C("Ward", "TX", "48475", 1892, 1935, 1873, 1873, "https://ward.tx.publicsearch.us",
       (_P("Tom Green", 1892, verify=True),), "CourthouseDirect title plant from 1873."),
    _C("Winkler", "TX", "48495", 1910, 1883, 1965, 1883, "Tyler Self-Service (EagleWeb)",
       (_P("Ward", 1910, verify=True),)),
    _C("Pecos", "TX", "48371", 1875, 1862, None, 1884,
       "https://kofilequicklinks.com/pecos/ (1884-1983); Tyler EagleWeb (1983-)",
       (_P("Presidio", 1875),)),
    _C("Culberson", "TX", "48109", 1911, 1854, None, 1882, "https://kofilequicklinks.com/culberson/",
       (_P("El Paso", 1911),), "Index starts before the county existed: transcribed El Paso records."),
    _C("Midland", "TX", "48329", 1885, 1884, 1979, 1884, "https://www.co.midland.tx.us/175/County-Clerk",
       (_P("Tom Green", 1885),)),
    _C("Martin", "TX", "48317", 1884, 1915, 1876, 1883, "https://martinclerk.com/252/Official-Records-Search",
       (_P("Howard", 1884, "attachment before organization", verify=True),)),
    _C("Howard", "TX", "48227", 1882, 1882, 1983, 1881, ""),
    _C("Glasscock", "TX", "48173", 1893, 1893, 1918, 1877, "",
       (_P("Tom Green", 1893, verify=True),)),
    _C("Reagan", "TX", "48383", 1903, 1903, 2010, 1886, "",
       (_P("Tom Green", 1903),), "CourthouseDirect images only 2010-2016."),
    _C("Upton", "TX", "48461", 1910, 1984, 1900, 1887, "https://i2j.uslandrecords.com/TX/Upton/D/default.aspx",
       (_P("Midland", 1910, verify=True),)),
    _C("Andrews", "TX", "48003", 1910, 1881, 1914, 1910, "", (), "CourthouseDirect title plant from 1914."),
    _C("Ector", "TX", "48135", 1891, 1887, 1958, 1888, "",
       (_P("Midland", 1891),)),
    _C("Crane", "TX", "48103", 1927, 1888, 2012, 1888,
       "https://cranetx.countygovernmentrecords.com/CraneTX/web/login.jsp",
       (_P("Tom Green / Midland / Ector / Ward", 1927, "check all four", verify=True),)),
    _C("Crockett", "TX", "48105", 1891, 1982, None, None, "iDocket (subscription)",
       (), "Little online before 1982: plan courthouse or plant time."),
    _C("Irion", "TX", "48235", 1889, 1983, 1983, None, "",
       (_P("Tom Green", 1889),), "Biggest online gap: nothing found online before 1983."),
    _C("Dawson", "TX", "48115", 1905, 1970, 1991, 1900, ""),
    _C("Borden", "TX", "48033", 1891, 1877, None, 1880, ""),
    _C("Gaines", "TX", "48165", 1905, 1881, 2010, 1881, "", (),
       "Deed vols 1-8 (1881-1916) carry a basic index only."),
    _C("Eddy", "NM", "35015", 1889, None, 1906, None, "https://eddy.nm.publicsearch.us"),
    _C("Lea", "NM", "35025", 1917, None, 1988, None, "http://liveweb.leacounty-nm.org/",
       (_P("Chaves", 1917, "north part of the county"), _P("Eddy", 1917, "south part of the county"))),
    _C("Chaves", "NM", "35005", 1889, None, None, None, "https://www.chavescounty.gov/how-do-i/find",
       (), "County search covers 1987 forward."),
    _C("Roosevelt", "NM", "35041", 1903, None, None, None,
       "https://rooseveltcountynm-web.tylerhost.net/recorder/web/"),
]}


def verification_urls(c: County) -> dict[str, str]:
    slug = c.name.lower().replace(" ", "-")
    state_path = "Texas" if c.state == "TX" else "NewMexico"
    urls = {"courthousedirect": f"https://www.courthousedirect.com/PropertySearch/{state_path}/{c.name.replace(' ', '')}"}
    if c.state == "TX":
        urls["texasfile"] = f"https://www.texasfile.com/texas-land-records-coverage/{slug}-county-clerk/"
    return urls


CHECKLIST_FIELDS = ["county", "state", "texasfile_index_from", "chd_images_from", "chd_historical_from",
                    "texasfile_url", "courthousedirect_url", "portal",
                    "confirmed_texasfile_index_from", "confirmed_chd_images_from", "confirmed_chd_historical_from",
                    "checked_by", "checked_on", "notes"]


def write_checklist(path: str | Path) -> Path:
    """A worksheet for re-verifying coverage years by hand. Fill the confirmed_* columns."""
    path = Path(path)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=CHECKLIST_FIELDS)
        w.writeheader()
        for c in COUNTIES.values():
            urls = verification_urls(c)
            w.writerow({"county": c.name, "state": c.state,
                        "texasfile_index_from": c.texasfile_index_from or "",
                        "chd_images_from": c.chd_images_from or "",
                        "chd_historical_from": c.chd_historical_from or "",
                        "texasfile_url": urls.get("texasfile", ""), "courthousedirect_url": urls["courthousedirect"],
                        "portal": c.portal})
    return path


def load_overrides(path: str | Path) -> dict[str, County]:
    """Apply confirmed years from a filled-in checklist; blank cells keep the shipped value."""
    table = dict(COUNTIES)
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            key = (row.get("county") or "").strip().upper()
            if key not in table:
                continue
            changes = {}
            for col, attr in (("confirmed_texasfile_index_from", "texasfile_index_from"),
                              ("confirmed_chd_images_from", "chd_images_from"),
                              ("confirmed_chd_historical_from", "chd_historical_from")):
                value = (row.get(col) or "").strip()
                if value:
                    changes[attr] = int(value)
            if changes:
                table[key] = replace(table[key], **changes)
    return table


def lookup(name: str, table: dict[str, County] | None = None) -> County:
    table = table or COUNTIES
    key = name.strip().upper().removesuffix(" COUNTY")
    if key not in table:
        raise KeyError(f"{name!r} isn't in the county table: {', '.join(sorted(c.title() for c in table))}")
    return table[key]


def plan_search(county: str, start_year: int, table: dict[str, County] | None = None) -> list[str]:
    """Plain-language search plan for a window starting in ``start_year``."""
    c = lookup(county, table)
    steps: list[str] = []
    if c.texasfile_index_from:
        if start_year < c.texasfile_index_from:
            steps.append(
                f"TexasFile's full index for {c.name} starts {c.texasfile_index_from}. "
                f"For {start_year}-{c.texasfile_index_from - 1}, use "
                + (f"CourthouseDirect historical books (from {c.chd_historical_from})"
                   if c.chd_historical_from and c.chd_historical_from <= start_year
                   else "the courthouse or a local abstract plant")
                + "."
            )
        else:
            steps.append(f"TexasFile full index covers {c.name} from {c.texasfile_index_from}: name searches online.")
    if c.chd_images_from:
        steps.append(f"CourthouseDirect images for {c.name} start {c.chd_images_from}.")
    if c.portal:
        steps.append(f"County portal: {c.portal}.")
    for p in c.parents:
        if start_year < p.until:
            extra = f" ({p.note})" if p.note else ""
            check = " Confirm this attachment before relying on it." if p.verify else ""
            steps.append(f"Search {p.county} County for anything filed before {p.until}{extra}.{check}")
    if c.notes:
        steps.append(c.notes)
    if c.state == "NM":
        steps.append("New Mexico: no county mineral appraisal roll. Pull BLM MLRS and NMSLO status for every section.")
    if table is None or table.get(c.name.upper()) is COUNTIES.get(c.name.upper()):
        steps.append(f"Coverage years as collected {COLLECTED_ON}; confirm on the vendor pages before you promise a date.")
    return steps
