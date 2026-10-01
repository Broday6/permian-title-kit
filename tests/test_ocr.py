import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from ptk import ocr
from ptk.extract import line_table, ocr_lines_for, Page

HAS_PIL = importlib.util.find_spec("PIL") is not None
HAS_TESSERACT = shutil.which("tesseract") is not None


def sample(tmp: Path) -> Path:
    from ptk.sample import render_sample_page
    return render_sample_page(tmp / "sample-deed.png")


class OcrUnitTests(unittest.TestCase):
    def test_rows_merge_left_to_right_and_sort_top_to_bottom(self):
        pieces = [ocr.OcrLine("Block 33,", 0.9, [500, 102, 700, 130]), ocr.OcrLine("Section 12,", 0.95, [100, 100, 450, 128]),
                  ocr.OcrLine("MINERAL DEED", 0.99, [100, 20, 400, 50])]
        merged = ocr._merge_rows(pieces)
        self.assertEqual([m.text for m in merged], ["MINERAL DEED", "Section 12, Block 33,"])
        self.assertEqual(merged[1].confidence, 0.9)
        self.assertEqual(merged[1].box, [100, 100, 700, 130])

    def test_full_width_punctuation_is_folded(self):
        self.assertEqual(ocr.clean("one-half （1/2）， more"), "one-half (1/2), more")

    @unittest.skipUnless(HAS_PIL, "needs Pillow")
    def test_textract_lines_with_fake_client(self):
        from PIL import Image
        tmp = Path(tempfile.mkdtemp())
        img = tmp / "p.png"
        Image.new("L", (1000, 1300), 255).save(img)
        client = SimpleNamespace(detect_document_text=lambda Document: {"Blocks": [
            {"BlockType": "LINE", "Text": "second line", "Confidence": 91.0,
             "Geometry": {"BoundingBox": {"Left": 0.1, "Top": 0.5, "Width": 0.5, "Height": 0.02}}},
            {"BlockType": "WORD", "Text": "ignored"},
            {"BlockType": "LINE", "Text": "MINERAL DEED", "Confidence": 99.5,
             "Geometry": {"BoundingBox": {"Left": 0.1, "Top": 0.1, "Width": 0.3, "Height": 0.02}}},
        ]})
        lines = ocr.ocr_textract(img, client)
        self.assertEqual([x.text for x in lines], ["MINERAL DEED", "second line"])
        self.assertAlmostEqual(lines[1].confidence, 0.91)
        self.assertEqual(lines[0].box, [100, 130, 400, 156])


@unittest.skipUnless(HAS_PIL and HAS_TESSERACT, "needs Pillow and tesseract")
class TesseractEndToEndTests(unittest.TestCase):
    def test_sample_deed_reads_and_feeds_extract(self):
        tmp = Path(tempfile.mkdtemp())
        img = sample(tmp)
        pages = ocr.run([img], tmp / "ocr", engine="tesseract")
        text = "\n".join(x.text for x in pages[0].lines)
        for phrase in ("MINERAL DEED", "one-half (1/2) of the one-eighth (1/8)", "Section 12, Block 33",
                       "Vol. 88, Page 77", "fifteen (15) years"):
            self.assertIn(phrase, text)
        lines = line_table([Page(img, ocr_lines_for(img, tmp / "ocr"))])
        self.assertEqual(lines["p1.l1"], "MINERAL DEED")
        self.assertTrue((tmp / "ocr" / "sample-deed.ocr.json").exists())

    @unittest.skipUnless(shutil.which("pdftoppm"), "needs pdftoppm")
    def test_pdf_is_split_into_pages(self):
        from PIL import Image
        tmp = Path(tempfile.mkdtemp())
        img = sample(tmp)
        pdf = tmp / "deed.pdf"
        with Image.open(img) as im:
            im.save(pdf, "PDF", resolution=200)
        pages = ocr.run([pdf], tmp / "ocr", engine="tesseract", dpi=150)
        self.assertEqual(Path(pages[0].image).name, "deed-p1.png")
        self.assertTrue((tmp / "ocr" / "deed-p1.txt").read_text().startswith("MINERAL DEED"))


if __name__ == "__main__":
    unittest.main()
