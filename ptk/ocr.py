"""OCR step: page images (or PDFs) in, one text file per page out.

For each page ``<stem>`` it writes:

* ``<stem>.txt``: one OCR line per line, top to bottom. ``ptk extract --ocr-dir``
  reads these and gives each line an ID (p1.l14) that the model must cite.
* ``<stem>.ocr.json``: the same lines with confidence and a pixel bounding box,
  for a review screen or for checking low-confidence lines by hand.

Engines:

* ``tesseract`` (default): local CLI (apt/brew ``tesseract``). On the sample
  typewritten deed it read every line, fractions included, at 0.81-0.97 line
  confidence.
* ``textract``: AWS Textract DetectDocumentText (``pip install boto3`` and AWS
  credentials). The managed option, about $1.50 per 1,000 pages.
* ``rapidocr``: local, no account (``pip install rapidocr-onnxruntime``). A
  fallback only: its default model, trained mostly on Chinese text, ran words
  in capitals together, misread 0 as o and dropped a line of the sample deed.

None of them read 1880s clerk handwriting well. For those books, use a
handwriting model or let ``ptk extract`` read the image directly.
PDFs are split into 300-dpi page images with ``pdftoppm`` (poppler) first.
"""

from __future__ import annotations

import csv
import io
import json
import shutil
import subprocess
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
ENGINES = ("tesseract", "textract", "rapidocr")


@dataclass
class OcrLine:
    text: str
    confidence: float  # 0..1
    box: list[int]  # x0, y0, x1, y1 in pixels


@dataclass
class OcrPage:
    image: str
    engine: str
    width: int
    height: int
    lines: list[OcrLine]

    def low_confidence(self, threshold: float = 0.8) -> list[OcrLine]:
        return [line for line in self.lines if line.confidence < threshold]


def _size(path: Path) -> tuple[int, int]:
    try:
        from PIL import Image
    except ImportError:
        return 0, 0
    with Image.open(path) as img:
        return img.size


def _merge_rows(pieces: list[OcrLine]) -> list[OcrLine]:
    """Join text boxes that sit on the same printed line, then sort top to bottom."""
    rows: list[list[OcrLine]] = []
    for piece in sorted(pieces, key=lambda p: (p.box[1], p.box[0])):
        mid = (piece.box[1] + piece.box[3]) / 2
        for row in rows:
            top = min(p.box[1] for p in row)
            bottom = max(p.box[3] for p in row)
            if top <= mid <= bottom:
                row.append(piece)
                break
        else:
            rows.append([piece])
    merged = []
    for row in rows:
        row.sort(key=lambda p: p.box[0])
        merged.append(OcrLine(
            text=" ".join(p.text for p in row),
            confidence=min(p.confidence for p in row),
            box=[min(p.box[0] for p in row), min(p.box[1] for p in row),
                 max(p.box[2] for p in row), max(p.box[3] for p in row)],
        ))
    return sorted(merged, key=lambda line: line.box[1])


def ocr_rapidocr(path: Path, engine: Any = None) -> list[OcrLine]:
    if engine is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError as e:
            raise RuntimeError("rapidocr isn't installed: pip install rapidocr-onnxruntime") from e
        engine = RapidOCR()
    result, _ = engine(str(path))
    pieces = []
    for points, text, score in result or []:
        xs = [int(p[0]) for p in points]
        ys = [int(p[1]) for p in points]
        pieces.append(OcrLine(text.strip(), float(score), [min(xs), min(ys), max(xs), max(ys)]))
    return _merge_rows([p for p in pieces if p.text])


def ocr_tesseract(path: Path, binary: str = "tesseract") -> list[OcrLine]:
    if shutil.which(binary) is None:
        raise RuntimeError("tesseract isn't installed (apt install tesseract-ocr, or brew install tesseract)")
    tsv = subprocess.run([binary, str(path), "stdout", "--psm", "4", "tsv"], check=True,
                         capture_output=True, text=True).stdout
    groups: dict[tuple[str, str, str, str], list[dict[str, str]]] = {}
    for row in csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE):
        if row.get("level") != "5" or not (row.get("text") or "").strip():
            continue
        key = (row["page_num"], row["block_num"], row["par_num"], row["line_num"])
        groups.setdefault(key, []).append(row)
    lines = []
    for words in groups.values():
        x0 = min(int(w["left"]) for w in words)
        y0 = min(int(w["top"]) for w in words)
        x1 = max(int(w["left"]) + int(w["width"]) for w in words)
        y1 = max(int(w["top"]) + int(w["height"]) for w in words)
        confs = [float(w["conf"]) for w in words if float(w["conf"]) >= 0]
        lines.append(OcrLine(" ".join(w["text"] for w in words), (min(confs) / 100) if confs else 0.0, [x0, y0, x1, y1]))
    return sorted(lines, key=lambda line: (line.box[1], line.box[0]))


