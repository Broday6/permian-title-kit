"""Chain-of-title ledger: replay conveyances to get ownership on any date.

Each row of a takeoff is an event that debits a grantor and credits grantees
for one tract, one depth interval and one estate. Replaying the events in
recording order gives the ownership at any date, in exact fractions.

The ledger *flags* legal questions and never resolves them. An over-conveyance
under a warranty deed is reported as a Duhig candidate, and only what the
grantor actually held is moved. A grantor who never appears as a grantee is a
chain gap, and nothing moves. The examining attorney decides both.

Estates:
    MI    mineral interest, as a fraction of the whole mineral estate
    NPRI  non-participating royalty, carried with its kind:
          fixed (fraction of production) or floating (fraction of royalty)
    ALL   everything the grantor holds in the tract (minerals and NPRIs), for
          probates, heirship distributions and "all right, title and interest" deeds

An NPRI burdens specific mineral interests, and the burden follows those
minerals through later conveyances. The takeoff's ``npri_burdens`` column says
what an NPRI was carved from:

    all         every mineral interest in the tract (the default)
    conveyed    the minerals conveyed by the same instrument
    retained    the minerals the reserving grantor kept
    owners:A;B  the minerals held by the named owners at that date

``npri_value`` is a fraction of the production (fixed) or royalty (floating)
attributable to the burdened minerals. Which reading a deed supports is the
examiner's call; the ledger only carries it.

Owner names match regardless of case, spacing and periods ("Baker Example" and
"BAKER EXAMPLE" are one owner); each such match is reported as NAME_VARIANT.
"""

from __future__ import annotations

import csv
import re
from collections import defaultdict
from dataclasses import dataclass, field, replace
from fractions import Fraction
from pathlib import Path

from .dates import DateError, iso
from .fracs import FractionError, fmt, parse_fraction

ALL_DEPTHS = "ALL"


@dataclass
class Event:
    seq: int
    instrument: str
    recorded: str  # ISO date; replay order is (recorded, seq)
    tract: str
    depth: str
    estate: str  # MI | NPRI
    kind: str  # root | convey | reserve
    grantor: str | None
    grantees: list[str]
    shares: list[Fraction]
    interest: str  # "1/4", "all", "1/2 of grantor"
    warranty: bool = False
    npri_kind: str | None = None  # fixed | floating
    npri_value: Fraction | None = None
    notes: str = ""
    npri_burdens: str = "all"


@dataclass
class Flag:
    code: str
    tract: str
    depth: str
    instrument: str
    detail: str

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"[{self.code}] {self.tract}/{self.depth} {self.instrument}: {self.detail}"


@dataclass
class Npri:
    owner: str
    kind: str  # fixed | floating
    value: Fraction  # of production (fixed) or of royalty (floating) from the burdened minerals
    source: str
    group: str = ""  # burden group: the reservation this holding descends from


@dataclass
class Lot:
    """A piece of one owner's minerals, carrying the NPRI groups that burden it."""

    owner: str
    fraction: Fraction
    burdens: frozenset[str] = frozenset()
    source: str = ""  # instrument that created this piece


@dataclass
class Position:
    """Ownership of one tract and depth interval at the end of a replay."""

    tract: str
    depth: str
    lots: list[Lot] = field(default_factory=list)
    npris: list[Npri] = field(default_factory=list)

    @property
    def minerals(self) -> dict[str, Fraction]:
        out: dict[str, Fraction] = {}
        for lot in self.lots:
            out[lot.owner] = out.get(lot.owner, Fraction(0)) + lot.fraction
        return {k: v for k, v in out.items() if v != 0}

    @property
    def mineral_total(self) -> Fraction:
        return sum((lot.fraction for lot in self.lots), Fraction(0))

    def balance(self, owner: str) -> Fraction:
        return sum((lot.fraction for lot in self.lots if lot.owner == owner), Fraction(0))

    def burdened_share(self, group: str) -> Fraction:
        """Share of the whole mineral estate an NPRI group burdens."""
        return sum((lot.fraction for lot in self.lots if group in lot.burdens), Fraction(0))


@dataclass
class Replay:
    positions: dict[tuple[str, str], Position]
    flags: list[Flag]


def name_key(name: str | None) -> str:
    """Identity key for matching owners across instruments: case, spacing and periods ignored."""
    return re.sub(r"\s+", " ", re.sub(r"[.,]", " ", name or "")).strip().upper()


def _bool(v: str | None) -> bool:
    return str(v or "").strip().lower() in ("y", "yes", "true", "1", "warranty", "general", "special")


