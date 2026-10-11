"""Stage 2, verify ownership: does the seller own at least what it sold?

For each asset this runs the kit's title math on the asset's own title files:

* minerals and royalties: the takeoff through ``ledger.replay``, as of the
  effective date and again with everything recorded since (the bring-down);
* leases, wellbores and ORRIs: the lease chain through ``leasehold.replay_leasehold``;
  a horizontal well's WI and NRI come from each lease's share of the well
  (its participation weights);
* the runsheet's red-flag rules, for rows in the asset's county.

It compares what the seller held with what the deal says it sold and raises
``SELLER_SHORTFALL``, ``NRI_SHORTFALL``, ``SELLER_NOT_IN_CHAIN`` or
``SELLER_CONVEYED_AFTER_EFFECTIVE`` (a later conveyance by the seller to someone
other than the buyer: a possible double sale). Title flags on instruments in
the seller's chain become curative tasks; flags elsewhere in the tract are kept
as notes on the result, so nothing is dropped and nothing irrelevant blocks
the deal.

For each producing well with a deck project, it also computes the decimal the
buyer should be paid after closing, by replaying the takeoff with the deal's
conveyance added and building the deck (the buyer takes subject to the
seller's lease). Those become the expected payments that stage 6 reconciles
against the operator's checks.
"""

from __future__ import annotations

import json
from datetime import date
from fractions import Fraction
from pathlib import Path
from typing import Any

from .. import runsheet
from ..doi import REVENUE_TYPES, Lease, build_deck, participation
from ..fracs import FractionError, fmt, parse_fraction, to_decimal
from ..leasehold import LeaseEvent, load_lease_events, position_for, replay_leasehold
from ..ledger import ALL_DEPTHS, Event, Flag, load_events, name_key, replay
from .intake import interest_of
from .record import lease_ids, seller_by_id
from .rules import Rules
from .state import fold, summary
from .store import Store
from .tasks import TaskSpec, sync


def _seller_names(record: dict[str, Any], asset: dict[str, Any]) -> list[str]:
    return [s["name"] for sid in asset["sellers"] if (s := seller_by_id(record, sid))]


def _chain(events: list, owners: list[str], scope: str, grantor_attr: str, grantees_attr: str, scope_attr: str,
           as_of: str | None) -> set[str]:
    """Instruments in the owners' chain: walk back from them through every grantor that conveyed to the chain."""
    keys = {name_key(o) for o in owners}
    found: set[str] = set()
    evs = [e for e in events if getattr(e, scope_attr) == scope and (not as_of or e.recorded <= as_of)]
    changed = True
    while changed:
        changed = False
        for e in evs:
            grantees = {name_key(g) for g in getattr(e, grantees_attr)}
            grantor = getattr(e, grantor_attr)
            touches = grantees & keys or (not grantees and grantor and name_key(grantor) in keys)
            if touches and e.instrument not in found:
                found.add(e.instrument)
                changed = True
                if grantor and name_key(grantor) not in keys:
                    keys.add(name_key(grantor))
    return found


BURDEN_CODES = {"NPRI_REVIEW", "NPRI_NOT_RATIFIED", "MERGER_REVIEW", "NPRI_EXCEEDS_ROYALTY", "NPRI_ON_UNLEASED",
                "NPRI_KIND", "ORRI_REVIEW"}


def burden_refs(pos, owners: list[str]) -> set[str]:
    """Instruments behind every NPRI (or ORRI) that burdens the owners' interest or that the owners hold."""
    keys = {name_key(o) for o in owners}
    groups = {g for lot in pos.lots if name_key(lot.owner) in keys for g in lot.burdens}
    refs: set[str] = set()
    for n in pos.npris:
        if n.group in groups or name_key(n.owner) in keys:
            refs |= {n.source, n.group.rsplit("#", 1)[0]}
    return refs


