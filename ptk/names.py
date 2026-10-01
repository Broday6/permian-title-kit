"""Name variants for grantor/grantee index searches.

Old Texas and New Mexico indexes misindex constantly: "Jno. W. Smith", "J. W.
Smith et ux", "John William Smith", "Mrs. J. W. Smith" and "Smith, John W.,
Estate" may all be the same owner. ``search_variants`` turns one name into the
ordered list of index searches a careful abstractor would run, most specific
first, plus a Soundex key for blocking candidate matches across misspellings.

Nothing here decides that two names are the same person. That takes evidence
(the same tract, a spouse, a recital in a later deed), and a person signs off.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

_GROUPS = [
    "william wm will willie bill billy", "john jno jack johnny", "charles chas charlie chuck",
    "james jas jim jimmy jimmie", "thomas thos tom tommy", "george geo", "samuel saml sam",
    "benjamin benj ben", "robert robt bob bobby rob", "edward edw ed eddie ned", "joseph jos joe",
    "richard richd dick rick", "alexander alex alexr", "andrew andw andy", "daniel danl dan",
    "frederick fredk fred", "henry hy hank harry", "nathaniel nathl nat", "lawrence larry",
    "albert al bert", "walter walt", "harold hal", "leonard len", "douglas doug", "kenneth ken",
    "elizabeth eliz betty beth bess bessie lizzie eliza", "margaret margt maggie peggy marge",
    "mary mollie molly polly mamie", "sarah sallie sally sadie", "catherine katherine kate kitty cathy",
    "martha mattie patsy", "ann anna annie nancy", "susan sue susie", "virginia jennie ginny",
    "dorothy dot dottie", "patricia pat", "frances fannie", "francis frank", "eleanor nell nellie",
    "jose", "maria ma", "guadalupe lupe", "jesus chuy", "francisco pancho fco", "ignacio nacho",
]
_NICK: dict[str, set[str]] = {}
for _g in _GROUPS:
    _names = _g.split()
    for _n in _names:
        _NICK.setdefault(_n, set()).update(x for x in _names if x != _n)

_SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}
_ORG_SUFFIX = {
    "co": "COMPANY", "company": "COMPANY", "corp": "CORPORATION", "corporation": "CORPORATION",
    "inc": "INC", "incorporated": "INC", "llc": "LLC", "l l c": "LLC", "ltd": "LTD", "limited": "LTD",
    "lp": "LP", "l p": "LP", "lc": "LC", "pc": "PC",
}
_ORG_WORDS = re.compile(
    r"\b(company|co|corp|corporation|inc|incorporated|llc|ltd|limited|lp|partnership|partners|oil|gas|"
    r"energy|petroleum|resources|minerals|royalt(?:y|ies)|operating|production|exploration|bank|ranch|"
    r"properties|holdings|investments|fund|association|church|university|state of|united states)\b",
    re.I,
)
_CAPACITY = re.compile(
    r",?\s*\b(?:as\s+)?(independent\s+executor|independent\s+executrix|executor|executrix|administrator|"
    r"administratrix|trustee|successor\s+trustee|attorney[-\s]in[-\s]fact|guardian|personal\s+representative)\b.*$",
    re.I,
)
_ET = re.compile(r"\bet\s*(ux|vir|al|ux\.?|vir\.?|al\.?)\b\.?", re.I)


@dataclass
class ParsedName:
    raw: str
    kind: str  # person | organization | trust | estate
    surname: str = ""
    given: list[str] = field(default_factory=list)
    suffix: str = ""
    org_core: str = ""
    capacity: str = ""
    et: str = ""  # ux | vir | al
    notes: list[str] = field(default_factory=list)


def ascii_upper(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", folded).strip().upper()


def soundex(word: str) -> str:
    """American Soundex, for blocking surname candidates across misspellings."""
    w = re.sub(r"[^A-Z]", "", ascii_upper(word))
    if not w:
        return ""
    codes = {**dict.fromkeys("BFPV", "1"), **dict.fromkeys("CGJKQSXZ", "2"), **dict.fromkeys("DT", "3"),
             "L": "4", **dict.fromkeys("MN", "5"), "R": "6"}
    out, last = w[0], codes.get(w[0], "")
    for ch in w[1:]:
        code = codes.get(ch, "")
        if code and code != last:
            out += code
        if ch not in "HW":
            last = code
    return (out + "000")[:4]


def parse_name(raw: str) -> ParsedName:
    text = raw.strip()
    et = _ET.search(text)
    et_kind = et.group(1).lower().strip(".") if et else ""
    text = _ET.sub(" ", text)
    cap = _CAPACITY.search(text)
    capacity = cap.group(1).lower() if cap else ""
    acting_for = ""
    if cap:
        after = re.search(r"\bof\s+(?:the\s+)?(.+)$", cap.group(0)[len(cap.group(1)) :].strip(" ,"), re.I)
        acting_for = after.group(1).strip(" ,.") if after else ""
    text = _CAPACITY.sub("", text).strip(" ,")

    if re.search(r"\btrust\b", text, re.I):
        core = re.sub(r"^THE\s+", "", ascii_upper(re.sub(r"[.,]", " ", text)))
        return ParsedName(raw, "trust", org_core=core, capacity=capacity, et=et_kind)
    heirs = re.match(r"^(?:the\s+)?(?:unknown\s+)?heirs(?:\s+at\s+law)?\s+of\s+(.+)$", text, re.I)
    if heirs:
        inner = parse_name(heirs.group(1))
        inner.kind, inner.raw = "heirs", raw
        inner.notes.append("heirs of: identify each heir by name, then search the decedent's probate and affidavits of heirship")
        return inner
    estate = re.match(r"^(?:the\s+)?estate\s+of\s+(.+)$", text, re.I) or re.match(r"^(.+?),?\s+estate$", text, re.I)
    if estate:
        inner = parse_name(estate.group(1))
        inner.kind, inner.raw = "estate", raw
        inner.notes.append("estate: search the decedent's probate in the county of domicile too")
        return inner
    if _ORG_WORDS.search(text):
        full = ascii_upper(re.sub(r"[,&]", " ", text.replace(".", "")))
        words = full.split()
        if words and words[0] == "THE":
            words = words[1:]
        while len(words) > 1 and words[-1].lower() in _ORG_SUFFIX:
            words.pop()
        p = ParsedName(raw, "organization", org_core=" ".join(words), capacity=capacity, et=et_kind)
        p.notes.append(f"full name as written: {full}")
        return p

    # Person. Accept "Smith, John W." or "John W. Smith" (and "Mrs. John W. Smith").
    t = ascii_upper(text).replace(".", " ")
    mrs = bool(re.match(r"^MRS\b", t))
    t = re.sub(r"^(MRS|MR|MISS|MS|DR)\s+", "", t)
    parts = [x.strip() for x in t.split(",") if x.strip()]
    trailing_suffix = ""
    if len(parts) > 1 and parts[-1].lower() in _SUFFIXES:
        trailing_suffix = parts.pop()
    if len(parts) > 1:  # "Smith, John W" (index order)
        surname = parts[0]
        tokens = " ".join(parts[1:]).split() + ([trailing_suffix] if trailing_suffix else [])
    else:  # "John W Smith" or "John W Smith, Jr"
        tokens = parts[0].split() if parts else []
        surname = tokens.pop() if tokens else ""
        if surname.lower() in _SUFFIXES and tokens:
            tokens.append(surname)
            surname = tokens.pop(-2)
        if trailing_suffix:
            tokens.append(trailing_suffix)
    suffix = next((x for x in tokens if x.lower() in _SUFFIXES), "")
    given = [x for x in tokens if x.lower() not in _SUFFIXES]
    p = ParsedName(raw, "person", surname=surname, given=given, suffix=suffix, capacity=capacity, et=et_kind)
    if acting_for:
        p.notes.append(f"signed for {acting_for}: search that party too")
    if mrs:
        p.notes.append("'Mrs.' with a husband's given name: search the wife's own given name too")
    if suffix:
        p.notes.append("Jr./Sr. shift when the senior dies: search without the suffix too")
    return p


def _given_variants(given: list[str]) -> list[list[str]]:
    if not given:
        return [[]]
    first, rest = given[0], given[1:]
    initials_rest = [g[0] for g in rest]
    forms: list[list[str]] = [given, [first] + initials_rest]
    if len(first) > 1:
        forms.append([first[0]] + initials_rest)
    forms.append([first])
    for nick in sorted(_NICK.get(first.lower(), ())):
        forms.append([nick.upper()] + initials_rest)
        forms.append([nick.upper()])
    forms.append([first[0]])
    seen, out = set(), []
    for f in forms:
        key = tuple(f)
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


_SPOUSE_SPLIT = re.compile(r",?\s+(?:and\s+(?:his\s+)?wife|and\s+(?:her\s+)?husband|et\s*ux|et\s*vir)\.?,?\s+(?=[A-Z])", re.I)


def split_parties(raw: str) -> list[str]:
    """'John Smith and wife, Mary Smith' -> ['John Smith', 'Mary Smith']. A bare 'et ux' stays attached."""
    parts = [p.strip(" ,") for p in _SPOUSE_SPLIT.split(raw.strip()) if p.strip(" ,")]
    return parts or [raw]


def search_variants(raw: str) -> dict[str, object]:
    """Index searches to run for a grantor or grantee field, most specific first.

    A field naming a couple ("John Smith and wife, Mary Smith") is split, and
    the searches for each person are returned together under ``parties``.
    """
    parties = split_parties(raw)
    if len(parties) > 1:
        each = [_variants_one(p) for p in parties]
        notes = ["joint grantors or grantees: search every person named, and check community property"]
        return {
            "input": raw,
            "kind": "multiple",
            "surname_soundex": each[0]["surname_soundex"],
            "searches": list(dict.fromkeys(s for v in each for s in v["searches"])),
            "notes": notes + [n for v in each for n in v["notes"]],
            "parties": each,
        }
    return _variants_one(raw)


def _variants_one(raw: str) -> dict[str, object]:
    p = parse_name(raw)
    searches: list[str] = []
    if p.kind in ("person", "estate", "heirs") and p.surname:
        for g in _given_variants(p.given):
            searches.append(" ".join([p.surname, *g]).strip())
            if p.suffix:
                searches.append(" ".join([p.surname, *g, p.suffix]).strip())
        searches.append(f"{p.surname} *")
        if p.kind == "estate":
            searches.append(f"ESTATE OF {' '.join(p.given)} {p.surname}".replace("  ", " "))
    else:
        core = p.org_core
        full = next((n.split(": ", 1)[1] for n in p.notes if n.startswith("full name as written")), "")
        if full:
            searches.append(full)
        multiword = len(core.split()) > 1
        if multiword or p.kind == "trust":
            searches.append(core)
        if p.kind == "organization":
            for suffix in ("COMPANY", "CO", "CORPORATION", "CORP", "INC", "LLC", "LTD", "LP"):
                searches.append(f"{core} {suffix}")
        if multiword:
            searches.append(f"{core.split()[0]} *")
        if p.kind == "trust":
            p.notes.append("trust: get the trust instrument or a certificate of trust, and search each trustee by name")
        else:
            p.notes.append("entity: check the Secretary of State for mergers and name changes, then search predecessors")
    if p.capacity:
        p.notes.append(f"signed as {p.capacity}: confirm authority (letters, trust, recorded power of attorney)")
    if p.et == "ux":
        p.notes.append("et ux: the wife joined; search her name and check community property")
    elif p.et == "al":
        p.notes.append("et al: other grantors aren't named here; read the instrument for every party")
    dedup = list(dict.fromkeys(s for s in searches if s))
    return {
        "input": raw,
        "kind": p.kind,
        "surname_soundex": soundex(p.surname) if p.surname else "",
        "searches": dedup,
        "notes": p.notes,
    }
