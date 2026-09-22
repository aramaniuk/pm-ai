"""What a model call says it is for (AD-15) — the vocabulary, and nothing else.

AD-15's invariant row states that model calls go "always via `ModelPort` with an
explicit `task_class` argument; a call without a declared task class is a
defect", and its Rule routes by "task class declared at the call site". An
argument needs a type, and this is it: `ModelPort.complete` takes a `TaskClass`
and never a `str`, so omitting the declaration — or spelling one — is a
construction error at the same boundary the `Sanitized` guard uses.

**This vocabulary is open, and that is a decision rather than an omission.**
The members below are what AD-15 lists today. They are the vocabulary's current
contents, not its limit: a later story that needs a class AD-15 never named adds
it here, and that is an ordinary act. The architecture's own implementer review
had already found the list short before this module existed — walking FR-06's
summary card, FR-07's fact-check digest and FR-25's drift audit, it reported
that "none of those is any of the ten" (F29), and separately that `extraction`
has no escalation seam against SM-7's precision target (F30). Freezing a set a
prior review had shown to be incomplete would have been the wrong thing to make
unforgeable.

So this deliberately does **not** read like `ObservedEventType` or
`SelfActionType`, the two enumerations AD-27 closes. Those are closed because a
member nobody declared would make a persisted segment unparseable — the cost of
an unknown value lands on a reader of last year's ledger. A task class is
declared at a call site and consumed by a router in the same process; an unknown
one cannot strand a record. Nothing here is registered with the AD-27 guards, no
table must be updated in lockstep with this enum, and that absence is what keeps
adding a member cheap. `tests/architecture/test_sanitize_boundary.py` asserts it
rather than trusting it.

**Names only.** Which classes may leave the machine, which model tier serves
each, and the router that reads both are AD-15's *other* clauses and story 7's
work, wired "behind the model port". None of it is declared here: there is no
grouping attribute, no eligibility flag and no tier map, and the enum carries no
data beyond its members' values.

**Members are in alphabetical order, and that is load-bearing rather than
tidiness.** They first stood in the order AD-15's sentence writes them, which
put its five local-only classes ahead of its five frontier-eligible ones — so
`list(TaskClass)[:5]` returned exactly the local group, and the file encoded the
egress split it claims not to declare. Alphabetical order removes the inference,
and keeps removing it: a class added later sorts wherever its name falls rather
than onto the end of a group, so the eleventh member cannot re-create the
pattern. Correspondence with AD-15 is not lost by reordering, because the test
that checks it compares sets.
"""

from __future__ import annotations

from enum import Enum

__all__ = ["TaskClass"]


class TaskClass(Enum):
    """The task classes AD-15 names today. Open for extension — see the module docstring."""

    BRIEFING_SYNTHESIS = "briefing_synthesis"
    CLASSIFICATION = "classification"
    COACHING = "coaching"
    DRAFT_GENERATION = "draft_generation"
    EMBEDDING = "embedding"
    EXTRACTION = "extraction"
    FUZZY_MATCH = "fuzzy_match"
    INQUIRY_SYNTHESIS = "inquiry_synthesis"
    RESEARCH = "research"
    TRANSCRIPTION = "transcription"
