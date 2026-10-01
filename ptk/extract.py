"""First-pass abstracting of a recorded instrument with Claude.

Sends the page images (or a PDF) plus OCR text with stable line IDs, and
gets back JSON that matches ``schema/instrument.schema.json`` exactly
(structured outputs). Then it checks the answer instead of trusting it:

* every ``evidence.quote`` must actually appear in the OCR lines it cites
* every fraction is re-read, and words that disagree with numerals are flagged
* refusals and truncated answers are reported, never parsed

The output is a draft for a landman to verify against the image. Fields that
change ownership always get a person (Tier A review in the playbook).

Requires ``pip install anthropic`` and credentials (ANTHROPIC_API_KEY or an
``ant auth login`` profile).
"""

from __future__ import annotations

import base64
import difflib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .fracs import fmt, read_fraction_text

SCHEMA_PATH = Path(__file__).with_name("schema") / "instrument.schema.json"
DEFAULT_MODEL = "claude-opus-5-5"
# Server-side refusal fallback is supported on these models (not on Batches).
FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MEDIA_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
               ".webp": "image/webp", ".pdf": "application/pdf"}

SYSTEM_PROMPT = """You abstract recorded instruments for oil and gas runsheets in the Texas and New Mexico Permian Basin.
A landman will check every field you return against the page image, and an attorney decides every legal question.
Your job is an accurate, verbatim first draft.

Rules:
1. Extract only what is on the pages. If a value isn't there, return null (or an empty list).
2. Copy names, fractions, legal descriptions and recording references exactly as written. Keep the original
   spelling, abbreviations and punctuation. For every fraction, copy both the words and the numerals.
3. Never guess an illegible character, digit or word. Write [illegible] in the quote and add an "illegible" flag.
4. Every evidence entry gives the page number, the OCR line_ids it relies on (like "p2.l14"), and a verbatim quote
   copied from those lines. If no OCR text was provided, leave line_ids empty and quote the image text.
5. Record each fraction as factors ("1/2 of 1/8" is two factors: 1/2 and 1/8) and the basis exactly as stated
   (of the minerals, of grantor's interest, of royalty, of production). Report fixed_or_floating_signal only as
   the wording suggests it. Don't decide fixed vs floating royalty, whether Duhig applies, or who the heirs are.
6. Add a flag for: written fraction different from its numeral, double fractions, term language ("for __ years and
   as long thereafter"), conveyances of "all my interest in __ County", a spouse who didn't join, unclear signing
   capacity (trustee, executor, attorney-in-fact, officer), interlineations or corrections, missing pages, no
   acknowledgment, marginal notations, and any conveyance larger than the grantor appears to own.
7. List every reservation, exception, "save and except" and "subject to" clause separately with its full text.
8. One page image can hold the end of one instrument and the start of another. Extract only the instrument
   named in the request, and flag text that belongs to a different instrument.
9. Party IDs are short labels you assign (P1, P2, ...) and use consistently in party_refs.
"""


def load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


@dataclass
class Page:
    path: Path
    ocr_lines: list[str] = field(default_factory=list)


def ocr_lines_for(page_path: Path, ocr_dir: Path | None) -> list[str]:
    """Read OCR text for a page from ``<ocr_dir>/<page stem>.txt`` (one line per OCR line)."""
    if ocr_dir is None:
        return []
    txt = ocr_dir / f"{page_path.stem}.txt"
    if not txt.exists():
        return []
    return [line.rstrip() for line in txt.read_text(encoding="utf-8", errors="replace").splitlines()]


def line_table(pages: list[Page]) -> dict[str, str]:
    """Stable IDs for every OCR line: p1.l1, p1.l2, ..."""
    table: dict[str, str] = {}
    for p_idx, page in enumerate(pages, start=1):
        for l_idx, line in enumerate(page.ocr_lines, start=1):
            if line.strip():
                table[f"p{p_idx}.l{l_idx}"] = line
    return table


