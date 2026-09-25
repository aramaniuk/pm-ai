# Adversarial review: AD-11 selection order (2026-09-25)

**Subject:** the uncommitted AD-11 revision in `ARCHITECTURE-SPINE.md` (selection order for a CLI command, one spelling `--scope project:<id>`, "the folder decides which project a command acts in, never a command's default target") and the three matching memlog entries.

**Lens:** build two units one level down that each obey every AD to the letter and still build incompatibly. Only holes the changed text opens or leaves open are counted.

**Read against:** AD-4, AD-7, AD-10, AD-30, AD-38 in the spine. Code: `pm_ai/app/entry.py` (`main`, `_compose`, `_acting_project`, `_selection_probe`, the refusal texts), `pm_ai/app/wiring.py` (`Daemon.scope`, `Daemon.__post_init__`, `_enrolment_scope`), `pm_ai/app/pipelines.py` (`run_dashboard`, `_persist_by_scope`, `run_transcript_ingestion`), `pm_ai/surfaces/cli/dispatch.py` (`_scope`, `_dashboard`, `_options`, `require_daemon`/`require_selection`, the command table).

## Verdict

**Revise before finalizing.** The order itself is sound and the rejected alternatives are argued well. But the new text gives one option, `--scope`, two jobs: it picks the acting project, and it is also a command's target. The text never says how those two jobs relate. It also never says who reads the option before composition runs. Six pairs of units below obey the new text and still build incompatible behaviour. Each can be closed with a sentence or two in AD-11; none needs a new AD.

## Findings

### F1 (high): the "named project" has no owner before composition, so two parsers can each claim `--scope`

**What the code does.** `entry.main` calls `_compose(keychain)` before `dispatch(arguments, ...)`, and `_compose` calls `_acting_project(projects)` with no argv at all. `--scope` is parsed only in `dispatch._options` and `dispatch._scope`, after the daemon exists. So step 1 of the new order ("a project named on its command line") cannot be reached where the acting project is decided today.

**Two compliant units.**
- **Unit A** (entry): pre-scans `argv` for `--scope project:<id>` inside `_compose`, with its own small parser, and passes the id to `_acting_project`. It obeys "one spelling", because the spelling is the same.
- **Unit B** (dispatch): parses first, then asks the composition root for a daemon bound to the named project, which makes composition lazy.

Both follow AD-11 and AD-30. They disagree on `--scope=project:x` versus `--scope project:x`, on `--scope --bogus`, and on a repeated `--scope`. `_options` refuses each of those with exit 2; a pre-scan would silently take a value. They also disagree on whether a usage error can leave behind a daemon that acted in the folder's project. "One spelling" rules out a second flag. It does not rule out a second parser, which is how the two precedence stories come back.

**Close with:** "The command line is parsed once, by the CLI's dispatcher. The acting project is selected after parsing, from the parsed scope, and never by a scan of raw argv. Composition that needs the acting project runs after that parse." Or name the one function that owns both steps.

### F2 (high): whether the named project becomes `Daemon.scope`, the write target and the guard's `into`, is unstated

**What the code does.** `Daemon.scope` is where a command writes and what AD-38's guard checks against. `wiring.build` says so, and so do `_persist_by_scope` (an empty harvest writes to `daemon.scope`) and `run_transcript_ingestion` (`assert_citation_legal(cited=meeting.scope, into=daemon.scope)`). `run_dashboard` does not use `daemon.scope` at all: it takes the render scope as its own argument. So for the one shipped command that accepts `--scope`, the target and the acting project are already separate values.

**Two compliant units.** Take a later write command, say `harvest --scope project:beta`, run from inside `alpha`'s folder.
- **Unit A** reads "a command acts in the first of: (1) a project named on its command line". It builds the daemon with `scope=project:beta`. The empty-harvest stamp goes to beta's log, and the guard checks `into=beta`.
- **Unit B** reads `--scope` the way `dashboard` uses it today, as the target. It passes `beta` to the pipeline and leaves `daemon.scope = alpha`, the folder's project. The empty-harvest stamp goes to alpha's log, and a transcript cited "into" alpha passes a guard that should have checked beta.

