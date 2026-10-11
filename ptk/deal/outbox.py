"""Owner and operator messages: drafts, approval, routine reminders, and sending.

Policy (rules.json ``messaging``):

* Every message is drafted from a template and logged. A **substantive**
  message (a first curative request, a transfer notice, a decimal question)
  waits for a person to approve it.
* A **routine** reminder (``routine_templates``) is approved automatically, and
  so goes out without a person, only when all of these hold:
  auto-send is on; it goes by email; the recipient isn't marked
  ``do_not_contact``; a person-approved message has already been delivered to
  that recipient on this deal; and the task hasn't used up ``max_reminders``.
  Otherwise it is drafted for approval like anything else.
* After ``max_reminders`` with no response, the program stops writing and
  opens a ``NO_RESPONSE`` task for a person to call.

Sending: ``send_approved`` delivers approved email through SMTP when
``PTK_SMTP_HOST`` is set (credentials only from the environment). Without it,
messages are written as ``.eml`` files to the outbox folder and marked
``exported``, not ``sent``, so nobody mistakes a file for a delivered email.
Letters by mail are printed and marked mailed by a person (``mark_mailed``).
"""

from __future__ import annotations

import os
import smtplib
from datetime import date, datetime
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from pathlib import Path
from typing import Any

from ..dates import iso
from ..fracs import parse_fraction, to_decimal
from ..ledger import name_key
from .drafts import INSTRUMENT_TEMPLATES, STATE_NAME, render_letter
from .rules import INTERNAL_PARTIES, Rules
from .state import DealState, Message, Task, fold
from .store import Store
from .tasks import TaskSpec, sync

REQUEST_TEMPLATES = ("curative_request", "document_request", "third_party_request")
REMINDER_FOR = {"curative_request": "curative_reminder", "curative_reminder": "curative_reminder",
                "document_request": "document_reminder", "document_reminder": "document_reminder",
                "third_party_request": "third_party_reminder", "third_party_reminder": "third_party_reminder",
                "transfer_notice": "do_followup", "do_followup": "do_followup",
                "payment_followup": "payment_followup"}
ROLE_TITLE = {"operator": "Operator", "payor": "Payor / first purchaser"}


AUTO_ACTOR = "ptk:auto-routine"


def _day(ts: str | None) -> date | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts)).date()
    except ValueError:
        try:
            return date.fromisoformat(str(ts)[:10])
        except ValueError:
            return None  # an unreadable date never stops the reminder run for every other deal


def _num(m: Message) -> int:
    """Message order (M1, M2, ...): ids are numbered inside the write transaction, so they never go backwards."""
    return int(m.msg_id[1:]) if m.msg_id[1:].isdigit() else 0


def _rejected_by_person(m: Message) -> bool:
    return m.status == "rejected" and bool(m.history) and m.history[-1].get("actor") != AUTO_ACTOR


