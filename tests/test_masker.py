import pymupdf

from masker.detect import Dictionary, find_pii, placeholders
from masker.pdf import mask_pdf


def labels(text, dictionary=None):
    return [(f.label, f.text) for f in find_pii(text, dictionary)]


def test_checksummed_ids():
    text = "PESEL 44051401359, NIP 526-104-08-28, dowód ABA 300000, konto PL61 1090 1014 0000 0712 1981 2874"
    assert labels(text) == [
        ("PESEL", "44051401359"),
        ("NIP", "526-104-08-28"),
        ("DOWOD", "ABA 300000"),
        ("KONTO", "PL61 1090 1014 0000 0712 1981 2874"),
    ]


def test_invalid_checksums_ignored():
    assert labels("PESEL 44051401358, NIP 526-104-08-29") == []


def test_contact_and_address():
    text = "tel. +48 601 234 567, e-mail jan.kowalski@firma.pl, ul. Jana Pawła II 12/4, 00-950 Warszawa, REGON: 123456789"
    found = labels(text)
    assert ("TELEFON", "+48 601 234 567") in found
    assert ("EMAIL", "jan.kowalski@firma.pl") in found
    assert ("ADRES", "ul. Jana Pawła II 12/4") in found
    assert ("KOD_POCZTOWY", "00-950") in found
    assert ("REGON", "123456789") in found


def test_dictionary_inflection_and_allow():
    d = Dictionary.from_dict({"WLASCICIEL": ["Jan* Kowalsk*"], "ALLOW": ["00-950"]})
    found = labels("Umowa z Janem Kowalskim, 00-950 Warszawa", d)
    assert found == [("WLASCICIEL", "Janem Kowalskim")]


def test_dictionary_ignores_diacritics_and_hyphen_breaks():
    d = Dictionary.from_dict({"WLASCICIEL": ["Łukasz* Fliegel*"], "ADRES_KLIENTA": ["Kraków (31-323)"]})
    found = labels("przez Łukasza\nFliegela, by Lukasz Fliegel, LUKASZ FLIEGEL, Krakow (31-\n323)", d)
    assert [t for _, t in found] == ["Łukasza\nFliegela", "Lukasz Fliegel", "LUKASZ FLIEGEL", "Krakow (31-\n323)"]


def test_krs_bdo_postal_code_variants():
    found = labels("pod numerem 0000390124, nr BDO 000123456, BDO No 000123456, Kraków (31 – 323), 41-\n503 Chorzow")
    assert found == [("KRS", "0000390124"), ("BDO", "000123456"), ("BDO", "000123456"),
                     ("KOD_POCZTOWY", "31 – 323"), ("KOD_POCZTOWY", "41-\n503")]


def test_unicode_spaces_and_dashes():
    found = labels("NIP\xa0526\xad104\u201308\u201328, tel.\xa0601\xa0234\xa0567")
    assert [label for label, _ in found] == ["NIP", "TELEFON"]
    assert found[0][1] == "526\xad104\u201308\u201328"


def test_placeholders_consistent():
    findings = find_pii("NIP 5261040828 oraz NIP 526-104-08-28, PESEL 44051401359")
    assert placeholders(findings) == {"5261040828": "[NIP_1]", "526-104-08-28": "[NIP_1]", "44051401359": "[PESEL_1]"}


def test_mask_pdf(tmp_path):
    src, dst = tmp_path / "in.pdf", tmp_path / "out.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "Klient: Jan Kowalski, PESEL 44051401359, NIP 526-104-08-28, 00-950 Warszawa")
    doc.save(src)

    res = mask_pdf(src, dst, Dictionary.from_dict({"KLIENT_A": ["Jan* Kowalsk*"]}))

    text = pymupdf.open(dst)[0].get_text()
    assert "44051401359" not in text and "Kowalski" not in text
    assert "[PESEL_1]" in text and "[NIP_1]" in text and "[KLIENT_A]" in text
    assert "[KOD_POCZTOWY_1]" in text  # label much wider than the masked value
    assert res.leaks == []
    assert "Klient: [KLIENT_A], PESEL [PESEL_1], NIP [NIP_1]" in dst.with_suffix(".txt").read_text(encoding="utf-8")


def test_mask_pdf_polish_diacritics(tmp_path):
    src, dst = tmp_path / "in.pdf", tmp_path / "out.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_htmlbox(pymupdf.Rect(50, 50, 550, 300),
                                  "Podpisał Łukasz Żółkiewski. Płatność dla ŁUKASZA ŻÓŁKIEWSKIEGO.")
    doc.save(src)

    res = mask_pdf(src, dst, Dictionary.from_dict({"WSPOLNIK": ["Łukasz* Żółkiewsk*"]}))

    assert res.leaks == []
    assert "Podpisał [WSPOLNIK]. Płatność dla [WSPOLNIK]." in dst.with_suffix(".txt").read_text(encoding="utf-8")


def test_mask_pdf_two_columns(tmp_path):
    src, dst = tmp_path / "in.pdf", tmp_path / "out.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_htmlbox(pymupdf.Rect(50, 50, 290, 300), "Siedziba: ul. Testowa 1, 41-503 Chorzów, reprezentowaną "
                        "przez Łukasza Fliegel, zwaną dalej Wykonawcą, pod numerem 0000939273.")
    page.insert_htmlbox(pymupdf.Rect(310, 50, 550, 300), "Seat: Testowa 1, 41-503 Chorzow represented by "
                        "Lukasz Fliegel, hereinafter referred to as the Contractor, number 0000939273.")
    doc.save(src)

    res = mask_pdf(src, dst, Dictionary.from_dict({"WLASCICIEL": ["Łukasz* Fliegel*"]}))

    text = pymupdf.open(dst)[0].get_text()
    assert "Fliegel" not in text and "0000939273" not in text and "503" not in text
    assert res.leaks == []


def test_mask_pdf_value_broken_after_hyphen(tmp_path):
    src, dst = tmp_path / "in.pdf", tmp_path / "out.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "with the seat at 41-")
    page.insert_text((72, 114), "503 Chorzow")
    doc.save(src)

    res = mask_pdf(src, dst)

    assert "503" not in pymupdf.open(dst)[0].get_text()
    assert res.leaks == []
