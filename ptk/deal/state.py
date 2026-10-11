"""A deal's current status, rebuilt from its event log.

``fold(events)`` replays every event for one deal into a ``DealState``;
``evaluate(state, rules)`` turns that into the six stages, what blocks each one,
and the next thing to do. Nothing here writes: status is always derived, so it
can never drift from the audit trail.

Stages:
    1 intake    the deal record is complete and every tract can be located
    2 verify    title shows the seller owns at least what it sold, on the effective date and today
    3 curative  every title requirement is cleared or waived; an attorney opinion when one is required
    4 closing   package drafted, approved and signed; funds released only through the gate
    5 record    conveyance recorded in every county; agency filings; operators and payors notified
    6 payment   division orders back, first payment received at the title decimal
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from typing import Any

from ..fracs import parse_fraction
from ..ledger import name_key
from .rules import STAGES, Rules
from .store import StoredEvent

OPEN_STATUSES = ("open", "requested", "received")
CLOSED_STATUSES = ("cleared", "waived", "resolved")
TASK_STATUSES = OPEN_STATUSES + CLOSED_STATUSES
STAGE_TITLES = {
    "intake": "Intake",
    "verify": "Verify ownership",
    "curative": "Curative",
    "closing": "Closing",
    "record": "Record and notify",
    "payment": "Track payment",
}


@dataclass
class Task:
    task_id: str
    code: str
    stage: str
    severity: str
    source: str
    message: str
    requirement: str
    party: str
    party_name: str | None
    asset_id: str | None
    requires_attorney: bool
    template: str | None
    due: str | None
    ref: str
    fingerprint: str
    opened: str
    status: str = "open"
    history: list[dict[str, Any]] = field(default_factory=list)

    @property
    def closed(self) -> bool:
        return self.status in CLOSED_STATUSES

    @property
    def blocking(self) -> bool:
        return not self.closed and self.severity in ("stop", "review")


@dataclass
class Message:
    msg_id: str
    to_name: str
    to_email: str | None
    to_address: str | None
    channel: str
    subject: str
    body: str
    template: str
    category: str  # routine | substantive
    task_id: str | None
    created: str
    status: str = "draft"  # draft | approved | rejected | sent | exported | mailed | failed
    approved_by: str | None = None
    auto_approved: bool = False
    sent_at: str | None = None
    history: list[dict[str, Any]] = field(default_factory=list)

    @property
    def delivered(self) -> bool:
        return self.status in ("sent", "mailed")


@dataclass
class DealState:
    deal_id: str
    record: dict[str, Any] = field(default_factory=dict)
    created: str = ""
    updated: str = ""
    intake: dict[str, Any] = field(default_factory=dict)
    tasks: dict[str, Task] = field(default_factory=dict)
    verify: dict[str, dict[str, Any]] = field(default_factory=dict)
    opinions: list[dict[str, Any]] = field(default_factory=list)
    package: dict[str, Any] = field(default_factory=dict)
    recordings: list[dict[str, Any]] = field(default_factory=list)
    notices: list[dict[str, Any]] = field(default_factory=list)
    division_orders: list[dict[str, Any]] = field(default_factory=list)
    expected: dict[str, dict[str, Any]] = field(default_factory=dict)
    payments: list[dict[str, Any]] = field(default_factory=list)
    messages: dict[str, Message] = field(default_factory=dict)
    drafts: list[dict[str, Any]] = field(default_factory=list)
    notes: list[dict[str, Any]] = field(default_factory=list)
    events: list[StoredEvent] = field(default_factory=list)

    # ---- lookups -----------------------------------------------------------
    def open_tasks(self) -> list[Task]:
        return [t for t in self.tasks.values() if not t.closed]

    def task_by_fingerprint(self, fp: str) -> Task | None:
        return next((t for t in self.tasks.values() if t.fingerprint == fp), None)

    def next_task_id(self) -> str:
        return f"T{len(self.tasks) + 1}"

    def next_msg_id(self) -> str:
        return f"M{len(self.messages) + 1}"

    def next_opinion_id(self) -> str:
        return f"OP{len(self.opinions) + 1}"

    def messages_for_task(self, task_id: str) -> list[Message]:
        return [m for m in self.messages.values() if m.task_id == task_id]

    @property
    def assets(self) -> list[dict[str, Any]]:
        return self.record.get("assets", [])

    @property
    def states(self) -> list[str]:
        return sorted({a["state"] for a in self.assets})

    def counties(self) -> list[tuple[str, str]]:
        """Every (state, county) the conveyance must be recorded in."""
        return sorted({(a["state"], a["county"]) for a in self.assets if a.get("county")})

    def notice_targets(self) -> list[dict[str, str]]:
        """Operators and payors of every listed well: each needs a transfer notice."""
        seen: dict[str, dict[str, str]] = {}
        for a in self.assets:
            for w in a.get("wells") or []:
                for role in ("operator", "payor"):
                    name = (w.get(role) or "").strip()
                    if name and name_key(name) not in seen:
                        seen[name_key(name)] = {"name": name, "role": role, "asset_id": a["id"], "state": a["state"]}
        return list(seen.values())

    def recorded_in(self, state: str, county: str) -> dict[str, Any] | None:
        return next((r for r in self.recordings
                     if r.get("state") == state and name_key(r.get("county")) == name_key(county)), None)

    def notified(self, name: str) -> dict[str, Any] | None:
        return next((n for n in self.notices if name_key(n.get("target")) == name_key(name)), None)

    def payments_for(self, payor: str, well: str | None) -> list[dict[str, Any]]:
        return [p for p in self.payments if name_key(p.get("payor")) == name_key(payor)
                and (not well or not p.get("well") or name_key(p.get("well")) == name_key(well))]

    def contact_for(self, name: str | None) -> dict[str, Any]:
        """Email/address for a seller, operator or third party named on the deal."""
        if not name:
            return {}
        for s in self.record.get("sellers", []):
            if name_key(s["name"]) == name_key(name) or s["id"] == name:
                return s
        for c in self.record.get("contacts", []):
            if name_key(c.get("name")) == name_key(name):
                return c
        return {}

    def price(self) -> Decimal | None:
        try:
            return Decimal(self.record["price"]) if self.record.get("price") else None
        except InvalidOperation:
            return None


def _deal_terms(record: dict[str, Any]) -> tuple:
    """Deal-level facts verification depends on: a change to any of them stales every asset's result."""
    return (record.get("effective_date"), record.get("buyer"),
            tuple(sorted((s["id"], s["name"]) for s in record.get("sellers", []))))


