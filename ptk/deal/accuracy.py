"""Accuracy checks on everything: ``ptk audit`` and the desk's Accuracy page.

Re-runnable at any time; it never changes data. Each check is PASS, WARN (look
at it) or FAIL (something is wrong and must be fixed before relying on it).

1. Engine     golden cases built into the package (the HANDOFF acceptance example,
              a leasehold chain, fractions, dates, rounding, deed words) recomputed
              on this install and compared exactly.
2. Records    the audit log's hash chain.
3. Deals      every deal's verification re-run in memory and compared with what's
              stored (stale when the title files changed); package and draft files
              compared with the SHA-256 recorded when they were drafted; open
              legal questions, overdue tasks, released funds that no longer pass
              the gate, title files that went missing.
4. CRM        stages, dates, money/acre/% fields, email addresses, links between
              owners and deals, attached files.
5. Rules      every flag code the program can raise has a rule; checklist ids are
              unique; every template a rule names exists and renders.
6. Setup      what can't be checked here (no API key, no OCR tools, no SMTP).
"""

from __future__ import annotations

import hashlib
import re
import shutil
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import date
from fractions import Fraction
from pathlib import Path
from typing import Any

from ..fracs import FractionError, fraction_words, parse_fraction, to_decimal

PASS, WARN, FAIL, INFO = "PASS", "WARN", "FAIL", "INFO"
AREAS = ("Engine", "Records", "Deals", "CRM", "Rules", "Setup")

# ---- golden cases (fictional; the HANDOFF section 8 example) --------------------------------

GOLDEN_TAKEOFF = """seq,instrument,recorded,tract,depth,estate,kind,grantor,grantees,shares,interest,warranty,npri_kind,npri_value,notes,npri_burdens
1,Patent Vol 12 Pg 301,1909-04-02,SEC12,ALL,MI,root,,Example Patentee,,all,,,,FICTIONAL,
2,DR Vol 40 Pg 118,1931-06-10,SEC12,ALL,MI,convey,Example Patentee,Adams Example,,all,Y,,,,
3,DR Vol 88 Pg 77,1948-02-15,SEC12,ALL,MI,convey,Adams Example,Baker Example,,1/2,Y,,,,
4,DR Vol 88 Pg 77,1948-02-15,SEC12,ALL,NPRI,reserve,Adams Example,,,,,fixed,1/16,,conveyed
5,Probate No. 1650 County Court,1962-09-01,SEC12,ALL,ALL,convey,Adams Example,Adams Heir One;Adams Heir Two;Adams Heir Three,,all,,,,,
6,DR Vol 140 Pg 22,1971-05-20,SEC12,ALL,MI,convey,Adams Heir One,Carter Example,,1/4,Y,,,,
7,DR Vol 141 Pg 9,1971-08-02,SEC12,ALL,MI,convey,Doe Stranger,Evans Example,,1/8,,,,,
10,Base: DOTO certified to 2015-01-01,2015-01-01,SEC13,ALL,MI,root,,Fox Example;Gray Example,1/2;1/2,all,,,,,
11,OPR 2018-004512,2018-03-14,SEC13,ALL,MI,convey,Gray Example,Hill Minerals LLC,,all,Y,,,,
"""
GOLDEN_PROJECT = {
    "state": "TX", "takeoff": "takeoff.csv", "produce_depth": "ALL", "pooled": True,
    "npri_ratified": {"Adams Heir One": True, "Adams Heir Two": True, "Adams Heir Three": False},
    "participation": {"weights": {"SEC12": 4800, "SEC13": 5200}},
    "leases": [
        {"id": "L-100", "tract": "SEC12", "lessors": ["Baker Example"], "royalty": "1/4",
         "lessees": {"Example Operating LLC": "1"}, "orris": {"Example ORRI Partners": "1/40"}},
        {"id": "L-101", "tract": "SEC12", "lessors": ["Adams Heir One", "Adams Heir Two", "Adams Heir Three", "Carter Example"],
         "royalty": "3/16", "lessees": {"Example Operating LLC": "3/4", "Example Non-Op LP": "1/4"}},
        {"id": "L-200", "tract": "SEC13", "lessors": ["Fox Example"], "royalty": "1/5", "lessees": {"Example Operating LLC": "1"}},
    ],
}
GOLDEN_DECK = {  # HANDOFF 8.2, by owner and type
    ("Example Operating LLC", "WI"): Fraction(17, 25), ("Example Operating LLC", "NRI"): Fraction(2113, 4000),
    ("Hill Minerals LLC", "UMI"): Fraction(13, 50), ("Hill Minerals LLC", "WI"): Fraction(13, 50),
    ("Example Non-Op LP", "WI"): Fraction(3, 50), ("Fox Example", "RI"): Fraction(13, 250),
    ("Example Non-Op LP", "NRI"): Fraction(39, 800), ("Baker Example", "RI"): Fraction(9, 200),
    ("Adams Heir Three", "RI"): Fraction(3, 200), ("Adams Heir Two", "RI"): Fraction(3, 200),
    ("Carter Example", "RI"): Fraction(3, 200), ("Example ORRI Partners", "ORRI"): Fraction(3, 500),
    ("Adams Heir One", "NPRI"): Fraction(1, 200), ("Adams Heir Three", "NPRI"): Fraction(1, 200),
    ("Adams Heir Two", "NPRI"): Fraction(1, 200),
}
GOLDEN_OWNERSHIP = {  # HANDOFF 8.1
    ("SEC12", "Adams Heir Three", "MI"): "1/6", ("SEC12", "Adams Heir Two", "MI"): "1/6",
    ("SEC12", "Baker Example", "MI"): "1/2", ("SEC12", "Carter Example", "MI"): "1/6",
    ("SEC12", "Adams Heir One", "NPRI"): "1/48", ("SEC13", "Fox Example", "MI"): "1/2",
    ("SEC13", "Hill Minerals LLC", "MI"): "1/2",
}
GOLDEN_LEASE_CHAIN = """seq,instrument,recorded,lease,depth,kind,assignor,assignees,shares,interest,warranty,orri_value,orri_burdens,royalty,notes
1,Lease,2014-03-01,L1,ALL,root,,Original Lessee,,all,,,,1/5,FICTIONAL
2,Assignment,2018-06-15,L1,ALL,assign,Original Lessee,Seller Co,,all,Y,,,,
3,Assignment,2018-06-15,L1,ALL,orri,Original Lessee,,,,,1/20,conveyed,,
4,Partial,2020-01-01,L1,ALL,assign,Seller Co,Partner Co,,1/2,,,,,
"""


