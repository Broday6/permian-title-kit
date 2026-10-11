"""Everything a person does to move a deal along, each one a logged event.

The CLI and the dashboard both call these, so a rule enforced here (an
attorney task can't be cleared without an opinion; funds can't be released
past the gate; a notice waits for the recording) holds no matter where the
click came from.
"""

from __future__ import annotations

import csv
import difflib
from datetime import date
from fractions import Fraction
from pathlib import Path
from typing import Any

from ..dates import iso
from ..fracs import FractionError, fmt, parse_fraction, to_decimal
from ..ledger import name_key
from ..runsheet import image_key
from . import outbox
from .rules import STAGES, Rules
from .state import TASK_STATUSES, attorney_tasks, fold, funds_gate
from .store import Store
from .tasks import TaskSpec, sync


def _state(store: Store, deal_id: str):
    if not store.exists(deal_id):
        raise KeyError(f"no deal {deal_id}")
    return fold(store.events(deal_id))


# ---- tasks ------------------------------------------------------------------

def update_task(store: Store, deal_id: str, task_id: str, actor: str, status: str | None = None, note: str = "",
                reason: str | None = None, attorney: str | None = None, opinion: str | None = None,
                due: str | None = None, party_name: str | None = None) -> dict[str, Any]:
    st = _state(store, deal_id)
    task = st.tasks.get(task_id)
    if task is None:
        raise KeyError(f"{deal_id} has no task {task_id}")
    if status == "resolved":
        raise ValueError("'resolved' is set by the program when a flag stops being raised; use cleared or waived")
    if status and status not in TASK_STATUSES:
        raise ValueError(f"status must be one of {', '.join(s for s in TASK_STATUSES if s != 'resolved')}")
    payload: dict[str, Any] = {"task_id": task_id}
    if status == "waived":
        if not reason:
            raise ValueError("a waiver needs --reason (it goes in the audit log)")
        if task.requires_attorney and not attorney:
            raise ValueError(f"{task_id} [{task.code}] is a legal question: only the examining attorney can waive it "
                             "(--attorney NAME), or cover it in an opinion (ptk deal opinion)")
        payload.update(reason=reason, attorney=attorney)
    if status == "cleared" and task.requires_attorney:
        op = next((o for o in st.opinions if o["opinion_id"] == opinion), None) if opinion else None
        if op is None or task_id not in (op.get("covers") or []):
            covered = f"{opinion} doesn't cover {task_id}. " if op is not None else ""
            raise ValueError(f"{task_id} [{task.code}] is a legal question. {covered}Record the attorney's opinion that "
                             f"covers it (ptk deal opinion {deal_id} --covers {task_id}), or have the attorney waive it")
        payload["opinion"] = opinion
    if status:
        payload["status"] = status
    if note:
        payload["note"] = note
    if due:
        payload["due"] = iso(due)
    if party_name:
        payload["party_name"] = party_name
    if len(payload) == 1:
        raise ValueError("nothing to change: give --status, --note, --due or --party")
    store.append(deal_id, "task.updated", payload, actor)
    if status in ("cleared", "waived"):
        outbox.withdraw_stale(store, deal_id)
    return payload


def add_task(store: Store, rules: Rules, deal_id: str, actor: str, message: str, stage: str = "curative",
             severity: str = "review", party: str = "landman", party_name: str | None = None,
             asset_id: str | None = None, code: str = "MANUAL", requires_attorney: bool = False,
             today: date | None = None) -> str:
    st = _state(store, deal_id)
    if stage not in STAGES:
        raise ValueError(f"stage must be one of {', '.join(STAGES)}")
    spec = TaskSpec(code, message, "manual", asset_id, ref=f"manual:{len(st.tasks) + 1}", party=party, stage=stage,
                    requirement=message, requires_attorney=requires_attorney, force_severity=severity, party_name=party_name)
    r = sync(store, st, rules, actor, "manual", asset_id, [spec], today, resolve_missing=False)
    return r["opened"][0] if r["opened"] else ""


