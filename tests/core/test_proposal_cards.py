"""Story 13d — the card's compact form, for the interactive review walk.

One `Card` rendered both ways: the compact form is the full form's first
lines, the heading, the field lines and the origin, and nothing else.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pm_ai.core.proposal_cards import Card, CardAction, card_text

STAGED = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)
ID = "prp_example_0001"


def _card(**changes) -> Card:
    values = dict(
        proposal_id=ID,
        type="action_item",
        label="Action item",
        state="staged",
        heading="Action item, waiting for you until Wed 4 Nov",
        version=1,
        scope="personal",
        fields={"what": "book the review room", "owner": "Alex Morgan", "due": None},
        field_lines=(
            "Alex Morgan: book the review room",
            "may repeat an item you edited: prp_example_0000_e1",
        ),
        origin="from Example Weekly Sync-up, Wed 21 Oct 10:00, personal",
        meeting=None,
        created_at=STAGED,
        staged_at=STAGED,
        expires_at=None,
        changed_at=None,
        changed_via=None,
        replaces="prp_example_0000",
        replaced_by="prp_example_0001_e1",
        may_repeat="prp_example_0000_e1",
        actions=(
            CardAction("approve", True, None, ("proposal", "approve", ID, "--scope", "personal", "--if-version", "1")),
            CardAction("edit", False, "already staged again as prp_example_0001_e1"),
            CardAction("reject", True, None, ("proposal", "reject", ID, "--scope", "personal", "--if-version", "1")),
            CardAction("show", True, None, ("proposal", "show", ID, "--scope", "personal")),
        ),
    )
    values.update(changes)
    return Card(**values)  # type: ignore[arg-type]


def test_the_compact_form_is_the_full_forms_first_lines():
    card = _card()
    full = card_text(card).splitlines()
    compact = card_text(card, compact=True).splitlines()
    assert compact == [
        "Action item, waiting for you until Wed 4 Nov",
        "  Alex Morgan: book the review room",
        "  may repeat an item you edited: prp_example_0000_e1",
        "  from Example Weekly Sync-up, Wed 21 Oct 10:00, personal",
    ]
    assert full[: len(compact)] == compact
    left_out = full[len(compact):]
    assert left_out == [
        "  replaces prp_example_0000",
        "  staged again as prp_example_0001_e1",
        "  approve: pm-ai proposal approve prp_example_0001 --scope personal --if-version 1",
        "  edit:    unavailable: already staged again as prp_example_0001_e1",
        "  reject:  pm-ai proposal reject prp_example_0001 --scope personal --if-version 1",
    ]
    assert card_text(card, compact=True).endswith("\n")


def test_the_full_form_is_the_default():
    card = _card()
    assert card_text(card) == card_text(card, compact=False)
