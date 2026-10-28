"""Command line entry point: masker -mask <pdf|folder> ..."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .detect import Dictionary
from .pdf import mask_pdf

DEFAULT_DICT = Path("dictionary.toml")


def _pdfs(paths: list[str]):
    for p in map(Path, paths):
        if p.is_dir():
            yield from (f for f in sorted(p.rglob("*.pdf")) if not f.stem.endswith(".masked"))
        else:
            yield p


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="masker", description="Mask PII (mostly Polish) in PDF documents.")
    ap.add_argument("-mask", "--mask", nargs="+", required=True, metavar="PATH", help="PDF file(s) or folder(s)")
    ap.add_argument("-o", "--out-dir", type=Path, help="output folder (default: next to the input file)")
    ap.add_argument("-d", "--dict", type=Path, help=f"dictionary file (default: {DEFAULT_DICT} if present)")
    ap.add_argument("--ner", nargs="?", const="pl_core_news_lg", metavar="MODEL",
                    help="also detect person names with a spaCy model (default: pl_core_news_lg)")
    ap.add_argument("--dry-run", action="store_true", help="only list what would be masked")
    ap.add_argument("--map", action="store_true", help="write <name>.map.json with originals (contains PII!)")
    args = ap.parse_args(argv)

    dict_path = args.dict or (DEFAULT_DICT if DEFAULT_DICT.exists() else None)
    dictionary = Dictionary.load(dict_path) if dict_path else None
    if not dictionary:
        print(f"warning: no dictionary ({DEFAULT_DICT}) - company-specific names will not be masked")

    nlp = None
    if args.ner:
        try:
            import spacy
            nlp = spacy.load(args.ner)
        except (ImportError, OSError):
            print(f"error: --ner needs spaCy and the '{args.ner}' model. Install with:\n"
                  f"  pip install -e .[ner]\n  python -m spacy download {args.ner}")
            return 2

    if args.out_dir:
        args.out_dir.mkdir(parents=True, exist_ok=True)

    status = 0
    for src in _pdfs(args.mask):
        if not src.is_file():
            print(f"error: {src} not found")
            status = 2
            continue
        dst = None if args.dry_run else (args.out_dir or src.parent) / f"{src.stem}.masked.pdf"
        res = mask_pdf(src, dst, dictionary, nlp)
        print(f"{src}: {len(res.findings)} item(s) masked" + (f" -> {dst} (+ .txt for RAG)" if dst else " (dry run)"))
        if args.dry_run:
            for page, f in res.findings:
                print(f"  p.{page:<3} {f.label:<14} {' '.join(f.text.split())!r}")
        if res.no_text_pages:
            print(f"  warning: pages without text {res.no_text_pages} - scanned? run OCR first (see README)")
        if res.leaks:
            print(f"  WARNING: still visible in output, check manually: {res.leaks}")
            status = 1
        if args.map and dst:
            inverse: dict[str, list[str]] = {}
            for original, ph in res.mapping.items():
                inverse.setdefault(ph, []).append(original)
            dst.with_suffix(".map.json").write_text(json.dumps(inverse, ensure_ascii=False, indent=2), encoding="utf-8")
    return status
