# Agent guide for Chiikawa

## Purpose and scope

This file guides coding agents working in this repository. Apply it to the CLI,
library, tests, demos, and release tooling. Follow explicit user instructions and
any higher-priority execution restrictions. More specific directory guidance
should be read before editing that directory.

Chiikawa is a small, observable, resumable coding-agent harness. Its core is
Python 3.10+ with **no third-party runtime dependencies**. It supports Microsoft
Foundry and OpenRouter, project tools, approval policies, durable sessions,
context compaction, bounded child agents, concurrent fleets, and optional Docker
isolation. Supported host environments are macOS, Linux, and WSL; POSIX primitives
such as `fcntl`, process groups, and terminal control are intentional dependencies.

Keep the implementation understandable and composable. Do not replace the small
standard-library architecture with an agent framework or provider SDK merely to
simplify one feature. Distinguish runtime dependencies from optional build tools,
Docker, and dependencies used by individual demo products.

## Start each task with current evidence

1. Read the user's request and inspect `git status --short`. Preserve existing
   edits and untracked work; do not reset or clean the repository to get a tidy tree.
2. Read the relevant implementation and nearby tests. Use `rg` and `rg --files`
   to locate code; avoid sweeping searches that print credentials or session logs.
3. Check [README.md](README.md) for behavior and [INSTALL.md](INSTALL.md) for
   installation and release instructions. Treat the current code, tests, and
   [CI workflow](.github/workflows/release.yml) as evidence of what exists now.
4. Identify the invariants and verification scope before changing code. Historical
   `docs/day*-verification.md` reports are evidence about past runs, not proof
   that the current worktree passes or that a feature remains unchanged.
5. Implement the requested outcome, verify it, and report concrete results and
   remaining limitations. Do not claim success from an unexecuted plan or an
   earlier agent's test output.

Do not create commits, push branches, publish releases, or run credentialed demos
solely because those operations appear in this guide. Follow the scope already
authorized by the user. Routine local edits and relevant verification should
proceed without repeated confirmation.

## Repository map

| Location | Responsibility |
| --- | --- |
| [chiikawa/__init__.py](chiikawa/__init__.py) | Public exports: `Harness`, `Policy`, `Tool`, `tool`, `run_fleet`. |
| [chiikawa/cli.py](chiikawa/cli.py), [chiikawa/__main__.py](chiikawa/__main__.py) | Argument parsing, entry points, CLI policy defaults, headless and interactive execution. |
| [chiikawa/commands.py](chiikawa/commands.py) | Local slash commands, candidate discovery, and configuration transitions. |
| [chiikawa/prompt.py](chiikawa/prompt.py) | POSIX line editor, completion, history, bracketed paste, and terminal restoration. |
| [chiikawa/terminal.py](chiikawa/terminal.py) | Activity display, bounded previews, diffs, progress, and terminal-safe text. |
| [chiikawa/transcript.py](chiikawa/transcript.py) | Compact activity, public history replay, keyboard ownership, and live footer. |
| [chiikawa/harness.py](chiikawa/harness.py) | Composition, conversation ownership, durable callbacks, resume, isolation switching, and child configuration. |
| [chiikawa/loop.py](chiikawa/loop.py) | Sequential model/tool loop, policy hook, events, error results, and final wrap-up. |
| [chiikawa/providers.py](chiikawa/providers.py) | Provider selection. |
| [chiikawa/provider.py](chiikawa/provider.py) | Foundry Responses API adapter, request transport, retries, and native replay. |
| [chiikawa/openrouter.py](chiikawa/openrouter.py) | OpenRouter Chat Completions adapter and provider-specific state. |
| [chiikawa/tools.py](chiikawa/tools.py) | Tool schemas, six core tools, rooted paths, process cleanup, and `ToolResult` details. |
| [chiikawa/security.py](chiikawa/security.py) | Tool approval policy and shell-command denylist. |
| [chiikawa/runtime.py](chiikawa/runtime.py) | Jail and Docker runtimes, protected mounts, JSON transport, readiness, image setup, and cleanup. |
| [chiikawa/sandbox_worker.py](chiikawa/sandbox_worker.py), [chiikawa/Sandbox.Dockerfile](chiikawa/Sandbox.Dockerfile) | Trusted single-request container worker and bundled image definition. |
| [chiikawa/session.py](chiikawa/session.py) | Locked JSONL journals, durable writes, latest-session selection, and interruption repair. |
| [chiikawa/context.py](chiikawa/context.py) | Token estimates, compaction trigger, and model-facing history replacement. |
| [chiikawa/system_policy.py](chiikawa/system_policy.py), [chiikawa/SYSTEM_PROMPT.md](chiikawa/SYSTEM_PROMPT.md) | Installed core policy, fingerprint, trusted runtime facts, and separate project context. |
| [chiikawa/memory.py](chiikawa/memory.py), [chiikawa/skills.py](chiikawa/skills.py) | `CHIIKAWA.md` memory and workspace `skills/*/SKILL.md` discovery/loading. |
| [chiikawa/subagent.py](chiikawa/subagent.py), [chiikawa/fleet.py](chiikawa/fleet.py) | Bounded delegation and concurrent independent jobs. |
| [tests/](tests/) | Offline regression tests, real terminal tests, and opt-in Docker integration tests. |
| [demos/](demos/), [docs/](docs/) | Course examples, credentialed verification scripts, and historical engineering notes. |
| [products/](products/) | Separate example products; inspect their own instructions and tooling before changing them. |
| [scripts/](scripts/) | Portable release builder, distribution verifier, and installer. |
| [pyproject.toml](pyproject.toml), [MANIFEST.in](MANIFEST.in) | Package metadata, Python compatibility, and source/package inclusion rules. |

