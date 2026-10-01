"""One test per bug found in the October 2026 self-review."""

import tempfile
import unittest
from fractions import Fraction as F
from pathlib import Path

from ptk import extract, legal_nm, legal_tx, names, runsheet
from ptk.dates import DateError, parse_date
from ptk.doi import Lease, build_deck
from ptk.fracs import parse_fraction
from ptk.ledger import Event, load_events, replay

import base64 as _b64

TINY_PNG = _b64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAAAAAA6fptVAAAACklEQVR4nGNgAAAAAgABSK+kcQAAAABJRU5ErkJggg==")
HEADER = "seq,instrument,recorded,tract,depth,estate,kind,grantor,grantees,shares,interest,warranty,npri_kind,npri_value,notes\n"


def write_takeoff(rows: str) -> Path:
    path = Path(tempfile.mkdtemp()) / "takeoff.csv"
    path.write_text(HEADER + rows)
    return path


class FractionRegressions(unittest.TestCase):
    def test_mixed_numbers_and_glyphs(self):
        for text in ("12 1/2%", "12½%", "12.5%"):
            self.assertEqual(parse_fraction(text), F(1, 8), text)
        self.assertEqual(parse_fraction("½"), F(1, 2))
        self.assertEqual(parse_fraction("1⁄8"), F(1, 8))
        self.assertEqual(parse_fraction("3/16 royalty"), F(3, 16))


class DateRegressions(unittest.TestCase):
    def test_two_digit_years_are_refused(self):
        with self.assertRaises(DateError):
            parse_date("5/10/48")

    def test_common_forms(self):
        for text in ("1948-05-10", "5/10/1948", "May 10, 1948", "May 10th, 1948", "10 May 1948"):
            self.assertEqual(parse_date(text).isoformat(), "1948-05-10", text)

    def test_takeoff_sorts_by_real_date_not_text(self):
        # As text, "12/5/1960" sorts before "3/1/1950" and the chain would run backwards.
        path = write_takeoff("1,P,1/2/1909,T,ALL,MI,root,,A,,all,,,,\n"
                             "2,D2,12/5/1960,T,ALL,MI,convey,B,C,,all,,,,\n"
                             "3,D1,3/1/1950,T,ALL,MI,convey,A,B,,all,,,,\n")
        r = replay(load_events(path))
        self.assertEqual(r.positions[("T", "ALL")].minerals, {"C": F(1)})
        self.assertEqual(r.flags, [])

    def test_takeoff_rejects_unreadable_dates(self):
        path = write_takeoff("1,P,5/10/09,T,ALL,MI,root,,A,,all,,,,\n")
        with self.assertRaises(ValueError):
            load_events(path)


