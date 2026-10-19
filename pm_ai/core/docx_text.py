"""The text of a `.docx`, taken from its bytes with the standard library (story 11d).

A `.docx` is a ZIP archive whose `word/document.xml` holds the text as
paragraphs of runs of text, with tabs and line breaks (the Office Open XML
layout for Word documents). `zipfile` opens the archive from memory and the
XML is read as a stream by the standard library's expat parser, keeping
nothing but the text read so far — so a large document costs the daemon no
whole document tree.

What is taken out, in document order: each paragraph's text as one line,
tables and text boxes included; a tab as a tab; a line break, carriage return
or page break inside a paragraph as a new line; a non-breaking hyphen as `-`
and a soft hyphen as nothing; any other character that would end a line,
inside a run's text, as a space; text inside hyperlinks,
content controls and tracked insertions; of an alternative-content block, the
first choice only, never its fallback copy. Deleted text in tracked changes,
comments, headers, footers, images and document properties are not read. A
paragraph nested in another (a text box anchored in it) comes out as its own
line, before the line of the paragraph holding it.

Every refusal is a `DocxRefused` naming the file, and every library error is
mapped to one: nothing here ends in a traceback. I/O-free: the caller reads the
bytes; this module only opens them in memory. Imported only when a `.docx` is
dropped, so the prompt's startup never loads it.
"""

from __future__ import annotations

import io
import zipfile
from xml.parsers import expat

__all__ = [
    "COMPOUND_SIGNATURE",
    "DOCUMENT_PART",
    "MAX_DOCUMENT_XML_BYTES",
    "MAX_ENTRIES",
    "MAX_TEXT_BYTES",
    "DamagedEntry",
    "DeclarationRefused",
    "DocumentTooLarge",
    "DocxRefused",
    "DuplicateDocumentPart",
    "EncryptedEntry",
    "MalformedDocumentXml",
    "NoDocumentPart",
    "NotADocument",
    "NotAZip",
    "OldWordOrProtected",
    "StrictDocument",
    "TextTooLarge",
    "TooManyEntries",
    "UnsupportedCompression",
    "docx_text",
]

COMPOUND_SIGNATURE = b"\xd0\xcf\x11\xe0"
"""The first bytes of a compound document: an old `.doc`, or any Word file
saved with a password (Word encrypts it into one)."""

DOCUMENT_PART = "word/document.xml"
MAX_ENTRIES = 10_000
MAX_DOCUMENT_XML_BYTES = 64 * 1024 * 1024
"""The most `word/document.xml` may hold uncompressed: 64 MiB, checked from the
archive's directory and again while it is read."""

MAX_TEXT_BYTES = 16 * 1024 * 1024
"""The most text taken out, as UTF-8: 16 MiB, `transcript drop`'s file bound."""

_CHUNK = 1024 * 1024

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_W_STRICT = "http://purl.oclc.org/ooxml/wordprocessingml/main"
_MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
_SEPARATOR = " "


def _w(local: str) -> str:
    return f"{_W}{_SEPARATOR}{local}"


def _mc(local: str) -> str:
    return f"{_MC}{_SEPARATOR}{local}"


_DOCUMENT = _w("document")
_PARAGRAPH = _w("p")
_RUN = _w("r")
_TEXT = _w("t")
_TABS = frozenset({_w("tab"), _w("ptab")})
_BREAKS = frozenset({_w("br"), _w("cr")})
_NO_BREAK_HYPHEN = _w("noBreakHyphen")
"""A hyphen Word keeps on one line (`Jean-Luc`): read as `-`. Its sibling
`w:softHyphen` marks only where a word may be broken and is read as nothing."""

_LINE_BOUNDARIES = str.maketrans(
    {boundary: " " for boundary in "\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029"}
)
"""Every character `str.splitlines` ends a line at, other than `\\n`, read as a
space inside a run's text: one paragraph is one line however its text was
typed (`\\n` is kept as written)."""

_DELETED = frozenset({_w("del"), _w("moveFrom")})
_ALTERNATE = _mc("AlternateContent")
_CHOICE = _mc("Choice")
_FALLBACK = _mc("Fallback")


class DocxRefused(ValueError):
    """A `.docx` pm-ai will not take text from. The message names the file and why."""


class OldWordOrProtected(DocxRefused):
    """The file starts with the compound-document signature: password-protected, or an old `.doc`."""


class NotAZip(DocxRefused):
    """The file is not a ZIP archive, so it is no `.docx`."""


class TooManyEntries(DocxRefused):
    """The archive holds more than `MAX_ENTRIES` entries."""


class NoDocumentPart(DocxRefused):
    """The archive holds no `word/document.xml`."""


class DuplicateDocumentPart(DocxRefused):
    """The archive holds `word/document.xml` more than once."""