def fold(events: list[StoredEvent]) -> DealState:
    if not events:
        raise KeyError("no such deal")
    st = DealState(events[0].deal_id, events=list(events), created=events[0].ts)
    for ev in events:
        p = ev.payload
        st.updated = ev.ts
        t = ev.type
        if t in ("deal.created", "deal.amended"):
            old = {a["id"]: a for a in st.record.get("assets", [])}
            new = {a["id"]: a for a in p["record"].get("assets", [])}
            for aid in list(st.verify):
                if new.get(aid) != old.get(aid) or (t == "deal.amended" and _deal_terms(st.record) != _deal_terms(p["record"])):
                    del st.verify[aid]  # the deal changed under it: verify again
            for key in [k for k, e in st.expected.items() if e.get("asset_id") not in new]:
                del st.expected[key]
            st.record = p["record"]
        elif t == "intake.checked":
            st.intake = p
        elif t == "task.opened":
            d = p["task"]
            st.tasks[d["task_id"]] = Task(**{**d, "opened": ev.ts, "history": [{"ts": ev.ts, "actor": ev.actor, "status": "open"}]})
        elif t == "task.updated":
            task = st.tasks.get(p["task_id"])
            if task is None:
                continue
            if p.get("status"):
                task.status = p["status"]
            if p.get("due"):
                task.due = p["due"]
            if p.get("party_name"):
                task.party_name = p["party_name"]
            task.history.append({"ts": ev.ts, "actor": ev.actor, **{k: v for k, v in p.items() if k != "task_id"}})
        elif t == "verify.completed":
            st.verify[p["asset_id"]] = {**p, "ts": ev.ts, "actor": ev.actor}
        elif t == "opinion.recorded":
            st.opinions.append({**p, "ts": ev.ts, "actor": ev.actor})
        elif t == "draft.written":
            st.drafts.append({**p, "ts": ev.ts, "actor": ev.actor})
        elif t == "package.drafted":
            st.package = {"drafted": {**p, "ts": ev.ts, "actor": ev.actor}}
        elif t in ("package.approved", "package.signed", "funds.released"):
            st.package[t.split(".")[1]] = {**p, "ts": ev.ts, "actor": ev.actor}
        elif t == "recording.logged":
            st.recordings.append({**p, "ts": ev.ts, "actor": ev.actor})
        elif t == "notice.logged":
            st.notices.append({**p, "ts": ev.ts, "actor": ev.actor})
        elif t == "division_order.received":
            st.division_orders.append({**p, "ts": ev.ts, "actor": ev.actor})
        elif t == "payment.expected":
            st.expected[p["key"]] = {**p, "ts": ev.ts, "actor": ev.actor}
        elif t == "payment.received":
            st.payments.append({**p, "ts": ev.ts, "actor": ev.actor})
        elif t == "message.drafted":
            d = p["message"]
            st.messages[d["msg_id"]] = Message(**{**d, "created": ev.ts, "history": [{"ts": ev.ts, "actor": ev.actor, "status": "draft"}]})
        elif t.startswith("message."):
            m = st.messages.get(p.get("msg_id", ""))
            if m is None:
                continue
            status = {"message.approved": "approved", "message.rejected": "rejected", "message.sent": "sent",
                      "message.exported": "exported", "message.failed": "failed", "message.mailed": "mailed",
                      "message.edited": m.status}.get(t, m.status)
            if t == "message.approved":
                m.approved_by, m.auto_approved = ev.actor, bool(p.get("auto"))
            if t == "message.edited":
                m.subject, m.body = p.get("subject", m.subject), p.get("body", m.body)
            if t in ("message.sent", "message.mailed"):
                m.sent_at = p.get("at") or ev.ts
            m.status = status
            m.history.append({"ts": ev.ts, "actor": ev.actor, "status": status,
                              **{k: v for k, v in p.items() if k not in ("msg_id", "body")}})
        elif t == "note.added":
            st.notes.append({**p, "ts": ev.ts, "actor": ev.actor})
    return st


