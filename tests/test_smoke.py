import json
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
import importlib.util

from ptk.smoke import EXPECTED, run_smoke

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
GOOD = json.loads((EXAMPLES / "sample_extraction.json").read_text())


class FakeMessages:
    def __init__(self, payload):
        self.payload = payload

    def create(self, **kwargs):
        return SimpleNamespace(model=kwargs["model"], stop_reason="end_turn", stop_details=None, usage=None,
                               content=[SimpleNamespace(type="text", text=json.dumps(self.payload))])


def client(payload):
    fake = FakeMessages(payload)
    return SimpleNamespace(beta=SimpleNamespace(messages=fake), messages=fake)


class SmokeTests(unittest.TestCase):
    def test_expected_facts_hold_for_the_sample_answer(self):
        for label, check in EXPECTED:
            self.assertTrue(check(GOOD), label)

    @unittest.skipUnless(importlib.util.find_spec("PIL") and shutil.which("tesseract"), "needs Pillow and tesseract")
    def test_full_run_passes_with_a_correct_answer(self):
        ok, report = run_smoke(client(GOOD), Path(tempfile.mkdtemp()), "claude-opus-5-5")
        self.assertTrue(ok, "\n".join(report))
        self.assertIn("PASS  quote checks: 0 problem(s)", report)

    @unittest.skipUnless(importlib.util.find_spec("PIL"), "needs Pillow")
    def test_wrong_answer_fails(self):
        bad = json.loads(json.dumps(GOOD))
        bad["recording"]["page"] = "71"
        bad["conveyances"][0]["interest"]["factors"] = [{"num": "1", "den": "4"}]
        ok, report = run_smoke(client(bad), Path(tempfile.mkdtemp()), "claude-opus-5-5")
        self.assertFalse(ok)
        self.assertIn("FAIL  recorded in Vol. 88, Page 77", report)
        self.assertIn("FAIL  conveys 1/2 of the minerals", report)


if __name__ == "__main__":
    unittest.main()