def _flag_specs(flags: list[Flag], scope: str, depth: str, chain: set[str], names: list[str], asset_id: str,
                origin: str, burdens: set[str] | None = None) -> tuple[list[TaskSpec], list[str]]:
    """Split flags into tasks (they bear on the seller's interest) and notes (elsewhere in the tract).

    A chain flag counts when its instrument is in the seller's chain. An NPRI or
    ORRI flag counts only when that burden sits on (or is held by) the seller's
    interest: an NPRI carved from another owner's half is that owner's problem.
    """
    specs, notes = [], []
    keys = [name_key(n) for n in names]
    burdens = burdens or set()
    for f in flags:
        if f.tract not in (scope, "-"):
            continue
        if depth != ALL_DEPTHS and f.depth not in (depth, ALL_DEPTHS, "-"):
            continue  # another depth interval of the same tract
        named = any(k and k in name_key(f.detail) for k in keys)
        if f.code in BURDEN_CODES:
            relevant = f.instrument in burdens or named
        else:
            relevant = f.instrument in chain or f.instrument == "-" or named
        party = None
        if f.code in ("NPRI_NOT_RATIFIED", "LESSOR_NOT_OWNER"):
            party = f.detail.split(" hasn't")[0].split(" owns")[0].strip()
        if relevant:
            specs.append(TaskSpec(f.code, f"{f.instrument}: {f.detail}" if f.instrument != "-" else f.detail,
                                  "verify", asset_id, ref=f"{origin}:{f.tract}/{f.depth}:{f.instrument}", party_name=party))
        else:
            notes.append(f"[{f.code}] {f.instrument}: {f.detail} (outside the seller's chain; not a task)")
    return specs, notes


def _runsheet_specs(asset: dict[str, Any], specs: list[TaskSpec], notes: list[str]) -> None:
    path = asset["title"].get("runsheet")
    if not path:
        return
    if not Path(path).exists():
        specs.append(TaskSpec("TITLE_FILE_MISSING", f"Runsheet {path} doesn't exist.", "verify", asset["id"], ref=f"file:{path}"))
        return
    images = asset["title"].get("images")
    rows, flags = runsheet.check_file(path, images)
    county_of = {r.get("row"): (r.get("county") or "").strip().upper() for r in rows}
    want = (asset.get("county") or "").upper()
    skipped = 0
    for f in flags:
        if f.row != "-" and county_of.get(f.row) and want and county_of[f.row] != want:
            skipped += 1
            continue
        specs.append(TaskSpec(f.code, f"Runsheet row {f.row}: {f.message}", "verify", asset["id"],
                              ref=f"runsheet:row {f.row}", severity=f.severity))
    if skipped:
        notes.append(f"{skipped} runsheet flag(s) on rows in other counties were skipped for this asset")


def _load_json(path: str | None) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8")) if path else None


def _post_closing_deck(project_path: str, record: dict[str, Any], asset: dict[str, Any], amounts: dict[str, Fraction],
                       estate: str) -> tuple[list[tuple[str, Fraction]], list[Flag], str]:
    """Buyer's unit decimals in one well after the deal: replay with the deal's conveyance added, then build the deck."""
    p = json.loads(Path(project_path).read_text(encoding="utf-8"))
    base = Path(project_path).parent
    events = load_events((base / p["takeoff"]).resolve())
    buyer = record["buyer"]
    seq = max((e.seq for e in events), default=0)
    for i, (seller, amount) in enumerate(amounts.items(), start=1):
        if amount:
            events.append(Event(seq + i, f"Deal {record['deal_id']} (pending)", record["effective_date"], asset["tract"],
                                asset.get("depth") or ALL_DEPTHS, estate, "convey", seller, [buyer], [Fraction(1)], fmt(amount)))
    result = replay(events, depths=p.get("depths"))
    leases = []
    for d in p.get("leases", []):
        lessors = list(d.get("lessors") or [])
        pos = result.positions.get((d["tract"], asset.get("depth") or ALL_DEPTHS)) or result.positions.get((d["tract"], ALL_DEPTHS))
        sellers_here = [s for s in amounts if any(name_key(x) == name_key(s) for x in lessors)]
        if sellers_here and not any(name_key(x) == name_key(buyer) for x in lessors):
            lessors.append(buyer)  # the buyer takes subject to the seller's lease (added once, however many sellers)
        for seller in sellers_here:
            kept = sum((lot.fraction for lot in pos.lots if name_key(lot.owner) == name_key(seller)), Fraction(0)) if pos else 0
            if not kept:
                lessors = [x for x in lessors if name_key(x) != name_key(seller)]
        leases.append(Lease.from_dict({**d, "lessors": lessors}))
    deck = build_deck(result, leases, participation(p["participation"]["weights"]), depth=p.get("produce_depth", ALL_DEPTHS),
                      state=p.get("state", asset["state"]), pooled=p.get("pooled", True), npri_ratified=p.get("npri_ratified"))
    rows = [(typ, value) for owner, typ, value in deck.by_owner() if name_key(owner) == name_key(buyer) and typ in REVENUE_TYPES]
    return rows, deck.flags, p.get("well", Path(project_path).stem)


