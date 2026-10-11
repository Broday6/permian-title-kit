"""Local desk for the whole business: ``ptk deal serve`` then open http://127.0.0.1:8780.

/crm is the Landowner CRM (owners, holdings, calls, outreach, lead pipeline),
served from web/crm.html with its storage seam pointed at the /api/crm/* routes
below. /deals is the title side: every deal's stage, the curative tracker, the
approval queue and the audit log. Both read and write the same database.

Standard library only. It shows every deal's stage, the curative tracker, the
approval queue and the audit log, and lets a person update tasks, approve or
reject messages, draft requests and rerun verification. Every button calls the
same functions as the CLI, so the same rules apply.

It binds to 127.0.0.1 by default, checks the Host header (so another site
can't reach it through DNS rebinding) and puts a per-run token in every form.
Changes are logged under the name given to ``--by``. It's a single-user
desk tool, not a multi-user web app.
"""

from __future__ import annotations

import html
import json
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse

from . import actions, crm, outbox
from .rules import Rules
from .state import STAGE_TITLES, DealState, fold, summary
from .store import Store

CSS = """
:root{--surface:#ffffff;--plane:#f4f5f7;--card:#ffffff;--card2:#f3f4f6;--ink:#111827;--ink2:#374151;--muted:#6b7280;
--line:#e3e6ea;--line2:#eef0f3;--accent:#1f4f86;--accent-ink:#ffffff;--good:#15803d;--warn:#b45309;--crit:#b42318;
--good-bg:#e8f5ec;--warn-bg:#fdf3e1;--crit-bg:#fdecea;--shadow:0 1px 2px rgba(16,24,40,.05);--radius:10px}
:root[data-theme="dark"]{--surface:#15181d;--plane:#0f1114;--card:#191c22;--card2:#20242b;--ink:#e8eaed;--ink2:#c3c8cf;--muted:#8a919c;
--line:#2a2f37;--line2:#22272e;--accent:#6c9fd8;--accent-ink:#0b1320;--good:#3fb873;--warn:#d9a33a;--crit:#e5605a;
--good-bg:#173022;--warn-bg:#33270f;--crit-bg:#3a1c1b;--shadow:0 1px 2px rgba(0,0,0,.35)}
*{box-sizing:border-box}html,body{margin:0;height:100%}
body{font:13.5px/1.45 "Inter","Segoe UI",system-ui,-apple-system,Roboto,sans-serif;background:var(--plane);color:var(--ink);display:flex;min-height:100vh}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
.i{width:16px;height:16px;fill:none;stroke:currentColor;stroke-width:1.75;stroke-linecap:round;stroke-linejoin:round;flex-shrink:0;vertical-align:-3px}
.i.sm{width:14px;height:14px;vertical-align:-2px}.i.lg{width:20px;height:20px}.i.xl{width:30px;height:30px;stroke-width:1.5}
.sidebar{width:230px;flex-shrink:0;background:var(--surface);border-right:1px solid var(--line);display:flex;flex-direction:column;padding:16px 12px;position:sticky;top:0;height:100vh}
.brand{display:flex;align-items:center;gap:10px;padding:6px 8px 10px}
.brand .logo{width:34px;height:34px;border-radius:9px;background:var(--accent);color:var(--accent-ink);display:flex;align-items:center;justify-content:center}
.brand .logo .i{width:19px;height:19px}.brand .t{font-weight:700;font-size:15px}.brand small{display:block;font-weight:500;color:var(--muted);font-size:11px}
.navlabel{font-size:10.5px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);font-weight:700;padding:16px 12px 6px}
.nav{display:flex;flex-direction:column;gap:2px}
.nav a{display:flex;align-items:center;gap:11px;padding:9px 12px;border-radius:8px;color:var(--ink2);font-weight:560}
.nav a:hover{background:var(--card2);color:var(--ink);text-decoration:none}
.nav a.active{background:color-mix(in srgb,var(--accent) 12%,transparent);color:var(--accent)}
.side-foot{margin-top:auto;border-top:1px solid var(--line);padding-top:10px;display:flex;flex-direction:column;gap:2px}
.side-foot button{background:transparent;border:none;text-align:left;padding:9px 12px;border-radius:8px;color:var(--ink2);cursor:pointer;display:flex;align-items:center;gap:11px;font:inherit;font-weight:560}
.side-foot button:hover{background:var(--card2);color:var(--ink)}
.main{flex:1;min-width:0;display:flex;flex-direction:column}
.topbar{height:58px;flex-shrink:0;display:flex;align-items:center;gap:12px;padding:0 24px;border-bottom:1px solid var(--line);background:var(--surface)}
.topbar h2{margin:0;font-size:16px;font-weight:680}.topbar .spacer{flex:1}.topbar .who{color:var(--muted);font-size:12.5px}
.content{padding:22px 24px;max-width:1240px;width:100%}
h1{font-size:21px;margin:0 0 4px;font-weight:700}
.muted{color:var(--muted)}.meta{color:var(--muted);display:flex;flex-wrap:wrap;gap:4px 14px;margin-bottom:6px}
.panel{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);box-shadow:var(--shadow);margin:14px 0;overflow:hidden}
.panel .ph{display:flex;align-items:center;gap:10px;padding:12px 16px;border-bottom:1px solid var(--line)}
.panel .ph h3{margin:0;font-size:13.5px;font-weight:680}.panel .ph .hint{margin-left:auto;color:var(--muted);font-size:12px}
.panel .pb{padding:14px 16px;overflow-x:auto}.panel .pb.flush{padding:0}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin:4px 0 16px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);padding:14px 16px;box-shadow:var(--shadow)}
.kpi .top{display:flex;justify-content:space-between;color:var(--muted);font-size:12px;font-weight:600}.kpi .top .i{color:var(--accent)}
.kpi .num{font-size:26px;font-weight:700;margin-top:6px}.kpi.alert .num{color:var(--crit)}.kpi.good .num{color:var(--good)}
table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:10px 14px;border-bottom:1px solid var(--line2);vertical-align:top}
th{font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);font-weight:700;background:var(--card)}
tr:last-child td{border-bottom:0}td .sub{color:var(--muted);font-size:12.5px}
.chip{display:inline-flex;align-items:center;gap:5px;padding:2px 9px;border-radius:20px;font-size:12px;font-weight:600;background:var(--card2);color:var(--ink2);white-space:nowrap}
.chip.stop{background:var(--crit-bg);color:var(--crit)}.chip.review{background:var(--warn-bg);color:var(--warn)}.chip.ok{background:var(--good-bg);color:var(--good)}
.chip.accent{background:color-mix(in srgb,var(--accent) 13%,transparent);color:var(--accent)}
.steps{display:grid;grid-template-columns:repeat(6,1fr);gap:8px;margin:14px 0}
.step{border:1px solid var(--line);border-radius:9px;padding:10px 12px;background:var(--card);font-size:12.5px;display:flex;gap:9px;align-items:flex-start}
.step .n{width:22px;height:22px;border-radius:50%;border:1.5px solid var(--line);display:flex;align-items:center;justify-content:center;font-weight:700;font-size:11.5px;color:var(--muted);flex-shrink:0}
.step b{display:block;font-size:12.5px}.step span.s{color:var(--muted)}
.step.done{border-color:color-mix(in srgb,var(--good) 45%,var(--line))}.step.done .n{border:none;color:var(--good)}
.step.cur{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent) inset}.step.cur .n{border-color:var(--accent);color:var(--accent)}
ul.block{margin:2px 0 0;padding:0;list-style:none}ul.block li{padding:7px 0;border-bottom:1px solid var(--line2);display:flex;gap:9px}
ul.block li:last-child{border-bottom:0}ul.block li .i{margin-top:2px}
.btn{display:inline-flex;align-items:center;gap:7px;background:var(--card);border:1px solid var(--line);color:var(--ink);padding:7px 12px;border-radius:8px;cursor:pointer;font:inherit;font-weight:580;white-space:nowrap}
.btn:hover{border-color:var(--muted);text-decoration:none}.btn.primary{background:var(--accent);border-color:var(--accent);color:var(--accent-ink)}
.btn.ghost{background:transparent;border-color:transparent;color:var(--accent)}.btn.sm{padding:5px 10px;font-size:12.5px}
.actions{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0 2px}
form.inline{display:inline-flex;gap:6px;flex-wrap:wrap;align-items:center;margin:6px 6px 0 0}
input,select,textarea{font:inherit;color:var(--ink);background:var(--card);border:1px solid var(--line);border-radius:7px;padding:5px 8px}
input:focus,select:focus{outline:none;border-color:var(--accent)}
details summary{cursor:pointer;color:var(--muted)}pre{white-space:pre-wrap;font:12.5px/1.45 ui-monospace,Consolas,monospace;margin:8px 0;background:var(--card2);padding:10px 12px;border-radius:8px}
.flash{display:flex;gap:10px;align-items:flex-start;padding:11px 14px;border-radius:9px;margin:0 0 14px;font-weight:560}
.flash.err{background:var(--crit-bg);color:var(--crit)}.flash.ok{background:var(--good-bg);color:var(--good)}
.empty{padding:36px 16px;text-align:center;color:var(--muted)}.empty .i{margin-bottom:8px;opacity:.6}
.c-good{color:var(--good)}.c-warn{color:var(--warn)}.c-crit{color:var(--crit)}.c-accent{color:var(--accent)}.c-muted{color:var(--muted)}
@media (max-width:860px){body{display:block}.sidebar{position:static;width:auto;height:auto;flex-direction:row;flex-wrap:wrap;gap:4px;align-items:center}
.navlabel,.side-foot{display:none}.nav{flex-direction:row;flex-wrap:wrap}.steps{grid-template-columns:repeat(2,1fr)}
th.hide,td.hide{display:none}.content{padding:16px}.topbar{padding:0 16px}}
@media print{.sidebar,.topbar,form,.actions{display:none!important}body{display:block}.panel{box-shadow:none;break-inside:avoid}}
"""
THEME_JS = ("<script>(function(){var t='light';try{t=localStorage.getItem('landCRM_theme')||'light';}catch(e){}"
            "document.documentElement.setAttribute('data-theme',t);})();"
            "function deskTheme(){var r=document.documentElement,t=r.getAttribute('data-theme')==='dark'?'light':'dark';"
            "r.setAttribute('data-theme',t);try{localStorage.setItem('landCRM_theme',t);}catch(e){}}</script>")