# ---- evaluation -------------------------------------------------------------

@dataclass
class StageStatus:
    stage: str
    done: bool
    blockers: list[str]
    notes: list[str] = field(default_factory=list)

    @property
    def title(self) -> str:
        return STAGE_TITLES[self.stage]


OWNERSHIP_CODES = ("SELLER_SHORTFALL", "NRI_SHORTFALL", "SELLER_NOT_IN_CHAIN", "SELLER_CONVEYED_AFTER_EFFECTIVE")


def asset_verified(st: DealState, asset_id: str) -> tuple[bool, str]:
    """Verified when title supports the sale, or a person has decided every ownership problem (cleared or waived)."""
    v = st.verify.get(asset_id)
    if v is None:
        return False, "not verified yet"
    status = v["result"].get("status")
    if status == "OK":
        return True, "title supports the sale"
    if status in ("NO_TITLE", "ERROR"):
        return False, v["result"].get("summary", status)
    open_ = [t for t in st.tasks.values() if t.asset_id == asset_id and t.code in OWNERSHIP_CODES and not t.closed]
    if open_:
        return False, v["result"].get("summary", status)
    return True, f"{status.lower().replace('_', ' ')}: decided ({', '.join(t.task_id + ' ' + t.status for t in st.tasks.values() if t.asset_id == asset_id and t.code in OWNERSHIP_CODES)})"


def attorney_tasks(st: DealState) -> list[Task]:
    return [t for t in st.tasks.values() if t.requires_attorney and t.status != "resolved"]