`dist/`, `build/`, and `*.egg-info/` are generated packaging output. `.chiikawa/`
contains runtime session data. `scratch/` and `output/` may contain prior user or
demo work; do not assume they are disposable just because of their names.

## Local commands and configuration

Run commands below from the repository root. The source checkout can run without
installation or a virtual environment. A virtual environment is useful when
installing build tools.

```sh
python3 --version
python3 -m chiikawa --help
python3 -m chiikawa --version
python3 -m unittest discover -s tests -v
```

The test suite uses `unittest`, temporary workspaces, and mocked provider calls.
Ordinary regression tests do not require provider credentials or Docker. Docker
tests are skipped unless `CHIIKAWA_TEST_DOCKER=1` is set.

Interactive startup and local slash commands do not require API credentials:

```sh
python3 -m chiikawa -d ./scratch
```

Submitting a model task does require credentials and can incur API charges. For
an authorized live test, use an appropriate disposable project and explicit
policy, for example:

```sh
python3 -m chiikawa -d ./scratch --mode safe -p "Inspect this project and summarize it"
```

Configuration is read from the environment; `.env` files are not loaded by the
CLI. Never print a real key to demonstrate configuration. Do not source an
unreviewed project `.env` as shell code.

| Setting | Foundry | OpenRouter |
| --- | --- | --- |
| Provider name | `foundry` (default) | `openrouter` |
| Default model | `gpt-6-astra` | `openai/gpt-5.4` |
| Model environment variable | `CHIIKAWA_MODEL` | `OPENROUTER_MODEL` |
| API key | `CHIIKAWA_API_KEY`, then `AZURE_OPENAI_API_KEY` | `OPENROUTER_API_KEY` only |
| Endpoint | `AZURE_OPENAI_ENDPOINT` | Adapter's OpenRouter endpoint |
| Default output cap | 65,536 tokens | 16,384 tokens |

`--provider` overrides `CHIIKAWA_PROVIDER`; `-m` overrides the selected provider's
model environment variable. `--max-output-tokens` controls response output,
including compaction and children. The default Harness/CLI turn limit is 120.
The default compaction threshold is an estimated 600,000 history tokens, not a
provider context-window guarantee or a hard ceiling.

The CLI defaults to `safe` policy interactively and `yolo` for `-p` tasks.
`Harness(...)` defaults to `yolo`; `Policy()` itself defaults to `safe`. Preserve
these distinct entry-point contracts unless the task explicitly changes them.

## Architecture and behavioral invariants

### Tools, loop, and policies

