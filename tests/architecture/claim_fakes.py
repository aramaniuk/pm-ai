"""An in-memory `ClaimPort`, for every writer a test builds that is not testing the lock.

The real adapter is `pm_ai.platform.claims.FlockClaims`, and only the tests about
the lock itself use it — so a filesystem without `flock` fails those tests and no
others. This fake keeps the property callers rely on: a second claim on one
file is refused at once with `ClaimHeld`, whichever writer asks, keyed on the
resolved path. Held process-wide, as a kernel lock taken on two descriptors in
one process would be.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path

from pm_ai.domain.claims import ClaimHeld

_HELD: set[Path] = set()
_GUARD = threading.Lock()


class InMemoryClaims:
    """`ClaimPort` with no file and no lock: a set of the paths now held."""

    def exclusive(self, guarded: Path) -> AbstractContextManager[None]:
        return _claim(guarded.resolve())


@contextmanager
def _claim(key: Path) -> Iterator[None]:
    with _GUARD:
        if key in _HELD:
            raise ClaimHeld(f"{key.name} is held")
        _HELD.add(key)
    try:
        yield
    finally:
        with _GUARD:
            _HELD.discard(key)