def opinion_required(st: DealState, rules: Rules) -> tuple[bool, str]:
    mode = rules.attorney.get("mode", "flagged")
    if mode == "always":
        return True, "every deal goes to the examining attorney (rules: attorney.mode = always)"
    raised = attorney_tasks(st)
    if raised:
        codes = ", ".join(sorted({t.code for t in raised}))
        return True, f"title raised legal questions for the examiner: {codes}"
    threshold = rules.attorney.get("price_threshold")
    price = st.price()
    if mode == "price" and threshold is not None and price is not None and price >= Decimal(str(threshold)):
        return True, f"price {price} is at or above the opinion threshold {threshold}"
    return False, "no legal-question flags: closes on landman work alone (rules: attorney.mode = flagged)"


def _stage_tasks(st: DealState, stage: str) -> list[Task]:
    return [t for t in st.tasks.values() if t.stage == stage and t.blocking]


def _task_line(t: Task) -> str:
    who = t.party_name or t.party
    due = f", due {t.due}" if t.due else ""
    return f"{t.task_id} [{t.code}] {t.requirement or t.message} ({who}{due}; {t.status})"


def evaluate(st: DealState, rules: Rules, today: date | None = None) -> list[StageStatus]:
    today = today or date.today()
    out: list[StageStatus] = []

    # 1 intake
    blockers = [_task_line(t) for t in _stage_tasks(st, "intake")]
    if not st.intake:
        blockers.insert(0, "Intake checks haven't run: ptk deal intake <deal.json>")
    out.append(StageStatus("intake", not blockers, blockers))

    # 2 verify
    blockers = []
    notes = []
    for a in st.assets:
        ok, why = asset_verified(st, a["id"])
        if a["id"] not in st.verify:
            blockers.append(f"{a['id']} ({a['kind']}, {a.get('county')} {a['state']}): not verified yet: ptk deal verify {st.deal_id}")
        elif not ok:
            blockers.append(f"{a['id']}: {why}")
        elif st.verify[a["id"]]["result"].get("status") != "OK":
            notes.append(f"{a['id']}: {why}")
    blockers += [_task_line(t) for t in _stage_tasks(st, "verify") if t.code not in OWNERSHIP_CODES]
    out.append(StageStatus("verify", not blockers, blockers, notes))

    # 3 curative
    blockers = [_task_line(t) for t in _stage_tasks(st, "curative")]
    needed, why = opinion_required(st, rules)
    notes = [f"Attorney opinion {'required' if needed else 'not required'}: {why}"]
    notes += [f"{o['opinion_id']}: {o.get('kind', 'opinion')} by {o['attorney']} dated {o.get('date')}, covering "
              f"{', '.join(o.get('covers') or []) or 'no tasks'}" for o in st.opinions]
    if needed:
        if not st.opinions:
            blockers.append("Get the examining attorney's opinion, then record it: ptk deal opinion "
                            f"{st.deal_id} --attorney NAME --covers T#,T#")
        open_att = [t for t in attorney_tasks(st) if not t.closed]
        blockers += [f"{t.task_id} [{t.code}] needs the attorney: cover it in an opinion or record an attorney waiver"
                     for t in open_att if not t.blocking]
    out.append(StageStatus("curative", not blockers, blockers, notes))

    # 4 closing
    blockers = [_task_line(t) for t in _stage_tasks(st, "closing")]
    pkg = st.package
    if "drafted" not in pkg:
        blockers.append(f"Draft the closing package: ptk deal package {st.deal_id}")
    elif "approved" not in pkg:
        blockers.append(f"Review and approve the package: ptk deal closing {st.deal_id} approve")
    elif "signed" not in pkg:
        blockers.append(f"Get the conveyance signed, then: ptk deal closing {st.deal_id} sign --date YYYY-MM-DD")
    if "released" not in pkg:
        gate = funds_gate(st, rules)
        blockers.append("Release funds: ptk deal closing " + st.deal_id + " release" if not gate
                        else "Funds stay held until: " + "; ".join(gate))
    out.append(StageStatus("closing", not blockers, blockers))

    # 5 record and notify
    blockers = []
    for state, county in st.counties():
        if not st.recorded_in(state, county):
            blockers.append(f"Record the conveyance in {county} County, {state}: ptk deal record {st.deal_id} --county {county} ...")
    for target in st.notice_targets():
        if not st.notified(target["name"]):
            blockers.append(f"Send the transfer notice to {target['name']} ({target['role']}): ptk deal notify {st.deal_id}")
    blockers += [_task_line(t) for t in _stage_tasks(st, "record")]
    notes = [] if st.notice_targets() else ["No wells listed: no operator or payor to notify."]
    out.append(StageStatus("record", not blockers, blockers, notes))

    # 6 payment
    blockers = [_task_line(t) for t in _stage_tasks(st, "payment")]
    notes = []
    has_wells = any(a.get("wells") for a in st.assets)
    if not has_wells:
        notes.append("No producing wells listed: nothing to track until a well is drilled.")
    elif not st.expected:
        blockers.append(f"No expected decimals yet: run ptk deal verify {st.deal_id} (wells with a deck project), "
                        f"or enter them with ptk deal expect")
    for exp in st.expected.values():
        paid = [p for p in st.payments_for(exp["payor"], exp.get("well")) if p.get("status", "paid") != "suspense"]
        if not paid:
            blockers.append(f"Waiting on the first payment from {exp['payor']} for {exp.get('well') or 'the well'} "
                            f"({exp['type']} {exp['decimal_display']})")
    out.append(StageStatus("payment", not blockers, blockers, notes))
    return out