class LedgerRegressions(unittest.TestCase):
    def test_names_match_across_case_and_punctuation(self):
        r = replay([Event(1, "P", "1900-01-01", "T", "ALL", "MI", "root", None, ["Baker Example"], [F(1)], "all"),
                    Event(2, "D", "1950-01-01", "T", "ALL", "MI", "convey", "BAKER EXAMPLE.", ["C"], [F(1)], "all")])
        self.assertEqual(r.positions[("T", "ALL")].minerals, {"C": F(1)})
        self.assertEqual([f.code for f in r.flags], ["NAME_VARIANT"])

    def test_mineral_reserve_rows_are_flagged_not_silently_dropped(self):
        r = replay([Event(1, "P", "1900-01-01", "T", "ALL", "MI", "root", None, ["A"], [F(1)], "all"),
                    Event(2, "D", "1950-01-01", "T", "ALL", "MI", "reserve", "A", ["A"], [F(1)], "1/2")])
        self.assertIn("MI_RESERVE_IGNORED", [f.code for f in r.flags])

    def test_probate_moves_npri_as_well_as_minerals(self):
        events = [
            Event(1, "P", "1900-01-01", "T", "ALL", "MI", "root", None, ["A"], [F(1)], "all"),
            Event(2, "D", "1940-01-01", "T", "ALL", "MI", "convey", "A", ["B"], [F(1)], "1/2"),
            Event(3, "D", "1940-01-01", "T", "ALL", "NPRI", "reserve", "A", [], [], "", npri_kind="fixed", npri_value=F(1, 16)),
            Event(4, "Probate", "1960-01-01", "T", "ALL", "ALL", "convey", "A", ["H1", "H2"], [F(1, 2), F(1, 2)], "all"),
        ]
        pos = replay(events).positions[("T", "ALL")]
        self.assertNotIn("A", pos.minerals)
        self.assertEqual(sorted((n.owner, n.value) for n in pos.npris), [("H1", F(1, 32)), ("H2", F(1, 32))])

    def test_all_estate_needs_relative_interest(self):
        r = replay([Event(1, "P", "1900-01-01", "T", "ALL", "MI", "root", None, ["A"], [F(1)], "all"),
                    Event(2, "D", "1950-01-01", "T", "ALL", "ALL", "convey", "A", ["B"], [F(1)], "1/2")])
        self.assertEqual(r.positions[("T", "ALL")].minerals, {"A": F(1)})
        self.assertIn("ALL_NEEDS_RELATIVE_INTEREST", [f.code for f in r.flags])

    def test_input_events_are_not_mutated(self):
        ev = Event(1, "P", "1900-01-01", "T", "ALL", "MI", "root", None, ["a. b."], [F(1)], "all")
        replay([ev])
        self.assertEqual(ev.grantees, ["a. b."])

    def test_lessor_names_match_ledger_names_loosely(self):
        r = replay([Event(1, "P", "1900-01-01", "T", "ALL", "MI", "root", None, ["Fox Example"], [F(1)], "all")])
        deck = build_deck(r, [Lease("L", "T", ["FOX EXAMPLE"], F(1, 4), {"Op": F(1)})], {"T": F(1)}, pooled=False)
        self.assertEqual(deck.revenue_total(), 1)
        self.assertNotIn("LESSOR_NOT_OWNER", [f.code for f in deck.flags])