@dataclass
class Check:
    area: str
    name: str
    status: str
    detail: str = ""
    items: list[str] = field(default_factory=list)


@dataclass
class Report:
    checks: list[Check]
    ran: str

    @property
    def counts(self) -> dict[str, int]:
        return {s: sum(1 for c in self.checks if c.status == s) for s in (PASS, WARN, FAIL, INFO)}

    @property
    def ok(self) -> bool:
        return not any(c.status == FAIL for c in self.checks)

    def to_dict(self) -> dict[str, Any]:
        return {"ran": self.ran, "counts": self.counts, "ok": self.ok, "checks": [asdict(c) for c in self.checks]}


def _expect(out: list[Check], area: str, name: str, cases: list[tuple[str, Any, Any]]) -> None:
    """cases: (label, got, want). PASS when every got == want."""
    bad = [f"{label}: got {got!r}, expected {want!r}" for label, got, want in cases if got != want]
    out.append(Check(area, name, FAIL if bad else PASS, f"{len(cases) - len(bad)} of {len(cases)} exact", bad))


# ---- 1. engine ---------------------------------------------------------------------------

def engine_checks() -> list[Check]:
    from ..dates import DateError, iso
    from ..doi import Lease, build_deck, participation
    from ..leasehold import load_lease_events, position_for, replay_leasehold
    from ..ledger import load_events, ownership_table, replay
    out: list[Check] = []
    tmp = Path(tempfile.mkdtemp(prefix="ptk-audit-"))
    try:
        (tmp / "takeoff.csv").write_text(GOLDEN_TAKEOFF, encoding="utf-8")
        result = replay(load_events(tmp / "takeoff.csv"))
        rows = {(r["tract"], r["owner"], r["estate"]): r["fraction"] for r in ownership_table(result)}
        _expect(out, "Engine", "Chain replay (HANDOFF 8.1)",
                [(f"{t} {o} {e}", rows.get((t, o, e)), want) for (t, o, e), want in GOLDEN_OWNERSHIP.items()]
                + [("flags", sorted({f.code for f in result.flags}), ["CHAIN_GAP", "DUHIG_CANDIDATE", "NPRI_REVIEW"])])
        p = GOLDEN_PROJECT
        deck = build_deck(result, [Lease.from_dict(d) for d in p["leases"]], participation(p["participation"]["weights"]),
                          state="TX", pooled=True, npri_ratified=p["npri_ratified"])
        by_owner = {(o, t): v for o, t, v in deck.by_owner()}
        cases = [(f"{o} {t}", by_owner.get((o, t)), want) for (o, t), want in GOLDEN_DECK.items()]
        cases.append(("revenue total", deck.revenue_total(), Fraction(1)))
        cases.append(("rows beyond the golden set", sorted(k for k in by_owner if k not in GOLDEN_DECK), []))
        _expect(out, "Engine", "Unit deck (HANDOFF 8.2)", cases)
        (tmp / "chain.csv").write_text(GOLDEN_LEASE_CHAIN, encoding="utf-8")
        lp = position_for(replay_leasehold(load_lease_events(tmp / "chain.csv")), "L1")
        _expect(out, "Engine", "Lease chain WI/NRI/ORRI", [
            ("Seller Co WI", lp.wi("Seller Co"), Fraction(1, 2)),
            ("Seller Co NRI", lp.nri("Seller Co"), Fraction(1, 2) * (1 - Fraction(1, 5) - Fraction(1, 20))),
            ("Partner Co NRI", lp.nri("Partner Co"), Fraction(1, 2) * (1 - Fraction(1, 5) - Fraction(1, 20))),
            ("ORRI of 8/8", lp.orri_held("Original Lessee"), Fraction(1, 20)),
        ])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    frac_cases = [("an undivided one-half (1/2)", Fraction(1, 2)), ("1/2 of 1/8", Fraction(1, 16)), ("12½%", Fraction(1, 8)),
                  ("three-sixteenths", Fraction(3, 16)), ("one sixty-fourth", Fraction(1, 64)), ("0.1875", Fraction(3, 16)),
                  ("18.75 percent", Fraction(3, 16)), ("1⁄8", Fraction(1, 8)), ("one hundred twenty-eighth", Fraction(1, 128))]
    cases = [(text, parse_fraction(text), want) for text, want in frac_cases]
    try:
        parse_fraction("one-fourth (1/8)")
        cases.append(("word/numeral mismatch refused", "accepted", "refused"))
    except FractionError:
        cases.append(("word/numeral mismatch refused", "refused", "refused"))
    _expect(out, "Engine", "Deed fractions", cases)

    date_cases = [(t, iso(t), want) for t, want in (("5/10/1948", "1948-05-10"), ("May 10, 1948", "1948-05-10"),
                                                     ("10 May 1948", "1948-05-10"), ("1948/05/10", "1948-05-10"))]
    try:
        iso("5/10/48")
        date_cases.append(("two-digit year refused", "accepted", "refused"))
    except DateError:
        date_cases.append(("two-digit year refused", "refused", "refused"))
    _expect(out, "Engine", "Recording dates", date_cases)

    _expect(out, "Engine", "8-place half-up rounding", [
        ("1/3", to_decimal(Fraction(1, 3)), "0.33333333"), ("2/3", to_decimal(Fraction(2, 3)), "0.66666667"),
        ("5e-9 rounds up", to_decimal(Fraction(5, 10**9)), "0.00000001"), ("-2/3", to_decimal(Fraction(-2, 3)), "-0.66666667"),
        ("1/128", to_decimal(Fraction(1, 128)), "0.00781250"), ("cents", to_decimal(Fraction(1, 200), 2), "0.01")])

    bad = []
    for d in range(2, 400):
        for n in range(1, min(d, 12)):
            v = Fraction(n, d)
            w = fraction_words(v)
            if w is not None and parse_fraction(w) != v:
                bad.append(f"{v}: '{w}' reads back as {parse_fraction(w)}")
    out.append(Check("Engine", "Deed words read back exactly", FAIL if bad else PASS,
                     "every fraction the drafts spell out reads back to the same value" if not bad else f"{len(bad)} mismatches",
                     bad[:20]))

    # Parsers against the hand-labelled corpus of record forms: a silently wrong reading fails,
    # a refusal or a flag that shouldn't fire is a warning.
    from .parser_accuracy import score_all
    results = score_all()
    misses = [r for r in results if r["verdict"] != "correct"]
    dangerous = [r for r in misses if r["risk"] == "dangerous"]
    out.append(Check("Engine", "Parsers read the labelled record forms", FAIL if dangerous else WARN if misses else PASS,
                     f"{len(results) - len(misses)} of {len(results)} correct"
                     + (f"; {len(dangerous)} silently wrong" if dangerous else ""),
                     [f"{r['id']} ({r['parser']}, {r['risk']}): got {r['actual']}" for r in misses][:30]))
    return out


