# document-pii-masker

Masks personal and company data in PDF documents (mostly Polish) before they go into a RAG system.

```
.\.venv\Scripts\Activate.ps1
masker -mask c:\docs\contract.pdf
```

produces

- `contract.masked.pdf` – a real redaction: the original text is **removed** from the PDF, not just covered, and a placeholder is drawn in its place,
- `contract.masked.txt` – the masked text in reading order. **Feed this file to RAG.** PDF text extractors put redaction placeholders at the end of the page, out of context.

Example `.txt` output:

```
Umowa najmu zawarta pomiędzy [FIRMA], NIP [NIP_FIRMY], [ADRES_FIRMY],
[KOD_POCZTOWY_1] Warszawa, a Panem [KLIENT_NOWAK], PESEL [PESEL_1], tel. [TELEFON_1] ...
```

## How it works

| Layer | What it finds | Notes |
|---|---|---|
| Dictionary (`dictionary.toml`) | your company, family members, key clients, addresses | Highest priority. Same entity gets the same label in every document, so RAG can reason about "[KLIENT_NOWAK]" across contracts. |
| Regex + checksums | PESEL, NIP, IBAN/account no., ID card, REGON, KRS, BDO, e-mail, phone, street address, postal code | PESEL, NIP, IBAN and ID card are checksum-validated → few false positives. |
| spaCy NER (optional, `--ner`) | other person names | Polish model `pl_core_news_lg`. Catches names you did not put in the dictionary. |

Within one document, equal values share a placeholder (`[PESEL_1]`, `[PESEL_2]`, ...), so the text stays understandable ("the person [PESEL_1] signs...").

Code: [masker/detect.py](masker/detect.py) (detection), [masker/pdf.py](masker/pdf.py) (PDF redaction), [masker/cli.py](masker/cli.py) (command line).

## Install

Python 3.11+.

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -e .
# optional, person-name detection (~500 MB model):
pip install -e .[ner]
python -m spacy download pl_core_news_lg
```

## Dictionary

```powershell
copy dictionary.example.toml dictionary.toml
```

and fill in your data. `dictionary.toml` contains personal data and is git-ignored. Format:

```toml
FIRMA = ["Kowalski i Synowie Sp. z o.o.", "Kowalski i Synowie"]
WLASCICIEL = ["Jan* Kowalsk*"]   # * = any word ending (Polish inflection: Jana Kowalskiego, Janowi Kowalskiemu)
WSPOLNIK = ["Łukasz* Żółkiewsk*"]  # case- and diacritic-insensitive: also ŁUKASZA ŻÓŁKIEWSKIEGO, Lukasz Zolkiewski
ALLOW = ["Kodeks Cywilny"]       # never mask
```

Only the labels (keys such as `WSPOLNIK`) must be ASCII, because they are drawn into the PDF with a font that has no Polish glyphs. Terms can contain any characters.

## Usage

```powershell
masker -mask c:\docs\contract.pdf                 # one file, dictionary.toml from current folder
masker -mask c:\docs -o c:\rag-input               # whole folder (recursive) to another folder
masker -mask c:\docs\contract.pdf --dry-run        # only show what would be masked
masker -mask c:\docs\contract.pdf --ner            # + person names via spaCy
masker -mask c:\docs\contract.pdf --map            # + contract.masked.map.json (placeholder -> original)
masker -mask c:\docs\contract.pdf -d d:\secure\dictionary.toml
```

Exit code `1` means some value was detected but is still visible in the output PDF, for example text split across columns in an unusual way. The tool prints those values, so check them manually.

`--map` lets you translate RAG answers back ("[KLIENT_NOWAK]" → "Piotr Nowak"). The map file contains the original PII, so keep it outside the RAG and outside git.

## Scanned documents

Scans have no text layer, so nothing can be detected. The tool warns: `pages without text`. OCR them first, for example with [OCRmyPDF](https://ocrmypdf.readthedocs.io/) (needs Tesseract with the Polish language pack):

```powershell
ocrmypdf -l pol scan.pdf scan.ocr.pdf
masker -mask scan.ocr.pdf
```

Image pixels under each redaction box are blanked as well. OCR can misread text, though, so always check masked scans visually.

## Recommended workflow for the RAG

1. Add your own company, family and regular clients to `dictionary.toml` once. That covers most real-world PII in a small business.
2. On a new batch, run `--dry-run` with `--ner` first and look for misses or false positives. Add misses to the dictionary and false positives to `ALLOW`.
3. Run masking into a separate folder (`-o`) and ingest only `*.masked.txt` into the RAG.
4. Keep originals and `*.map.json` in a separate, access-controlled location.

## Limitations

- Automatic detection is best-effort. Names that are not in the dictionary are only found with `--ner`, and NER is not perfect. Treat the dictionary as the main safety net.
- Matching is text-based. A value is redacted everywhere it appears on the page, including inside longer words (over-masking is preferred to leaks).
- Images, signatures and stamps inside PDFs are not analysed.

## Tests

```powershell
pip install -e .[dev]
pytest
```