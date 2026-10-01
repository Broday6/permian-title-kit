"""Division of interest: from tract ownership to unit decimals.

Takes the mineral and NPRI positions a ``ledger.replay`` produced, the leases
covering them, and each tract's participation in the well or unit, and builds
the revenue deck in exact fractions.

Conventions (each one is a business rule the examiner should confirm):

* An NPRI burdens only the mineral interests the ledger says it burdens (see
  ``npri_burdens`` in ledger.py), and comes out of those owners' royalty in
  proportion to their burdened minerals (Wenske v. Ealy, Tex. 2017, is the
  Texas default when the burden is spread across the estate).
* A fixed NPRI is a fraction of the production attributable to the burdened
  minerals. A floating NPRI is a fraction of each burdened owner's royalty.
* An ORRI is a fraction of 8/8 of the lease, proportionately reduced to the
  share of the minerals the lease covers, and burdens the lessees.
* An unleased mineral owner in Texas is a cost-bearing cotenant, paid a share
  of net proceeds, not a royalty. In New Mexico, a compulsory pooling order
  treats the unleased interest as a 7/8 working interest plus a 1/8 royalty
  (NMSA 70-2-17).
* In Texas, a pooled unit or allocation well doesn't bind an NPRI owner who
  hasn't ratified. Those rows are produced but flagged.

Every tract's revenue interests must add to exactly 1 before participation is
applied. Anything else is flagged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction

from .fracs import fmt, parse_fraction, to_decimal
from .ledger import ALL_DEPTHS, Flag, Position, Replay, name_key

REVENUE_TYPES = ("RI", "NPRI", "ORRI", "NRI", "UMI")


@dataclass
class Lease:
    lease_id: str
    tract: str
    lessors: list[str]
    royalty: Fraction
    lessees: dict[str, Fraction]
    orris: dict[str, Fraction] = field(default_factory=dict)
    depth: str = ALL_DEPTHS

    @classmethod
    def from_dict(cls, d: dict) -> "Lease":
        lessees = {k: parse_fraction(v) for k, v in (d.get("lessees") or {}).items()}
        if lessees and sum(lessees.values()) != 1:
            raise ValueError(f"lease {d.get('id')}: lessee shares add to {fmt(sum(lessees.values()))}, not 1")
        return cls(
            lease_id=str(d["id"]),
            tract=str(d["tract"]),
            lessors=list(d.get("lessors") or []),
            royalty=parse_fraction(d["royalty"]),
            lessees=lessees,
            orris={k: parse_fraction(v) for k, v in (d.get("orris") or {}).items()},
            depth=str(d.get("depth") or ALL_DEPTHS),
        )


@dataclass
class DeckRow:
    owner: str
    type: str  # RI | NPRI | ORRI | NRI | UMI | WI
    tract: str
    depth: str
    tract_decimal: Fraction
    unit_decimal: Fraction
    source: str  # lease id, NPRI instrument, or basis note
    note: str = ""


@dataclass
class Deck:
    rows: list[DeckRow]
    flags: list[Flag]

    def revenue_total(self) -> Fraction:
        return sum((r.unit_decimal for r in self.rows if r.type in REVENUE_TYPES), Fraction(0))

    def by_owner(self) -> list[tuple[str, str, Fraction]]:
        """Unit decimals summed per owner and interest type, largest first."""
        acc: dict[tuple[str, str], Fraction] = {}
        for r in self.rows:
            key = (r.owner, r.type)
            acc[key] = acc.get(key, Fraction(0)) + r.unit_decimal
        return sorted(((o, t, v) for (o, t), v in acc.items() if v), key=lambda x: (-x[2], x[0]))


def participation(weights: dict[str, object]) -> dict[str, Fraction]:
    """Normalize tract weights (acres in the unit, or completed lateral feet) to factors adding to 1."""
    w = {k: parse_fraction(str(v)) for k, v in weights.items()}
    total = sum(w.values(), Fraction(0))
    if total <= 0:
        raise ValueError("participation weights must add to more than zero")
    return {k: v / total for k, v in w.items()}


def build_deck(
    replay: Replay,
    leases: list[Lease],
    tract_factors: dict[str, Fraction],
    depth: dict[str, str] | str = ALL_DEPTHS,
    state: str = "TX",
    pooled: bool = True,
    npri_ratified: dict[str, bool] | None = None,
) -> Deck:
    """Build the revenue and working-interest deck for one well or unit.

    ``tract_factors`` gives each tract's participation (use ``participation``).
    ``depth`` picks the depth interval the well produces from, per tract or for all.
    """
    state = state.upper()
    npri_ratified = npri_ratified or {}
    rows: list[DeckRow] = []
    flags: list[Flag] = []
    if sum(tract_factors.values(), Fraction(0)) != 1:
        flags.append(Flag("PARTICIPATION_NOT_WHOLE", "-", "-", "-",
                          f"tract factors add to {fmt(sum(tract_factors.values(), Fraction(0)))}"))

    for tract, factor in tract_factors.items():
        want = depth.get(tract, ALL_DEPTHS) if isinstance(depth, dict) else depth
        pos = replay.positions.get((tract, want)) or replay.positions.get((tract, ALL_DEPTHS))
        if pos is None:
            flags.append(Flag("NO_TITLE", tract, want, "-", "no ownership in the ledger for this tract"))
            continue
        tract_rows, tract_flags = _tract_deck(pos, [l for l in leases if l.tract == tract and l.depth in (ALL_DEPTHS, pos.depth)],
                                              state, pooled, npri_ratified)
        for r in tract_rows:
            r.unit_decimal = r.tract_decimal * factor
        rows.extend(tract_rows)
        flags.extend(tract_flags)
        revenue = sum((r.tract_decimal for r in tract_rows if r.type in REVENUE_TYPES), Fraction(0))
        if revenue != 1:
            flags.append(Flag("DECK_NOT_WHOLE", tract, pos.depth, "-",
                              f"revenue interests add to {fmt(revenue)} ({to_decimal(revenue)}), not 1"))
    return Deck(rows, flags)


def _tract_deck(pos: Position, leases: list[Lease], state: str, pooled: bool,
                npri_ratified: dict[str, bool]) -> tuple[list[DeckRow], list[Flag]]:
    rows: list[DeckRow] = []
    flags: list[Flag] = []
    t, d = pos.tract, pos.depth

    owner_by_key = {name_key(o): o for o in pos.minerals}
    lease_of: dict[str, Lease] = {}
    for lease in leases:
        for lessor_raw in lease.lessors:
            lessor = owner_by_key.get(name_key(lessor_raw))
            if lessor is None:
                flags.append(Flag("LESSOR_NOT_OWNER", t, d, lease.lease_id,
                                  f"{lessor_raw} owns no minerals here per the ledger"))
                continue
            if lessor in lease_of:
                flags.append(Flag("DOUBLE_LEASE", t, d, lease.lease_id,
                                  f"{lessor} is also lessor in {lease_of[lessor].lease_id}; using the first"))
                continue
            lease_of[lessor] = lease

    groups: dict[str, dict] = {}
    for n in pos.npris:
        if n.kind not in ("fixed", "floating"):
            flags.append(Flag("NPRI_KIND", t, d, n.source, f"{n.owner}: kind {n.kind!r} must be fixed or floating"))
            continue
        g = groups.setdefault(n.group, {"kind": n.kind, "total": Fraction(0), "base": Fraction(0)})
        g["total"] += n.value

    covered: dict[str, Fraction] = {}
    lots_by_owner: dict[str, list] = {}
    for lot in pos.lots:
        lots_by_owner.setdefault(lot.owner, []).append(lot)
    for owner, m in sorted(pos.minerals.items()):
        lease = lease_of.get(owner)
        if lease is None:
            if state == "NM":
                rows.append(DeckRow(owner, "UMI", t, d, m / 8, Fraction(0), "NM pooling", "1/8 royalty under NMSA 70-2-17"))
                rows.append(DeckRow(owner, "UMI", t, d, m * 7 / 8, Fraction(0), "NM pooling", "7/8 working interest, subject to risk charge"))
                rows.append(DeckRow(owner, "WI", t, d, m * 7 / 8, Fraction(0), "NM pooling", "cost-bearing"))
            else:
                rows.append(DeckRow(owner, "UMI", t, d, m, Fraction(0), "unleased",
                                    "Texas cotenant: share of net proceeds after costs, not a royalty"))
                rows.append(DeckRow(owner, "WI", t, d, m, Fraction(0), "unleased", "cost-bearing"))
            flags.append(Flag("UNLEASED", t, d, "-", f"{owner} holds {fmt(m)} unleased"))
            burdened = sum((lot.fraction for lot in lots_by_owner.get(owner, []) if lot.burdens & groups.keys()), Fraction(0))
            if burdened:
                flags.append(Flag("NPRI_ON_UNLEASED", t, d, "-",
                                  f"NPRIs burden {fmt(burdened)} of {owner}'s unleased minerals; the deck doesn't carve them out"))
            continue
        r = lease.royalty
        burden = Fraction(0)
        for lot in lots_by_owner.get(owner, []):
            for gid in lot.burdens:
                g = groups.get(gid)
                if g is None:
                    continue
                share = lot.fraction if g["kind"] == "fixed" else lot.fraction * r
                g["base"] += share
                burden += share * g["total"]
        net = m * r - burden
        if net < 0:
            flags.append(Flag("NPRI_EXCEEDS_ROYALTY", t, d, lease.lease_id,
                              f"{owner}'s royalty {fmt(m * r)} can't carry the NPRI burden; check the reservation language"))
        rows.append(DeckRow(owner, "RI", t, d, net, Fraction(0), lease.lease_id,
                            f"{fmt(m)} minerals × {fmt(r)} royalty, less NPRI burden"))
        covered[lease.lease_id] = covered.get(lease.lease_id, Fraction(0)) + m

    for n in pos.npris:
        g = groups.get(n.group)
        if g is None:
            continue
        basis = "of production" if g["kind"] == "fixed" else "of royalty"
        rows.append(DeckRow(n.owner, "NPRI", t, d, n.value * g["base"], Fraction(0), n.source,
                            f"{g['kind']} {fmt(n.value)} {basis} from the leased minerals it burdens"))
    if pooled and state == "TX":
        for n in pos.npris:
            if not any(name_key(k) == name_key(n.owner) and v for k, v in npri_ratified.items()):
                flags.append(Flag("NPRI_NOT_RATIFIED", t, d, n.source,
                                  f"{n.owner} hasn't ratified pooling; paid on tract production, not the unit formula"))

    for lease in leases:
        m = covered.get(lease.lease_id, Fraction(0))
        if not m:
            continue
        orri_total = sum(lease.orris.values(), Fraction(0))
        for owner, o in lease.orris.items():
            rows.append(DeckRow(owner, "ORRI", t, d, o * m, Fraction(0), lease.lease_id,
                                f"{fmt(o)} of 8/8, reduced to the {fmt(m)} the lease covers"))
        nri_factor = 1 - lease.royalty - orri_total
        if nri_factor < 0:
            flags.append(Flag("BURDENS_EXCEED_LEASE", t, d, lease.lease_id, "royalty plus ORRIs exceed 8/8"))
        if not lease.lessees:
            flags.append(Flag("NO_LESSEE", t, d, lease.lease_id, "lease has no lessee shares"))
        for lessee, share in lease.lessees.items():
            rows.append(DeckRow(lessee, "WI", t, d, m * share, Fraction(0), lease.lease_id, ""))
            rows.append(DeckRow(lessee, "NRI", t, d, m * share * nri_factor, Fraction(0), lease.lease_id,
                                f"WI × (1 − {fmt(lease.royalty)} − {fmt(orri_total)})"))
    return rows, flags