def funds_gate(st: DealState, rules: Rules) -> list[str]:
    """What still stands between this deal and releasing the purchase price. Empty means clear."""
    reasons: list[str] = []
    for t in st.tasks.values():
        if t.stage in ("intake", "verify", "curative", "closing") and t.blocking:
            reasons.append(f"{t.task_id} [{t.code}] is {t.status}")
    for a in st.assets:
        if not asset_verified(st, a["id"])[0]:
            reasons.append(f"{a['id']} title isn't verified")
    needed, _ = opinion_required(st, rules)
    if needed and not st.opinions:
        reasons.append("the attorney opinion isn't recorded")
    reasons += [f"{t.task_id} [{t.code}] is a legal question still open" for t in attorney_tasks(st)
                if not t.closed and not (t.blocking and t.stage in ("intake", "verify", "curative", "closing"))]
    for key in ("drafted", "approved", "signed"):
        if key not in st.package:
            reasons.append(f"the package isn't {key}")
    return reasons


def current_stage(stages: list[StageStatus]) -> StageStatus | None:
    return next((s for s in stages if not s.done), None)


def summary(st: DealState, rules: Rules, today: date | None = None) -> dict[str, Any]:
    stages = evaluate(st, rules, today)
    cur = current_stage(stages)
    needed, why = opinion_required(st, rules)
    return {
        "deal_id": st.deal_id,
        "name": st.record.get("name", st.deal_id),
        "buyer": st.record.get("buyer"),
        "states": st.states,
        "kinds": sorted({a["kind"] for a in st.assets}),
        "counties": [f"{c} {s}" for s, c in st.counties()],
        "stage": cur.stage if cur else "done",
        "stage_title": cur.title if cur else "Closed out",
        "next": cur.blockers[0] if cur and cur.blockers else "",
        "stages": [{"stage": s.stage, "title": s.title, "done": s.done, "blockers": s.blockers, "notes": s.notes}
                   for s in stages],
        "open_tasks": len(st.open_tasks()),
        "stop_tasks": sum(1 for t in st.open_tasks() if t.severity == "stop"),
        "opinion_required": needed,
        "opinion_reason": why,
        "messages_awaiting_approval": sum(1 for m in st.messages.values() if m.status == "draft"),
        "updated": st.updated,
    }


def decimal_display(value: Fraction | str) -> str:
    from ..fracs import to_decimal
    return to_decimal(parse_fraction(value) if isinstance(value, str) else value)


__all__ = ["DealState", "Task", "Message", "StageStatus", "fold", "evaluate", "summary", "funds_gate",
           "opinion_required", "attorney_tasks", "current_stage", "OPEN_STATUSES", "CLOSED_STATUSES",
           "TASK_STATUSES", "STAGES"]