ICONS_FILE = Path(__file__).with_name("web") / "icons.svg"
_ICONS: list[str] = []


def icons_sprite() -> str:
    if not _ICONS:
        _ICONS.append(ICONS_FILE.read_text(encoding="utf-8"))
    return _ICONS[0]


def ic(name: str, cls: str = "") -> str:
    return f'<svg class="i{(" " + cls) if cls else ""}" aria-hidden="true"><use href="#i-{name}"/></svg>'


def e(v: Any) -> str:
    return html.escape("" if v is None else str(v))


def _clip(text: str, n: int) -> str:
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0] + " …"


def sev_chip(sev: str) -> str:
    cls = "stop" if sev == "stop" else "review" if sev == "review" else ""
    return f'<span class="chip {cls}">{e(sev.title())}</span>'


STATUS_CHIP = {"PASS": ("ok", "check"), "WARN": ("review", "alert"), "FAIL": ("stop", "x"), "INFO": ("", "info")}
NAV = [("Acquisition", [("dashboard", "/crm#v=dashboard", "grid", "Dashboard"), ("owners", "/crm#v=customers", "users", "Owners"),
                        ("pipeline", "/crm#v=pipeline", "columns", "Pipeline"), ("queue", "/crm#v=queue", "phone", "Call queue"),
                        ("outreach", "/crm#v=emails", "mail", "Outreach")]),
       ("Title", [("deals", "/deals", "briefcase", "Deals"), ("outbox", "/outbox", "inbox", "Approval queue"),
                  ("audit", "/audit", "shield", "Accuracy")])]


