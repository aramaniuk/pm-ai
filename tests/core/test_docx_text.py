"""Story 11d — the text of a `.docx`, taken out with the standard library.

Every refusal row is built here from a small archive, so each names exactly one
thing wrong; the recorded Teams export (`fixtures/transcripts/`) is the layout
the reader is fitted to.
"""

from __future__ import annotations

import io
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from pm_ai.core.docx_text import (
    MAX_DOCUMENT_XML_BYTES,
    MAX_ENTRIES,
    MAX_TEXT_BYTES,
    DamagedEntry,
    DeclarationRefused,
    DocumentTooLarge,
    DocxRefused,
    DuplicateDocumentPart,
    EncryptedEntry,
    MalformedDocumentXml,
    NoDocumentPart,
    NotADocument,
    NotAZip,
    OldWordOrProtected,
    StrictDocument,
    TextTooLarge,
    TooManyEntries,
    UnsupportedCompression,
    docx_text,
)

FIXTURES = Path(__file__).parent / "fixtures" / "transcripts"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
STRICT = "http://purl.oclc.org/ooxml/wordprocessingml/main"
MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"


def _document(body: str, *, namespace: str = W) -> bytes:
    return (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{namespace}" xmlns:mc="{MC}"><w:body>{body}</w:body>'
        f"</w:document>"
    ).encode("utf-8")


def _archive(*entries: tuple[str, bytes], compression: int = zipfile.ZIP_DEFLATED) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression) as archive:
        for name, data in entries:
            archive.writestr(name, data)
    return buffer.getvalue()


def _docx(body: str, **kwargs: str) -> bytes:
    return _archive(("[Content_Types].xml", b"<Types/>"), ("word/document.xml", _document(body, **kwargs)))


def _p(*runs: str) -> str:
    return "<w:p>" + "".join(runs) + "</w:p>"


def _r(inner: str) -> str:
    return f"<w:r>{inner}</w:r>"


def _t(text: str) -> str:
    return f'<w:t xml:space="preserve">{text}</w:t>'


# ── What is taken out ────────────────────────────────────────────────────────


def test_the_recorded_export_is_a_line_per_paragraph_with_its_breaks():
    text = docx_text((FIXTURES / "teams_export_recorded.docx").read_bytes(), name="x.docx")
    lines = text.splitlines()
    assert lines[:3] == [
        "Example Weekly Sync-up-20260914_093000-Meeting Recording",
        "14 September 2026, 09:30am",
        "29m 03s",
    ]
    # Each turn paragraph starts with a line break, so it opens with a blank line.
    assert lines[3:6] == ["", "Alex Morgan   0:32", "Placeholder line 1 of turn 1."]
    assert lines[-1] == "Sam Rivera stopped transcription"
    assert sum(line.endswith(("0:32", "0:33")) for line in lines) == 2
    assert text.endswith("\n")


def test_tabs_and_breaks_inside_a_paragraph_are_kept():
    body = _p(
        _r(_t("a") + "<w:tab/>" + _t("b") + "<w:br/>" + _t("c") + "<w:cr/>" + _t("d")),
        _r('<w:br w:type="page"/>' + _t("e")),
    )
    assert docx_text(_docx(body), name="x") == "a\tb\nc\nd\ne\n"


def test_tab_stops_in_paragraph_properties_are_not_text():
    body = (
        '<w:p><w:pPr><w:tabs><w:tab w:val="left" w:pos="720"/></w:tabs></w:pPr>'
        + _r(_t("text"))
        + "</w:p>"
    )
    assert docx_text(_docx(body), name="x") == "text\n"


def test_hyperlinks_content_controls_and_tables_are_read_in_order():
    body = (
        _p(f'<w:hyperlink r:id="x" xmlns:r="urn:r">{_r(_t("linked"))}</w:hyperlink>')
        + f"<w:sdt><w:sdtPr/><w:sdtContent>{_p(_r(_t('controlled')))}</w:sdtContent></w:sdt>"
        + f"<w:tbl><w:tr><w:tc>{_p(_r(_t('cell one')))}</w:tc>"
        + f"<w:tc>{_p(_r(_t('cell two')))}</w:tc></w:tr></w:tbl>"
    )
    assert docx_text(_docx(body), name="x") == "linked\ncontrolled\ncell one\ncell two\n"


