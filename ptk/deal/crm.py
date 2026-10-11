"""The Landowner CRM, folded into the deal desk.

The CRM is the acquisition side: owners found in pooling exhibits and county
records, their interest holdings, every call, the outreach email cadence, and
the lead pipeline (New → Contacted → Interested → Negotiating → Under Contract →
Closed, plus Dead). ``ptk deal`` is the title side that starts once a deal is
signed. This module joins them:

* **One store.** CRM records live in the same append-only, hash-chained log as
  the deals, as their own streams: ``crm:<customer id>`` holds one owner
  (``crm.customer.saved`` with the whole record, ``crm.customer.deleted``),
  ``crm:settings`` holds the app settings. Current state is the latest saved
  record per id, so every change to an owner is in the audit trail too.
* **The CRM's own shapes.** Records are stored exactly as the CRM app holds
  them (see INTEGRATION and land-crm-schema.json from the old tool): money,
  acres and percentages as strings as entered, dates as local YYYY-MM-DD.
  ``import_backup`` / ``export_backup`` read and write the CRM's backup file.
* **The hand-off.** ``start_deal`` turns a CRM owner into a ``ptk deal``: the
  owner becomes the seller, each interest holding becomes an asset, and intake
  then lists everything acquisitions still has to supply (legal description,
  tract, interest, effective date, price, agreement). Nothing is guessed into a
  conveyance: CRM interest types are mapped to asset kinds and that mapping is
  opened as a task for acquisitions to confirm.
* **Status back.** ``deals_by_customer`` reports each owner's title deals and
  their stage, so the CRM can show where a signed deal stands.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any

from .. import counties
from ..ledger import name_key
from .store import Store, canonical

PREFIX = "crm:"
SETTINGS_ID = "crm:settings"
SETTINGS_KEYS = ("email", "tagColors", "knownTags", "lastBy", "lastBuyer")
STATUSES = ("New", "Contacted", "Interested", "Negotiating", "Under Contract", "Closed", "Dead")
_SAFE_ID = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def _check_id(cid: str) -> str:
    if not _SAFE_ID.match(cid or ""):
        raise ValueError(f"customer id {cid!r}: use letters, digits, '-', '_' or '.' (up to 64)")
    return cid


# ---- state ------------------------------------------------------------------------

def load_state(store: Store) -> dict[str, Any]:
    """The CRM's ``data`` object, rebuilt from the log: {customers, tagColors, knownTags, lastBy, lastBuyer, email}."""
    customers: dict[str, dict[str, Any]] = {}
    settings: dict[str, Any] = {}
    for ev in store.stream_events(PREFIX):
        if ev.type == "crm.customer.saved":
            customers[ev.payload["customer"]["id"]] = ev.payload["customer"]
        elif ev.type == "crm.customer.deleted":
            customers.pop(ev.payload["id"], None)
        elif ev.type == "crm.settings.saved":
            settings = ev.payload["settings"]
    state: dict[str, Any] = {"customers": list(customers.values()), "tagColors": {}, "knownTags": [], "lastBy": "",
                             "lastBuyer": ""}
    state.update({k: settings[k] for k in SETTINGS_KEYS if k in settings})
    return state


def customer(store: Store, cid: str) -> dict[str, Any] | None:
    return next((c for c in load_state(store)["customers"] if c.get("id") == cid), None)


def save_customer(store: Store, record: dict[str, Any], actor: str) -> bool:
    """Store one owner record as the CRM holds it. Returns False when nothing changed (no event)."""
    if not isinstance(record, dict) or not str(record.get("name") or "").strip():
        raise ValueError("a customer needs a name")
    cid = _check_id(str(record.get("id") or ""))
    if record.get("status") and record["status"] not in STATUSES:
        raise ValueError(f"status {record['status']!r} must be one of {', '.join(STATUSES)}")
    have = customer(store, cid)
    if have is not None and canonical(have) == canonical(record):
        return False
    store.append(PREFIX + cid, "crm.customer.saved", {"customer": record}, actor)
    return True


def delete_customer(store: Store, cid: str, actor: str) -> bool:
    """Remove an owner from the CRM. The log keeps every earlier version, so it can be recovered."""
    if customer(store, _check_id(cid)) is None:
        return False
    store.append(PREFIX + cid, "crm.customer.deleted", {"id": cid}, actor)
    return True


