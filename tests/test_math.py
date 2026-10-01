import unittest
from fractions import Fraction as F
from pathlib import Path

from ptk.doi import Lease, build_deck, participation
from ptk.fracs import FractionError, parse_fraction, read_fraction_text, to_decimal
from ptk.ledger import Event, load_events, replay

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


class FractionTests(unittest.TestCase):
    def test_numeric_forms(self):
        for text in ("3/16", "3/16ths", "0.1875", "18.75%", "3 / 16"):
            self.assertEqual(parse_fraction(text), F(3, 16), text)

    def test_products(self):
        self.assertEqual(parse_fraction("1/2 of 1/8"), F(1, 16))
        self.assertEqual(parse_fraction("1/2 x 1/8"), F(1, 16))
        self.assertEqual(parse_fraction("one-half of one-eighth"), F(1, 16))

    def test_words(self):
        cases = {
            "one-half": F(1, 2), "an undivided one-sixteenth": F(1, 16), "three-sixteenths": F(3, 16),
            "two-thirds": F(2, 3), "three quarters": F(3, 4), "one sixty-fourth": F(1, 64),
            "one hundred twenty-eighth": F(1, 128), "one-eighth (1/8th)": F(1, 8),
        }
        for text, value in cases.items():
            self.assertEqual(parse_fraction(text), value, text)

    def test_word_numeral_mismatch_is_caught(self):
        reading = read_fraction_text("one-half (1/4)")
        self.assertFalse(reading.consistent)
        self.assertIsNone(reading.value)
        self.assertEqual(reading.words_value, F(1, 2))
        self.assertEqual(reading.numeral_value, F(1, 4))
        with self.assertRaises(FractionError):
            parse_fraction("one-half (1/4)")

    def test_rounding_is_exact_half_up(self):
        self.assertEqual(to_decimal(F(1, 3)), "0.33333333")
        self.assertEqual(to_decimal(F(2, 3)), "0.66666667")
        self.assertEqual(to_decimal(F(1, 128)), "0.00781250")
        self.assertEqual(to_decimal(F(5, 10**9)), "0.00000001")  # exactly half rounds up
        self.assertEqual(to_decimal(F(1)), "1.00000000")


def ev(seq, rec, tract, kind, grantor, grantees, interest="all", estate="MI", **kw):
    shares = kw.pop("shares", None) or [F(1, len(grantees))] * len(grantees)
    return Event(seq, f"I{seq}", rec, tract, kw.pop("depth", "ALL"), estate, kind, grantor, grantees, shares, interest, **kw)


class LedgerTests(unittest.TestCase):
    def test_simple_chain_sums_to_one(self):
        r = replay([
            ev(1, "1900-01-01", "T", "root", None, ["A"]),
            ev(2, "1950-01-01", "T", "convey", "A", ["B", "C"], "1/2"),
            ev(3, "1960-01-01", "T", "convey", "B", ["D"], "1/2 of grantor"),
        ])
        pos = r.positions[("T", "ALL")]
        self.assertEqual(pos.minerals, {"A": F(1, 2), "B": F(1, 8), "C": F(1, 4), "D": F(1, 8)})
        self.assertEqual(r.flags, [])

    def test_duhig_candidate_moves_only_what_grantor_held(self):
        r = replay([
            ev(1, "1900-01-01", "T", "root", None, ["A"]),
            ev(2, "1950-01-01", "T", "convey", "A", ["B"], "1/2"),
            ev(3, "1960-01-01", "T", "convey", "A", ["C"], "3/4", warranty=True),
        ])
        self.assertEqual(r.positions[("T", "ALL")].minerals, {"B": F(1, 2), "C": F(1, 2)})
        self.assertEqual([f.code for f in r.flags], ["DUHIG_CANDIDATE"])

    def test_quitclaim_over_conveyance_is_not_duhig(self):
        r = replay([
            ev(1, "1900-01-01", "T", "root", None, ["A"], "1/2"),
            ev(2, "1900-01-01", "T", "root", None, ["Z"], "1/2"),
            ev(3, "1960-01-01", "T", "convey", "A", ["C"], "3/4"),
        ])
        self.assertIn("OVER_CONVEYANCE", [f.code for f in r.flags])

    def test_chain_gap_moves_nothing(self):
        r = replay([
            ev(1, "1900-01-01", "T", "root", None, ["A"]),
            ev(2, "1950-01-01", "T", "convey", "Stranger", ["B"], "1/2"),
        ])
        self.assertEqual(r.positions[("T", "ALL")].minerals, {"A": F(1)})
        self.assertEqual([f.code for f in r.flags], ["CHAIN_GAP"])

    def test_as_of_date_stops_the_replay(self):
        events = [ev(1, "1900-01-01", "T", "root", None, ["A"]), ev(2, "1990-01-01", "T", "convey", "A", ["B"])]
        self.assertEqual(replay(events, as_of="1980-12-31").positions[("T", "ALL")].minerals, {"A": F(1)})

    def test_depth_severance(self):
        depths = {"T": ["SHALLOW", "DEEP"]}
        r = replay([
            ev(1, "1900-01-01", "T", "root", None, ["A"]),
            ev(2, "1980-01-01", "T", "convey", "A", ["B"], depth="DEEP"),
        ], depths=depths)
        self.assertEqual(r.positions[("T", "SHALLOW")].minerals, {"A": F(1)})
        self.assertEqual(r.positions[("T", "DEEP")].minerals, {"B": F(1)})

    def test_missing_minerals_are_flagged(self):
        r = replay([ev(1, "1900-01-01", "T", "root", None, ["A"], "3/4")])
        self.assertIn("MINERALS_NOT_WHOLE", [f.code for f in r.flags])


