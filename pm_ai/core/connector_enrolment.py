"""Enrolling a connector: probe it, seal its credential, then configure it (CAP-35).

Two artifacts are written and **the order is the whole story**. The credential
goes into the encrypted `~/.pm-ai/private/config.json`; the connector's
configuration goes into the unencrypted `~/.pm-ai/connectors/`. The master key is
fetched lazily (story `1f`), so the encrypted write is the one that can still
refuse at the moment it happens — and in this order a refusal leaves nothing
behind at all.

The other order leaves a connector configured, enabled and holding no
credential, which is the worst of the three reachable states: it reads as a
working connector, the daemon harvests nothing from it, and AD-39 renders that
as "no coverage yet" forever. So the secret is written first, and the tests
assert the **absence of files** rather than the presence of an error message. An
error message proves the code noticed; an empty directory proves it left nothing
behind.

## What this module may not do

`core` is I/O-free (AD-1, `.importlinter`'s `core-is-io-free`), which forbids
every HTTP client here. The live probe is therefore a **parameter**, supplied by
the composition root from an adapter in `pm_ai.connectors` — the same
arrangement that makes `8d`'s health probes legal. Importing one instead would
be either an illegal import or an `Any`-typed dependency, which is the defect
story `1k` retired.

`core` also may not open a file. Every write goes through `write_artifact`, and
what is sealed is decided by the artifact's declaration in `pm_ai.domain
.scope_model` — never by this module. `connectors/<instance>.json` lands at 0600
because the trees declare that collection gitignored (story `8f`), not because
anything here chmods it.

## The credential never appears

Not echoed at the prompt (`4c`'s surface reads it without echo), not logged, not
formatted into a refusal, not returned, and not written into the unencrypted
connector configuration. `enrol_connector` returns the *instance name*, for the
same reason `pm_ai.core.enrolment.enrol` returns the key name: a return value
carrying the secret puts it in the caller's frame and in any traceback raised
beneath it.

## What is deliberately not here

No connector-specific auth. The Graph device-code flow is story `33a`, and this
has to work for a plain token typed at a prompt. No `pm-ai connector disable`,
and no hot registration into a running daemon: both need a poller to halt or a
daemon to register into, and neither exists before `4d`/`9a`. Registration is
construction-time, per `8d` — which is why the success message says the
connector becomes active at the next start rather than claiming it is live.
"""

from __future__ import annotations

import json
from collections.abc import Mapping

from pm_ai.domain.identity import DataScope, ScopeKind
from pm_ai.ports import (
    CredentialProbePort,
    DuplicateConnector,
    ProbeFailed,
    ProbeUnreachable,
    StoragePort,
    UnknownConnectorSystem,
)

__all__ = [
    "CONNECTORS",
    "CREDENTIAL_STORE",
    "DuplicateConnector",
    "MalformedInstanceName",
    "OrphanedCredential",
    "ProbeFailed",
    "ProbeUnreachable",
    "UnknownConnectorSystem",
    "connector_configurations",
    "enrol_connector",
    "stored_credentials",
]

# Both artifacts live in the application scope, and this module says so rather
# than taking a scope from its caller. `config.json` and `connectors/` are
# declared in `APPLICATION_TREE` and nowhere else — a connector's credential is
# system-level state by construction, and letting a surface choose the scope
# would be letting it file an employer's token into the sovereign personal one.
APPLICATION = DataScope(ScopeKind.APPLICATION)

CREDENTIAL_STORE = "config.json"
"""The sealed store, one file for every connector's credential."""

CONNECTORS = "connectors/"
"""The unencrypted collection, one member per enrolled connector instance."""

# The one key inside the sealed store this slice owns. Nested rather than
# top-level so that a later occupant of `config.json` — the Graph refresh token
# `33a` needs somewhere to live is the next one — is not something this module
# has to know about in order to preserve. The read-modify-write keeps every
# sibling key it did not put there.
CREDENTIALS_KEY = "connectors"

# What a connector's instance name may be spelled with. Deliberately narrower
# than the filesystem allows, because the name is interpolated into a path and
# is also a registry key, a cursor key and a coverage-window key: `gitlab:alpha`
# has to be expressible, and `../graph` must not be. The colon is in the set
# because `8d`'s registry already spells an instance `<system>:<what it covers>`.
_INSTANCE_CHARACTERS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.:"
)

# Well under any filesystem's component limit, and under `pm_ai.storage`'s own
# 128-byte ceiling once `.json` is appended.
_INSTANCE_LIMIT = 96


class MalformedInstanceName(ValueError):
    """The instance name cannot be a filename, so nothing was attempted.

    Raised **before the probe**, which is the load-bearing half. The name is
    interpolated into `connectors/<instance>.json`, so a traversal would be
    written outside the directory the git check just answered for — and a
    refusal that arrived after the credential was sealed would orphan one on
    every attempt.
    """