class NpriBurdenTests(unittest.TestCase):
    """An NPRI reserved out of part of the minerals burdens only that part, and the burden follows it."""

    def events(self, burdens):
        return [
            Event(1, "P", "1900-01-01", "T", "ALL", "MI", "root", None, ["A"], [F(1)], "all"),
            Event(2, "D1", "1940-01-01", "T", "ALL", "MI", "convey", "A", ["B"], [F(1)], "1/2"),
            Event(3, "D1", "1940-01-01", "T", "ALL", "NPRI", "reserve", "A", ["N"], [F(1)], "",
                  npri_kind="fixed", npri_value=F(1, 16), npri_burdens=burdens),
            Event(4, "D2", "1960-01-01", "T", "ALL", "MI", "convey", "B", ["C", "D"], [F(1, 2), F(1, 2)], "all"),
        ]

    def deck(self, burdens, royalty=F(1, 4)):
        r = replay(self.events(burdens))
        leases = [Lease("L", "T", ["A", "C", "D"], royalty, {"Op": F(1)})]
        return r, build_deck(r, leases, {"T": F(1)}, pooled=False)

    def test_conveyed_half_only(self):
        r, deck = self.deck("conveyed")
        pos = r.positions[("T", "ALL")]
        self.assertEqual(pos.burdened_share("D1#3"), F(1, 2))  # followed B's half on to C and D
        rows = {(x.owner, x.type): x.tract_decimal for x in deck.rows}
        self.assertEqual(rows[("A", "RI")], F(1, 2) * F(1, 4))  # A's retained half is unburdened
        self.assertEqual(rows[("C", "RI")], F(1, 4) * (F(1, 4) - F(1, 16)))
        self.assertEqual(rows[("N", "NPRI")], F(1, 16) * F(1, 2))
        self.assertEqual(deck.revenue_total(), 1)

    def test_all_minerals_is_the_old_behavior(self):
        _, deck = self.deck("all")
        rows = {(x.owner, x.type): x.tract_decimal for x in deck.rows}
        self.assertEqual(rows[("N", "NPRI")], F(1, 16))
        self.assertEqual(rows[("A", "RI")], F(1, 2) * (F(1, 4) - F(1, 16)))
        self.assertEqual(deck.revenue_total(), 1)

    def test_retained_and_named_owners(self):
        r, deck = self.deck("retained")
        self.assertEqual(r.positions[("T", "ALL")].burdened_share("D1#3"), F(1, 2))
        self.assertEqual({(x.owner, x.type): x.tract_decimal for x in deck.rows}[("C", "RI")], F(1, 4) * F(1, 4))
        r, _ = self.deck("owners:B")
        self.assertEqual(r.positions[("T", "ALL")].burdened_share("D1#3"), F(1, 2))

    def test_floating_npri_on_conveyed_half(self):
        events = self.events("conveyed")
        events[2] = Event(3, "D1", "1940-01-01", "T", "ALL", "NPRI", "reserve", "A", ["N"], [F(1)], "",
                          npri_kind="floating", npri_value=F(1, 2), npri_burdens="conveyed")
        r = replay(events)
        deck = build_deck(r, [Lease("L", "T", ["A", "C", "D"], F(1, 4), {"Op": F(1)})], {"T": F(1)}, pooled=False)
        rows = {(x.owner, x.type): x.tract_decimal for x in deck.rows}
        self.assertEqual(rows[("N", "NPRI")], F(1, 2) * F(1, 2) * F(1, 4))
        self.assertEqual(deck.revenue_total(), 1)

    def test_unreadable_or_empty_burden_is_flagged(self):
        codes = {f.code for f in replay(self.events("half of it")).flags}
        self.assertIn("NPRI_BURDENS_UNREADABLE", codes)
        events = self.events("conveyed")
        events[2] = Event(0, "D9", "1940-01-01", "T", "ALL", "NPRI", "reserve", "A", ["N"], [F(1)], "",
                          npri_kind="fixed", npri_value=F(1, 16), npri_burdens="conveyed")
        self.assertIn("NPRI_BURDENS_NOTHING", {f.code for f in replay(events).flags})

    def test_merger_review_when_owner_holds_burdened_minerals_and_the_npri(self):
        events = self.events("conveyed")
        events.append(Event(5, "D3", "1970-01-01", "T", "ALL", "MI", "convey", "C", ["N"], [F(1)], "all"))
        flags = [f for f in replay(events).flags if f.code == "MERGER_REVIEW"]
        self.assertEqual(len(flags), 1)
        self.assertIn("1/4", flags[0].detail)


class NameRegressions(unittest.TestCase):
    def test_trailing_suffix_after_comma(self):
        self.assertEqual(names.search_variants("John W. Smith, Jr.")["searches"][0], "SMITH JOHN W")
        self.assertEqual(names.search_variants("Smith, John William, Jr.")["searches"][0], "SMITH JOHN WILLIAM")

    def test_and_wife_splits_into_two_people(self):
        v = names.search_variants("John Smith and wife, Mary Smith")
        self.assertEqual(v["kind"], "multiple")
        self.assertIn("SMITH JOHN", v["searches"])
        self.assertIn("SMITH MARY", v["searches"])

    def test_heirs_of(self):
        v = names.search_variants("Heirs of John Smith")
        self.assertEqual((v["kind"], v["searches"][0]), ("heirs", "SMITH JOHN"))


class RunsheetRegressions(unittest.TestCase):
    base = {"row": "1", "state": "TX", "county": "Reeves", "volume": "1", "page": "1", "image_file": "x.pdf",
            "instrument_type": "Mineral Deed", "grantor": "A", "grantee": "B"}

    def codes(self, **kw):
        return {f.code for f in runsheet.check_row({**self.base, **kw})}

    def test_double_fraction_with_numerals_in_parentheses(self):
        self.assertIn("DOUBLE_FRACTION", self.codes(
            reservations_exceptions="reserving an undivided one-half (1/2) of the one-eighth (1/8) royalty"))
        self.assertNotIn("DOUBLE_FRACTION", self.codes(reservations_exceptions="one-half (1/2) of the minerals"))

    def test_short_term_clause(self):
        self.assertIn("TERM_INTEREST", self.codes(
            reservations_exceptions="for fifteen years and so long thereafter as minerals are produced"))

    def test_bad_dates_on_either_column(self):
        self.assertIn("BAD_DATE", self.codes(instrument_date="5/10/48"))
        self.assertIn("BAD_DATE", self.codes(filed_date="sometime in 1948"))

    def test_release_is_not_a_lien(self):
        self.assertNotIn("LIEN_OR_LITIGATION", self.codes(instrument_type="Release of Deed of Trust"))
        self.assertIn("LIEN_OR_LITIGATION", self.codes(instrument_type="Deed of Trust"))


