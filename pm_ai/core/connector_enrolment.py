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
has to work for a plain token typed at a prompt. A Graph enrolment (story 8l)
signs in and checks the result before it calls this, and hands its settings in
as data; nothing here knows what they mean. No `pm-ai connector disable`.
Nothing here registers a connector into a running daemon either: since `4r`
every daemon job reads the connector rows afresh, and since `9a` a running
daemon's harvest timer starts harvesting a new row the next time it reads the
connector list, so the success message says when that normally is (story 4s)
rather than "the next start".
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping

from pm_ai.domain.connector_rows import CATEGORIES_KEY, USER_ID_KEY
from pm_ai.domain.handles import usable_user_id
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
    "_default_project",
    "CONNECTORS",
    "CATEGORIES_ABSENT",
    "CREDENTIAL_STORE",
    "CategoriesChanged",
    "CategoryProjectUnenrolled",
    "ConnectorNotConfigured",
    "ConnectorRowChanged",
    "CredentialStoreUnclaimable",
    "CredentialNotHeld",
    "DuplicateConnector",
    "MalformedInstanceName",
    "MalformedSettings",
    "NotAGraphRow",
    "OrphanedCredential",
    "ProbeFailed",
    "ProbeUnreachable",
    "RESERVED_ROW_KEYS",
    "USER_ID_KEY",
    "UnknownConnectorSystem",
    "UnplacedConnector",
    "UserNotRecorded",
    "assert_enrollable",
    "assert_replaceable",
    "assert_unconfigured",
    "connector_configurations",
    "connector_row",
    "credential_for",
    "enrol_connector",
    "record_user_id",
    "record_categories",
    "recorded_user_id",
    "redacted",
    "replace_credential",
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
# top-level so that a later occupant of `config.json` is not something this
# module has to know about in order to preserve. The read-modify-write keeps
# every sibling key it did not put there. The Graph refresh token is *not* such
# an occupant: it is the `credential` of its own instance under this key, and a
# rotated one is written back by `replace_credential` (story 8j).
CREDENTIALS_KEY = "connectors"

# What a connector's instance name may be spelled with. Deliberately narrower
# than the filesystem allows, because the name is interpolated into a path and
# is also a registry key, a cursor key and a coverage-window key: `gitlab:alpha`
# has to be expressible, and `../graph` must not be. The colon is in the set
# because `8d`'s registry already spells an instance `<system>:<what it covers>`.
_INSTANCE_CHARACTERS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.:"
)

RESERVED_ROW_KEYS = frozenset({"instance", "system", "enabled", "project"})
"""The keys of a `connectors/<instance>.json` row enrolment writes itself.

A caller's `settings` may not name one (story 8l): a row whose `instance` or
`system` disagreed with the credential sealed beside it would be a connector
wearing another's name, so a clash is refused before anything is asked.
"""

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


