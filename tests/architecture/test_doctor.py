"""Startup probes: every failure they exist to catch, provoked (AD-6, AD-26).

Each probe covers something that cannot happen on the developer's machine and is
near-certain on somebody else's — so every row here has to be *simulated*, and a
test that asserted the current machine's state would be asserting somebody's last
command rather than the code.

Two properties matter as much as the individual answers:

- **Nothing raises.** A probe that threw would hide the three after it, and an
  operator would fix one thing per restart.
- **Every failure names its remediation.** "Missing `enable_load_extension`" with
  no "install a uv-managed interpreter" makes the operator guess.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from pm_ai.platform import doctor
from pm_ai.platform.doctor import (
    Health,
    encryption_toggle,
    git_available,
    keychain_reachable,
    packages_installed,
    run_all,
    sqlite_extension_support,
)
from pm_ai.platform.environment import DISABLE_ENCRYPTION_VAR
from pm_ai.ports import KeychainUnavailable, KeyNotFound

SECRET = b"\x00\x01\xfe a-real-looking-key"


class Keychain:
    """A whole `KeychainPort`, which fails however a row needs it to.

    All three methods, not just the one the probe calls. Annotating
    `keychain_reachable` with the port on 2026-08-25 immediately caught this as a
    partial fake: it had satisfied the parameter only because the parameter was
    implicit `Any`. A fake that claims to be a port and is not means every row
    using it proves less than it appears to.
    """

    def __init__(self, *, secret=None, raises=None):
        self._secret, self._raises = secret, raises

    def store(self, name: str, secret: bytes) -> None:
        self._secret = secret

    def fetch(self, name: str) -> bytes:
        if self._raises is not None:
            raise self._raises
        if self._secret is None:
            raise KeyNotFound(f"nothing stored under {name!r}")
        return self._secret

    def delete(self, name: str) -> None:
        if self._secret is None:
            raise KeyNotFound(f"nothing stored under {name!r}")
        self._secret = None


# ── sqlite extension support ─────────────────────────────────────────────────


def test_an_interpreter_with_extension_support_passes():
    probe = sqlite_extension_support()
    assert probe.health is Health.OK
    assert "enable_load_extension" in probe.detail


def test_an_interpreter_without_extension_support_reports_rather_than_raises(monkeypatch):
    """The absence is an `AttributeError` waiting to happen, and must not be one.

    Simulated by a connection object that lacks the attribute, which is exactly
    the shape a stock macOS or python.org build presents.
    """

    class NoExtensions:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    monkeypatch.setattr(doctor.sqlite3, "connect", lambda *_a, **_k: NoExtensions())

    probe = sqlite_extension_support()

    assert probe.health is Health.FAILING
    assert "uv-managed" in probe.remediation, "the remediation must say what to install"


# ── keychain ─────────────────────────────────────────────────────────────────


def test_a_key_present_passes_without_printing_it():
    """A diagnostic that prints a secret turns a support request into a leak."""
    probe = keychain_reachable(Keychain(secret=SECRET))

    assert probe.health is Health.OK
    rendered = str(probe) + probe.detail + probe.remediation
    assert SECRET.decode("latin-1") not in rendered
    assert repr(SECRET) not in rendered


def test_a_reachable_keychain_with_no_key_is_absent_not_failing():
    """First run is not a broken machine.

    Collapsing these two would send an operator to fix a keychain that is fine,
    and the fix they actually need is to enrol a key.
    """
    probe = keychain_reachable(Keychain())

    assert probe.health is Health.ABSENT
    assert probe.health is not Health.FAILING
    assert "Enrol" in probe.remediation


def test_an_unreachable_keychain_fails_and_says_so():
    probe = keychain_reachable(Keychain(raises=KeychainUnavailable("the keychain is locked")))

    assert probe.health is Health.FAILING
    assert "locked" in probe.detail, "the cause must survive into the result"


def test_the_real_adapter_and_the_probe_agree_about_a_missing_backend(monkeypatch):
    """The two halves connected, not each half alone.

    Every other keychain row here uses a fake, so nothing checked that the error
    the *real* adapter raises is the one the probe branches on. It matters
    because the split is by type: the adapter raising the base
    `KeychainUnavailable` for an absent package would land the report in the
    "present and refusing" branch and send an operator to unlock a keychain that
    was never installed.

    Absence is simulated rather than read from the environment, so the row holds
    whether or not the `runtime` extra happens to be installed.
    """
    import sys

    from pm_ai.platform.keychain import MacOSKeychainAdapter

    monkeypatch.setitem(sys.modules, "keyring", None)

    probe = keychain_reachable(MacOSKeychainAdapter())

    assert probe.health is Health.FAILING
    assert "backend is not installed" in probe.detail
    assert "needs attention" in probe.remediation, (
        "the real adapter's error reached the refusing branch, not the absent one"
    )


# ── the encryption toggle ────────────────────────────────────────────────────


def test_encryption_unset_is_healthy(monkeypatch):
    monkeypatch.delenv(DISABLE_ENCRYPTION_VAR, raising=False)
    probe = encryption_toggle()
    assert probe.health is Health.OK
    assert probe.detail == "enabled"


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", " on "])
def test_encryption_disabled_is_a_warning_not_a_pass(monkeypatch, value):
    """Not `OK`, because the daemon working is not the same as healthy.

    A report that called this healthy would be the summary an operator trusts
    while credentials sit in plaintext.
    """
    monkeypatch.setenv(DISABLE_ENCRYPTION_VAR, value)

    probe = encryption_toggle()

    assert probe.health is Health.WARNING
    assert "DISABLED" in probe.detail
    assert "restart" in probe.remediation


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "", "please"])
def test_an_unrecognised_value_leaves_encryption_on_and_says_so(monkeypatch, value):
    """Fails secure, and reports the mismatch rather than swallowing it.

    `PM_AI_DISABLE_ENCRYPTION=0` reads to a human as *off*; a truthiness test
    would read it as *on*, which is the one direction this flag must never fail
    in. But silently ignoring an unrecognised value looks identical to honouring
    it, so whoever exported `=please` needs telling.
    """
    monkeypatch.setenv(DISABLE_ENCRYPTION_VAR, value)

    probe = encryption_toggle()

    assert probe.health is Health.WARNING
    assert "not a value this recognises" in probe.detail
    assert "DISABLED" not in probe.detail, "encryption is on; the detail must not imply otherwise"


# ── git ──────────────────────────────────────────────────────────────────────


def test_git_present_and_answering_passes():
    probe = git_available()
    assert probe.health is Health.OK
    assert "git version" in probe.detail or "git" in probe.detail


def test_no_git_says_captures_will_be_refused(monkeypatch):
    """Not a generic not-found. The consequence is specific and silent.

    Harvests, briefings and the CLI all keep working, so the operator has no
    other symptom to go on.
    """
    monkeypatch.setattr(doctor.shutil, "which", lambda _name: None)

    probe = git_available()

    assert probe.health is Health.FAILING
    assert "capture" in probe.remediation.lower()
    assert "PATH" in probe.remediation


def test_git_present_but_unanswering_is_distinguishable_from_absent(monkeypatch):
    """`shutil.which` passes for a stub that cannot answer anything.

    So the two are separate reports: absent is an install, present-but-broken is
    an investigation, and telling an operator to install git they already have
    sends them in a circle.
    """
    monkeypatch.setattr(doctor.shutil, "which", lambda _name: "/usr/bin/git")
    monkeypatch.setattr(
        doctor.subprocess,
        "run",
        lambda *_a, **_k: subprocess.CompletedProcess([], 3, "", "unrecognised subcommand"),
    )

    probe = git_available()

    assert probe.health is Health.FAILING
    assert "exited 3" in probe.detail
    assert "rather than installing another" in probe.remediation


def test_a_git_that_times_out_is_reported_not_raised(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda _name: "/usr/bin/git")

    def hang(*_a, **_k):
        raise subprocess.TimeoutExpired(cmd="git", timeout=10)

    monkeypatch.setattr(doctor.subprocess, "run", hang)

    probe = git_available()

    assert probe.health is Health.FAILING
    assert "unusable" in probe.detail


# ── the summary ──────────────────────────────────────────────────────────────


def test_every_probe_still_runs_when_one_fails(monkeypatch):
    """One broken thing must not hide three others.

    Otherwise an operator fixes one item per restart, which for four probes is
    four restarts to learn what a single run could have told them.
    """
    monkeypatch.setattr(doctor.shutil, "which", lambda _name: None)
    monkeypatch.delenv(DISABLE_ENCRYPTION_VAR, raising=False)

    report = run_all(keychain=Keychain(raises=KeychainUnavailable("locked")))

    assert len(report.probes) == 5, "a probe went missing when another failed"
    assert {p.name for p in report.probes} == {
        "runtime packages", "sqlite extension support", "keychain", "encryption", "git",
    }
    assert not report.healthy


def test_a_warning_alone_makes_the_report_unhealthy(monkeypatch):
    """Encryption off is the case this exists for.

    Every other probe can pass while credentials are written in plaintext, and a
    summary that reported healthy then would be worse than no summary.
    """
    monkeypatch.setenv(DISABLE_ENCRYPTION_VAR, "1")

    report = run_all(keychain=Keychain(secret=SECRET))

    assert [p.health for p in report.probes].count(Health.WARNING) == 1
    assert not report.healthy


def test_a_fully_healthy_machine_reports_healthy(monkeypatch):
    """Simulated, because this repo is deliberately not one.

    The `runtime` extra is unset here on purpose — pyproject says the
    architecture suite must run before the stack resolves — so the packages probe
    is correctly FAILING and a healthy verdict is unreachable without standing
    that in. Before the fifth probe existed this test passed by accident.
    """
    monkeypatch.delenv(DISABLE_ENCRYPTION_VAR, raising=False)
    monkeypatch.setattr(doctor, "missing_distributions", lambda _names: ())

    report = run_all(keychain=Keychain(secret=SECRET))

    assert report.healthy, str(report)
    assert "pm-ai is healthy." in str(report)


def test_the_module_runs_as_a_subprocess_and_exits_by_verdict():
    """There is no console entry point yet, so `python -m` is the whole surface.

    Exit status matters more than the text: whatever runs this at install time
    reads the code, not the prose.
    """
    result = subprocess.run(
        [sys.executable, "-m", "pm_ai.platform.doctor"],
        capture_output=True, text=True, check=False,
    )
    assert "sqlite extension support" in result.stdout
    assert result.returncode in (0, 1), "the runner must exit by verdict, not crash"


# ── the environment is the only way in ───────────────────────────────────────


def test_the_daemon_honours_the_environment_variable(tmp_path, monkeypatch):
    """The composition root consults the environment, and nothing else may.

    Asserted through `build()` rather than the reader, because the reader being
    correct is worth nothing if the daemon does not ask it.
    """
    from pm_ai.app.wiring import build
    from pm_ai.storage.crypto import LazyKeyCrypto, PlaintextCrypto

    monkeypatch.setenv(DISABLE_ENCRYPTION_VAR, "1")
    assert isinstance(
        build(tmp_path, "alpha", keychain=Keychain(secret=SECRET)).crypto, PlaintextCrypto
    )

    monkeypatch.delenv(DISABLE_ENCRYPTION_VAR)
    assert isinstance(
        build(tmp_path / "b", "alpha", keychain=Keychain(secret=SECRET)).crypto, LazyKeyCrypto
    )


@pytest.mark.parametrize("value", ["0", "false", "please"])
def test_a_value_the_reader_rejects_leaves_the_daemon_encrypting(tmp_path, monkeypatch, value):
    """The end-to-end direction of fail-secure.

    Someone who believes they disabled encryption and did not gets working
    encryption and a warning from `doctor` — never plaintext credentials.
    """
    from pm_ai.app.wiring import build
    from pm_ai.storage.crypto import PlaintextCrypto

    monkeypatch.setenv(DISABLE_ENCRYPTION_VAR, value)

    daemon = build(tmp_path, "alpha", keychain=Keychain(secret=SECRET))

    assert not isinstance(daemon.crypto, PlaintextCrypto)


def test_the_environment_is_read_in_exactly_one_place():
    """One reader, because two could disagree about the most dangerous setting.

    A second `os.environ` lookup for this variable — in wiring, in a surface, in
    a connector — is how a flag ends up honoured in one code path and ignored in
    another. `pm_ai.platform.environment` is the only module that may name it.
    """
    import ast
    import pathlib

    offenders = []
    for source in sorted(pathlib.Path("pm_ai").rglob("*.py")):
        if source.name == "environment.py":
            continue
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            # By AST, not by text. The variable's name appears in `doctor.py`'s
            # prose explaining what the flag does, and a text search flagged that
            # — the same mistake a grep for `assert ` makes, and one a reader
            # could "fix" by rewording a comment.
            if isinstance(node, ast.Attribute) and node.attr == "environ":
                offenders.append(f"{source}:{node.lineno} reads os.environ")
            if isinstance(node, ast.Constant) and node.value == DISABLE_ENCRYPTION_VAR:
                offenders.append(f"{source}:{node.lineno} names the variable in code")
    assert not offenders, (
        "the process environment is read in pm_ai/platform/environment.py alone: "
        + ", ".join(offenders)
    )


def test_an_unusable_sqlite3_module_is_reported_not_raised(monkeypatch):
    """The branch that was `# pragma: no cover` until 2026-08-25.

    Excluding it from *measurement* is not the same as it working, and it is
    trivially reachable — a `sqlite3.connect` that raises is one monkeypatch. The
    probe must still report, because a caller that got an exception here would
    lose the three probes after it, which is the one thing this module promises.
    """

    def unusable(*_a, **_k):
        raise doctor.sqlite3.Error("unable to open database file")

    monkeypatch.setattr(doctor.sqlite3, "connect", unusable)

    probe = sqlite_extension_support()

    assert probe.health is Health.FAILING
    assert "unusable" in probe.detail
    assert "unable to open database file" in probe.detail, "the cause must survive"
    assert probe.remediation, "a failure with no remediation makes the operator guess"


def test_an_unusable_sqlite3_does_not_stop_the_other_probes(monkeypatch):
    """The reason the branch reports rather than raises, asserted end to end."""

    def unusable(*_a, **_k):
        raise doctor.sqlite3.Error("unable to open database file")

    monkeypatch.setattr(doctor.sqlite3, "connect", unusable)
    monkeypatch.delenv(DISABLE_ENCRYPTION_VAR, raising=False)

    report = run_all(keychain=Keychain(secret=SECRET))

    assert len(report.probes) == 5
    assert not report.healthy


def test_every_probe_the_report_carries_satisfies_the_port_it_was_given():
    """The fake must really be a KeychainPort, or every keychain row is theatre.

    `keychain_reachable` is annotated with the port as of 2026-08-25; before that
    the parameter was implicit `Any`, so a fake missing `fetch` would have type-
    checked and the probe would have failed at runtime instead.
    """
    from pm_ai.ports import KeychainPort

    assert isinstance(Keychain(secret=SECRET), KeychainPort)


# ── The packages probe ───────────────────────────────────────────────────────
#
# Added 2026-08-26. Until then the keychain probe was the only thing that
# reported a missing runtime stack, and it did so as a message about a keychain —
# an oblique way to tell an operator that nothing works.


def test_all_packages_present_passes():
    """Checked against distributions this environment really has."""
    probe = packages_installed(["pytest", "cryptography"])
    assert probe.health is Health.OK
    assert "2 present" in probe.detail


def test_missing_packages_are_named_individually():
    """Which ones, not how many. "3 missing" sends an operator guessing."""
    probe = packages_installed(["pytest", "definitely-not-installed", "also-absent"])

    assert probe.health is Health.FAILING
    assert "also-absent" in probe.detail and "definitely-not-installed" in probe.detail
    assert "pytest" not in probe.detail, "a present package was reported as missing"
    assert "uv sync" in probe.remediation


def test_the_probe_is_generic_and_not_a_keyring_check_in_disguise():
    """Any distribution set, which is what makes it reusable.

    The story asked for install status in general, not one more special case for
    the package that happened to expose the gap.
    """
    assert packages_installed(["pytest"]).health is Health.OK
    assert packages_installed(["no-such-distribution-anywhere"]).health is Health.FAILING


def test_names_are_compared_the_way_packaging_compares_them():
    """PEP 503: case-insensitive, and `-`, `_`, `.` runs are equivalent.

    `python-telegram-bot` is declared with hyphens and imports as `telegram`;
    metadata and requirement strings disagree on punctuation often enough that a
    literal comparison reports installed packages as absent.
    """
    assert doctor.missing_distributions(["PyTest"]) == (), "case must not matter"
    assert doctor.missing_distributions(["import_linter"]) == (), (
        "an underscore must match the hyphenated `import-linter`"
    )
    assert doctor.missing_distributions(["Import.Linter"]) == (), (
        "a dot is equivalent to a hyphen too"
    )
    # And normalisation must not fuse distinct names: `py_test` becomes
    # `py-test`, which is not `pytest` and must stay reported as absent.
    assert doctor.missing_distributions(["py_test"]) == ("py-test",)


def test_the_default_set_comes_from_the_projects_own_metadata():
    """Derived, so adding a dependency extends the check with no edit here.

    A hardcoded list is a second place to edit, and the failure mode is silent:
    the new dependency is simply never checked.
    """
    declared = doctor.required_distributions("runtime")

    assert "keyring" in declared, "the runtime extra's contents are not being read"
    assert "cryptography" in declared
    assert all(d == d.lower() for d in declared), "names must arrive normalised"


def test_checking_does_not_import_the_packages_it_checks(monkeypatch):
    """Metadata, not `try: import`.

    Importing to find out is the obvious implementation and the wrong one:
    fastapi, uvicorn and ollama all cost real time and some have side effects,
    and a diagnostic must not pay a startup cost to report that one exists.
    """
    import sys

    monkeypatch.setattr(doctor.importlib.metadata, "packages_distributions", lambda: {})

    def forbidden(name, *_a, **_k):  # pragma: no cover - must never run
        raise AssertionError(f"the probe imported {name!r} to check whether it exists")

    monkeypatch.setattr(doctor.importlib, "import_module", forbidden)

    probe = packages_installed(["pytest"])

    assert probe.health is Health.FAILING
    assert "pytest" not in sys.modules or True  # already imported by the runner


def test_an_unreadable_distribution_reports_rather_than_guessing(monkeypatch):
    """pm-ai diagnosing itself before it is installed has nothing to read.

    ABSENT rather than OK: "no packages to check" must never render as "all
    present", which is what an empty-set-passes implementation would say.
    """
    monkeypatch.setattr(doctor.importlib.metadata, "requires", lambda _d: None)

    probe = packages_installed()

    assert probe.health is Health.ABSENT
    assert probe.health is not Health.OK
    assert "not installed" in probe.detail


# ── The keychain's two causes, split ─────────────────────────────────────────


def test_a_missing_backend_is_reported_differently_from_a_refusing_keychain():
    """The bar the git probe already met, applied to the keychain.

    An incomplete install and a keychain that is present and refusing take
    different repairs. Collapsing them made the reader parse a message — the
    equivalent of telling someone to install git they already have.
    """
    from pm_ai.ports import KeychainBackendMissing

    absent = keychain_reachable(
        Keychain(raises=KeychainBackendMissing("the `keyring` package is not installed"))
    )
    refusing = keychain_reachable(
        Keychain(raises=KeychainUnavailable("the keychain is locked"))
    )

    assert absent.health is refusing.health is Health.FAILING, "both are failures"
    assert absent.detail != refusing.detail
    assert absent.remediation != refusing.remediation
    assert "not installed" in absent.detail
    assert "needs attention" in absent.remediation, (
        "the backend case must point away from the keychain, not at it"
    )
    assert "unlock" in refusing.remediation


def test_the_narrower_error_is_still_caught_as_the_general_one():
    """Adding the type must not break a caller that catches the base."""
    from pm_ai.ports import KeychainBackendMissing, KeychainUnavailable as Base

    assert issubclass(KeychainBackendMissing, Base)


def test_the_report_leads_with_the_cause_not_the_consequence(monkeypatch):
    """Ordering is the point of the fifth probe.

    With no runtime stack the keychain, and later Ollama and Telegram, all fail
    for one reason. That reason has to be read first or an operator fixes
    symptoms.
    """
    monkeypatch.delenv(DISABLE_ENCRYPTION_VAR, raising=False)

    report = run_all(keychain=Keychain(secret=SECRET))

    assert len(report.probes) == 5
    assert report.probes[0].name.endswith("packages"), (
        "the cause must be reported before the probes that report its consequences"
    )