def _split(v: str | None) -> list[str]:
    return [p.strip() for p in str(v or "").split(";") if p.strip()]


def load_events(path: str | Path) -> list[Event]:
    """Read a takeoff CSV. Columns:

    seq, instrument, recorded, tract, depth, estate, kind, grantor, grantees,
    shares, interest, warranty, npri_kind, npri_value, notes, npri_burdens

    ``grantees`` and ``shares`` are ``;``-separated. Shares default to equal.
    """
    events: list[Event] = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for i, row in enumerate(csv.DictReader(fh), start=1):
            grantees = _split(row.get("grantees"))
            shares_raw = _split(row.get("shares"))
            if shares_raw:
                shares = [parse_fraction(s) for s in shares_raw]
                if len(shares) != len(grantees):
                    raise ValueError(f"row {i}: {len(grantees)} grantees but {len(shares)} shares")
            else:
                shares = [Fraction(1, len(grantees))] * len(grantees) if grantees else []
            if shares and sum(shares) != 1:
                raise ValueError(f"row {i}: grantee shares add to {fmt(sum(shares))}, not 1")
            npri_value = row.get("npri_value") or ""
            try:
                recorded = iso(row.get("recorded") or "")
            except DateError as e:
                raise ValueError(f"row {i}: recorded date: {e}") from e
            events.append(
                Event(
                    seq=int(row.get("seq") or i),
                    instrument=(row.get("instrument") or f"row {i}").strip(),
                    recorded=recorded,
                    tract=(row.get("tract") or "").strip(),
                    depth=(row.get("depth") or ALL_DEPTHS).strip() or ALL_DEPTHS,
                    estate=(row.get("estate") or "MI").strip().upper(),
                    kind=(row.get("kind") or "convey").strip().lower(),
                    grantor=(row.get("grantor") or "").strip() or None,
                    grantees=grantees,
                    shares=shares,
                    interest=(row.get("interest") or "").strip(),
                    warranty=_bool(row.get("warranty")),
                    npri_kind=(row.get("npri_kind") or "").strip().lower() or None,
                    npri_value=parse_fraction(npri_value) if npri_value.strip() else None,
                    notes=(row.get("notes") or "").strip(),
                    npri_burdens=(row.get("npri_burdens") or "all").strip() or "all",
                )
            )
    return events


def _is_relative(interest: str) -> bool:
    t = interest.strip().lower()
    return t in ("", "all") or t.startswith("all of grantor") or t.startswith("all grantor") or " of grantor" in t


def _amount(interest: str, balance: Fraction) -> tuple[Fraction, bool]:
    """Resolve an interest phrase against the grantor's balance.

    Returns (amount of the whole estate, phrase was relative to the grantor).
    """
    t = interest.strip().lower()
    if t in ("", "all", "all of grantor", "all of grantor's interest", "all grantor's interest"):
        return balance, True
    for suffix in (" of grantor's interest", " of grantor", " of grantor's"):
        if t.endswith(suffix):
            return parse_fraction(t[: -len(suffix)]) * balance, True
    return parse_fraction(t), False


