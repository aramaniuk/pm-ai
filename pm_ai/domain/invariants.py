"""When the declared model contradicts itself, refuse to load (AD-3, AD-14, AD-44).

The domain declares structures that have to agree with each other: the scope
trees against the tier and exclusion sets derived from them, the artifact keys
spelled as constants against the nodes those constants name, the proposal states
against the commitment states. Each `__post_init__` checks what a node can know
about itself. What is left is the *relationships* between declarations, and those
are checked once, at import, so a model that contradicts itself cannot be loaded
at all.

Those checks were bare `assert` statements until 2026-08-22. `python -O` strips
every one — verified, `__debug__` is `False` and the AD-44 duplicate-tier guard
goes inert — and the daemon runs as a `launchd` agent, where `-O` in a plist is
an ordinary thing to add for startup time. The scope model would then load with
none of its coherence established, and the first symptom would be a rebuild
deleting Tier-2 state or a capture written to a path no rule covers. A guard that
depends on an interpreter flag is not a guard.

Deliberately not moved into the test suite instead. A test proves that *this
repository's* model is coherent. `scope_model.py` is a plain Python file inside a
`uv tool install`ed package, and the failure being prevented is a hand-edited
declaration on someone's machine — so the refusal has to live in the code that
loads, not in a suite that shipped separately.

`RuntimeError` rather than `ValueError`: nothing was passed in wrongly. The
program is in a state it cannot proceed from, which is the same reading
`UnprotectedCaptureDir` takes.
"""

from __future__ import annotations

__all__ = ["InconsistentModel"]


class InconsistentModel(RuntimeError):
    """Two declarations in the domain disagree, so nothing may be built on them.

    Raised only at import, only about declared structure, and never about a
    caller's argument — a wrong argument is a `ValueError` and reaches exactly
    one call site, while this reaches everything.
    """
