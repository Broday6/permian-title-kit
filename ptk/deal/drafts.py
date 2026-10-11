"""Stage 3 and 4 paperwork: curative drafts and the closing package.

Everything here is a draft for a person: legal instruments carry a banner
saying they're for attorney review, and every fact the program doesn't know is
left as a visible ``[bracketed blank]``, never guessed. Templates live in
``templates/`` as Markdown with ``${name}`` fields; a template that asks for a
field the program can't supply raises an error instead of printing a hole.

The package (``build_package``) groups the deal's assets into one conveyance
per state, form and seller group, because Texas and New Mexico use different
venue, joinder and acknowledgment forms and leases are assigned on a
different form than minerals are deeded:

* mineral and royalty assets: a Mineral and Royalty Deed;
* lease and wellbore assets: an Assignment and Bill of Sale;
* ORRI assets: an Assignment of Overriding Royalty Interest.

In New Mexico, a married individual's spouse is named as a grantor and signs
(if community property, NMSA 40-3-13). Each package also has Exhibit A (exact
interests in words and numerals), a closing statement with any title
shortfall shown as a *proposed* adjustment, a closing checklist, and a
manifest of SHA-256 hashes so the signed version can be matched to its draft.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
from datetime import date
from fractions import Fraction
from pathlib import Path
from string import Template
from typing import Any

from ..fracs import FractionError, fmt, fraction_words, parse_fraction, to_decimal
from .intake import interest_of, married_individual, seller_kind
from .record import seller_by_id
from .rules import Rules
from .state import DealState, Task, fold, funds_gate
from .store import Store

TEMPLATES = Path(__file__).with_name("templates")
STATE_NAME = {"TX": "Texas", "NM": "New Mexico"}
INSTRUMENT_TEMPLATES = ("affidavit_of_heirship", "ratification", "spousal_ratification", "correction_deed",
                        "release", "affidavit_of_identity")
BANNER = "**DRAFT FOR ATTORNEY REVIEW. NOT FOR SIGNATURE.** Prepared by ptk from the title file; every [bracketed] item must be completed or confirmed."
_FIELD = re.compile(r"\$\{(\w+)\}")
_BLANK = re.compile(r"\[[^\]\n]{2,}\]")


def render(name: str, values: dict[str, Any]) -> str:
    text = (TEMPLATES / f"{name}.md").read_text(encoding="utf-8")
    missing = sorted(set(_FIELD.findall(text)) - set(values))
    if missing:
        raise ValueError(f"template {name} needs: {', '.join(missing)}")
    return Template(text).substitute({k: str(v) for k, v in values.items()})


def render_letter(name: str, values: dict[str, Any]) -> tuple[str, str]:
    """Letters start with 'Subject: ...'; returns (subject, body)."""
    text = render(name, values)
    first, _, rest = text.partition("\n")
    if not first.startswith("Subject: "):
        raise ValueError(f"letter template {name} must start with 'Subject: '")
    return first[len("Subject: "):].strip(), rest.lstrip("\n")


def blanks(text: str) -> list[str]:
    """Bracketed items a person still has to fill in or confirm."""
    return _BLANK.findall(text)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---- pieces ----------------------------------------------------------------

def venue(state: str, counties: list[str]) -> str:
    county = " and ".join(c.upper() for c in counties) or "[COUNTY]"
    if state == "TX":
        return f"STATE OF TEXAS §  \n§ KNOW ALL PERSONS BY THESE PRESENTS:  \nCOUNTY OF {county} §"
    return f"STATE OF NEW MEXICO )  \n) ss.  \nCOUNTY OF {county} )"


def acknowledgment(state: str, name: str, entity: bool) -> str:
    sname = STATE_NAME[state]
    head = f"STATE OF {sname.upper()} {'§' if state == 'TX' else ')'}  \nCOUNTY OF ____________ {'§' if state == 'TX' else ')'}"
    if entity:
        who = f"[name of signer], [title] of {name}, on behalf of {name}"
    else:
        who = name
    return (f"{head}\n\nThis instrument was acknowledged before me on ____________, 20__, by {who}.\n\n"
            f"______________________________  \nNotary Public, State of {sname}")


def signature(name: str, entity: bool) -> str:
    if entity:
        return f"**{name}**\n\nBy: ______________________________  \nName: [name of signer]  \nTitle: [title]"
    return f"______________________________  \n{name}"


def grantor_parties(record: dict[str, Any], seller_ids: list[str], state: str) -> list[tuple[str, bool]]:
    """(name, is_entity) for everyone who must sign; New Mexico spouses of married individuals included."""
    out: list[tuple[str, bool]] = []
    for sid in seller_ids:
        s = seller_by_id(record, sid)
        if s is None:
            continue
        entity = seller_kind(s) not in ("person",)
        out.append((s["name"], entity))
        if state == "NM" and married_individual(s):
            out.append((s.get("spouse") or "[name of spouse]", False))
    return out


def grantors_clause(record: dict[str, Any], seller_ids: list[str], state: str) -> str:
    parts = []
    for sid in seller_ids:
        s = seller_by_id(record, sid)
        if s is None:
            continue
        kind = seller_kind(s)
        if kind == "person":
            status = str(s.get("marital_status") or "").lower()
            if state == "NM" and status == "married":
                parts.append(f"{s['name']} and spouse, {s.get('spouse') or '[name of spouse]'}")
            elif state == "TX" and status == "married":
                parts.append(f"{s['name']}, [a married person dealing with separate property / joined by spouse "
                             f"{s.get('spouse') or '[name]'} if homestead]")
            elif status:
                parts.append(f"{s['name']}, a {status} person")
            else:
                parts.append(f"{s['name']}, [marital status]")
        else:
            parts.append(f"{s['name']}, [entity type and state of organization]")
    return "; ".join(parts) or "[Grantor]"


def interest_text(asset: dict[str, Any], verified: dict[str, Any] | None) -> str:
    kind = asset["kind"]
    value, _ = interest_of(asset)

    def words(v: Fraction) -> str:
        w = fraction_words(v)
        return f"{w} ({fmt(v)})" if w else f"{fmt(v)} ({to_decimal(v)})"

    if kind == "mineral":
        if value == "all":
            shown = f"; title shows {verified['owned']}" if verified and verified.get("owned") else ""
            return f"all of Grantor's right, title and interest in and to the oil, gas and other minerals{shown}"
        if isinstance(value, Fraction):
            nma = ""
            if asset.get("gross_acres"):
                try:
                    nma = f", being {to_decimal(value * parse_fraction(str(asset['gross_acres'])), 4)} net mineral acres"
                except FractionError:
                    nma = ""
            return f"an undivided {words(value)} interest in and to the oil, gas and other minerals{nma}"
    if kind == "royalty" and isinstance(value, Fraction):
        basis = "of all oil, gas and other minerals produced" if asset.get("npri_kind") == "fixed" else \
            "of the royalty reserved in any oil, gas and mineral lease"
        return (f"an undivided non-participating royalty interest equal to {words(value)} {basis} "
                f"[attorney to confirm fixed vs floating wording against the instruments of record]")
    if kind == "orri" and isinstance(value, Fraction):
        return (f"an overriding royalty interest equal to {words(value)} of 8/8 of production under the lease "
                f"{asset.get('lease', {}).get('id', '[lease]')}, [proportionately reduced: attorney to confirm]")
    if kind in ("leasehold", "wellbore"):
        try:
            wi = parse_fraction(str(asset.get("wi")))
        except FractionError:
            return "[working interest]"
        nri = f", delivering a net revenue interest of {asset['nri']} ({to_decimal(parse_fraction(str(asset['nri'])))})" if asset.get("nri") else ""
        scope = "insofar as it covers the lands and depths described"
        if kind == "wellbore":
            names = ", ".join(f"{w.get('name')} (API {w.get('api') or '[API]'})" for w in asset.get("wells") or [])
            scope = f"limited to the wellbore(s) of {names or '[wells]'} [depth limitation: attorney to confirm]"
        return f"{words(wi)} of the working interest in the lease(s) described, {scope}{nri}"
    return "[interest conveyed]"


def exhibit(st: DealState, assets: list[dict[str, Any]], title: str) -> str:
    sellers = "; ".join(s["name"] for s in st.record["sellers"] if any(s["id"] in a["sellers"] for a in assets))
    lines = ["## Exhibit A", "",
             f"Attached to and made part of the {title} from {sellers} to {st.record['buyer']}, "
             f"effective {st.record.get('effective_date') or '[date]'}.", ""]
    for a in assets:
        v = st.verify.get(a["id"], {}).get("result")
        lines.append(f"### {a['id']}: {a.get('tract') or a.get('lease', {}).get('id') or a['kind']}, "
                     f"{a.get('county')} County, {STATE_NAME[a['state']]}")
        lines.append("")
        lines.append(f"**Lands:** {a.get('legal_description') or '[legal description]'}  ")
        if a.get("gross_acres"):
            lines.append(f"**Gross acres:** {a['gross_acres']}  ")
        lines.append(f"**Interest conveyed:** {interest_text(a, v)}  ")
        if v and v.get("status") not in (None, "OK"):
            lines.append(f"**Title note:** [{v.get('summary')}; attorney to confirm the interest stated before signing]  ")
        lines.append(f"**Depths:** {'all depths' if (a.get('depth') or 'ALL') == 'ALL' else a['depth']}  ")
        lease = a.get("lease") or {}
        if lease:
            ref = ", ".join(f"{k}: {v}" for k, v in lease.items() if k != "id")
            lines.append(f"**Lease:** {lease.get('id')}{(' (' + ref + ')') if ref else ''}  ")
        if a.get("wells"):
            lines.append("**Wells:** " + "; ".join(f"{w.get('name') or '[name]'} (API {w.get('api') or '[API]'})"
                                                  for w in a["wells"]) + "  ")
        lines.append("")
    return "\n".join(lines)


def _form(kind: str) -> tuple[str, str]:
    if kind in ("mineral", "royalty"):
        return "mineral_deed", "MINERAL AND ROYALTY DEED"
    if kind == "orri":
        return "assignment", "ASSIGNMENT OF OVERRIDING ROYALTY INTEREST"
    return "assignment", "ASSIGNMENT AND BILL OF SALE"


def conveyance_groups(st: DealState) -> list[dict[str, Any]]:
    groups: dict[tuple, dict[str, Any]] = {}
    for a in st.assets:
        template, title = _form(a["kind"])
        key = (a["state"], template, title, tuple(sorted(a["sellers"])))
        g = groups.setdefault(key, {"state": a["state"], "template": template, "title": title,
                                    "sellers": list(key[3]), "assets": []})
        g["assets"].append(a)
    return list(groups.values())


def render_conveyance(st: DealState, group: dict[str, Any]) -> str:
    state = group["state"]
    counties = sorted({a["county"] for a in group["assets"] if a.get("county")})
    parties = grantor_parties(st.record, group["sellers"], state)
    addresses = "; ".join(s.get("address") or "[address]" for sid in group["sellers"] if (s := seller_by_id(st.record, sid)))
    regulatory = [f"- {t.requirement}" for t in st.tasks.values()
                  if t.stage == "record" and t.source == "checklist" and t.asset_id in {a["id"] for a in group["assets"]}]
    values = {
        "banner": BANNER,
        "title": group["title"],
        "venue": venue(state, counties),
        "effective_date": st.record.get("effective_date") or "[effective date]",
        "grantors": grantors_clause(st.record, group["sellers"], state),
        "grantor_address": addresses or "[address]",
        "buyer": st.record["buyer"],
        "buyer_address": st.record.get("buyer_address") or "[buyer address]",
        "warranty": ("Grantor binds itself and its heirs, successors and assigns to warrant and forever defend the "
                     "Interests to Grantee against every person lawfully claiming them or any part of them by, through "
                     "or under Grantor, but not otherwise. [Attorney to confirm special or general warranty.]"),
        "regulatory": ("**Filings after recording:**\n\n" + "\n".join(regulatory)) if regulatory else "",
        "signatures": "\n\n".join(signature(n, e) for n, e in parties),
        "acknowledgments": "\n\n".join(acknowledgment(state, n, e) for n, e in parties),
        "exhibit": exhibit(st, group["assets"], group["title"].title()),
    }
    return render(group["template"], values)


def _cents(v: Fraction) -> str:
    return to_decimal(v, 2)


def _money(value: Any) -> Fraction | None:
    try:
        return Fraction(str(value)) if value not in (None, "") else None
    except (ValueError, ZeroDivisionError):
        return None


def closing_statement(st: DealState) -> list[dict[str, str]]:
    """Rows: label, amount, note. Money is exact (Fractions of decimal strings) and shown to the cent."""
    rows: list[dict[str, str]] = []
    price = _money(st.record.get("price"))
    rows.append({"line": "Purchase price", "amount": _cents(price) if price is not None else "",
                 "note": "" if price is not None else "[price not on the deal record]"})
    adjustments = Fraction(0)
    for a in st.assets:
        v = (st.verify.get(a["id"]) or {}).get("result") or {}
        asset_price = _money(a.get("price"))
        if asset_price is not None:
            rows.append({"line": f"  Allocated to {a['id']} ({a.get('tract') or a['kind']}, {a.get('county')} {a['state']})",
                         "amount": _cents(asset_price), "note": ""})
        if v.get("status") == "SHORTFALL" and asset_price is not None and v.get("sold") and v.get("owned"):
            sold, owned = parse_fraction(v["sold"]), parse_fraction(v["owned"])
            if sold > owned:
                adj = -asset_price * (sold - owned) / sold
                adjustments += adj
                rows.append({"line": f"  PROPOSED title adjustment, {a['id']}", "amount": _cents(adj),
                             "note": f"seller held {fmt(owned)} of the {fmt(sold)} sold; needs acquisitions' approval"})
    rows.append({"line": "Recording fees", "amount": "", "note": "[enter from the county clerk's fee schedule]"})
    if price is not None:
        rows.append({"line": "Due seller before proposed adjustments", "amount": _cents(price), "note": ""})
        if adjustments:
            rows.append({"line": "Due seller if proposed adjustments are approved", "amount": _cents(price + adjustments), "note": ""})
    return rows


def build_package(store: Store, rules: Rules, deal_id: str, actor: str, out_dir: str | Path | None = None,
                  reason: str | None = None, today: date | None = None) -> dict[str, Any]:
    st = fold(store.events(deal_id))
    if "released" in st.package:
        raise ValueError(f"{deal_id}'s funds were released on {st.package['released'].get('date')}; the package can't be "
                         "redrafted. Record a correction as its own instrument (ptk deal draft) or a note")
    if "approved" in st.package and not reason:
        raise ValueError(f"{deal_id}'s package is already approved; pass --reason to redraft it (approval resets)")
    out = Path(out_dir) if out_dir else store.home / "deals" / deal_id / "package"
    out.mkdir(parents=True, exist_ok=True)
    files: list[Path] = []
    blank_count = 0
    for i, group in enumerate(conveyance_groups(st), start=1):
        text = render_conveyance(st, group)
        blank_count += len(blanks(text))
        who = "-".join(re.sub(r"[^a-z0-9]+", "-", s["name"].lower()).strip("-")
                       for sid in group["sellers"] if (s := seller_by_id(st.record, sid)))
        p = out / f"{i:02d}-{group['state']}-{group['template'].replace('_', '-')}-{who[:40]}.md"
        p.write_text(text, encoding="utf-8")
        files.append(p)
    with open(out / "exhibit-a.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["asset", "kind", "state", "county", "tract_or_lease", "legal_description", "gross_acres",
                    "interest_conveyed", "verified_owned", "wells"])
        for a in st.assets:
            v = (st.verify.get(a["id"]) or {}).get("result") or {}
            w.writerow([a["id"], a["kind"], a["state"], a.get("county"), a.get("tract") or a.get("lease", {}).get("id"),
                        a.get("legal_description"), a.get("gross_acres"), interest_text(a, v),
                        v.get("owned") or v.get("owned_wi") or "", "; ".join(str(w_.get("api") or w_.get("name")) for w_ in a.get("wells") or [])])
    files.append(out / "exhibit-a.csv")
    rows = closing_statement(st)
    with open(out / "closing-statement.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["line", "amount", "note"])
        w.writeheader()
        w.writerows(rows)
    files.append(out / "closing-statement.csv")
    gate = [g for g in funds_gate(st, rules) if g != "the package isn't drafted"]
    md = [f"# Closing checklist: {st.record.get('name')} ({deal_id})", "",
          f"Buyer: {st.record['buyer']}. Effective {st.record.get('effective_date')}. Prepared {(today or date.today()).isoformat()} by {actor}.", "",
          "## Closing statement", "", "| Line | Amount | Note |", "|---|---:|---|"]
    md += [f"| {r['line'].strip()} | {r['amount']} | {r['note']} |" for r in rows]
    md += ["", "## Before funds are released", ""]
    md += [f"- [ ] {g}" for g in gate] or ["- Nothing outstanding except signature."]
    md += ["", "## After closing", ""]
    md += [f"- [ ] Record the conveyance in {c} County, {s}." for s, c in st.counties()]
    md += [f"- [ ] {t.task_id}: {t.requirement}" for t in st.tasks.values() if t.stage == "record" and not t.closed]
    md += [f"- [ ] Send the transfer notice to {n['name']} ({n['role']})." for n in st.notice_targets()]
    (out / "closing-checklist.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    files.append(out / "closing-checklist.md")
    manifest = [{"file": p.name, "sha256": sha256(p)} for p in files]
    (out / "manifest.json").write_text(json.dumps({"deal_id": deal_id, "files": manifest}, indent=2), encoding="utf-8")
    payload = {"dir": str(out), "files": manifest, "blanks": blank_count}
    if reason:
        payload["reason"] = reason
    store.append(deal_id, "package.drafted", payload, actor)
    return {"dir": str(out), "files": [m["file"] for m in manifest], "blanks": blank_count, "gate": gate}


# ---- curative drafts --------------------------------------------------------

def _instrument_of(task: Task) -> str:
    tail = task.ref.rsplit(":", 1)[-1] if ":" in task.ref else ""
    return tail if tail and tail != "-" else "[recording reference]"


def draft_for_task(store: Store, deal_id: str, task_id: str, actor: str, out_dir: str | Path | None = None,
                   template: str | None = None) -> dict[str, Any]:
    st = fold(store.events(deal_id))
    task = st.tasks.get(task_id)
    if task is None:
        raise KeyError(f"{deal_id} has no task {task_id}")
    name = template or task.template
    if name not in INSTRUMENT_TEMPLATES:
        raise ValueError(f"{task_id} [{task.code}] has no instrument template; use --template one of {', '.join(INSTRUMENT_TEMPLATES)}")
    asset = next((a for a in st.assets if a["id"] == task.asset_id), st.assets[0])
    state = asset["state"]
    note = ("Texas: an affidavit of heirship recorded for five years is prima facie evidence of the facts stated "
            "(Est. Code §203.001); it's evidence, not a cure. Record it in every county with minerals and in the "
            "decedent's domicile county. [Attorney to confirm.]" if state == "TX" else
            "New Mexico: examiners generally want a probate or a determination of heirship; treat this affidavit as "
            "supporting evidence only. [Attorney to confirm.]")
    values = {
        "banner": BANNER, "venue": venue(state, [asset.get("county") or "[county]"]),
        "county": asset.get("county") or "[county]", "state_name": STATE_NAME[state],
        "instrument": _instrument_of(task), "message": task.message, "requirement": task.requirement,
        "party": task.party_name or "[name of owner]", "state_note": note,
        "acknowledgment_one": acknowledgment(state, "[name of signer]", False),
        "exhibit": exhibit(st, [asset], name.replace("_", " ").title()),
    }
    text = render(name, values)
    out = Path(out_dir) if out_dir else store.home / "deals" / deal_id / "drafts"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{task_id}-{name.replace('_', '-')}.md"
    path.write_text(text, encoding="utf-8")
    store.append(deal_id, "draft.written", {"task_id": task_id, "template": name, "file": str(path),
                                             "sha256": sha256(path), "blanks": len(blanks(text))}, actor)
    return {"file": str(path), "blanks": len(blanks(text))}
