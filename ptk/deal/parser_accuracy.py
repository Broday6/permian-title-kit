"""Parser accuracy against a hand-labelled corpus (parser_corpus.json beside this file).

Each case is a string in a form found in Permian records, with the answer a landman would read from it.
``score_all()`` returns one result per case: verdict correct / partial / wrong, and for anything not
correct a risk: dangerous (silently wrong or incomplete), safe (refused, or the gap is named in
issues) or noise (a runsheet flag that shouldn't fire).
"""
# ruff: noqa: E501
import json
from fractions import Fraction  # noqa: F401
from pathlib import Path

from .. import dates, fracs, legal_nm, legal_tx, names, runsheet

CORPUS = Path(__file__).with_name("parser_corpus.json")


def load_corpus() -> dict:
    return json.loads(CORPUS.read_text(encoding="utf-8"))


def fstr(v):
    return None if v is None else fracs.fmt(v)


def score_scalar(case):
    """fracs.parse_fraction / dates.iso: value or refusal."""
    exp = case["expect"]
    try:
        if case["parser"] == "fracs.parse_fraction":
            got = fracs.fmt(fracs.parse_fraction(case["input"]))
        else:
            got = dates.iso(case["input"])
        err = None
    except Exception as e:  # noqa: BLE001
        got, err = None, f"{type(e).__name__}: {e}"
    want = exp.get("value", exp.get("iso"))
    actual = got if err is None else f"ERROR ({err})"
    if exp.get("error"):
        if err:
            return "correct", None, actual
        return "wrong", "dangerous", actual  # accepted something it must refuse
    if err:
        return "wrong", "safe", actual
    if got == want:
        return "correct", None, actual
    return "wrong", "dangerous", actual


def score_read(case):
    r = fracs.read_fraction_text(case["input"])
    actual = {"consistent": r.consistent, "value": fstr(r.value), "words": fstr(r.words_value), "numerals": fstr(r.numeral_value), "problem": r.problem}
    exp = case["expect"]
    bad = [k for k, v in exp.items() if actual.get(k) != v]
    if not bad:
        return "correct", None, actual
    risk = "dangerous" if "consistent" in bad and exp["consistent"] is False else "safe"
    return ("partial" if len(bad) < len(exp) else "wrong"), risk, actual


TX_ISSUE = {"county": ("no county", "isn't in the Permian"), "abstract": ("no abstract",), "sections": ("no section",),
            "survey_no": ("no section or survey",), "survey_code": ("no survey",), "survey_name": ("no survey",),
            "save_and_except_count": ("save-and-except",)}


def score_tx(case):
    d = legal_tx.parse(case["input"], **case.get("kwargs", {}))
    actual = {k: getattr(d, k) for k in ("county", "abstract", "survey_code", "survey_name", "survey_no", "block", "township", "sections", "aliquots", "acres")}
    actual["save_and_except_count"] = len(d.save_and_except)
    actual["save_and_except"] = d.save_and_except
    actual["issues"] = d.issues
    exp = case["expect"]
    fields = {}
    for k, v in exp.items():
        a = actual[k]
        if k == "acres":
            ok = (a is None and v is None) or (a is not None and v is not None and float(a) == float(v))
        else:
            ok = a == v
        if ok:
            fields[k] = "ok"
        elif a in (None, [], 0) and v not in (None, [], 0):
            hint = TX_ISSUE.get(k, ())
            fields[k] = "missing-flagged" if any(h in i for i in d.issues for h in hint) else "missing-silent"
        else:
            hint = TX_ISSUE.get(k, ())
            fields[k] = "wrong-flagged" if (k == "county" and any("isn't in the Permian" in i for i in d.issues)) else "wrong-silent"
    return verdict(fields), risk(fields), actual, fields


