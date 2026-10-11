"""Stage 1, intake: check a signed deal before title work starts.

Runs the kit's parsers over the deal record so problems go back to
acquisitions on day one instead of surfacing at closing:

* every legal description is parsed (Texas survey/block/section or New Mexico
  PLSS) and, when map layers are given, checked against them (``mapcheck``);
* counties are matched to the right state and the county table;
* interests are read exactly; interest x gross acres must equal the stated NMA;
* New Mexico tracts need a land status (fee, State or federal), and married
  individual sellers need the spouse named (community property);
* seller contact details, the signed agreement, the price and well API numbers.

Each problem becomes an intake task (see rules.json, codes ``INTAKE_*``). The
state- and asset-specific checklists (Texas vs New Mexico, minerals vs leases
vs wells) are opened at the same time, so the whole job is visible at once.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from typing import Any

from .. import counties, legal_nm, legal_tx, names
from ..fracs import FractionError, fmt, parse_fraction, to_decimal
from .record import lease_ids, seller_by_id
from .rules import Rules, checklist_applies
from .state import fold, summary
from .store import Store
from .tasks import TaskSpec, sync

LAND_STATUSES = ("fee", "state", "federal", "university", "relinquishment_act", "free_royalty")
AGREEMENT_TYPES = ("purchase_agreement", "psa", "letter_of_intent", "loi", "offer_letter", "signed_conveyance", "assignment")
_API = {"TX": re.compile(r"^42-?\d{3}-?\d{5}(?:-?\d{2,4})?$"), "NM": re.compile(r"^30-?\d{3}-?\d{5}(?:-?\d{2,4})?$")}


def seller_kind(seller: dict[str, Any]) -> str:
    return seller.get("kind") or names.parse_name(seller["name"]).kind


def married_individual(seller: dict[str, Any]) -> bool:
    return seller_kind(seller) == "person" and str(seller.get("marital_status") or "").lower() == "married"


def interest_of(asset: dict[str, Any]) -> tuple[Fraction | str | None, str | None]:
    """The interest bought as an exact fraction, 'all', or None, plus a problem description."""
    raw = asset.get("interest")
    gross, nma = asset.get("gross_acres"), asset.get("nma")
    try:
        if raw not in (None, ""):
            if str(raw).strip().lower() in ("all", "all of seller's interest", "all of grantor's interest"):
                return "all", None
            value = parse_fraction(str(raw))
            if not 0 < value <= 1:
                return None, f"interest {raw} isn't between 0 and 1"
            return value, None
        if nma and gross:
            return parse_fraction(str(nma)) / parse_fraction(str(gross)), None
    except (FractionError, ZeroDivisionError) as e:
        return None, f"can't read the interest: {e}"
    return None, "no interest given (interest, or nma with gross_acres)"


def asset_facts(record: dict[str, Any], asset: dict[str, Any]) -> dict[str, Any]:
    """Facts checklist ``when`` conditions test."""
    facts: dict[str, Any] = {
        "land_status": asset.get("land_status") or ("fee" if asset["state"] == "TX" else None),
        "has_wells": bool(asset.get("wells")),
        "operated": bool(asset.get("operated")),
        "seller_married_individual": any(married_individual(s) for sid in asset["sellers"]
                                         if (s := seller_by_id(record, sid))),
        "survey_code": None,
    }
    if asset["state"] == "TX" and asset.get("legal_description"):
        facts["survey_code"] = legal_tx.parse(asset["legal_description"], county=asset.get("county")).survey_code
    return facts


def _legal(asset: dict[str, Any], add) -> dict[str, Any]:
    text = asset.get("legal_description") or ""
    aid = asset["id"]
    if not text:
        if asset["kind"] in ("mineral", "royalty"):
            add("INTAKE_LEGAL_UNREADABLE", "No legal description for this tract.", aid)
        return {}
    gross = asset.get("gross_acres")
    if asset["state"] == "TX":
        d = legal_tx.parse(text, county=asset.get("county") or None)
        for issue in d.issues:
            if issue.startswith("county ") and "isn't in the Permian county table" in issue:
                continue  # reported from the asset's county below
            code = "INTAKE_LEGAL_UNREADABLE" if issue in ("no county", "no section or survey number") else "INTAKE_LEGAL_REVIEW"
            add(code, f"Texas description: {issue}.", aid, ref=f"legal:{issue}")
        if d.county and asset.get("county") and d.county.upper() != asset["county"].upper():
            add("INTAKE_COUNTY_MISMATCH", f"Description says {d.county} County; the deal says {asset['county']}.", aid)
        if d.acres and gross:
            try:
                if abs(parse_fraction(d.acres) - parse_fraction(str(gross))) > parse_fraction(str(gross)) / 20:
                    add("INTAKE_ACRES_MISMATCH", f"Description states {d.acres} acres; the deal says {gross} gross acres.", aid)
            except FractionError:
                pass
        return {"state": "TX", "survey_keys": d.survey_keys(), "abstract_key": d.abstract_key(),
                "survey_code": d.survey_code, "issues": d.issues}
    d = legal_nm.parse(text)
    for issue in d.issues:
        if issue.startswith("meridian"):
            continue
        add("INTAKE_LEGAL_UNREADABLE", f"New Mexico description: {issue}.", aid, ref=f"legal:{issue}")
    if gross and d.sections and not any(s.lots for s in d.sections):
        area = sum((s.regular_area() for s in d.sections), Fraction(0)) * 640
        try:
            stated = parse_fraction(str(gross))
            if area and abs(area - stated) > stated / 20:
                add("INTAKE_ACRES_MISMATCH", f"Description covers {to_decimal(area, 2)} regular acres; the deal says {gross}.", aid)
        except FractionError:
            pass
    return {"state": "NM", "keys": d.keys(), "issues": d.issues}


def check_record(record: dict[str, Any], layers: dict | None = None, fields: dict | None = None
                 ) -> tuple[list[TaskSpec], dict[str, Any]]:
    """Intake problems for a normalized deal record, plus what was learned (parses, name searches)."""
    specs: list[TaskSpec] = []

    def add(code: str, message: str, asset_id: str | None = None, ref: str = "-", severity: str | None = None,
            party_name: str | None = None) -> None:
        specs.append(TaskSpec(code, message, "intake", asset_id, ref, force_severity=severity, party_name=party_name))

    info: dict[str, Any] = {"legal": {}, "names": {}, "interest": {}}
    if not record.get("effective_date"):
        add("INTAKE_EFFECTIVE_DATE", "The deal has no effective date.")
    if not record.get("price"):
        add("INTAKE_PRICE_MISSING", "The deal has no price.")
    else:
        for where, value in [("deal", record["price"])] + [(a["id"], a["price"]) for a in record["assets"] if a.get("price")]:
            try:
                Decimal(str(value))
            except InvalidOperation:
                add("INTAKE_PRICE_MISSING", f"Price {value!r} on {where} isn't a number.", ref=f"price:{where}")
    if not any(str(d.get("type", "")).lower() in AGREEMENT_TYPES for d in record.get("documents", [])):
        add("INTAKE_NO_AGREEMENT", "No signed purchase agreement, letter of intent or conveyance is listed in documents.")

    nm_sellers = {sid for a in record["assets"] if a["state"] == "NM" for sid in a["sellers"]}
    for s in record["sellers"]:
        info["names"][s["name"]] = names.search_variants(s["name"])
        if not (s.get("email") or s.get("address")):
            add("INTAKE_SELLER_CONTACT", f"{s['name']} has no email or mailing address.", ref=s["id"])
        if seller_kind(s) == "person":
            status = str(s.get("marital_status") or "").lower()
            if not status:
                add("INTAKE_MARITAL_STATUS", f"{s['name']}: marital status not given.", ref=s["id"],
                    severity="stop" if s["id"] in nm_sellers else None)
            elif status == "married" and not s.get("spouse"):
                add("INTAKE_SPOUSE_MISSING", f"{s['name']} is married but the spouse isn't named.", ref=s["id"],
                    severity="stop" if s["id"] in nm_sellers else "review")

    for a in record["assets"]:
        aid = a["id"]
        if not a["sellers"]:
            add("INTAKE_SELLER_REF", "The deal has several sellers; name this asset's seller (seller or sellers).", aid)
        for sid in a["sellers"]:
            if seller_by_id(record, sid) is None:
                add("INTAKE_SELLER_REF", f"Seller id {sid} isn't in the sellers list.", aid, ref=sid)
        county = a.get("county")
        if not county:
            add("INTAKE_COUNTY_MISMATCH", "No county.", aid)
        else:
            try:
                c = counties.lookup(county)
                if c.state != a["state"]:
                    add("INTAKE_COUNTY_MISMATCH", f"{c.name} County is in {c.state}, but the asset says {a['state']}.", aid)
            except KeyError:
                add("INTAKE_COUNTY_UNKNOWN", f"{county} County isn't in the county table.", aid)
        info["legal"][aid] = _legal(a, add)

        if a["state"] == "NM":
            if not a.get("land_status"):
                add("INTAKE_LAND_STATUS", "New Mexico tract with no land status (fee, state or federal).", aid)
        if a.get("land_status") and a["land_status"] not in LAND_STATUSES:
            add("INTAKE_LAND_STATUS", f"Land status {a['land_status']!r} must be one of {', '.join(LAND_STATUSES)}.", aid)

        kind = a["kind"]
        if kind in ("mineral", "royalty", "orri"):
            value, problem = interest_of(a)
            if problem:
                add("INTAKE_INTEREST", problem.capitalize() + ".", aid)
            elif isinstance(value, Fraction):
                info["interest"][aid] = fmt(value)
                if a.get("nma") and a.get("gross_acres") and a.get("interest"):
                    try:
                        product = value * parse_fraction(str(a["gross_acres"]))
                        if to_decimal(product, 4) != to_decimal(parse_fraction(str(a["nma"])), 4):
                            add("INTAKE_NMA_MISMATCH", f"{fmt(value)} x {a['gross_acres']} acres = {to_decimal(product, 4)} NMA, "
                                                       f"but the deal says {a['nma']}.", aid)
                    except FractionError as e:
                        add("INTAKE_INTEREST", f"Can't read acres or NMA: {e}.", aid)
            if kind in ("mineral", "royalty") and not a.get("tract"):
                add("INTAKE_LEGAL_UNREADABLE", "No tract id (the takeoff's tract name for this land).", aid)
            if kind == "royalty" and a.get("npri_kind") not in ("fixed", "floating"):
                add("INTAKE_INTEREST", "A royalty buy needs npri_kind: fixed or floating, as the deal describes it.", aid)
            if kind == "orri" and not lease_ids(a):
                add("INTAKE_INTEREST", "An ORRI buy needs the lease it burdens (lease.id).", aid)
        else:
            try:
                wi = parse_fraction(str(a.get("wi") or ""))
                nri = parse_fraction(str(a["nri"])) if a.get("nri") else None
                if not 0 < wi <= 1:
                    add("INTAKE_INTEREST", f"WI {a.get('wi')} isn't between 0 and 1.", aid)
                elif nri is not None and not 0 < nri <= wi:
                    add("INTAKE_INTEREST", f"NRI {a.get('nri')} must be more than 0 and no more than the WI {a.get('wi')}.", aid)
                info["interest"][aid] = {"wi": fmt(wi), "nri": fmt(nri) if nri is not None else None}
            except FractionError as e:
                add("INTAKE_INTEREST", f"Can't read WI/NRI: {e}.", aid)
            if not lease_ids(a):
                add("INTAKE_INTEREST", "Name the lease (lease.id), or the leases in each well's participation.", aid)
            if kind == "wellbore" and not a.get("wells"):
                add("INTAKE_INTEREST", "A wellbore buy needs its wells listed.", aid)
        for w in a.get("wells") or []:
            api = str(w.get("api") or "").strip()
            if api and not _API[a["state"]].match(api):
                add("INTAKE_WELL_API", f"Well {w.get('name') or api}: API {api} doesn't look like a {a['state']} number.", aid, ref=api)

    if layers:
        from ..mapcheck import check_tract
        for a in record["assets"]:
            if not a.get("legal_description"):
                continue
            row = {"tract": a.get("tract") or a["id"], "state": a["state"], "county": a.get("county", ""),
                   "legal_description": a["legal_description"], "stated_acres": str(a.get("gross_acres") or "")}
            r = check_tract(row, layers, fields or {})
            info["legal"].setdefault(a["id"], {})["mapcheck"] = r.status
            if r.status != "OK":
                add("INTAKE_MAPCHECK", f"Map check {r.status}: " + "; ".join(r.notes), a["id"])
    return specs, info


def checklist_specs(record: dict[str, Any], rules: Rules) -> list[TaskSpec]:
    specs: list[TaskSpec] = []
    per_deal_done: set[str] = set()
    same_text: set[tuple[str, str]] = set()  # two assets on one tract need the county run once, not twice
    for a in record["assets"]:
        facts = asset_facts(record, a)
        for item in rules.checklists():
            if not checklist_applies(item, a["state"], a["kind"], facts):
                continue
            per_deal = item.get("per") == "deal"
            if per_deal and item["id"] in per_deal_done:
                continue
            per_deal_done.add(item["id"])
            values = defaultdict(str, {"county": a.get("county", ""), "tract": a.get("tract") or a.get("legal_description") or a["id"],
                                       "state": a["state"], "asset": a["id"]})
            text = item["requirement"].format_map(values)
            if item.get("authority"):
                text += f" ({item['authority']})"
            if (item["id"], text) in same_text:
                continue
            same_text.add((item["id"], text))
            specs.append(TaskSpec(item["id"], text, "checklist", None if per_deal else a["id"], ref=item["id"],
                                  party=item.get("party", "landman"), stage=item.get("stage", "curative"),
                                  requirement=text, template=item.get("template"),
                                  requires_attorney=bool(item.get("requires_attorney", False)),
                                  due_days=int(item.get("due_days", 14)), force_severity=item.get("severity", "review")))
    return specs


def intake_deal(store: Store, rules: Rules, record: dict[str, Any], actor: str, reason: str | None = None,
                layers: dict | None = None, fields: dict | None = None, today: date | None = None) -> dict[str, Any]:
    """Create (or amend, with a reason) a deal, run intake checks, and open intake and checklist tasks."""
    deal_id = record["deal_id"]
    exists = store.exists(deal_id)
    if exists and not reason:
        raise ValueError(f"deal {deal_id} already exists: pass --reason to amend it")
    payload: dict[str, Any] = {"record": record}
    if reason:
        payload["reason"] = reason
    store.append(deal_id, "deal.amended" if exists else "deal.created", payload, actor)
    problems, info = check_record(record, layers, fields)
    store.append(deal_id, "intake.checked", {"problems": sorted({p.code for p in problems}), **info}, actor)
    st = fold(store.events(deal_id))
    scopes = {None, *(a["id"] for a in record["assets"]),
              *(t.asset_id for t in st.tasks.values() if t.source in ("intake", "checklist"))}
    result: dict[str, list] = {"opened": [], "resolved": [], "reopened": []}
    checklist = checklist_specs(record, rules)
    for source, specs in (("intake", problems), ("checklist", checklist)):
        for scope in sorted(scopes, key=lambda s: s or ""):
            r = sync(store, st, rules, actor, source, scope, [s for s in specs if s.asset_id == scope], today)
            for k in result:
                result[k] += r[k]
    # An asset the amendment removed takes its title tasks with it (resolved, with a note).
    current = {a["id"] for a in record["assets"]}
    gone = {t.asset_id for t in st.tasks.values() if t.source == "verify" and t.asset_id and t.asset_id not in current}
    for aid in sorted(gone):
        result["resolved"] += sync(store, st, rules, actor, "verify", aid, [], today)["resolved"]
    st = fold(store.events(deal_id))
    return {"summary": summary(st, rules, today), "tasks": result, "problems": [p.code for p in problems]}
