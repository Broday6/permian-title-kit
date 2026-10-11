"""Write deal status back out: a CSV for any spreadsheet or CRM import, or a workbook (openpyxl).

One row per deal on the Deals sheet (stage, next action, opinion needed, open
and stop tasks), plus Tasks, Messages, Recordings and Payments sheets. The
deals database stays the record; these are reports.
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path
from typing import Any

from .rules import Rules
from .state import fold, summary
from .store import Store

DEAL_FIELDS = ["deal_id", "name", "buyer", "states", "kinds", "counties", "stage", "stage_title", "next",
               "opinion_required", "open_tasks", "stop_tasks", "messages_awaiting_approval", "updated"]
TASK_FIELDS = ["deal_id", "task_id", "code", "stage", "severity", "status", "party", "party_name", "asset_id",
               "requires_attorney", "due", "requirement", "message"]
MESSAGE_FIELDS = ["deal_id", "msg_id", "to_name", "channel", "template", "category", "status", "approved_by",
                  "auto_approved", "sent_at", "subject"]
RECORDING_FIELDS = ["deal_id", "state", "county", "series", "instrument_no", "volume", "page", "recorded", "image_key"]
PAYMENT_FIELDS = ["deal_id", "payor", "well", "type", "expected", "received", "amount", "decimal", "status"]


def collect(store: Store, rules: Rules, today: date | None = None) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {"deals": [], "tasks": [], "messages": [], "recordings": [], "payments": []}
    for did in store.deal_ids():
        st = fold(store.events(did))
        s = summary(st, rules, today)
        out["deals"].append({k: (", ".join(v) if isinstance(v, list) else v) for k, v in s.items() if k in DEAL_FIELDS})
        for t in st.tasks.values():
            out["tasks"].append({"deal_id": did, **{k: getattr(t, k) for k in TASK_FIELDS if k != "deal_id"}})
        for m in st.messages.values():
            out["messages"].append({"deal_id": did, **{k: getattr(m, k) for k in MESSAGE_FIELDS if k != "deal_id"}})
        for r in st.recordings:
            out["recordings"].append({"deal_id": did, **{k: r.get(k) for k in RECORDING_FIELDS if k != "deal_id"}})
        for e in st.expected.values():
            paid = st.payments_for(e["payor"], e.get("well"))
            last = paid[-1] if paid else {}
            out["payments"].append({"deal_id": did, "payor": e["payor"], "well": e.get("well"), "type": e["type"],
                                    "expected": e["decimal_display"], "received": last.get("received"),
                                    "amount": last.get("amount"), "decimal": last.get("decimal"),
                                    "status": last.get("status", "waiting")})
    return out


def write_csv(data: dict[str, list[dict[str, Any]]], path: str | Path) -> Path:
    path = Path(path)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=DEAL_FIELDS)
        w.writeheader()
        w.writerows(data["deals"])
    return path


def write_xlsx(data: dict[str, list[dict[str, Any]]], path: str | Path) -> Path:
    from ..xlsx import FILLS, _about, _body_font, _header, _openpyxl
    openpyxl, _, _, PatternFill, _ = _openpyxl()
    wb = openpyxl.Workbook()
    sheets = [("Deals", DEAL_FIELDS, data["deals"]), ("Tasks", TASK_FIELDS, data["tasks"]),
              ("Messages", MESSAGE_FIELDS, data["messages"]), ("Recordings", RECORDING_FIELDS, data["recordings"]),
              ("Payments", PAYMENT_FIELDS, data["payments"])]
    for i, (title, fields, rows) in enumerate(sheets):
        ws = wb.active if i == 0 else wb.create_sheet(title)
        ws.title = title
        _header(ws, fields, {"next": 70, "requirement": 60, "message": 60, "subject": 50, "name": 30})
        for r in rows:
            ws.append([r.get(f) if not isinstance(r.get(f), bool) else ("Y" if r.get(f) else "") for f in fields])
            if title == "Tasks" and r.get("status") in ("open", "requested", "received"):
                fill = FILLS.get(r.get("severity"))
                if fill:
                    for cell in ws[ws.max_row]:
                        cell.fill = PatternFill("solid", fgColor=fill)
        ws.auto_filter.ref = ws.dimensions
        _body_font(ws)
    _about(wb, ["Deal follow-up status (ptk deal export)",
                f"Exported {date.today().isoformat()}. The deals database is the record; this workbook is a report.",
                "Open tasks are shaded by severity: red stop, amber review."])
    path = Path(path)
    wb.save(path)
    return path
