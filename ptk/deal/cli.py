"""``ptk deal``: follow a signed deal from intake to a recorded, paid interest."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from ..dates import iso
from . import actions, outbox
from .intake import intake_deal
from .record import DEAL_CSV_COLUMNS, load_deal, load_deals_csv
from .rules import STAGES, Rules
from .state import fold, summary
from .store import Store, default_actor

EXAMPLE_DEAL = {
    "_note": "FICTIONAL EXAMPLE. Replace every value.",
    "deal_id": "D-2026-000",
    "name": "Seller to Buyer mineral purchase",
    "buyer": "Buyer Minerals LLC",
    "buyer_address": "[address]",
    "effective_date": "2026-09-01",
    "price": "100000.00",
    "sellers": [{"id": "S1", "name": "Seller Example", "marital_status": "single", "email": "seller@example.com",
                 "address": "[address]"}],
    "assets": [{"id": "A1", "kind": "mineral", "state": "TX", "county": "Reeves", "tract": "SEC12",
                "legal_description": "Section 12, Block 33, T-2-S, T&P RR Co. Survey, A-123, Reeves County, Texas",
                "gross_acres": "640", "interest": "1/4", "seller": "S1",
                "title": {"takeoff": "takeoff.csv", "runsheet": "runsheet.csv"},
                "wells": [{"api": "42-389-00000", "name": "[well name]", "operator": "[operator]", "project": "project.json"}]}],
    "contacts": [{"name": "[operator]", "role": "operator", "email": "owner.relations@example.com"}],
    "documents": [{"type": "purchase_agreement", "file": "psa.pdf", "received": "2026-09-15"}],
}


def _today(a) -> date | None:
    return date.fromisoformat(iso(a.today)) if getattr(a, "today", None) else None


_OPEN: list[Store] = []


def _ctx(a):
    store = Store(a.db)
    _OPEN.append(store)
    rules = Rules.load(a.rules, beside=store.path)
    return store, rules, a.by or default_actor()


def _print_summary(s: dict, verbose: bool = True) -> None:
    print(f"{s['deal_id']}: {s['name']}  (buyer {s['buyer']}; {', '.join(s['states'])}; {', '.join(s['kinds'])})")
    print(f"  Stage: {s['stage_title']}.  Open tasks: {s['open_tasks']} ({s['stop_tasks']} stop).  "
          f"Messages awaiting approval: {s['messages_awaiting_approval']}.")
    print(f"  Attorney opinion: {'REQUIRED' if s['opinion_required'] else 'not required'} ({s['opinion_reason']})")
    if not verbose:
        return
    for i, stg in enumerate(s["stages"], start=1):
        mark = "[x]" if stg["done"] else "[ ]"
        print(f"  {mark} {i} {stg['title']}")
        for n in stg["notes"]:
            print(f"        note: {n}")
        for b in stg["blockers"][:12]:
            print(f"        - {b}")
        if len(stg["blockers"]) > 12:
            print(f"        ... and {len(stg['blockers']) - 12} more (ptk deal tasks {s['deal_id']})")


def cmd_intake(a) -> int:
    store, rules, actor = _ctx(a)
    record = load_deal(a.file)
    layers = None
    if a.layer:
        from ..mapcheck import read_layer
        layers = {"TX": [], "NM": []}
        for p in a.layer:
            feats = read_layer(p)
            sample = feats[0].properties if feats else {}
            layers["NM" if any(k in sample for k in ("PLSSID", "FRSTDIVNO", "TWNSHPNO")) else "TX"].extend(feats)
    fields = json.loads(Path(a.fields).read_text()) if a.fields else None
    r = intake_deal(store, rules, record, actor, a.reason, layers, fields, _today(a))
    t = r["tasks"]
    print(f"{'Amended' if a.reason else 'Created'} {record['deal_id']}: {len(t['opened'])} task(s) opened, "
          f"{len(t['resolved'])} resolved, {len(t['reopened'])} reopened.")
    _print_summary(r["summary"])
    return 0


def cmd_import(a) -> int:
    store, rules, actor = _ctx(a)
    for record in load_deals_csv(a.file):
        reason = a.reason if store.exists(record["deal_id"]) else None
        if store.exists(record["deal_id"]) and not reason:
            print(f"{record['deal_id']}: already exists; skipped (pass --reason to amend)")
            continue
        r = intake_deal(store, rules, record, actor, reason, today=_today(a))
        _print_summary(r["summary"], verbose=False)
    return 0


def cmd_list(a) -> int:
    store, rules, _ = _ctx(a)
    rows = [summary(fold(store.events(d)), rules, _today(a)) for d in store.deal_ids()]
    if a.json:
        print(json.dumps(rows, indent=2))
        return 0
    if not rows:
        print(f"No deals in {store.path}. Start with: ptk deal intake deal.json")
        return 0
    print(f"{'Deal':<14}{'Stage':<20}{'Open':>5}{'Stop':>5}  {'Opinion':<8} Next")
    for s in rows:
        print(f"{s['deal_id']:<14}{s['stage_title']:<20}{s['open_tasks']:>5}{s['stop_tasks']:>5}  "
              f"{'yes' if s['opinion_required'] else 'no':<8} {s['next'][:90]}")
    return 0


def cmd_status(a) -> int:
    store, rules, _ = _ctx(a)
    st = fold(store.events(a.deal))
    s = summary(st, rules, _today(a))
    if a.json:
        print(json.dumps(s, indent=2))
    else:
        _print_summary(s)
    return 0


def cmd_verify(a) -> int:
    from .verify import verify_deal
    store, rules, actor = _ctx(a)
    r = verify_deal(store, rules, a.deal, actor, a.asset, _today(a))
    bad = 0
    for aid, res in r["assets"].items():
        print(f"{aid}: {res['status']}: {res.get('summary', '')}")
        for k, v in (res.get("tasks") or {}).items():
            print(f"    tasks {k}: {', '.join(v)}")
        for n in res.get("notes", [])[:20]:
            print(f"    note: {n}")
        bad += res["status"] != "OK"
    _print_summary(r["summary"], verbose=False)
    verify_stage = next(s for s in r["summary"]["stages"] if s["stage"] == "verify")
    return 1 if bad or not verify_stage["done"] else 0


def cmd_title_run(a) -> int:
    from .titlerun import start_title_run
    store, rules, actor = _ctx(a)
    r = start_title_run(store, rules, a.deal, actor, a.asset, a.start, _today(a))
    for aid, files in r["created"].items():
        print(f"{aid}: {Path(files[-1]).parent}")
        for f in files:
            print(f"    {Path(f).name}")
    print("Linked the new files to the deal." if r["linked"] else "The deal already pointed at title files; left as is.")
    print(f"Next: follow SEARCH-PLAN.md in each folder, then ptk deal verify {a.deal}")
    return 0


def cmd_desk_init(a) -> int:
    folder = Path(a.folder).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    db = folder / "deals.db"
    Store(db).close()
    rules = folder / "ptk-rules.json"
    if not rules.exists():
        rules.write_text(json.dumps({"_about": "Your changes to the desk's rules. Put only the keys you change here; "
                                               "everything else keeps the built-in defaults (ptk/deal/rules.json)."}, indent=2),
                         encoding="utf-8")
    py = sys.executable
    cmd = folder / "Start Land Desk.cmd"
    cmd.write_text("@echo off\r\ntitle Land Desk\r\ncd /d \"%~dp0\"\r\n"
                   f"\"{py}\" -m ptk deal serve --db \"%~dp0deals.db\" --open\r\npause\r\n", encoding="utf-8")
    sh = folder / "start-land-desk.sh"
    sh.write_text("#!/bin/sh\ncd \"$(dirname \"$0\")\"\n" f"\"{py}\" -m ptk deal serve --db deals.db --open\n", encoding="utf-8")
    print(f"Desk folder ready: {folder}")
    print("  deals.db           the database (CRM owners, deals, audit log)")
    print("  ptk-rules.json     your rule changes")
    print("  Start Land Desk.cmd  double-click to open the desk in your browser")
    if a.crm:
        from . import crm
        store = Store(db)
        r = crm.import_backup(store, a.crm, a.by or default_actor())
        store.close()
        print(f"Imported {a.crm}: {r['added']} owner(s) added.")
    return 0


def cmd_tasks(a) -> int:
    store, _, _ = _ctx(a)
    st = fold(store.events(a.deal))
    tasks = [t for t in st.tasks.values() if a.all or not t.closed]
    if a.stage:
        tasks = [t for t in tasks if t.stage == a.stage]
    if a.json:
        print(json.dumps([{k: v for k, v in t.__dict__.items() if k != "history"} for t in tasks], indent=2))
        return 0
    for t in sorted(tasks, key=lambda t: (STAGES.index(t.stage), {"stop": 0, "review": 1, "info": 2}[t.severity], int(t.task_id[1:]))):
        att = " ATTORNEY" if t.requires_attorney else ""
        print(f"{t.task_id:<5}{t.stage:<9}{t.severity:<7}{t.status:<10}{t.code:<32}{(t.party_name or t.party)[:28]:<29}"
              f"due {t.due or '-'}{att}")
        print(f"      {t.requirement or t.message}")
        if t.message and t.message != t.requirement:
            print(f"      ({t.message[:200]})")
    print(f"{len(tasks)} task(s).")
    return 0


def cmd_task(a) -> int:
    store, _, actor = _ctx(a)
    actions.update_task(store, a.deal, a.task, actor, a.status, a.note or "", a.reason, a.attorney, a.opinion, a.due, a.party)
    print(f"Updated {a.task}.")
    return 0


def cmd_task_add(a) -> int:
    store, rules, actor = _ctx(a)
    tid = actions.add_task(store, rules, a.deal, actor, a.message, a.stage, a.severity, a.party, a.party_name, a.asset,
                           requires_attorney=a.attorney_question, today=_today(a))
    print(f"Opened {tid}.")
    return 0


def cmd_opinion(a) -> int:
    store, rules, actor = _ctx(a)
    covers = [c.strip() for c in (a.covers or "").split(",") if c.strip()]
    r = actions.record_opinion(store, rules, a.deal, actor, a.attorney, a.date, a.file, covers, a.requirement, a.kind, _today(a))
    print(f"Recorded {r['opinion_id']}: cleared {', '.join(r['cleared']) or 'nothing'}; "
          f"new requirement tasks {', '.join(r['requirements']) or 'none'}.")
    return 0


def cmd_draft(a) -> int:
    from .drafts import draft_for_task
    store, _, actor = _ctx(a)
    r = draft_for_task(store, a.deal, a.task, actor, a.out, a.template)
    print(f"Wrote {r['file']} ({r['blanks']} bracketed item(s) to complete). It's a draft for attorney review.")
    return 0


def cmd_request(a) -> int:
    store, rules, actor = _ctx(a)
    for r in outbox.request_for_task(store, rules, a.deal, a.task, actor, a.to):
        print(f"Drafted {r['msg_id']} ({r['channel']}): {r['subject']}. Approve it with: ptk deal outbox approve {a.deal} {r['msg_id']}")
    return 0


def cmd_package(a) -> int:
    from .drafts import build_package
    store, rules, actor = _ctx(a)
    r = build_package(store, rules, a.deal, actor, a.out, a.reason, _today(a))
    print(f"Wrote the closing package to {r['dir']}:")
    for f in r["files"]:
        print(f"  {f}")
    print(f"{r['blanks']} bracketed item(s) to complete in the conveyances. Next: attorney review, then "
          f"ptk deal closing {a.deal} approve")
    if r["gate"]:
        print("Funds stay held until:")
        for g in r["gate"]:
            print(f"  - {g}")
    return 0


def cmd_closing(a) -> int:
    store, rules, actor = _ctx(a)
    actions.closing(store, rules, a.deal, actor, a.action, a.date, a.amount, a.note or "")
    print({"approve": "Package approved.", "sign": "Signing recorded.", "release": "Funds released."}[a.action])
    return 0


def cmd_record(a) -> int:
    store, _, actor = _ctx(a)
    r = actions.log_recording(store, a.deal, actor, a.county, a.recorded, a.instrument_no or "", a.volume or "",
                              a.page or "", a.series, a.state)
    print(f"Logged recording in {r['county']} County, {r['state']}: image file {r['image_key'] or '(no key)'}")
    return 0


def cmd_notify(a) -> int:
    store, rules, actor = _ctx(a)
    rows = actions.notify(store, rules, a.deal, actor, a.before_recording, _today(a))
    for r in rows:
        print(f"{r['to']}: {r['result']}")
    if not rows:
        print("Every operator and payor has been notified (or no wells are listed).")
    return 0


def cmd_division_order(a) -> int:
    store, rules, actor = _ctx(a)
    r = actions.division_order(store, rules, a.deal, actor, a.payor, a.decimal, a.well, a.type, a.received, _today(a))
    if r["matches"] is False:
        print(f"MISMATCH: title says {r['expected']}. Don't sign it; a decimal question is drafted for approval.")
        return 1
    print("Division order matches the title decimal." if r["matches"] else f"Logged. {r.get('note', '')}")
    return 0


def cmd_expect(a) -> int:
    store, _, actor = _ctx(a)
    r = actions.expect(store, a.deal, actor, a.asset, a.payor, a.well, a.type, a.decimal)
    print(f"Expecting {r['type']} {r['decimal_display']} from {r['payor']} for {r['well']}.")
    return 0


def cmd_payment(a) -> int:
    store, rules, actor = _ctx(a)
    r = actions.payment(store, rules, a.deal, actor, a.payor, a.received, a.amount or "", a.decimal, a.well, a.type,
                        a.period or "", "suspense" if a.suspense else "paid", a.owner, _today(a))
    if r.get("matches") is False:
        print(f"Logged, but the decimal differs from title ({r['expected']}); a question is drafted for approval.")
        return 1
    print("Payment logged." + (f" Matches title decimal {r['expected']}." if r.get("matches") else ""))
    return 0


def cmd_reconcile(a) -> int:
    store, rules, actor = _ctx(a)
    r = actions.reconcile(store, rules, a.deal, actor, a.statement, _today(a))
    print(f"{r['matched']} line(s) for the buyer, {r['skipped_other_owners']} for other owners skipped, "
          f"{len(r['mismatches'])} decimal mismatch(es).")
    for v in r["name_variants"]:
        print(f"  payee name variant: {v}")
    return 1 if r["mismatches"] else 0


def cmd_remind(a) -> int:
    store, rules, actor = _ctx(a)
    acts = outbox.run_reminders(store, rules, actor, _today(a), a.deal)
    for x in acts:
        if x["action"] in ("reminder", "payment_overdue"):
            how = "auto-approved" if x.get("auto_approved") else f"for approval ({x.get('reason')})"
            print(f"{x['deal_id']} {x['task_id']}: drafted {x['msg_id']} {how}")
        else:
            print(f"{x['deal_id']} {x['task_id']}: no response after the reminder limit; opened {', '.join(x['opened'])}")
    if not acts:
        print("No reminders due.")
    if a.send:
        return _send(store, rules, actor, None, False)
    return 0


def _send(store, rules, actor, deal, dry_run) -> int:
    results = outbox.send_approved(store, rules, actor, deal_id=deal, dry_run=dry_run)
    for r in results:
        print(f"{r['deal_id']} {r['msg_id']} to {r['to']}: {r['result']}")
    if not results:
        print("Nothing approved and waiting to go out.")
    return 1 if any(r["result"].startswith("failed") for r in results) else 0


def cmd_outbox(a) -> int:
    store, rules, actor = _ctx(a)
    if a.action == "send":
        return _send(store, rules, actor, a.deal, a.dry_run)
    if a.action == "list":
        n = 0
        for did in ([a.deal] if a.deal else store.deal_ids()):
            st = fold(store.events(did))
            for m in st.messages.values():
                if a.all or m.status in ("draft", "approved", "exported", "failed"):
                    n += 1
                    auto = " (auto)" if m.auto_approved else ""
                    print(f"{did:<14}{m.msg_id:<5}{m.status + auto:<16}{m.category:<12}{m.channel:<6}{m.to_name[:28]:<29}{m.subject[:70]}")
        print(f"{n} message(s).")
        return 0
    if not (a.deal and a.msg):
        raise ValueError(f"outbox {a.action} needs DEAL and MESSAGE ids")
    if a.action == "show":
        m = fold(store.events(a.deal)).messages[a.msg]
        print(f"To: {m.to_name} <{m.to_email or m.to_address or 'no address'}> by {m.channel}\nStatus: {m.status}\n"
              f"Subject: {m.subject}\n\n{m.body}")
    elif a.action == "approve":
        outbox.approve(store, a.deal, a.msg, actor)
        print(f"Approved {a.msg}. Send approved email with: ptk deal outbox send")
    elif a.action == "reject":
        outbox.reject(store, a.deal, a.msg, actor, a.reason or "rejected")
        print(f"Rejected {a.msg}.")
    elif a.action == "edit":
        body = Path(a.body_file).read_text(encoding="utf-8") if a.body_file else None
        outbox.edit(store, a.deal, a.msg, actor, a.subject, body)
        print(f"Edited {a.msg}.")
    elif a.action == "mark-mailed":
        outbox.mark_mailed(store, a.deal, a.msg, actor, a.date)
        print(f"Marked {a.msg} delivered.")
    return 0


def cmd_note(a) -> int:
    store, _, actor = _ctx(a)
    actions.add_note(store, a.deal, actor, a.text)
    print("Noted.")
    return 0


def cmd_log(a) -> int:
    store, _, _ = _ctx(a)
    events = store.events(a.deal)
    if not events:
        raise ValueError(f"no deal {a.deal}")
    if a.json:
        print(json.dumps([e.__dict__ for e in events], indent=2))
        return 0
    for e in events:
        detail = {k: v for k, v in e.payload.items() if k not in ("record", "body", "message", "task", "result")}
        if "task" in e.payload:
            detail = {"task": e.payload["task"]["task_id"], "code": e.payload["task"]["code"]}
        if "message" in e.payload:
            detail = {"msg": e.payload["message"]["msg_id"], "to": e.payload["message"]["to_name"]}
        if "result" in e.payload:
            detail = {"asset": e.payload.get("asset_id"), "status": e.payload["result"].get("status")}
        print(f"{e.id:>5} {e.ts} {e.actor:<16} {e.type:<24} {json.dumps(detail, ensure_ascii=False)[:120]}")
    return 0


def cmd_verify_log(a) -> int:
    store, _, _ = _ctx(a)
    problems = store.verify()
    for p in problems:
        print(p)
    n = len(store.events())
    print(f"{n} event(s); " + ("hash chain intact." if not problems else f"{len(problems)} problem(s)."))
    return 1 if problems else 0


def cmd_export(a) -> int:
    from .export import collect, write_csv, write_xlsx
    store, rules, _ = _ctx(a)
    data = collect(store, rules, _today(a))
    if not (a.csv or a.xlsx):
        raise ValueError("give --csv and/or --xlsx")
    if a.csv:
        print(f"Wrote {write_csv(data, a.csv)}")
    if a.xlsx:
        print(f"Wrote {write_xlsx(data, a.xlsx)}")
    return 0


def cmd_serve(a) -> int:
    from .server import serve
    store, rules, actor = _ctx(a)
    serve(store, rules, actor, a.host, a.port, open_browser=a.open)
    return 0


def cmd_template(a) -> int:
    path = Path(a.path)
    if a.kind == "deal-json":
        path.write_text(json.dumps(EXAMPLE_DEAL, indent=2), encoding="utf-8")
    else:
        import csv
        cols = {"deal-csv": DEAL_CSV_COLUMNS, "statement": actions.STATEMENT_COLUMNS,
                "lease-chain": __import__("ptk.leasehold", fromlist=["LEASE_COLUMNS"]).LEASE_COLUMNS}[a.kind]
        with open(path, "w", newline="", encoding="utf-8") as fh:
            csv.writer(fh).writerow(cols)
    print(f"Wrote {path}")
    return 0


def add_parser(sub) -> None:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", help="deals database (default: $PTK_DEALS_DB or ./deals.db)")
    common.add_argument("--by", help="your name for the audit log (default: $PTK_USER or your login)")
    common.add_argument("--rules", help="rules JSON (default: ptk-rules.json beside the database, else built-in)")
    common.add_argument("--today", help=argparse.SUPPRESS)

    deal = sub.add_parser("deal", help="follow signed deals: intake, verify, curative, closing, record, payment")
    ds = deal.add_subparsers(dest="deal_cmd", required=True)

    def p(name, fn, help_):
        s = ds.add_parser(name, help=help_, parents=[common])
        s.set_defaults(fn=fn)
        return s

    s = p("intake", cmd_intake, "create a deal from a deal JSON and run intake checks (or amend one with --reason)")
    s.add_argument("file")
    s.add_argument("--reason", help="amend an existing deal; the reason goes in the audit log")
    s.add_argument("--layer", action="append", help="survey/PLSS layer for the map check (repeatable)")
    s.add_argument("--fields", help="field-name overrides for the map check, as in ptk mapcheck")
    s = p("import", cmd_import, "create deals from a deal sheet CSV (one row per asset)")
    s.add_argument("file")
    s.add_argument("--reason", help="amend deals that already exist")
    s = p("list", cmd_list, "every deal with its stage and next step")
    s.add_argument("--json", action="store_true")
    s = p("status", cmd_status, "one deal's six stages, blockers and next steps")
    s.add_argument("deal")
    s.add_argument("--json", action="store_true")
    s = p("verify", cmd_verify, "run title on each asset: does the seller own what it sold? (exit 1 if not clear)")
    s.add_argument("deal")
    s.add_argument("--asset", action="append", help="only these asset ids")
    s = p("title-run", cmd_title_run, "start the title run: a workspace per asset (search plan, runsheet, takeoff) linked to the deal")
    s.add_argument("deal")
    s.add_argument("--asset", action="append", help="only these asset ids")
    s.add_argument("--from", dest="start", type=int, default=1850, help="first year to search (default 1850: back to the patent)")
    s = p("tasks", cmd_tasks, "the curative tracker for a deal")
    s.add_argument("deal")
    s.add_argument("--all", action="store_true", help="include closed tasks")
    s.add_argument("--stage", choices=STAGES)
    s.add_argument("--json", action="store_true")
    s = p("task", cmd_task, "update a task: status, note, due date, party")
    s.add_argument("deal")
    s.add_argument("task")
    s.add_argument("--status", choices=["open", "requested", "received", "cleared", "waived"])
    s.add_argument("--note")
    s.add_argument("--reason", help="required to waive")
    s.add_argument("--attorney", help="the attorney waiving a legal-question task")
    s.add_argument("--opinion", help="opinion id (OP#) that clears a legal-question task")
    s.add_argument("--due")
    s.add_argument("--party", help="who must act (name)")
    s = p("task-add", cmd_task_add, "open a task by hand")
    s.add_argument("deal")
    s.add_argument("message")
    s.add_argument("--stage", default="curative", choices=STAGES)
    s.add_argument("--severity", default="review", choices=["stop", "review"])
    s.add_argument("--party", default="landman", help="landman, attorney, acquisitions, seller, operator or third_party")
    s.add_argument("--party-name")
    s.add_argument("--asset")
    s.add_argument("--attorney-question", action="store_true", help="only the attorney can clear it")
    s = p("opinion", cmd_opinion, "record the examining attorney's opinion; clears the tasks it covers")
    s.add_argument("deal")
    s.add_argument("--attorney", required=True)
    s.add_argument("--date")
    s.add_argument("--file", help="the opinion document")
    s.add_argument("--covers", help="task ids it resolves, comma-separated, or 'all' for every open legal question")
    s.add_argument("--requirement", action="append", help="a requirement in the opinion (repeatable); each becomes a task")
    s.add_argument("--kind", default="title opinion", help="e.g. acquisition title opinion, drilling title opinion")
    s = p("draft", cmd_draft, "draft the curative instrument for a task (affidavit, ratification, correction, release)")
    s.add_argument("deal")
    s.add_argument("task")
    s.add_argument("--template")
    s.add_argument("--out", help="folder (default: deals/<deal>/drafts beside the database)")
    s = p("request", cmd_request, "draft the first request for a task to the party who must act (for approval)")
    s.add_argument("deal")
    s.add_argument("task")
    s.add_argument("--to", help="recipient name, if not the task's party")
    s = p("package", cmd_package, "draft the closing package: conveyances, Exhibit A, closing statement, checklist")
    s.add_argument("deal")
    s.add_argument("--out")
    s.add_argument("--reason", help="redraft an approved package (resets approval)")
    s = p("closing", cmd_closing, "approve the package, record signing, or release funds (gated)")
    s.add_argument("deal")
    s.add_argument("action", choices=["approve", "sign", "release"])
    s.add_argument("--date")
    s.add_argument("--amount")
    s.add_argument("--note")
    s = p("record", cmd_record, "log the recorded conveyance in a county")
    s.add_argument("deal")
    s.add_argument("--county", required=True)
    s.add_argument("--recorded", required=True, help="recording date")
    s.add_argument("--instrument-no")
    s.add_argument("--volume")
    s.add_argument("--page")
    s.add_argument("--series", default="OPR", help="record series, e.g. OPR, DR, OR")
    s.add_argument("--state", choices=["TX", "NM"])
    s = p("notify", cmd_notify, "draft transfer notices to every operator and payor (after recording)")
    s.add_argument("deal")
    s.add_argument("--before-recording", action="store_true")
    s = p("division-order", cmd_division_order, "log a division order and check its decimal against title")
    s.add_argument("deal")
    s.add_argument("--payor", required=True)
    s.add_argument("--decimal", required=True)
    s.add_argument("--well")
    s.add_argument("--type", help="RI, NPRI, ORRI, NRI or UMI")
    s.add_argument("--received")
    s = p("expect", cmd_expect, "enter an expected payment decimal by hand")
    s.add_argument("deal")
    s.add_argument("--asset", required=True)
    s.add_argument("--payor", required=True)
    s.add_argument("--well", required=True)
    s.add_argument("--type", required=True)
    s.add_argument("--decimal", required=True)
    s = p("payment", cmd_payment, "log a payment or revenue statement line")
    s.add_argument("deal")
    s.add_argument("--payor", required=True)
    s.add_argument("--received", required=True)
    s.add_argument("--amount")
    s.add_argument("--decimal")
    s.add_argument("--well")
    s.add_argument("--type")
    s.add_argument("--period")
    s.add_argument("--owner", help="payee name as shown on the statement")
    s.add_argument("--suspense", action="store_true", help="the statement shows the interest in suspense")
    s = p("reconcile", cmd_reconcile, "import an operator statement CSV and check the buyer's decimals")
    s.add_argument("deal")
    s.add_argument("statement")
    s = p("remind", cmd_remind, "draft due reminders (routine ones auto-approve per the rules) and flag late payments")
    s.add_argument("--deal")
    s.add_argument("--send", action="store_true", help="send approved email afterwards")
    s = p("outbox", cmd_outbox, "list, show, approve, reject, edit, send or mark-mailed messages")
    s.add_argument("action", choices=["list", "show", "approve", "reject", "edit", "send", "mark-mailed"])
    s.add_argument("deal", nargs="?")
    s.add_argument("msg", nargs="?")
    s.add_argument("--all", action="store_true")
    s.add_argument("--reason")
    s.add_argument("--subject")
    s.add_argument("--body-file")
    s.add_argument("--date")
    s.add_argument("--dry-run", action="store_true")
    s = p("note", cmd_note, "add a note to a deal's log")
    s.add_argument("deal")
    s.add_argument("text")
    s = p("log", cmd_log, "a deal's full audit log")
    s.add_argument("deal")
    s.add_argument("--json", action="store_true")
    p("verify-log", cmd_verify_log, "check the log's hash chain (exit 1 if anything was edited outside ptk)")
    s = p("export", cmd_export, "write every deal's status to CSV or a workbook")
    s.add_argument("--csv")
    s.add_argument("--xlsx")
    s = p("serve", cmd_serve, "local dashboard: deals, curative tracker, approval queue (http://127.0.0.1:8780)")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8780)
    s.add_argument("--open", action="store_true", help="open the desk in your browser")
    s = p("template", cmd_template, "write a blank deal JSON, deal sheet, lease chain or statement CSV")
    s.add_argument("path")
    s.add_argument("--kind", default="deal-json", choices=["deal-json", "deal-csv", "lease-chain", "statement"])



# ---- ptk crm ------------------------------------------------------------------------

def cmd_crm_import(a) -> int:
    from . import crm
    store, _, actor = _ctx(a)
    r = crm.import_backup(store, a.file, actor, replace=a.replace)
    print(f"Imported {a.file}: {r['added']} added, {r['changed']} updated, {r['unchanged']} unchanged, {r['removed']} removed.")
    return 0


def cmd_crm_export(a) -> int:
    from . import crm
    store, _, _ = _ctx(a)
    Path(a.file).write_text(json.dumps(crm.export_backup(store), indent=2), encoding="utf-8")
    print(f"Wrote {a.file} (restorable in the CRM: menu > Restore backup)")
    return 0


def cmd_crm_list(a) -> int:
    from . import crm
    store, rules, _ = _ctx(a)
    state = crm.load_state(store)
    deals = crm.deals_by_customer(store, rules)
    if a.json:
        print(json.dumps(state["customers"], indent=2))
        return 0
    print(f"{'Id':<16}{'Owner':<32}{'County':<14}{'Stage':<16}{'Holdings':>8}  Title deal")
    for c in sorted(state["customers"], key=lambda c: c.get("name", "").lower()):
        d = (deals.get(c["id"]) or [{}])[0]
        print(f"{c['id']:<16}{c.get('name', '')[:31]:<32}{(c.get('county') or '')[:13]:<14}{c.get('status', ''):<16}"
              f"{len(c.get('interests') or []):>8}  {d.get('deal_id', '')} {d.get('stage_title', '')}")
    print(f"{len(state['customers'])} owner(s).")
    return 0


def cmd_crm_handoff(a) -> int:
    from . import crm
    store, rules, actor = _ctx(a)
    r = crm.start_deal(store, rules, a.customer, actor, a.buyer, a.state, _today(a))
    print(f"Created {r['deal_id']} from CRM customer {a.customer}. Draft record: {r['file']}")
    for x in r["assumptions"]:
        print(f"  to confirm: {x}")
    _print_summary(r["summary"], verbose=False)
    return 0


def add_crm_parser(sub) -> None:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", help="deals database (default: $PTK_DEALS_DB or ./deals.db)")
    common.add_argument("--by", help="your name for the audit log (default: $PTK_USER or your login)")
    common.add_argument("--rules", help="rules JSON")
    common.add_argument("--today", help=argparse.SUPPRESS)
    crm_p = sub.add_parser("crm", help="the Landowner CRM on the deal desk database: import, export, list, hand off")
    cs = crm_p.add_subparsers(dest="crm_cmd", required=True)
    s = cs.add_parser("import", help="load a CRM backup (land-crm-data-export.json) into the database", parents=[common])
    s.add_argument("file")
    s.add_argument("--replace", action="store_true", help="also remove owners that aren't in the file")
    s.set_defaults(fn=cmd_crm_import)
    s = cs.add_parser("export", help="write the CRM backup file (restorable in the CRM app)", parents=[common])
    s.add_argument("file")
    s.set_defaults(fn=cmd_crm_export)
    s = cs.add_parser("list", help="every owner with stage, holdings and title deal", parents=[common])
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_crm_list)
    s = cs.add_parser("handoff", help="start title follow-up for a signed owner (creates the deal and runs intake)", parents=[common])
    s.add_argument("customer", help="CRM customer id (ptk crm list)")
    s.add_argument("--buyer", required=True, help="the entity taking title")
    s.add_argument("--state", choices=["TX", "NM"], help="needed when the county isn't in the TX/NM table")
    s.set_defaults(fn=cmd_crm_handoff)



# ---- ptk audit ----------------------------------------------------------------------

def cmd_audit(a) -> int:
    from .accuracy import AREAS, format_report, run_audit
    store, rules, _ = _ctx(a)
    areas = tuple(x.strip().title() for x in a.only.split(",")) if a.only else AREAS
    report = run_audit(store, rules, _today(a), areas)
    if a.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(format_report(report))
    if a.out:
        Path(a.out).write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
    return 0 if report.ok else 1


def add_desk_parser(sub) -> None:
    d = sub.add_parser("desk", help="set up the desk: a folder with the database, your rules and a start file")
    ds = d.add_subparsers(dest="desk_cmd", required=True)
    s = ds.add_parser("init", help="create a desk folder (database, ptk-rules.json, 'Start Land Desk' launcher)")
    s.add_argument("folder")
    s.add_argument("--crm", help="also import a CRM backup (land-crm-data-export.json)")
    s.add_argument("--by", help="your name for the audit log")
    s.add_argument("--db", help=argparse.SUPPRESS)
    s.add_argument("--rules", help=argparse.SUPPRESS)
    s.set_defaults(fn=cmd_desk_init)


def add_audit_parser(sub) -> None:
    s = sub.add_parser("audit", help="accuracy checks on everything: engine math, records, deals, CRM, rules (exit 1 on any FAIL)")
    s.add_argument("--db", help="deals database (default: $PTK_DEALS_DB or ./deals.db)")
    s.add_argument("--by", help=argparse.SUPPRESS)
    s.add_argument("--rules", help="rules JSON")
    s.add_argument("--today", help=argparse.SUPPRESS)
    s.add_argument("--only", help="comma-separated areas: engine, records, deals, crm, rules, setup")
    s.add_argument("--json", action="store_true")
    s.add_argument("--out", help="also write the report as JSON")
    s.set_defaults(fn=cmd_audit)


def run(a) -> int:
    try:
        sys.stdout.reconfigure(errors="replace")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass
    try:
        return a.fn(a)
    except KeyError as e:
        raise ValueError(e.args[0] if e.args else str(e)) from e
    except OSError as e:  # a missing or locked file is bad input, not a crash
        raise ValueError(f"{e.strerror or e}: {e.filename}" if getattr(e, "filename", None) else str(e)) from e
    finally:
        while _OPEN:
            _OPEN.pop().close()


__all__ = ["add_parser", "add_crm_parser", "add_audit_parser", "add_desk_parser", "run"]
