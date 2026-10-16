"""Guards over every recorded Graph fixture (`fixtures/graph/*_recorded.*`).

The repository is public, and these files began as a real tenant's answers.
Each test here checks one property the redaction promises, over every recorded
file, so a re-capture that slips cannot land quietly. The last two compare
pm-ai's reading of Graph's timestamps with an independent one, over every
timestamp the recorded pages carry.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pytest

from pm_ai.connectors.graph.channels import instant
from tests.connectors.recorded_graph import FIXTURES, date_times, fraction_digits, graph_time

JSON_FILES = sorted(FIXTURES.glob("*_recorded*.json"))
VTT_FILES = sorted(FIXTURES.glob("*_recorded*.vtt"))
ALL_FILES = JSON_FILES + VTT_FILES

PERSON = re.compile(r"Person \d+")
SPEAKER = re.compile(r"Speaker \d+")
URL = re.compile(r"(?i)\bhttps?://([^/\s\"'<>\\?#]+)")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
TEAMS_ID = re.compile(r"19:\S+@(?:thread\.tacv2|thread\.v2|unq\.gbl\.spaces)")
ALLOWED_HOSTS = {"graph.microsoft.com", "example.invalid"}
MARKUP_TYPE = re.compile(r'itemtype="http://schema\.skype\.com/[A-Za-z]+"')
"""Teams' own markup vocabulary on a mention or link, kept as recorded: a type
name, not a place anything was fetched from."""
FREE_TEXT_KEYS = {"subject", "summary", "name"}
WORD = re.compile(r"[^\W\d_][^\W_]*")


def _document(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _strings(node: Any, key: str = "") -> Iterator[tuple[str, str]]:
    """Every string in a document with the key it sits under."""
    if isinstance(node, dict):
        for inner, value in node.items():
            yield from _strings(value, inner)
    elif isinstance(node, list):
        for value in node:
            yield from _strings(value, key)
    elif isinstance(node, str):
        yield key, node


def _recorded_strings(path: Path) -> Iterator[tuple[str, str]]:
    """A recorded file's strings, leaving out the note pm-ai wrote about it."""
    if path.suffix == ".vtt":
        yield "vtt", path.read_text(encoding="utf-8")
        return
    document = dict(_document(path))
    document.pop("_provenance", None)
    yield from _strings(document)


class _Data(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _free_text(path: Path) -> Iterator[str]:
    """What people wrote: message markup's character data, subjects, summaries,
    attachment names, and a transcript's spoken words."""
    if path.suffix == ".vtt":
        for line in path.read_text(encoding="utf-8").splitlines():
            if "-->" in line or line.strip() in ("", "WEBVTT"):
                continue
            yield re.sub(r"<v [^>]*>|</v>", "", line)
        return
    for key, value in _recorded_strings(path):
        if key == "content":
            reader = _Data()
            reader.feed(value)
            reader.close()
            yield "".join(reader.parts)
        elif key in FREE_TEXT_KEYS:
            yield value


def test_the_recorded_fixtures_are_the_four_expected():
    assert [path.name for path in ALL_FILES] == [
        "channel_messages_recorded.json",
        "chat_messages_recorded.json",
        "transcripts_recorded.json",
        "transcript_recorded.vtt",
    ]


@pytest.mark.parametrize("path", JSON_FILES, ids=lambda path: path.name)
def test_every_recorded_json_says_where_it_came_from(path):
    provenance = _document(path).get("_provenance")
    assert isinstance(provenance, str) and "2026-10-15" in provenance and "HMAC" in provenance


@pytest.mark.parametrize("path", ALL_FILES, ids=lambda path: path.name)
def test_every_url_points_at_the_redacted_graph_path_or_example_invalid(path):
    for key, value in _recorded_strings(path):
        for host in URL.findall(MARKUP_TYPE.sub("", value)):
            assert host.lower() in ALLOWED_HOSTS, (key, host)


@pytest.mark.parametrize("path", ALL_FILES, ids=lambda path: path.name)
def test_no_email_address_outside_message_text(path):
    """Teams ids are shaped like an address and are not one.

    Inside message markup three address-shaped strings remain, all in one
    message's mail link; every word of them was replaced with random letters
    like the rest of the text (see the capitals test), so no address is there.
    """
    for key, value in _recorded_strings(path):
        found = EMAIL.findall(TEAMS_ID.sub("", value))
        if key == "content":
            assert all(not any(c.isupper() for c in match) for match in found), found
        else:
            assert found == [], (key, found)


@pytest.mark.parametrize("path", JSON_FILES, ids=lambda path: path.name)
def test_every_person_named_is_a_person_label(path):
    for key, value in _recorded_strings(path):
        if key in ("displayName", "mentionText"):
            assert PERSON.fullmatch(value), (key, value)


@pytest.mark.parametrize("path", VTT_FILES, ids=lambda path: path.name)
def test_every_transcript_voice_is_a_speaker_label(path):
    voices = re.findall(r"<v(?:\.[^\s>]*)?\s+([^>]*)>", path.read_text(encoding="utf-8"))
    assert voices
    for voice in voices:
        assert SPEAKER.fullmatch(voice), voice


@pytest.mark.parametrize("path", ALL_FILES, ids=lambda path: path.name)
def test_no_capitalised_word_in_free_text_but_the_labels(path):
    for text in _free_text(path):
        unlabelled = SPEAKER.sub("", PERSON.sub("", text))
        capitalised = [word for word in WORD.findall(unlabelled) if word[0].isupper()]
        assert capitalised == [], capitalised


# ── Timestamps ───────────────────────────────────────────────────────────────


def _stamps() -> list[str]:
    return [stamp for path in JSON_FILES for stamp in date_times(_document(path))]


def test_the_independent_parser_reads_each_fraction_length_by_hand():
    assert graph_time("2026-10-12T08:01:37.83Z").microsecond == 830000
    assert graph_time("2026-10-12T13:35:09.705Z").microsecond == 705000
    assert graph_time("2026-10-15T08:33:21.243328Z").microsecond == 243328
    assert graph_time("2026-10-15T08:46:21.0385744Z").microsecond == 38574


def test_pm_ais_reading_of_every_recorded_timestamp_agrees_with_the_independent_one():
    stamps = _stamps()
    lengths = {fraction_digits(stamp) for stamp in stamps}
    assert {2, 3, 6, 7} <= lengths
    for stamp in stamps:
        assert instant(stamp) == graph_time(stamp), stamp