def _expected(asset: dict[str, Any], well: dict[str, Any], typ: str, value: Fraction, source: str) -> dict[str, Any]:
    wname = well.get("name") or well.get("api") or "well"
    return {"key": f"{asset['id']}|{well.get('api') or wname}|{typ}", "asset_id": asset["id"],
            "payor": well.get("payor") or well.get("operator") or "operator (not named)", "well": wname,
            "well_api": well.get("api"), "type": typ, "decimal": fmt(value), "decimal_display": to_decimal(value),
            "source": source}


def verify_minerals(record: dict[str, Any], asset: dict[str, Any]) -> tuple[dict[str, Any], list[TaskSpec], list[dict]]:
    aid, kind = asset["id"], asset["kind"]
    estate = "MI" if kind == "mineral" else "NPRI"
    specs: list[TaskSpec] = []
    notes: list[str] = []
    expected: list[dict] = []
    takeoff = asset["title"].get("takeoff")
    if not takeoff:
        specs.append(TaskSpec("NO_TITLE_FILES", "No takeoff for this asset (title.takeoff).", "verify", aid))
        return {"status": "NO_TITLE", "summary": "no takeoff to verify against"}, specs, expected
    if not Path(takeoff).exists():
        specs.append(TaskSpec("TITLE_FILE_MISSING", f"Takeoff {takeoff} doesn't exist.", "verify", aid, ref=f"file:{takeoff}"))
        return {"status": "NO_TITLE", "summary": f"takeoff {takeoff} not found"}, specs, expected
    try:
        events = load_events(takeoff)
        depths = _load_json(asset["title"].get("depths"))
    except (ValueError, FractionError) as e:
        specs.append(TaskSpec("TITLE_FILE_ERROR", f"Takeoff {Path(takeoff).name}: {e}", "verify", aid, ref=f"file:{takeoff}"))
        return {"status": "ERROR", "summary": str(e)}, specs, expected

    as_of = record.get("effective_date")
    tract, depth = asset["tract"], asset.get("depth") or ALL_DEPTHS
    sellers = _seller_names(record, asset)
    buyer_key = name_key(record["buyer"])
    at_eff = replay(events, depths=depths, as_of=as_of)
    later = [e for e in events if as_of and e.recorded > as_of and not any(name_key(g) == buyer_key for g in e.grantees)]
    # The bring-down: everything recorded since, except the deal's own conveyance to the buyer.
    no_buyer = replay([e for e in events if not (as_of and e.recorded > as_of and any(name_key(g) == buyer_key for g in e.grantees))],
                      depths=depths)

    def pos_of(r):
        return r.positions.get((tract, depth)) or r.positions.get((tract, ALL_DEPTHS))

    def held(r, owner: str) -> Fraction:
        pos = pos_of(r)
        if pos is None:
            return Fraction(0)
        if estate == "MI":
            return sum((lot.fraction for lot in pos.lots if name_key(lot.owner) == name_key(owner)), Fraction(0))
        return sum((n.value for n in pos.npris if name_key(n.owner) == name_key(owner)), Fraction(0))

    owned = {s: held(at_eff, s) for s in sellers}
    owned_now = {s: held(no_buyer, s) for s in sellers}
    total, total_now = sum(owned.values(), Fraction(0)), sum(owned_now.values(), Fraction(0))
    value, problem = interest_of(asset)
    result: dict[str, Any] = {"as_of": as_of, "tract": tract, "depth": depth, "estate": estate,
                              "owned": fmt(total), "owned_today": fmt(total_now),
                              "owned_by_seller": {s: fmt(v) for s, v in owned.items()}}
    if pos_of(at_eff) is None:
        specs.append(TaskSpec("NO_TITLE", f"The takeoff has no position for {tract}/{depth}.", "verify", aid, ref=f"ledger:{tract}"))
        return {**result, "status": "NO_TITLE", "summary": f"no ownership for {tract} in the takeoff"}, specs, expected
    sold = total if value == "all" else value
    result["sold"] = fmt(sold) if isinstance(sold, Fraction) else None
    status = "OK"
    if total == 0:
        status = "NOT_IN_CHAIN"
        specs.append(TaskSpec("SELLER_NOT_IN_CHAIN", f"{' and '.join(sellers)} hold no {'minerals' if estate == 'MI' else 'NPRI'} "
                              f"in {tract}/{depth} on {as_of}.", "verify", aid, ref=f"owned:{tract}"))
    elif isinstance(sold, Fraction) and sold > total:
        status = "SHORTFALL"
        specs.append(TaskSpec("SELLER_SHORTFALL", f"Sold {fmt(sold)} ({to_decimal(sold)}) but held {fmt(total)} ({to_decimal(total)}) "
                              f"on {as_of}; short {fmt(sold - total)}.", "verify", aid, ref=f"owned:{tract}"))
    if total_now < total:
        seller_keys = {name_key(s) for s in sellers}
        after = [e.instrument for e in later if e.grantor and name_key(e.grantor) in seller_keys and e.tract == tract]
        status = "CONVEYED_AFTER" if status == "OK" else status
        specs.append(TaskSpec("SELLER_CONVEYED_AFTER_EFFECTIVE", f"Seller held {fmt(total)} on {as_of} but {fmt(total_now)} today, "
                              f"after {', '.join(after) or 'later instruments'}.", "verify", aid, ref=f"after:{tract}"))
    gross = asset.get("gross_acres")
    if gross and isinstance(sold, Fraction):
        try:
            g = parse_fraction(str(gross))
            result["nma_owned"] = to_decimal(total * g, 4)
            result["nma_sold"] = to_decimal(sold * g, 4)
            if asset.get("nma") and value == "all" and to_decimal(total * g, 4) != to_decimal(parse_fraction(str(asset["nma"])), 4):
                specs.append(TaskSpec("NMA_DIFFERS", f"Title shows {to_decimal(total * g, 4)} NMA; the deal says {asset['nma']}.",
                                      "verify", aid, ref="nma"))
        except FractionError:
            pass
    if estate == "NPRI":
        pos = pos_of(at_eff)
        kinds = sorted({n.kind for n in pos.npris if name_key(n.owner) in {name_key(s) for s in sellers}})
        result["npri_kinds"] = kinds
        if asset.get("npri_kind") and kinds and kinds != [asset["npri_kind"]]:
            specs.append(TaskSpec("NPRI_KIND_DIFFERS", f"The deal says {asset['npri_kind']}; the takeoff carries {', '.join(kinds)}.",
                                  "verify", aid, ref="npri_kind"))
    else:
        pos = pos_of(at_eff)
        groups = sorted({g for lot in pos.lots if name_key(lot.owner) in {name_key(s) for s in sellers} for g in lot.burdens})
        if groups:
            result["burdens"] = [f"{n.owner}: {n.kind} NPRI {fmt(n.value)} ({n.source})" for n in pos.npris if n.group in groups]
            notes.append("The seller's minerals carry NPRI burdens: " + "; ".join(result["burdens"]))

    chain = _chain(events, sellers, tract, "grantor", "grantees", "tract", as_of)
    result["chain"] = sorted(chain)
    burdens = burden_refs(pos_of(at_eff), sellers)
    flag_specs, flag_notes = _flag_specs(no_buyer.flags, tract, depth, chain, sellers, aid, "ledger", burdens)
    specs += flag_specs
    notes += flag_notes
    _runsheet_specs(asset, specs, notes)

    if isinstance(sold, Fraction) and total and record.get("effective_date"):
        share = min(sold, total)
        amounts = {s: share * owned[s] / total for s in sellers}
        for w in asset.get("wells") or []:
            if not w.get("project"):
                continue
            try:
                rows, deck_flags, well = _post_closing_deck(w["project"], record, asset, amounts, estate)
            except (OSError, ValueError, KeyError, FractionError) as e:
                specs.append(TaskSpec("TITLE_FILE_ERROR", f"Well project {w['project']}: {e}", "verify", aid, ref=f"file:{w['project']}"))
                continue
            dspecs, dnotes = _flag_specs(deck_flags, tract, depth, chain | {f"Deal {record['deal_id']} (pending)"},
                                         sellers + [record["buyer"]], aid, f"deck {well}", burdens)
            specs += dspecs
            notes += dnotes
            for typ, v in rows:
                expected.append(_expected(asset, w, typ, v, f"post-closing deck: {Path(w['project']).name}"))
            if not rows:
                notes.append(f"{well}: the buyer has no revenue row in the post-closing deck; check the project's leases")
    result["status"] = status
    result["notes"] = notes
    result["summary"] = {
        "OK": f"seller held {fmt(total)}; sold {result.get('sold')}",
        "SHORTFALL": f"seller held {fmt(total)} but sold {result.get('sold')}",
        "NOT_IN_CHAIN": "seller holds nothing of record here",
        "CONVEYED_AFTER": f"seller conveyed part of the interest after {as_of}",
    }[status]
    return result, specs, expected


