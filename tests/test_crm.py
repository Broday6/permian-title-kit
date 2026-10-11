"""The Landowner CRM folded into the deal desk: one store, the CRM's API seam, the hand-off to title.

All owners here are FICTIONAL.
"""

import io
import json
import socket
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path

from ptk.cli import main
from ptk.deal import crm
from ptk.deal.outbox import FileTransport
from ptk.deal.rules import Rules
from ptk.deal.state import fold
from ptk.deal.store import Store

DAY0 = date(2026, 10, 11)
ACTOR = "Test Landman"


def backup() -> dict:
    return {
        "_comment": "FICTIONAL EXAMPLE CRM backup for tests",
        "customers": [
            {"id": "c1", "name": "Jane Example", "phone": "(575) 555-0101", "email": "jane@example.com",
             "mailing": "1 Example Rd, Carlsbad, NM 88220", "apn": "", "acres": "160", "county": "Eddy",
             "location": "Section 15, T23S, R31E, NMPM: NE/4", "value": "90000", "asking": "120000", "forsale": "Maybe",
             "ownership": "Inherited", "status": "Under Contract", "tags": ["hot-lead"], "notes": "FICTIONAL",
             "created": "2026-10-01", "followUp": "",
             "interests": [
                 {"caseId": "25001", "well": "EXAMPLE 15 FED 1H", "type": "Royalty", "pct": "0.390625%", "status": "Producing",
                  "buySignal": "High", "sourcePDF": "exhibitA.pdf", "foundIn": "Exhibit A"},
                 {"caseId": "25001", "well": "EXAMPLE 15 FED 2H", "type": "Working Interest", "pct": "1.5%", "status": "Permitted",
                  "buySignal": "", "sourcePDF": "exhibitA.pdf", "foundIn": "Exhibit B"}],
             "calls": [{"date": "2026-10-05", "outcome": "Reached", "success": "Yes", "by": "Mark", "notes": "Agreed.", "follow": ""}],
             "emails": [], "documents": []},
            {"id": "c2", "name": "Doe Example", "phone": "", "email": "doe@example.com", "county": "Lea", "status": "Dead",
             "tags": ["do-not-contact"], "interests": [], "calls": [], "emails": [], "documents": []},
            {"id": "c3", "name": "Far Away Example", "county": "Yellowstone", "status": "Interested", "interests": [],
             "calls": [], "emails": [], "documents": []},
        ],
        "tagColors": {"hot-lead": 2}, "knownTags": [], "lastBy": "Mark",
        "email": {"from": "", "sig": "", "webhook": "", "steps": [{"day": 0, "subject": "Hi {{firstName}}", "body": "x"}]},
    }


