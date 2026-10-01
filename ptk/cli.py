"""Command line for the Permian title kit. Run ``python -m ptk --help``."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import asdict
from fractions import Fraction
from pathlib import Path

from . import counties, legal_nm, legal_tx, names, runsheet
from .doi import Lease, build_deck, participation
from .fracs import FractionError, fmt, parse_fraction, to_decimal
from .ledger import load_events, ownership_table, replay


def _print_flags(flags, out=sys.stdout) -> None:
    if not flags:
        print("No flags.", file=out)
        return
    print(f"{len(flags)} flag(s):", file=out)
    for f in flags:
        print(f"  [{f.code}] {f.tract}/{f.depth} {f.instrument}: {f.detail}", file=out)


def cmd_calc(a: argparse.Namespace) -> int:
    owned, royalty = parse_fraction(a.owned), parse_fraction(a.royalty)
    nma = parse_fraction(a.acres) * owned
    part = Fraction(1)
    if a.unit:
        num, den = a.unit.split("/", 1)
        part = parse_fraction(num) / parse_fraction(den)
    burden = Fraction(0)
    if a.npri_fixed:
        burden += parse_fraction(a.npri_fixed)
    net_royalty = royalty - burden
    if a.npri_floating:
        net_royalty -= royalty * parse_fraction(a.npri_floating)
    decimal = owned * net_royalty * part
    print(f"Net mineral acres:  {to_decimal(nma, 4)}")
    print(f"NRA (1/8 basis):    {to_decimal(nma * royalty * 8, 4)}")
    print(f"Participation:      {to_decimal(part)} ({fmt(part)})")
    print(f"Decimal interest:   {to_decimal(decimal)} ({fmt(decimal)})")
    return 0


def cmd_ledger(a: argparse.Namespace) -> int:
    depths = json.loads(Path(a.depths).read_text()) if a.depths else None
    result = replay(load_events(a.takeoff), depths=depths, as_of=a.as_of)
    rows = ownership_table(result)
    _write_or_print(rows, a.out, ["tract", "depth", "owner", "estate", "fraction", "kind"])
    _print_flags(result.flags, sys.stderr if a.out else sys.stdout)
    return 0


def _lease_list(data: list[dict]) -> list[Lease]:
    return [Lease.from_dict(d) for d in data]


def cmd_deck(a: argparse.Namespace) -> int:
    project_path = Path(a.project)
    p = json.loads(project_path.read_text())
    takeoff = (project_path.parent / p["takeoff"]).resolve()
    result = replay(load_events(takeoff), depths=p.get("depths"), as_of=p.get("as_of"))
    weights = p["participation"]["weights"]
    factors = participation(weights)
    deck = build_deck(result, _lease_list(p.get("leases", [])), factors,
                      depth=p.get("produce_depth", "ALL"), state=p.get("state", "TX"),
                      pooled=p.get("pooled", True), npri_ratified=p.get("npri_ratified"))
    print(f"{p.get('well', 'Well')}: participation by {p['participation'].get('method', 'weights')}")
    for tract, f in factors.items():
        print(f"  {tract}: {to_decimal(f)} ({fmt(f)})")
    rows = [{"owner": r.owner, "type": r.type, "tract": r.tract, "depth": r.depth,
             "tract_decimal": to_decimal(r.tract_decimal), "unit_decimal": to_decimal(r.unit_decimal),
             "exact": fmt(r.unit_decimal), "source": r.source, "note": r.note} for r in deck.rows]
    if a.out:
        _write_or_print(rows, a.out, list(rows[0].keys()) if rows else ["owner"])
    print("\nUnit deck by owner:")
    for owner, typ, value in deck.by_owner():
        print(f"  {owner:<34} {typ:<5} {to_decimal(value)}  ({fmt(value)})")
    total = deck.revenue_total()
    print(f"\nRevenue interests total: {to_decimal(total)} ({fmt(total)})")
    flags = result.flags + deck.flags
    _print_flags(flags)
    not_whole = any(f.code in ("DECK_NOT_WHOLE", "MINERALS_NOT_WHOLE", "PARTICIPATION_NOT_WHOLE") for f in flags)
    return 2 if not_whole or total != 1 else 0


def cmd_legal(a: argparse.Namespace) -> int:
    state = a.state or ("NM" if legal_nm._TWP.search(a.text) and legal_nm._RNG.search(a.text) else "TX")
    if state.upper() == "NM":
        d = legal_nm.parse(a.text)
        out = {"state": "NM", "township": d.township, "range": d.range, "keys": d.keys(),
               "sections": [{"section": s.section, "whole": s.whole, "quarter_quarters": sorted(s.quarter_quarters),
                             "finer": sorted(s.sub_divisions), "lots": s.lots,
                             "regular_acres": to_decimal(s.regular_area() * 640, 2)} for s in d.sections],
               "issues": d.issues}
    else:
        d = legal_tx.parse(a.text, county=a.county)
        out = {"state": "TX", **{k: v for k, v in asdict(d).items() if k != "text"},
               "abstract_key": d.abstract_key(), "survey_key": d.survey_key()}
    print(json.dumps(out, indent=2))
    return 0


def cmd_names(a: argparse.Namespace) -> int:
    print(json.dumps([names.search_variants(n) for n in a.names], indent=2))
    return 0


def cmd_plan(a: argparse.Namespace) -> int:
    for step in counties.plan_search(a.county, a.start):
        print(f"- {step}")
    return 0


def cmd_check(a: argparse.Namespace) -> int:
    rows, flags = runsheet.check_file(a.runsheet, a.images)
    by_sev = {s: sum(1 for f in flags if f.severity == s) for s in ("stop", "review", "info")}
    for f in flags:
        print(f"row {f.row:<4} {f.severity:<6} {f.code:<24} {f.message}")
    print(f"\n{len(rows)} rows: {by_sev['stop']} stop, {by_sev['review']} review, {by_sev['info']} info")
    if a.out:
        _write_or_print([asdict(f) for f in flags], a.out, ["row", "code", "severity", "message"])
    return 1 if by_sev["stop"] else 0


def cmd_template(a: argparse.Namespace) -> int:
    if a.kind == "runsheet":
        runsheet.write_template(a.path)
    else:
        with open(a.path, "w", newline="", encoding="utf-8") as fh:
            csv.writer(fh).writerow(["seq", "instrument", "recorded", "tract", "depth", "estate", "kind",
                                     "grantor", "grantees", "shares", "interest", "warranty",
                                     "npri_kind", "npri_value", "notes"])
    print(f"Wrote {a.path}")
    return 0


def _client():
    try:
        import anthropic
    except ImportError:
        sys.exit("Install the SDK first: pip install anthropic")
    return anthropic.Anthropic()


def cmd_extract(a: argparse.Namespace) -> int:
    from .extract import Page, extract, ocr_lines_for
    ocr_dir = Path(a.ocr_dir) if a.ocr_dir else None
    pages = [Page(Path(p), ocr_lines_for(Path(p), ocr_dir)) for p in a.pages]
    result = extract(_client(), pages, doc_id=a.doc_id, county=a.county, state=a.state.upper(),
                     model=a.model, effort=a.effort, max_tokens=a.max_tokens)
    text = json.dumps(result, indent=2)
    if a.out:
        Path(a.out).write_text(text)
        print(f"Wrote {a.out}")
    else:
        print(text)
    if "error" in result:
        print(f"Not extracted: {result['error']}", file=sys.stderr)
        return 1
    if result.get("checks"):
        print(f"{len(result['checks'])} check(s) failed: review before use", file=sys.stderr)
    return 0


def _load_jobs(path: str) -> list[dict]:
    from .extract import Page, ocr_lines_for
    spec = json.loads(Path(path).read_text())
    base = Path(path).parent
    jobs = []
    for job in spec["jobs"]:
        ocr_dir = base / job["ocr_dir"] if job.get("ocr_dir") else None
        pages = [Page(base / p, ocr_lines_for(base / p, ocr_dir)) for p in job["pages"]]
        jobs.append({**job, "pages": pages})
    return jobs


def cmd_batch(a: argparse.Namespace) -> int:
    from .extract import collect_batch, custom_id, line_table, submit_batch
    client = _client()
    if a.action == "submit":
        print(submit_batch(client, _load_jobs(a.target)))
        return 0
    lines = {custom_id(j["doc_id"]): line_table(j["pages"]) for j in _load_jobs(a.jobs)} if a.jobs else None
    status = collect_batch(client, a.target, lines)
    if status["status"] != "ended":
        print(f"Batch still {status['status']}; try again later.")
        return 3
    Path(a.out or f"{a.target}.json").write_text(json.dumps(status["results"], indent=2))
    print(f"Wrote {a.out or a.target + '.json'} ({len(status['results'])} results)")
    return 0


def _write_or_print(rows: list[dict], out: str | None, fields: list[str]) -> None:
    if out:
        with open(out, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)
        print(f"Wrote {out}", file=sys.stderr)
    else:
        w = csv.DictWriter(sys.stdout, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ptk", description="Permian title kit: runsheets, ownership math, legal descriptions, AI first-pass abstracting.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("calc", help="decimal interest for one owner")
    s.add_argument("--acres", required=True)
    s.add_argument("--owned", required=True, help="undivided mineral interest, e.g. 1/16")
    s.add_argument("--royalty", required=True, help="lease royalty, e.g. 1/4")
    s.add_argument("--unit", help="tract share as a/b: acres in unit/unit acres, or lateral feet in tract/total feet")
    s.add_argument("--npri-fixed", help="fixed NPRI burdening the tract, fraction of production")
    s.add_argument("--npri-floating", help="floating NPRI, fraction of royalty")
    s.set_defaults(fn=cmd_calc)

    s = sub.add_parser("ledger", help="replay a takeoff CSV into ownership")
    s.add_argument("takeoff")
    s.add_argument("--depths", help="JSON file: {tract: [depth intervals]}")
    s.add_argument("--as-of", help="ISO date: ignore instruments recorded after it")
    s.add_argument("--out")
    s.set_defaults(fn=cmd_ledger)

    s = sub.add_parser("deck", help="build the unit deck from a project JSON")
    s.add_argument("project")
    s.add_argument("--out")
    s.set_defaults(fn=cmd_deck)

    s = sub.add_parser("legal", help="parse a Texas or New Mexico legal description")
    s.add_argument("text")
    s.add_argument("--state", choices=["TX", "NM", "tx", "nm"])
    s.add_argument("--county")
    s.set_defaults(fn=cmd_legal)

    s = sub.add_parser("names", help="index search variants for names")
    s.add_argument("names", nargs="+")
    s.set_defaults(fn=cmd_names)

    s = sub.add_parser("plan", help="where to search a county for a given start year")
    s.add_argument("--county", required=True)
    s.add_argument("--from", dest="start", type=int, required=True)
    s.set_defaults(fn=cmd_plan)

    s = sub.add_parser("check", help="run red-flag checks on a runsheet CSV")
    s.add_argument("runsheet")
    s.add_argument("--images", help="folder holding the image files the runsheet links")
    s.add_argument("--out")
    s.set_defaults(fn=cmd_check)

    s = sub.add_parser("template", help="write a blank runsheet or takeoff CSV")
    s.add_argument("path")
    s.add_argument("--kind", choices=["runsheet", "takeoff"], default="runsheet")
    s.set_defaults(fn=cmd_template)

    s = sub.add_parser("extract", help="AI first-pass abstract of one instrument (needs the anthropic SDK)")
    s.add_argument("pages", nargs="+", help="page images (PNG/JPEG) or a PDF")
    s.add_argument("--doc-id", required=True)
    s.add_argument("--county", required=True)
    s.add_argument("--state", required=True, choices=["TX", "NM", "tx", "nm"])
    s.add_argument("--ocr-dir", help="folder with <page stem>.txt OCR files")
    s.add_argument("--model", default="claude-opus-5-5")
    s.add_argument("--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"])
    s.add_argument("--max-tokens", type=int, default=16000)
    s.add_argument("--out")
    s.set_defaults(fn=cmd_extract)

    s = sub.add_parser("batch", help="submit or collect a Message Batch of extractions (half price)")
    s.add_argument("action", choices=["submit", "collect"])
    s.add_argument("target", help="jobs JSON (submit) or batch id (collect)")
    s.add_argument("--jobs", help="collect: the jobs JSON you submitted, so quotes are checked against the OCR lines")
    s.add_argument("--out")
    s.set_defaults(fn=cmd_batch)

    a = ap.parse_args(argv)
    try:
        return a.fn(a)
    except (FractionError, ValueError, KeyError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