def save_settings(store: Store, settings: dict[str, Any], actor: str) -> bool:
    clean = {k: settings[k] for k in SETTINGS_KEYS if k in settings}
    current = load_state(store)
    if all(current.get(k) == v for k, v in clean.items()):
        return False
    store.append(SETTINGS_ID, "crm.settings.saved", {"settings": {**{k: current.get(k) for k in SETTINGS_KEYS}, **clean}}, actor)
    return True


def import_backup(store: Store, path: str | Path, actor: str, replace: bool = False) -> dict[str, int]:
    """Load a CRM backup file ({customers, tagColors, knownTags, lastBy, email}) into the store.

    Customers are matched by id: new ids are added, changed records are saved as a
    new version. ``replace`` also deletes customers that aren't in the file.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(data.get("customers"), list):
        raise ValueError(f"{Path(path).name} isn't a CRM backup: no customers list")
    added = changed = same = removed = 0
    before = {c["id"] for c in load_state(store)["customers"]}
    ids = set()
    for rec in data["customers"]:
        ids.add(rec.get("id"))
        existed = rec.get("id") in before
        if save_customer(store, rec, actor):
            changed += existed
            added += not existed
        else:
            same += 1
    if replace:
        for cid in before - ids:
            removed += delete_customer(store, cid, actor)
    save_settings(store, {k: data[k] for k in SETTINGS_KEYS if k in data}, actor)
    return {"added": added, "changed": changed, "unchanged": same, "removed": removed}


def export_backup(store: Store) -> dict[str, Any]:
    """The CRM backup shape, restorable in the CRM app (menu > Restore backup)."""
    state = load_state(store)
    return {"customers": state["customers"], **{k: state.get(k) for k in SETTINGS_KEYS if state.get(k) is not None}}


# ---- deals <-> CRM -------------------------------------------------------------------

def deals_by_customer(store: Store, rules) -> dict[str, list[dict[str, Any]]]:
    """For each CRM customer id, the title deals started from it with their stage."""
    from .state import fold, summary
    out: dict[str, list[dict[str, Any]]] = {}
    for did in store.deal_ids():
        st = fold(store.events(did))
        ids = {s.get("crm_id") for s in st.record.get("sellers", []) if s.get("crm_id")}
        if not ids:
            continue
        s = summary(st, rules)
        row = {"deal_id": did, "stage": s["stage"], "stage_title": s["stage_title"], "next": s["next"],
               "open_tasks": s["open_tasks"], "stop_tasks": s["stop_tasks"], "opinion_required": s["opinion_required"]}
        for cid in ids:
            out.setdefault(cid, []).append(row)
    return out


def asset_kind(interest_type: str) -> str:
    """CRM interest type → ptk asset kind. A guess from the label, so the hand-off asks acquisitions to confirm it."""
    t = (interest_type or "").lower()
    if "working" in t or t.strip() in ("wi", "leasehold"):
        return "wellbore"
    if "overrid" in t or "orri" in t:
        return "orri"
    if "non-part" in t or "npri" in t:
        return "royalty"
    return "mineral"  # "Royalty" on a pooling exhibit is usually a leased mineral owner


def state_for(county: str) -> str | None:
    try:
        return counties.lookup(county).state if county else None
    except KeyError:
        return None


def deal_from_customer(c: dict[str, Any], buyer: str, state: str | None = None, deal_id: str | None = None,
                       today: date | None = None) -> tuple[dict[str, Any], list[str]]:
    """A ``ptk deal`` record for one CRM owner, plus the assumptions made (each becomes a task to confirm)."""
    today = today or date.today()
    st = (state or state_for(c.get("county", "")) or "").upper()
    if st not in ("TX", "NM"):
        raise ValueError(f"{c.get('county') or 'No'} County isn't in the Texas/New Mexico county table; "
                         "choose the state (TX or NM) for this deal")
    if not buyer.strip():
        raise ValueError("name the buyer (the entity taking title)")
    did = deal_id or "D-" + re.sub(r"[^A-Za-z0-9_.-]", "-", f"{today.year}-{c['id']}")[:60]
    assumptions: list[str] = []
    interests = c.get("interests") or [{}]
    assets = []
    for i, it in enumerate(interests, start=1):
        kind = asset_kind(it.get("type", ""))
        if it.get("type"):
            assumptions.append(f"A{i}: CRM interest type '{it['type']}' was set up as a {kind} buy")
        else:
            assumptions.append(f"A{i}: no interest type in the CRM; set up as a mineral buy")
        asset: dict[str, Any] = {
            "id": f"A{i}", "kind": kind, "state": st, "county": c.get("county") or "",
            "legal_description": c.get("location") or "", "gross_acres": c.get("acres") or None,
            "seller": "S1", "title": {}, "wells": [],
            "crm": {k: it.get(k) for k in ("caseId", "well", "type", "pct", "status", "buySignal", "sourcePDF", "foundIn")},
        }
        if it.get("well"):
            asset["wells"].append({"name": it["well"], "case_id": it.get("caseId") or None})
            if it.get("pct"):
                assumptions.append(f"A{i}: the CRM shows {it['pct']} in {it['well']}; that's the owner's decimal in the "
                                   "well from the exhibit, not yet verified by title")
        assets.append(asset)
    record = {
        "deal_id": did,
        "name": f"{c['name']} ({c.get('county') or 'county?'} County) from the CRM",
        "buyer": buyer.strip(),
        "price": None,
        "sellers": [{"id": "S1", "name": c["name"], "email": c.get("email") or None, "phone": c.get("phone") or None,
                     "address": c.get("mailing") or None, "crm_id": c["id"]}],
        "assets": assets,
        "documents": [{"type": "crm_document", "name": d.get("name"), "crm_file": d.get("id")}
                      for d in c.get("documents") or []],
        "crm": {"customer_id": c["id"], "status": c.get("status"), "value": c.get("value"), "asking": c.get("asking"),
                "notes": c.get("notes")},
    }
    if c.get("asking") or c.get("value"):
        assumptions.append(f"Price left blank: the CRM shows value {c.get('value') or '-'} and asking "
                           f"{c.get('asking') or '-'}; enter the contract price")
    return record, assumptions


def start_deal(store: Store, rules, cid: str, actor: str, buyer: str, state: str | None = None,
               today: date | None = None) -> dict[str, Any]:
    """Hand a CRM owner to title follow-up: create the deal, run intake, open a task to confirm the mapping."""
    from . import actions
    from .intake import intake_deal
    from .record import normalize
    c = customer(store, cid)
    if c is None:
        raise KeyError(f"no CRM customer {cid}")
    existing = deals_by_customer(store, rules).get(cid)
    if existing:
        raise ValueError(f"{c['name']} already has title deal {existing[0]['deal_id']}")
    raw, assumptions = deal_from_customer(c, buyer, state, today=today)
    record = normalize(raw, store.home)
    if store.exists(record["deal_id"]):
        raise ValueError(f"deal {record['deal_id']} already exists")
    result = intake_deal(store, rules, record, actor, today=today)
    task = actions.add_task(store, rules, record["deal_id"], actor,
                            "Confirm what the seller is selling and complete the deal record (from the CRM): "
                            + "; ".join(assumptions), stage="intake", severity="stop", party="acquisitions",
                            asset_id=None, code="CRM_HANDOFF_CONFIRM", today=today)
    folder = store.home / "deals" / record["deal_id"]
    folder.mkdir(parents=True, exist_ok=True)
    draft = folder / "deal.json"
    draft.write_text(json.dumps({**raw, "_note": "Created from the CRM. Complete it, then: ptk deal intake deal.json "
                                                 "--reason \"completed the record\""}, indent=2), encoding="utf-8")
    return {"deal_id": record["deal_id"], "file": str(draft), "assumptions": assumptions, "task": task,
            "summary": result["summary"]}


def seller_is_contactable(c: dict[str, Any]) -> tuple[bool, str]:
    """Outreach email is refused to an owner marked Dead or tagged do-not-contact."""
    tags = {name_key(t).replace(" ", "-") for t in c.get("tags") or []}
    if c.get("status") == "Dead":
        return False, f"{c.get('name')} is marked Dead"
    if tags & {"DO-NOT-CONTACT", "DNC"}:
        return False, f"{c.get('name')} is tagged do-not-contact"
    return True, ""


# ---- outreach email -----------------------------------------------------------------

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def send_outreach(store: Store, actor: str, cid: str, msg: dict[str, Any], transport=None) -> dict[str, Any]:
    """Send one CRM follow-up email a person just reviewed and clicked Send on.

    Goes through the deal desk's mailer: SMTP when PTK_SMTP_HOST is set, otherwise an
    .eml file in the outbox folder (status "exported": written, not emailed). Refused
    for an owner marked Dead or tagged do-not-contact. Every attempt is logged.
    """
    import os
    from email.message import EmailMessage
    from email.utils import formataddr, make_msgid

    from .outbox import transport_from_env
    c = customer(store, cid)
    if c is None:
        raise KeyError(f"no CRM customer {cid}")
    ok, why = seller_is_contactable(c)
    if not ok:
        raise ValueError(f"not sent: {why}")
    to = str(msg.get("to") or "").strip()
    if not _EMAIL.match(to):
        raise ValueError(f"not sent: {to or 'no address'} isn't an email address")
    sender = os.environ.get("PTK_SMTP_FROM") or str(msg.get("from") or "").strip() or "title@localhost"
    e = EmailMessage()
    e["From"] = sender
    if msg.get("from") and str(msg["from"]).strip() != sender:
        e["Reply-To"] = str(msg["from"]).strip()
    e["To"] = formataddr((c.get("name") or "", to))
    e["Subject"] = str(msg.get("subject") or "")
    e["Message-ID"] = make_msgid(domain=sender.split("@")[-1])
    e["X-PTK-CRM-Customer"] = cid
    e["X-PTK-Step"] = str(msg.get("step") or "")
    e.set_content(str(msg.get("body") or ""))
    transport = transport or transport_from_env(store)
    n = sum(1 for ev in store.events(PREFIX + cid) if ev.type == "crm.email.delivered") + 1
    status, ref = transport.send(e, f"crm-{cid}", f"E{n}")
    store.append(PREFIX + cid, "crm.email.delivered", {"to": to, "subject": e["Subject"], "body": msg.get("body") or "",
                                                       "step": msg.get("step"), "status": status,
                                                       "transport": transport.name, "ref": ref}, actor)
    return {"status": status, "ref": ref}


# ---- documents ----------------------------------------------------------------------

FILE_TYPES = {"application/pdf": ".pdf", "image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif",
              "image/webp": ".webp", "text/csv": ".csv", "application/json": ".json", "text/plain": ".txt",
              "text/markdown": ".md"}
MAX_FILE_BYTES = 20 * 1024 * 1024
_KEY = re.compile(r"^[A-Za-z0-9_.-]{1,64}-[0-9a-f]{12}\.[a-z]{2,4}$")


def files_dir(store: Store) -> Path:
    return store.home / "crm-files"


def add_document(store: Store, actor: str, cid: str, filename: str, content_type: str, data: bytes) -> dict[str, Any]:
    """Keep an attached file beside the database; returns the CRM Document pointer {id, name, type, size, date}."""
    import hashlib
    import secrets
    if customer(store, _check_id(cid)) is None:
        raise KeyError(f"no CRM customer {cid}")
    ctype = (content_type or "").split(";")[0].strip().lower()
    if ctype not in FILE_TYPES:
        raise ValueError(f"{filename}: only PDF, PNG/JPEG/GIF/WebP images, CSV, JSON, TXT and Markdown can be attached")
    if len(data) > MAX_FILE_BYTES:
        raise ValueError(f"{filename} is over 20 MB")
    key = f"{cid}-{secrets.token_hex(6)}{FILE_TYPES[ctype]}"
    folder = files_dir(store)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / key).write_bytes(data)
    doc = {"id": key, "name": Path(filename or key).name[:200], "type": ctype, "size": len(data),
           "date": date.today().isoformat()}
    store.append(PREFIX + cid, "crm.file.added", {**doc, "sha256": hashlib.sha256(data).hexdigest()}, actor)
    return doc


def file_path(store: Store, key: str) -> Path:
    if not _KEY.match(key or ""):
        raise KeyError("no such file")
    p = files_dir(store) / key
    if not p.is_file():
        raise KeyError("no such file")
    return p


def remove_document(store: Store, actor: str, key: str) -> None:
    """Take a file off a record. It moves to crm-files/.removed, so it can be recovered."""
    p = file_path(store, key)
    trash = files_dir(store) / ".removed"
    trash.mkdir(exist_ok=True)
    p.replace(trash / key)
    store.append(PREFIX + key.rsplit("-", 1)[0], "crm.file.removed", {"id": key}, actor)
