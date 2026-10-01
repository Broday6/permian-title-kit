import json
import unittest
from fractions import Fraction as F
from pathlib import Path
from types import SimpleNamespace

from ptk import counties, extract, legal_nm, legal_tx, names, runsheet
from ptk.cli import main

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


class TexasLegalTests(unittest.TestCase):
    def test_full_modern_description(self):
        d = legal_tx.parse("Section 12, Block 33, T-2-S, T&P RR Co. Survey, A-123, Midland County, Texas")
        self.assertEqual((d.county, d.section, d.block, d.township, d.survey_code, d.abstract),
                         ("Midland", "12", "33", "2S", "T&P", "123"))
        self.assertEqual(d.abstract_key(), "TX|MIDLAND|A-123")
        self.assertEqual(d.survey_key(), "TX|MIDLAND|T&P|BLK 33|T2S|SEC 12")
        self.assertEqual(d.issues, [])

    def test_old_style_abbreviations(self):
        d = legal_tx.parse("The W/2 of Section 22, Block 57, Township 1 South, T. & P. Ry. Co. Survey, Abst. 567, Reeves County")
        self.assertEqual((d.section, d.block, d.township, d.survey_code, d.abstract, d.aliquots),
                         ("22", "57", "1S", "T&P", "567", ["W2"]))

    def test_school_land_and_missing_abstract(self):
        d = legal_tx.parse("All of Sec. 12, Blk. C-21, PSL", county="Reeves")
        self.assertEqual((d.county, d.block, d.survey_code), ("Reeves", "C-21", "PSL"))
        self.assertTrue(any("abstract" in i for i in d.issues))

    def test_lowercase_a_is_not_an_abstract(self):
        d = legal_tx.parse("a 160 acre tract being the SE/4 of Section 9, Block 38, T-1-S, T&P Ry Co Survey, Martin County")
        self.assertIsNone(d.abstract)
        self.assertEqual((d.acres, d.aliquots), ("160", ["SE4"]))

    def test_save_and_except(self):
        d = legal_tx.parse("Section 5, Block 34, H&TC RR Co Survey, A-1234, Reeves County, SAVE AND EXCEPT 10 acres out of the NW corner")
        self.assertEqual(d.save_and_except, ["10 acres out of the NW corner"])
        self.assertEqual(d.aliquots, [])
        self.assertEqual(d.survey_code, "H&TC")


class NewMexicoLegalTests(unittest.TestCase):
    def test_section_first_style_with_lots(self):
        d = legal_nm.parse("Section 15, T23S, R31E, NMPM: NE/4, N/2 SE/4 and Lots 1-4")
        s = d.sections[0]
        self.assertEqual((d.township, d.range, s.section), ("23S", "31E", 15))
        self.assertEqual(s.quarter_quarters, {"NENE", "NWNE", "SENE", "SWNE", "NESE", "NWSE"})
        self.assertEqual(s.lots, [1, 2, 3, 4])
        self.assertEqual(s.regular_area(), F(3, 8))

    def test_of_style_and_right_to_left(self):
        d = legal_nm.parse("SE/4 of NW/4 and W/2 W/2 of Section 6, Township 24 South, Range 32 East, N.M.P.M.")
        self.assertEqual(d.sections[0].quarter_quarters, {"SENW", "NWNW", "SWNW", "NWSW", "SWSW"})
        self.assertEqual(d.issues, [])

    def test_multiple_sections_and_compact_aliquots(self):
        d = legal_nm.parse("Sec. 22: All; Sec. 27: N2NE4, T. 19 S., R. 33 E., N.M.P.M.")
        self.assertTrue(d.sections[0].whole)
        self.assertEqual(d.sections[1].quarter_quarters, {"NENE", "NWNE"})

    def test_words_and_finer_divisions(self):
        self.assertEqual(legal_nm.parse("The north half of the southeast quarter of Section 10, T26S, R29E, NMPM")
                         .sections[0].quarter_quarters, {"NESE", "NWSE"})
        d = legal_nm.parse("Section 8: N/2 NE/4 NW/4, T20S R34E NMPM")
        self.assertEqual(d.keys(), ["NM|20S|34E|8|N2NENW"])
        self.assertEqual(d.sections[0].regular_area(), F(1, 32))

    def test_unreadable_text_is_reported(self):
        d = legal_nm.parse("Section 3: that part lying north of the railroad, T21S, R27E, NMPM")
        self.assertTrue(any("couldn't read" in i for i in d.issues))


