"""Deal follow-up pipeline (ptk deal): intake -> verify -> curative -> closing -> record -> payment.

Every deal here is the fictional data in examples/deals/. Dates are pinned with
``today=`` so due dates and reminder timing don't depend on the calendar.
"""

import io
import json
import socket
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stdout
from datetime import date
from fractions import Fraction as F
from pathlib import Path

from ptk.cli import main
from ptk.deal import actions, outbox
from ptk.deal.drafts import build_package, draft_for_task, render
from ptk.deal.intake import intake_deal
from ptk.deal.record import load_deal, load_deals_csv, normalize
from ptk.deal.rules import Rules
from ptk.deal.state import fold, funds_gate, summary
from ptk.deal.store import Store
from ptk.deal.verify import verify_deal
from ptk.fracs import fraction_words, parse_fraction, read_fraction_text
from ptk.leasehold import LeaseEvent, load_lease_events, position_for, replay_leasehold

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
DEALS = EXAMPLES / "deals"
DAY0 = date(2026, 10, 11)
ACTOR = "Test Landman"


def by_code(st, code, asset=None):
    return [t for t in st.tasks.values() if t.code == code and (asset is None or t.asset_id == asset)]


class Env:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "deals.db")
        self.rules = Rules.load()

    def close(self):
        self.store.close()
        self.tmp.cleanup()

    def intake(self, name):
        record = load_deal(DEALS / name)
        intake_deal(self.store, self.rules, record, ACTOR, today=DAY0)
        return record["deal_id"]

    def state(self, deal_id):
        return fold(self.store.events(deal_id))


class FractionWordsTests(unittest.TestCase):
    def test_words_read_back_exactly(self):
        cases = {F(1, 2): "one-half", F(1, 4): "one-fourth", F(3, 16): "three-sixteenths", F(1, 64): "one sixty-fourth",
                 F(5, 32): "five thirty-seconds", F(2, 3): "two-thirds", F(13, 25): "thirteen twenty-fifths"}
        for value, words in cases.items():
            self.assertEqual(fraction_words(value), words)
            self.assertEqual(parse_fraction(words), value)
            self.assertTrue(read_fraction_text(f"{words} ({value})").consistent)

    def test_words_are_withheld_when_they_would_not_read_back(self):
        self.assertIsNone(fraction_words(F(1, 10**6)))  # past the ordinal table
        self.assertIsNone(fraction_words(F(1)))
        # hyphen groups keep long terms unambiguous, so these read back and are allowed
        self.assertEqual(parse_fraction(fraction_words(F(2113, 4000))), F(2113, 4000))
        for d in range(2, 200):
            for n in (1, 3, 7):
                v = F(n, d)
                if 0 < v < 1 and fraction_words(v):
                    self.assertEqual(parse_fraction(fraction_words(v)), v, v)


class LeaseholdTests(unittest.TestCase):
    def setUp(self):
        self.events = load_lease_events(DEALS / "lease_chain.csv")

    def test_nri_carries_the_orri_reserved_out_of_the_assignment(self):
        r = replay_leasehold(self.events, as_of="2026-08-01")
        lp = position_for(r, "NM-FEE-15")
        self.assertEqual(lp.wi("Example Permian Energy LLC"), 1)
        self.assertEqual(lp.nri("Example Permian Energy LLC"), F(3, 4))  # 1 - 1/5 royalty - 1/20 ORRI
        self.assertEqual(lp.orri_held("Example Original Lessee LLC"), F(1, 20))
        codes = {f.code for f in r.flags}
        self.assertIn("ORRI_REVIEW", codes)
        self.assertIn("LEASE_STATUS_REVIEW", codes)
        self.assertNotIn("NPRI_REVIEW", codes)

    def test_orri_follows_a_partial_assignment(self):
        events = [
            LeaseEvent(1, "L", "2010-01-01", "L1", "ALL", "root", None, ["A"], [F(1)], "all", royalty=F(1, 4)),
            LeaseEvent(2, "X", "2011-01-01", "L1", "ALL", "assign", "A", ["B"], [F(1)], "1/2"),
            LeaseEvent(3, "X", "2011-01-01", "L1", "ALL", "orri", "A", [], [], "", orri_value=F(1, 16)),
            LeaseEvent(4, "Y", "2012-01-01", "L1", "ALL", "assign", "B", ["C"], [F(1)], "1/2 of grantor"),
        ]
        lp = position_for(replay_leasehold(events), "L1")
        self.assertEqual(lp.nri("A"), F(1, 2) * F(3, 4))  # A kept an unburdened half
        self.assertEqual(lp.nri("C"), F(1, 4) * (F(3, 4) - F(1, 16)))  # the burden travelled with B's half
        self.assertEqual(lp.orri_held("A"), F(1, 16) * F(1, 2))

    def test_missing_royalty_is_flagged_not_assumed(self):
        events = [LeaseEvent(1, "L", "2010-01-01", "L1", "ALL", "root", None, ["A"], [F(1)], "all")]
        self.assertIn("ROYALTY_MISSING", {f.code for f in replay_leasehold(events).flags})