# ---- 2-4. records, deals, CRM ----------------------------------------------------------------

def _sha(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def deal_checks(store, rules, today: date) -> list[Check]:
    from .state import attorney_tasks, fold, funds_gate
    from .verify import verify_asset
    out: list[Check] = []
    stale, missing_files, pkg_bad, draft_bad, overdue, gate_bad, open_legal, errors = ([] for _ in range(8))
    for did in store.deal_ids():
        st = fold(store.events(did))
        for a in st.assets:
            for key in ("takeoff", "runsheet", "lease_chain", "depths"):
                path = (a.get("title") or {}).get(key)
                if path and not Path(path).exists():
                    missing_files.append(f"{did} {a['id']}: {key} {path} is missing")
            stored = st.verify.get(a["id"])
            if stored is None:
                continue
            try:
                result, _specs, expected = verify_asset(st.record, a)
            except Exception as ex:  # an unreadable title file is reported, not raised
                errors.append(f"{did} {a['id']}: verification can't be re-run: {ex}")
                continue
            old = stored["result"]
            keys = ("status", "owned", "sold", "owned_wi", "owned_nri", "buyer_nri")
            diff = [f"{k} {old.get(k)} -> {result.get(k)}" for k in keys if old.get(k) != result.get(k)]
            exp_now = {e["key"]: e["decimal"] for e in expected}
            exp_then = {k: e["decimal"] for k, e in st.expected.items()
                        if e.get("asset_id") == a["id"] and not str(e.get("source", "")).startswith("entered by")}
            if exp_now != exp_then:
                diff.append(f"expected decimals {exp_then} -> {exp_now}")
            if diff:
                stale.append(f"{did} {a['id']}: " + "; ".join(diff) + f" (rerun: ptk deal verify {did})")
        drafted = st.package.get("drafted")
        if drafted:
            folder = Path(drafted["dir"])
            for f in drafted.get("files", []):
                now = _sha(folder / f["file"])
                if now != f["sha256"]:
                    signed = " after signing" if "signed" in st.package else ""
                    pkg_bad.append(f"{did}: {f['file']} {'is missing' if now is None else 'changed since it was drafted'}{signed}")
        for d in st.drafts:
            now = _sha(Path(d["file"]))
            if now != d.get("sha256"):
                draft_bad.append(f"{did} {d['task_id']}: {Path(d['file']).name} {'is missing' if now is None else 'was edited after drafting'}")
        for t in st.open_tasks():
            if t.due and t.due < today.isoformat() and t.severity in ("stop", "review"):
                overdue.append(f"{did} {t.task_id} [{t.code}] due {t.due} ({t.party_name or t.party})")
        if "released" in st.package:
            g = funds_gate(st, rules)
            if g:
                gate_bad.append(f"{did}: funds released on {st.package['released'].get('date')}, but now: " + "; ".join(g[:4]))
        open_legal += [f"{did} {t.task_id} [{t.code}]" for t in attorney_tasks(st) if not t.closed]
    n = len(store.deal_ids())
    out.append(Check("Deals", "Verification matches the title files", WARN if stale else PASS,
                     f"{n} deal(s) re-run in memory" if not stale else f"{len(stale)} result(s) are stale", stale))
    out.append(Check("Deals", "Verification can be re-run", FAIL if errors else PASS, "", errors))
    out.append(Check("Deals", "Title files are present", FAIL if missing_files else PASS, "", missing_files))
    out.append(Check("Deals", "Closing packages unchanged since drafting", FAIL if pkg_bad else PASS,
                     "SHA-256 of every package file against its manifest", pkg_bad))
    out.append(Check("Deals", "Curative drafts unchanged since drafting", WARN if draft_bad else PASS,
                     "edited drafts should be redrafted or noted", draft_bad))
    out.append(Check("Deals", "Released funds still pass the gate", FAIL if gate_bad else PASS, "", gate_bad))
    out.append(Check("Deals", "Open legal questions", WARN if open_legal else PASS,
                     "waiting on the examining attorney" if open_legal else "none open", open_legal))
    out.append(Check("Deals", "Overdue tasks", WARN if overdue else PASS, f"as of {today.isoformat()}", overdue[:50]))
    return out


_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def crm_checks(store, rules) -> list[Check]:
    from . import crm
    state = crm.load_state(store)
    customers = state["customers"]
    bad_fields, bad_status, bad_links, bad_files = [], [], [], []
    for c in customers:
        who = f"{c.get('id')} {c.get('name')}"
        if c.get("status") and c["status"] not in crm.STATUSES:
            bad_status.append(f"{who}: stage {c['status']!r}")
        for k in ("created", "followUp"):
            if c.get(k) and not _ISO.match(str(c[k])):
                bad_fields.append(f"{who}: {k} {c[k]!r} isn't YYYY-MM-DD")
        for k in ("value", "asking", "acres"):
            if c.get(k) not in (None, ""):
                try:
                    Fraction(str(c[k]).replace(",", ""))
                except (ValueError, ZeroDivisionError):
                    bad_fields.append(f"{who}: {k} {c[k]!r} isn't a number")
        if c.get("email") and not _EMAIL.match(c["email"].strip()):
            bad_fields.append(f"{who}: email {c['email']!r} doesn't look like an address")
        for i, cl in enumerate(c.get("calls") or [], start=1):
            if cl.get("date") and not _ISO.match(str(cl["date"])):
                bad_fields.append(f"{who}: call {i} date {cl['date']!r}")
        for it in c.get("interests") or []:
            if it.get("pct"):
                try:
                    parse_fraction(str(it["pct"]))
                except FractionError:
                    bad_fields.append(f"{who}: holding {it.get('well') or it.get('caseId')}: % {it['pct']!r} can't be read")
        for d in c.get("documents") or []:
            try:
                crm.file_path(store, d.get("id", ""))
            except KeyError:
                bad_files.append(f"{who}: attachment {d.get('name')} ({d.get('id')}) is missing")
    ids = {c["id"] for c in customers}
    for cid, deals in crm.deals_by_customer(store, rules).items():
        if cid not in ids:
            bad_links.append(f"{', '.join(d['deal_id'] for d in deals)}: CRM owner {cid} no longer exists")
    return [
        Check("CRM", "Owner stages are valid", FAIL if bad_status else PASS, f"{len(customers)} owner(s)", bad_status),
        Check("CRM", "Dates, numbers, % and email addresses read cleanly", WARN if bad_fields else PASS, "", bad_fields),
        Check("CRM", "Owners and title deals link up", FAIL if bad_links else PASS, "", bad_links),
        Check("CRM", "Attached files are present", FAIL if bad_files else PASS, "", bad_files),
    ]


# ---- 5. rules ---------------------------------------------------------------------------

_CODE_PATTERNS = [re.compile(p) for p in (r'TaskSpec\(\s*"([A-Z][A-Z0-9_]+)"', r'Flag\(\s*"([A-Z][A-Z0-9_]+)"',
                                          r'\badd\(\s*"([A-Z][A-Z0-9_]+)"', r'code="([A-Z][A-Z0-9_]+)"',
                                          r'"(DO_DECIMAL_MISMATCH|PAY_DECIMAL_MISMATCH)"')]
_RUNSHEET_RULE = re.compile(r'\(\s*"([A-Z][A-Z0-9_]+)",\s*"(?:stop|review|info)"')


def rules_checks(rules) -> list[Check]:
    from .drafts import INSTRUMENT_TEMPLATES, TEMPLATES, render
    out: list[Check] = []
    pkg = Path(__file__).resolve().parents[1]
    emitted: set[str] = set()
    for src in pkg.rglob("*.py"):
        text = src.read_text(encoding="utf-8")
        for pat in _CODE_PATTERNS:
            emitted |= set(pat.findall(text))
        if src.name == "runsheet.py":
            emitted |= set(_RUNSHEET_RULE.findall(text))
    checklist_ids = [c["id"] for c in rules.checklists()]
    emitted -= set(checklist_ids)
    emitted -= {"MANUAL"}
    missing = sorted(code for code in emitted if code not in rules.data["flags"])
    out.append(Check("Rules", "Every flag the program raises has a rule", WARN if missing else PASS,
                     f"{len(emitted)} codes found in the source" + (" (these fall back to the default rule)" if missing else ""), missing))
    dupes = sorted({i for i in checklist_ids if checklist_ids.count(i) > 1})
    out.append(Check("Rules", "Checklist ids are unique", FAIL if dupes else PASS, f"{len(checklist_ids)} checklist item(s)", dupes))
    named = {r.get("template") for r in rules.data["flags"].values() if r.get("template")} | \
            {c.get("template") for c in rules.checklists() if c.get("template")}
    absent = sorted(t for t in named if not (TEMPLATES / f"{t}.md").exists())
    out.append(Check("Rules", "Templates named by the rules exist", FAIL if absent else PASS, f"{len(named)} named", absent))
    broken = []
    sample = {"banner": "x", "venue": "x", "county": "x", "state_name": "x", "instrument": "x", "message": "x",
              "requirement": "x", "party": "x", "state_note": "x", "acknowledgment_one": "x", "exhibit": "x"}
    for name in INSTRUMENT_TEMPLATES:
        try:
            render(name, sample)
        except (ValueError, KeyError, OSError) as ex:
            broken.append(f"{name}: {ex}")
    out.append(Check("Rules", "Curative templates render", FAIL if broken else PASS, f"{len(INSTRUMENT_TEMPLATES)} templates", broken))
    unconfirmed = [c["id"] for c in rules.checklists() if c.get("authority") and "confirm" not in c["authority"].lower()]
    out.append(Check("Rules", "Legal citations carry 'confirm with counsel'", WARN if unconfirmed else PASS, "", unconfirmed))
    return out


def setup_checks() -> list[Check]:
    import os
    out = [Check("Setup", "AI abstracting (ptk extract)", INFO if not (os.environ.get("ANTHROPIC_API_KEY") or
                 os.environ.get("ANTHROPIC_AUTH_TOKEN")) else PASS,
                 "no API key: abstract in a Claude Code chat instead (ptk abstract-prompt / abstract-check; "
                 "ptk smoke-test --render-only then --answer), then measure a gold set of 300-500 instruments"
                 if not os.environ.get("ANTHROPIC_API_KEY") else
                 "a key is set: run `ptk smoke-test` to check extraction against the sample deed")]
    tools = [t for t in ("tesseract", "pdftoppm") if not shutil.which(t)]
    out.append(Check("Setup", "OCR tools", INFO if tools else PASS,
                     f"not installed: {', '.join(tools)} (ptk ocr needs them)" if tools else "tesseract and pdftoppm found"))
    out.append(Check("Setup", "Outgoing email", INFO if not os.environ.get("PTK_SMTP_HOST") else PASS,
                     "no PTK_SMTP_HOST: approved email is saved as .eml files, not sent" if not os.environ.get("PTK_SMTP_HOST")
                     else f"SMTP host {os.environ['PTK_SMTP_HOST']}"))
    return out


def run_audit(store, rules, today: date | None = None, areas: tuple[str, ...] = AREAS) -> Report:
    today = today or date.today()
    checks: list[Check] = []
    if "Engine" in areas:
        checks += engine_checks()
    if "Records" in areas:
        problems = store.verify()
        checks.append(Check("Records", "Audit log hash chain", FAIL if problems else PASS,
                            f"{len(store.events())} event(s)", problems))
    if "Deals" in areas:
        checks += deal_checks(store, rules, today)
    if "CRM" in areas:
        checks += crm_checks(store, rules)
    if "Rules" in areas:
        checks += rules_checks(rules)
    if "Setup" in areas:
        checks += setup_checks()
    return Report(checks, today.isoformat())


def format_report(report: Report) -> str:
    lines = [f"Accuracy checks, {report.ran}: " + ", ".join(f"{n} {s}" for s, n in report.counts.items() if n)]
    for area in AREAS:
        rows = [c for c in report.checks if c.area == area]
        if not rows:
            continue
        lines.append(f"\n{area}")
        for c in rows:
            lines.append(f"  {c.status:<5} {c.name}" + (f": {c.detail}" if c.detail else ""))
            for item in c.items[:25]:
                lines.append(f"          - {item}")
            if len(c.items) > 25:
                lines.append(f"          ... and {len(c.items) - 25} more")
    return "\n".join(lines)