AREA_HINTS = {"Engine": "golden cases recomputed on this install", "Records": "tamper evidence",
              "Deals": "verification re-run and compared", "CRM": "owner records", "Rules": "rules and templates",
              "Setup": "tools on this machine"}


class App:
    def __init__(self, store: Store, rules: Rules, actor: str, port: int):
        self.store, self.rules, self.actor, self.port = store, rules, actor, port
        self.token = secrets.token_urlsafe(24)

    # ---- pages ---------------------------------------------------------------
    def page(self, title: str, body: str, flash: str = "", err: bool = False, active: str = "deals") -> str:
        f = (f'<div class="flash {"err" if err else "ok"}">{ic("alert" if err else "check-circle")}<span>{e(flash)}</span></div>'
             if flash else "")
        nav = ""
        for label, items in NAV:
            nav += f"<div class='navlabel'>{label}</div><nav class='nav'>"
            nav += "".join(f"<a href='{href}' class='{'active' if key == active else ''}'>{ic(icon)}{text}</a>"
                           for key, href, icon, text in items)
            nav += "</nav>"
        return (f"<!doctype html><html lang='en' data-theme='light'><head><meta charset='utf-8'>"
                f"<meta name='viewport' content='width=device-width,initial-scale=1'><title>{e(title)} · Land Desk</title>"
                f"{THEME_JS}<style>{CSS}</style></head><body>{icons_sprite()}"
                f"<aside class='sidebar'><div class='brand'><div class='logo'>{ic('logo')}</div>"
                f"<div class='t'>Land Desk<small>Acquisition and title</small></div></div>{nav}"
                f"<div class='side-foot'><button onclick='deskTheme()'>{ic('moon')}Theme</button>"
                f"<button onclick='window.print()'>{ic('printer')}Print</button></div></aside>"
                f"<div class='main'><div class='topbar'><h2>{e(title)}</h2><span class='spacer'></span>"
                f"<span class='who'>{ic('user', 'sm')} {e(self.actor)} · {e(self.store.path.name)}</span></div>"
                f"<div class='content'>{f}{body}</div></div></body></html>")

    def form(self, action: str, inner: str, cls: str = "inline") -> str:
        return f'<form class="{cls}" method="post" action="{e(action)}"><input type="hidden" name="token" value="{self.token}">{inner}</form>'

    def panel(self, title: str, body: str, icon: str = "", hint: str = "", flush: bool = False) -> str:
        head = f"<div class='ph'>{ic(icon) if icon else ''}<h3>{e(title)}</h3>{f'<span class=hint>{e(hint)}</span>' if hint else ''}</div>"
        return f"<div class='panel'>{head}<div class='pb{' flush' if flush else ''}'>{body}</div></div>"

    @staticmethod
    def kpi(icon: str, num: Any, label: str, cls: str = "") -> str:
        return f"<div class='kpi {cls}'><div class='top'><span>{e(label)}</span>{ic(icon)}</div><div class='num'>{e(num)}</div></div>"

    def deals(self) -> str:
        rows, sums = [], [summary(fold(self.store.events(d)), self.rules) for d in self.store.deal_ids()]
        for s in sums:
            did = s["deal_id"]
            stops = f" <span class='chip stop'>{s['stop_tasks']} stop</span>" if s["stop_tasks"] else ""
            opinion = f"<span class='chip review'>{ic('scale', 'sm')} Needed</span>" if s["opinion_required"] else "<span class='muted'>No</span>"
            rows.append(f"<tr><td><a href='/deal/{e(did)}'><b>{e(did)}</b></a><div class='sub'>{e(s['name'])}</div></td>"
                        f"<td>{e(', '.join(s['states']))}<div class='sub'>{e(', '.join(s['kinds']))}</div></td>"
                        f"<td><span class='chip accent'>{e(s['stage_title'])}</span></td>"
                        f"<td>{s['open_tasks']}{stops}</td><td>{opinion}</td>"
                        f"<td class='hide sub'>{e(_clip(s['next'], 200))}</td></tr>")
        kpis = ("<div class='kpis'>" + self.kpi("briefcase", len(sums), "Deals")
                + self.kpi("alert", sum(1 for s in sums if s["stop_tasks"]), "Deals with stop tasks",
                           "alert" if any(s["stop_tasks"] for s in sums) else "")
                + self.kpi("scale", sum(1 for s in sums if s["opinion_required"]), "Need an attorney opinion")
                + self.kpi("inbox", sum(s["messages_awaiting_approval"] for s in sums), "Messages to approve")
                + self.kpi("check-circle", sum(1 for s in sums if s["stage"] == "done"), "Closed out", "good") + "</div>")
        table = ("<table><tr><th>Deal</th><th>State / kind</th><th>Stage</th><th>Open tasks</th><th>Opinion</th>"
                 "<th class='hide'>Next step</th></tr>" + "".join(rows) + "</table>") if rows else (
            f"<div class='empty'>{ic('briefcase', 'xl')}<div><b>No title deals yet.</b></div><div>Hand one off from an owner in "
            f"<a href='/crm#v=customers'>Owners</a> (Start title follow-up), or run <code>ptk deal intake deal.json</code>.</div></div>")
        return self.page("Deals", kpis + self.panel("Title deals", table, "briefcase", flush=bool(rows)), active="deals")

    def deal(self, did: str, flash: str = "", err: bool = False) -> str:
        st = fold(self.store.events(did))
        s = summary(st, self.rules)
        r = st.record
        steps = []
        cur = s["stage"]
        for i, stg in enumerate(s["stages"], start=1):
            cls = "done" if stg["done"] else ("cur" if stg["stage"] == cur else "")
            mark = ic("check-circle", "lg") if stg["done"] else str(i)
            state = "Done" if stg["done"] else f"{len(stg['blockers'])} to do"
            steps.append(f"<div class='step {cls}'><span class='n'>{mark}</span><div><b>{e(stg['title'])}</b><span class='s'>{state}</span></div></div>")
        crm_links = " ".join(f"<a href='/crm#c={quote(sl['crm_id'])}'>{ic('user', 'sm')} {e(sl['name'])} in Owners {ic('external', 'sm')}</a>"
                             for sl in r.get("sellers", []) if sl.get("crm_id"))
        needs_run = any(not ((a.get("title") or {}).get("takeoff") or (a.get("title") or {}).get("lease_chain")) for a in st.assets)
        buttons = self.form(f"/deal/{did}/verify", f"<button class='btn'>{ic('refresh', 'sm')} Rerun verification</button>")
        buttons += self.form(f"/deal/{did}/titlerun", f"<button class='btn {'primary' if needs_run else ''}'>{ic('folder', 'sm')} "
                                                      f"{'Start title run' if needs_run else 'Refresh title run plan'}</button>")
        body = [f"<h1>{e(r.get('name'))}</h1><div class='meta'><span>{e(did)}</span><span>Buyer {e(r.get('buyer'))}</span>"
                f"<span>Effective {e(r.get('effective_date') or 'not set')}</span><span>Price {e(r.get('price') or 'not set')}</span>"
                f"<span>{ic('pin', 'sm')} {e(', '.join(s['counties']))}</span></div>",
                f"<div class='meta'>{crm_links}</div>" if crm_links else "",
                f"<div class='actions'>{buttons}</div>",
                f"<div class='steps'>{''.join(steps)}</div>"]
        stage_panels = []
        for stg in s["stages"]:
            if stg["done"] and not stg["notes"]:
                continue
            items = "".join(f"<li>{ic('chevron-right', 'sm c-muted')}<span>{e(b)}</span></li>" for b in stg["blockers"])
            notes = "".join(f"<li class='muted'>{ic('info', 'sm')}<span>{e(n)}</span></li>" for n in stg["notes"])
            chip = "<span class='chip ok'>Done</span>" if stg["done"] else f"<span class='chip'>{len(stg['blockers'])} to do</span>"
            stage_panels.append(f"<div class='panel'><div class='ph'><h3>{e(stg['title'])}</h3>{chip}</div>"
                                f"<div class='pb'><ul class='block'>{items}{notes}</ul></div></div>")
        body += stage_panels
        body.append(self.panel("Assets", self.assets_table(st), "file-text", flush=True))
        body.append(self.panel("Curative tracker", self.tasks_table(st), "list", f"{len(st.open_tasks())} open", flush=True))
        body.append(self.panel("Messages", self.messages_table(st), "mail", flush=True))
        body.append(self.panel("Recording, notices and payments", self.record_table(st), "calendar", flush=True))
        log = "".join(f"<tr><td>{ev.id}</td><td>{e(ev.ts)}</td><td>{e(ev.actor)}</td><td>{e(ev.type)}</td></tr>"
                      for ev in reversed(st.events[-80:]))
        body.append(self.panel("Audit log", f"<details><summary>{len(st.events)} events, newest first</summary>"
                                            f"<table><tr><th>#</th><th>When</th><th>Who</th><th>What</th></tr>{log}</table></details>", "clock"))
        return self.page(f"{did}", "".join(body), flash, err, active="deals")

    def assets_table(self, st: DealState) -> str:
        rows = []
        for a in st.assets:
            v = (st.verify.get(a["id"]) or {}).get("result") or {}
            status = v.get("status", "Not verified")
            chip = "ok" if status == "OK" else "stop" if status in ("SHORTFALL", "NOT_IN_CHAIN", "CONVEYED_AFTER", "NO_TITLE", "ERROR") else ""
            interest = a.get("interest") or (f"WI {a.get('wi')} / NRI {a.get('nri')}" if a.get("wi") else "")
            notes = "".join(f"<li>{ic('info', 'sm c-muted')}<span>{e(n)}</span></li>" for n in v.get("notes", []))
            note_html = f"<details><summary>Notes</summary><ul class='block'>{notes}</ul></details>" if notes else ""
            rows.append(f"<tr><td><b>{e(a['id'])}</b><div class='sub'>{e(a['kind'])}</div></td><td>{e(a.get('county'))} {e(a['state'])}</td>"
                        f"<td class='hide'>{e(a.get('legal_description') or a.get('tract'))}</td><td>{e(interest)}</td>"
                        f"<td><span class='chip {chip}'>{e(status)}</span><div class='sub'>{e(v.get('summary', ''))}</div>{note_html}</td></tr>")
        return "<table><tr><th>Asset</th><th>Where</th><th class='hide'>Lands</th><th>Interest</th><th>Title</th></tr>" + "".join(rows) + "</table>"

    def tasks_table(self, st: DealState) -> str:
        rows = []
        order = list(STAGE_TITLES)
        tasks = sorted(st.tasks.values(), key=lambda t: (t.closed, order.index(t.stage), t.severity != "stop", int(t.task_id[1:])))
        for t in tasks:
            if t.closed:
                rows.append(f"<tr class='muted'><td>{e(t.task_id)}</td><td>{e(t.code)}</td><td colspan='3'>{e(t.requirement or t.message)}</td>"
                            f"<td><span class='chip ok'>{ic('check', 'sm')} {e(t.status.title())}</span></td></tr>")
                continue
            extra = ("<input name='attorney' placeholder='Attorney (to waive)'><input name='opinion' placeholder='OP# (to clear)'>"
                     if t.requires_attorney else "")
            ctl = self.form(f"/deal/{st.deal_id}/task/{t.task_id}",
                            "<select name='status'><option value=''>Status</option>" +
                            "".join(f"<option value='{s}'>{s.title()}</option>" for s in ("requested", "received", "cleared", "waived", "open")) +
                            f"</select><input name='note' placeholder='Note'><input name='reason' placeholder='Reason (to waive)'>{extra}"
                            f"<button class='btn sm'>{ic('check', 'sm')} Save</button>")
            req = ""
            if t.party not in ("landman", "attorney", "acquisitions"):
                req = self.form(f"/deal/{st.deal_id}/request/{t.task_id}", f"<button class='btn sm ghost'>{ic('mail', 'sm')} Draft request</button>")
            att = f" <span class='chip review'>{ic('scale', 'sm')} Attorney</span>" if t.requires_attorney else ""
            msg = f"<div class='sub'>{e(t.message)}</div>" if t.message and t.message != t.requirement else ""
            rows.append(f"<tr><td>{e(t.task_id)}</td><td>{sev_chip(t.severity)}<div class='sub'>{e(STAGE_TITLES[t.stage])}</div></td>"
                        f"<td><b>{e(t.code)}</b>{att}<div>{e(t.requirement or t.message)}</div>{msg}{ctl}{req}</td>"
                        f"<td class='hide'>{e(t.party_name or t.party)}</td><td class='hide'>{e(t.due)}</td><td>{e(t.status.title())}</td></tr>")
        return ("<table><tr><th>#</th><th>Severity</th><th>Task</th><th class='hide'>Who</th><th class='hide'>Due</th><th>Status</th></tr>"
                + "".join(rows) + "</table>") if rows else f"<div class='empty'>{ic('check-circle', 'xl')}<div>No tasks.</div></div>"

    def _message_controls(self, did: str, m, back: str = "") -> str:
        if m.status != "draft":
            return ""
        q = "?back=outbox" if back else ""
        return (self.form(f"/deal/{did}/message/{m.msg_id}/approve{q}", f"<button class='btn sm primary'>{ic('check', 'sm')} Approve</button>") +
                self.form(f"/deal/{did}/message/{m.msg_id}/reject{q}",
                          f"<input name='reason' placeholder='Reason'><button class='btn sm'>{ic('x', 'sm')} Reject</button>"))

    def messages_table(self, st: DealState) -> str:
        rows = []
        for m in reversed(list(st.messages.values())):
            auto = " (auto)" if m.auto_approved else ""
            rows.append(f"<tr><td>{e(m.msg_id)}</td><td>{e(m.to_name)}<div class='sub'>{e(m.channel)} · {e(m.category)}</div></td>"
                        f"<td><details><summary>{e(m.subject)}</summary><pre>{e(m.body)}</pre></details>{self._message_controls(st.deal_id, m)}</td>"
                        f"<td>{e(m.status.title() + auto)}</td></tr>")
        return ("<table><tr><th>#</th><th>To</th><th>Message</th><th>Status</th></tr>" + "".join(rows) + "</table>") if rows \
            else f"<div class='empty'>{ic('mail', 'xl')}<div>No messages.</div></div>"

    def record_table(self, st: DealState) -> str:
        rows = []
        for s_, c in st.counties():
            r = st.recorded_in(s_, c)
            got = (f"{ic('check', 'sm c-good')} {e(r['instrument_no'] or (str(r['volume']) + '/' + str(r['page'])))} on {e(r['recorded'])}"
                   if r else "<span class='muted'>Not recorded</span>")
            rows.append(f"<tr><td>Recording</td><td>{e(c)} County, {e(s_)}</td><td>{got}</td></tr>")
        for t in st.notice_targets():
            n = st.notified(t["name"])
            got = f"{ic('check', 'sm c-good')} {e(n['method'])} {e(n['date'])}" if n else "<span class='muted'>Not sent</span>"
            rows.append(f"<tr><td>Notice</td><td>{e(t['name'])} ({e(t['role'])})</td><td>{got}</td></tr>")
        for x in st.expected.values():
            paid = st.payments_for(x["payor"], x.get("well"))
            last = paid[-1] if paid else None
            got = f"{e(last['status'].title())} {e(last.get('decimal') or '')} on {e(last['received'])}" if last else "<span class='muted'>Waiting</span>"
            rows.append(f"<tr><td>Payment</td><td>{e(x['payor'])}: {e(x.get('well'))} ({e(x['type'])} {e(x['decimal_display'])})</td><td>{got}</td></tr>")
        return ("<table><tr><th>Item</th><th>For</th><th>Status</th></tr>" + "".join(rows) + "</table>") if rows \
            else f"<div class='empty'>{ic('calendar', 'xl')}<div>Nothing yet.</div></div>"

    def queue(self, flash: str = "", err: bool = False) -> str:
        rows = []
        for did in self.store.deal_ids():
            st = fold(self.store.events(did))
            for m in st.messages.values():
                if m.status not in ("draft", "approved", "exported", "failed"):
                    continue
                rows.append(f"<tr><td><a href='/deal/{e(did)}'>{e(did)}</a><div class='sub'>{e(m.msg_id)}</div></td>"
                            f"<td>{e(m.to_name)}<div class='sub'>{e(m.channel)} · {e(m.category)}</div></td>"
                            f"<td><details><summary>{e(m.subject)}</summary><pre>{e(m.body)}</pre></details>"
                            f"{self._message_controls(did, m, back='outbox')}</td><td>{e(m.status.title() + (' (auto)' if m.auto_approved else ''))}</td></tr>")
        table = ("<table><tr><th>Deal</th><th>To</th><th>Message</th><th>Status</th></tr>" + "".join(rows) + "</table>") if rows \
            else f"<div class='empty'>{ic('inbox', 'xl')}<div>Nothing waiting for approval.</div></div>"
        send = self.form("/outbox/send", f"<button class='btn primary'>{ic('send', 'sm')} Send approved email</button>"
                                         "<span class='muted'>SMTP from the PTK_SMTP_* settings; without them, approved email is saved as .eml files.</span>")
        return self.page("Approval queue", self.panel("Waiting to go out", table, "inbox", flush=bool(rows)) +
                         f"<div class='actions'>{send}</div>", flash, err, active="outbox")

    def audit(self) -> str:
        from .accuracy import AREAS, run_audit
        report = run_audit(self.store, self.rules)
        c = report.counts
        kpis = ("<div class='kpis'>" + self.kpi("check-circle", c["PASS"], "Passed", "good")
                + self.kpi("alert", c["WARN"], "To look at", "alert" if c["WARN"] else "")
                + self.kpi("x", c["FAIL"], "Failed", "alert" if c["FAIL"] else "")
                + self.kpi("info", c["INFO"], "Not checkable here") + "</div>")
        panels = []
        for area in AREAS:
            rows = []
            for chk in (x for x in report.checks if x.area == area):
                cls, icon = STATUS_CHIP[chk.status]
                items = "".join(f"<li>{ic('chevron-right', 'sm c-muted')}<span>{e(i)}</span></li>" for i in chk.items[:60])
                more = f"<li class='muted'>and {len(chk.items) - 60} more</li>" if len(chk.items) > 60 else ""
                detail = (f"<details><summary>{len(chk.items)} item(s)</summary><ul class='block'>{items}{more}</ul></details>"
                          if chk.items else "")
                rows.append(f"<tr><td style='width:110px'><span class='chip {cls}'>{ic(icon, 'sm')} {chk.status}</span></td>"
                            f"<td><b>{e(chk.name)}</b><div class='sub'>{e(chk.detail)}</div>{detail}</td></tr>")
            panels.append(self.panel(area, "<table>" + "".join(rows) + "</table>", hint=AREA_HINTS.get(area, ""), flush=True))
        head = (f"<div class='meta'><span>Run {e(report.ran)}</span><span>Read-only: it never changes data.</span>"
                f"<span>Same checks as <code>ptk audit</code>.</span></div>"
                f"<div class='actions'><a class='btn primary' href='/audit'>{ic('refresh', 'sm')} Run again</a></div>")
        return self.page("Accuracy", head + kpis + "".join(panels), active="audit")



    # ---- actions -------------------------------------------------------------
    def post(self, path: str, form: dict[str, str]) -> tuple[str, str, bool]:
        """Returns (page to show, flash message, is_error)."""
        parts = [p for p in urlparse(path).path.split("/") if p]
        back_outbox = "back=outbox" in (urlparse(path).query or "")
        try:
            if parts[:1] == ["outbox"] and parts[1:] == ["send"]:
                res = outbox.send_approved(self.store, self.rules, self.actor)
                return "outbox", f"{len(res)} message(s) processed: " + "; ".join(f"{r['msg_id']} {r['result']}" for r in res), False
            if parts[0] != "deal" or len(parts) < 3:
                raise ValueError("unknown action")
            did = parts[1]
            if parts[2] == "verify":
                from .verify import verify_deal
                verify_deal(self.store, self.rules, did, self.actor)
                return did, "Verification rerun.", False
            if parts[2] == "titlerun":
                from .titlerun import start_title_run
                r = start_title_run(self.store, self.rules, did, self.actor)
                return did, f"Title run set up for {', '.join(r['created'])}: open each folder's SEARCH-PLAN.md.", False
            if parts[2] == "task":
                actions.update_task(self.store, did, parts[3], self.actor, form.get("status") or None, form.get("note", ""),
                                    form.get("reason") or None, form.get("attorney") or None, form.get("opinion") or None)
                return did, f"Updated {parts[3]}.", False
            if parts[2] == "request":
                res = outbox.request_for_task(self.store, self.rules, did, parts[3], self.actor)
                return did, "Drafted " + ", ".join(r["msg_id"] for r in res) + " for approval.", False
            if parts[2] == "message":
                mid, verb = parts[3], parts[4]
                if verb == "approve":
                    outbox.approve(self.store, did, mid, self.actor)
                elif verb == "reject":
                    outbox.reject(self.store, did, mid, self.actor, form.get("reason") or "rejected in the dashboard")
                else:
                    raise ValueError("unknown message action")
                return ("outbox" if back_outbox else did), f"{mid} {verb}d.", False
            raise ValueError("unknown action")
        except IndexError:
            return "outbox", "That action needs more of the address (deal, task or message id).", True
        except (ValueError, KeyError) as ex:
            target = parts[1] if len(parts) > 1 and parts[0] == "deal" else "outbox"
            return ("outbox" if back_outbox else target), str(ex.args[0] if ex.args else ex), True