- Keep the public `Tool` name, schema, and callable together. The `@tool`
  decorator describes string arguments; function defaults make them optional.
- The six core tools are `read_file`, `write_file`, `edit_file`, `bash`,
  `list_files`, and `grep`. Preserve their output bounds, exact-edit uniqueness
  checks, canonical-path restrictions, and symlink escape rejection.
- Preserve `ToolResult` as string-compatible model output with separate details
  for UI diffs and exit codes. A failed or blocked change must not display a
  fabricated success or diff.
- Check policy before dispatching each tool. Only `None` from the policy permits
  execution; even an empty reason string blocks. Approval callbacks must return
  literal `True` for one call or the explicit `Approval.ALL` decision below.
- `read-only` permits only the three read tools. `safe` requires approval for
  other tools. `yolo` still applies the shell denylist. The regex denylist is a
  guardrail, not an OS security boundary.
- `ask` prompts for every model-requested tool call, including reads. Approval
  callbacks accept literal `True` for one call or `Approval.ALL` to change the
  shared policy to `yolo`; other values deny. Grants live only in memory and
  retain command denials and enterprise restrictions. `/permissions` changes or
  revokes the policy without replacing the conversation; CLI replacements and
  children share the active Policy. Do not restore grants from journals.
- Tool exceptions become correlated model-visible error results. Provider and
  application-hook failures remain visible to callers. Preserve interrupt
  propagation rather than catching `BaseException` as an ordinary tool error.
- The turn limit counts model replies, not tool calls. Keep the final tool-free
  wrap-up request and its durable input message.
- Kill shell process groups on timeout/interruption; do not leave descendants
  running after reporting that execution stopped.

### Conversations, sessions, and compaction

- One `Harness` owns one active conversation and permits one run at a time.
  Isolation changes are allowed only between runs.
- Preserve provider-neutral messages, tool call IDs, and native provider replay
  fields. Do not flatten opaque state into ordinary assistant text.
- Flush journal messages before exposing their events or executing associated
  tools. Keep `_recorded` bookkeeping consistent when replacing the working view.
- Journals live under `.chiikawa/sessions` by default. Preserve private file
  creation, locking, file and directory synchronization, torn-tail handling, and
  repair of missing tool results without duplicating earlier records.
- An interrupted-call marker is not proof that the original tool had no effects.
  Inspect actual state before retrying a mutation.
- Compaction replaces the model's working history; it does not rewrite the full
  durable journal. Retain valid recent call/result relationships and opaque state.
  Replace history only after a nonempty, tool-free summary succeeds.
- Resume must reject incompatible provider state. OpenRouter resume restores its
  saved model unless an explicitly selected conflicting model requires rejection.
- Children receive fresh ephemeral conversations and no child journal. Preserve
  inherited model, provider, policy, output cap, workspace, and isolation settings;
  retain the default depth ceiling of two.
- Fleet jobs use separate Harness instances, preserve input result order, and
  isolate ordinary job errors. Callers must coordinate shared files; prefer
  distinct workspaces for independent jobs.

### Provider and terminal behavior

- Keep credentials and wire formats specific to their provider. OpenRouter must
  never fall back to Foundry credentials. Do not log request authorization data.
- Preserve retry bounds and nonretryable error handling. Reject malformed,
  incomplete, or failed model responses before executing their tool calls.
- Only public reasoning summaries belong in UI output. Preserve opaque provider
  state for replay without displaying or summarizing private reasoning fields.
- Foundry activity uses SSE text/refusal deltas for provisional stderr previews.
  Never execute streamed function arguments or persist partial assistant output.
  Require a validated completed response, then preserve usage/native replay and
  emit the final answer once. Do not retry after opening a stream. Managed
  Responses streaming must use the approved transport and reserve quota first.
  Keep compaction buffered, terminal controls escaped, and cleanup on interruption.
- Slash commands are local to interactive input. Headless `-p "/status"` remains
  literal model input. Keep discovery, filtering, arrow selection, Tab completion,
  green selection highlighting, and direct command submission consistent.
- `/model` and `/provider` changes start a new conversation and preserve the old
  journal. `/new` keeps configuration. Selecting current settings is a no-op.
