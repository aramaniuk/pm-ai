"""Startup probes for the failures that cannot happen on the developer's machine.

Each of these is near-certain on somebody else's and invisible until the moment
it matters. They share a shape: installation succeeds, the daemon starts, and the
thing that breaks is one specific capability, silently.

- **sqlite extension support.** The vector index loads only into an interpreter
  whose connection exposes `enable_load_extension`. Stock macOS and python.org
  builds do not have it — absent, not disabled. Then the first write touching the
  index fails deep inside the storage layer.
- **Keychain reachability.** Breaks after an OS or interpreter upgrade. Silent,
  unattended, and it presents as the morning briefing simply not arriving.
- **The encryption toggle.** Disabled means credentials are being written in
  plaintext right now.
- **`git`.** A hard runtime dependency of the capture write path: without it every
  capture is refused, and *nothing else changes* — harvests, briefings and the
  CLI all keep working, so the one thing that stops is the one nobody notices
  stopping until a meeting has already happened.

## Two rules shape all of it

**Every probe reports; none raises.** A caller sees the whole picture in one
pass, and one broken thing cannot hide three others. That is why an unreachable
keychain returns a result instead of an exception.

**Every failure names its remediation.** "Missing `enable_load_extension`" is
useless without "install a uv-managed interpreter". A probe that only says what
is wrong makes the operator guess at what to do, and guessing is how a machine
gets a second key written over a store that could still have been opened.

No repair actions, ever: probes are read-only and never create, migrate, or fix
anything. And no `pm-ai doctor` subcommand here: this ships callables and
nothing else. Story 4c surfaced them from `pm_ai.surfaces.cli.dispatch`, which
is also where the exit code a `doctor` run produces is decided, since
`Report.healthy` is a verdict and not a code.

This module had its own `python -m` runner until 4c, returning 0 or 1 by
verdict. It was retired with the console script that superseded it: `1` is
`pm-ai broke, read the traceback` in the one table that decides these, so a
second surface answering `1` for `the machine is unhealthy` made the code an
operator alerts on mean two different things. `platform` may not import
`surfaces`, so this module cannot reuse those constants — which is the reason
it no longer decides a code at all.
"""

from __future__ import annotations

import errno
import http.client
import importlib.metadata
import io
import json
import os
import re
import socket
import stat
import time
import shutil
import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

# Re-exported, not redefined. `Health`, `Probe` and `Report` moved to
# `pm_ai.domain.health` when `ConnectorPort` gained a health method: a port may
# import only `pm_ai.domain`, so the types a port names cannot live here. Every
# existing `from pm_ai.platform.doctor import Health, Probe, Report` still
# resolves, and there is one `Health` in the process rather than two that agree
# by convention.
from pm_ai.domain.health import (
    CONNECTOR_SYSTEMS,
    ArtifactState,
    EnrolmentReading,
    Health,
    Presence,
    Probe,
    Report,
)
from pm_ai.domain.api_token import (
    MAX_TOKEN_FILE_BYTES,
    ApiTokenRefused,
    check_api_token,
    symlinked,
)
from pm_ai.core.config import ConfigRefused, load_config
from pm_ai.core.project_registry import RegistryRefused, parse_registry
from pm_ai.domain.vcs import VcsUnavailable
from pm_ai.platform.environment import DISABLE_ENCRYPTION_VAR, TRUTHY, raw_toggle
from pm_ai.platform.vcs import GitVcs
from pm_ai.ports import (
    MASTER_KEY_NAME,
    KeychainBackendMissing,
    KeychainPort,
    KeychainUnavailable,
    KeyNotFound,
)

__all__ = [
    "ArtifactState",
    "CONNECTORS_PROBE",
    "Health",
    "Presence",
    "Probe",
    "Report",
    "config_readable",
    "connectors_enrolled",
    "daemon_reachable",
    "packages_installed",
    "registry_readable",
    "run_all",
]


# ── Dependencies ─────────────────────────────────────────────────────────────

DISTRIBUTION = "pm-ai"


