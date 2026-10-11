"""Start the title run for a deal: one workspace per asset, wired into verification.

``start_title_run`` creates, for each asset that has no title files yet:

    deals/<deal>/title/<asset>/
        SEARCH-PLAN.md    where the records are and for which years, parent counties, the
                          legal description's keys, every name variant to search, the
                          state checklist, and the steps below
        runsheet.csv      one row per instrument (ptk check reads it)
        takeoff.csv       minerals and royalties (ptk ledger reads it), or
        lease_chain.csv   leases, wellbores and ORRIs (the lease chain)
        images/  ocr/  abstracts/

then points the deal's title section at those files (a recorded amendment), so
``ptk deal verify`` runs on them as soon as they're filled in. It never
overwrites a file that already exists.

The steps it lays out are the kit's own: pull the instruments from a licensed
source (never by scraping a county portal), ``ptk ocr``, abstract each
instrument (``ptk extract`` with an API key, or in a Claude Code chat with
``ptk abstract-prompt`` / ``ptk abstract-check``), ``ptk check`` the runsheet,
build the takeoff, ``ptk deal verify``.
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path
from typing import Any

from .. import counties, legal_nm, legal_tx, names, runsheet
from ..leasehold import LEASE_COLUMNS
from .state import fold
from .store import Store

TAKEOFF_COLUMNS = ["seq", "instrument", "recorded", "tract", "depth", "estate", "kind", "grantor", "grantees", "shares",
                   "interest", "warranty", "npri_kind", "npri_value", "notes", "npri_burdens"]
MINERAL_KINDS = ("mineral", "royalty")


def _write_csv(path: Path, columns: list[str]) -> bool:
    if path.exists():
        return False
    with open(path, "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerow(columns)
    return True


def search_plan(st, asset: dict[str, Any], start_year: int) -> str:
    deal_id, aid = st.deal_id, asset["id"]
    county = asset.get("county") or ""
    lines = [f"# Title run: {deal_id} {aid}", "",
             f"{asset['kind'].title()} in {county} County, {asset['state']}. Effective date "
             f"{st.record.get('effective_date') or '[not set]'}: run title to that date, then bring it down to today.", "",
             "## Lands", "", f"{asset.get('legal_description') or '[no legal description on the deal]'}", ""]
    text = asset.get("legal_description") or ""
    if text and asset["state"] == "TX":
        d = legal_tx.parse(text, county=county or None)
        lines += [f"- Abstract key: {d.abstract_key() or '-'}", f"- Survey keys: {', '.join(d.survey_keys()) or '-'}"]
        lines += [f"- Check: {i}" for i in d.issues]
    elif text:
        d = legal_nm.parse(text)
        lines += [f"- PLSS keys: {', '.join(d.keys()) or '-'}"] + [f"- Check: {i}" for i in d.issues]
    lease = asset.get("lease") or {}
    if lease:
        lines += [f"- Lease: {lease.get('id')}" + (f", {lease.get('recording')}" if lease.get("recording") else "")]
    for w in asset.get("wells") or []:
        lines.append(f"- Well: {w.get('name') or '-'} (API {w.get('api') or '-'})")
    lines += ["", f"## Where the records are (searching from {start_year})", ""]
    try:
        lines += [f"- {step}" for step in counties.plan_search(county, start_year)]
    except KeyError:
        lines.append(f"- {county} County isn't in the county table: add its coverage (ptk counties --checklist).")
    lines += ["- Use licensed sources only (TexasFile, CourthouseDirect, county subscriptions, abstract plants); never scrape a county portal."]
    if asset["state"] == "NM":
        lines.append("- Pull BLM MLRS (federal) and NMSLO (State) status for every section, whatever the deal says.")
    lines += ["", "## Names to search (grantor and grantee indexes)", ""]
    sellers = [s for s in st.record.get("sellers", []) if s["id"] in asset["sellers"]]
    for s in sellers:
        v = names.search_variants(s["name"])
        lines.append(f"- **{s['name']}**: " + "; ".join(v["searches"]))
        lines += [f"  - {n}" for n in v["notes"]]
    lines += ["", "## Checklist for this asset", ""]
    lines += [f"- [ ] {t.task_id} {t.requirement}" for t in st.tasks.values()
              if t.source == "checklist" and t.asset_id in (aid, None) and not t.closed]
    chain = "lease_chain.csv" if asset["kind"] not in MINERAL_KINDS else "takeoff.csv"
    lines += ["", "## Steps", "",
              f"1. Index search: every name above, every key above, from {start_year} to today. List each instrument in runsheet.csv.",
              "2. Save each image to images/ named by the image key (e.g. TX-REEVES-OPR-2019-012345.pdf; see ptk.runsheet.image_key).",
              "3. OCR: `ptk ocr images/* --out ocr/`",
              "4. Abstract each instrument: `ptk extract <pages> --doc-id <key> --county <county> --state <ST> --ocr-dir ocr/ "
              "--out abstracts/<key>.json` (API key), or in a Claude Code chat: `ptk abstract-prompt ...`, write the JSON, "
              "`ptk abstract-check abstracts/<key>.json --pages ... --ocr-dir ocr/`.",
              "5. Fill runsheet.csv from the abstracts and run `ptk check runsheet.csv --images images/ --xlsx runsheet.xlsx`.",
              f"6. Build {chain} in recording order (see the README's formats).",
              f"7. `ptk deal verify {deal_id}`, then work the tasks it opens.", ""]
    return "\n".join(lines)


def start_title_run(store: Store, rules, deal_id: str, actor: str, asset_ids: list[str] | None = None,
                    start_year: int = 1850, today: date | None = None) -> dict[str, Any]:
    from .intake import intake_deal
    st = fold(store.events(deal_id))
    record = st.record
    created: dict[str, list[str]] = {}
    changed = False
    new_assets = []
    for a in record["assets"]:
        a = {**a, "title": dict(a.get("title") or {})}
        if asset_ids and a["id"] not in asset_ids:
            new_assets.append(a)
            continue
        folder = store.home / "deals" / deal_id / "title" / a["id"]
        for sub in ("images", "ocr", "abstracts"):
            (folder / sub).mkdir(parents=True, exist_ok=True)
        made = []
        if _write_csv(folder / "runsheet.csv", runsheet.COLUMNS):
            made.append("runsheet.csv")
        if a["kind"] in MINERAL_KINDS:
            if _write_csv(folder / "takeoff.csv", TAKEOFF_COLUMNS):
                made.append("takeoff.csv")
            wanted = {"takeoff": folder / "takeoff.csv", "runsheet": folder / "runsheet.csv", "images": folder / "images"}
        else:
            if _write_csv(folder / "lease_chain.csv", LEASE_COLUMNS):
                made.append("lease_chain.csv")
            wanted = {"lease_chain": folder / "lease_chain.csv", "runsheet": folder / "runsheet.csv", "images": folder / "images"}
        plan = folder / "SEARCH-PLAN.md"
        plan.write_text(search_plan(st, a, start_year), encoding="utf-8")  # regenerated: it reflects the current deal
        made.append("SEARCH-PLAN.md")
        for key, path in wanted.items():
            if not a["title"].get(key):
                a["title"][key] = str(path)
                changed = True
        created[a["id"]] = [str(folder / m) for m in made]
        new_assets.append(a)
        store.append(deal_id, "title.run_started", {"asset_id": a["id"], "folder": str(folder), "start_year": start_year}, actor)
    if changed:
        intake_deal(store, rules, {**record, "assets": new_assets}, actor,
                    reason="title run started: title files set up", today=today)
    return {"deal_id": deal_id, "created": created, "linked": changed}