def replay(
    events: list[Event],
    depths: dict[str, list[str]] | None = None,
    as_of: str | None = None,
) -> Replay:
    """Replay events in recording order.

    ``depths`` maps a tract to its depth intervals, e.g.
    ``{"T1": ["SURF-BASE_WOLFCAMP", "BELOW_BASE_WOLFCAMP"]}``. An event with
    depth ``ALL`` applies to every interval of its tract. Without a map, each
    tract has the single interval ``ALL``.
    """
    depths = depths or {}
    flags: list[Flag] = []
    positions: dict[tuple[str, str], Position] = {}

    def intervals(tract: str, depth: str) -> list[str]:
        known = depths.get(tract) or [ALL_DEPTHS]
        if depth == ALL_DEPTHS:
            return known
        if depth not in known:
            raise ValueError(f"tract {tract}: depth {depth!r} isn't one of {known}")
        return [depth]

    canon: dict[str, str] = {}

    def canonical(name: str | None, ev: Event) -> str | None:
        if not name:
            return name
        first = canon.setdefault(name_key(name), name)
        if first != name:
            flags.append(Flag("NAME_VARIANT", ev.tract, ev.depth, ev.instrument,
                              f"{name!r} treated as the same owner as {first!r}"))
        return first

    as_of = iso(as_of) if as_of else None
    ordered = sorted(events, key=lambda e: (e.recorded or "9999", e.seq))
    for ev in ordered:
        if as_of and ev.recorded and ev.recorded > as_of:
            continue
        ev = replace(ev, grantor=canonical(ev.grantor, ev), grantees=[canonical(g, ev) or g for g in ev.grantees])
        for depth in intervals(ev.tract, ev.depth):
            pos = positions.setdefault((ev.tract, depth), Position(ev.tract, depth))
            try:
                if ev.estate == "MI":
                    _apply_mineral(ev, pos, flags)
                elif ev.estate == "NPRI":
                    _apply_npri(ev, pos, flags)
                elif ev.estate == "ALL" and not _is_relative(ev.interest):
                    flags.append(Flag("ALL_NEEDS_RELATIVE_INTEREST", pos.tract, pos.depth, ev.instrument,
                                      f"estate ALL needs 'all' or 'x of grantor', not {ev.interest!r}; nothing moved"))
                elif ev.estate == "ALL":
                    has_mi = pos.balance(ev.grantor or "") > 0
                    has_npri = any(n.owner == ev.grantor for n in pos.npris)
                    if not (has_mi or has_npri):
                        flags.append(Flag("CHAIN_GAP", pos.tract, pos.depth, ev.instrument,
                                          f"{ev.grantor} holds nothing here on this date; nothing moved"))
                    if has_mi:
                        _apply_mineral(ev, pos, flags)
                    if has_npri:
                        _apply_npri(ev, pos, flags)
                else:
                    flags.append(Flag("UNKNOWN_ESTATE", ev.tract, depth, ev.instrument, ev.estate))
            except FractionError as e:
                flags.append(Flag("UNREADABLE_FRACTION", ev.tract, depth, ev.instrument, str(e)))

    for (tract, depth), pos in positions.items():
        _consolidate(pos)
        for n in pos.npris:
            own = sum((lot.fraction for lot in pos.lots if lot.owner == n.owner and n.group in lot.burdens), Fraction(0))
            if own:
                flags.append(Flag("MERGER_REVIEW", tract, depth, n.source,
                                  f"{n.owner} holds {fmt(own)} of the minerals its own NPRI burdens; the examiner decides merger"))
        total = pos.mineral_total
        if total != 1:
            flags.append(
                Flag("MINERALS_NOT_WHOLE", tract, depth, "-",
                     f"mineral interests add to {fmt(total)}, not 1")
            )
    return Replay(positions, flags)


def _consolidate(pos: Position) -> None:
    merged: dict[tuple[str, frozenset[str]], Fraction] = {}
    for lot in pos.lots:
        key = (lot.owner, lot.burdens)
        merged[key] = merged.get(key, Fraction(0)) + lot.fraction
    pos.lots = [Lot(o, f, b) for (o, b), f in merged.items() if f != 0]


def _apply_mineral(ev: Event, pos: Position, flags: list[Flag]) -> None:
    if ev.kind == "root":
        amount = parse_fraction(ev.interest) if ev.interest and ev.interest.lower() != "all" else Fraction(1)
        for g, s in zip(ev.grantees, ev.shares):
            pos.lots.append(Lot(g, amount * s, frozenset(), ev.instrument))
        return
    if ev.kind == "reserve":
        flags.append(Flag("MI_RESERVE_IGNORED", pos.tract, pos.depth, ev.instrument,
                          "mineral reservations are modeled by conveying less; this row moved nothing"))
        return
    grantor = ev.grantor or ""
    balance = pos.balance(grantor)
    if balance == 0:
        flags.append(Flag("CHAIN_GAP", pos.tract, pos.depth, ev.instrument,
                          f"{grantor or 'blank grantor'} holds no minerals here on this date; nothing moved"))
        return
    amount, _relative = _amount(ev.interest, balance)
    if amount > balance:
        code = "DUHIG_CANDIDATE" if ev.warranty else "OVER_CONVEYANCE"
        flags.append(Flag(code, pos.tract, pos.depth, ev.instrument,
                          f"{grantor} conveyed {fmt(amount)} but held {fmt(balance)}; moved {fmt(balance)}"))
        amount = balance
    # Take the conveyed amount pro rata from each of the grantor's pieces, so
    # every NPRI burden on those pieces travels with the minerals.
    for lot in [x for x in pos.lots if x.owner == grantor]:
        take = amount * lot.fraction / balance
        lot.fraction -= take
        for g, s in zip(ev.grantees, ev.shares):
            pos.lots.append(Lot(g, take * s, lot.burdens, ev.instrument))
    pos.lots = [x for x in pos.lots if x.fraction != 0]


