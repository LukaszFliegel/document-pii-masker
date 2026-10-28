"""Detection of (mostly Polish) PII in plain text."""
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Finding:
    start: int
    end: int
    label: str
    text: str
    fixed: bool = False  # dictionary label is used verbatim, without numbering


# 1:1 replacements, so offsets in cleaned text match the original.
_CLEAN = str.maketrans({c: " " for c in "\xa0\u2007\u2009\u202f"} | {c: "-" for c in "\xad\u2010\u2011\u2012\u2013\u2014\u2212"})


def norm(s: str) -> str:
    return " ".join(s.split())


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def _weighted(values, weights) -> int:
    return sum(int(v) * w for v, w in zip(values, weights))


def valid_pesel(s: str) -> bool:
    d = _digits(s)
    return len(d) == 11 and (10 - _weighted(d, (1, 3, 7, 9, 1, 3, 7, 9, 1, 3)) % 10) % 10 == int(d[10])


def valid_nip(s: str) -> bool:
    d = _digits(s)
    return len(d) == 10 and _weighted(d, (6, 5, 7, 2, 3, 4, 5, 6, 7)) % 11 == int(d[9])


def valid_iban(s: str) -> bool:
    d = _digits(s)
    return len(d) == 26 and int(d[2:] + "2521" + d[:2]) % 97 == 1  # "PL" -> 25 21


def valid_id_card(s: str) -> bool:
    s = s.replace(" ", "").upper()
    values = [ord(c) - 55 for c in s[:3]] + [int(c) for c in s[3:]]
    return _weighted(values, (7, 3, 1, 9, 7, 3, 1, 7, 3)) % 10 == 0


# Order = priority when matches overlap. A capture group, if present, marks the part to mask.
PATTERNS = [
    ("PESEL", r"(?<!\d)\d{11}(?!\d)", valid_pesel),
    ("KONTO", r"(?<!\w)(?:PL ?)?\d{2}(?: ?\d{4}){6}(?!\w)", valid_iban),
    ("KRS", r"(?<!\d)000\d{7}(?!\d)", None),  # KRS numbers are always zero-padded to 10 digits
    ("NIP", r"(?<![\w-])(?:PL ?)?(?:\d{3}[- ]?\d{3}[- ]?\d{2}[- ]?\d{2}|\d{3}[- ]?\d{2}[- ]?\d{2}[- ]?\d{3})(?![\w-])", valid_nip),
    ("REGON", r"(?i:REGON)\W{0,3}(\d{9}(?:\d{5})?)(?!\d)", None),
    ("BDO", r"(?i:BDO)(?:\s+(?i:nr|no)\.?)?\W{0,3}(\d{6,9})(?!\d)", None),
    ("DOWOD", r"(?<!\w)[A-Z]{3} ?\d{6}(?!\w)", valid_id_card),
    ("EMAIL", r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", None),
    ("TELEFON", r"(?<![\w+])(?:\+48[ -]?)?(?:[4-8]\d{2}[ -]?\d{3}[ -]?\d{3}|\(?\d{2}\)?[ -]?\d{3}[ -]?\d{2}[ -]?\d{2})(?![\w-])", None),
    ("ADRES", r"(?<![\w.])(?i:ul\.|al\.|pl\.|os\.|ulica|aleja|plac|osiedle)\s*(?:\S+\s+){1,4}?\d+[a-zA-Z]?(?:\s*/\s*\d+[a-zA-Z]?)?(?:\s*(?i:m\.|lok\.)\s*\d+)?(?!\w)", None),
    ("KOD_POCZTOWY", r"(?<![\w-])\d{2}\s?-\s?\d{3}(?![\w-])", None),
]
PATTERNS = [(label, re.compile(rx), check) for label, rx, check in PATTERNS]
NUMERIC = {"PESEL", "KONTO", "NIP", "REGON", "KRS", "BDO", "TELEFON"}

# Each letter matches its Polish variants both ways: "Lukasz" ~ "Łukasz" (English versions drop diacritics).
_FOLD = {c: f"[{g}]" for g in ("aą", "cć", "eę", "lł", "nń", "oó", "sś", "zźż") for c in g}


def _term_regex(term: str) -> re.Pattern:
    words = ["".join(_FOLD.get(c.lower(), re.escape(c)) for c in w)
             .replace(r"\*", r"\w*").replace(r"\-", r"\s*-\s*") for w in term.translate(_CLEAN).split()]
    return re.compile(r"(?<!\w)" + r"\s+".join(words) + r"(?!\w)", re.IGNORECASE)


@dataclass
class Dictionary:
    terms: list[tuple[str, re.Pattern]] = field(default_factory=list)
    allow: set[str] = field(default_factory=set)

    @classmethod
    def from_dict(cls, data: dict[str, list[str]]) -> Dictionary:
        data = dict(data)
        allow = {norm(a).casefold() for a in data.pop("ALLOW", [])}
        return cls([(label, _term_regex(t)) for label, terms in data.items() for t in terms], allow)

    @classmethod
    def load(cls, path: Path) -> Dictionary:
        return cls.from_dict(tomllib.loads(Path(path).read_text(encoding="utf-8")))


def find_pii(text: str, dictionary: Dictionary | None = None, nlp=None) -> list[Finding]:
    clean = text.translate(_CLEAN)
    found: list[tuple[int, Finding]] = []
    if dictionary:
        found += [(0, Finding(m.start(), m.end(), label, text[m.start():m.end()], fixed=True))
                  for label, rx in dictionary.terms for m in rx.finditer(clean)]
    for prio, (label, rx, check) in enumerate(PATTERNS, start=1):
        g = 1 if rx.groups else 0
        found += [(prio, Finding(m.start(g), m.end(g), label, text[m.start(g):m.end(g)]))
                  for m in rx.finditer(clean) if check is None or check(m.group(g))]
    if nlp is not None:
        found += [(99, Finding(e.start_char, e.end_char, "OSOBA", text[e.start_char:e.end_char]))
                  for e in nlp(clean).ents if e.label_ == "persName"]

    allow = dictionary.allow if dictionary else set()
    kept: list[Finding] = []
    for _, f in sorted(found, key=lambda pf: (pf[0], pf[1].start - pf[1].end)):
        if norm(f.text).casefold() not in allow and all(f.end <= k.start or f.start >= k.end for k in kept):
            kept.append(f)
    return sorted(kept, key=lambda f: f.start)


def placeholders(findings) -> dict[str, str]:
    """Map normalized found text to a placeholder; equal values share one placeholder."""
    by_key: dict[tuple[str, str], str] = {}
    counters: dict[str, int] = {}
    out: dict[str, str] = {}
    for f in findings:
        key = (f.label, _digits(f.text) if f.label in NUMERIC else norm(f.text).casefold())
        if key not in by_key:
            if f.fixed:
                by_key[key] = f"[{f.label}]"
            else:
                counters[f.label] = counters.get(f.label, 0) + 1
                by_key[key] = f"[{f.label}_{counters[f.label]}]"
        out[norm(f.text)] = by_key[key]
    return out


def mask_text(text: str, findings: list[Finding], mapping: dict[str, str]) -> str:
    out, pos = [], 0
    for f in findings:
        out += [text[pos:f.start], mapping[norm(f.text)]]
        pos = f.end
    return "".join(out) + text[pos:]