- Restore terminal settings on success, EOF, and Ctrl-C. Pasted newlines must not
  submit commands. Respect `NO_COLOR`, non-TTY input, and `TERM=dumb` fallbacks.
- Escape untrusted terminal controls, bound previews, and keep activity on its
  intended stream. Preserve CLI exit codes: success 0, runtime error 1, argparse
  error 2, and interruption 130.
- Interactive terminals default to compact activity; headless/plain output uses
  verbose events. Preserve `--display`, Ctrl+T and `/history`, grouped exploration,
  public-only replay, prompt drafts, and exclusive keyboard ownership for approvals.
  Esc interrupts from the working view and returns from the history viewer.
  Shell tool stdin is closed so subprocesses cannot take over the terminal.
  Persist activity-enabled tool status/details for replay; never infer successful
  exit codes from legacy journals without status. Display history survives compaction.
  Keep Working... and its public action description visible for the entire task
  in both layouts, including tools and expanded history. Approvals own input and
  show a static waiting status. Clear activity before the final answer and on
  failure/interruption; transient status must not enter model or session history.

## Jail and Sandbox rules

Isolation and approval policy are separate controls. Jail is the default for a
fresh standard-profile CLI launch, including resume, unless explicitly configured otherwise.
Enterprise-profile sessions require an enterprise launch and cannot downgrade to Jail.
**Jail restricts file tools; shell commands run with host permissions.**

`/sandbox` and `/jail`, and the equivalent `Harness.set_isolation(...)`, must
prepare and validate the replacement before committing it. Failed activation
keeps the current runtime and conversation. Successful switching preserves
conversation, model, provider, workspace, and policy; refreshes environment
instructions; and appends a durable environment-change notice. `/new`, provider
and model changes, and children inherit active isolation. Loading history must
not silently restore prior execution privileges.

Maintain these Sandbox boundaries:

- Provider requests, credentials, policy approval, and orchestration stay on the
  host. Core tools, memory operations, and skill discovery/loading use the trusted
  container worker; do not add host file-access shortcuts in Sandbox mode.
- Each invocation uses a fresh container with the project at `/workspace`.
  Project edits persist immediately; temporary files and background processes
  do not persist across calls.
- Exchange requests and results as JSON and validate responses. Pass Docker
  arguments as an argument vector; never interpolate model arguments into a host
  shell command. Preserve string results, errors, and diff details across transport.
- Use a non-root user, read-only root filesystem, writable bounded temporary
  storage, dropped capabilities, no privilege escalation, default seccomp,
  2 CPUs, 2 GiB memory, and a 256-process limit.
- Default networking to `deny`; `allow` explicitly enables bridge networking.
  Never forward host environment variables or mount the host home, Docker socket,
  or SSH agent. Require a local Linux Docker daemon over a Unix socket.
- Mask existing `.env`/`.env.*` files and `credential.md`; preserve the implemented
  example/sample template exceptions. Mask root `.chiikawa`, `.codex`, `.agents`,
  `.aws`, and `.ssh`, and mount root `.git` read-only.
- Preserve metadata validation for protected symlinks, hard links, sockets,
  special files, unsafe mount paths, and host-home workspaces. Sandbox journals
  must remain inside the validated non-symlink `.chiikawa/sessions` location.
- Reject arbitrary host `extra_tools` in Sandbox v1. Git commits and linked
  worktrees with external Git metadata are outside v1 support.
- Pin an existing local image for execution. `/sandbox` must never pull images
  or execute a project Dockerfile. Only explicit `--sandbox-setup` builds from
  bundled sources; custom images are trusted code supplied by the operator.
- Remove the exact invocation's container and descendants on completion, failure,
  timeout, or interruption. Surface cleanup failures; never fall back to Jail.
  Do not use broad Docker prune operations to clean up tests.

Sandbox does not make destructive project edits safe, detect secrets generally,
or prevent transmission of accessible project data when networking is enabled.
Concurrent host changes to mount paths are outside the documented guarantee.
Do not weaken protections to make a test or development command pass.

## Protected policy and enterprise execution