def verify_leasehold(record: dict[str, Any], asset: dict[str, Any]) -> tuple[dict[str, Any], list[TaskSpec], list[dict]]:
    aid, kind = asset["id"], asset["kind"]
    specs: list[TaskSpec] = []
    notes: list[str] = []
    expected: list[dict] = []
    path = asset["title"].get("lease_chain")
    if not path:
        specs.append(TaskSpec("NO_TITLE_FILES", "No lease chain for this asset (title.lease_chain).", "verify", aid))
        return {"status": "NO_TITLE", "summary": "no lease chain to verify against"}, specs, expected
    if not Path(path).exists():
        specs.append(TaskSpec("TITLE_FILE_MISSING", f"Lease chain {path} doesn't exist.", "verify", aid, ref=f"file:{path}"))
        return {"status": "NO_TITLE", "summary": f"lease chain {path} not found"}, specs, expected
    try:
        events: list[LeaseEvent] = load_lease_events(path)
        depths = _load_json(asset["title"].get("lease_depths") or asset["title"].get("depths"))
    except (ValueError, FractionError) as e:
        specs.append(TaskSpec("TITLE_FILE_ERROR", f"Lease chain {Path(path).name}: {e}", "verify", aid, ref=f"file:{path}"))
        return {"status": "ERROR", "summary": str(e)}, specs, expected

    as_of = record.get("effective_date")
    sellers = _seller_names(record, asset)
    seller_keys = {name_key(s) for s in sellers}
    buyer_key = name_key(record["buyer"])
    depth = asset.get("depth") or ALL_DEPTHS
    at_eff = replay_leasehold(events, depths, as_of)
    no_buyer = replay_leasehold([e for e in events if not (as_of and e.recorded > as_of and any(name_key(g) == buyer_key for g in e.assignees))],
                                depths)
    leases = lease_ids(asset)
    result: dict[str, Any] = {"as_of": as_of, "leases": {}, "wells": []}
    status = "OK"
    if kind == "wellbore" and not asset.get("wells"):
        specs.append(TaskSpec("WELLBORE_NO_WELLS", "A wellbore buy with no wells listed: nothing to compare.",
                              "verify", aid, ref="wells"))
        return {**result, "status": "NO_TITLE", "summary": "no wells listed"}, specs, expected
    if not leases:
        specs.append(TaskSpec("LEASE_NOT_NAMED", "No lease named for this asset (lease.id, or each well's participation).",
                              "verify", aid, ref="lease"))
        return {**result, "status": "NO_TITLE", "summary": "no lease named"}, specs, expected

    def per_lease(r, lease):
        lp = position_for(r, lease, depth)
        if lp is None:
            return None
        nris = [lp.nri(s) for s in sellers]
        return {"wi": sum((lp.wi(s) for s in sellers), Fraction(0)),
                "nri": None if any(n is None for n in nris) else sum(nris, Fraction(0)),
                "orri": sum((lp.orri_held(s) for s in sellers), Fraction(0)), "royalty": lp.royalty}

    held = {}
    for lease in leases:
        h = per_lease(at_eff, lease)
        if h is None:
            specs.append(TaskSpec("NO_TITLE", f"The lease chain has no lease {lease}.", "verify", aid, ref=f"lease:{lease}"))
            status = "NO_TITLE"
            continue
        held[lease] = h
        now = per_lease(no_buyer, lease)
        result["leases"][lease] = {k: fmt(v) if isinstance(v, Fraction) else v for k, v in h.items()}
        if now and (now["wi"] < h["wi"] or now["orri"] < h["orri"]):
            after = [e.instrument for e in events if as_of and e.recorded > as_of and e.lease == lease
                     and e.assignor and name_key(e.assignor) in seller_keys]
            specs.append(TaskSpec("SELLER_CONVEYED_AFTER_EFFECTIVE", f"Lease {lease}: seller held WI {fmt(h['wi'])} on {as_of} but "
                                  f"{fmt(now['wi'])} today, after {', '.join(after) or 'later instruments'}.", "verify", aid, ref=f"after:{lease}"))
            status = "CONVEYED_AFTER" if status == "OK" else status
    if status == "NO_TITLE":
        return {**result, "status": status, "summary": "a lease isn't in the chain"}, specs, expected

    def compare(label: str, owned_wi: Fraction, owned_nri: Fraction | None, ref: str) -> tuple[Fraction, Fraction | None] | None:
        nonlocal status
        if owned_wi == 0:
            specs.append(TaskSpec("SELLER_NOT_IN_CHAIN", f"{label}: {' and '.join(sellers)} hold no working interest on {as_of}.",
                                  "verify", aid, ref=f"owned:{ref}"))
            status = "NOT_IN_CHAIN"
            return None
        sold_wi = parse_fraction(str(asset["wi"]))
        if sold_wi > owned_wi:
            specs.append(TaskSpec("SELLER_SHORTFALL", f"{label}: sold WI {fmt(sold_wi)} but held {fmt(owned_wi)} on {as_of}.",
                                  "verify", aid, ref=f"owned:{ref}"))
            status = "SHORTFALL" if status == "OK" else status
        share = min(sold_wi, owned_wi)
        if owned_nri is None:
            notes.append(f"{label}: NRI not computed because a lease royalty is missing (ROYALTY_MISSING)")
            return share, None
        nri_bought = owned_nri * share / owned_wi
        if asset.get("nri"):
            represented = parse_fraction(str(asset["nri"]))
            if nri_bought < represented:
                specs.append(TaskSpec("NRI_SHORTFALL", f"{label}: title gives the buyer NRI {fmt(nri_bought)} ({to_decimal(nri_bought)}); "
                                      f"the seller represented {fmt(represented)} ({to_decimal(represented)}).", "verify", aid, ref=f"nri:{ref}"))
                status = "SHORTFALL" if status == "OK" else status
            elif nri_bought > represented:
                notes.append(f"{label}: title gives NRI {to_decimal(nri_bought)}, more than the {to_decimal(represented)} represented")
        return share, nri_bought

    if kind == "orri":
        lease = leases[0]
        owned = held[lease]["orri"]
        value, _ = interest_of(asset)
        sold = owned if value == "all" else value
        result.update({"owned": fmt(owned), "sold": fmt(sold) if isinstance(sold, Fraction) else None})
        if owned == 0:
            specs.append(TaskSpec("SELLER_NOT_IN_CHAIN", f"Lease {lease}: seller holds no ORRI on {as_of}.", "verify", aid, ref=f"owned:{lease}"))
            status = "NOT_IN_CHAIN"
        elif isinstance(sold, Fraction) and sold > owned:
            specs.append(TaskSpec("SELLER_SHORTFALL", f"Lease {lease}: sold ORRI {fmt(sold)} of 8/8 but held {fmt(owned)}.",
                                  "verify", aid, ref=f"owned:{lease}"))
            status = "SHORTFALL"
        if isinstance(sold, Fraction) and owned:
            for w in asset.get("wells") or []:
                factor = participation(w["participation"]).get(lease, Fraction(0)) if w.get("participation") else Fraction(1)
                if factor:
                    expected.append(_expected(asset, w, "ORRI", min(sold, owned) * factor, f"lease chain: {Path(path).name}"))
    elif kind == "leasehold":
        lease = leases[0]
        h = held[lease]
        got = compare(f"Lease {lease}", h["wi"], h["nri"], lease)
        result.update({"owned_wi": fmt(h["wi"]), "owned_nri": fmt(h["nri"]) if h["nri"] is not None else None,
                       "sold_wi": str(asset.get("wi"))})
        if got and got[1] is not None:
            result["buyer_nri"] = fmt(got[1])
            for w in asset.get("wells") or []:
                factor = participation(w["participation"]).get(lease, Fraction(0)) if w.get("participation") else Fraction(1)
                if factor and got[1] is not None:
                    expected.append(_expected(asset, w, "NRI", got[1] * factor, f"lease chain: {Path(path).name}"))
    else:  # wellbore: interests are of the well, built from each lease's share
        for w in asset.get("wells") or []:
            factors = participation(w["participation"]) if w.get("participation") else {leases[0]: Fraction(1)}
            wi = sum((f * held[lse]["wi"] for lse, f in factors.items() if lse in held), Fraction(0))
            parts = [held[lse]["nri"] for lse in factors if lse in held]
            nri = None if any(n is None for n in parts) else \
                sum((f * held[lse]["nri"] for lse, f in factors.items() if lse in held), Fraction(0))
            label = f"Well {w.get('name') or w.get('api')}"
            got = compare(label, wi, nri, w.get("api") or w.get("name") or "well")
            result["wells"].append({"well": w.get("name") or w.get("api"), "owned_wi": fmt(wi),
                                    "owned_nri": fmt(nri) if nri is not None else None,
                                    "factors": {k: fmt(v) for k, v in factors.items()}})
            if got and got[1] is not None:
                expected.append(_expected(asset, w, "NRI", got[1], f"lease chain: {Path(path).name}"))

    for lease in leases:
        chain = _chain(events, sellers, lease, "assignor", "assignees", "lease", as_of)
        result.setdefault("chain", {})[lease] = sorted(chain)
        lp = position_for(at_eff, lease, depth)
        s, n = _flag_specs(no_buyer.flags, lease, depth, chain, sellers, aid, "lease chain",
                           burden_refs(lp.position, sellers) if lp else set())
        specs += s
        notes += n
    _runsheet_specs(asset, specs, notes)
    result["status"] = status
    result["notes"] = notes
    result["summary"] = {"OK": "seller holds what it sold", "SHORTFALL": "seller holds less than it sold or represented",
                         "NOT_IN_CHAIN": "seller holds nothing of record in the lease",
                         "CONVEYED_AFTER": f"seller assigned part of the interest after {as_of}"}[status]
    return result, specs, expected


