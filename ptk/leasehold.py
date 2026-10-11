"""Leasehold chain: replay assignments of oil and gas leases into working and net revenue interests.

A lease buy or a wellbore buy is verified against the lease's own chain: the
lease, then every assignment of the working interest and every overriding
royalty carved out along the way. The math is the mineral ledger's math:

* the lease puts 8/8 of the working interest (WI) in the lessee(s);
* an assignment moves WI pro rata from each piece the assignor holds, so every
  ORRI burden on those pieces travels with the WI;
* an ORRI is a fraction of 8/8 of production from the WI it burdens
  (proportionately reduced), exactly like a fixed NPRI on the minerals it
  burdens. ``orri_burdens`` says what it was carved from: ``conveyed`` (the WI
  the same instrument assigned; the default for a reservation), ``retained``
  (the assignor's WI; the default for a grant), ``all`` or ``owners:A;B``.

So the replay itself runs through ``ledger.replay``; this module translates the
rows, renames the flags to leasehold terms, and adds the NRI arithmetic:

    NRI(owner) = sum over the owner's WI pieces of  WI x (1 - lease royalty - ORRIs burdening that piece)

Interests are of the lease (8/8 of the lease), not of the tract. A lease
covering less than all of the minerals pays proportionately; carry that in the
deal, not here.

Leasehold takeoff CSV columns:

    seq, instrument, recorded, lease, depth, kind, assignor, assignees, shares,
    interest, warranty, orri_value, orri_burdens, royalty, notes

kind:
    root         the lease (``assignees`` are the lessees; ``royalty`` is the lessor royalty)
    assign       assignment of WI (``interest`` absolute ``1/2`` or relative ``all``, ``1/2 of grantor``)
    orri         an ORRI reserved by the assignor, or granted to ``assignees``
    orri_assign  an assignment of ORRI
    all          everything the assignor holds in the lease, WI and ORRIs (mergers, probates)
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

from .dates import DateError, iso
from .fracs import fmt, parse_fraction
from .ledger import ALL_DEPTHS, Event, Flag, Position, name_key, replay

LEASE_COLUMNS = ["seq", "instrument", "recorded", "lease", "depth", "kind", "assignor", "assignees", "shares",
                 "interest", "warranty", "orri_value", "orri_burdens", "royalty", "notes"]
KINDS = ("root", "assign", "orri", "orri_assign", "all")

_CODES = {
    "NPRI_REVIEW": "ORRI_REVIEW",
    "NPRI_INCOMPLETE": "ORRI_INCOMPLETE",
    "NPRI_BURDENS_UNREADABLE": "ORRI_BURDENS_UNREADABLE",
    "NPRI_BURDENS_NOTHING": "ORRI_BURDENS_NOTHING",
    "MINERALS_NOT_WHOLE": "WI_NOT_WHOLE",
}
_WORDS = [
    (r"\bmineral interests\b", "working interests"),
    (r"\bminerals\b", "working interest"),
    (r"\bNPRI\b", "ORRI"),
    (r"\bconveyed\b", "assigned"),
    (r"npri_kind \(fixed\|floating\) and npri_value", "orri_value"),
    (r"npri_burdens", "orri_burdens"),
]


@dataclass
class LeaseEvent:
    seq: int
    instrument: str
    recorded: str
    lease: str
    depth: str
    kind: str
    assignor: str | None
    assignees: list[str]
    shares: list[Fraction]
    interest: str
    warranty: bool = False
    orri_value: Fraction | None = None
    orri_burdens: str = ""
    royalty: Fraction | None = None
    notes: str = ""


@dataclass
class LeaseholdPosition:
    """One lease and depth interval after the replay."""

    lease: str
    depth: str
    royalty: Fraction | None
    position: Position

    @property
    def working_interests(self) -> dict[str, Fraction]:
        return self.position.minerals

    def orri_total(self, group: str) -> Fraction:
        return sum((n.value for n in self.position.npris if n.group == group), Fraction(0))

    def wi(self, owner: str) -> Fraction:
        return sum((lot.fraction for lot in self.position.lots if name_key(lot.owner) == name_key(owner)), Fraction(0))

    def nri(self, owner: str) -> Fraction | None:
        """Owner's net revenue interest in the lease: WI less royalty and the ORRIs on each piece.

        None when the lease royalty isn't known: NRI is never computed from a guess.
        """
        if self.royalty is None:
            return None
        royalty = self.royalty
        total = Fraction(0)
        for lot in self.position.lots:
            if name_key(lot.owner) != name_key(owner):
                continue
            total += lot.fraction * (1 - royalty) - sum((lot.fraction * self.orri_total(g) for g in lot.burdens), Fraction(0))
        return total

    def orris(self) -> list[dict[str, object]]:
        """Each ORRI holding as a fraction of 8/8 of the lease: value x share of the WI it burdens."""
        return [{"owner": n.owner, "value": n.value, "burdened_wi": self.position.burdened_share(n.group),
                 "of_lease": n.value * self.position.burdened_share(n.group), "source": n.source}
                for n in self.position.npris]

    def orri_held(self, owner: str) -> Fraction:
        return sum((o["of_lease"] for o in self.orris() if name_key(str(o["owner"])) == name_key(owner)), Fraction(0))


@dataclass
class LeaseholdReplay:
    positions: dict[tuple[str, str], LeaseholdPosition] = field(default_factory=dict)
    flags: list[Flag] = field(default_factory=list)


def _split(v: str | None) -> list[str]:
    return [p.strip() for p in str(v or "").split(";") if p.strip()]


def load_lease_events(path: str | Path) -> list[LeaseEvent]:
    """Read a leasehold takeoff CSV (see the module docstring for columns)."""
    events: list[LeaseEvent] = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for i, row in enumerate(csv.DictReader(fh), start=1):
            kind = (row.get("kind") or "assign").strip().lower()
            if kind not in KINDS:
                raise ValueError(f"row {i}: kind {kind!r} must be one of {', '.join(KINDS)}")
            assignees = _split(row.get("assignees"))
            shares_raw = _split(row.get("shares"))
            if shares_raw:
                shares = [parse_fraction(s) for s in shares_raw]
                if len(shares) != len(assignees):
                    raise ValueError(f"row {i}: {len(assignees)} assignees but {len(shares)} shares")
            else:
                shares = [Fraction(1, len(assignees))] * len(assignees) if assignees else []
            if shares and sum(shares) != 1:
                raise ValueError(f"row {i}: assignee shares add to {fmt(sum(shares))}, not 1")
            try:
                recorded = iso(row.get("recorded") or "")
            except DateError as e:
                raise ValueError(f"row {i}: recorded date: {e}") from e
            orri = (row.get("orri_value") or "").strip()
            royalty = (row.get("royalty") or "").strip()
            events.append(LeaseEvent(
                seq=int(row.get("seq") or i),
                instrument=(row.get("instrument") or f"row {i}").strip(),
                recorded=recorded,
                lease=(row.get("lease") or "").strip(),
                depth=(row.get("depth") or ALL_DEPTHS).strip() or ALL_DEPTHS,
                kind=kind,
                assignor=(row.get("assignor") or "").strip() or None,
                assignees=assignees,
                shares=shares,
                interest=(row.get("interest") or "").strip(),
                warranty=str(row.get("warranty") or "").strip().lower() in ("y", "yes", "true", "1", "warranty", "special"),
                orri_value=parse_fraction(orri) if orri else None,
                orri_burdens=(row.get("orri_burdens") or "").strip(),
                royalty=parse_fraction(royalty) if royalty else None,
                notes=(row.get("notes") or "").strip(),
            ))
    return events


def _to_ledger(ev: LeaseEvent) -> Event:
    base = dict(seq=ev.seq, instrument=ev.instrument, recorded=ev.recorded, tract=ev.lease, depth=ev.depth,
                grantor=ev.assignor, grantees=ev.assignees, shares=ev.shares, warranty=ev.warranty, notes=ev.notes)
    if ev.kind == "root":
        return Event(**base, estate="MI", kind="root", interest=ev.interest or "all")
    if ev.kind == "assign":
        return Event(**base, estate="MI", kind="convey", interest=ev.interest)
    if ev.kind == "orri":
        burdens = ev.orri_burdens or ("retained" if ev.assignees else "conveyed")
        return Event(**base, estate="NPRI", kind="reserve", interest=ev.interest, npri_kind="fixed",
                     npri_value=ev.orri_value, npri_burdens=burdens)
    if ev.kind == "orri_assign":
        return Event(**base, estate="NPRI", kind="convey", interest=ev.interest)
    return Event(**base, estate="ALL", kind="convey", interest=ev.interest or "all")


def _rename(flag: Flag) -> Flag:
    code = _CODES.get(flag.code, flag.code)
    detail = flag.detail
    m = re.match(r"fixed NPRI of (\S+) burdening (\S+) of the minerals \(([^)]*)\)", detail)
    if flag.code == "NPRI_REVIEW" and m:
        share = "all" if m.group(2) == "1" else m.group(2)
        detail = (f"ORRI of {m.group(1)} of 8/8 burdening {share} of the working interest ({m.group(3)}): "
                  "confirm it is proportionately reduced, and what it burdens, with the examiner")
    else:
        for pattern, repl in _WORDS:
            detail = re.sub(pattern, repl, detail)
    return Flag(code, flag.tract, flag.depth, flag.instrument, detail)


def replay_leasehold(events: list[LeaseEvent], depths: dict[str, list[str]] | None = None,
                     as_of: str | None = None) -> LeaseholdReplay:
    """Replay lease events in recording order; ``as_of`` ignores instruments recorded after it."""
    result = replay([_to_ledger(e) for e in events], depths=depths, as_of=as_of)
    out = LeaseholdReplay(flags=[_rename(f) for f in result.flags])
    royalty: dict[str, Fraction] = {}
    for ev in sorted(events, key=lambda e: (e.recorded, e.seq)):
        if ev.kind == "root" and ev.royalty is not None:
            royalty.setdefault(ev.lease, ev.royalty)
    for (lease, depth), pos in sorted(result.positions.items()):
        lp = LeaseholdPosition(lease, depth, royalty.get(lease), pos)
        out.positions[(lease, depth)] = lp
        if lp.royalty is None:
            out.flags.append(Flag("ROYALTY_MISSING", lease, depth, "-",
                                  "no lessor royalty on the lease's root row; add it before computing NRI"))
        for owner in lp.working_interests:
            nri = lp.nri(owner)
            if nri is not None and nri < 0:
                out.flags.append(Flag("BURDENS_EXCEED_LEASE", lease, depth, "-",
                                      f"{owner}'s royalty and ORRI burdens exceed its working interest"))
    for lease in sorted({e.lease for e in events}):
        out.flags.append(Flag("LEASE_STATUS_REVIEW", lease, ALL_DEPTHS, "-",
                              f"Confirm lease {lease} is still in force: production by month against the primary term "
                              "and the cessation clause, plus any shut-in payments (RRC PDQ in Texas, OCD in New Mexico; "
                              "BLM or NMSLO lease status for federal or State leases)."))
    return out


def position_for(result: LeaseholdReplay, lease: str, depth: str = ALL_DEPTHS) -> LeaseholdPosition | None:
    return result.positions.get((lease, depth)) or result.positions.get((lease, ALL_DEPTHS))


def leasehold_table(result: LeaseholdReplay) -> list[dict[str, str]]:
    rows = []
    for (lease, depth), lp in sorted(result.positions.items()):
        for owner, wi in sorted(lp.working_interests.items()):
            rows.append({"lease": lease, "depth": depth, "owner": owner, "interest": "WI", "fraction": fmt(wi),
                         "nri": fmt(lp.nri(owner)) if lp.royalty is not None else ""})
        for o in lp.orris():
            rows.append({"lease": lease, "depth": depth, "owner": str(o["owner"]), "interest": "ORRI",
                         "fraction": fmt(o["of_lease"]), "nri": ""})
    return rows


__all__ = ["LEASE_COLUMNS", "LeaseEvent", "LeaseholdPosition", "LeaseholdReplay", "load_lease_events",
           "replay_leasehold", "position_for", "leasehold_table"]