class Env:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "deals.db")
        self.rules = Rules.load()
        self.file = Path(self.tmp.name) / "land-crm-data-export.json"
        self.file.write_text(json.dumps(backup()), encoding="utf-8")

    def close(self):
        self.store.close()
        self.tmp.cleanup()


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()

    def tearDown(self):
        self.env.close()

    def test_backup_imports_once_and_round_trips(self):
        r = crm.import_backup(self.env.store, self.env.file, ACTOR)
        self.assertEqual(r, {"added": 3, "changed": 0, "unchanged": 0, "removed": 0})
        self.assertEqual(crm.import_backup(self.env.store, self.env.file, ACTOR)["unchanged"], 3)
        out = crm.export_backup(self.env.store)
        self.assertEqual(sorted(c["id"] for c in out["customers"]), ["c1", "c2", "c3"])
        self.assertEqual(out["tagColors"], {"hot-lead": 2})
        self.assertEqual(out["customers"][0], backup()["customers"][0])  # stored exactly as the CRM holds it
        self.assertEqual(self.env.store.deal_ids(), [])  # CRM streams aren't deals
        self.assertEqual(self.env.store.verify(), [])

    def test_every_version_is_kept_and_deletes_are_recoverable(self):
        crm.import_backup(self.env.store, self.env.file, ACTOR)
        c = crm.customer(self.env.store, "c1")
        self.assertFalse(crm.save_customer(self.env.store, c, ACTOR))  # unchanged: no event
        self.assertTrue(crm.save_customer(self.env.store, {**c, "status": "Closed"}, ACTOR))
        self.assertTrue(crm.delete_customer(self.env.store, "c1", ACTOR))
        self.assertIsNone(crm.customer(self.env.store, "c1"))
        versions = [e.payload["customer"]["status"] for e in self.env.store.events("crm:c1") if e.type == "crm.customer.saved"]
        self.assertEqual(versions, ["Under Contract", "Closed"])
        with self.assertRaises(ValueError):
            crm.save_customer(self.env.store, {"id": "bad id!", "name": "X"}, ACTOR)
        with self.assertRaises(ValueError):
            crm.save_customer(self.env.store, {"id": "c9", "name": "X", "status": "Won"}, ACTOR)

    def test_replace_removes_owners_missing_from_the_file(self):
        crm.import_backup(self.env.store, self.env.file, ACTOR)
        data = backup()
        data["customers"] = data["customers"][:1]
        self.env.file.write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual(crm.import_backup(self.env.store, self.env.file, ACTOR, replace=True)["removed"], 2)


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        crm.import_backup(self.env.store, self.env.file, ACTOR)

    def tearDown(self):
        self.env.close()

    def test_interest_types_map_to_asset_kinds(self):
        self.assertEqual([crm.asset_kind(t) for t in ("Royalty", "Working Interest", "ORRI", "Overriding Royalty", "NPRI", "")],
                         ["mineral", "wellbore", "orri", "orri", "royalty", "mineral"])

    def test_owner_becomes_a_deal_and_intake_lists_what_is_missing(self):
        r = crm.start_deal(self.env.store, self.env.rules, "c1", ACTOR, "Example Minerals Fund LP", today=DAY0)
        st = fold(self.env.store.events(r["deal_id"]))
        self.assertEqual(r["deal_id"], "D-2026-c1")
        self.assertEqual(st.record["sellers"][0]["crm_id"], "c1")
        self.assertEqual([(a["kind"], a["state"], a["county"]) for a in st.assets],
                         [("mineral", "NM", "Eddy"), ("wellbore", "NM", "Eddy")])
        self.assertEqual(st.assets[0]["wells"][0]["name"], "EXAMPLE 15 FED 1H")
        codes = {t.code for t in st.tasks.values()}
        self.assertTrue({"CRM_HANDOFF_CONFIRM", "INTAKE_EFFECTIVE_DATE", "INTAKE_NO_AGREEMENT", "INTAKE_INTEREST",
                         "INTAKE_LAND_STATUS"} <= codes)
        confirm = next(t for t in st.tasks.values() if t.code == "CRM_HANDOFF_CONFIRM")
        self.assertEqual((confirm.severity, confirm.party), ("stop", "acquisitions"))
        self.assertIn("'Working Interest' was set up as a wellbore buy", confirm.requirement)
        self.assertIn("0.390625%", confirm.requirement)  # the exhibit decimal is shown, never used as title
        self.assertEqual(st.expected, {})
        self.assertTrue(Path(r["file"]).exists())
        self.assertEqual(crm.deals_by_customer(self.env.store, self.env.rules)["c1"][0]["deal_id"], "D-2026-c1")
        with self.assertRaises(ValueError):
            crm.start_deal(self.env.store, self.env.rules, "c1", ACTOR, "Example Minerals Fund LP", today=DAY0)

    def test_state_and_buyer_are_never_guessed(self):
        with self.assertRaises(ValueError):
            crm.start_deal(self.env.store, self.env.rules, "c3", ACTOR, "Example Minerals Fund LP", today=DAY0)
        with self.assertRaises(ValueError):
            crm.start_deal(self.env.store, self.env.rules, "c1", ACTOR, "  ", today=DAY0)
        r = crm.start_deal(self.env.store, self.env.rules, "c3", ACTOR, "Example Minerals Fund LP", state="TX", today=DAY0)
        self.assertEqual(fold(self.env.store.events(r["deal_id"])).assets[0]["state"], "TX")


class OutreachAndFilesTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        crm.import_backup(self.env.store, self.env.file, ACTOR)

    def tearDown(self):
        self.env.close()

    def test_outreach_goes_through_the_mailer_and_is_logged(self):
        sent = []

        class Fake:
            name = "fake"

            def send(self, email, deal_id, msg_id):
                sent.append((str(email["To"]), str(email["Subject"]), email["X-PTK-CRM-Customer"]))
                return "sent", "<id@test>"

        r = crm.send_outreach(self.env.store, ACTOR, "c1", {"to": "jane@example.com", "subject": "Hello", "body": "Hi", "step": 1},
                              transport=Fake())
        self.assertEqual(r["status"], "sent")
        self.assertIn("jane@example.com", sent[0][0])
        logged = [e for e in self.env.store.events("crm:c1") if e.type == "crm.email.delivered"]
        self.assertEqual(logged[0].payload["subject"], "Hello")
        with self.assertRaises(ValueError):  # Dead and do-not-contact
            crm.send_outreach(self.env.store, ACTOR, "c2", {"to": "doe@example.com", "subject": "x", "body": "x"}, transport=Fake())
        with self.assertRaises(ValueError):
            crm.send_outreach(self.env.store, ACTOR, "c1", {"to": "not-an-address", "subject": "x"}, transport=Fake())
        r = crm.send_outreach(self.env.store, ACTOR, "c1", {"to": "jane@example.com", "subject": "x", "body": "x"},
                              transport=FileTransport(self.env.store.home / "outbox"))
        self.assertEqual(r["status"], "exported")
        self.assertEqual(len(sent), 1)

    def test_documents_are_kept_beside_the_database(self):
        doc = crm.add_document(self.env.store, ACTOR, "c1", "deed.pdf", "application/pdf", b"%PDF-1.4 fictional")
        self.assertEqual((doc["name"], doc["type"], doc["size"]), ("deed.pdf", "application/pdf", 18))
        self.assertEqual(crm.file_path(self.env.store, doc["id"]).read_bytes(), b"%PDF-1.4 fictional")
        with self.assertRaises(ValueError):
            crm.add_document(self.env.store, ACTOR, "c1", "x.svg", "image/svg+xml", b"<svg/>")
        with self.assertRaises(KeyError):
            crm.file_path(self.env.store, "../deals.db")
        crm.remove_document(self.env.store, ACTOR, doc["id"])
        with self.assertRaises(KeyError):
            crm.file_path(self.env.store, doc["id"])
        self.assertTrue((crm.files_dir(self.env.store) / ".removed" / doc["id"]).exists())