class DeckTests(unittest.TestCase):
    def test_worked_example_fixed_npri_allocation_well(self):
        """Owner A 1/4 MI, lease royalty 1/4, fixed 1/16 NPRI, 4,800 of 9,600 lateral feet."""
        r = replay([
            ev(1, "1900-01-01", "S12", "root", None, ["A", "B"], shares=[F(1, 4), F(3, 4)]),
            Event(2, "NPRI", "1900-01-01", "S12", "ALL", "NPRI", "reserve", "N", [], [], "",
                  npri_kind="fixed", npri_value=F(1, 16)),
            ev(3, "1900-01-01", "S13", "root", None, ["X"]),
        ])
        leases = [Lease("L1", "S12", ["A", "B"], F(1, 4), {"Op": F(1)}, {"O": F(1, 40)}),
                  Lease("L2", "S13", ["X"], F(1, 4), {"Op": F(1)})]
        deck = build_deck(r, leases, participation({"S12": 4800, "S13": 4800}), npri_ratified={"N": True})
        rows = {(x.owner, x.type, x.tract): x.unit_decimal for x in deck.rows}
        self.assertEqual(rows[("A", "RI", "S12")], F(3, 128))
        self.assertEqual(to_decimal(rows[("A", "RI", "S12")]), "0.02343750")
        self.assertEqual(rows[("N", "NPRI", "S12")], F(1, 32))
        self.assertEqual(rows[("Op", "NRI", "S12")], F(29, 80))
        self.assertEqual(deck.revenue_total(), 1)
        self.assertNotIn("NPRI_NOT_RATIFIED", [f.code for f in deck.flags])

    def test_floating_npri_follows_each_lease_royalty(self):
        r = replay([
            ev(1, "1900-01-01", "T", "root", None, ["A", "B"]),
            Event(2, "NPRI", "1900-01-01", "T", "ALL", "NPRI", "reserve", "N", [], [], "",
                  npri_kind="floating", npri_value=F(1, 2)),
        ])
        leases = [Lease("L1", "T", ["A"], F(1, 4), {"Op": F(1)}), Lease("L2", "T", ["B"], F(1, 5), {"Op": F(1)})]
        deck = build_deck(r, leases, {"T": F(1)}, pooled=False)
        rows = {(x.owner, x.type): x.unit_decimal for x in deck.rows if x.type in ("RI", "NPRI")}
        self.assertEqual(rows[("A", "RI")], F(1, 2) * F(1, 4) * F(1, 2))
        self.assertEqual(rows[("N", "NPRI")], F(1, 2) * (F(1, 2) * F(1, 4) + F(1, 2) * F(1, 5)))
        self.assertEqual(deck.revenue_total(), 1)

    def test_unleased_new_mexico_owner_is_seven_eighths_wi_plus_one_eighth_royalty(self):
        r = replay([ev(1, "1900-01-01", "T", "root", None, ["A", "U"])])
        deck = build_deck(r, [Lease("L1", "T", ["A"], F(3, 16), {"Op": F(1)})], {"T": F(1)}, state="NM")
        umi = sorted(x.tract_decimal for x in deck.rows if x.owner == "U" and x.type == "UMI")
        self.assertEqual(umi, [F(1, 16), F(7, 16)])
        self.assertEqual(deck.revenue_total(), 1)

    def test_unratified_npri_is_flagged_in_texas_units(self):
        r = replay([
            ev(1, "1900-01-01", "T", "root", None, ["A"]),
            Event(2, "NPRI", "1900-01-01", "T", "ALL", "NPRI", "reserve", "N", [], [], "",
                  npri_kind="fixed", npri_value=F(1, 32)),
        ])
        deck = build_deck(r, [Lease("L1", "T", ["A"], F(1, 4), {"Op": F(1)})], {"T": F(1)})
        self.assertIn("NPRI_NOT_RATIFIED", [f.code for f in deck.flags])

    def test_npri_larger_than_royalty_is_flagged(self):
        r = replay([
            ev(1, "1900-01-01", "T", "root", None, ["A"]),
            Event(2, "NPRI", "1900-01-01", "T", "ALL", "NPRI", "reserve", "N", [], [], "",
                  npri_kind="fixed", npri_value=F(1, 4)),
        ])
        deck = build_deck(r, [Lease("L1", "T", ["A"], F(1, 8), {"Op": F(1)})], {"T": F(1)}, pooled=False)
        self.assertIn("NPRI_EXCEEDS_ROYALTY", [f.code for f in deck.flags])

    def test_example_project_deck_is_whole(self):
        r = replay(load_events(EXAMPLES / "takeoff.csv"))
        import json
        p = json.loads((EXAMPLES / "project.json").read_text())
        deck = build_deck(r, [Lease.from_dict(d) for d in p["leases"]], participation(p["participation"]["weights"]),
                          npri_ratified=p["npri_ratified"])
        self.assertEqual(deck.revenue_total(), 1)
        codes = {f.code for f in r.flags + deck.flags}
        self.assertTrue({"DUHIG_CANDIDATE", "CHAIN_GAP", "NPRI_NOT_RATIFIED", "UNLEASED"} <= codes)


if __name__ == "__main__":
    unittest.main()