class EncryptedEntry(DocxRefused):
    """An entry in the archive is encrypted."""


class UnsupportedCompression(DocxRefused):
    """`word/document.xml` is compressed by a method `zipfile` cannot read."""


class DamagedEntry(DocxRefused):
    """`word/document.xml` is damaged: a bad checksum, truncated data, bad compression."""


class DocumentTooLarge(DocxRefused):
    """`word/document.xml` is over `MAX_DOCUMENT_XML_BYTES` uncompressed."""


class DeclarationRefused(DocxRefused):
    """The XML carries a document type declaration, refused whatever its encoding."""


class MalformedDocumentXml(DocxRefused):
    """`word/document.xml` does not parse, or does not decode."""


class StrictDocument(DocxRefused):
    """The document is the Strict variant of the standard, in its other namespace."""


class NotADocument(DocxRefused):
    """`word/document.xml` parses, and its root is not a Word document's."""


class TextTooLarge(DocxRefused):
    """More than `MAX_TEXT_BYTES` of text was taken out."""


def docx_text(data: bytes, *, name: str) -> str:
    """The text of the `.docx` in `data`, a line per paragraph; `name` is what refusals call it.

    Checked in order: the compound-document signature, a ZIP archive, at most
    `MAX_ENTRIES` entries, no encrypted entry, exactly one `word/document.xml`,
    within `MAX_DOCUMENT_XML_BYTES` by the directory; then the part is read
    and parsed as a stream, every refusal raised the moment it is met.
    """
    if data.startswith(COMPOUND_SIGNATURE):
        raise OldWordOrProtected(
            f"{name} is password-protected, or an old .doc renamed .docx: it is a "
            f"compound document, not the ZIP archive a .docx is. Remove the password, "
            f"or save it again in Word as a Word Document (.docx), and drop that."
        )
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise NotAZip(
            f"{name} is not a .docx pm-ai can open: a .docx is a ZIP archive, and "
            f"this file is not one."
        ) from None
    except Exception as broken:  # noqa: BLE001 — every library error is a refusal
        raise NotAZip(
            f"{name} is not a .docx pm-ai can open: its ZIP directory could not be "
            f"read ({type(broken).__name__})."
        ) from None
    with archive:
        entries = archive.infolist()
        if len(entries) > MAX_ENTRIES:
            raise TooManyEntries(
                f"{name} holds {len(entries)} entries, more than the {MAX_ENTRIES} a "
                f".docx pm-ai opens may hold, so it was not opened."
            )
        encrypted = next((entry for entry in entries if entry.flag_bits & 0x1), None)
        if encrypted is not None:
            raise EncryptedEntry(
                f"{name} has an encrypted entry ({encrypted.filename}); pm-ai does not "
                f"read encrypted archives. Save the document again without a password."
            )
        parts = [entry for entry in entries if entry.filename.casefold() == DOCUMENT_PART]
        if not parts:
            raise NoDocumentPart(
                f"{name} is a ZIP archive with no {DOCUMENT_PART}, so it is not a Word "
                f"document pm-ai can read."
            )
        if len(parts) > 1:
            raise DuplicateDocumentPart(
                f"{name} holds {DOCUMENT_PART} {len(parts)} times, so which one is the "
                f"document cannot be told."
            )
        (part,) = parts
        if part.file_size > MAX_DOCUMENT_XML_BYTES:
            raise DocumentTooLarge(_too_large(name, f"{part.file_size} bytes"))
        return _read_part(archive, part, name=name)


def _too_large(name: str, size: str) -> str:
    return (
        f"{name}'s {DOCUMENT_PART} is {size} uncompressed, over the "
        f"{MAX_DOCUMENT_XML_BYTES // (1024 * 1024)} MiB pm-ai reads."
    )


def _read_part(archive: zipfile.ZipFile, part: zipfile.ZipInfo, *, name: str) -> str:
    try:
        stream = archive.open(part)
    except NotImplementedError:
        raise UnsupportedCompression(
            f"{name}'s {DOCUMENT_PART} is compressed by a method pm-ai's ZIP reader "
            f"cannot read (method {part.compress_type}). Save the document again in "
            f"Word and drop that."
        ) from None
    except Exception as broken:  # noqa: BLE001 — every library error is a refusal
        raise DamagedEntry(_damaged(name, broken)) from None
    extractor = _Extractor(name)
    parser = extractor.parser()
    read = 0
    try:
        with stream:
            while True:
                try:
                    chunk = stream.read(_CHUNK)
                except Exception as broken:  # noqa: BLE001 — every library error is a refusal
                    raise DamagedEntry(_damaged(name, broken)) from None
                if not chunk:
                    break
                read += len(chunk)
                if read > MAX_DOCUMENT_XML_BYTES:
                    raise DocumentTooLarge(_too_large(name, "more than that"))
                parser.Parse(chunk, False)
            parser.Parse(b"", True)
    except expat.ExpatError as unparsed:
        raise MalformedDocumentXml(
            f"{name}'s {DOCUMENT_PART} is not XML pm-ai can read "
            f"({expat.ErrorString(unparsed.code)}, line {unparsed.lineno})."
        ) from None
    except DocxRefused:
        raise
    except Exception as broken:  # noqa: BLE001 — every library error is a refusal
        raise MalformedDocumentXml(
            f"{name}'s {DOCUMENT_PART} is not XML pm-ai can read "
            f"({type(broken).__name__})."
        ) from None
    if not extractor.rooted:
        raise MalformedDocumentXml(f"{name}'s {DOCUMENT_PART} holds no XML element.")
    return extractor.text()