`chiikawa/SYSTEM_PROMPT.md` is application-owned policy. The host composes it with
runtime facts through `system_policy.py`; Sandbox workers return only memory and
catalog data. Keep project-derived text out of provider system/instructions fields.
Send contextual user messages separately from the durable conversation and
compaction input. Never stage the deployed policy in the worker container.

`Harness(..., profile="enterprise")` requires Sandbox, rejects host extras and
Jail downgrades, and validates that the installed package/zipapp is outside the
writable project. Preserve profile inheritance, read-only profile/system/isolation
properties, and rejection of standard-profile resume for enterprise journals.
Recheck invariants before execution. Nonempty `system_extra` is rejected; migrate
old callers to task context or project instruction files. Empty legacy arguments
remain accepted. Low-level provider/loop calls are trusted host application code,
not a protected interface for untrusted users.

The policy fingerprint identifies content, not authenticity. Installation and
startup administrators are trusted; standard Jail does not guarantee protection
from host-shell tampering. Do not claim full enterprise readiness or guaranteed
prompt-injection prevention. Preserve the existing approval-policy defaults;
enterprise isolation alone does not imply safe-mode approval.

## Memory, skills, and sensitive data

Project memory is `CHIIKAWA.md`; project skills are `skills/*/SKILL.md`. Preserve
workspace containment, cheap catalog discovery, and explicit skill-body loading.
Memory and skill text are reference data, not authority to override policy or
system instructions. Preserve memory locking and durable appends.

Never put credentials, session contents, private reasoning, or personal local
paths into source, fixtures, documentation, screenshots, or release archives.
Use synthetic sentinel secrets in security tests. Existing `.env`, `credential.md`,
and `.chiikawa/` contents are private working data, not sample inputs to inspect
routinely. Keep exclusion rules and safe template handling intentional.

This repository guide is distinct from Chiikawa's runtime project memory and
skill loader. Do not claim that adding `AGENTS.MD` changes the harness's system
prompt or adds automatic instruction discovery without implementing that feature.

## Coding and change discipline

- Match existing Python style: four-space indentation, `snake_case`, small
  functions, focused modules, and useful docstrings for contracts and invariants.
- Retain Python 3.10 compatibility; avoid newer-only syntax or standard-library
  APIs unless the supported-version change is part of the task.
- Prefer dependency injection and existing hooks over globals, hidden side
  effects, or duplicated orchestration. Package import must remain free of
  credential reads, network calls, and session creation.
- For new tools or runtime operations, update schemas, policy expectations,
  worker dispatch where appropriate, error handling, tests, and documentation.
- For CLI changes, update argparse, slash-command candidates where applicable,
  terminal tests, and installation guidance. Construct replacement configuration
  before abandoning the active conversation.
- For persistence changes, test real files and failure paths. Do not replace
  durability checks with mocks that only assert the implementation was called.
- For subprocesses and containers, cover timeout, nonzero exit, interruption,
  descendants, and cleanup. Avoid shell interpolation of untrusted input.
- Keep unrelated formatting, generated products, dependency migrations, and
  historical reports out of a focused change. There is no mandatory formatter,
  linter, or third-party test runner configured at the repository root.

## Verification by change type

Use the smallest relevant test slice while iterating, then run the regression
suite for code changes. Add tests for meaningful new behavior and regressions;
documentation-only edits usually need content, command, and link checks rather
than new automated tests.

| Change | Primary coverage |
| --- | --- |
| Loop / Foundry transport | `tests/test_day1.py` |
| Core tools / policy | `tests/test_day2.py` |
| Memory / skills / compaction | `tests/test_day3.py`, `tests/test_metrics.py` |
| Harness / sessions / child agents | `tests/test_day4.py` |
| CLI / fleets / interruption | `tests/test_day5.py`, `tests/test_day5_interrupt.py` |
| Slash commands / line editor | `tests/test_commands.py` (real pseudo-terminal coverage) |
| Activity / diffs / terminal output | `tests/test_activity.py` |
| Compact transcript / replay / keyboard ownership | `tests/test_transcript.py` (including real pseudo-terminals) |
| OpenRouter / provider routing | `tests/test_openrouter.py` |
| Core policy / enterprise profile / provider trust separation | `tests/test_system_policy.py` |
| Isolation transitions / configuration | `tests/test_isolation.py` |
| Real container boundary / terminal switching | `tests/test_sandbox_docker.py` |
| Installer / archives | `tests/test_install.py`, `scripts/check_dist.py` |

