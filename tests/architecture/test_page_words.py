"""Story 15d — every state and due word a page shows lives in `core/commitment_cards.py`.

`rendering` and `member_updates` only compose and escape the words
`commitment_cards.page_parts` built, so the two modules' sources hold none of
them. One module holds every such literal, and the pages and the cards can
never word a commitment's state two ways.
"""

from __future__ import annotations

import pytest

from conftest import REPO_ROOT

WORDS = ("set by you", "past due", "meeting day estimated", "could not check", "by telemetry")
"""The state and due words `page_parts` builds (story 15d)."""

COMPOSERS = ("pm_ai/core/member_updates.py", "pm_ai/core/rendering.py")


@pytest.mark.parametrize("path", COMPOSERS)
def test_the_page_composers_hold_no_state_or_due_word(path):
    source = (REPO_ROOT / path).read_text(encoding="utf-8")
    found = [word for word in WORDS if word in source]
    assert not found, f"{path} words a commitment's state or due itself: {found}"


def test_the_words_are_in_commitment_cards():
    source = (REPO_ROOT / "pm_ai/core/commitment_cards.py").read_text(encoding="utf-8")
    assert all(word in source for word in WORDS)
