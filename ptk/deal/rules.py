"""Business rules for the deal pipeline, loaded from JSON so the land department can change them.

The defaults live in ``rules.json`` beside this file. A ``ptk-rules.json`` next
to the deals database (or ``--rules``) overrides them key by key: objects merge,
everything else replaces.
"""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

from ..fracs import parse_fraction

DEFAULT_PATH = Path(__file__).with_name("rules.json")
SEVERITIES = ("stop", "review", "info")
STAGES = ("intake", "verify", "curative", "closing", "record", "payment")
INTERNAL_PARTIES = ("landman", "attorney", "acquisitions")


def _merge(base: dict, over: dict) -> dict:
    out = deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = deepcopy(v)
    return out


@dataclass(frozen=True)
class FlagRule:
    code: str
    severity: str
    party: str
    requires_attorney: bool
    stage: str
    due_days: int
    template: str | None
    requirement: str


class Rules:
    def __init__(self, data: dict[str, Any], source: str = "defaults"):
        self.data = data
        self.source = source
        for item in data.get("checklists", []):
            if item.get("stage", "curative") not in STAGES:
                raise ValueError(f"rules: checklist {item.get('id')} has unknown stage {item.get('stage')!r}")

    @classmethod
    def load(cls, path: str | Path | None = None, beside: str | Path | None = None) -> "Rules":
        data = json.loads(DEFAULT_PATH.read_text(encoding="utf-8"))
        candidates = [Path(path)] if path else []
        if beside and not path:
            candidates.append(Path(beside).resolve().parent / "ptk-rules.json")
        for c in candidates:
            if c.exists():
                return cls(_merge(data, json.loads(c.read_text(encoding="utf-8"))), str(c))
            if path:
                raise ValueError(f"rules file {c} doesn't exist")
        return cls(data)

    # ---- flags -------------------------------------------------------------
    def flag(self, code: str, severity: str | None = None) -> FlagRule:
        base = {**self.data["default_flag"], **self.data["flags"].get(code, {})}
        if "severity" not in self.data["flags"].get(code, {}) and severity:
            base["severity"] = severity  # the source's own severity (runsheet rules carry one)
        sev = base["severity"] if base["severity"] in SEVERITIES else "review"
        att = base.get("requires_attorney", False)
        if isinstance(att, list):
            att = sev in att
        return FlagRule(code, sev, base.get("party", "landman"), bool(att), base.get("stage", "curative"),
                        int(base.get("due_days", 14)), base.get("template"), base.get("requirement", ""))

    # ---- policies ----------------------------------------------------------
    @property
    def attorney(self) -> dict[str, Any]:
        return self.data["attorney"]

    @property
    def messaging(self) -> dict[str, Any]:
        return self.data["messaging"]

    def payment(self, state: str) -> dict[str, Any]:
        return self.data["payment"].get(state.upper(), {"watch_days_after_notice": 120, "authority": ""})

    @property
    def decimal_tolerance(self) -> Fraction:
        return parse_fraction(str(self.data["payment"].get("decimal_tolerance", "1/100000000")))

    # ---- checklists --------------------------------------------------------
    def checklists(self) -> list[dict[str, Any]]:
        return list(self.data.get("checklists", []))


def checklist_applies(item: dict[str, Any], state: str, kind: str, facts: dict[str, Any]) -> bool:
    """Does a checklist item apply to an asset with these facts?

    ``when`` keys: a list means "fact is one of these"; a bool means "fact is exactly this".
    """
    if state not in item.get("states", [state]):
        return False
    kinds = item.get("kinds", ["*"])
    if "*" not in kinds and kind not in kinds:
        return False
    for key, want in (item.get("when") or {}).items():
        have = facts.get(key)
        if isinstance(want, list):
            if have not in want:
                return False
        elif have != want:
            return False
    return True
