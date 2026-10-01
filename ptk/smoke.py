"""One real end-to-end run on the fictional sample deed, to prove the AI step works.

Renders the sample page, OCRs it (Tesseract when installed), sends one request
to the Claude API, validates the answer against the schema, and checks it
against facts known to be on the page. Costs one request (a few cents).

Run it after you set ANTHROPIC_API_KEY (or log in with ``ant auth login``),
whenever you change the model, the prompt or the schema.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from .extract import Page, extract, ocr_lines_for

EXPECTED = [
    ("instrument type is a mineral deed", lambda x: x.get("instrument_type") == "mineral_deed"),
    ("recorded in Vol. 88, Page 77", lambda x: str((x.get("recording") or {}).get("volume")) == "88"
     and str((x.get("recording") or {}).get("page")) == "77"),
    ("grantor ADAMS EXAMPLE, grantee BAKER EXAMPLE",
     lambda x: {"ADAMS EXAMPLE", "BAKER EXAMPLE"} <= {(p.get("name_verbatim") or "").upper() for p in x.get("parties", [])}),
    ("conveys 1/2 of the minerals",
     lambda x: any(c.get("estate") == "mineral" and [(f.get("num"), f.get("den")) for f in c["interest"]["factors"]] == [("1", "2")]
                   for c in x.get("conveyances", []))),
    ("reservation of 1/2 of the 1/8 royalty",
     lambda x: any(sorted((f.get("num"), f.get("den")) for f in r["interest"]["factors"]) == [("1", "2"), ("1", "8")]
                   for r in x.get("reservations_exceptions", []))),
    ("term royalty (15 years) captured",
     lambda x: any((r.get("term") or {}).get("is_term_interest") for r in x.get("reservations_exceptions", []))),
    ("double fraction flagged", lambda x: any(f.get("code") == "double_fraction" for f in x.get("flags", []))),
    ("legal description parsed to Section 12, Block 33, A-123",
     lambda x: any((ld.get("tx_survey") or {}).get("section") == "12" and (ld.get("tx_survey") or {}).get("block") == "33"
                   and str((ld.get("tx_survey") or {}).get("abstract_no") or "").endswith("123")
                   for ld in x.get("legal_descriptions", []))),
]


def run_smoke(client: Any, work_dir: Path, model: str, effort: str = "high") -> tuple[bool, list[str]]:
    from .ocr import run as run_ocr
    from .sample import render_sample_page

    work_dir.mkdir(parents=True, exist_ok=True)
    page_path = render_sample_page(work_dir / "sample-deed.png")
    report: list[str] = [f"Rendered {page_path}"]
    ocr_dir = None
    if shutil.which("tesseract"):
        run_ocr([page_path], work_dir / "ocr", engine="tesseract")
        ocr_dir = work_dir / "ocr"
        report.append("OCR: tesseract")
    else:
        report.append("OCR: skipped (tesseract not installed); quotes can't be checked against OCR lines")
    result = extract(client, [Page(page_path, ocr_lines_for(page_path, ocr_dir))],
                     doc_id="SAMPLE-REEVES-DR-0088-0077", county="Reeves", state="TX", model=model, effort=effort)
    if "error" in result:
        return False, report + [f"FAIL  request: {result['error']}"]
    report.append(f"Model: {result.get('model')}  stop: {result.get('stop_reason')}")
    usage = result.get("usage") or {}
    if usage:
        report.append(f"Tokens: {usage.get('input_tokens')} in, {usage.get('output_tokens')} out, "
                      f"{usage.get('cache_read_input_tokens') or 0} from cache")
    ok = True
    schema_problems = result.get("schema_errors") or []
    if schema_problems:
        ok = False
        report += [f"FAIL  schema: {p}" for p in schema_problems[:5]]
    else:
        report.append("PASS  answer matches the JSON schema")
    extraction = result.get("extraction") or {}
    for label, test in EXPECTED:
        try:
            passed = bool(test(extraction))
        except (KeyError, TypeError, AttributeError):
            passed = False
        ok &= passed
        report.append(f"{'PASS' if passed else 'FAIL'}  {label}")
    checks = result.get("checks") or []
    report.append(f"{'PASS' if not checks else 'WARN'}  quote checks: {len(checks)} problem(s)")
    report += [f"      {c['code']}: {c['detail']}" for c in checks[:5]]
    return ok, report