Both are legal under the text. They write to different trees, and B checks AD-38's guard against the wrong scope. That is the "two owners of one entity" case: the acting project has two sources in one process.

**Close with:** "When a command names a project, that project is `Daemon.scope`: the scope its writes default to and the `into` of every citation check it makes. A command never holds a target scope and an acting project that disagree." Then decide separately what a non-project `--scope` does to the acting project (F3).

### F3 (high): `--scope personal` (or no `--scope`) still requires a project, which contradicts "wherever it runs"

**What the code does.** `_dashboard` calls `context.require_daemon()`, and that calls `require_selection()` first. With two enrolled projects and a working directory outside both, `pm-ai dashboard` (personal by default) is refused today. The new order does not change this: `personal` names no project, so selection falls to step 2, then step 3, then refusal. The same text says the personal dashboard renders "wherever it runs". Those two statements are contradictory on exactly the machine the revision was written for.

**Two compliant units.**
- **Unit A** follows the order literally, so the personal dashboard is refused outside every folder.
- **Unit B** follows "wherever it runs". It exempts personal-target commands from selection, perhaps by building a daemon with no acting project or with `scope=personal`. That changes `Daemon.scope`'s type contract, because `Daemon.__post_init__` only checks project scopes.

`goal set` has the same problem. It writes only `~/.manager-ai/strategic_goals.md` and is still refused outside every folder. The new principle ("the folder decides which project a command acts in") implies that a command acting in no project should not be refused for a missing project, but nothing says so.

**Close with:** a sentence classing commands. Either "Commands whose target is personal (or application) scope do not run project selection and are never refused for want of one", with `Daemon.scope` for them stated, or "every command selects a project, and 'wherever it runs' means inside a folder or with one enrolled project". The first matches the decision's intent. `people:<id>` and `application` also parse in `_scope` and need the same sentence: they are not "a named project".

### F4 (medium): a named project that is not enrolled has no defined outcome

**Two compliant units,** for `--scope project:ghost` with `alpha` and `beta` enrolled:
- **Unit A** treats "named" as only counting when the name is enrolled. `ghost` is not, so selection falls through to the folder and acts in `alpha`. The flag silently loses to the folder, which is the failure the memlog entry says this rule prevents.
- **Unit B** refuses at selection with exit 3, naming the enrolled projects.
- **Unit C,** today's code path: the id parses as a valid `DataScope`, a daemon is built for the folder's project, and `ghost` fails later as a `ScopeResolutionError` from the resolver, after any reads before the write.

There is a quieter variant. `_acting_project` returns early when `len(projects) == 1`. A unit that adds step 1 after that early return lets `--scope project:ghost` bind to the only project. A unit that adds step 1 before it refuses.

AD-11's "no auto-discovery" also needs restating here. Naming an unenrolled directory's id must never enrol it or watch it. `Daemon.__post_init__` refuses an acting project it does not watch, but that is a code check, not an architecture rule.

**Close with:** "A named project must be enrolled. Otherwise the command is refused (a refusal, exit 3, not a usage error), naming each enrolled project, and selection never falls through to the folder or to the only enrolled project."

### F5 (medium): "lifts the outside-every-folder refusal" leaves the other two refusals ambiguous

4l defines three refusals: outside every folder, one directory under two ids, and an unreadable working directory. The new text says naming a project lifts only the first.

- **Unit A** checks step 1 first and never reads `Path.cwd()`. A named project works from a deleted directory and from inside a duplicate-id folder.
- **Unit B** reads the text literally. It still refuses on an unreadable cwd or a duplicate-id folder even when the operator named the project.

The same command line then works on one build and is refused on another.

**Close with:** "A named project is decided before the working directory is read. None of the three folder refusals applies to a command that names one."