class OrphanedCredential(RuntimeError):
    """The credential is sealed and the configuration is not, and here is which one.

    The second write failed — a full disk, a directory that vanished. Reported
    rather than rolled back: unwinding means a delete path inside the enrolment
    flow, and a delete that itself fails leaves the same state with more code in
    it. Saying which instance is half-enrolled lets a human either re-run
    enrolment or clean up, and the duplicate check finds it on the next attempt
    either way.

    Never silent, which is the point. The reachable alternative was a sealed
    credential nothing on the machine refers to.
    """


def stored_credentials(storage: StoragePort) -> dict[str, dict[str, str]]:
    """Every connector credential the sealed store holds, keyed by instance.

    An absent store reads as an empty mapping. It is the ordinary state of the
    first machine any connector is ever enrolled on, and `read_artifact` says so
    with `None` since story `8f` — a first run is not a failure.

    Everything else propagates. A store that will not decrypt, a keychain that
    cannot be asked, no key at all: the file is there and unopenable, which is a
    different sentence from "nothing has been written yet", and answering the
    second when the first is true is how a machine that cannot read its own
    credentials looks freshly installed.
    """
    sealed = storage.read_artifact(scope=APPLICATION, artifact=CREDENTIAL_STORE)
    return _credentials_in(_decode(sealed))


def connector_configurations(storage: StoragePort) -> tuple[str, ...]:
    """The instance names `connectors/` currently configures, from its members.

    Names, never paths — `list_collection` is what keeps this module unable to
    learn where the collection lives (story `1a`). The `.json` suffix is the
    writer's own convention, applied here and stripped here, so nothing outside
    this module has to know a member is a file.
    """
    return tuple(
        member.removesuffix(".json")
        for member in storage.list_collection(scope=APPLICATION, artifact=CONNECTORS)
        if member.endswith(".json")
    )


def enrol_connector(
    storage: StoragePort,
    *,
    system: str,
    instance: str,
    credential: str,
    probe: CredentialProbePort,
) -> str:
    """Probe `credential`, seal it, then configure `instance`. Returns the instance.

    The order, and what each step buys:

    1. **The name is checked.** It becomes a filename, and a bad one refused
       after the seal would orphan a credential on every attempt.
    2. **The duplicate check enumerates both stores** — `connectors/` first,
       then the sealed one. `connectors/` alone misses an orphaned credential;
       the sealed store alone misses a configured connector. First rather than
       second because reading the sealed store needs the master key, and on a
       keyless machine the refusal should be about the key rather than a
       spurious duplicate.
    3. **The git question is pre-flighted.** `connectors/` is declared
       gitignored, so its write is refused when git cannot say whether the
       directory would be committed — and that refusal would otherwise land
       after the seal.
    4. **The provider is asked.** A bad credential is refused while the human
       who typed it is present, rather than discovered by a silent harvest at
       03:00. Nothing has been written at this point, and nothing is if it
       refuses.
    5. **The sealed store is read, modified and written under an exclusive
       claim.** It is one file holding every connector's credential and
       `write_artifact` replaces whole, so a plain write while enrolling a
       second connector destroys the first's token — and two enrolments racing
       do the same thing one level down, which is what the claim is for.
    6. **The configuration is written.** No credential in it. If this fails, the
       sealed credential is reported as orphaned rather than left silent.

    Returns the instance name. Never the credential, and never anything derived
    from it.
    """
    _assert_nameable(instance)
    _assert_nameable(system)

    configured = connector_configurations(storage)
    if instance in configured:
        raise DuplicateConnector(
            f"{instance!r} is already configured in {CONNECTORS}, and enrolment "
            f"does not replace one. Two connectors under one instance name "
            f"share a cursor and a credential, so one of them silently "
            f"re-harvests from the other's position. Nothing was written."
        )
    if instance in stored_credentials(storage):
        raise DuplicateConnector(
            f"a credential is already sealed for {instance!r}, though nothing "
            f"configures it in {CONNECTORS}. That is a half-finished enrolment — "
            f"a previous attempt was interrupted between its two writes — and "
            f"not a machine with a working connector on it. Nothing was written. "
            f"Remove the {instance!r} entry from the sealed store, or enrol under "
            f"another instance name."
        )

    # Before the first write, exactly as the name is checked before the probe.
    storage.assert_writable(scope=APPLICATION, artifact=CONNECTORS)

    answer = probe(system, credential)

    with storage.exclusive(scope=APPLICATION, artifact=CREDENTIAL_STORE):
        # Read *inside* the claim, and not reusing what the duplicate check
        # already read. Between those two moments another process may have
        # finished its own enrolment, and merging into a stale mapping is how
        # this write would delete that one's credential — which is the entire
        # failure the claim and the read-modify-write exist to prevent.
        document = _decode(storage.read_artifact(scope=APPLICATION, artifact=CREDENTIAL_STORE))
        credentials = _credentials_in(document)
        if instance in credentials:
            raise DuplicateConnector(
                f"a credential for {instance!r} appeared in the sealed store "
                f"while this enrolment was running, so another process enrolled "
                f"it first. Nothing was written: overwriting would replace a "
                f"credential this command never saw."
            )
        credentials[instance] = {"system": system, "credential": credential}
        document[CREDENTIALS_KEY] = credentials
        storage.write_artifact(
            _encode(document), scope=APPLICATION, artifact=CREDENTIAL_STORE
        )

        try:
            storage.write_artifact(
                _encode({"instance": instance, "system": system, "enabled": True}),
                scope=APPLICATION,
                artifact=CONNECTORS,
                name=f"{instance}.json",
            )
        except Exception as unwritten:
            # Reported, not rolled back — see `OrphanedCredential`. The cause is
            # chained rather than quoted, so whatever the filesystem said
            # survives; none of it can carry the credential, which never left
            # the sealed write above.
            raise OrphanedCredential(
                f"{instance!r}'s credential is sealed and its configuration was "
                f"not written, so this machine holds a credential nothing refers "
                f"to. The connector is NOT enrolled and will not harvest. Fix "
                f"what the write failed on and enrol {instance!r} again — the "
                f"next attempt will refuse as a duplicate and name this "
                f"credential, which is how it is found."
            ) from unwritten

    return _redacted(answer, credential)