def score_nm(case):
    d = legal_nm.parse(case["input"])
    secs = {}
    for s in d.sections:
        secs[str(s.section)] = {"qq": sorted(s.quarter_quarters), "lots": s.lots, "whole": s.whole, "sub": sorted(s.sub_divisions)}
    actual = {"township": d.township, "range": d.range, "sections": secs, "issues": d.issues}
    exp = case["expect"]
    fields = {}
    for k in ("township", "range"):
        if actual[k] == exp[k]:
            fields[k] = "ok"
        elif actual[k] is None:
            fields[k] = "missing-flagged"  # parser always names a missing township/range
        else:
            fields[k] = "wrong-silent"
    for sec, want in exp["sections"].items():
        got = secs.get(sec)
        flagged = any(f"section {sec}:" in i for i in d.issues)
        if got == want:
            fields[f"sec {sec}"] = "ok"
        elif got is None:
            fields[f"sec {sec}"] = "missing-flagged" if any("no section" in i for i in d.issues) else "missing-silent"
        else:
            fields[f"sec {sec}"] = "wrong-flagged" if flagged else "wrong-silent"
    for sec in secs:
        if sec not in exp["sections"]:
            fields[f"extra sec {sec}"] = "wrong-silent"
    for needle in exp.get("issues_contains", []):
        fields[f"issue~{needle}"] = "ok" if any(needle.lower() in i.lower() for i in d.issues) else "missing-silent"
    for needle in exp.get("issues_absent", []):
        fields[f"no-issue~{needle}"] = "ok" if not any(needle.lower() in i.lower() for i in d.issues) else "noise"
    return verdict(fields), risk(fields), actual, fields


def score_names(case):
    out = names.search_variants(case["input"])
    actual = {"kind": out["kind"], "searches": out["searches"], "notes": out["notes"], "parties": len(out.get("parties", [])) or 1}
    exp = case["expect"]
    fields = {"kind": "ok" if out["kind"] == exp["kind"] else "wrong-silent"}
    for s in exp.get("searches_include", []):
        fields[f"search:{s}"] = "ok" if s in out["searches"] else "missing-silent"
    if "searches_any_contains" in exp:
        hit = any(any(n in s for n in exp["searches_any_contains"]) for s in out["searches"])
        fields["search~any"] = "ok" if hit else "missing-silent"
    for n in exp.get("notes_include", []):
        fields[f"note:{n}"] = "ok" if any(n.lower() in x.lower() for x in out["notes"]) else "missing-silent"
    if "parties" in exp:
        fields["parties"] = "ok" if actual["parties"] == exp["parties"] else "wrong-silent"
    return verdict(fields), risk(fields), actual, fields


def score_rs(case):
    flags = runsheet.check_row(case["input"])
    got = sorted({f.code for f in flags})
    want = case["expect"]["flags"]
    fields = {}
    for c in want:
        fields[c] = "ok" if c in got else "missing-silent"
    for c in got:
        if c not in want:
            fields[c] = "noise"
    if not want and not got:
        fields["(no flags)"] = "ok"
    return verdict(fields), risk(fields), {"flags": got}, fields


def verdict(fields):
    vals = list(fields.values())
    if all(v == "ok" for v in vals):
        return "correct"
    if any(v == "ok" for v in vals):
        return "partial"
    return "wrong"


def risk(fields):
    vals = set(fields.values()) - {"ok"}
    if not vals:
        return None
    if vals & {"wrong-silent", "missing-silent"}:
        return "dangerous"
    if vals == {"noise"}:
        return "noise"
    return "safe"




def score_case(case: dict) -> dict:
    p = case["parser"]
    f = None
    if p in ("fracs.parse_fraction", "dates.iso"):
        v, r, a = score_scalar(case)
    elif p == "fracs.read_fraction_text":
        v, r, a = score_read(case)
    elif p == "legal_tx.parse":
        v, r, a, f = score_tx(case)
    elif p == "legal_nm.parse":
        v, r, a, f = score_nm(case)
    elif p == "names.search_variants":
        v, r, a, f = score_names(case)
    else:
        v, r, a, f = score_rs(case)
    return {"id": case["id"], "parser": p, "freq": case.get("freq"), "form": case.get("form"), "verdict": v, "risk": r,
            "actual": a, "fields": f}


def score_all() -> list[dict]:
    return [score_case(c) for c in load_corpus()["cases"]]