OWNERSHIP_CODES = ("SELLER_SHORTFALL", "NRI_SHORTFALL", "SELLER_NOT_IN_CHAIN", "SELLER_CONVEYED_AFTER_EFFECTIVE")


def verify_asset(record: dict[str, Any], asset: dict[str, Any]) -> tuple[dict[str, Any], list[TaskSpec], list[dict]]:
    if asset["kind"] in ("mineral", "royalty"):
        result, specs, expected = verify_minerals(record, asset)
    else:
        result, specs, expected = verify_leasehold(record, asset)
    problems = [s.message for s in specs if s.code in OWNERSHIP_CODES]
    if len(problems) > 1:
        result["summary"] = "; ".join(problems)
    return result, specs, expected


def verify_deal(store: Store, rules: Rules, deal_id: str, actor: str, asset_ids: list[str] | None = None,
                today: date | None = None) -> dict[str, Any]:
    st = fold(store.events(deal_id))
    if not st.record.get("effective_date"):
        raise ValueError(f"{deal_id} has no effective date; fix the deal record first (ptk deal intake --reason ...)")
    out: dict[str, Any] = {"assets": {}}
    for asset in st.assets:
        if asset_ids and asset["id"] not in asset_ids:
            continue
        result, specs, expected = verify_asset(st.record, asset)
        tasks = sync(store, st, rules, actor, "verify", asset["id"], specs, today)
        if tasks["resolved"]:
            from .outbox import withdraw_stale
            withdraw_stale(store, deal_id)
        result["notes"] = result.get("notes", []) + [f"[{s.code}] {s.message}" for s in tasks["info"]]
        result["tasks"] = {k: v for k, v in tasks.items() if k != "info" and v}
        store.append(deal_id, "verify.completed", {"asset_id": asset["id"], "result": result}, actor)
        for exp in expected:
            have = st.expected.get(exp["key"])
            if not have or have.get("decimal") != exp["decimal"] or have.get("payor") != exp["payor"]:
                store.append(deal_id, "payment.expected", exp, actor)
        out["assets"][asset["id"]] = result
        st = fold(store.events(deal_id))
    out["summary"] = summary(st, rules, today)
    return out


__all__ = ["verify_deal", "verify_asset", "verify_minerals", "verify_leasehold"]
