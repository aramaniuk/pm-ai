"""An in-memory `ClaimPort`, for every writer a test builds that is not testing the lock.

The real adapter is `pm_ai.platform.claims.FlockClaims`, and only the tests about
the lock itself use it — so a filesystem without `flock` fails those tests and no
others. This fake keeps the property callers rely on: a second claim on one
file is refused at once with `ClaimHeld`, whichever writer asks, keyed on the
resolved path. Held process-wide, as a kernel lock taken on two descriptors in
one process would be — except that the thread already holding it gets
`ClaimReentered`, as from the real adapter (AD-5, story 8q).
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path

from pm_ai.domain.claims import ClaimHeld, ClaimReentered

_HELD: dict[Path, int] = {}
_GUARD = threading.Lock()


class InMemoryClaims:
    """`ClaimPort` with no file and no lock: a set of the paths now held."""

    def exclusive(self, guarded: Path) -> AbstractContextManager[None]:
        return _claim(guarded.resolve())


@contextmanager
def _claim(key: Path) -> Iterator[None]:
    with _GUARD:
        if _HELD.get(key) == threading.get_ident():
            raise ClaimReentered(f"{key} is already held by this thread")
        if key in _HELD:
            raise ClaimHeld(f"{key.name} is held")
        _HELD[key] = threading.get_ident()
    try:
        yield
    finally:
        with _GUARD:
            _HELD.pop(key, None)


@contextmanager
def held_by_another_thread(
    claim: Callable[[], AbstractContextManager[object]],
) -> Iterator[None]:
    """Hold `claim()` from a second thread for the body: contention, as a test can stage it.

    A claim taken again by the thread already holding it is a programming error
    (AD-5, story 8q — `ClaimReentered`), so a test that wants the *busy* answer
    has the claim held by someone else: here, a thread that takes it, waits for
    the body to finish, and releases it. Whatever taking it raised is raised here.
    """
    taken = threading.Event()
    release = threading.Event()
    failure: list[BaseException] = []
    released: list[BaseException] = []

    def hold() -> None:
        try:
            entered = claim()
            entered.__enter__()
        except BaseException as refused:  # noqa: BLE001 — handed to the test
            failure.append(refused)
            taken.set()
            return
        taken.set()
        release.wait(30)
        try:
            entered.__exit__(None, None, None)
        except BaseException as unreleased:  # noqa: BLE001 — handed to the test
            released.append(unreleased)

    holder = threading.Thread(target=hold, daemon=True)
    holder.start()
    if not taken.wait(30):
        release.set()
        raise AssertionError("the other thread never took the claim within 30s")
    if failure:
        holder.join(30)
        raise failure[0]
    try:
        yield
    finally:
        release.set()
        holder.join(30)
        if holder.is_alive():
            raise AssertionError("the other thread did not release the claim within 30s")
        if released:
            raise released[0]
