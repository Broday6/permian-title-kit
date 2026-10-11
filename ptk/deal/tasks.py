"""Turn flags and checklist items into tracked tasks (the curative tracker).

Every flag the kit raises (ledger, runsheet, deck, leasehold chain, intake,
verification) becomes a task with an owner, a requirement, a due date and a
status, using the rule for its code in ``rules.json``. Re-running a check never
duplicates a task: each one has a fingerprint (where it came from + code +
reference), and

* a flag raised again that already has a task is left alone, whatever its status;
* a task whose flag is no longer raised is marked ``resolved`` with a note, so
  fixing the takeoff clears it and the history shows why;
* a resolved task whose flag comes back is reopened.

Info-severity flags don't become tasks; the caller keeps them as notes on the
verification result, so nothing is dropped.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from .rules import Rules
from .state import DealState
from .store import Store


@dataclass
class TaskSpec:
    code: str
    message: str
    source: str
    asset_id: str | None = None
    ref: str = "-"
    severity: str | None = None  # the source's own severity, if it has one
    party_name: str | None = None
    # explicit fields (checklist items carry their own instead of a flag rule)
    party: str | None = None
    stage: str | None = None
    requirement: str | None = None
    template: str | None = None
    requires_attorney: bool | None = None
    due_days: int | None = None
    force_severity: str | None = None

    @property
    def fingerprint(self) -> str:
        key = "|".join([self.source, self.asset_id or "-", self.code, self.ref, self.party_name or ""])
        return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def resolve(spec: TaskSpec, rules: Rules) -> dict[str, Any]:
    """Fill a spec's fields from the flag rule for its code."""
    rule = rules.flag(spec.code, spec.severity)
    return {
        "severity": spec.force_severity or rule.severity,
        "party": spec.party or rule.party,
        "stage": spec.stage or rule.stage,
        "requirement": spec.requirement if spec.requirement is not None else rule.requirement,
        "template": spec.template if spec.template is not None else rule.template,
        "requires_attorney": rule.requires_attorney if spec.requires_attorney is None else spec.requires_attorney,
        "due_days": spec.due_days if spec.due_days is not None else rule.due_days,
    }


def _party_name(st: DealState, spec: TaskSpec, party: str) -> str | None:
    if spec.party_name:
        return spec.party_name
    if party == "seller":
        asset = next((a for a in st.assets if a["id"] == spec.asset_id), None)
        ids = asset["sellers"] if asset else [s["id"] for s in st.record.get("sellers", [])]
        names = [s["name"] for s in st.record.get("sellers", []) if s["id"] in ids]
        return "; ".join(names) or None
    return None


def merge_specs(specs: list[TaskSpec]) -> list[TaskSpec]:
    """Collapse specs with the same fingerprint, keeping every message."""
    out: dict[str, TaskSpec] = {}
    for s in specs:
        have = out.get(s.fingerprint)
        if have is None:
            out[s.fingerprint] = s
        elif s.message not in have.message:
            have.message = f"{have.message} | {s.message}"
            if s.severity == "stop":
                have.severity = "stop"
    return list(out.values())


def sync(store: Store, st: DealState, rules: Rules, actor: str, source: str, asset_id: str | None,
         specs: list[TaskSpec], today: date | None = None, resolve_missing: bool = True) -> dict[str, list]:
    """Open, reopen and resolve the tasks for one source (and asset) to match ``specs``.

    Returns {"opened": [task ids], "reopened": [...], "resolved": [...], "info": [specs not tracked]}.
    """
    today = today or date.today()
    result: dict[str, list] = {"opened": [], "reopened": [], "resolved": [], "info": [], "existing": []}
    wanted: set[str] = set()
    for spec in merge_specs(specs):
        fields = resolve(spec, rules)
        if fields["severity"] == "info":
            result["info"].append(spec)
            continue
        fp = spec.fingerprint
        wanted.add(fp)
        have = st.task_by_fingerprint(fp)
        if have is not None:
            if have.status == "resolved":
                store.append(st.deal_id, "task.updated", {"task_id": have.task_id, "status": "open",
                                                          "note": f"raised again by {source} on {today.isoformat()}"}, actor)
                have.status = "open"
                result["reopened"].append(have.task_id)
            else:
                result["existing"].append(have.task_id)
            continue
        task = {
            "task_id": "",
            "code": spec.code,
            "stage": fields["stage"],
            "severity": fields["severity"],
            "source": source,
            "message": spec.message,
            "requirement": fields["requirement"],
            "party": fields["party"],
            "party_name": _party_name(st, spec, fields["party"]),
            "asset_id": spec.asset_id,
            "requires_attorney": fields["requires_attorney"],
            "template": fields["template"],
            "due": (today + timedelta(days=fields["due_days"])).isoformat(),
            "ref": spec.ref,
            "fingerprint": fp,
            "status": "open",
        }
        def build(cur, task=task, fp=fp):
            # Numbered and de-duplicated inside the write transaction, so two processes can't
            # reuse a task id or open the same task twice.
            if Store.has_event(cur, st.deal_id, "task.opened", f'"fingerprint":"{fp}"'):
                return None
            task["task_id"] = f"T{Store.next_number(cur, st.deal_id, 'task.opened')}"
            return {"task": task}

        ev = store.append(st.deal_id, "task.opened", build, actor)
        if ev is None:
            continue  # another process opened it a moment ago
        from .state import Task
        st.tasks[task["task_id"]] = Task(**{**task, "opened": ev.ts, "history": []})
        result["opened"].append(task["task_id"])
    if resolve_missing:
        for t in list(st.tasks.values()):
            if t.source == source and t.asset_id == asset_id and t.fingerprint not in wanted and not t.closed:
                store.append(st.deal_id, "task.updated", {"task_id": t.task_id, "status": "resolved",
                                                          "note": f"no longer raised by {source} on {today.isoformat()}"}, actor)
                t.status = "resolved"
                result["resolved"].append(t.task_id)
    return result