def record_opinion(store: Store, rules: Rules, deal_id: str, actor: str, attorney: str, on: str | None = None,
                   file: str | None = None, covers: list[str] | None = None, requirements: list[str] | None = None,
                   kind: str = "title opinion", today: date | None = None) -> dict[str, Any]:
    """Log the examining attorney's opinion; the tasks it covers are cleared, its requirements become tasks."""
    st = _state(store, deal_id)
    covers = covers or []
    if covers == ["all"]:
        covers = [t.task_id for t in attorney_tasks(st) if not t.closed]
    unknown = [c for c in covers if c not in st.tasks]
    if unknown:
        raise KeyError(f"{deal_id} has no task {', '.join(unknown)}")
    on_iso = iso(on) if on else (today or date.today()).isoformat()

    def build(cur):
        return {"opinion_id": f"OP{store.next_number(cur, deal_id, 'opinion.recorded')}", "attorney": attorney,
                "date": on_iso, "file": file, "kind": kind, "covers": covers, "requirements": requirements or []}

    op_id = store.append(deal_id, "opinion.recorded", build, actor).payload["opinion_id"]
    for tid in covers:
        if not st.tasks[tid].closed:
            store.append(deal_id, "task.updated", {"task_id": tid, "status": "cleared", "opinion": op_id,
                                                   "note": f"covered by {op_id} ({attorney})"}, actor)
    outbox.withdraw_stale(store, deal_id)
    st = fold(store.events(deal_id))
    specs = [TaskSpec("OPINION_REQUIREMENT", req, "opinion", None, ref=f"{op_id}:{i}", requirement=req)
             for i, req in enumerate(requirements or [], start=1)]
    opened = sync(store, st, rules, actor, "opinion", None, specs, today, resolve_missing=False)["opened"] if specs else []
    return {"opinion_id": op_id, "cleared": covers, "requirements": opened}


# ---- closing ----------------------------------------------------------------

def closing(store: Store, rules: Rules, deal_id: str, actor: str, action: str, on: str | None = None,
            amount: str | None = None, note: str = "") -> dict[str, Any]:
    st = _state(store, deal_id)
    pkg = st.package
    if action == "approve":
        if "drafted" not in pkg:
            raise ValueError(f"no package yet: ptk deal package {deal_id}")
        store.append(deal_id, "package.approved", {"note": note}, actor)
    elif action == "sign":
        if "approved" not in pkg:
            raise ValueError("approve the package before recording signatures")
        store.append(deal_id, "package.signed", {"date": iso(on) if on else date.today().isoformat(), "note": note}, actor)
    elif action == "release":
        if "released" in pkg:
            raise ValueError(f"funds were already released on {pkg['released'].get('date')} by {pkg['released'].get('actor')}")
        reasons = funds_gate(st, rules)
        if reasons:
            raise ValueError("funds stay held: " + "; ".join(reasons))
        store.append(deal_id, "funds.released", {"date": iso(on) if on else date.today().isoformat(),
                                                  "amount": amount or st.record.get("price"), "note": note}, actor)
    else:
        raise ValueError("action must be approve, sign or release")
    return {"action": action}


# ---- record and notify -------------------------------------------------------

def log_recording(store: Store, deal_id: str, actor: str, county: str, recorded: str, instrument_no: str = "",
                  volume: str = "", page: str = "", series: str = "OPR", state: str | None = None,
                  instrument: str = "conveyance") -> dict[str, Any]:
    st = _state(store, deal_id)
    match = [(s, c) for s, c in st.counties() if name_key(c) == name_key(county) and (not state or s == state.upper())]
    if not match:
        raise ValueError(f"{county} isn't a county in {deal_id}: {', '.join(c + ' ' + s for s, c in st.counties())}")
    s, c = match[0]
    if not (instrument_no or (volume and page)):
        raise ValueError("give the instrument number, or the volume and page")
    rec_iso = iso(recorded)
    try:
        key = image_key(s, c, series, volume, page, instrument_no, rec_iso[:4])
    except ValueError:
        key = ""
    payload = {"state": s, "county": c, "series": series, "instrument_no": instrument_no, "volume": volume, "page": page,
               "recorded": rec_iso, "instrument": instrument, "image_key": key}
    store.append(deal_id, "recording.logged", payload, actor)
    return payload