def build_params(pages: list[Page], *, doc_id: str, county: str, state: str,
                 model: str = DEFAULT_MODEL, effort: str = "high", max_tokens: int = 16000) -> dict[str, Any]:
    """Messages API parameters for one instrument. The system prompt is the cached prefix."""
    content: list[dict[str, Any]] = []
    for idx, page in enumerate(pages, start=1):
        media = MEDIA_TYPES.get(page.path.suffix.lower())
        if media is None:
            raise ValueError(f"{page.path}: use PNG, JPEG, GIF, WebP or PDF")
        data = base64.standard_b64encode(page.path.read_bytes()).decode("utf-8")
        content.append({"type": "text", "text": f"Page {idx} ({page.path.name}):"})
        block_type = "document" if media == "application/pdf" else "image"
        content.append({"type": block_type, "source": {"type": "base64", "media_type": media, "data": data}})
    lines = line_table(pages)
    if lines:
        ocr_text = "\n".join(f"{lid}: {text}" for lid, text in lines.items())
        content.append({"type": "text", "text": f"OCR text with line IDs:\n{ocr_text}"})
    else:
        content.append({"type": "text", "text": "No OCR text is available. Read the images directly."})
    content.append({"type": "text", "text": (
        f"Abstract this instrument. doc_id: {doc_id}. Recorded in {county} County, {state}. "
        "Return JSON that matches the schema."
    )})
    return {
        "model": model,
        "max_tokens": max_tokens,
        "system": [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": content}],
        "output_config": {"effort": effort, "format": {"type": "json_schema", "schema": load_schema()}},
    }


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _walk(node: Any, path: str = "$"):
    if isinstance(node, dict):
        yield path, node
        for k, v in node.items():
            yield from _walk(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk(v, f"{path}[{i}]")


def verify(extraction: dict[str, Any], lines: dict[str, str], threshold: float = 0.85) -> list[dict[str, str]]:
    """Checks that don't trust the model: quotes against OCR lines, fraction words against numerals."""
    problems: list[dict[str, str]] = []
    for path, node in _walk(extraction):
        for ev in node.get("evidence", []) if isinstance(node.get("evidence"), list) else []:
            quote, ids = ev.get("quote") or "", ev.get("line_ids") or []
            if not lines or not ids:
                continue
            missing = [i for i in ids if i not in lines]
            if missing:
                problems.append({"path": path, "code": "UNKNOWN_LINE_ID", "detail": f"cites {missing}"})
                continue
            cited = _norm(" ".join(lines[i] for i in ids))
            q = _norm(quote.replace("[illegible]", ""))
            if q and q not in cited:
                ratio = difflib.SequenceMatcher(None, q, cited).find_longest_match(0, len(q), 0, len(cited)).size / max(len(q), 1)
                if ratio < threshold:
                    problems.append({"path": path, "code": "QUOTE_NOT_IN_CITED_LINES",
                                     "detail": f"quote {quote[:80]!r} doesn't match {ids}"})
        if "text_verbatim" in node and "factors" in node:
            reading = read_fraction_text_safe(node.get("text_verbatim") or "")
            if reading and reading.consistent is False:
                problems.append({"path": path, "code": "WORD_NUMERAL_MISMATCH",
                                 "detail": f"{node['text_verbatim']!r}: words {fmt(reading.words_value)}, numerals {fmt(reading.numeral_value)}"})
    return problems


def read_fraction_text_safe(text: str):
    match = re.search(r"((?:[a-z]+[-\s]){0,3}[a-z]+\s*\([^)]*\d[^)]*\))", text, re.I)
    if not match:
        return None
    reading = read_fraction_text(match.group(1))
    return reading if reading.value is not None or reading.consistent is False else None


def _result(doc_id: str, message: Any, lines: dict[str, str]) -> dict[str, Any]:
    usage = getattr(message, "usage", None)
    out: dict[str, Any] = {
        "doc_id": doc_id,
        "model": getattr(message, "model", None),
        "stop_reason": message.stop_reason,
        "usage": usage.to_dict() if usage is not None and hasattr(usage, "to_dict") else None,
    }
    if message.stop_reason == "refusal":
        details = getattr(message, "stop_details", None)
        out["error"] = f"declined ({getattr(details, 'category', None)}); abstract this one by hand"
        return out
    if message.stop_reason == "max_tokens":
        out["error"] = "answer was cut off at max_tokens; raise --max-tokens and rerun"
        return out
    text = next((b.text for b in message.content if b.type == "text"), None)
    if text is None:
        out["error"] = "no text block in the response"
        return out
    extraction = json.loads(text)
    out["extraction"] = extraction
    out["checks"] = verify(extraction, lines)
    return out


def extract(client: Any, pages: list[Page], *, doc_id: str, county: str, state: str,
            model: str = DEFAULT_MODEL, effort: str = "high", max_tokens: int = 16000) -> dict[str, Any]:
    """One instrument, synchronously. Uses server-side refusal fallback where the model supports it."""
    params = build_params(pages, doc_id=doc_id, county=county, state=state,
                          model=model, effort=effort, max_tokens=max_tokens)
    if model in FALLBACK_MODELS:
        message = client.beta.messages.create(**params, betas=[FALLBACK_BETA], fallbacks="default")
    else:
        message = client.messages.create(**params)
    return _result(doc_id, message, line_table(pages))


def custom_id(doc_id: str) -> str:
    """Batch custom_id for a document: letters, digits, _ and - only, at most 64 characters."""
    return re.sub(r"[^A-Za-z0-9_-]", "_", doc_id)[:64]


def submit_batch(client: Any, jobs: list[dict[str, Any]]) -> str:
    """Queue many instruments at half price. ``jobs``: dicts with doc_id, pages, county, state, model, effort."""
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    requests = [
        Request(
            custom_id=custom_id(job["doc_id"]),
            params=MessageCreateParamsNonStreaming(**build_params(
                job["pages"], doc_id=job["doc_id"], county=job["county"], state=job["state"],
                model=job.get("model", DEFAULT_MODEL), effort=job.get("effort", "high"))),
        )
        for job in jobs
    ]
    batch = client.messages.batches.create(requests=requests)
    return batch.id


def collect_batch(client: Any, batch_id: str, lines_by_id: dict[str, dict[str, str]] | None = None) -> dict[str, Any]:
    """Results keyed by custom_id (they arrive in any order). Returns None-filled entries while running."""
    batch = client.messages.batches.retrieve(batch_id)
    if batch.processing_status != "ended":
        return {"status": batch.processing_status, "results": {}}
    results: dict[str, Any] = {}
    for item in client.messages.batches.results(batch_id):
        kind = item.result.type
        if kind == "succeeded":
            results[item.custom_id] = _result(item.custom_id, item.result.message,
                                              (lines_by_id or {}).get(item.custom_id, {}))
        elif kind == "errored":
            results[item.custom_id] = {"doc_id": item.custom_id, "error": f"errored: {item.result.error.type}"}
        else:
            results[item.custom_id] = {"doc_id": item.custom_id, "error": f"{kind}: resubmit"}
    return {"status": "ended", "results": results}