class MalformedSettings(ValueError):
    """A connector's row settings cannot be written, so nothing was attempted.

    Two causes, one repair at the caller: a setting names a key enrolment
    writes itself (`RESERVED_ROW_KEYS`), or a value cannot be encoded into the
    row. Raised **before the probe and before the seal**, for
    `MalformedInstanceName`'s reason — refused after the credential was sealed,
    it would orphan one.
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


class UnplacedConnector(ValueError):
    """No start would build a connector under this name, so nothing was asked (story 8o).

    On a machine watching several projects, a GitLab connector is built only
    when its instance name is `gitlab:<enrolled project id>`: nothing else says
    which project's tree its harvest files into. Enrolling under any other name
    would seal a credential for a connector that is never built, so the name
    is refused before anything is asked. The message names the names that
    would build.

    Declared here rather than in `pm_ai.ports` because it is about enrolment's
    own rule for a name — like `MalformedInstanceName` beside it — not about a
    port's contract (AD-49); `pm_ai.app` raises it and the CLI catches it.
    """


class CredentialNotHeld(LookupError):
    """The sealed store holds no credential for this instance under this system.

    Raised by `replace_credential`. Either the instance has no entry — never
    enrolled, or removed since — and the caller did not say a row configures
    it, or its entry is recorded under another system, which is a different
    connector wearing the same name. Nothing is written, and the instance is
    named.
    """


class UserNotRecorded(RuntimeError):
    """The new sign-in is sealed and the row's user id was not written (story 8m).

    `pm-ai connector sign-in` replaces the credential first and then records
    the signed-in user in the row; this is the second write failing after the
    first landed. Reported rather than rolled back, for `OrphanedCredential`'s
    reason: the credential is good and in use from now on, and running the
    command again records the user. Never silent.

    `answer` is the health check's sentence, redacted: the sign-in did land,
    so the surface prints it before the refusal.
    """

    def __init__(self, message: str, *, answer: str) -> None:
        super().__init__(message)
        self.answer = answer


class ConnectorRowChanged(RuntimeError):
    """The row changed while its sign-in ran, so the sign-in was not saved (story 8m).

    A sign-in is made with the row's app id, tenant and widths as they were
    read before it; a row edited meanwhile to name others would be saved
    beside a credential obtained for the old ones. Nothing is written, and
    what changed is named.
    """


class CredentialStoreUnclaimable(RuntimeError):
    """The credentials file's claim could not be taken at all — not busy (story 8m).

    The lock path is a directory or unreadable, or the filesystem has no locks.
    Nothing is written, and `config.json` is named.
    """


class ConnectorNotConfigured(LookupError):
    """`connectors/` holds no row for this instance, so there is nothing to update.

    Raised by `record_user_id`, which changes one key of an enrolled row and
    never creates one. Nothing is written, and the instance is named.
    """


class NotAGraphRow(ValueError):
    """The row is not a Graph connector's, so it records no Graph user (story 8q).

    `record_user_id` writes a Graph user id; written into another system's row
    it would name a user that system never signed in. Nothing is written, and
    the instance is named.
    """


class CategoriesChanged(RuntimeError):
    """The row's `categories` changed while its questions were answered (story 4s).

    `pm-ai connector categories` shows the mapping, asks, and saves only if the
    row still holds what it showed: two people's answers about one mapping
    have no right merge. Nothing is written.
    """


class CategoryProjectUnenrolled(LookupError):
    """A project the answers map a category to was removed from the registry (4s).

    A Graph row mapping a category to a project no registry knows is not built
    at all, so it is never saved. Nothing is written, and the project is named.
    """


class _Absent:
    """The `categories` key was not in the row."""

    def __repr__(self) -> str:
        return "no categories key"


CATEGORIES_ABSENT: object = _Absent()
"""What `record_categories` is told was shown when the row had no `categories`."""


class ClaimNotHeld(RuntimeError):
    """A caller said it held the claim and does not — a bug, not a refusal (story 8q).

    `claimed=True` means "I hold the sealed store's claim on this thread";
    trusted without checking, a caller that was wrong would write unserialized.
    """


def _sealed_under(entry: Mapping[str, object], system: str) -> bool:
    """Whether a sealed entry may be used, and replaced, as `system`'s credential.

    One rule for reading and for writing back, so a credential that can be
    read can also be replaced. An entry recording a different, non-empty
    system belongs to another connector. An entry recording none — missing,
    empty, or not a string — is the instance's own; refusing it on write-back
    after accepting it on read would lose a rotated token.
    """
    recorded = entry.get("system")
    return not (isinstance(recorded, str) and recorded and recorded != system)


def credential_for(sealed: object, *, system: str) -> str | None:
    """The credential string in one sealed entry, or `None` when it has none to use.

    `None` for an entry that is not an object, is recorded under another
    system (see `_sealed_under`), or holds something other than a string.
    """
    if not isinstance(sealed, Mapping) or not _sealed_under(sealed, system):
        return None
    credential = sealed.get("credential")
    return credential if isinstance(credential, str) else None


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


def assert_unconfigured(
    storage: StoragePort,
    *,
    system: str,
    instance: str,
    files_into: Callable[[str], str | None] | None = None,
) -> None:
    """Refuse a malformed name, or one a row in `connectors/` already uses (story 8o).

    The part of `assert_enrollable` that needs no master key: the names'
    shape, then the unencrypted collection. `assert_enrollable` calls it
    first. `pm-ai connector add gitlab` also calls it on its own, before the
    terminal check — nothing is asked by it — so a name in use is the first
    thing said; the rest of `assert_enrollable`, where the keychain is first
    reached, runs after the terminal check.

    `files_into` (story 8t, AD-10) names the project an instance name files
    into, or `None` when it files into none yet. Given it, a GitLab name is
    refused when another **enrolled** GitLab row already files into the same
    project, naming that row: one GitLab instance per project scope, for now.
    The built-in `gitlab:<project id>` each watched project gets is not a row,
    so enrolling under its name replaces it and is not a second instance.

    Raises `MalformedInstanceName` or `DuplicateConnector`.
    """
    _assert_nameable(instance)
    _assert_nameable(system, label="system name")

    configured = connector_configurations(storage)
    if instance in configured:
        raise DuplicateConnector(
            f"{instance!r} is already configured in {CONNECTORS}, and enrolment "
            f"does not replace one. Two connectors under one instance name "
            f"share a cursor and a credential, so one of them silently "
            f"re-harvests from the other's position. Nothing was written."
        )
    if system == "gitlab" and files_into is not None:
        _assert_one_gitlab_per_project(
            storage, instance=instance, configured=configured, files_into=files_into
        )


def _assert_one_gitlab_per_project(
    storage: StoragePort,
    *,
    instance: str,
    configured: tuple[str, ...],
    files_into: Callable[[str], str | None],
) -> None:
    """Refuse a second enrolled GitLab row for a project that has one (AD-10, story 8t).

    A reader cannot yet tell which repository a bare `#N` means, and the
    project's work is kept per `(project, instance)` (AD-3), so a project has
    at most one GitLab instance. Only enrolled rows count — a row whose
    `system` is `gitlab`; a row that cannot be read is not counted.
    """
    project = files_into(instance)
    if project is None:
        return
    for other in sorted(configured):
        if other == instance:
            continue
        try:
            row = connector_row(storage, instance=other)
        except (ConnectorNotConfigured, MalformedSettings, MalformedInstanceName):
            continue
        if row.get("system") != "gitlab" or files_into(other) != project:
            continue
        raise DuplicateConnector(
            f"{other!r} is already an enrolled GitLab connector filing into the "
            f"project {project!r}, and a project has one GitLab instance for now "
            f"(AD-10): with two, a bare issue number could mean either "
            f"repository. Nothing was written. Remove {other!r}'s row in "
            f"{CONNECTORS} first to enrol {instance!r} in its place."
        )


def assert_enrollable(storage: StoragePort, *, system: str, instance: str) -> None:
    """Refuse an instance that cannot be enrolled, before anything is asked of anyone.

    Steps 2 to 4 of `enrol_connector`, callable on their own (story 8l). A
    Graph enrolment asks four questions and runs a sign-in that takes minutes
    before it has a credential to hand over, so a name already in use has to
    be refused *first* — nobody should finish a sign-in only to be told the
    name was taken. `enrol_connector` runs this again itself, because the state
    may have changed while the human was answering.

    Raises `MalformedInstanceName`, `DuplicateConnector`, and whatever reading
    the sealed store, pre-flighting the git question or fetching the master
    key raises — `KeyNotFound` on a machine with none enrolled.
    """
    assert_unconfigured(storage, system=system, instance=instance)
    if instance in stored_credentials(storage):
        raise DuplicateConnector(
            f"a credential is already sealed for {instance!r}, though nothing "
            f"configures it in {CONNECTORS}. That is a half-finished enrolment — "
            f"a previous attempt was interrupted between its two writes — and "
            f"not a machine with a working connector on it. Nothing was written. "
            f"Remove the {instance!r} entry from the sealed store, or enrol under "
            f"another instance name."
        )

    # Before the first write, exactly as the name is checked before the probe:
    # git's question for `connectors/`, and the master key for the sealed
    # store, which on a fresh machine has never been read and so has never
    # asked for it.
    storage.assert_writable(scope=APPLICATION, artifact=CONNECTORS)
    storage.assert_writable(scope=APPLICATION, artifact=CREDENTIAL_STORE)


def assert_replaceable(storage: StoragePort, *, system: str, instance: str) -> None:
    """Refuse an instance whose stored credential a new sign-in may not replace (story 8m).

    `assert_enrollable`'s counterpart for `pm-ai connector sign-in`, run before
    the sign-in for the same reason: nobody should finish one only to learn the
    result cannot be saved. The row itself is the caller's to judge; this asks
    the sealed store. An entry recorded under another system is refused —
    another connector wearing this name — while no entry at all, or no file,
    is not: `replace_credential(configured=True)` writes one. Then both writes
    are pre-flighted, which fetches the master key: `KeyNotFound` on a machine
    with none, before anything is asked.

    Raises `MalformedInstanceName`, `CredentialNotHeld`, and whatever reading
    the sealed store or pre-flighting the writes raises.
    """
    _assert_nameable(instance)
    _assert_nameable(system, label="system name")
    held = stored_credentials(storage).get(instance)
    if held is not None and not _sealed_under(held, system):
        raise CredentialNotHeld(
            f"the entry for {instance!r} in {CREDENTIAL_STORE} is recorded under "
            f"the system {held.get('system')!r}, and its row says {system!r}. "
            f"That is a different connector under the same instance name, so a "
            f"{system} sign-in will not replace it. Nothing was asked and nothing "
            f"was saved."
        )
    storage.assert_writable(scope=APPLICATION, artifact=CONNECTORS)
    storage.assert_writable(scope=APPLICATION, artifact=CREDENTIAL_STORE)


def enrol_connector(
    storage: StoragePort,
    *,
    system: str,
    instance: str,
    credential: str,
    probe: CredentialProbePort,
    settings: Mapping[str, object] | None = None,
    project: str | None = None,
) -> str:
    """Probe `credential`, seal it, then configure `instance`.

    `project` is the row's `project`, when the caller knows it (story 8o): a
    GitLab path such as `group/project`, which an instance name cannot hold.
    `None` keeps the default derived from the instance's suffix. A parameter
    rather than a setting, so `project` stays in `RESERVED_ROW_KEYS` and a
    caller's settings still cannot name it. A blank one, or one with no `/`,
    is `MalformedSettings` before anything is asked or written.

    The order, and what each step buys:

    1. **The row is composed and encoded.** `settings` are the connector's own
       keys for it — a Graph row's app id, tenant and harvest widths (story 8l)
       — written beside the four this module owns, never instead of them. A
       setting naming one of `RESERVED_ROW_KEYS`, or a value that cannot be
       encoded, is a `MalformedSettings` here, before anything is asked or
       written; refused after the seal, it would orphan a credential.
    2. **The name is checked.** It becomes a filename, and a bad one refused
       after the seal would orphan a credential on every attempt.
    3. **The duplicate check enumerates both stores** — `connectors/` first,
       then the sealed one. `connectors/` alone misses an orphaned credential;
       the sealed store alone misses a configured connector. First rather than
       second because reading the sealed store needs the master key, and on a
       keyless machine the refusal should be about the key rather than a
       spurious duplicate.
    4. **Both writes are pre-flighted.** `connectors/` is declared gitignored,
       so its write is refused when git cannot say whether the directory would
       be committed — and that refusal would otherwise land after the seal.
       The sealed store's master key is fetched, so a machine with none
       refuses now rather than at the seal (story 8l).
    5. **The provider is asked.** A bad credential is refused while the human
       who typed it is present, rather than discovered by a silent harvest at
       03:00. Nothing has been written at this point, and nothing is if it
       refuses.
    6. **The sealed store is read, modified and written under an exclusive
       claim.** It is one file holding every connector's credential and
       `write_artifact` replaces whole, so a plain write while enrolling a
       second connector destroys the first's token — and two enrolments racing
       do the same thing one level down, which is what the claim is for.
    7. **The row encoded in step 1 is written.** No credential in it. If this
       fails, the sealed credential is reported as orphaned rather than left
       silent.

    Steps 2 to 4 are `assert_enrollable`, which a caller may also run on its
    own before it has a credential to offer.

    Returns the provider's own sentence about what answered, redacted — for the
    operator, who has just typed a secret and deserves to know what accepted it.
    Never the credential: `redacted` withholds any sentence holding an
    eight-character run of it, and every refusal path is asserted against five
    spellings of it. A credential that wraps another secret — a sealed Graph
    sign-in holds its refresh token inside a JSON document — is redacted as the
    string it was handed; the caller that knows the inner secret redacts it too
    (`GraphEnrolment` does).
    """
    extra = dict(settings or {})
    if project is not None and (
        not isinstance(project, str)
        or not project.strip()
        or "/" not in project
    ):
        raise MalformedSettings(
            f"the project for {instance!r} is {project!r}, which is not a path "
            f"such as 'group/project'. Nothing was written."
        )
    clash = sorted(RESERVED_ROW_KEYS & extra.keys())
    if clash:
        raise MalformedSettings(
            f"the settings for {instance!r} name {', '.join(clash)}, which "
            f"enrolment writes itself. Nothing was written."
        )
    # Encoded now, before anything is asked or sealed: a value that cannot be
    # written refuses here rather than after the credential is on disk.
    try:
        row = _encode(
            {
                "instance": instance,
                "system": system,
                "enabled": True,
                # Written explicitly rather than re-derived from the instance
                # suffix at composition. The instance name is a path component
                # and may not contain `/`, while a real GitLab project is
                # `group/project` — so deriving one from the other silently
                # built an adapter for the wrong path. Recorded here, and
                # editable in this file, which is unencrypted and hand-editable
                # for exactly this.
                "project": _default_project(instance) if project is None else project,
                # A connector's own settings (story 8l): Graph's app id, tenant
                # and harvest widths, in the same row and by the same writer.
                **extra,
            },
            strict=True,
        )
    except (TypeError, ValueError) as unencodable:
        raise MalformedSettings(
            f"the settings for {instance!r} cannot be written into its row "
            f"({unencodable}). Nothing was written."
        ) from None
    assert_enrollable(storage, system=system, instance=instance)

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
                row,
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
                f"what the write failed on and run `pm-ai connector add` for "
                f"{instance!r} once more — the "
                f"next attempt will refuse as a duplicate and name this "
                f"credential, which is how it is found."
            ) from unwritten

    return redacted(answer, credential)


def replace_credential(
    storage: StoragePort,
    *,
    system: str,
    instance: str,
    credential: str,
    claimed: bool = False,
    configured: bool = False,
) -> bool:
    """Replace one enrolled instance's credential, and nothing else (story 8j).

    The write-back a provider that rotates its credentials needs: Microsoft
    retires a refresh token on use, and a replacement kept only in memory is
    lost when the command ends. The same read-modify-write as enrolment, under
    the same claim, so neither can lose the other's write — and keyed, so every
    other connector's credential and every other top-level key is written back
    as it was read.

    `claimed` says the caller already holds `storage.exclusive` on the sealed
    store. The claim is not re-entrant, so a caller holding it must say so, and
    one that does not hold it gets it taken here.

    Differs from enrolment in two ways and only two: an existing entry is
    required rather than refused, and there is no probe, because the provider
    that issued the credential has just answered. Refused with
    `CredentialNotHeld`, writing nothing, when the instance has no entry or its
    entry is sealed under another system.

    `configured` is the one exception to "an existing entry is required"
    (story 8m): the caller says `connectors/` configures this instance, so an
    absent entry — none for the name, or no credentials file at all — is
    written rather than refused. `pm-ai connector sign-in` is that caller: a
    rotated token that had nowhere to go would otherwise have no remedy. The
    row is read here, inside the claim: its absence is
    `ConnectorNotConfigured`, a row that is not an object `MalformedSettings`,
    and a row recording another `system` than `system` is `CredentialNotHeld`;
    an entry under another system is still refused.

    Returns whether the file was written. The same credential is not written
    again: a rewrite that changes nothing is a write that can still fail.
    """
    if not claimed:
        with storage.exclusive(scope=APPLICATION, artifact=CREDENTIAL_STORE):
            return replace_credential(
                storage,
                system=system,
                instance=instance,
                credential=credential,
                claimed=True,
                configured=configured,
            )
    # Read inside the claim, for the reason enrolment does: a mapping read
    # before it may be missing another process's write.
    document = _decode(storage.read_artifact(scope=APPLICATION, artifact=CREDENTIAL_STORE))
    credentials = _credentials_in(document)
    held = credentials.get(instance)
    if configured:
        row = connector_row(storage, instance=instance, purpose="a credential written")
        if row.get("system") != system:
            raise CredentialNotHeld(
                f"the row for {instance!r} in {CONNECTORS} is configured as a "
                f"{row.get('system')!r} connector, not {system!r}, so a {system} "
                f"credential will not be written for it. Nothing was written."
            )
    if held is None and configured:
        credentials[instance] = {"system": system, "credential": credential}
        document[CREDENTIALS_KEY] = credentials
        storage.write_artifact(
            _encode(document), scope=APPLICATION, artifact=CREDENTIAL_STORE
        )
        return True
    if held is None:
        raise CredentialNotHeld(
            f"{CREDENTIAL_STORE} has no entry for {instance!r}, so there is "
            f"nothing to replace. Nothing was written."
        )
    if not _sealed_under(held, system):
        raise CredentialNotHeld(
            f"the entry for {instance!r} in {CREDENTIAL_STORE} is recorded "
            f"under the system {held.get('system')!r}, not {system!r}. Nothing "
            f"was written: that is a different connector under the same "
            f"instance name."
        )
    if held.get("credential") == credential:
        return False
    credentials[instance] = {**held, "credential": credential}
    document[CREDENTIALS_KEY] = credentials
    storage.write_artifact(_encode(document), scope=APPLICATION, artifact=CREDENTIAL_STORE)
    return True


def record_user_id(
    storage: StoragePort,
    *,
    instance: str,
    user_id: str,
    claimed: bool = False,
) -> bool:
    """Set `instance`'s row `user_id` to `user_id`, and change nothing else (story 8q).

    The writer `connector sign-in` (story 8m) uses after a sign-in: the row is
    re-read at write time, the one key is set, and every other key — a
    `categories` mapping hand-added since enrolment included — is written back
    as it was read. `connector add graph` does not call this; it writes the key
    in the row it composes, in the same write as everything else.

    **It overwrites a different recorded user.** It does not compare, and no
    caller does: pm-ai trusts any authenticated Graph sign-in, since identity
    and access are Microsoft's to guard (Andrei, 2026-10-12). The row records
    whoever signed in last.

    Runs under the claim enrolment writes rows under, the sealed store's.
    `claimed` says the caller already holds it, as `replace_credential`'s does:
    a claim is never taken twice by one process (AD-5), and a caller that holds
    it and asks again gets a programming error, not a busy refusal. `claimed`
    is checked, not trusted: `ClaimNotHeld` when this thread does not hold it.

    Returns whether the file was written. A row already recording this user —
    compared ignoring case, a Graph user id being a GUID — is not rewritten, so
    a repeat sign-in changes no bytes. Refused, writing nothing, with
    `ConnectorNotConfigured` when no row exists, `MalformedSettings` when the
    row is not a JSON object, `NotAGraphRow` when its `system` is not `graph`,
    and `ValueError` for a blank or non-text `user_id`, or one
    `usable_user_id` reads as absent (not a GUID): a value the reader ignores
    is never written.
    """
    if not isinstance(user_id, str) or not user_id.strip():
        raise ValueError(
            f"no user id was given to record for {instance!r}; a blank one would "
            f"read as no recorded user. Nothing was written."
        )
    if usable_user_id({USER_ID_KEY: user_id}) is None:
        # The writer refuses what its reader ignores: a value `recorded_user_id`
        # reads as absent would record a user id no reader can use.
        raise ValueError(
            f"{user_id!r} is not a Graph user id (a GUID), so {instance!r} would "
            f"read back as having no recorded user. Nothing was written."
        )
    _assert_nameable(instance)
    if not claimed:
        with storage.exclusive(scope=APPLICATION, artifact=CREDENTIAL_STORE):
            return record_user_id(
                storage, instance=instance, user_id=user_id, claimed=True
            )
    if not storage.holds_claim(scope=APPLICATION, artifact=CREDENTIAL_STORE):
        raise ClaimNotHeld(
            f"record_user_id was told the claim on {CREDENTIAL_STORE} is held "
            f"for {instance!r}, and this thread does not hold it. That is a bug "
            f"in pm-ai; nothing was written."
        )
    name = f"{instance}.json"
    # Read inside the claim, so a key another writer set a moment ago is kept.
    row = connector_row(storage, instance=instance, purpose="its user id recorded")
    if row.get("system") != "graph":
        raise NotAGraphRow(
            f"{instance!r} is configured as a {row.get('system')!r} connector, "
            f"not a Graph one, so it records no Graph user id. Nothing was "
            f"written."
        )
    user_id = user_id.strip()
    recorded = recorded_user_id(row)
    if recorded is not None and recorded.casefold() == user_id.casefold():
        return False
    row[USER_ID_KEY] = user_id
    storage.write_artifact(
        _encode(row, strict=True), scope=APPLICATION, artifact=CONNECTORS, name=name
    )
    return True


def record_categories(
    storage: StoragePort,
    *,
    instance: str,
    shown: object,
    categories: Mapping[str, str],
    registered: Callable[[str], bool],
    claimed: bool = False,
) -> bool:
    """Set `instance`'s row `categories` to `categories`, and change nothing else (4s).

    `record_user_id`'s shape: the row is re-read at write time under the claim
    enrolment writes rows under (the sealed store's), the one key is set, and
    every other key is written back as it was read — a `user_id` a sign-in
    recorded meanwhile included. An empty `categories` removes the key.

    `shown` is the `categories` value the caller showed the person before
    asking — `CATEGORIES_ABSENT` when the row had none. When the row now holds
    anything else, the answers are about a mapping that is gone and
    `CategoriesChanged` is raised. Every project `categories` names is judged
    again with `registered`, read now: one removed meanwhile is
    `CategoryProjectUnenrolled`, naming it. Also refused, writing nothing:
    `ConnectorNotConfigured` when the row is gone, `MalformedSettings` when it
    is not a JSON object, `NotAGraphRow` when its `system` is not `graph`.

    Returns whether the file was written: a mapping equal to the one found is
    not written again.
    """
    _assert_nameable(instance)
    if not claimed:
        with storage.exclusive(scope=APPLICATION, artifact=CREDENTIAL_STORE):
            return record_categories(
                storage,
                instance=instance,
                shown=shown,
                categories=categories,
                registered=registered,
                claimed=True,
            )
    if not storage.holds_claim(scope=APPLICATION, artifact=CREDENTIAL_STORE):
        raise ClaimNotHeld(
            f"record_categories was told the claim on {CREDENTIAL_STORE} is held "
            f"for {instance!r}, and this thread does not hold it. That is a bug "
            f"in pm-ai; nothing was written."
        )
    row = connector_row(storage, instance=instance, purpose="its categories saved")
    if row.get("system") != "graph":
        raise NotAGraphRow(
            f"{instance!r} is configured as a {row.get('system')!r} connector, "
            f"not a Graph one, so it maps no Outlook categories. Nothing was "
            f"written."
        )
    found = row.get(CATEGORIES_KEY, CATEGORIES_ABSENT)
    if found != shown:
        raise CategoriesChanged(
            f"the Outlook categories of {instance!r} changed while the questions "
            f"were answered — another run or a hand edit saved them first — so "
            f"these answers were not saved. Nothing was written; run `pm-ai "
            f"connector categories {instance}` again to see the mapping as it is "
            f"now."
        )
    gone = sorted({project for project in categories.values() if not registered(project)})
    if gone:
        raise CategoryProjectUnenrolled(
            f"{', '.join(gone)} {'is' if len(gone) == 1 else 'are'} no longer "
            f"enrolled — removed from the project registry while the questions "
            f"were answered — and a Graph row mapping a category to a project no "
            f"registry knows is not built at all. Nothing was written."
        )
    wanted = dict(categories)
    if wanted == found or (not wanted and found is CATEGORIES_ABSENT):
        return False
    if wanted:
        row[CATEGORIES_KEY] = wanted
    else:
        row.pop(CATEGORIES_KEY, None)
    storage.write_artifact(
        _encode(row, strict=True),
        scope=APPLICATION,
        artifact=CONNECTORS,
        name=f"{instance}.json",
    )
    return True


def connector_row(
    storage: StoragePort, *, instance: str, purpose: str = "it read"
) -> dict[str, object]:
    """`connectors/<instance>.json` as a mapping, or the refusal naming why not.

    The one reader of a single row (stories 8q, 8m). `ConnectorNotConfigured`
    when there is no row; `MalformedSettings` when it is not JSON or not an
    object — refused rather than read as empty, because a writer handed `{}`
    would discard whatever the file holds. `purpose` finishes the refusal's
    sentence ("…cannot have {purpose}"), so each caller says what it was for.
    """
    _assert_nameable(instance)
    name = f"{instance}.json"
    raw = storage.read_artifact(scope=APPLICATION, artifact=CONNECTORS, name=name)
    if raw is None:
        raise ConnectorNotConfigured(
            f"{CONNECTORS} has no row for {instance!r}: no connector of that name "
            f"is configured, so it cannot have {purpose}. Nothing was written."
        )
    try:
        row = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as unreadable:
        raise MalformedSettings(
            f"{CONNECTORS}{name} is not valid JSON ({unreadable}), so it cannot "
            f"have {purpose} without discarding it. Nothing was written."
        ) from None
    if not isinstance(row, dict):
        raise MalformedSettings(
            f"{CONNECTORS}{name} holds a {type(row).__name__} rather than an "
            f"object, so it cannot have {purpose} without discarding it. Nothing "
            f"was written."
        )
    return row


def recorded_user_id(row: Mapping[str, object]) -> str | None:
    """The user a connector row records, or `None` when it records none (story 8q).

    `domain`'s one rule (`usable_user_id`, story 2o): a `user_id` that is
    missing, blank, whitespace, not text, or not a Graph user id the producer
    accepts is absent — the rule every reader of the key applies, story 23c's
    included. Stripped, and as the provider cased it.
    """
    return usable_user_id(row)


# ── Everything below is pure, and none of it ever sees a path ────────────────


def _default_project(instance: str) -> str:
    """The provider-side name an instance covers, before anyone edits it.

    The suffix of `<system>:<what it covers>`, which is right for the common
    case and wrong for a GitLab project inside a group. It is written into the
    connector's configuration so the wrong answer can be corrected by editing
    one plaintext line, rather than being recomputed from a name that cannot
    express the right one.
    """
    return instance.split(":", 1)[1] if ":" in instance else instance


def _assert_nameable(value: str, *, label: str = "instance name") -> None:
    """Refuse a name that cannot be one path component of `connectors/`.

    `label` names which argument is being judged. The same rules apply to the
    system and the instance, but the refusals used to talk about registry keys
    and `connectors/<instance>.json` whichever one was wrong — so
    `pm-ai connector add "git lab" alpha` complained about the instance name.

    The refusals mirror the single writer's own `_capture_name`, which validates
    the same string again at the moment it is interpolated. Duplicated on
    purpose and in this direction only: this one has to run *before* the probe
    and before either write, and the writer's has to hold for every caller
    whether or not one remembered to ask. Two checks that agree are a belt; one
    check in the wrong place is a credential orphaned on every attempt.
    """
    if not value or value != value.strip():
        raise MalformedInstanceName(
            f"the {label} {value!r} is empty or padded with whitespace. It is "
            f"a filename, a registry key and a cursor key, and two names "
            f"differing only in spacing are one name in every listing that "
            f"reports them."
        )
    if value.startswith("."):
        raise MalformedInstanceName(
            f"the {label} {value!r} starts with a dot. `.` and `..` are directories, and a "
            f"dotfile is hidden from the operator who has to find it — the "
            f"single writer omits dot-prefixed entries from a collection "
            f"listing, so this connector would be invisible to its own "
            f"duplicate check."
        )
    if len(value) > _INSTANCE_LIMIT:
        raise MalformedInstanceName(
            f"the {label} {value!r} is {len(value)} characters; the limit is "
            f"{_INSTANCE_LIMIT}. Past the filesystem's own limit the write fails "
            f"with a bare `OSError` naming neither the connector nor the limit."
        )
    illegal = sorted(set(value) - _INSTANCE_CHARACTERS)
    if illegal:
        raise MalformedInstanceName(
            f"the {label} {value!r} contains {illegal}, which it may not: it "
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
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as unreadable:
        # `MalformedSettings` (a `ValueError`) so every command meeting it
        # refuses naming the file rather than raising a traceback (story 8m).
        raise MalformedSettings(
            f"{CREDENTIAL_STORE} decrypted to something that is not JSON "
            f"({type(unreadable).__name__}). Enrolment will not replace it: "
            f"something else wrote this file, and rewriting it whole would "
            f"destroy whatever that was."
        ) from None
    if not isinstance(document, dict):
        raise MalformedSettings(
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
        raise MalformedSettings(
            f"{CREDENTIAL_STORE}'s {CREDENTIALS_KEY!r} is a "
            f"{type(held).__name__} rather than an object keyed by instance "
            f"name. Enrolment will not replace it."
        )
    unrecognised = sorted(str(k) for k, v in held.items() if not isinstance(v, dict))
    if unrecognised:
        # Refused rather than filtered. Dropping these and writing the mapping
        # back would delete them from the sealed store — the module preserves
        # unrelated *top-level* keys with care and would have destroyed sibling
        # credential entries it merely did not recognise. They are also
        # invisible to both duplicate checks while filtered, so the instance
        # would be silently overwritten rather than refused.
        raise MalformedSettings(
            f"{CREDENTIAL_STORE} holds entries under {CREDENTIALS_KEY!r} that "
            f"are not objects: {', '.join(unrecognised)}. Enrolment will not "
            f"rewrite the store while it holds something it cannot interpret, "
            f"because writing it back would drop them."
        )
    return {str(k): dict(v) for k, v in held.items()}


def _encode(document: Mapping[str, object], *, strict: bool = False) -> bytes:
    """Canonical JSON bytes. Sorted, so two runs writing the same state agree.

    `strict` also refuses `NaN` and the infinities, which `json` would write as
    tokens no JSON reader accepts — a row the next start could not read.
    """
    return (
        json.dumps(document, sort_keys=True, indent=2, allow_nan=not strict).encode("utf-8")
        + b"\n"
    )


def redacted(
    answer: str,
    credential: str,
    *,
    instead: str = "the provider accepted the credential",
) -> str:
    """`answer`, or `instead` if any eight-character run of `credential` is in it.

    `instead` defaults to the sentence for a probe that accepted; a caller
    redacting a refusal passes its own.

    A probe is an adapter, and an adapter that interpolated the token it was
    given into "gitlab accepted <token>" would put it on the operator's screen
    from a module this one cannot see. Cheap, and it turns a leak that depends
    on somebody else's string formatting into one this slice's own tests can
    assert against.
    """
    if not credential:
        return answer
    # Not just the whole string. A provider that echoed a *truncated* token —
    # "gitlab accepted glpat-not-a…", which is how most APIs identify a
    # credential back to you — passed a whole-string check and printed the
    # first half of the secret. Any run of eight characters is enough to be
    # worth not printing.
    window = 8
    candidates = {credential, credential.casefold()}
    if len(credential) > window:
        candidates |= {
            credential[index : index + window]
            for index in range(len(credential) - window + 1)
        }
        candidates |= {c.casefold() for c in list(candidates)}
    folded = answer.casefold()
    if any(candidate and candidate.casefold() in folded for candidate in candidates):
        return instead
    return answer