class LegalRegressions(unittest.TestCase):
    def test_texas_multiple_sections(self):
        d = legal_tx.parse("All of Sections 12 and 13, Block 33, T-2-S, T&P RR Co Survey, Reeves County")
        self.assertEqual(d.sections, ["12", "13"])
        self.assertEqual(d.survey_keys(), ["TX|REEVES|T&P|BLK 33|T2S|SEC 12", "TX|REEVES|T&P|BLK 33|T2S|SEC 13"])

    def test_new_mexico_all_of_section(self):
        d = legal_nm.parse("All of Section 6, T24S, R32E, NMPM")
        self.assertTrue(d.sections[0].whole)
        self.assertEqual(d.issues, [])

    def test_new_mexico_multiple_sections(self):
        d = legal_nm.parse("Sections 15 and 22: All, T23S, R31E, NMPM")
        self.assertEqual([(s.section, s.whole) for s in d.sections], [(15, True), (22, True)])
        d = legal_nm.parse("N/2 of Sections 15 and 22, T23S, R31E, NMPM")
        self.assertEqual([s.regular_area() for s in d.sections], [F(1, 2), F(1, 2)])


class CountyChecklistTests(unittest.TestCase):
    def test_checklist_round_trip_overrides_years(self):
        import csv as _csv
        from ptk import counties
        path = Path(tempfile.mkdtemp()) / "check.csv"
        counties.write_checklist(path)
        rows = list(_csv.DictReader(open(path)))
        irion = next(r for r in rows if r["county"] == "Irion")
        self.assertEqual(irion["texasfile_url"], "https://www.texasfile.com/texas-land-records-coverage/irion-county-clerk/")
        self.assertEqual(next(r for r in rows if r["county"] == "Lea")["courthousedirect_url"],
                         "https://www.courthousedirect.com/PropertySearch/NewMexico/Lea")
        irion["confirmed_texasfile_index_from"] = "1950"
        with open(path, "w", newline="") as fh:
            w = _csv.DictWriter(fh, fieldnames=rows[0].keys())
            w.writeheader()
            w.writerows(rows)
        table = counties.load_overrides(path)
        self.assertEqual(table["IRION"].texasfile_index_from, 1950)
        self.assertIn("starts 1950", " ".join(counties.plan_search("Irion", 1905, table)))
        self.assertIn("confirm on the vendor pages", " ".join(counties.plan_search("Irion", 1905)))


class ExtractRegressions(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "p1.png").write_bytes(TINY_PNG)

    def test_haiku_gets_no_effort(self):
        params = extract.build_params([extract.Page(self.tmp / "p1.png")], doc_id="D", county="Reeves",
                                      state="TX", model="claude-haiku-4-5")
        self.assertNotIn("effort", params["output_config"])
        self.assertIn("format", params["output_config"])

    def test_uncited_evidence_is_flagged_when_ocr_exists(self):
        problems = extract.verify({"x": {"evidence": [{"page": 1, "line_ids": [], "quote": "invented"}]}},
                                  {"p1.l1": "MINERAL DEED"})
        self.assertEqual([p["code"] for p in problems], ["EVIDENCE_NOT_CITED"])

    def test_oversized_request_fails_with_a_clear_message(self):
        big = self.tmp / "big.pdf"
        big.write_bytes(b"%PDF" + b"0" * (23 * 1024 * 1024))
        with self.assertRaisesRegex(ValueError, "30 MB"):
            extract.build_params([extract.Page(big)], doc_id="D", county="Reeves", state="TX")


if __name__ == "__main__":
    unittest.main()