def _damaged(name: str, broken: BaseException) -> str:
    reason = " ".join(str(broken).split())[:120] or type(broken).__name__
    return (
        f"{name}'s {DOCUMENT_PART} is damaged ({type(broken).__name__}: {reason}), so "
        f"its text cannot be read."
    )


class _Extractor:
    """expat's handlers: the text of `word/document.xml`, as it streams past."""

    def __init__(self, name: str) -> None:
        self._name = name
        self._stack: list[str] = []
        self._skipping_at: int | None = None
        self._alternates: list[bool] = []
        self._paragraphs: list[list[str]] = []
        self._out: list[str] = []
        self._in_text = False
        self._size = 0
        self.rooted = False

    def parser(self) -> expat.XMLParserType:
        parser = expat.ParserCreate(namespace_separator=_SEPARATOR)
        parser.buffer_text = True
        parser.StartDoctypeDeclHandler = self._declaration
        parser.EntityDeclHandler = self._declaration
        parser.StartElementHandler = self._start
        parser.EndElementHandler = self._end
        parser.CharacterDataHandler = self._characters
        return parser

    def text(self) -> str:
        return "".join(self._out)

    def _declaration(self, *_: object) -> None:
        raise DeclarationRefused(
            f"{self._name}'s {DOCUMENT_PART} carries a document type declaration, "
            f"which a Word document never needs; pm-ai does not read one."
        )

    def _start(self, element: str, _attributes: object) -> None:
        if not self.rooted:
            self.rooted = True
            if element.startswith(f"{_W_STRICT}{_SEPARATOR}"):
                raise StrictDocument(
                    f"{self._name} is saved as Strict Open XML, which pm-ai does not "
                    f"read. Open it in Word, save it again as a Word Document (.docx) "
                    f"rather than a Strict Open XML Document, and drop that."
                )
            if element != _DOCUMENT:
                raise NotADocument(
                    f"{self._name}'s {DOCUMENT_PART} is XML, and not a Word document's "
                    f"body, so there is no text in it pm-ai can read."
                )
        parent = self._stack[-1] if self._stack else None
        self._stack.append(element)
        if self._skipping_at is not None:
            return
        if element in _DELETED or element == _FALLBACK:
            self._skip()
            return
        if element == _ALTERNATE:
            self._alternates.append(False)
        elif element == _CHOICE and parent == _ALTERNATE:
            if self._alternates[-1]:
                self._skip()
                return
            self._alternates[-1] = True
        elif element == _PARAGRAPH:
            self._paragraphs.append([])
        elif parent == _RUN:
            if element == _TEXT:
                self._in_text = True
            elif element in _TABS:
                self._emit("\t")
            elif element in _BREAKS:
                self._emit("\n")
            elif element == _NO_BREAK_HYPHEN:
                self._emit("-")

    def _skip(self) -> None:
        self._skipping_at = len(self._stack) - 1

    def _end(self, element: str) -> None:
        self._stack.pop()
        if self._skipping_at is not None:
            if len(self._stack) == self._skipping_at:
                self._skipping_at = None
            return
        if element == _TEXT:
            self._in_text = False
        elif element == _PARAGRAPH and self._paragraphs:
            line = "".join(self._paragraphs.pop())
            self._count("\n")
            self._out.append(line + "\n")
        elif element == _ALTERNATE and self._alternates:
            self._alternates.pop()

    def _characters(self, data: str) -> None:
        if self._in_text and self._skipping_at is None:
            self._emit(data.translate(_LINE_BOUNDARIES))

    def _count(self, piece: str) -> None:
        self._size += len(piece.encode("utf-8", "surrogatepass"))
        if self._size > MAX_TEXT_BYTES:
            raise TextTooLarge(
                f"more than {MAX_TEXT_BYTES // (1024 * 1024)} MiB of text was taken out "
                f"of {self._name}, over what a dropped transcript may hold."
            )

    def _emit(self, piece: str) -> None:
        self._count(piece)
        if self._paragraphs:
            self._paragraphs[-1].append(piece)
        else:
            self._out.append(piece)