def test_a_tracked_insertion_is_read_and_a_deletion_is_not():
    body = _p(
        _r(_t("kept ")),
        f'<w:ins w:id="1" w:author="a">{_r(_t("inserted "))}</w:ins>',
        f'<w:del w:id="2" w:author="a"><w:r><w:delText>deleted </w:delText></w:r>'
        f"{_r(_t('also deleted '))}</w:del>",
        f'<w:moveFrom w:id="3">{_r(_t("moved away "))}</w:moveFrom>',
        _r(_t("end")),
    )
    assert docx_text(_docx(body), name="x") == "kept inserted end\n"


def test_alternative_content_is_read_once_from_its_first_choice():
    box = f"<w:txbxContent>{_p(_r(_t('in the box')))}</w:txbxContent>"
    body = _p(
        _r(_t("before ")),
        _r(
            "<mc:AlternateContent>"
            f'<mc:Choice Requires="wps"><w:drawing>{box}</w:drawing></mc:Choice>'
            f'<mc:Choice Requires="w14"><w:drawing>{_p(_r(_t("second choice")))}</w:drawing></mc:Choice>'
            f"<mc:Fallback><w:pict>{box}</w:pict></mc:Fallback>"
            "</mc:AlternateContent>"
        ),
        _r(_t("after")),
    )
    # A text box's paragraph is its own line, before the paragraph holding it.
    assert docx_text(_docx(body), name="x") == "in the box\nbefore after\n"


def test_field_codes_and_images_are_not_text():
    body = _p(
        _r("<w:instrText> HYPERLINK x </w:instrText>"),
        _r('<w:drawing><wp:docPr xmlns:wp="urn:wp" descr="an avatar"/></w:drawing>'),
        _r(_t("words")),
    )
    assert docx_text(_docx(body), name="x") == "words\n"


def test_other_parts_of_the_archive_are_never_read():
    data = _archive(
        ("word/document.xml", _document(_p(_r(_t("body"))))),
        ("word/comments.xml", _document(_p(_r(_t("a comment"))))),
        ("word/header1.xml", _document(_p(_r(_t("a header"))))),
        ("docProps/core.xml", b"<cp:coreProperties>Creator Name</cp:coreProperties>"),
    )
    assert docx_text(data, name="x") == "body\n"


# ── Refused by name, every one a refusal and never a traceback ───────────────


def _refusal(data: bytes, kind: type[DocxRefused]) -> str:
    with pytest.raises(kind) as refused:
        docx_text(data, name="minutes.docx")
    assert "minutes.docx" in str(refused.value)
    return str(refused.value)


def test_a_compound_document_is_password_protected_or_an_old_doc():
    said = _refusal(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 512, OldWordOrProtected)
    assert "password-protected, or an old .doc" in said


def test_a_file_that_is_not_a_zip_is_refused():
    assert "ZIP archive" in _refusal(b"just some text\n", NotAZip)


def test_more_than_ten_thousand_entries_are_refused():
    entries = [(f"x/{index}", b"") for index in range(MAX_ENTRIES + 1)]
    data = _archive(("word/document.xml", _document("")), *entries, compression=zipfile.ZIP_STORED)
    assert str(MAX_ENTRIES) in _refusal(data, TooManyEntries)


def test_no_document_part_is_refused():
    assert "word/document.xml" in _refusal(_archive(("other.xml", b"<x/>")), NoDocumentPart)


def test_two_document_parts_are_refused():
    data = _archive(
        ("word/document.xml", _document("")), ("WORD/Document.xml", _document(""))
    )
    _refusal(data, DuplicateDocumentPart)


def test_an_encrypted_entry_is_refused():
    data = bytearray(_docx(_p(_r(_t("x")))))
    # Set bit 0 of the general-purpose flags in every local and central header.
    for signature, offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        start = 0
        while (found := data.find(signature, start)) != -1:
            data[found + offset] |= 0x1
            start = found + 4
    _refusal(bytes(data), EncryptedEntry)


def test_an_unknown_compression_method_is_refused():
    data = bytearray(_docx(_p(_r(_t("x")))))
    for signature, offset in ((b"PK\x03\x04", 8), (b"PK\x01\x02", 10)):
        start = 0
        while (found := data.find(signature, start)) != -1:
            data[found + offset : found + offset + 2] = (98).to_bytes(2, "little")  # PPMd
            start = found + 4
    assert "method 98" in _refusal(bytes(data), UnsupportedCompression)