def notify(store: Store, rules: Rules, deal_id: str, actor: str, allow_unrecorded: bool = False,
           today: date | None = None) -> list[dict[str, Any]]:
    """Draft a transfer notice for every operator and payor not yet notified (each waits for approval)."""
    st = _state(store, deal_id)
    out = []
    for target in st.notice_targets():
        if st.notified(target["name"]):
            continue
        pending = [m for m in st.messages.values() if m.template == "transfer_notice"
                   and name_key(m.to_name) == name_key(target["name"]) and m.status in ("draft", "approved", "exported")]
        if pending:
            out.append({"to": target["name"], "result": f"{pending[0].msg_id} is already {pending[0].status}"})
            continue
        asset = next(a for a in st.assets if a["id"] == target["asset_id"])
        if not allow_unrecorded and not st.recorded_in(asset["state"], asset["county"]):
            out.append({"to": target["name"], "result": f"held: record in {asset['county']} County first "
                                                        f"(or pass --before-recording)"})
            continue
        spec = TaskSpec("DIVISION_ORDER_PENDING", f"Waiting on {target['name']} ({target['role']}) to confirm the transfer "
                        "and send a division order.", "notices", None, ref=f"do:{target['name']}",
                        party_name=target["name"])
        opened = sync(store, st, rules, actor, "notices", None, [spec], today, resolve_missing=False)
        tid = (opened["opened"] or opened["existing"] or opened["reopened"] or [None])[0]
        r = outbox.draft_message(store, rules, deal_id, actor, "transfer_notice", target["name"], tid,
                                 extra={"role_title": outbox.ROLE_TITLE.get(target["role"], "Operator")})
        out.append({"to": target["name"], "result": f"drafted {r['msg_id']} for approval", **r})
        st = fold(store.events(deal_id))
    return out


def _close_tasks(store: Store, st, code: str, party: str | None, actor: str, status: str, note: str) -> list[str]:
    done = []
    for t in st.tasks.values():
        if t.code == code and not t.closed and (party is None or name_key(t.party_name) == name_key(party)):
            store.append(st.deal_id, "task.updated", {"task_id": t.task_id, "status": status, "note": note}, actor)
            done.append(t.task_id)
    if done:
        outbox.withdraw_stale(store, st.deal_id)
    return done


def _expected_for(st, payor: str, well: str | None, typ: str | None) -> dict[str, Any] | None:
    """The buyer's expected decimal from one payor for one well and type, summed across the deal's assets.

    An operator pays one decimal per owner per well, so two interests bought in
    the same well (say, from two sellers) are checked as their total.
    """
    hits = []
    for e in st.expected.values():
        if name_key(e["payor"]) != name_key(payor):
            continue
        if well and name_key(e.get("well")) != name_key(well) and name_key(e.get("well_api")) != name_key(well):
            continue
        if typ and e["type"] != typ.upper():
            continue
        hits.append(e)
    if not hits:
        return None
    types = sorted({e["type"] for e in hits})
    if len(types) > 1:
        hits = [e for e in hits if e["type"] == types[0]]  # no type given: compare against one type at a time
    total = sum((parse_fraction(e["decimal"]) for e in hits), Fraction(0))
    return {**hits[0], "decimal": fmt(total), "decimal_display": to_decimal(total),
            "assets": sorted({e.get("asset_id") for e in hits})}