Examples:

```sh
python3 -m unittest discover -s tests -p 'test_isolation.py' -v
python3 -m unittest discover -s tests -p 'test_commands.py' -v
python3 -m unittest discover -s tests -v
git diff --check
```

Use discovery with `-s tests`; the Docker tests import the terminal test helpers
from that discovery directory. Keep tests deterministic, use temporary projects,
mock network boundaries in offline tests, and restore modified environment and
terminal state. `demos/verify_day*.py` are separate live/demo workflows: inspect
them before running; they can read credentials, call providers, or modify products.

For isolation changes, run real Docker tests when a daemon is available. Building
the image is an explicit operation that downloads base images and packages:

```sh
docker info
python3 -m chiikawa --sandbox-setup
CHIIKAWA_TEST_DOCKER=1 python3 -m unittest discover -s tests -p 'test_sandbox_docker.py' -v
```

Verify outside-project denial, synthetic credential isolation, protected paths,
network denial and controlled allowance, permitted edits, Python/Node execution,
child inheritance, and timeout/Ctrl-C cleanup. A mocked Docker test does not prove
container isolation. If Docker is unavailable, state which integration checks
were not run; do not describe skipped tests as passing boundary verification.

CI runs ordinary tests and packaging on Ubuntu and macOS with Python 3.10 and
3.14. A separate Ubuntu/Python 3.12 job requires real Docker and installed Sandbox
checks. For Docker Desktop compatibility changes, also verify on macOS when
available and distinguish local results from CI results.

## Packaging and releases

The version source is [chiikawa/_version.py](chiikawa/_version.py). Packaging uses
setuptools, a wheel, a source distribution, a portable zipapp, an installer bundle,
and checksums. Do not hand-edit generated archives or metadata.

With the `build` frontend installed in the chosen development environment:

```sh
python3 -m build
python3 scripts/build_release.py
python3 scripts/check_dist.py
```

`uv build` is an alternative to `python3 -m build` when available. If needed,
install the build frontend into a development virtual environment with
`python3 -m pip install build`; this does not add a project runtime dependency.
Run the wheel/source build before the portable build and distribution check.

For changes to Sandbox resources or installed execution, also run:

```sh
CHIIKAWA_TEST_DOCKER=1 python3 scripts/check_dist.py
```

This verifies Sandbox startup and switching from installed wheel and portable
artifacts, outside the checkout, including enterprise-profile startup and downgrade rejection. Resource access must work through
`importlib.resources`, including inside a zipapp. Bundle the trusted worker,
its required modules, the host-only core policy, and the Dockerfile in every applicable release format.

Keep `pyproject.toml`, `MANIFEST.in`, `scripts/build_release.py`, and the explicit
archive allowlists in `scripts/check_dist.py` aligned when adding package files
or changing layout. Do not broaden packaging to include secrets, sessions,
products, caches, or arbitrary workspace files. Preserve installer checksum
verification, atomic replacement, and refusal to overwrite unrelated executables.

The release workflow checks that a `v*` tag matches the package version and
creates a draft only after the ordinary and Linux Sandbox jobs succeed. Local
packaging does not authorize creating tags or publishing a release.

## Completion checklist

Before reporting a task complete:

- Compare the result against every requested behavior and deliverable.
- Inspect the final diff and preserve pre-existing unrelated work.
- Run the relevant checks and record exact outcomes, including skipped or blocked
  checks. Do not copy a historical test count as current verification.
- Check behavior across both providers, both isolation modes, resume, and children
  when the changed code affects those paths.
- Update user documentation and this guide when public behavior, commands,
  architecture, or verification requirements change.
- Confirm that temporary processes/containers are cleaned up and that private
  data or unintended artifacts were not added.
- Report what changed, what was verified, and any material limitations. Reference
  affected files and give a next action only when one is actually needed.