def _apply_npri(ev: Event, pos: Position, flags: list[Flag]) -> None:
    if ev.kind in ("root", "reserve"):
        if not ev.npri_kind or ev.npri_value is None:
            flags.append(Flag("NPRI_INCOMPLETE", pos.tract, pos.depth, ev.instrument,
                              "an NPRI needs npri_kind (fixed|floating) and npri_value"))
            return
        group = f"{ev.instrument}#{ev.seq}"
        burdened = _burdened_lots(ev, pos)
        if burdened is None:
            flags.append(Flag("NPRI_BURDENS_UNREADABLE", pos.tract, pos.depth, ev.instrument,
                              f"npri_burdens {ev.npri_burdens!r}: use all, conveyed, retained or owners:A;B"))
            return
        if not burdened:
            flags.append(Flag("NPRI_BURDENS_NOTHING", pos.tract, pos.depth, ev.instrument,
                              f"npri_burdens {ev.npri_burdens!r} matched no minerals on this date"))
            return
        for lot in burdened:
            lot.burdens = lot.burdens | {group}
        owners = ev.grantees or ([ev.grantor] if ev.grantor else [])
        shares = ev.shares or [Fraction(1)]
        for g, s in zip(owners, shares):
            pos.npris.append(Npri(g, ev.npri_kind, ev.npri_value * s, ev.instrument, group))
        share = sum((lot.fraction for lot in burdened), Fraction(0))
        flags.append(Flag("NPRI_REVIEW", pos.tract, pos.depth, ev.instrument,
                          f"{ev.npri_kind} NPRI of {fmt(ev.npri_value)} burdening {fmt(share)} of the minerals "
                          f"({ev.npri_burdens}): confirm fixed vs floating and what it burdens with the examiner"))
        return
    # convey: move part of the grantor's NPRI holdings, kind by kind
    held = [n for n in pos.npris if n.owner == ev.grantor]
    if not held:
        flags.append(Flag("CHAIN_GAP", pos.tract, pos.depth, ev.instrument,
                          f"{ev.grantor} holds no NPRI here on this date; nothing moved"))
        return
    t = ev.interest.strip().lower()
    for n in held:
        if t in ("", "all", "all of grantor", "all of grantor's interest"):
            part = n.value
        elif t.endswith(" of grantor") or t.endswith(" of grantor's interest"):
            part = parse_fraction(t.split(" of grantor")[0]) * n.value
        else:
            part = parse_fraction(t) * (n.value / sum((h.value for h in held), Fraction(0)))
            if part > n.value:
                flags.append(Flag("OVER_CONVEYANCE", pos.tract, pos.depth, ev.instrument,
                                  f"{ev.grantor} conveyed more NPRI than held"))
                part = n.value
        n.value -= part
        for g, s in zip(ev.grantees, ev.shares):
            pos.npris.append(Npri(g, n.kind, part * s, ev.instrument, n.group))
    pos.npris = [n for n in pos.npris if n.value != 0]


def _burdened_lots(ev: Event, pos: Position) -> list[Lot] | None:
    how = (ev.npri_burdens or "all").strip()
    low = how.lower()
    if low in ("", "all"):
        return [lot for lot in pos.lots if lot.fraction]
    if low == "conveyed":
        return [lot for lot in pos.lots if lot.source == ev.instrument and lot.owner != ev.grantor and lot.fraction]
    if low == "retained":
        return [lot for lot in pos.lots if lot.owner == ev.grantor and lot.fraction]
    if low.startswith("owners:"):
        wanted = {name_key(x) for x in how.split(":", 1)[1].split(";") if x.strip()}
        return [lot for lot in pos.lots if name_key(lot.owner) in wanted and lot.fraction]
    return None


def ownership_table(result: Replay) -> list[dict[str, str]]:
    rows = []
    for (tract, depth), pos in sorted(result.positions.items()):
        for owner, frac in sorted(pos.minerals.items()):
            rows.append({"tract": tract, "depth": depth, "owner": owner, "estate": "MI",
                         "fraction": fmt(frac), "kind": "", "burdens": ""})
        for n in pos.npris:
            rows.append({"tract": tract, "depth": depth, "owner": n.owner, "estate": "NPRI",
                         "fraction": fmt(n.value), "kind": n.kind, "burdens": fmt(pos.burdened_share(n.group))})
    return rows


def group_flags(flags: list[Flag]) -> dict[str, list[Flag]]:
    out: dict[str, list[Flag]] = defaultdict(list)
    for f in flags:
        out[f.code].append(f)
    return dict(out)