CRM_PAGE = Path(__file__).with_name("web") / "crm.html"
CRM_CSP = ("default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self' data:; "
           "connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
MAX_JSON = 10 * 1024 * 1024


def crm_api(app: App, method: str, parts: list[str], body: bytes, headers) -> tuple[int, Any]:
    """The CRM's storage seam on the server side: returns (status, JSON body)."""
    def obj() -> dict[str, Any]:
        try:
            value = json.loads(body.decode("utf-8") or "null")
        except (UnicodeDecodeError, ValueError) as ex:
            raise ValueError(f"not JSON: {ex}") from ex
        if not isinstance(value, dict):
            raise ValueError("expected a JSON object")
        return value

    store, rules, actor = app.store, app.rules, app.actor
    route = "/".join(parts[2:])  # after "api/crm"
    if method == "GET" and route == "state":
        return 200, {**crm.load_state(store), "deals": crm.deals_by_customer(store, rules)}
    if method == "PUT" and route == "settings":
        return 200, {"saved": crm.save_settings(store, obj(), actor)}
    if method == "POST" and route == "send":
        msg = obj()
        return 200, crm.send_outreach(store, actor, str(msg.get("customerId") or ""), msg)
    if len(parts) >= 4 and parts[2] == "customers":
        cid = parts[3]
        if len(parts) == 4 and method == "PUT":
            rec = obj()
            if rec.get("id") != cid:
                raise ValueError("the record's id doesn't match the address")
            return 200, {"saved": crm.save_customer(store, rec, actor)}
        if len(parts) == 4 and method == "DELETE":
            return 200, {"deleted": crm.delete_customer(store, cid, actor)}
        if len(parts) == 5 and parts[4] == "deal" and method == "POST":
            req = obj()
            r = crm.start_deal(store, rules, cid, actor, str(req.get("buyer") or ""), req.get("state") or None)
            row = crm.deals_by_customer(store, rules).get(cid, [{}])[0]
            return 200, {"deal_id": r["deal_id"], "deal": row, "assumptions": r["assumptions"], "file": r["file"]}
        if len(parts) == 5 and parts[4] == "documents" and method == "POST":
            name = unquote(headers.get("X-Filename") or "file")
            return 200, crm.add_document(store, actor, cid, name, headers.get("Content-Type") or "", body)
    if len(parts) == 4 and parts[2] == "files" and method == "DELETE":
        crm.remove_document(store, actor, parts[3])
        return 200, {"removed": parts[3]}
    return 404, {"error": "no such API route"}


def make_handler(app: App):
    allowed_hosts = {f"127.0.0.1:{app.port}", f"localhost:{app.port}"}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # quiet
            return

        def _send(self, code: int, body: str | bytes, ctype: str = "text/html; charset=utf-8",
                  csp: str = "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src 'self' data:; form-action 'self'",
                  extra: dict[str, str] | None = None) -> None:
            data = body.encode("utf-8") if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", csp)
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def _json(self, code: int, value: Any) -> None:
            self._send(code, json.dumps(value), "application/json; charset=utf-8", "default-src 'none'")

        def _redirect(self, where: str, code: int = 302) -> None:
            self.send_response(code)
            self.send_header("Location", where)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _host_ok(self) -> bool:
            return self.headers.get("Host", "") in allowed_hosts

        def _origin_ok(self) -> bool:
            origin = self.headers.get("Origin")
            return not origin or origin.split("://", 1)[-1] in allowed_hosts

        def _api(self, method: str) -> None:
            """JSON API for the CRM page. Writes need the page's token in X-PTK-Token (a custom header, so no CSRF)."""
            if not (self._host_ok() and self._origin_ok()):
                return self._json(403, {"error": "forbidden host or origin"})
            if not secrets.compare_digest(self.headers.get("X-PTK-Token") or "", app.token):
                return self._json(403, {"error": "stale page: reload it and try again"})
            length = int(self.headers.get("Content-Length") or 0)
            limit = crm.MAX_FILE_BYTES + 1024 if self.path.endswith("/documents") else MAX_JSON
            if length > limit:
                return self._json(413, {"error": "too large"})
            body = self.rfile.read(length) if length else b""
            parts = [unquote(x) for x in urlparse(self.path).path.split("/") if x]
            try:
                code, value = crm_api(app, method, parts, body, self.headers)
            except KeyError as ex:
                code, value = 404, {"error": str(ex.args[0] if ex.args else ex)}
            except (ValueError, OSError) as ex:
                code, value = 400, {"error": str(ex)}
            self._json(code, value)

        def do_GET(self):  # noqa: N802
            if not self._host_ok():
                return self._send(403, "Forbidden host")
            url = urlparse(self.path)
            path = url.path.rstrip("/") or "/"
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            flash, err = q.get("flash", ""), q.get("err") == "1"
            try:
                if path == "/":
                    return self._redirect("/crm")
                if path == "/crm":
                    page = (CRM_PAGE.read_text(encoding="utf-8").replace("__PTK_TOKEN__", app.token)
                            .replace("<!--PTK_ICONS-->", icons_sprite()))
                    return self._send(200, page, csp=CRM_CSP)
                if path.startswith("/api/crm/"):
                    return self._api("GET")
                if path.startswith("/files/"):
                    f = crm.file_path(app.store, unquote(path.split("/", 2)[2]))
                    ctype = next((t for t, ext in crm.FILE_TYPES.items() if f.suffix == ext), "application/octet-stream")
                    return self._send(200, f.read_bytes(), ctype, "sandbox; default-src 'none'; img-src 'self'; style-src 'unsafe-inline'",
                                      {"Content-Disposition": f"inline; filename=\"{f.name}\""})
                if path == "/deals":
                    return self._send(200, app.deals())
                if path == "/audit":
                    return self._send(200, app.audit())
                if path == "/outbox":
                    return self._send(200, app.queue(flash, err))
                if path.startswith("/deal/"):
                    return self._send(200, app.deal(unquote(path.split("/")[2]), flash, err))
                if path == "/api/deals":
                    data = [summary(fold(app.store.events(d)), app.rules) for d in app.store.deal_ids()]
                    return self._json(200, data)
                return self._send(404, app.page("Not found", "<h1>Not found</h1>"))
            except KeyError:
                return self._send(404, app.page("Not found", "<h1>Not found</h1>"))

        def do_PUT(self):  # noqa: N802
            return self._api("PUT") if self.path.startswith("/api/crm/") else self._send(404, "Not found")

        def do_DELETE(self):  # noqa: N802
            return self._api("DELETE") if self.path.startswith("/api/crm/") else self._send(404, "Not found")

        def do_POST(self):  # noqa: N802
            if self.path.startswith("/api/crm/"):
                return self._api("POST")
            if not self._host_ok():
                return self._send(403, "Forbidden host")
            if not self._origin_ok():
                return self._send(403, "Forbidden origin")
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_JSON:
                return self._send(413, "Too large")
            form = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode("utf-8")).items()}
            if not secrets.compare_digest(form.get("token", ""), app.token):
                return self._send(403, "Stale form: reload the page and try again.")
            target, flash, err = app.post(self.path, form)
            # Post/redirect/get, so reloading the page never repeats an action.
            where = "/outbox" if target == "outbox" else f"/deal/{quote(target)}"
            self._redirect(f"{where}?{urlencode({'flash': flash[:600], 'err': '1' if err else '0'})}", 303)

    return Handler


LOOPBACK = ("127.0.0.1", "localhost", "::1")


def serve(store: Store, rules: Rules, actor: str, host: str = "127.0.0.1", port: int = 8780, open_browser: bool = False) -> None:
    if host not in LOOPBACK:
        raise ValueError(f"the dashboard has no sign-in, so it only listens on this computer ({', '.join(LOOPBACK)}), "
                         f"not {host}. A shared, multi-user version is open work (HANDOFF 12.1)")
    app = App(store, rules, actor, port)
    httpd = ThreadingHTTPServer((host, port), make_handler(app))
    if open_browser:
        import threading
        import webbrowser
        threading.Timer(0.8, lambda: webbrowser.open(f"http://127.0.0.1:{port}/crm")).start()
    print(f"Desk on http://127.0.0.1:{port}: CRM at /crm, title deals at /deals (database {store.path}; "
          f"logging changes as {actor}). Ctrl+C stops it.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


__all__ = ["serve", "App", "make_handler"]