class ServerTests(unittest.TestCase):
    def setUp(self):
        from http.server import ThreadingHTTPServer

        from ptk.deal.server import App, make_handler
        self.env = Env()
        crm.import_backup(self.env.store, self.env.file, ACTOR)
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        self.app = App(self.env.store, self.env.rules, ACTOR, self.port)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", self.port), make_handler(self.app))
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.port}"

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.env.close()

    def call(self, method, path, body=None, token=True, headers=None, raw=False):
        data = body if raw else (json.dumps(body).encode() if body is not None else None)
        h = {"X-PTK-Token": self.app.token} if token else {}
        if data is not None and not raw:
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, r.read(), dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read(), dict(e.headers)

    def test_crm_page_and_api(self):
        code, page, headers = self.call("GET", "/crm", token=False)
        self.assertEqual(code, 200)
        self.assertIn(self.app.token.encode(), page)
        self.assertNotIn(b"__PTK_TOKEN__", page)
        self.assertIn("connect-src 'self'", headers["Content-Security-Policy"])
        self.assertEqual(self.call("GET", "/api/crm/state", token=False)[0], 403)
        code, body, _ = self.call("GET", "/api/crm/state")
        state = json.loads(body)
        self.assertEqual(len(state["customers"]), 3)
        self.assertEqual(state["deals"], {})
        c1 = next(c for c in state["customers"] if c["id"] == "c1")
        c1["calls"].append({"date": "2026-10-11", "outcome": "Voicemail", "success": "No", "by": "Mark", "notes": "", "follow": ""})
        self.assertEqual(json.loads(self.call("PUT", "/api/crm/customers/c1", c1)[1]), {"saved": True})
        self.assertEqual(len(crm.customer(self.env.store, "c1")["calls"]), 2)
        self.assertEqual(self.call("PUT", "/api/crm/customers/c9", c1)[0], 400)  # id doesn't match the address
        self.assertEqual(json.loads(self.call("DELETE", "/api/crm/customers/c3")[1]), {"deleted": True})
        self.assertEqual(json.loads(self.call("PUT", "/api/crm/settings", {"lastBuyer": "Example Minerals Fund LP"})[1]),
                         {"saved": True})
        code, body, _ = self.call("POST", "/api/crm/customers/c1/deal", {"buyer": "Example Minerals Fund LP"})
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["deal"]["stage"], "intake")
        self.assertIn("c1", json.loads(self.call("GET", "/api/crm/state")[1])["deals"])
        code, body, _ = self.call("POST", "/api/crm/send", {"customerId": "c1", "to": "jane@example.com", "subject": "S", "body": "B", "step": 1})
        self.assertEqual(json.loads(body)["status"], "exported")  # no SMTP in tests: written, not sent
        self.assertEqual(self.call("POST", "/api/crm/send", {"customerId": "c2", "to": "doe@example.com", "subject": "S"})[0], 400)
        code, body, _ = self.call("POST", "/api/crm/customers/c1/documents", b"%PDF fictional", raw=True,
                                  headers={"Content-Type": "application/pdf", "X-Filename": "lease%20copy.pdf"})
        doc = json.loads(body)
        self.assertEqual(doc["name"], "lease copy.pdf")
        code, content, headers = self.call("GET", f"/files/{doc['id']}", token=False)
        self.assertEqual((code, content), (200, b"%PDF fictional"))
        self.assertIn("sandbox", headers["Content-Security-Policy"])
        self.assertEqual(json.loads(self.call("DELETE", f"/api/crm/files/{doc['id']}")[1]), {"removed": doc["id"]})
        self.assertEqual(self.call("GET", "/api/crm/nope")[0], 404)
        self.assertEqual(self.env.store.verify(), [])

    def test_guards(self):
        self.assertEqual(self.call("GET", "/crm", token=False, headers={"Host": "evil.example"})[0], 403)
        self.assertEqual(self.call("PUT", "/api/crm/settings", {}, headers={"Origin": "http://evil.example"})[0], 403)
        self.assertEqual(self.call("PUT", "/api/crm/settings", {}, token=False)[0], 403)
        req = urllib.request.Request(self.base + "/", method="GET")
        opener = urllib.request.build_opener(type("NoRedirect", (urllib.request.HTTPRedirectHandler,),
                                                  {"redirect_request": lambda *a, **k: None}))
        with self.assertRaises(urllib.error.HTTPError) as cm:
            opener.open(req)
        self.assertEqual((cm.exception.code, cm.exception.headers["Location"]), (302, "/crm"))


class CliTests(unittest.TestCase):
    def test_import_list_handoff_export(self):
        env = Env()
        try:
            def run(*args):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    code = main(["crm", *args, "--db", str(env.store.path), "--by", ACTOR, "--today", "2026-10-11"])
                return code, buf.getvalue()
            env.store.close()
            self.assertIn("3 added", run("import", str(env.file))[1])
            self.assertIn("Jane Example", run("list")[1])
            code, out = run("handoff", "c1", "--buyer", "Example Minerals Fund LP")
            self.assertEqual(code, 0)
            self.assertIn("Created D-2026-c1", out)
            self.assertEqual(run("handoff", "c3", "--buyer", "Example Minerals Fund LP")[0], 1)  # state needed
            out_file = Path(env.tmp.name) / "back.json"
            run("export", str(out_file))
            self.assertEqual(len(json.loads(out_file.read_text())["customers"]), 3)
            env.store = Store(env.store.path)
        finally:
            env.close()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