# ── Everything below is pure, and none of it ever sees a path ────────────────


def _assert_nameable(value: str) -> None:
    """Refuse a name that cannot be one path component of `connectors/`.

    The refusals mirror the single writer's own `_capture_name`, which validates
    the same string again at the moment it is interpolated. Duplicated on
    purpose and in this direction only: this one has to run *before* the probe
    and before either write, and the writer's has to hold for every caller
    whether or not one remembered to ask. Two checks that agree are a belt; one
    check in the wrong place is a credential orphaned on every attempt.
    """
    if not value or value != value.strip():
        raise MalformedInstanceName(
            f"{value!r} is empty or padded with whitespace. The instance name is "
            f"a filename, a registry key and a cursor key, and two names "
            f"differing only in spacing are one name in every listing that "
            f"reports them."
        )
    if value.startswith("."):
        raise MalformedInstanceName(
            f"{value!r} starts with a dot. `.` and `..` are directories, and a "
            f"dotfile is hidden from the operator who has to find it — the "
            f"single writer omits dot-prefixed entries from a collection "
            f"listing, so this connector would be invisible to its own "
            f"duplicate check."
        )
    if len(value) > _INSTANCE_LIMIT:
        raise MalformedInstanceName(
            f"{value!r} is {len(value)} characters; the limit is "
            f"{_INSTANCE_LIMIT}. Past the filesystem's own limit the write fails "
            f"with a bare `OSError` naming neither the connector nor the limit."
        )
    illegal = sorted(set(value) - _INSTANCE_CHARACTERS)
    if illegal:
        raise MalformedInstanceName(
            f"{value!r} contains {illegal}, which an instance name may not: it "
            f"is interpolated into a path, so a separator would be written "
            f"outside the directory the git check answered for, and a control "
            f"character would split one filename across two lines in every "
            f"message that reports it. Letters, digits, `-`, `_`, `.` and `:`."
        )


def _decode(raw: bytes | None) -> dict[str, object]:
    """The sealed store as a mapping — absent, empty and blank all meaning `{}`.

    `None` is `read_artifact`'s answer for "nothing has been written there yet",
    which on this artifact is the first enrolment ever and not a failure. A
    store that decodes to something other than an object is refused rather than
    replaced: it is not this module's to discard, and overwriting it would
    destroy whatever put it there.
    """
    if raw is None or not raw.strip():
        return {}
    document = json.loads(raw.decode("utf-8"))
    if not isinstance(document, dict):
        raise ValueError(
            f"{CREDENTIAL_STORE} decrypted to a "
            f"{type(document).__name__} rather than an object. Enrolment will "
            f"not replace it: something else wrote this file, and rewriting it "
            f"whole would destroy whatever that was."
        )
    return document


def _credentials_in(document: Mapping[str, object]) -> dict[str, dict[str, str]]:
    """The connector half of the sealed store, defaulted and type-checked."""
    held = document.get(CREDENTIALS_KEY, {})
    if not isinstance(held, dict):
        raise ValueError(
            f"{CREDENTIAL_STORE}'s {CREDENTIALS_KEY!r} is a "
            f"{type(held).__name__} rather than an object keyed by instance "
            f"name. Enrolment will not replace it."
        )
    return {str(k): dict(v) for k, v in held.items() if isinstance(v, dict)}


def _encode(document: Mapping[str, object]) -> bytes:
    """Canonical JSON bytes. Sorted, so two runs writing the same state agree."""
    return json.dumps(document, sort_keys=True, indent=2).encode("utf-8") + b"\n"


def _redacted(answer: str, credential: str) -> str:
    """The probe's sentence, with a last check that the credential is not in it.

    A probe is an adapter, and an adapter that interpolated the token it was
    given into "gitlab accepted <token>" would put it on the operator's screen
    from a module this one cannot see. Cheap, and it turns a leak that depends
    on somebody else's string formatting into one this slice's own tests can
    assert against.
    """
    if credential and credential in answer:
        return "the provider accepted the credential"
    return answer