class NameTests(unittest.TestCase):
    def test_person_variants_include_abbreviations_and_initials(self):
        v = names.search_variants("Jno. W. Smith et ux")
        for s in ("SMITH JNO W", "SMITH J W", "SMITH JOHN W", "SMITH JACK", "SMITH *"):
            self.assertIn(s, v["searches"])
        self.assertTrue(any("et ux" in n for n in v["notes"]))

    def test_entities_and_capacity(self):
        v = names.search_variants("XYZ Oil & Gas, L.L.C.")
        self.assertEqual(v["searches"][0], "XYZ OIL GAS LLC")
        self.assertEqual(v["kind"], "organization")
        v = names.search_variants("Mary Ann Brown, Independent Executrix of the Estate of Robert Brown")
        self.assertEqual(v["searches"][0], "BROWN MARY ANN")
        self.assertTrue(any("Estate of Robert Brown" in n for n in v["notes"]))

    def test_soundex(self):
        self.assertEqual(names.soundex("Robert"), "R163")
        self.assertEqual(names.soundex("Rupert"), "R163")
        self.assertEqual(names.soundex("Ashcraft"), "A261")
        self.assertEqual(names.soundex("Tymczak"), "T522")


class RunsheetTests(unittest.TestCase):
    def test_example_runsheet_flags(self):
        rows, flags = runsheet.check_file(EXAMPLES / "runsheet.csv")
        got = {(f.row, f.code) for f in flags}
        expected = {("1", "DOUBLE_FRACTION"), ("2", "WORD_NUMERAL_MISMATCH"), ("3", "NO_IMAGE"),
                    ("3", "AFFIDAVIT_OF_HEIRSHIP"), ("4", "TOP_LEASE"), ("4", "DEPTH_LIMITED"),
                    ("5", "BLANKET_CONVEYANCE"), ("5", "SPOUSE_NOT_JOINED"), ("5", "UNACKNOWLEDGED"),
                    ("5", "FILED_BEFORE_EXECUTED"), ("5", "FIDUCIARY_CAPACITY")}
        self.assertTrue(expected <= got, expected - got)
        self.assertNotIn(("1", "WORD_NUMERAL_MISMATCH"), got)

    def test_image_key(self):
        self.assertEqual(runsheet.image_key("TX", "Reeves", "OPR", instrument_no="2019-012345"),
                         "TX-REEVES-OPR-2019-012345.pdf")
        self.assertEqual(runsheet.image_key("TX", "Midland", "DR", volume="512", page="33"),
                         "TX-MIDLAND-DR-0512-0033.pdf")


class CountyTests(unittest.TestCase):
    def test_late_index_county_sends_you_to_the_courthouse(self):
        plan = " ".join(counties.plan_search("Irion", 1905))
        self.assertIn("starts 1983", plan)
        self.assertIn("courthouse", plan)

    def test_parent_county_before_organization(self):
        plan = " ".join(counties.plan_search("Lea", 1910))
        self.assertIn("Chaves", plan)
        self.assertIn("Eddy", plan)
        self.assertNotIn("Chaves", " ".join(counties.plan_search("Lea", 1950)))


class FakeMessages:
    def __init__(self, payload, stop_reason="end_turn"):
        self.payload, self.stop_reason, self.calls = payload, stop_reason, []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            model=kwargs["model"], stop_reason=self.stop_reason, stop_details=None, usage=None,
            content=[SimpleNamespace(type="text", text=json.dumps(self.payload))],
        )


class ExtractTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "p1.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
        (self.tmp / "p1.txt").write_text("MINERAL DEED\nan undivided one-half (1/4) interest\nSection 12, Block 33\n")

    def _payload(self, quote):
        return {"conveyances": [{"interest": {"text_verbatim": "an undivided one-half (1/4)", "factors": [{"num": "1", "den": "4"}]},
                                 "evidence": [{"page": 1, "line_ids": ["p1.l2"], "quote": quote}]}]}

    def test_request_shape_and_checks(self):
        pages = [extract.Page(self.tmp / "p1.png", extract.ocr_lines_for(self.tmp / "p1.png", self.tmp))]
        fake = FakeMessages(self._payload("a quote that is not on the page at all"))
        client = SimpleNamespace(beta=SimpleNamespace(messages=fake), messages=fake)
        out = extract.extract(client, pages, doc_id="D1", county="Reeves", state="TX")
        call = fake.calls[0]
        self.assertEqual(call["model"], "claude-opus-5-5")
        self.assertEqual(call["fallbacks"], "default")
        self.assertEqual(call["output_config"]["format"]["type"], "json_schema")
        self.assertEqual(call["output_config"]["effort"], "high")
        self.assertEqual(call["system"][0]["cache_control"], {"type": "ephemeral"})
        blocks = call["messages"][0]["content"]
        self.assertEqual(blocks[1]["type"], "image")
        self.assertIn("p1.l2: an undivided one-half (1/4) interest", blocks[2]["text"])
        codes = {c["code"] for c in out["checks"]}
        self.assertEqual(codes, {"QUOTE_NOT_IN_CITED_LINES", "WORD_NUMERAL_MISMATCH"})

    def test_good_quote_passes_and_haiku_skips_fallbacks(self):
        pages = [extract.Page(self.tmp / "p1.png", extract.ocr_lines_for(self.tmp / "p1.png", self.tmp))]
        payload = self._payload("an undivided one-half (1/4) interest")
        payload["conveyances"][0]["interest"]["text_verbatim"] = "an undivided one-fourth (1/4)"
        fake = FakeMessages(payload)
        client = SimpleNamespace(beta=SimpleNamespace(messages=fake), messages=fake)
        out = extract.extract(client, pages, doc_id="D1", county="Reeves", state="TX", model="claude-haiku-4-5")
        self.assertNotIn("fallbacks", fake.calls[0])
        self.assertEqual(out["checks"], [])

    def test_refusal_and_truncation_are_not_parsed(self):
        pages = [extract.Page(self.tmp / "p1.png")]
        for reason in ("refusal", "max_tokens"):
            fake = FakeMessages({}, stop_reason=reason)
            client = SimpleNamespace(beta=SimpleNamespace(messages=fake), messages=fake)
            out = extract.extract(client, pages, doc_id="D1", county="Reeves", state="TX")
            self.assertIn("error", out)
            self.assertNotIn("extraction", out)

    def test_batch_submit_and_collect(self):
        pages = [extract.Page(self.tmp / "p1.png", extract.ocr_lines_for(self.tmp / "p1.png", self.tmp))]
        submitted = {}
        message = SimpleNamespace(model="claude-opus-5-5", stop_reason="end_turn", stop_details=None, usage=None,
                                  content=[SimpleNamespace(type="text", text=json.dumps(self._payload("not on the page")))])
        batches = SimpleNamespace(
            create=lambda requests: submitted.update(requests=requests) or SimpleNamespace(id="msgbatch_1"),
            retrieve=lambda bid: SimpleNamespace(processing_status="ended"),
            results=lambda bid: [SimpleNamespace(custom_id="TX-1_2", result=SimpleNamespace(type="succeeded", message=message)),
                                 SimpleNamespace(custom_id="TX-9", result=SimpleNamespace(type="expired"))],
        )
        client = SimpleNamespace(messages=SimpleNamespace(batches=batches))
        bid = extract.submit_batch(client, [{"doc_id": "TX-1/2", "pages": pages, "county": "Reeves", "state": "TX"}])
        self.assertEqual(bid, "msgbatch_1")
        req = submitted["requests"][0]
        self.assertEqual(req["custom_id"], "TX-1_2")
        self.assertNotIn("fallbacks", req["params"])
        out = extract.collect_batch(client, bid, {"TX-1_2": extract.line_table(pages)})
        self.assertEqual({c["code"] for c in out["results"]["TX-1_2"]["checks"]},
                         {"QUOTE_NOT_IN_CITED_LINES", "WORD_NUMERAL_MISMATCH"})
        self.assertIn("resubmit", out["results"]["TX-9"]["error"])

    def test_schema_is_strict(self):
        schema = extract.load_schema()
        self.assertFalse(schema["additionalProperties"])
        for name, d in schema["$defs"].items():
            if d.get("type") == "object":
                self.assertFalse(d["additionalProperties"], name)
                self.assertEqual(set(d["required"]), set(d["properties"]), name)


class CliTests(unittest.TestCase):
    def test_deck_command_exits_zero_on_whole_deck(self):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(["deck", str(EXAMPLES / "project.json")])
        self.assertEqual(code, 0)
        self.assertIn("Revenue interests total: 1.00000000", buf.getvalue())

    def test_calc_command(self):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            main(["calc", "--acres", "640", "--owned", "1/16", "--royalty", "1/4", "--unit", "640/1280"])
        self.assertIn("0.00781250 (1/128)", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