There are also two things to fix in the code the text now makes stale, as notes for the implementing story rather than for the spine:
- `_no_selection_is_a_fault` still says "There is no way to name a project on the command line yet". The refusal should give the spelling.
- `_selection_probe` always says "chosen by the working directory", which is already false for the single-project case and will be false for a named one. The spine's "doctor … says which project this invocation binds to" should say it names the reason too: named, folder, or only enrolled.

### F6 (medium): which commands accept `--scope project:<id>` is only in the memlog, and the refusal would advertise a flag most commands reject

The spine says "one spelling". The memlog adds "A command that acts in a project accepts that form", but the rendered spine does not carry that sentence. Today only `dashboard` declares the option. `_options` refuses `--scope` on every other command with exit 2 ("has no `--scope` option").

- **Unit A** updates the outside-every-folder refusal to say "or name one with `--scope project:<id>`" everywhere.
- **Unit B** adds `--scope` to `connector add` and uses it to file the enrolment, which `_enrolment_scope` currently refuses to guess between projects.
- **Unit C** does neither.

So on `connector add` an operator can be told to use a flag the command then rejects as a usage error. `connector add` can also end up with two sources for its filing scope: `_enrolment_scope`'s built-in lookup, and a named project that disagrees with it.

**Close with:** carry the memlog sentence into the spine. State that a command which acts in a project accepts `--scope project:<id>`, and that the refusal text names the spelling only for commands that accept it. Also say which of the two wins for `connector add` when a row replaces a built-in of one project and the command line names another. The simplest rule is to refuse on disagreement, because this is filing a credential rather than rendering a view.

### F7 (medium): Telegram keeps the "configured default" the CLI just rejected, and nothing says where it lives or whether the CLI reads it

The revised rule line still says Telegram "requires explicit project selection or a configured default". The new decision rejects "folder then a configured default" for the CLI because "a forgotten cd silently acts in the default". AD-7 says both surfaces reach identical functionality through the same core services.

- **Unit A** (Telegram) stores the default as a `config.toml` key such as `default_project`.
- **Unit B** (CLI), seeing that key under the one registry and config (AD-11: "operates on the one registry through the daemon"), reads it as the step before refusal. Nothing in the spine forbids that, and it brings back the rejected alternative.
- **Unit C** keeps the default Telegram-only, so the same "render beta's dashboard" request succeeds on Telegram and is refused on the CLI from the same place. AD-7 is broken in spirit.

The spine also does not say whether step 3 (only enrolled project) applies to Telegram, or whether Telegram's explicit selection resolves through the same selection function.

**Close with:** state Telegram's order in the same shape as the CLI's: (1) explicit selection in the message or conversation, (2) configured default, (3) the only enrolled project, otherwise refuse. Name where the default is stored. Say it is read by the Telegram surface only and never by CLI selection. Say both orders run through one selection function that takes a "folder" input and a "default" input, so a third surface cannot write a third order. Give one sentence on why a silent default is acceptable on Telegram and not on the CLI (no working directory to forget). Or drop the default for Telegram as well and require explicit selection, which is the consistent reading of the rejection.

## Checked and not a hole

- **Named project against the innermost-folder rule.** Step 1 comes before step 2, so nesting is irrelevant once a project is named.
- **AD-10 watch set.** `_compose` already watches every enrolled project, and `Daemon.__post_init__` refuses an acting project it does not watch. A named, enrolled project is always watched, so F2's rebinding cannot write outside the watch set.
- **AD-4 and the personal dashboard.** `run_dashboard` routes the personal branch through `render_dashboard` and the project branch through `render_project_dashboard`, so which daemon is acting cannot mix goals into a project render. The "never a command's default target" sentence does not weaken AD-4.

## Out of scope (existing before this change, noted only)

- The long-running daemon (AD-7) has no working directory and no command line, and still needs one `Daemon.scope` for the empty-harvest stamp. The new order is "for a CLI command" only, which leaves the background process's acting project where it was.