def _decimal_check(store: Store, rules: Rules, st, actor: str, code: str, source: str, payor: str, well: str | None,
                   typ: str | None, decimal: str, today: date | None) -> dict[str, Any]:
    exp = _expected_for(st, payor, well, typ)
    if exp is None:
        return {"matches": None, "note": "no expected decimal on file for this payor and well"}
    got = parse_fraction(decimal)
    want = parse_fraction(exp["decimal"])
    if abs(got - want) <= rules.decimal_tolerance:
        return {"matches": True, "expected": exp["decimal_display"]}
    spec = TaskSpec(code, f"{payor} shows {to_decimal(got)} for {exp.get('well')} ({exp['type']}); title says "
                    f"{exp['decimal_display']} ({exp['decimal']}). Difference {to_decimal(got - want)}.",
                    source, exp.get("asset_id"), ref=f"{code}:{payor}|{exp.get('well')}|{exp['type']}", party_name=payor)
    r = sync(store, st, rules, actor, source, exp.get("asset_id"), [spec], today, resolve_missing=False)
    for tid in r["opened"]:
        outbox.draft_message(store, rules, st.deal_id, actor, "decimal_query", payor, tid)
    return {"matches": False, "expected": exp["decimal_display"], "tasks": r["opened"]}


def division_order(store: Store, rules: Rules, deal_id: str, actor: str, payor: str, decimal: str,
                   well: str | None = None, typ: str | None = None, received: str | None = None,
                   today: date | None = None) -> dict[str, Any]:
    st = _state(store, deal_id)
    parse_fraction(decimal)
    check = _decimal_check(store, rules, st, actor, "DO_DECIMAL_MISMATCH", "division_orders", payor, well, typ, decimal, today)
    store.append(deal_id, "division_order.received", {"payor": payor, "well": well, "type": (typ or "").upper() or None,
                                                       "decimal": decimal, "received": iso(received) if received else
                                                       (today or date.today()).isoformat(), **check}, actor)
    st = fold(store.events(deal_id))
    cleared: list[str] = []
    if check["matches"]:
        cleared = _close_tasks(store, st, "DIVISION_ORDER_PENDING", payor, actor, "cleared",
                               "division order received at the title decimal")
    else:
        why = ("its decimal doesn't match title" if check["matches"] is False
               else "no title decimal on file to check it against")
        for t in st.tasks.values():
            if t.code == "DIVISION_ORDER_PENDING" and not t.closed and name_key(t.party_name) == name_key(payor):
                store.append(deal_id, "task.updated", {"task_id": t.task_id, "status": "received",
                                                       "note": f"division order received, but {why}"}, actor)
    return {**check, "cleared": cleared}


def expect(store: Store, deal_id: str, actor: str, asset_id: str, payor: str, well: str, typ: str, decimal: str) -> dict[str, Any]:
    st = _state(store, deal_id)
    if not any(a["id"] == asset_id for a in st.assets):
        raise KeyError(f"{deal_id} has no asset {asset_id}")
    value = parse_fraction(decimal)
    payload = {"key": f"{asset_id}|{well}|{typ.upper()}", "asset_id": asset_id, "payor": payor, "well": well, "well_api": None,
               "type": typ.upper(), "decimal": fmt(value), "decimal_display": to_decimal(value), "source": f"entered by {actor}"}
    store.append(deal_id, "payment.expected", payload, actor)
    return payload