def ocr_textract(path: Path, client: Any = None) -> list[OcrLine]:
    if client is None:
        try:
            import boto3
        except ImportError as e:
            raise RuntimeError("Textract needs boto3 and AWS credentials: pip install boto3") from e
        client = boto3.client("textract")
    width, height = _size(path)
    response = client.detect_document_text(Document={"Bytes": path.read_bytes()})
    found = []
    for block in response.get("Blocks", []):
        if block.get("BlockType") != "LINE":
            continue
        bb = block["Geometry"]["BoundingBox"]
        box = [int(bb["Left"] * width), int(bb["Top"] * height),
               int((bb["Left"] + bb["Width"]) * width), int((bb["Top"] + bb["Height"]) * height)]
        line = OcrLine(block.get("Text", ""), float(block.get("Confidence", 0)) / 100, box)
        found.append(((bb["Top"], bb["Left"]), line))  # order by page position, not rounded pixels
    return [line for _, line in sorted(found, key=lambda x: x[0])]


def split_pdf(pdf: Path, pages_dir: Path, dpi: int = 300) -> list[Path]:
    if shutil.which("pdftoppm") is None:
        raise RuntimeError("PDF pages need pdftoppm (apt install poppler-utils, or brew install poppler)")
    pages_dir.mkdir(parents=True, exist_ok=True)
    prefix = pages_dir / f"{pdf.stem}-ptk-split"
    subprocess.run(["pdftoppm", "-r", str(dpi), "-gray", "-png", str(pdf), str(prefix)], check=True)
    pages = []
    for raw in pages_dir.glob(f"{prefix.name}-*.png"):  # pdftoppm writes <prefix>-1.png or -01.png
        number = int(raw.stem.rsplit("-", 1)[1])
        final = pages_dir / f"{pdf.stem}-p{number}.png"
        raw.replace(final)
        pages.append((number, final))
    return [path for _, path in sorted(pages)]


def clean(text: str) -> str:
    """NFKC folds full-width punctuation ('，' '（') to ASCII; collapse runs of spaces."""
    return " ".join(unicodedata.normalize("NFKC", text).split())


def ocr_page(path: Path, engine: str = "tesseract", **engine_args: Any) -> OcrPage:
    if engine == "rapidocr":
        lines = ocr_rapidocr(path, engine_args.get("engine"))
    elif engine == "tesseract":
        lines = ocr_tesseract(path)
    elif engine == "textract":
        lines = ocr_textract(path, engine_args.get("client"))
    else:
        raise ValueError(f"engine must be one of {', '.join(ENGINES)}")
    lines = [OcrLine(clean(x.text), x.confidence, x.box) for x in lines]
    width, height = _size(path)
    return OcrPage(str(path), engine, width, height, [x for x in lines if x.text])


def write_page(page: OcrPage, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(page.image).stem
    txt = out_dir / f"{stem}.txt"
    txt.write_text("\n".join(line.text for line in page.lines) + "\n", encoding="utf-8")
    meta = out_dir / f"{stem}.ocr.json"
    meta.write_text(json.dumps({**asdict(page), "lines": [asdict(x) for x in page.lines]}, indent=1), encoding="utf-8")
    return txt, meta


def run(inputs: list[Path], out_dir: Path, engine: str = "tesseract", pages_dir: Path | None = None,
        dpi: int = 300, **engine_args: Any) -> list[OcrPage]:
    """OCR every page of every input. PDFs are split into page images in ``pages_dir``."""
    pages: list[Path] = []
    for item in inputs:
        if item.suffix.lower() == ".pdf":
            pages += split_pdf(item, pages_dir or out_dir / "pages", dpi)
        elif item.suffix.lower() in IMAGE_SUFFIXES:
            pages.append(item)
        else:
            raise ValueError(f"{item}: use an image or a PDF")
    results = []
    for page_path in pages:
        page = ocr_page(page_path, engine, **engine_args)
        write_page(page, out_dir)
        results.append(page)
    return results
