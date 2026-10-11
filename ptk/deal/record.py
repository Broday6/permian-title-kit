"""The deal record acquisitions hands over, and how it's read from JSON or a spreadsheet.

A deal is one signed purchase. It can mix asset kinds and states: a mineral buy
in Reeves County, a lease and its horizontal well in Eddy County, and an ORRI
can all sit in one deal. Each asset carries what title follow-up needs:

    kind        mineral | royalty | orri | leasehold | wellbore
    state       TX | NM (the two run title very differently; checklists follow the state)
    interest    mineral/royalty: fraction of the whole estate in the tract ("1/4"), or "all"
                orri: fraction of 8/8 of the lease
    wi, nri     leasehold/wellbore: working and net revenue interest bought (of 8/8)
    title       the takeoff (minerals), lease_chain (leasehold), runsheet, images, depths
    wells       api, name, operator, payor, project (a ptk deck project for the well),
                participation (lease id -> weight, for a horizontal well across leases)

``load_deal`` checks structure only (ids, lists, kinds) and raises ``ValueError``
on a malformed file. Everything about the *content* (unreadable legal
descriptions, a wrong county, NMA that doesn't add up) is an intake task, so
it goes back to acquisitions instead of stopping the import.
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any

from ..dates import DateError, iso

ASSET_KINDS = ("mineral", "royalty", "orri", "leasehold", "wellbore")
STATES = ("TX", "NM")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_TITLE_PATHS = ("takeoff", "depths", "runsheet", "images", "lease_chain", "lease_depths")


def _abs(base: Path, value: str | None) -> str | None:
    if not value:
        return None
    p = Path(value)
    return str(p if p.is_absolute() else (base / p).resolve())


def normalize(raw: dict[str, Any], base: str | Path = ".") -> dict[str, Any]:
    """Validate the structure of a deal record and resolve its file paths against ``base``."""
    base = Path(base)
    deal_id = str(raw.get("deal_id") or "").strip()
    if not _ID.match(deal_id):
        raise ValueError(f"deal_id {deal_id!r}: use letters, digits, '-', '_' or '.' (up to 64)")
    sellers = raw.get("sellers") or []
    assets = raw.get("assets") or []
    if not isinstance(sellers, list) or not sellers:
        raise ValueError(f"{deal_id}: list at least one seller")
    if not isinstance(assets, list) or not assets:
        raise ValueError(f"{deal_id}: list at least one asset")
    out = {k: v for k, v in raw.items() if not k.startswith("_")}
    out["deal_id"] = deal_id
    out["name"] = str(raw.get("name") or deal_id)
    out["buyer"] = str(raw.get("buyer") or "").strip()
    if not out["buyer"]:
        raise ValueError(f"{deal_id}: name the buyer (the entity taking title)")
    for key in ("effective_date", "signed_date"):
        if raw.get(key):
            try:
                out[key] = iso(str(raw[key]))
            except DateError as e:
                raise ValueError(f"{deal_id}: {key}: {e}") from e
    out["price"] = None if raw.get("price") in (None, "") else str(raw["price"]).replace(",", "").replace("$", "")

    seen: set[str] = set()
    norm_sellers = []
    for i, s in enumerate(sellers, start=1):
        sid = str(s.get("id") or f"S{i}")
        if sid in seen:
            raise ValueError(f"{deal_id}: seller id {sid} is used twice")
        seen.add(sid)
        if not str(s.get("name") or "").strip():
            raise ValueError(f"{deal_id}: seller {sid} has no name")
        norm_sellers.append({**s, "id": sid, "name": str(s["name"]).strip(),
                             "do_not_contact": bool(s.get("do_not_contact", False)),
                             "channel": s.get("channel") or ("email" if s.get("email") else "mail")})
    out["sellers"] = norm_sellers

    seen = set()
    norm_assets = []
    for i, a in enumerate(assets, start=1):
        aid = str(a.get("id") or f"A{i}")
        if aid in seen:
            raise ValueError(f"{deal_id}: asset id {aid} is used twice")
        seen.add(aid)
        kind = str(a.get("kind") or "").strip().lower()
        if kind not in ASSET_KINDS:
            raise ValueError(f"{deal_id} asset {aid}: kind {kind!r} must be one of {', '.join(ASSET_KINDS)}")
        state = str(a.get("state") or "").strip().upper()
        if state not in STATES:
            raise ValueError(f"{deal_id} asset {aid}: state {state!r} must be TX or NM")
        title = {k: (_abs(base, v) if k in _TITLE_PATHS else v) for k, v in (a.get("title") or {}).items()}
        wells = []
        for w in a.get("wells") or []:
            wells.append({**w, "project": _abs(base, w.get("project"))} if w.get("project") else dict(w))
        seller_ids = a.get("sellers") or ([a["seller"]] if a.get("seller") else [norm_sellers[0]["id"]] if len(norm_sellers) == 1 else [])
        norm_assets.append({**a, "id": aid, "kind": kind, "state": state, "county": str(a.get("county") or "").strip(),
                            "depth": str(a.get("depth") or "ALL"), "sellers": [str(x) for x in seller_ids],
                            "title": title, "wells": wells, "operated": bool(a.get("operated", False)),
                            "land_status": (str(a["land_status"]).strip().lower() if a.get("land_status") else None)})
        norm_assets[-1].pop("seller", None)
    out["assets"] = norm_assets
    out["contacts"] = list(raw.get("contacts") or [])
    out["documents"] = [{**d, "file": _abs(base, d.get("file")) if d.get("file") else None}
                        for d in raw.get("documents") or []]
    return out


def load_deal(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    return normalize(json.loads(path.read_text(encoding="utf-8-sig")), path.parent)


DEAL_CSV_COLUMNS = [
    "deal_id", "deal_name", "buyer", "effective_date", "signed_date", "price", "agreement_file",
    "seller_name", "seller_email", "seller_phone", "seller_address", "seller_marital_status", "seller_spouse",
    "asset_kind", "state", "county", "tract", "legal_description", "gross_acres", "interest", "nma",
    "npri_kind", "lease_id", "wi", "nri", "land_status", "operated", "depth",
    "well_api", "well_name", "operator", "payor", "well_project", "takeoff", "lease_chain", "runsheet",
]


def load_deals_csv(path: str | Path) -> list[dict[str, Any]]:
    """Read a deal sheet: one row per asset, rows grouped into deals by ``deal_id``.

    Sellers are matched by name within a deal. One well per row; put more wells
    in a deal JSON. Paths are relative to the sheet.
    """
    path = Path(path)
    deals: dict[str, dict[str, Any]] = {}
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for n, row in enumerate(csv.DictReader(fh), start=2):
            row = {k: (v or "").strip() for k, v in row.items() if k}
            did = row.get("deal_id")
            if not did:
                raise ValueError(f"{path.name} row {n}: no deal_id")
            d = deals.setdefault(did, {"deal_id": did, "name": row.get("deal_name") or did, "buyer": row.get("buyer"),
                                       "effective_date": row.get("effective_date") or None,
                                       "signed_date": row.get("signed_date") or None,
                                       "price": row.get("price") or None, "sellers": [], "assets": [], "documents": []})
            if row.get("agreement_file") and not d["documents"]:
                d["documents"].append({"type": "purchase_agreement", "file": row["agreement_file"],
                                       "received": row.get("signed_date") or None})
            name = row.get("seller_name") or ""
            seller = next((s for s in d["sellers"] if s["name"].upper() == name.upper()), None)
            if seller is None:
                seller = {"id": f"S{len(d['sellers']) + 1}", "name": name, "email": row.get("seller_email"),
                          "phone": row.get("seller_phone"), "address": row.get("seller_address"),
                          "marital_status": row.get("seller_marital_status") or None,
                          "spouse": row.get("seller_spouse") or None}
                d["sellers"].append(seller)
            asset: dict[str, Any] = {
                "id": f"A{len(d['assets']) + 1}", "kind": row.get("asset_kind"), "state": row.get("state"),
                "county": row.get("county"), "tract": row.get("tract"), "legal_description": row.get("legal_description"),
                "gross_acres": row.get("gross_acres") or None, "interest": row.get("interest") or None,
                "nma": row.get("nma") or None, "npri_kind": row.get("npri_kind") or None,
                "wi": row.get("wi") or None, "nri": row.get("nri") or None,
                "land_status": row.get("land_status") or None, "depth": row.get("depth") or "ALL",
                "operated": row.get("operated", "").lower() in ("y", "yes", "true", "1"),
                "seller": seller["id"],
                "title": {k: row[k] for k in ("takeoff", "lease_chain", "runsheet") if row.get(k)},
                "wells": [],
            }
            if row.get("lease_id"):
                asset["lease"] = {"id": row["lease_id"]}
            if row.get("well_api") or row.get("well_name"):
                asset["wells"].append({"api": row.get("well_api"), "name": row.get("well_name"),
                                       "operator": row.get("operator"), "payor": row.get("payor") or row.get("operator"),
                                       "project": row.get("well_project") or None})
            d["assets"].append(asset)
    return [normalize(d, path.parent) for d in deals.values()]


def seller_by_id(record: dict[str, Any], sid: str) -> dict[str, Any] | None:
    return next((s for s in record["sellers"] if s["id"] == sid), None)


def asset_by_id(record: dict[str, Any], aid: str) -> dict[str, Any] | None:
    return next((a for a in record["assets"] if a["id"] == aid), None)


def lease_ids(asset: dict[str, Any]) -> list[str]:
    """Leases whose title an asset depends on.

    A lease or ORRI buy depends on its own lease only; a well that also crosses
    other leases just sets that lease's share of the well. A wellbore buy depends
    on every lease in its wells' participation (or its own lease, for a single-lease well).
    """
    ids: list[str] = []
    if (asset.get("lease") or {}).get("id"):
        ids.append(str(asset["lease"]["id"]))
    if asset.get("kind") == "wellbore":
        for w in asset.get("wells") or []:
            ids += [str(k) for k in (w.get("participation") or {})]
    return list(dict.fromkeys(ids))