def payment(store: Store, rules: Rules, deal_id: str, actor: str, payor: str, received: str, amount: str = "",
            decimal: str | None = None, well: str | None = None, typ: str | None = None, period: str = "",
            status: str = "paid", owner: str | None = None, today: date | None = None) -> dict[str, Any]:
    st = _state(store, deal_id)
    if status not in ("paid", "suspense"):
        raise ValueError("status must be paid or suspense")
    result: dict[str, Any] = {"payor": payor, "well": well, "type": (typ or "").upper() or None, "received": iso(received),
                              "amount": amount, "decimal": decimal, "period": period, "status": status, "owner": owner}
    notes = []
    if decimal:
        result.update(_decimal_check(store, rules, st, actor, "PAY_DECIMAL_MISMATCH", "payments", payor, well, typ, decimal, today))
    if owner and name_key(owner) != name_key(st.record["buyer"]):
        spec = TaskSpec("PAYEE_NAME_VARIANT", f"{payor} pays '{owner}', not '{st.record['buyer']}'.", "payments", None,
                        ref=f"payee:{payor}|{owner}", party_name=payor)
        sync(store, fold(store.events(deal_id)), rules, actor, "payments", None, [spec], today, resolve_missing=False)
        notes.append("payee name differs from the buyer")
    store.append(deal_id, "payment.received", result, actor)
    st = fold(store.events(deal_id))
    exp = _expected_for(st, payor, well, typ)
    if status == "suspense":
        spec = TaskSpec("PAYMENT_IN_SUSPENSE", f"{payor} shows the interest in suspense ({period or received}).", "payments",
                        exp.get("asset_id") if exp else None, ref=f"suspense:{payor}|{well}", party_name=payor)
        r = sync(store, st, rules, actor, "payments", exp.get("asset_id") if exp else None, [spec], today, resolve_missing=False)
        for tid in r["opened"]:
            outbox.draft_message(store, rules, deal_id, actor, "payment_followup", payor, tid)
    else:
        _close_tasks(store, st, "PAYMENT_OVERDUE", payor, actor, "resolved", f"payment received {result['received']}")
        _close_tasks(store, fold(store.events(deal_id)), "PAYMENT_IN_SUSPENSE", payor, actor, "resolved",
                     f"payment received {result['received']}")
        _close_tasks(store, fold(store.events(deal_id)), "DIVISION_ORDER_PENDING", payor, actor, "cleared",
                     f"first payment received {result['received']}")
    return {**result, "notes": notes}


STATEMENT_COLUMNS = ["payor", "well", "owner", "decimal", "type", "amount", "check_date", "period", "status"]


def reconcile(store: Store, rules: Rules, deal_id: str, actor: str, statement: str | Path,
              today: date | None = None) -> dict[str, Any]:
    """Import an operator's revenue statement or pay deck (CSV) and check the buyer's lines against title."""
    st = _state(store, deal_id)
    buyer = st.record["buyer"]
    with open(statement, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    missing = [c for c in ("payor", "owner", "decimal") if rows and c not in rows[0]]
    if missing:
        raise ValueError(f"statement is missing columns: {', '.join(missing)} (expected {', '.join(STATEMENT_COLUMNS)})")
    matched, skipped, variants = [], 0, []
    for i, row in enumerate(rows, start=2):
        owner = (row.get("owner") or "").strip()
        ratio = difflib.SequenceMatcher(None, name_key(owner), name_key(buyer)).ratio()
        if name_key(owner) != name_key(buyer) and ratio < 0.85:
            skipped += 1
            continue
        if name_key(owner) != name_key(buyer):
            variants.append(owner)
        try:
            r = payment(store, rules, deal_id, actor, row["payor"].strip(), row.get("check_date") or (today or date.today()).isoformat(),
                        amount=row.get("amount", ""), decimal=(row.get("decimal") or "").strip() or None,
                        well=(row.get("well") or "").strip() or None, typ=(row.get("type") or "").strip() or None,
                        period=row.get("period", ""), status=(row.get("status") or "paid").strip().lower() or "paid",
                        owner=owner, today=today)
        except (ValueError, FractionError) as e:
            raise ValueError(f"statement row {i}: {e}") from e
        matched.append(r)
    return {"matched": len(matched), "skipped_other_owners": skipped, "name_variants": variants,
            "mismatches": [m for m in matched if m.get("matches") is False]}


def add_note(store: Store, deal_id: str, actor: str, text: str) -> None:
    _state(store, deal_id)
    store.append(deal_id, "note.added", {"text": text}, actor)


__all__ = ["update_task", "add_task", "record_opinion", "closing", "log_recording", "notify", "division_order",
           "expect", "payment", "reconcile", "add_note"]
