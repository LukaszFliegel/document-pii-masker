"""Applying masks to PDF files."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from .detect import Dictionary, Finding, find_pii, mask_text, norm, placeholders

FILL = (0.85, 0.85, 0.85)


@dataclass
class Result:
    findings: list[tuple[int, Finding]]  # (page number, finding)
    mapping: dict[str, str]  # original text -> placeholder
    leaks: list[str]  # originals still present in the output
    no_text_pages: list[int]


def _find(page: pymupdf.Page, value: str) -> list[pymupdf.Rect]:
    # MuPDF search joins "41-" + newline + "503" into "41503".
    return page.search_for(value) or page.search_for(re.sub(r"[-\xad]\s+", "", value))


def _label(page: pymupdf.Page, rect: pymupdf.Rect, text: str) -> None:
    size = max(3, min(rect.height * 0.7, 0.95 * rect.width / pymupdf.get_text_length(text, "helv", 1)))
    page.insert_text((rect.x0, rect.y1 - rect.height * 0.25), text, fontname="helv", fontsize=size)


def mask_pdf(src: Path, dst: Path | None, dictionary: Dictionary | None = None, nlp=None) -> Result:
    """Mask PII in `src`, save to `dst` plus a `.txt` with masked text. With `dst=None` only detects."""
    with pymupdf.open(src) as doc:
        # Block by block, so side-by-side columns (e.g. PL/EN contracts) are not merged line by line.
        texts = ["\n".join(b[4] for b in page.get_text("blocks", sort=True) if b[6] == 0) for page in doc]
        findings = [(i, f) for i, t in enumerate(texts, 1) for f in find_pii(t, dictionary, nlp)]
        mapping = placeholders(f for _, f in findings)
        no_text = [i for i, t in enumerate(texts, 1) if not t.strip()]
        if dst is None:
            return Result(findings, mapping, [], no_text)

        # PDF extractors return redaction text out of reading order, so RAG should ingest this file.
        pages = [mask_text(t, [f for p, f in findings if p == i], mapping) for i, t in enumerate(texts, 1)]
        Path(dst).with_suffix(".txt").write_text(
            "".join(f"--- strona {i} ---\n{t}\n" for i, t in enumerate(pages, 1)), encoding="utf-8")

        for page_no, page in enumerate(doc, 1):
            labels = [(rect, mapping[value])
                      for value in {norm(f.text) for p, f in findings if p == page_no}
                      for rect in _find(page, value)]
            for rect, _ in labels:
                page.add_redact_annot(rect, fill=FILL)
            page.apply_redactions()
            # Drawn separately: PyMuPDF silently drops redaction text that does not fit the box.
            for rect, text in labels:
                _label(page, rect, text)
        # Keep invisible text: OCR-ed scans store their text layer that way.
        doc.scrub(hidden_text=False)
        doc.save(dst, garbage=4, deflate=True)

    with pymupdf.open(dst) as out:
        masked = norm(" ".join(page.get_text() for page in out)).casefold()
    leaks = sorted(v for v in mapping if v.casefold() in masked)
    return Result(findings, mapping, leaks, no_text)