def test_a_bad_checksum_is_a_damaged_entry():
    data = bytearray(_archive(("word/document.xml", _document(_p(_r(_t("x"))))),
                              compression=zipfile.ZIP_STORED))
    at = data.find(b"<w:t")
    data[at + 1] = ord("W")  # the stored bytes change; the recorded CRC does not
    assert "damaged" in _refusal(bytes(data), DamagedEntry)


def test_truncated_compressed_data_is_a_damaged_entry():
    document = _document(_p(_r(_t("word " * 2000))))
    data = bytearray(_archive(("word/document.xml", document)))
    start = data.find(b"word/document.xml") + len("word/document.xml")
    data[start + 10 : start + 40] = b"\xff" * 30
    _refusal(bytes(data), DamagedEntry)


def test_a_document_part_over_64_mib_is_refused_from_the_directory():
    big = b"<w:document>" + b" " * MAX_DOCUMENT_XML_BYTES + b"</w:document>"
    data = _archive(("word/document.xml", big))
    assert "64 MiB" in _refusal(data, DocumentTooLarge)


def test_more_than_16_mib_of_text_is_refused():
    text = "a" * (MAX_TEXT_BYTES + 1)
    assert "16 MiB" in _refusal(_docx(_p(_r(_t(text)))), TextTooLarge)


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16"])
def test_a_document_type_declaration_is_refused_in_any_encoding(encoding):
    xml = (
        f'<?xml version="1.0" encoding="{encoding.upper()}"?>'
        '<!DOCTYPE w:document [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;">]>'
        f'<w:document xmlns:w="{W}"><w:body><w:p><w:r><w:t>&b;</w:t></w:r></w:p></w:body>'
        "</w:document>"
    ).encode(encoding)
    data = _archive(("word/document.xml", xml))
    assert "document type declaration" in _refusal(data, DeclarationRefused)


@pytest.mark.parametrize(
    "xml",
    [b"<w:document><unclosed>", b"\xff\xfe\x00not xml at all", b"", "<w:document>\udc80"
     .encode("utf-8", "surrogatepass")],
    ids=["unclosed", "garbage", "empty", "undecodable"],
)
def test_xml_that_does_not_parse_or_decode_is_refused(xml):
    _refusal(_archive(("word/document.xml", xml)), MalformedDocumentXml)


def test_the_strict_variant_is_refused_naming_how_to_save_again():
    said = _refusal(_docx(_p(_r(_t("x"))), namespace=STRICT), StrictDocument)
    assert "Strict Open XML" in said and "Word Document (.docx)" in said


def test_xml_that_is_not_a_word_document_is_refused():
    _refusal(_archive(("word/document.xml", b"<html><p>hi</p></html>")), NotADocument)


def test_the_reader_is_not_imported_by_the_prompt_or_the_command_line():
    probe = (
        "import sys; import pm_ai.app.entry, pm_ai.surfaces.cli.repl; "
        "assert 'pm_ai.core.docx_text' not in sys.modules, 'loaded at startup'"
    )
    subprocess.run([sys.executable, "-c", probe], check=True)


def test_a_non_breaking_hyphen_is_a_hyphen_and_a_soft_hyphen_is_nothing():
    body = _p(_r(_t("Jean") + "<w:noBreakHyphen/>" + _t("Luc in") + "<w:softHyphen/>" + _t("formal")))
    assert docx_text(_docx(body), name="x") == "Jean-Luc informal\n"


@pytest.mark.parametrize(
    "boundary", ["&#13;", "\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x85", " ", " "]
)
def test_a_character_that_would_end_a_line_inside_a_run_is_a_space(boundary):
    # \x0b, \x0c and \x1c-\x1e are not allowed in XML 1.0, so a document holding
    # them does not parse; the others are read as a space.
    data = _docx(_p(_r(_t(f"one{boundary}two"))))
    try:
        text = docx_text(data, name="x")
    except MalformedDocumentXml:
        assert boundary in "\x0b\x0c\x1c\x1d\x1e"
        return
    assert text == "one two\n"
    assert len(text.splitlines()) == 1