class StoreTests(unittest.TestCase):
    def test_log_is_append_only_and_tamper_evident(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "d.db"
            s = Store(path)
            s.append("D1", "note.added", {"text": "one"}, "me")
            s.append("D1", "note.added", {"text": "two"}, "me")
            self.assertEqual(s.verify(), [])
            with self.assertRaises(sqlite3.DatabaseError):
                s._db.execute("UPDATE events SET payload = '{}' WHERE id = 1")
            with self.assertRaises(sqlite3.DatabaseError):
                s._db.execute("DELETE FROM events WHERE id = 2")
            s.close()
            raw = sqlite3.connect(path)  # someone edits the file behind ptk's back
            raw.execute("DROP TRIGGER events_no_update")
            raw.execute("UPDATE events SET payload = '{\"text\":\"edited\"}' WHERE id = 1")
            raw.commit()
            raw.close()
            again = Store(path)
            self.assertTrue(any("match its hash" in p for p in again.verify()))
            again.close()


class IntakeTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()

    def tearDown(self):
        self.env.close()

    def test_texas_mineral_deal_opens_state_checklists_once_per_tract(self):
        st = self.env.state(self.env.intake("tx-mineral.json"))
        self.assertEqual(len(by_code(st, "TX-RUN-COUNTY")), 1)  # two assets, one tract, one county run
        self.assertEqual([t.asset_id for t in by_code(st, "TX-HOMESTEAD")], ["A1"])  # only Baker is married
        self.assertEqual(len(by_code(st, "ALL-W9")), 1)
        self.assertFalse([t for t in st.tasks.values() if t.code.startswith("INTAKE_")])
        self.assertTrue(summary(st, self.env.rules, DAY0)["stages"][0]["done"])

    def test_new_mexico_sheet_deal_goes_back_to_acquisitions(self):
        record = load_deals_csv(DEALS / "deals.csv")[0]
        intake_deal(self.env.store, self.env.rules, record, ACTOR, today=DAY0)
        st = self.env.state(record["deal_id"])
        fed = by_code(st, "NM-FEDERAL-MINERALS")
        self.assertEqual((fed[0].severity, fed[0].party, fed[0].stage), ("stop", "acquisitions", "intake"))
        self.assertEqual(by_code(st, "INTAKE_SPOUSE_MISSING")[0].severity, "stop")  # New Mexico community property
        self.assertEqual(summary(st, self.env.rules, DAY0)["stage"], "intake")

    def test_content_problems_are_tasks_but_structure_problems_are_errors(self):
        raw = json.loads((DEALS / "tx-mineral.json").read_text())
        raw["deal_id"] = "D-BAD"
        raw["assets"][0]["county"] = "Eddy"  # a New Mexico county on a Texas asset
        raw["assets"][0]["nma"] = "300"  # 1/2 x 640 is 320
        raw["documents"] = []
        record = normalize(raw, DEALS)
        intake_deal(self.env.store, self.env.rules, record, ACTOR, today=DAY0)
        codes = {t.code for t in self.env.state("D-BAD").tasks.values()}
        self.assertTrue({"INTAKE_COUNTY_MISMATCH", "INTAKE_NMA_MISMATCH", "INTAKE_NO_AGREEMENT"} <= codes)
        with self.assertRaises(ValueError):
            normalize({**raw, "assets": [{**raw["assets"][0], "kind": "surface"}]}, DEALS)
        with self.assertRaises(ValueError):
            intake_deal(self.env.store, self.env.rules, record, ACTOR, today=DAY0)  # exists: needs a reason


class VerifyTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()

    def tearDown(self):
        self.env.close()

    def test_texas_seller_shortfall_and_chain_relevance(self):
        d = self.env.intake("tx-mineral.json")
        r = verify_deal(self.env.store, self.env.rules, d, ACTOR, today=DAY0)
        self.assertEqual(r["assets"]["A1"]["status"], "OK")
        self.assertEqual(r["assets"]["A2"]["status"], "SHORTFALL")
        self.assertEqual((r["assets"]["A2"]["owned"], r["assets"]["A2"]["sold"]), ("1/6", "1/4"))
        st = self.env.state(d)
        # the NPRI reserved out of Baker's half burdens Baker's minerals, not Carter's
        self.assertEqual([t.asset_id for t in by_code(st, "NPRI_REVIEW")], ["A1"])
        self.assertEqual([t.asset_id for t in by_code(st, "NPRI_NOT_RATIFIED")], ["A1"])
        self.assertEqual(by_code(st, "NPRI_NOT_RATIFIED")[0].party_name, "Adams Heir Three")
        # the over-conveyance into Carter is in Carter's chain only; the wild deed is in nobody's
        self.assertEqual([t.asset_id for t in by_code(st, "DUHIG_CANDIDATE")], ["A2"])
        self.assertFalse(by_code(st, "CHAIN_GAP"))
        self.assertTrue(any("CHAIN_GAP" in n for n in r["assets"]["A1"]["notes"]))
        # post-closing deck: the buyer steps into each seller's royalty
        exp = {e["asset_id"]: e for e in st.expected.values()}
        self.assertEqual((exp["A1"]["decimal"], exp["A1"]["decimal_display"]), ("9/200", "0.04500000"))
        self.assertEqual(exp["A2"]["decimal"], "3/200")
        needed, why = actions_opinion(st, self.env.rules)
        self.assertTrue(needed)
        self.assertIn("DUHIG_CANDIDATE", why)

    def test_new_mexico_wellbore_nri_and_later_assignment(self):
        d = self.env.intake("nm-leasehold.json")
        r = verify_deal(self.env.store, self.env.rules, d, ACTOR, today=DAY0)
        self.assertEqual(r["assets"]["A1"]["status"], "OK")
        a2 = r["assets"]["A2"]
        self.assertEqual(a2["wells"][0]["owned_nri"], "19/32")  # 1/2 x 3/4 + 1/2 x (1/2 x 7/8)
        st = self.env.state(d)
        self.assertEqual(len(by_code(st, "NRI_SHORTFALL")), 1)
        later = by_code(st, "SELLER_CONVEYED_AFTER_EFFECTIVE")
        self.assertTrue(later and later[0].requires_attorney and "Eddy OR Bk 1300 Pg 5" in later[0].message)
        self.assertEqual(len(by_code(st, "LEASE_STATUS_REVIEW")), 3)
        self.assertEqual(by_code(st, "NM-BLM-TRANSFER")[0].stage, "record")

    def test_rerunning_verify_never_duplicates_and_fixing_data_resolves(self):
        d = self.env.intake("tx-mineral.json")
        verify_deal(self.env.store, self.env.rules, d, ACTOR, today=DAY0)
        n = len(self.env.state(d).tasks)
        verify_deal(self.env.store, self.env.rules, d, ACTOR, today=DAY0)
        self.assertEqual(len(self.env.state(d).tasks), n)
        raw = json.loads((DEALS / "tx-mineral.json").read_text())
        raw["assets"][1]["interest"], raw["assets"][1]["nma"] = "1/6", None
        intake_deal(self.env.store, self.env.rules, normalize(raw, DEALS), ACTOR, reason="Carter sells what title shows", today=DAY0)
        verify_deal(self.env.store, self.env.rules, d, ACTOR, today=DAY0)
        st = self.env.state(d)
        self.assertEqual(by_code(st, "SELLER_SHORTFALL")[0].status, "resolved")
        self.assertEqual(st.verify["A2"]["result"]["status"], "OK")


def actions_opinion(st, rules):
    from ptk.deal.state import opinion_required
    return opinion_required(st, rules)


class CurativeAndClosingTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.d = self.env.intake("tx-mineral.json")
        verify_deal(self.env.store, self.env.rules, self.d, ACTOR, today=DAY0)

    def tearDown(self):
        self.env.close()

    def test_legal_questions_need_the_attorney(self):
        st = self.env.state(self.d)
        npri = by_code(st, "NPRI_REVIEW")[0].task_id
        with self.assertRaises(ValueError):
            actions.update_task(self.env.store, self.d, npri, ACTOR, "cleared")
        with self.assertRaises(ValueError):
            actions.update_task(self.env.store, self.d, npri, ACTOR, "waived", reason="looks fine")
        with self.assertRaises(ValueError):
            actions.update_task(self.env.store, self.d, npri, ACTOR, "resolved")
        r = actions.record_opinion(self.env.store, self.env.rules, self.d, ACTOR, "Example Examining Attorney",
                                   "2026-10-20", covers=["all"], requirements=["Record an affidavit of non-production"], today=DAY0)
        st = self.env.state(self.d)
        self.assertEqual(st.tasks[npri].status, "cleared")
        self.assertEqual(st.tasks[r["requirements"][0]].code, "OPINION_REQUIREMENT")
        self.assertFalse([t for t in st.tasks.values() if t.requires_attorney and not t.closed])

    def test_funds_stay_held_until_the_gate_clears(self):
        with self.assertRaises(ValueError):
            actions.closing(self.env.store, self.env.rules, self.d, ACTOR, "release")
        actions.record_opinion(self.env.store, self.env.rules, self.d, ACTOR, "Example Examining Attorney", covers=["all"], today=DAY0)
        st = self.env.state(self.d)
        short = by_code(st, "SELLER_SHORTFALL")[0].task_id
        actions.update_task(self.env.store, self.d, short, ACTOR, "waived", reason="price adjusted")
        for t in self.env.state(self.d).open_tasks():
            if t.stage in ("verify", "curative", "closing"):
                actions.update_task(self.env.store, self.d, t.task_id, ACTOR, "cleared")
        build_package(self.env.store, self.env.rules, self.d, ACTOR, today=DAY0)
        self.assertEqual(funds_gate(self.env.state(self.d), self.env.rules), ["the package isn't approved", "the package isn't signed"])
        actions.closing(self.env.store, self.env.rules, self.d, ACTOR, "approve")
        actions.closing(self.env.store, self.env.rules, self.d, ACTOR, "sign", "2026-10-28")
        actions.closing(self.env.store, self.env.rules, self.d, ACTOR, "release", "2026-10-29")
        self.assertIn("released", self.env.state(self.d).package)

    def test_package_documents(self):
        r = build_package(self.env.store, self.env.rules, self.d, ACTOR, today=DAY0)
        out = Path(r["dir"])
        deeds = sorted(out.glob("*-TX-mineral-deed-*.md"))
        self.assertEqual([p.name.split("-deed-")[1] for p in deeds], ["baker-example.md", "carter-example.md"])
        baker = deeds[0].read_text(encoding="utf-8")
        self.assertIn("DRAFT FOR ATTORNEY REVIEW", baker)
        self.assertIn("an undivided one-half (1/2) interest in and to the oil, gas and other minerals, being 320.0000 net mineral acres", baker)
        self.assertIn("STATE OF TEXAS", baker)
        self.assertNotIn("${", baker)
        carter = deeds[1].read_text(encoding="utf-8")
        self.assertIn("**Title note:** [seller held 1/6 but sold 1/4", carter)
        rows = (out / "closing-statement.csv").read_text(encoding="utf-8")
        self.assertIn("PROPOSED title adjustment, A2\",-48000.00", rows)
        self.assertIn("Due seller if proposed adjustments are approved,384000.00", rows)
        manifest = json.loads((out / "manifest.json").read_text())
        self.assertTrue(all(len(f["sha256"]) == 64 for f in manifest["files"]))
        with self.assertRaises(ValueError):
            render("mineral_deed", {"banner": "x"})  # a missing field is an error, never a hole

    def test_new_mexico_spouse_signs(self):
        raw = json.loads((DEALS / "nm-leasehold.json").read_text())
        raw["deal_id"] = "D-NM-SPOUSE"
        raw["sellers"] = [{"id": "S1", "name": "Jane Example", "marital_status": "married", "spouse": "John Example",
                           "email": "jane@example.com"}]
        intake_deal(self.env.store, self.env.rules, normalize(raw, DEALS), ACTOR, today=DAY0)
        r = build_package(self.env.store, self.env.rules, "D-NM-SPOUSE", ACTOR, today=DAY0)
        text = next(Path(r["dir"]).glob("*-NM-assignment-*.md")).read_text(encoding="utf-8")
        self.assertIn("Jane Example and spouse, John Example", text)
        self.assertIn("STATE OF NEW MEXICO", text)
        self.assertEqual(text.count("This instrument was acknowledged"), 2)
        self.assertTrue(by_code(self.env.state("D-NM-SPOUSE"), "NM-SPOUSAL-JOINDER"))

    def test_curative_draft(self):
        tid = by_code(self.env.state(self.d), "NPRI_NOT_RATIFIED")[0].task_id
        r = draft_for_task(self.env.store, self.d, tid, ACTOR)
        text = Path(r["file"]).read_text(encoding="utf-8")
        self.assertIn("RATIFICATION OF POOLED UNIT", text)
        self.assertIn("Adams Heir Three", text)
        self.assertGreater(r["blanks"], 0)


class MessagingTests(unittest.TestCase):
    """The user's choice: routine reminders go out on their own; everything else waits for a person."""

    def setUp(self):
        self.env = Env()
        self.d = self.env.intake("tx-mineral.json")
        verify_deal(self.env.store, self.env.rules, self.d, ACTOR, today=DAY0)
        self.st = self.env.state(self.d)

    def tearDown(self):
        self.env.close()

    def _deliver(self, msg_id, on="2026-10-12"):
        outbox.approve(self.env.store, self.d, msg_id, ACTOR)
        outbox.mark_mailed(self.env.store, self.d, msg_id, ACTOR, on)

    def test_first_contact_is_never_automatic(self):
        w9 = by_code(self.st, "ALL-W9")[0].task_id
        r = outbox.request_for_task(self.env.store, self.env.rules, self.d, w9, ACTOR)
        self.assertEqual(len(r), 2)  # both sellers
        self.assertTrue(all(not m["auto_approved"] for m in r))
        self.assertTrue(all(m["category"] == "substantive" for m in r))

    def test_routine_reminder_auto_approves_only_after_an_approved_contact(self):
        w9 = by_code(self.st, "ALL-W9")[0].task_id
        first = outbox.request_for_task(self.env.store, self.env.rules, self.d, w9, ACTOR, to_name="Baker Example")[0]
        self._deliver(first["msg_id"])
        self.assertEqual(self.env.state(self.d).tasks[w9].status, "requested")
        acts = outbox.run_reminders(self.env.store, self.env.rules, ACTOR, date(2026, 10, 20))
        self.assertEqual(acts, [])  # not due yet (14-day cadence)
        acts = outbox.run_reminders(self.env.store, self.env.rules, ACTOR, date(2026, 10, 27))
        self.assertEqual(len(acts), 1)
        self.assertTrue(acts[0]["auto_approved"])
        m = self.env.state(self.d).messages[acts[0]["msg_id"]]
        self.assertEqual((m.template, m.approved_by), ("document_reminder", "ptk:auto-routine"))

    def test_mail_and_do_not_contact_are_never_automatic(self):
        tid = by_code(self.st, "NPRI_NOT_RATIFIED")[0].task_id  # Adams Heir Three: mailing address only
        first = outbox.request_for_task(self.env.store, self.env.rules, self.d, tid, ACTOR)[0]
        self.assertEqual(first["channel"], "mail")
        self._deliver(first["msg_id"])
        acts = outbox.run_reminders(self.env.store, self.env.rules, ACTOR, date(2026, 10, 27))
        self.assertFalse(acts[0]["auto_approved"])
        self.assertIn("email only", acts[0]["reason"])

    def test_reminder_limit_then_a_person_calls(self):
        w9 = by_code(self.st, "ALL-W9")[0].task_id
        first = outbox.request_for_task(self.env.store, self.env.rules, self.d, w9, ACTOR, to_name="Carter Example")[0]
        self._deliver(first["msg_id"], "2026-10-12")
        day = date(2026, 10, 12)
        for _ in range(3):
            day = date.fromordinal(day.toordinal() + 14)
            acts = outbox.run_reminders(self.env.store, self.env.rules, ACTOR, day)
            outbox.mark_mailed(self.env.store, self.d, acts[0]["msg_id"], ACTOR, day.isoformat())
        day = date.fromordinal(day.toordinal() + 14)
        acts = outbox.run_reminders(self.env.store, self.env.rules, ACTOR, day)
        self.assertEqual(acts[0]["action"], "no_response")
        self.assertEqual(self.env.state(self.d).tasks[acts[0]["opened"][0]].code, "NO_RESPONSE")

    def test_sending_uses_the_transport_and_withdraws_stale_reminders(self):
        sent = []

        class Fake:
            name = "fake"

            def send(self, email, deal_id, msg_id):
                sent.append((str(email["To"]), str(email["Subject"])))
                return "sent", "<id@test>"

        w9 = by_code(self.st, "ALL-W9")[0].task_id
        first = outbox.request_for_task(self.env.store, self.env.rules, self.d, w9, ACTOR, to_name="Baker Example")[0]
        outbox.approve(self.env.store, self.d, first["msg_id"], ACTOR)
        res = outbox.send_approved(self.env.store, self.env.rules, ACTOR, transport=Fake())
        self.assertEqual(len(sent), 1)
        self.assertIn("baker.example@example.com", sent[0][0])
        self.assertTrue(res[0]["delivered"])
        acts = outbox.run_reminders(self.env.store, self.env.rules, ACTOR, date.fromordinal(date.today().toordinal() + 15))
        actions.update_task(self.env.store, self.d, w9, ACTOR, "cleared", note="W-9s in")
        self.assertEqual(self.env.state(self.d).messages[acts[0]["msg_id"]].status, "rejected")
        self.assertEqual(outbox.send_approved(self.env.store, self.env.rules, ACTOR, transport=Fake()), [])
        self.assertEqual(len(sent), 1)

    def test_without_smtp_messages_are_exported_not_sent(self):
        w9 = by_code(self.st, "ALL-W9")[0].task_id
        first = outbox.request_for_task(self.env.store, self.env.rules, self.d, w9, ACTOR, to_name="Baker Example")[0]
        outbox.approve(self.env.store, self.d, first["msg_id"], ACTOR)
        res = outbox.send_approved(self.env.store, self.env.rules, ACTOR, transport=outbox.FileTransport(self.env.store.home / "outbox"))
        self.assertTrue(res[0]["result"].startswith("exported"))
        self.assertEqual(self.env.state(self.d).messages[first["msg_id"]].status, "exported")
        self.assertTrue((self.env.store.home / "outbox" / f"{self.d}-{first['msg_id']}.eml").exists())


class RecordAndPayTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.d = self.env.intake("tx-mineral.json")
        verify_deal(self.env.store, self.env.rules, self.d, ACTOR, today=DAY0)

    def tearDown(self):
        self.env.close()

    def test_notice_waits_for_recording_and_names_every_seller(self):
        self.assertIn("held", actions.notify(self.env.store, self.env.rules, self.d, ACTOR, today=DAY0)[0]["result"])
        rec = actions.log_recording(self.env.store, self.d, ACTOR, "Reeves", "2026-11-03", instrument_no="2026-004321")
        self.assertEqual(rec["image_key"], "TX-REEVES-OPR-2026-004321.pdf")
        with self.assertRaises(ValueError):
            actions.log_recording(self.env.store, self.d, ACTOR, "Lea", "2026-11-03", instrument_no="1")
        r = actions.notify(self.env.store, self.env.rules, self.d, ACTOR, today=DAY0)
        m = self.env.state(self.d).messages[r[0]["msg_id"]]
        self.assertEqual(m.category, "substantive")
        self.assertIn("Baker Example and Carter Example", m.body)
        self.assertIn("RI 0.06000000 in Example Unit 12-13 1H", m.body)  # one total per well
        self.assertTrue(by_code(self.env.state(self.d), "DIVISION_ORDER_PENDING"))

    def test_division_order_and_statement_reconcile_against_title(self):
        bad = actions.division_order(self.env.store, self.env.rules, self.d, ACTOR, "Example Operating LLC", "0.05500000",
                                     "Example Unit 12-13 1H", "RI", "2026-12-01", today=DAY0)
        self.assertFalse(bad["matches"])
        st = self.env.state(self.d)
        self.assertEqual(by_code(st, "DO_DECIMAL_MISMATCH")[0].severity, "stop")
        self.assertTrue(any(m.template == "decimal_query" and m.status == "draft" for m in st.messages.values()))
        r = actions.reconcile(self.env.store, self.env.rules, self.d, ACTOR, DEALS / "statement.csv", today=DAY0)
        self.assertEqual((r["matched"], r["skipped_other_owners"], r["mismatches"]), (1, 2, []))
        self.assertEqual(r["name_variants"], ["EXAMPLE MINERALS FUND, L.P."])
        self.assertTrue(by_code(self.env.state(self.d), "PAYEE_NAME_VARIANT"))

    def test_payment_overdue_after_the_watch_window(self):
        actions.log_recording(self.env.store, self.d, ACTOR, "Reeves", "2026-11-03", instrument_no="2026-004321")
        r = actions.notify(self.env.store, self.env.rules, self.d, ACTOR, today=DAY0)
        outbox.approve(self.env.store, self.d, r[0]["msg_id"], ACTOR)
        outbox.mark_mailed(self.env.store, self.d, r[0]["msg_id"], ACTOR, "2026-11-04")
        self.assertFalse([a for a in outbox.run_reminders(self.env.store, self.env.rules, ACTOR, date(2027, 2, 1))
                          if a["action"] == "payment_overdue"])
        acts = [a for a in outbox.run_reminders(self.env.store, self.env.rules, ACTOR, date(2027, 3, 10))
                if a["action"] == "payment_overdue"]
        self.assertEqual(len(acts), 1)
        self.assertTrue(acts[0]["auto_approved"])  # routine, and the operator already got an approved notice
        actions.payment(self.env.store, self.env.rules, self.d, ACTOR, "Example Operating LLC", "2027-03-15", "1000.00",
                        "0.06", "Example Unit 12-13 1H", "RI", today=DAY0)
        st = self.env.state(self.d)
        self.assertEqual(by_code(st, "PAYMENT_OVERDUE")[0].status, "resolved")
        self.assertEqual(by_code(st, "DIVISION_ORDER_PENDING")[0].status, "cleared")
        self.assertTrue(summary(st, self.env.rules, DAY0)["stages"][5]["done"])  # one check pays both sellers' shares


class ReviewRegressionTests(unittest.TestCase):
    """One test per defect found in the independent code review (HANDOFF section 9)."""

    def setUp(self):
        self.env = Env()

    def tearDown(self):
        self.env.close()

    def _tx(self):
        d = self.env.intake("tx-mineral.json")
        verify_deal(self.env.store, self.env.rules, d, ACTOR, today=DAY0)
        return d

    def _raw(self, name):
        return json.loads((DEALS / name).read_text())

    def test_amending_a_deal_invalidates_its_verification(self):
        d = self._tx()
        raw = self._raw("tx-mineral.json")
        raw["assets"][0]["interest"], raw["assets"][0]["nma"] = "1", None  # Baker now "sells" everything
        intake_deal(self.env.store, self.env.rules, normalize(raw, DEALS), ACTOR, reason="price renegotiated", today=DAY0)
        st = self.env.state(d)
        self.assertNotIn("A1", st.verify)
        self.assertIn("A1 title isn't verified", funds_gate(st, self.env.rules))
        raw["assets"] = raw["assets"][:1]  # drop Carter's asset
        intake_deal(self.env.store, self.env.rules, normalize(raw, DEALS), ACTOR, reason="Carter withdrew", today=DAY0)
        st = self.env.state(d)
        self.assertTrue(all(t.status == "resolved" for t in st.tasks.values() if t.asset_id == "A2" and t.source == "verify"))
        self.assertFalse([e for e in st.expected.values() if e["asset_id"] == "A2"])

    def test_an_opinion_clears_only_the_tasks_it_covers(self):
        d = self._tx()
        st = self.env.state(d)
        npri, dbl = by_code(st, "NPRI_REVIEW")[0].task_id, by_code(st, "DOUBLE_FRACTION")[0].task_id
        actions.record_opinion(self.env.store, self.env.rules, d, ACTOR, "Example Examining Attorney", covers=[npri], today=DAY0)
        with self.assertRaises(ValueError):
            actions.update_task(self.env.store, d, dbl, ACTOR, "cleared", opinion="OP1")

    def _deliver_request(self, d, to="Baker Example", on="2026-10-12"):
        w9 = by_code(self.env.state(d), "ALL-W9")[0].task_id
        m = outbox.request_for_task(self.env.store, self.env.rules, d, w9, ACTOR, to_name=to)[0]["msg_id"]
        outbox.approve(self.env.store, d, m, ACTOR)
        outbox.mark_mailed(self.env.store, d, m, ACTOR, on)
        return w9

    def test_a_reminder_a_person_rejected_stays_rejected(self):
        d = self._tx()
        self._deliver_request(d)
        first = outbox.run_reminders(self.env.store, self.env.rules, ACTOR, date(2026, 10, 27))
        outbox.reject(self.env.store, d, first[0]["msg_id"], ACTOR, "spoke to Baker by phone")
        self.assertEqual(outbox.run_reminders(self.env.store, self.env.rules, ACTOR, date(2026, 10, 28)), [])
        self.assertEqual(outbox.run_reminders(self.env.store, self.env.rules, ACTOR, date(2026, 12, 28)), [])

    def test_a_received_task_gets_no_reminders(self):
        d = self._tx()
        w9 = self._deliver_request(d)
        actions.update_task(self.env.store, d, w9, ACTOR, "received", note="W-9 arrived; checking it")
        self.assertEqual(outbox.run_reminders(self.env.store, self.env.rules, ACTOR, date(2026, 10, 27)), [])

    def test_each_recipient_on_a_task_gets_reminders(self):
        d = self._tx()
        self._deliver_request(d, "Baker Example")
        self._deliver_request(d, "Carter Example")
        acts = outbox.run_reminders(self.env.store, self.env.rules, ACTOR, date(2026, 10, 27))
        self.assertEqual(sorted(self.env.state(d).messages[a["msg_id"]].to_name for a in acts), ["Baker Example", "Carter Example"])

    def test_two_sends_at_once_deliver_once_and_ids_never_collide(self):
        d = self._tx()
        w9 = by_code(self.env.state(d), "ALL-W9")[0].task_id
        m = outbox.request_for_task(self.env.store, self.env.rules, d, w9, ACTOR, to_name="Baker Example")[0]["msg_id"]
        outbox.approve(self.env.store, d, m, ACTOR)
        sent, lock = [], threading.Lock()

        class Slow:
            name = "slow"

            def send(self, email, deal_id, msg_id):
                import time
                time.sleep(0.2)
                with lock:
                    sent.append(msg_id)
                return "sent", "<id@test>"

        other = Store(self.env.store.path)  # a second process on the same file
        try:
            threads = [threading.Thread(target=outbox.send_approved, args=(s, self.env.rules, ACTOR, Slow()))
                       for s in (self.env.store, other, self.env.store)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertEqual(sent, [m])
            ids, errors = [], []

            def draft(store):
                try:
                    for _ in range(5):
                        ids.append(outbox.draft_message(store, self.env.rules, d, ACTOR, "document_request", "Carter Example", w9)["msg_id"])
                except Exception as e:  # pragma: no cover - surfaced below
                    errors.append(e)
            threads = [threading.Thread(target=draft, args=(s,)) for s in (self.env.store, other)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertEqual(errors, [])
            self.assertEqual(len(set(ids)), 10)
            self.assertEqual(len(self.env.state(d).messages), 11)
            self.assertEqual(self.env.store.verify(), [])
        finally:
            other.close()

    def test_no_redraft_or_second_release_after_funds_go_out(self):
        d = self._tx()
        actions.record_opinion(self.env.store, self.env.rules, d, ACTOR, "Example Examining Attorney", covers=["all"], today=DAY0)
        for t in self.env.state(d).open_tasks():
            if t.code == "SELLER_SHORTFALL":
                actions.update_task(self.env.store, d, t.task_id, ACTOR, "waived", reason="price adjusted")
            elif t.stage in ("verify", "curative", "closing"):
                actions.update_task(self.env.store, d, t.task_id, ACTOR, "cleared")
        build_package(self.env.store, self.env.rules, d, ACTOR, today=DAY0)
        for step in ("approve", "sign", "release"):
            actions.closing(self.env.store, self.env.rules, d, ACTOR, step, "2026-10-29")
        with self.assertRaises(ValueError):
            build_package(self.env.store, self.env.rules, d, ACTOR, reason="fix a typo", today=DAY0)
        with self.assertRaises(ValueError):
            actions.closing(self.env.store, self.env.rules, d, ACTOR, "release")
        self.assertIn("released", self.env.state(d).package)

    def test_a_missing_lease_royalty_never_becomes_an_nri(self):
        chain = Path(self.env.tmp.name) / "chain.csv"
        chain.write_text("seq,instrument,recorded,lease,depth,kind,assignor,assignees,shares,interest,warranty,orri_value,"
                         "orri_burdens,royalty,notes\n1,Lease,2020-01-01,LX,ALL,root,,Seller Co LLC,,all,,,,,FICTIONAL\n")
        raw = {"deal_id": "D-NOROY", "buyer": "Example Minerals Fund LP", "effective_date": "2026-08-01", "price": "1",
               "sellers": [{"id": "S1", "name": "Seller Co LLC", "email": "s@example.com"}],
               "assets": [{"id": "A1", "kind": "leasehold", "state": "NM", "county": "Eddy", "land_status": "fee",
                           "legal_description": "NW/4 of Section 10, T23S, R31E, NMPM", "lease": {"id": "LX"},
                           "wi": "1", "nri": "3/4", "title": {"lease_chain": str(chain)},
                           "wells": [{"api": "30-015-00002", "name": "X 1H", "operator": "Example Operating LLC"}]}],
               "documents": [{"type": "purchase_agreement"}]}
        intake_deal(self.env.store, self.env.rules, normalize(raw, self.env.tmp.name), ACTOR, today=DAY0)
        verify_deal(self.env.store, self.env.rules, "D-NOROY", ACTOR, today=DAY0)
        st = self.env.state("D-NOROY")
        self.assertEqual(st.expected, {})
        self.assertTrue(by_code(st, "ROYALTY_MISSING"))
        self.assertFalse(by_code(st, "NRI_SHORTFALL"))
        self.assertTrue(any("ROYALTY_MISSING" in g for g in funds_gate(st, self.env.rules)))  # it holds the funds

    def test_a_wellbore_buy_without_wells_and_an_orri_without_a_lease_are_reported(self):
        raw = self._raw("nm-leasehold.json")
        raw["assets"][1]["wells"] = []
        raw["assets"].append({"id": "A3", "kind": "orri", "state": "NM", "county": "Eddy", "land_status": "fee",
                              "interest": "1/40", "title": {"lease_chain": "lease_chain.csv"}})
        intake_deal(self.env.store, self.env.rules, normalize(raw, DEALS), ACTOR, today=DAY0)
        r = verify_deal(self.env.store, self.env.rules, "D-2026-002", ACTOR, today=DAY0)
        self.assertEqual(r["assets"]["A2"]["status"], "NO_TITLE")
        self.assertEqual(r["assets"]["A3"]["status"], "NO_TITLE")
        st = self.env.state("D-2026-002")
        self.assertTrue(by_code(st, "WELLBORE_NO_WELLS") and by_code(st, "LEASE_NOT_NAMED"))

    def test_a_lease_buy_whose_well_crosses_another_lease_still_verifies(self):
        raw = self._raw("nm-leasehold.json")
        raw["assets"][0]["wells"] = [{"api": "30-015-00003", "name": "Cross 1H", "operator": "Example Operating LLC",
                                      "payor": "Example Purchasing LLC", "participation": {"NM-FEE-10": 1, "NM-ELSEWHERE": 1}}]
        intake_deal(self.env.store, self.env.rules, normalize(raw, DEALS), ACTOR, today=DAY0)
        r = verify_deal(self.env.store, self.env.rules, "D-2026-002", ACTOR, today=DAY0)
        self.assertEqual(r["assets"]["A1"]["status"], "OK")
        exp = [e for e in self.env.state("D-2026-002").expected.values() if e["asset_id"] == "A1"]
        self.assertEqual(exp[0]["decimal"], "3/8")  # NRI 3/4 x half of the well

    def test_mark_mailed_dates_are_read_strictly(self):
        d = self._tx()
        w9 = by_code(self.env.state(d), "ALL-W9")[0].task_id
        m = outbox.request_for_task(self.env.store, self.env.rules, d, w9, ACTOR, to_name="Baker Example")[0]["msg_id"]
        outbox.approve(self.env.store, d, m, ACTOR)
        with self.assertRaises(ValueError):
            outbox.mark_mailed(self.env.store, d, m, ACTOR, "10/12/26")
        outbox.mark_mailed(self.env.store, d, m, ACTOR, "10/12/2026")
        self.assertTrue(self.env.state(d).messages[m].sent_at.startswith("2026-10-12"))
        self.assertEqual(len(outbox.run_reminders(self.env.store, self.env.rules, ACTOR, date(2026, 10, 27))), 1)

    def test_co_sellers_on_one_lease_add_the_buyer_once(self):
        raw = self._raw("tx-mineral.json")
        raw["deal_id"] = "D-HEIRS"
        raw["sellers"] = [{"id": "S1", "name": "Adams Heir Two", "marital_status": "single", "email": "two@example.com"},
                          {"id": "S2", "name": "ADAMS HEIR THREE", "marital_status": "single", "email": "three@example.com"}]
        raw["assets"] = [{**raw["assets"][1], "id": "A1", "interest": "1/3", "nma": None, "seller": None, "sellers": ["S1", "S2"]}]
        intake_deal(self.env.store, self.env.rules, normalize(raw, DEALS), ACTOR, today=DAY0)
        verify_deal(self.env.store, self.env.rules, "D-HEIRS", ACTOR, today=DAY0)
        st = self.env.state("D-HEIRS")
        self.assertFalse(by_code(st, "DOUBLE_LEASE"))
        self.assertEqual([e["decimal"] for e in st.expected.values()], ["3/100"])  # 1/3 x 3/16 x 12/25

    def test_a_mismatched_division_order_keeps_waiting(self):
        d = self._tx()
        actions.log_recording(self.env.store, d, ACTOR, "Reeves", "2026-11-03", instrument_no="2026-004321")
        actions.notify(self.env.store, self.env.rules, d, ACTOR, today=DAY0)
        actions.division_order(self.env.store, self.env.rules, d, ACTOR, "Example Operating LLC", "0.05",
                               "Example Unit 12-13 1H", "RI", today=DAY0)
        self.assertEqual(by_code(self.env.state(d), "DIVISION_ORDER_PENDING")[0].status, "received")

    def test_a_legal_question_at_any_stage_holds_funds(self):
        d = self._tx()
        actions.record_opinion(self.env.store, self.env.rules, d, ACTOR, "Example Examining Attorney", covers=["all"], today=DAY0)
        tid = actions.add_task(self.env.store, self.env.rules, d, ACTOR, "Does the BLM approval affect the effective date?",
                               stage="record", requires_attorney=True, today=DAY0)
        self.assertTrue(any(g.startswith(tid) for g in funds_gate(self.env.state(d), self.env.rules)))

    def test_the_dashboard_only_listens_on_this_computer(self):
        from ptk.deal.server import App, serve
        with self.assertRaises(ValueError):
            serve(self.env.store, self.env.rules, ACTOR, host="0.0.0.0", port=1)
        app = App(self.env.store, self.env.rules, ACTOR, 8780)
        target, flash, err = app.post("/deal/X/task", {})
        self.assertTrue(err)


class CliAndDashboardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "deals.db")

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *args):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(["deal", *args, "--db", self.db, "--by", ACTOR, "--today", "2026-10-11"])
        return code, buf.getvalue()

    def test_commands_and_exit_codes(self):
        code, out = self.run_cli("intake", str(DEALS / "tx-mineral.json"))
        self.assertEqual(code, 0)
        self.assertIn("Created D-2026-001", out)
        code, out = self.run_cli("verify", "D-2026-001")
        self.assertEqual(code, 1)  # Carter's shortfall
        self.assertIn("A2: SHORTFALL", out)
        code, out = self.run_cli("status", "D-2026-001", "--json")
        self.assertEqual(json.loads(out)["stage"], "verify")
        self.assertEqual(self.run_cli("closing", "D-2026-001", "release")[0], 1)
        self.assertEqual(self.run_cli("status", "NOPE")[0], 1)
        code, out = self.run_cli("verify-log")
        self.assertEqual(code, 0)
        self.assertIn("hash chain intact", out)

    def test_dashboard_guards_and_renders(self):
        store = Store(self.db)
        rules = Rules.load()
        intake_deal(store, rules, load_deal(DEALS / "tx-mineral.json"), ACTOR, today=DAY0)
        verify_deal(store, rules, "D-2026-001", ACTOR, today=DAY0)
        from http.server import ThreadingHTTPServer

        from ptk.deal.server import App, make_handler
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        app = App(store, rules, ACTOR, port)
        httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            base = f"http://127.0.0.1:{port}"
            page = urllib.request.urlopen(f"{base}/deal/D-2026-001").read().decode()
            self.assertIn("Curative tracker", page)
            self.assertIn("SELLER_SHORTFALL", page)
            req = urllib.request.Request(f"{base}/", headers={"Host": "evil.example:80"})
            with self.assertRaises(urllib.error.HTTPError):
                urllib.request.urlopen(req)
            tid = by_code(fold(store.events("D-2026-001")), "TX-RUN-COUNTY")[0].task_id
            stale = urllib.request.Request(f"{base}/deal/D-2026-001/task/{tid}", data=b"token=wrong&status=cleared")
            with self.assertRaises(urllib.error.HTTPError):
                urllib.request.urlopen(stale)
            ok = urllib.request.Request(f"{base}/deal/D-2026-001/task/{tid}",
                                        data=f"token={app.token}&status=cleared&note=from+the+dashboard".encode())
            page = urllib.request.urlopen(ok).read().decode()  # follows the redirect
            self.assertIn(f"Updated {tid}.", page)
            self.assertEqual(fold(store.events("D-2026-001")).tasks[tid].status, "cleared")
        finally:
            httpd.shutdown()
            httpd.server_close()
            store.close()

    def test_export(self):
        self.run_cli("intake", str(DEALS / "nm-leasehold.json"))
        out_csv = Path(self.tmp.name) / "status.csv"
        code, _ = self.run_cli("export", "--csv", str(out_csv))
        self.assertEqual(code, 0)
        self.assertIn("D-2026-002", out_csv.read_text(encoding="utf-8"))
        try:
            import openpyxl
        except ImportError:
            self.skipTest("openpyxl not installed")
        out_x = Path(self.tmp.name) / "status.xlsx"
        self.run_cli("export", "--xlsx", str(out_x))
        self.assertEqual(openpyxl.load_workbook(out_x).sheetnames, ["Deals", "Tasks", "Messages", "Recordings", "Payments", "About"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