def lands_summary(assets: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for a in assets:
        where = f"{a.get('county')} County, {STATE_NAME[a['state']]}"
        what = a.get("legal_description") or a.get("tract") or a.get("lease", {}).get("id") or a["id"]
        text = what if a.get("county", "").lower() in what.lower() else f"{what} ({where})"
        if text not in parts:
            parts.append(text)
    return "; ".join(parts)


def assets_for(st: DealState, to_name: str, task: Task | None) -> list[dict[str, Any]]:
    """The assets a message is about: the task's asset, else every asset whose wells this party operates or pays."""
    if task and task.asset_id:
        return [a for a in st.assets if a["id"] == task.asset_id] or st.assets
    k = name_key(to_name)
    seller = next((s for s in st.record.get("sellers", []) if name_key(s["name"]) == k), None)
    if seller is not None:
        return [a for a in st.assets if seller["id"] in a["sellers"]] or st.assets
    mine = [a for a in st.assets if any(k in (name_key(w.get("operator")), name_key(w.get("payor"))) for w in a.get("wells") or [])]
    return mine or st.assets


def decimals_for(st: DealState, to_name: str, assets: list[dict[str, Any]]) -> str:
    """The buyer's title decimals this party should pay, one total per well and interest type."""
    ids = {a["id"] for a in assets}
    totals: dict[tuple[str, str], Any] = {}
    for e in st.expected.values():
        if e.get("asset_id") in ids and name_key(e.get("payor")) == name_key(to_name):
            key = (str(e.get("well")), e["type"])
            totals[key] = totals.get(key, 0) + parse_fraction(e["decimal"])
    return "; ".join(f"{t} {to_decimal(v)} in {w}" for (w, t), v in totals.items())


def _interest_line(st: DealState, a: dict[str, Any]) -> str:
    sellers = " and ".join(s["name"] for s in st.record["sellers"] if s["id"] in a["sellers"])
    if a["kind"] in ("leasehold", "wellbore"):
        what = f"WI {a.get('wi')}" + (f", NRI {a.get('nri')}" if a.get("nri") else "")
    else:
        what = f"{a['kind']} interest {a.get('interest') or ''}".strip()
    return f"{what} from {sellers}"


def letter_values(st: DealState, rules: Rules, to_name: str, task: Task | None = None,
                  extra: dict[str, Any] | None = None) -> dict[str, Any]:
    assets = assets_for(st, to_name, task)
    wells = list({(w.get("api") or w.get("name")): w for a in assets for w in a.get("wells") or []}.values())
    recs = [f"{r.get('county')} County {r.get('series') or ''} {r.get('instrument_no') or (str(r.get('volume')) + '/' + str(r.get('page')))} "
            f"on {r.get('recorded')}".replace("  ", " ") for r in st.recordings]
    first = None
    if task:
        delivered = sorted((m for m in st.messages_for_task(task.task_id) if m.delivered), key=lambda m: m.sent_at or "")
        first = delivered[0].sent_at[:10] if delivered else None
    drafted = task is not None and any(d["task_id"] == task.task_id for d in st.drafts)
    values = {
        "to_name": to_name,
        "deal_name": st.record.get("name", st.deal_id),
        "buyer": st.record["buyer"],
        "buyer_address": st.record.get("buyer_address") or "[buyer address]",
        "seller": " and ".join(s["name"] for s in st.record["sellers"] if any(s["id"] in a["sellers"] for a in assets)),
        "effective_date": st.record.get("effective_date") or "[effective date]",
        "lands": lands_summary(assets),
        "signature": rules.messaging.get("signature", "Title Department"),
        "requirement": task.requirement if task else "",
        "message": task.message if task else "",
        "due": task.due if task and task.due else "[date]",
        "first_sent": first or "[date]",
        "attachment_line": ("We've enclosed a draft instrument for you to review and sign in front of a notary. "
                            if drafted and task and task.template in INSTRUMENT_TEMPLATES else ""),
        "interest_summary": "; ".join(_interest_line(st, a) for a in assets),
        "wells": "; ".join(f"{w.get('name') or '[name]'} (API {w.get('api') or '[API]'})" for w in wells) or "[wells]",
        "recording": "; ".join(recs) or "[recording reference]",
        "decimals": decimals_for(st, to_name, assets) or "[decimal]",
        "role_title": "Operator",
    }
    values.update(extra or {})
    return values


def may_auto_send(st: DealState, msg: Message, rules: Rules) -> tuple[bool, str]:
    m = rules.messaging
    if msg.template not in m.get("routine_templates", []):
        return False, "not a routine template; a person approves it"
    if not m.get("auto_send_routine", False):
        return False, "auto-send is off (messaging.auto_send_routine)"
    if msg.channel != "email" or not msg.to_email:
        return False, "routine reminders go out automatically by email only"
    if st.contact_for(msg.to_name).get("do_not_contact"):
        return False, f"{msg.to_name} is marked do_not_contact"
    if m.get("require_prior_approved_contact", True):
        prior = [x for x in st.messages.values() if x.msg_id != msg.msg_id and name_key(x.to_name) == name_key(msg.to_name)
                 and x.delivered and x.approved_by and not x.auto_approved]
        if not prior:
            return False, f"no person-approved message has been delivered to {msg.to_name} on this deal yet"
    if msg.task_id:
        used = [x for x in st.messages_for_task(msg.task_id) if x.msg_id != msg.msg_id and x.template in m.get("routine_templates", [])
                and x.status not in ("rejected", "failed")]
        if len(used) >= int(m.get("max_reminders", 3)):
            return False, f"task {msg.task_id} already has {len(used)} reminders (messaging.max_reminders)"
    return True, "routine reminder to a party already contacted with approval"


def draft_message(store: Store, rules: Rules, deal_id: str, actor: str, template: str, to_name: str,
                  task_id: str | None = None, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    st = fold(store.events(deal_id))
    task = st.tasks.get(task_id) if task_id else None
    if task_id and task is None:
        raise KeyError(f"{deal_id} has no task {task_id}")
    contact = st.contact_for(to_name)
    email = contact.get("email") or None
    channel = contact.get("channel") or ("email" if email else "mail")
    if channel == "email" and not email:
        channel = "mail"
    subject, body = render_letter(template, letter_values(st, rules, to_name, task, extra))
    category = "routine" if template in rules.messaging.get("routine_templates", []) else "substantive"
    msg = {"msg_id": "", "to_name": to_name, "to_email": email, "to_address": contact.get("address"),
           "channel": channel, "subject": subject, "body": body, "template": template, "category": category,
           "task_id": task_id, "status": "draft"}

    def build(cur):
        msg["msg_id"] = f"M{Store.next_number(cur, deal_id, 'message.drafted')}"
        return {"message": msg}

    store.append(deal_id, "message.drafted", build, actor)
    st = fold(store.events(deal_id))
    ok, why = may_auto_send(st, st.messages[msg["msg_id"]], rules)
    if ok:
        store.append(deal_id, "message.approved", {"msg_id": msg["msg_id"], "auto": True, "reason": why}, AUTO_ACTOR)
    return {"msg_id": msg["msg_id"], "subject": subject, "channel": channel, "category": category,
            "auto_approved": ok, "reason": why}


def request_for_task(store: Store, rules: Rules, deal_id: str, task_id: str, actor: str,
                     to_name: str | None = None) -> list[dict[str, Any]]:
    """Draft the first request for a task to the party who must act on it (always for approval)."""
    st = fold(store.events(deal_id))
    task = st.tasks.get(task_id)
    if task is None:
        raise KeyError(f"{deal_id} has no task {task_id}")
    if task.party in INTERNAL_PARTIES and not to_name:
        raise ValueError(f"{task_id} belongs to {task.party}; there's no outside party to write to (pass --to NAME to override)")
    names = [to_name] if to_name else [n.strip() for n in (task.party_name or "").split(";") if n.strip()]
    if not names:
        raise ValueError(f"{task_id} has no party name; pass --to NAME")
    if task.template == "document_request":
        template = "document_request"
    elif task.party == "third_party":
        template = "third_party_request"
    else:
        template = "curative_request"
    return [draft_message(store, rules, deal_id, actor, template, n, task_id) for n in names]


def approve(store: Store, deal_id: str, msg_id: str, actor: str) -> None:
    st = fold(store.events(deal_id))
    m = st.messages.get(msg_id)
    if m is None:
        raise KeyError(f"{deal_id} has no message {msg_id}")
    if m.status != "draft":
        raise ValueError(f"{msg_id} is {m.status}; only drafts can be approved")
    store.append(deal_id, "message.approved", {"msg_id": msg_id, "auto": False}, actor)


def reject(store: Store, deal_id: str, msg_id: str, actor: str, reason: str) -> None:
    st = fold(store.events(deal_id))
    m = st.messages.get(msg_id)
    if m is None:
        raise KeyError(f"{deal_id} has no message {msg_id}")
    if m.delivered:
        raise ValueError(f"{msg_id} was already {m.status}")
    store.append(deal_id, "message.rejected", {"msg_id": msg_id, "reason": reason}, actor)


def edit(store: Store, deal_id: str, msg_id: str, actor: str, subject: str | None, body: str | None) -> None:
    st = fold(store.events(deal_id))
    m = st.messages.get(msg_id)
    if m is None:
        raise KeyError(f"{deal_id} has no message {msg_id}")
    if m.status not in ("draft",):
        raise ValueError(f"{msg_id} is {m.status}; only drafts can be edited (reject it and draft again)")
    store.append(deal_id, "message.edited", {"msg_id": msg_id, "subject": subject or m.subject, "body": body or m.body}, actor)


# ---- delivery ---------------------------------------------------------------

class FileTransport:
    """Writes each email to <outbox>/<deal>-<msg>.eml. Nothing leaves the machine."""

    name = "file"

    def __init__(self, folder: Path):
        self.folder = folder

    def send(self, email: EmailMessage, deal_id: str, msg_id: str) -> tuple[str, str]:
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self.folder / f"{deal_id}-{msg_id}.eml"
        path.write_bytes(bytes(email))
        return "exported", str(path)


class SmtpTransport:
    """SMTP from environment settings: PTK_SMTP_HOST, PTK_SMTP_PORT, PTK_SMTP_USER, PTK_SMTP_PASSWORD, PTK_SMTP_TLS."""

    name = "smtp"

    def __init__(self, env: dict[str, str] | None = None):
        env = env if env is not None else dict(os.environ)
        self.host = env["PTK_SMTP_HOST"]
        self.port = int(env.get("PTK_SMTP_PORT") or 587)
        self.user = env.get("PTK_SMTP_USER")
        self.password = env.get("PTK_SMTP_PASSWORD")
        self.tls = (env.get("PTK_SMTP_TLS") or "starttls").lower()

    def send(self, email: EmailMessage, deal_id: str, msg_id: str) -> tuple[str, str]:
        cls = smtplib.SMTP_SSL if self.tls == "ssl" else smtplib.SMTP
        with cls(self.host, self.port, timeout=30) as s:
            if self.tls == "starttls":
                s.starttls()
            if self.user:
                s.login(self.user, self.password or "")
            s.send_message(email)
        return "sent", str(email["Message-ID"])


def transport_from_env(store: Store):
    return SmtpTransport() if os.environ.get("PTK_SMTP_HOST") else FileTransport(store.home / "outbox")


def _email(msg: Message, deal_id: str, rules: Rules) -> EmailMessage:
    sender = os.environ.get("PTK_SMTP_FROM") or "title@localhost"
    e = EmailMessage()
    e["From"] = formataddr((rules.messaging.get("from_name", "Title Department"), sender))
    e["To"] = formataddr((msg.to_name, msg.to_email or ""))
    e["Subject"] = msg.subject
    e["Message-ID"] = make_msgid(domain=sender.split("@")[-1])
    e["X-PTK-Deal"] = deal_id
    e["X-PTK-Message"] = msg.msg_id
    e.set_content(msg.body)
    return e


def _after_delivery(store: Store, st: DealState, msg: Message, actor: str, method: str, on: str) -> None:
    """A delivered transfer notice logs the notice; a delivered request marks its task requested."""
    if msg.template == "transfer_notice":
        role = next((t["role"] for t in st.notice_targets() if name_key(t["name"]) == name_key(msg.to_name)), "operator")
        store.append(st.deal_id, "notice.logged", {"target": msg.to_name, "role": role, "method": method,
                                                    "date": on, "msg_id": msg.msg_id}, actor)
    if msg.task_id and msg.template in REQUEST_TEMPLATES:
        task = st.tasks.get(msg.task_id)
        if task and task.status == "open":
            store.append(st.deal_id, "task.updated", {"task_id": task.task_id, "status": "requested",
                                                       "note": f"{msg.msg_id} {method} to {msg.to_name}"}, actor)


def withdraw_stale(store: Store, deal_id: str) -> list[str]:
    """Withdraw routine reminders still waiting to go out for tasks that have since closed."""
    st = fold(store.events(deal_id))
    out = []
    for m in st.messages.values():
        task = st.tasks.get(m.task_id or "")
        if m.category == "routine" and m.status in ("draft", "approved") and task is not None and task.closed:
            store.append(deal_id, "message.rejected", {"msg_id": m.msg_id, "reason": f"{task.task_id} was {task.status} "
                                                       "before this reminder went out"}, AUTO_ACTOR)
            out.append(m.msg_id)
    return out


def _claim(store: Store, deal_id: str, m: Message, actor: str) -> bool:
    """Claim one delivery attempt inside the write transaction; a second sender gets False."""
    attempt = 1 + sum(1 for h in m.history if "error" in h)
    fragment = f'"attempt":{attempt},"msg_id":"{m.msg_id}"'

    def build(cur):
        if Store.has_event(cur, deal_id, "message.claimed", fragment):
            return None
        return {"attempt": attempt, "msg_id": m.msg_id}

    return store.append(deal_id, "message.claimed", build, actor) is not None


def send_approved(store: Store, rules: Rules, actor: str, transport=None, deal_id: str | None = None,
                  dry_run: bool = False) -> list[dict[str, Any]]:
    transport = transport or transport_from_env(store)
    cap = int(rules.messaging.get("max_sends_per_run", 50))
    results: list[dict[str, Any]] = []
    for did in ([deal_id] if deal_id else store.deal_ids()):
        withdraw_stale(store, did)
        st = fold(store.events(did))
        for m in st.messages.values():
            if m.status not in ("approved", "failed") or not m.approved_by:
                continue
            entry = {"deal_id": did, "msg_id": m.msg_id, "to": m.to_name, "channel": m.channel, "subject": m.subject}
            if m.category == "substantive" and m.auto_approved:
                entry["result"] = "refused: a substantive message needs a person's approval"
            elif st.contact_for(m.to_name).get("do_not_contact"):
                entry["result"] = "refused: recipient is marked do_not_contact"
            elif m.channel != "email" or not m.to_email:
                entry["result"] = "print and mail it, then: ptk deal outbox mark-mailed " + f"{did} {m.msg_id}"
            elif len([r for r in results if r.get("delivered")]) >= cap:
                entry["result"] = f"held: max_sends_per_run ({cap}) reached"
            elif dry_run:
                entry["result"] = f"would send via {transport.name}"
            elif not _claim(store, did, m, actor):
                entry["result"] = "skipped: another send is already handling it"
            else:
                try:
                    status, ref = transport.send(_email(m, did, rules), did, m.msg_id)
                    etype = "message.sent" if status == "sent" else "message.exported"
                    store.append(did, etype, {"msg_id": m.msg_id, "transport": transport.name, "ref": ref,
                                              "at": datetime.now().astimezone().isoformat(timespec="seconds")}, actor)
                    entry.update(result=f"{status} ({ref})", delivered=status == "sent")
                    if status == "sent":
                        _after_delivery(store, fold(store.events(did)), m, actor, "email", date.today().isoformat())
                except (OSError, smtplib.SMTPException) as e:
                    store.append(did, "message.failed", {"msg_id": m.msg_id, "error": str(e)}, actor)
                    entry["result"] = f"failed: {e}"
            results.append(entry)
    return results


def mark_mailed(store: Store, deal_id: str, msg_id: str, actor: str, on: str | None = None) -> None:
    st = fold(store.events(deal_id))
    m = st.messages.get(msg_id)
    if m is None:
        raise KeyError(f"{deal_id} has no message {msg_id}")
    if m.status not in ("approved", "exported"):
        raise ValueError(f"{msg_id} is {m.status}; approve it before marking it mailed or sent")
    on = iso(on) if on else date.today().isoformat()
    store.append(deal_id, "message.mailed", {"msg_id": msg_id, "at": on}, actor)
    _after_delivery(store, fold(store.events(deal_id)), m, actor, "mail" if m.channel == "mail" else "email (sent by hand)", on)


# ---- reminders --------------------------------------------------------------

def run_reminders(store: Store, rules: Rules, actor: str, today: date | None = None,
                  deal_id: str | None = None) -> list[dict[str, Any]]:
    """Draft due reminders (auto-approved when the policy allows), open NO_RESPONSE and PAYMENT_OVERDUE tasks."""
    today = today or date.today()
    m = rules.messaging
    cadence, cap = int(m.get("cadence_days", 14)), int(m.get("max_reminders", 3))
    actions: list[dict[str, Any]] = []
    for did in ([deal_id] if deal_id else store.deal_ids()):
        st = fold(store.events(did))
        for task in list(st.tasks.values()):
            # "received" means the party answered: a person reviews it, nobody is reminded
            if task.closed or task.status == "received" or task.party in INTERNAL_PARTIES:
                continue
            by_party: dict[str, list[Message]] = {}
            for x in st.messages_for_task(task.task_id):
                by_party.setdefault(name_key(x.to_name), []).append(x)
            for msgs in by_party.values():
                if any(x.status in ("draft", "approved", "exported", "failed") for x in msgs):
                    continue  # something is already waiting to go out to this party
                delivered = sorted((x for x in msgs if x.delivered), key=lambda x: x.sent_at or "")
                if not delivered:
                    continue  # first contact is a person's call: ptk deal request
                if any(_rejected_by_person(x) and _num(x) > _num(delivered[-1]) for x in msgs):
                    continue  # a person turned down the last reminder: no more until someone writes again
                who = delivered[-1].to_name
                last = _day(delivered[-1].sent_at)
                if last is None or (today - last).days < cadence:
                    continue
                reminders = [x for x in delivered if x.template in m.get("routine_templates", [])]
                template = REMINDER_FOR.get(delivered[0].template)
                if template and len(reminders) < cap:
                    r = draft_message(store, rules, did, actor, template, who, task.task_id)
                    actions.append({"deal_id": did, "task_id": task.task_id, "action": "reminder", **r})
                else:
                    spec = TaskSpec("NO_RESPONSE", f"{who} hasn't responded on {task.task_id} [{task.code}] after "
                                    f"{len(reminders)} reminder(s); last contact {last.isoformat()}.", "reminders",
                                    task.asset_id, f"no-response:{task.task_id}", party="landman", party_name=who)
                    if st.task_by_fingerprint(spec.fingerprint):
                        continue
                    r = sync(store, st, rules, actor, "reminders", task.asset_id, [spec], today, resolve_missing=False)
                    actions.append({"deal_id": did, "task_id": task.task_id, "action": "no_response", "opened": r["opened"]})
                st = fold(store.events(did))
        # payments past their watch date (one check per payor and well, however many assets share it)
        seen: set[tuple[str, str]] = set()
        for exp in list(st.expected.values()):
            pair = (name_key(exp["payor"]), name_key(exp.get("well")))
            if pair in seen or st.payments_for(exp["payor"], exp.get("well")):
                continue
            seen.add(pair)
            notice = st.notified(exp["payor"])
            if not notice:
                continue
            asset = next((a for a in st.assets if a["id"] == exp.get("asset_id")), None)
            watch = int(rules.payment(asset["state"] if asset else "TX").get("watch_days_after_notice", 120))
            sent = _day(notice.get("date"))
            if sent is None or (today - sent).days < watch:
                continue
            spec = TaskSpec("PAYMENT_OVERDUE", f"No payment from {exp['payor']} for {exp.get('well')} {watch} days after the "
                            f"transfer notice ({sent.isoformat()}). {rules.payment(asset['state'] if asset else 'TX').get('authority', '')}",
                            "payments", None, f"overdue:{exp['payor']}|{exp.get('well')}", party_name=exp["payor"])
            r = sync(store, st, rules, actor, "payments", None, [spec], today, resolve_missing=False)
            for tid in r["opened"]:
                d = draft_message(store, rules, did, actor, "payment_followup", exp["payor"], tid)
                actions.append({"deal_id": did, "task_id": tid, "action": "payment_overdue", **d})
            st = fold(store.events(did))
    return actions


__all__ = ["draft_message", "request_for_task", "approve", "reject", "edit", "send_approved", "mark_mailed",
           "run_reminders", "may_auto_send", "FileTransport", "SmtpTransport", "transport_from_env",
           "letter_values"]
