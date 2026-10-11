"""Ratchet: every parser case in ptk/deal/parser_corpus.json must stay correct.

A case that can't be fixed yet goes in KNOWN_FAILURES with a reason; a known failure that starts
passing must be removed from the set, so the list only shrinks.
"""

import unittest

from ptk.deal.parser_accuracy import score_all

KNOWN_FAILURES: dict[str, str] = {}


class ParserCorpusTests(unittest.TestCase):
    def test_corpus(self):
        results = score_all()
        self.assertGreaterEqual(len(results), 264)
        broken = [f"{r['id']} [{r['risk']}] {r['parser']}: got {r['actual']}"
                  for r in results if r["verdict"] != "correct" and r["id"] not in KNOWN_FAILURES]
        self.assertEqual(broken, [], "\n".join(broken))
        fixed = [r["id"] for r in results if r["verdict"] == "correct" and r["id"] in KNOWN_FAILURES]
        self.assertEqual(fixed, [], f"now passing, remove from KNOWN_FAILURES: {fixed}")

    def test_case_ids_are_unique(self):
        ids = [r["id"] for r in score_all()]
        self.assertEqual(len(ids), len(set(ids)))


if __name__ == "__main__":
    unittest.main()