def _normalise(name: str) -> str:
    """PEP 503 name comparison: case-insensitive, with `-_.` runs equivalent."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _requirement_name(requirement: str) -> str:
    """The bare distribution name from a requirement string.

    `anthropic[mcp]; extra == "runtime"` and `keyring==25.7.0` both reduce to a
    name. Parsed rather than pattern-matched against a hardcoded list, so the
    answer comes from the same `pyproject.toml` that decides what to install and
    cannot drift from it.
    """
    return _normalise(re.split(r"[\[<>=!~;\s]", requirement.strip(), maxsplit=1)[0])


def required_distributions(extra: str, *, distribution: str = DISTRIBUTION) -> tuple[str, ...]:
    """What `distribution`'s `extra` declares, from installed metadata.

    Derived, never listed here. A hardcoded set would be a second place to edit
    when a dependency is added and would silently stop covering the new one.
    """
    requirements = importlib.metadata.requires(distribution) or []
    marker = f'extra == "{extra}"'
    alternate = f"extra == '{extra}'"
    return tuple(
        _requirement_name(r)
        for r in requirements
        if marker in r or alternate in r
    )


def missing_distributions(names: Iterable[str]) -> tuple[str, ...]:
    """Which of `names` are not installed, without importing any of them.

    Importing to find out would be the obvious implementation and the wrong one:
    `fastapi`, `uvicorn` and `ollama` all cost real time to import and some have
    side effects, and a diagnostic must not pay a startup cost to report that a
    startup cost exists. Distribution metadata answers without loading code.
    """
    installed = {
        _normalise(dist)
        for dists in importlib.metadata.packages_distributions().values()
        for dist in dists
    }
    return tuple(sorted(n for n in {_normalise(x) for x in names} if n not in installed))


# ── The probes ───────────────────────────────────────────────────────────────


def packages_installed(
    names: Iterable[str] | None = None, *, extra: str = "runtime"
) -> Probe:
    """Whether the packages pm-ai depends on are actually installed.

    First among the probes, because when the answer is no, three of the four
    after it are answering questions that do not matter yet — and one of them,
    the keychain, was the only thing reporting this at all until 2026-08-26. An
    operator learned that their whole runtime stack was absent through a message
    about a keychain, which is an oblique way to say that nothing works.

    Generic by design: `names` checks any set of distributions, so this is not a
    keyring probe wearing a different hat. The default derives the `runtime`
    extra from installed metadata, so adding a dependency extends the check with
    no edit here.
    """
    name = f"{extra} packages" if names is None else "packages"
    wanted = tuple(names) if names is not None else required_distributions(extra)
    if not wanted:
        return Probe(
            name, Health.ABSENT,
            f"no packages to check — {DISTRIBUTION!r} metadata declares no {extra!r} "
            f"extra, or the distribution is not installed",
            f"Install pm-ai itself before diagnosing it: an editable install is "
            f"what makes its own metadata readable.",
        )
    missing = missing_distributions(wanted)
    if not missing:
        return Probe(name, Health.OK, f"all {len(wanted)} present")
    return Probe(
        name,
        Health.FAILING,
        f"{len(missing)} of {len(wanted)} not installed: {', '.join(missing)}",
        f"Install them with `uv sync --extra {extra}`. Until then the features "
        f"they back are unavailable, and the probes below will report the "
        f"consequences rather than this cause.",
    )


def sqlite_extension_support() -> Probe:
    """Whether this interpreter can load the vector-search extension at all.

    Tests the attribute on a real connection object rather than the version
    string. The failure is a property of how the interpreter was *built*, not of
    which version it is — a correct version compiled without the feature passes
    every version check and fails on first use, which is exactly the sequence
    this probe exists to break.
    """
    name = "sqlite extension support"
    try:
        with sqlite3.connect(":memory:") as connection:
            supported = hasattr(connection, "enable_load_extension")
    except sqlite3.Error as broken:
        return Probe(name, Health.FAILING, f"sqlite3 is unusable: {broken}",
                     "Reinstall the interpreter; nothing here can proceed without sqlite.")
    if supported:
        return Probe(name, Health.OK, "the interpreter exposes enable_load_extension")
    return Probe(
        name,
        Health.FAILING,
        "this interpreter's sqlite3 has no enable_load_extension, so the vector "
        "index cannot be loaded",
        "Install a uv-managed interpreter (`[tool.uv] python-preference = "
        "\"only-managed\"` is set for this reason). Stock macOS and python.org "
        "builds omit the feature — installation will succeed and the first write "
        "touching the index will fail inside the storage layer.",
    )


def keychain_reachable(keychain: KeychainPort, key_name: str = MASTER_KEY_NAME) -> Probe:
    """Whether the master key can be reached, and whether one is stored.

    `keychain` is injected rather than constructed so the probe can be exercised
    against every failure a real keychain has, none of which a test may provoke
    for real. Typed as the port rather than left implicit: story 1k found exactly
    this shape in `SkillRegistry` — an unannotated dependency is `Any`, so every
    attribute read on it is unverified — and this module was written after that
    fix and repeated it.

    The key's value is never placed in the result. A diagnostic that prints a
    secret turns a support request into a disclosure.
    """
    name = "keychain"
    try:
        keychain.fetch(key_name)
    except KeyNotFound:
        return Probe(
            name,
            Health.ABSENT,
            f"the keychain is reachable and holds no key named {key_name!r}",
            "Enrol the master key by running `pm-ai setup`. The key is "
            "configured as a setup step, never minted by the daemon: a new key "
            "makes every previously sealed artifact unreadable.",
        )
    except KeychainBackendMissing as absent:
        # Split from the refusal below on 2026-08-26, to the same bar the git
        # probe already met: an incomplete install and a keychain that is present
        # and refusing take different repairs, and making the reader parse a
        # message to tell them apart is what telling someone to install git they
        # already have looks like.
        return Probe(
            name, Health.FAILING, f"the keychain backend is not installed: {absent}",
            "An incomplete installation rather than a broken keychain — see the "
            "packages probe above. Nothing about the OS keychain needs attention.",
        )
    except KeychainUnavailable as unreachable:
        return Probe(
            name, Health.FAILING, f"the keychain is present and refused: {unreachable}",
            "The backend is installed, so this is the keychain itself: unlock it, "
            "or check that this process may reach it. Encrypted artifacts cannot "
            "be read or written until it answers.",
        )
    return Probe(name, Health.OK, f"a key named {key_name!r} is present and readable")


def encryption_toggle() -> Probe:
    """Whether this process is writing the encrypted set in plaintext.

    Three outcomes rather than two. Unset is healthy; a recognised value is a
    warning, because the daemon is doing what it was told and that is not the same
    as healthy; and an **unrecognised** value is its own report, because whoever
    exported `PM_AI_DISABLE_ENCRYPTION=please` believes they disabled encryption
    and did not. That confusion has nowhere else to surface.
    """
    name = "encryption"
    value = raw_toggle()
    if value is None:
        return Probe(name, Health.OK, "enabled")
    if value.strip().lower() in TRUTHY:
        return Probe(
            name,
            Health.WARNING,
            f"DISABLED by {DISABLE_ENCRYPTION_VAR}={value!r} — credentials and "
            f"voice notes are being written in plaintext",
            "Short-term debugging only. Unset the variable and restart; there is "
            "no persistent way to disable encryption and restarting restores it.",
        )
    return Probe(
        name,
        Health.WARNING,
        f"enabled, but {DISABLE_ENCRYPTION_VAR}={value!r} is not a value this "
        f"recognises, so it is having no effect",
        f"Use one of {sorted(TRUTHY)} if disabling was intended, or unset the "
        f"variable. Encryption is on either way — this reports the mismatch "
        f"because silently ignoring it looks identical to honouring it.",
    )


def git_available() -> Probe:
    """Whether `git` is present *and* able to answer, reported separately.

    `shutil.which` proves a file exists. The capture guard needs git to *answer a
    question*, and the ways that fails — a build without `check-ignore`, a wrapper
    that shells elsewhere, a `PATH` entry pointing at a stub — all pass a `which`
    check. So one real query is run, and absent is reported differently from
    present-but-unanswering: the first is an install, the second an investigation.
    """
    name = "git"
    binary = shutil.which("git")
    if binary is None:
        return Probe(
            name, Health.FAILING, "no `git` on this process's PATH",
            "Every capture write will be refused and nothing else will change — "
            "harvests, briefings and the CLI keep working, so this is silent. "
            "Install git, or add it to the daemon's PATH (`launchd` supplies a "
            "minimal one).",
        )
    # Through the adapter, never `subprocess` beside it. This probe used to
    # spawn git directly, which put two invocations outside `GIT_SUBCOMMANDS`
    # and made that set's "complete" claim false the day it shipped (review
    # 2026-08-28) — the closed set is only closed if the doctor is inside it.
    # `check-ignore` is asked from the home directory: exit 128 ("not a
    # repository") is an expected answer there, and the probe is about whether
    # git answers, not about any particular repository.
    vcs = GitVcs()
    try:
        version = vcs.probe("--version", repository=Path.home())
        answering = vcs.probe("check-ignore", "--quiet", "--", "probe/", repository=Path.home())
    except VcsUnavailable as unusable:
        return Probe(
            name, Health.FAILING, f"`git` is present at {binary} but unusable: {unusable}",
            "Distinct from git being absent: something is there and cannot answer. "
            "Check that the binary on PATH is really git.",
        )
    # `check-ignore` exits 0 when a path is ignored, 1 when it is not, and 128
    # outside a repository — all three mean git answered. Anything else is a git
    # this codebase does not understand.
    if answering.returncode not in (0, 1, 128):
        return Probe(
            name, Health.FAILING,
            f"`git check-ignore` exited {answering.returncode}: "
            f"{answering.stderr.strip() or 'no output'}",
            "git is installed but cannot answer the exclusion question the capture "
            "guard asks, so captures will be refused. Investigate this binary "
            "rather than installing another.",
        )
    return Probe(name, Health.OK, f"{version.stdout.strip() or binary} answers exclusion queries")



# ── Artifacts pm-ai reads about itself (stories 4i, 4d) ──────────────────────
#
# The two probes below differ from the five above in kind: those ask the
# machine, these interpret a value somebody handed them. That is deliberate and
# it is what `ArtifactState` is for — `read_artifact` is the single reader
# (`pm_ai/storage/service.py:1093`), so a probe that opened `config.toml` itself
# would be a second reader with its own idea of what absence means.
#
# `ArtifactState` and `Presence` live in `pm_ai.domain.health`, beside `Probe`,
# and are re-exported here. Same reason those three moved: `pm_ai.app.wiring`
# produces the states and may not import this module — `.importlinter`'s AD-1
# contract reaches `subprocess` through `pm_ai.platform.vcs` — so leaving them
# here meant either a third ignored import in that contract or a second, quietly
# divergent `ArtifactState`. Every `from pm_ai.platform.doctor import
# ArtifactState` still resolves to the one in the process.


def _bytes_or_probe(name: str, state: ArtifactState, *, absent: Probe) -> Probe | bytes:
    """The three non-`READ` answers, which both artifact probes share.

    Returns the bytes when there are bytes to interpret, which is where the two
    probes stop being the same. Shared rather than repeated because the
    distinction this draws is the whole point of `ArtifactState`, and a second
    copy is where one of them would quietly lose a state.

    Returning `Probe | bytes` rather than `Probe | None` beside a separate
    `state.raw` read is what lets the caller narrow by `isinstance`. The
    obvious spelling — return `None`, then `assert state.raw is not None` — is
    the one story `1l` forbids: `python -O` deletes an `assert`, and an
    invariant that holds only when the interpreter is not optimizing is not one.
    """
    if state.presence is Presence.ABSENT:
        return absent
    if state.presence is Presence.UNREADABLE:
        return Probe(
            name, Health.FAILING,
            f"the file exists and could not be read: {state.detail}",
            f"Check the ownership and permissions of {name}. It is present, so "
            f"do not create a new one — a second file written over an "
            f"unreadable one is how the first one's contents are lost.",
        )
    if state.presence is Presence.UNOBTAINABLE:
        return Probe(
            name, Health.FAILING,
            f"pm-ai could not reach the file to find out: {state.detail}",
            "Fix whatever the probes above report first. This is not a claim "
            "that the file is missing — nothing got far enough to look.",
        )
    # `Presence.READ` carries bytes by construction: the classmethod is the only
    # way to build one and it requires them.
    return state.raw if state.raw is not None else b""


def config_readable(state: ArtifactState) -> Probe:
    """What state `config.toml` is actually in (story 4i).

    Five healthy probes could be reported on a machine whose configuration is
    unparseable, because none of them asked. `ConfigRefused` is caught and
    carried as this probe's own detail: an operator needs the loader's message,
    which names the offending key, and not a traceback.
    """
    name = "config.toml"
    answer = _bytes_or_probe(
        name,
        state,
        absent=Probe(
            name, Health.ABSENT,
            "no config.toml, so every setting is at its default",
            "Run `pm-ai setup`. A first run has no config and that is not a "
            "fault.",
        ),
    )
    if isinstance(answer, Probe):
        return answer
    try:
        load_config(answer)
    except ConfigRefused as refused:
        return Probe(
            name, Health.FAILING,
            f"config.toml says something pm-ai will not act on: {refused}",
            "Fix or remove the offending key. An unreadable config is refused "
            "rather than ignored, because a setting that reads as configured "
            "while having no effect stays wrong forever.",
        )
    return Probe(name, Health.OK, "config.toml parses")


def registry_readable(state: ArtifactState) -> Probe:
    """Which projects are enrolled, and whether their repositories are still there.

    `ABSENT` covers both no file and a file with no entries. They are one answer
    to an operator — nothing is enrolled, run `pm-ai project add` — and the
    distinction `keychain_reachable` draws between "unreachable" and "reachable,
    nothing stored" is the one that matters here too.

    A registry naming a directory that has since moved away is `FAILING` and
    names the project. Nothing refuses it: `ScopePaths.repository()` performs no
    existence check, and adding one was declined on 2026-09-15 rather than put
    filesystem access into a resolver that has none.
    """
    name = "project registry"
    absent = Probe(
        name, Health.ABSENT,
        "no project is enrolled, so pm-ai has nothing to act on",
        "Run `pm-ai project add <path>`. Projects enter the system through the "
        "registry and never by being found on disk (AD-11), so an empty "
        "registry means no work has been offered yet rather than something "
        "broken.",
    )
    answer = _bytes_or_probe(name, state, absent=absent)
    if isinstance(answer, Probe):
        return answer
    try:
        projects = parse_registry(answer)
    except RegistryRefused as refused:
        return Probe(
            name, Health.FAILING,
            f"projects.toml cannot be read as a registry: {refused}",
            "Repair the file by hand. It is never reset automatically: "
            "projects.toml is rebuildable from nothing, so a registry that "
            "parsed to empty would be one the next `project add` writes over, "
            "forgetting every project it named.",
        )
    if not projects:
        return absent
    missing = sorted(
        project_id for project_id, entry in projects.items() if not entry.path.is_dir()
    )
    if missing:
        return Probe(
            name, Health.FAILING,
            f"{len(projects)} project(s) enrolled; the repository is gone for "
            f"{', '.join(missing)}",
            "Restore the directory, or enrol the project afresh at its new path. "
            "Every artifact already written for it resolves under the old one, "
            "so moving a repository is not something pm-ai can follow on its "
            "own.",
        )
    return Probe(name, Health.OK, f"{len(projects)} project(s) enrolled: {', '.join(sorted(projects))}")


# ── The enrolled connectors (story 4n) ──────────────────────────────────────

CONNECTORS_PROBE = "connectors"

_ADD_COMMANDS = (
    "`pm-ai connector add graph <name>` (Microsoft 365) or "
    "`pm-ai connector add gitlab <name>`"
)
_SUPPORTED = " and ".join(CONNECTOR_SYSTEMS)


def _named(what: str, names: Sequence[str]) -> str:
    return f"{len(names)} {what}: {', '.join(names)}"


def connectors_enrolled(reading: EnrolmentReading) -> Probe:
    """Which connectors are enrolled on this machine, contacting none of them.

    Takes the rows already read, as `config_readable` takes bytes: `platform`
    may import neither `pm_ai.connectors` nor `pm_ai.storage`, and a probe that
    opened `connectors/` itself would be a second reader. Names only — the
    reading carries no setting and no credential, so nothing secret can reach
    this line, and no provider's client is imported to produce it. Whether a
    listed connector answers is `pm-ai connector check`'s question (AD-39), and
    whether a listed row builds is the start's, which names its reason.

    - **Nothing enrolled is `OK`**, said with both commands that add one: a
      machine with nothing to harvest from is an ordinary state, not a fault,
      and it must not read like a ready one.
    - **Only disabled rows is "none enabled"**, never "none enrolled": the
      operator turned them off, and saying nothing is there would be false.
    - **Rows only for systems pm-ai has no connector for are named**, with the
      systems it does support: they are not enrolled connectors, and they are
      not nothing either.
    - **A row that could not be used is a `WARNING`** naming it, beside every
      row that could: dropping it silently is how a typo reads as no connector.
    - **A directory that cannot be listed is `FAILING`**, never "none
      enrolled": an unlisted directory is not an empty one.
    """
    name = CONNECTORS_PROBE
    if reading.unlisted is not None:
        return Probe(
            name, Health.FAILING,
            f"connectors/ could not be listed, so which connectors are enrolled "
            f"is unknown: {reading.unlisted}",
            f"Check the ownership and permissions of {reading.directory}. No "
            f"connector can start until its row can be read.",
        )
    parts: list[str] = []
    if reading.enrolled:
        parts.append(_named("enrolled", reading.enrolled))
    elif reading.unknown:
        parts.append("none for a system pm-ai supports")
    elif reading.disabled:
        parts.append("none enabled")
    else:
        parts.append("none enrolled")
    if reading.disabled:
        parts.append(_named("disabled", reading.disabled))
    if reading.unknown:
        parts.append(
            _named("for a system pm-ai has no connector for", reading.unknown)
            + f" (it has connectors for {_SUPPORTED})"
        )
    if reading.problems:
        rows = "row" if len(reading.problems) == 1 else "rows"
        parts.append(
            f"{len(reading.problems)} {rows} could not be used: "
            + "; ".join(reading.problems)
        )
    detail = "; ".join(parts)
    if reading.problems:
        return Probe(
            name, Health.WARNING, detail,
            f"Repair or remove each row named, in {reading.directory}. The "
            f"connectors listed as enrolled are unaffected by it.",
        )
    if reading.enrolled:
        return Probe(
            name, Health.OK, detail,
            "`pm-ai connector check` contacts each one and says whether it "
            "answers; this line contacts none of them.",
        )
    if reading.unknown:
        return Probe(
            name, Health.OK, detail,
            f"Correct the `system` of each row named, in {reading.directory}, to "
            f"one of {_SUPPORTED}, or add one with {_ADD_COMMANDS}.",
        )
    if reading.disabled:
        return Probe(
            name, Health.OK, detail,
            f"Set `\"enabled\": true` in a disabled row to use it, or add one "
            f"with {_ADD_COMMANDS}.",
        )
    return Probe(
        name, Health.OK, detail,
        f"Nothing will be harvested until one is added: run {_ADD_COMMANDS}.",
    )


# ── The daemon (story 4e) ────────────────────────────────────────────────────

DAEMON_PROBE = "daemon"
DAEMON_WAIT_SECONDS = 2.0
"""The longest `doctor` waits for a daemon to answer. Loopback answers in milliseconds."""

_LOOPBACK = "127.0.0.1"
_STATUS_PATH = "/v1/status"
# What every response from pm-ai's own API carries (`pm_ai.surfaces.api.app`
# names it `IDENTITY_HEADER`; `platform` may not import `surfaces`, so it is
# spelled again here and the API's tests pin the two together).
_IDENTITY_HEADER = "x-pm-ai"


def daemon_reachable(
    port: int,
    token_path: Path,
    *,
    timeout: float = DAEMON_WAIT_SECONDS,
    port_note: str = "",
) -> Probe:
    """Whether a pm-ai daemon answers on `127.0.0.1:port`, asked with the token file.

    Not running is an ordinary state and reports `OK` — the daemon is started
    by hand in this wave. `WARNING` is for the answers that mean the next
    `pm-ai daemon run` or the next client will not work: something that is not
    pm-ai holds the port, a pm-ai daemon rejects this token file or answers
    with an error, or the port accepts a connection and no whole answer comes
    back inside `timeout` seconds — **in total**, however the peer paces it.

    The token file is read and judged by the daemon's own rule
    (`pm_ai.domain.api_token`, with the same regular-file and size guards), so
    a file the daemon would refuse is named as such and never sent. Speaks
    HTTP/1.1 to loopback only — pm-ai talking to itself, not egress (AD-1) —
    and parses the answer with the standard library's `http.client`. The token
    is never printed. `port_note` is appended to the detail when the caller
    could not read the configured port and fell back to the default.
    """
    address = f"{_LOOPBACK}:{port}"
    token, note = _read_token(token_path)
    note += port_note
    deadline = time.monotonic() + timeout
    request = (
        f"GET {_STATUS_PATH} HTTP/1.1\r\nHost: {address}\r\nConnection: close\r\n"
        + ("" if token is None else f"Authorization: Bearer {token}\r\n")
        + "\r\n"
    ).encode("ascii")
    try:
        raw = _exchange(port, request, deadline)
    except ConnectionRefusedError:
        return Probe(DAEMON_PROBE, Health.OK, f"not running — nothing listens on {address}{note}")
    except TimeoutError:
        return Probe(
            DAEMON_PROBE, Health.WARNING,
            f"not answering — {address} accepted the connection and sent no "
            f"complete answer within {timeout:g} seconds{note}",
            "A daemon that has stopped responding needs a restart (Ctrl-C in its "
            "terminal, then `pm-ai daemon run`). If it is not pm-ai, "
            "`pm-ai daemon run` will be refused on this port.",
        )
    except OSError as unreachable:
        return Probe(
            DAEMON_PROBE, Health.WARNING,
            f"not answering — {address} could not be reached ({unreachable}){note}",
            "Check what holds the port before running `pm-ai daemon run`.",
        )
    response = http.client.HTTPResponse(_Received(raw))  # type: ignore[arg-type]
    try:
        response.begin()
        body = response.read()
    except (http.client.HTTPException, ValueError) as garbled:
        return _foreign(address, f"it did not answer as HTTP ({type(garbled).__name__})", note)
    if response.getheader(_IDENTITY_HEADER) is None:
        return _foreign(address, f"it answered HTTP {response.status}", note)
    if response.status == 401:
        return Probe(
            DAEMON_PROBE, Health.WARNING,
            f"a pm-ai daemon answers on {address} but rejects this token file{note}",
            "The daemon read its token when it started. If the file was replaced "
            "or deleted since, restart the daemon so both use the same one.",
        )
    if response.status != 200:
        return Probe(
            DAEMON_PROBE, Health.WARNING,
            f"a pm-ai daemon answers on {address} with HTTP {response.status}{note}",
            "Restart it; a running daemon answers its status with 200.",
        )
    try:
        status = json.loads(body)
        started_at = str(status["started_at"])
        uptime = int(status["uptime_seconds"])
    except (ValueError, KeyError, TypeError, OverflowError):
        return Probe(
            DAEMON_PROBE, Health.WARNING,
            f"a pm-ai daemon answers on {address} but its status could not be read{note}",
            "Restart it; this pm-ai and the running daemon may be different versions.",
        )
    return Probe(
        DAEMON_PROBE, Health.OK,
        f"running on {address}, up {uptime}s, started at {started_at}{note}",
    )


_MAX_ANSWER_BYTES = 64 * 1024
"""The most of an answer the probe reads. A status body is a few kilobytes."""


def _read_token(path: Path) -> tuple[str | None, str]:
    """The token to send, or `None` and the clause naming why nothing is sent."""
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None, (
            f"; there is no token file at {path} yet — the first `pm-ai daemon "
            f"run` mints it"
        )
    except OSError as unreadable:
        if unreadable.errno == errno.ELOOP:
            return None, f"; {symlinked(str(path))}"
        return None, f"; the token file at {path} could not be read ({unreadable.strerror})"
    try:
        mode = os.fstat(descriptor).st_mode
        raw = b""
        if stat.S_ISREG(mode):
            limit = MAX_TOKEN_FILE_BYTES + 1
            while len(raw) < limit and (chunk := os.read(descriptor, limit - len(raw))):
                raw += chunk
    except OSError as unreadable:
        return None, f"; the token file at {path} could not be read ({unreadable.strerror})"
    finally:
        os.close(descriptor)
    try:
        return check_api_token(raw, mode=mode, where=str(path)), ""
    except ApiTokenRefused as unusable:
        return None, f"; the daemon would refuse its token file, so it was not sent: {unusable}"


def _exchange(port: int, request: bytes, deadline: float) -> bytes:
    """Send `request` and read until the peer closes, all before `deadline`.

    Every socket operation gets only what is left of the one budget, so a
    peer that drips a byte at a time cannot stretch the wait. `TimeoutError`
    when the budget runs out; `ConnectionRefusedError` when nothing listens.
    """

    def left() -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("the probe's deadline passed")
        return remaining

    with socket.create_connection((_LOOPBACK, port), timeout=left()) as connection:
        connection.settimeout(left())
        connection.sendall(request)
        received = bytearray()
        while len(received) <= _MAX_ANSWER_BYTES:
            connection.settimeout(left())
            chunk = connection.recv(4096)
            if not chunk:
                break
            received += chunk
        return bytes(received)


class _Received:
    """A finished answer, offered to `http.client.HTTPResponse` as its socket."""

    def __init__(self, raw: bytes) -> None:
        self._raw = raw

    def makefile(self, mode: str, *args: object, **kwargs: object) -> io.BytesIO:
        return io.BytesIO(self._raw)


def _foreign(address: str, how: str, note: str = "") -> Probe:
    return Probe(
        DAEMON_PROBE, Health.WARNING,
        f"something that is not pm-ai answers on {address} — {how}{note}",
        "`pm-ai daemon run` would be refused on this port. Stop that service, or "
        "choose a free port with `daemon_port` in ~/.pm-ai/config.toml.",
    )


def run_all(
    keychain: KeychainPort | None = None,
    *,
    config: ArtifactState | None = None,
    registry: ArtifactState | None = None,
) -> Report:
    """Every probe, whatever any single one of them says.

    Sequential and independent on purpose: one failure must not stop the others,
    or an operator fixes one thing at a time across four restarts.

    `config` and `registry` arrive already read, because `read_artifact` is the
    single reader and a probe that opened either file would be a second one.
    Their default is `UNOBTAINABLE` rather than `ABSENT`, and the difference is
    the whole reason `ArtifactState` exists: a caller that did not hand over the
    bytes has not told this function the file is missing, and reporting it as a
    first run would be an answer nobody gave.

    The two are last in the order for the same reason `packages_installed` is
    first — an operator reading top-down meets causes before consequences, and a
    config that could not be obtained is usually downstream of something above
    it.
    """
    if keychain is None:  # pragma: no cover - the real adapter, not used in tests
        from pm_ai.platform.keychain import MacOSKeychainAdapter

        keychain = MacOSKeychainAdapter()
    nobody_said = ArtifactState.unobtainable("no caller supplied it to this run")
    return Report(
        (
            packages_installed(),
            sqlite_extension_support(),
            keychain_reachable(keychain),
            encryption_toggle(),
            git_available(),
            config_readable(config if config is not None else nobody_said),
            registry_readable(registry if registry is not None else nobody_said),
        )
    )
