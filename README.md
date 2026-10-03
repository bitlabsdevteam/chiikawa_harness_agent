# Chiikawa

A small coding-agent harness with a CLI, bounded subagents, and a concurrent fleet.
The standard profile has zero third-party runtime dependencies; Enterprise adds
optional identity dependencies and IT-managed controls. Requires Python 3.10 or later.
Standard Chiikawa connects **gpt-6-astra on Microsoft Foundry by default**, with optional OpenRouter support, to a small, observable tool
loop. All harness code lives in `chiikawa/`; runnable examples live in `demos/`.

Day 2 adds rooted file tools, shell execution, and an approval policy. Day 3 adds
context compaction, durable project memory, and skills loaded on demand. Day 4
adds durable sessions, crash repair, bounded sub-agents, and the public `Harness`
class. Day 5 finishes the CLI and fleet, then uses them to build and review
three complete products.

## Run

Install a downloadable release or use `pipx install .` / `uv tool install .`
from this checkout to get the `chiikawa` command. See [installation instructions](INSTALL.md)
for downloads, configuration, upgrades, and removal. Requires Python 3.10+ on
macOS, Linux, or WSL. Source checkouts also support `python3 -m chiikawa`.

For the standard profile on an unmanaged machine, set the Foundry configuration in your shell, using
your actual resource endpoint and deployment name. Supply the API key through
your environment or secret manager; the placeholder below is not a working key.

```sh
export AZURE_OPENAI_ENDPOINT="https://YOUR-RESOURCE.openai.azure.com/openai/v1"
export AZURE_OPENAI_API_KEY="YOUR-API-KEY"
export CHIIKAWA_MODEL="gpt-6-astra"

# Interactive: safe mode asks before write, shell, and delegation calls.
python3 -m chiikawa -d ./scratch

# Headless: yolo mode by default; use --mode safe to require approval.
python3 -m chiikawa -d ./scratch -p "Create and test a Fibonacci function"

# Continue the same directory's latest durable session.
python3 -m chiikawa -d ./scratch --resume -p "Review the result and fix any bugs"
```

Use `-m` to override the model, `--mode read-only` for file inspection, or
`--max-turns` to change the default 120-turn budget. Ctrl-D exits; Ctrl-C exits
with status 130 and explains how to resume. A missing `--resume` session is an
error. The CLI shows live activity and public reasoning summaries when returned
by the provider. **Jail is the default:** in the standard profile, its directory restriction applies to file
tools; shell commands run with host permissions. Use `/sandbox` to activate Docker
isolation for subsequent tools.

**Enterprise mode (integration in progress):** dedicated developer machines use
Microsoft Entra ID, an IT-owned core policy, approved providers and destinations,
device assignments, and administrator-controlled token limits. **Jail remains the
default**, using a native OS boundary; IT-approved Docker Sandbox is optional.
After company IT provisions the managed installation, policy, identity registration,
credentials, and local management service, developers sign in with:

```sh
chiikawa -d /path/to/project --profile enterprise --mode safe
```

The flag alone does not provision an enterprise deployment. Managed machines
select Enterprise automatically and reject `--profile standard`. Project
`AGENTS.MD` guidance remains supported under the core policy. Networking is offline
by default; provider access also requires IT approval. See
[Protected core policy and enterprise profile](#protected-core-policy-and-enterprise-profile)
for configuration, installation requirements, and the trusted-administrator boundary.

### Interactive commands

Type `/` at the prompt to see the command menu immediately, without pressing
Enter. Continue typing to filter it, use ↑/↓ to select, Tab to complete, Enter
to choose, and Esc to dismiss. `/model` and `/provider` open their choices;
you can also type a complete command directly:

| Command | Action |
| --- | --- |
| `/model [model-id]` | Show the current/configured models or choose a deployment/model ID. |
| `/provider [name]` | Show or change providers: Foundry/OpenRouter in standard, IT-approved names in Enterprise; `/provider/` also works. |
| `/status` | Show profile, core policy fingerprint, isolation, network access, provider, model, approval policy, workspace, session, context, and limits. |
| `/permissions [ask\|safe\|all\|read-only]` | Show or change approvals for this CLI process, including new conversations and subagents. |
| `/sandbox` | Validate Docker and switch subsequent tools to Sandbox. |
| `/jail` | Switch subsequent tools to Jail: host execution with file-tool restrictions in standard, native OS isolation in Enterprise. |
| `/new` | Start a new conversation using the current configuration. |
| `/history` | Expand public conversation history and retained tool output. |
| `/help` or `/` | List every command. |
| `/exit` | Exit the CLI. |

Submitted prompts and recalled history text appear in green, as does the selected
command-menu row. `NO_COLOR=1`, basic terminals, and redirected output omit color.

In the standard profile, use `/provider openrouter`, then `/model openai/gpt-5.4`.
The model picker lists current, environment-configured, and default model IDs;
type any other available ID directly. Foundry expects a deployment name and
OpenRouter expects a qualified `provider/model` ID. These commands do not make
API requests or validate remote model access. Keys still come from the environment.
Enterprise lists IT-approved providers/models and uses credentials held by the
management service. `/status` also reports managed network access, the IT token
limit, and charged tokens.

Changing model/provider starts a fresh conversation and preserves the previous
session log. `/new` does the same without changing configuration. Workspace,
policy, isolation, sandbox network/image settings, context threshold, turn limit, reasoning preference, and explicit output
limit remain in effect. Selecting the current model/provider keeps the session.
Changes apply to this CLI process, not your shell configuration.
Enterprise identity, device assignment, provider/network restrictions, and shared
quota remain in effect across these changes and delegated agents.

The prompt supports cursor editing, ↑/↓ history outside the menu, and bracketed
paste (pasted newlines become spaces and never submit automatically). Ctrl-D on
an empty prompt exits. Basic terminals (`TERM=dumb`) and redirected input use
plain input: type `/` and press Enter to list commands. Slash commands are local
to the interactive prompt; a headless `-p` argument is always sent as task text.

### Action permissions

Choose approval for each action or approve all subsequent actions. Every approval
prompt displays the tool and its full arguments before offering:

- **`y` — allow once:** approve only this tool call; the next eligible call asks again.
- **`a` — allow all until exit:** approve this and subsequent tool calls, including
  subagents and new conversations in the same CLI process.
- **`n`, Enter, or EOF — deny:** block the call without executing it. Ctrl-C interrupts.

Use `/permissions` to inspect the setting or change it at the interactive prompt:

| Command | Behavior | Startup equivalent |
| --- | --- | --- |
| `/permissions ask` | Ask before every model-requested tool call, including reads. Also revokes an earlier allow-all grant. | `--mode ask` |
| `/permissions safe` | Allow read tools; ask before writes, shell commands, memory/skill tools, and delegation. | `--mode safe` |
| `/permissions all` | Approve tool calls without further prompts until exit. | `--mode yolo` |
| `/permissions read-only` | Allow only `read_file`, `list_files`, and `grep`; block other tools. | `--mode read-only` |

```sh
chiikawa --mode ask                         # Ask before each tool action
chiikawa --mode yolo                        # Grant permission once at startup
chiikawa --mode ask -p "Inspect the project" # Also works for headless tasks
```

Interactive startup still defaults to `safe`; headless tasks and library calls
default to `yolo`. Grants are kept in memory, survive `/new`, model/provider and
isolation changes, and are not restored from session history on a later launch.
`/status` reports the active mode (`yolo` means all actions approved). Model
requests, automatic context loading, and local slash commands are not tool calls
and do not receive these prompts.

Approval never disables command denials, workspace/container restrictions, or
enterprise identity, provider, network, and quota controls. It authorizes actions
within the user's task. In library integrations, `Policy("ask", approver=...)`
accepts literal `True` for one call, `Approval.ALL` for subsequent calls sharing
that Policy, and denies other return values. Import `Approval` from `chiikawa`;
set `policy.mode = "ask"` to revoke the grant. Policies do not persist grants to disk.

### Jail and Docker Sandbox

These startup examples apply to the standard profile on an unmanaged machine:

```sh
chiikawa                              # Jail; Docker is not required
chiikawa --sandbox-setup              # Explicit one-time image download/build
chiikawa --isolation sandbox          # Start in Sandbox using the local image
chiikawa --sandbox-network allow      # Jail now; allow networking on later /sandbox
chiikawa --sandbox-image trusted:tag  # Use an existing, trusted local image
```

`/sandbox` checks Docker and validates a worker invocation before committing the
switch. It never pulls an image or runs a project Dockerfile. A failed switch
keeps the current mode and conversation. `/jail` returns to standard host execution
or Enterprise's native OS Jail, depending on the active profile;
selecting the active mode does nothing. Successful switches preserve messages,
model, provider, workspace, and approval policy, refresh system instructions,
and append a durable environment-change notice. `/new`, model/provider changes,
and child agents inherit active isolation. Every fresh CLI launch defaults to
Jail unless `--isolation sandbox` is supplied, including when resuming a log. Enterprise-profile
sessions require an enterprise-profile launch.
History does not restore execution privileges.

In Sandbox, provider requests, credentials, approvals, and journals stay on the host. All six
core tools, project memory, and skill discovery/loading run through a trusted
container worker. Each call starts a fresh container with the project mounted at
`/workspace`: project edits persist immediately, while `/tmp` and background
processes do not survive between calls. The standard image supplies Python 3.12,
Node 22/npm, Git, Bash, core utilities, and ripgrep. Standard Jail adds no dependencies.

Containers run as a non-root user with a read-only root, a writable temporary
filesystem, dropped capabilities, no privilege escalation, Docker's default
seccomp profile, 2 CPUs, 2 GiB memory, and a 256-process limit. Networking defaults
to `deny`; standard-profile `--sandbox-network allow` enables bridge networking. Host environment
variables, home directories, Docker sockets, and SSH agents are not forwarded.
Timeouts, Ctrl-C, and failures remove the specific container and its descendants;
execution never silently falls back to the host.

Enterprise rejects `--sandbox-network allow`: both workers remain offline and
approved requests use the IT-managed HTTPS gateway. Enterprise Sandbox also
requires an existing local image pinned by SHA-256 in IT policy; developers cannot
select arbitrary images. Native Enterprise Jail uses macOS Seatbelt or Linux
bubblewrap and fails if the required isolation is unavailable.

Existing `.env`/`.env.*` files and `credential.md` anywhere in the project are
masked with empty read-only files; `.env` templates containing an `example` or
`sample` suffix component remain accessible. Root `.chiikawa`, `.codex`, `.agents`,
`.aws`, and `.ssh` directories are masked, and root `.git` is read-only. Unsafe
protected-path symlinks, hard-linked files, sockets, and device files cause
activation/tool execution to fail. Sandbox journals must reside under the
non-symlink `.chiikawa/sessions` directory. Arbitrary host `extra_tools`, Git
commits, and linked worktrees with external Git metadata are outside v1 support.
Custom images are trusted code and must already exist locally. Use a local Linux
Docker daemon (Docker Desktop on macOS); concurrently changing project mount
paths from host processes is outside the isolation guarantee.

Sandbox still permits destructive project edits when requested. Path protection
is not general secret detection: secrets in other files or Git history can remain
accessible. Enabled networking permits transmission of accessible project data.
Approval policy remains independent of isolation in both modes.

Library callers use `Harness(..., isolation="jail", sandbox_network="deny",
sandbox_image="chiikawa-sandbox:1")` and `harness.set_isolation("sandbox")` or
`harness.set_isolation("jail")`. Changes are permitted only between runs; the
method returns `True` for a successful change and `False` for an active-mode no-op.

Run the real container and terminal suite after setup:

```sh
CHIIKAWA_TEST_DOCKER=1 python3 -m unittest discover -s tests -p 'test_sandbox_docker.py' -v
```

Linux Docker coverage is required by CI. Docker Desktop is verified with the same
suite on macOS; ordinary unit and package tests also run without Docker.

### Protected core policy and enterprise profile

Chiikawa's comprehensive operating policy is maintained in
[`chiikawa/SYSTEM_PROMPT.md`](chiikawa/SYSTEM_PROMPT.md) and loaded from the installed
application. It covers autonomous implementation, investigation, debugging,
verification, delegation, instruction trust, permissions, secrets, and clear
progress reporting. A project's `SYSTEM_PROMPT.md` is not a replacement policy.

The host assembles the trusted system instructions from this policy and actual
runtime facts. Project memory and skill descriptions are sent separately as
labeled contextual user messages, while loaded skills, project files, and tool
results remain ordinary tool output. They never become the provider's system
instructions. Context messages are reattached for model requests without being
duplicated in the durable journal or included in compaction summaries.

Developers can write `agents.md`, `AGENTS.md`, or `AGENTS.MD` for repository
conventions, architecture, and test commands. Chiikawa is instructed to discover
and read applicable files through its tools before editing; this is model-driven
inspection, not automatic file injection. Nested guidance applies within its
directory. Conflicting files at the same level require clarification. Project
guidance cannot authorize permission changes or override the core policy.

**Enterprise feature coverage.** The eight requirements below are defined in
[SECURITY.md](SECURITY.md). Managed implementation is present in this checkout,
but deployment tooling and end-to-end verification are unfinished; this is not
yet a production-ready enterprise release.

| Requirement | Enterprise behavior and current scope |
| --- | --- |
| Lightweight coding agent | Runs locally on dedicated developer laptops and development machines for implementation, review, debugging, and testing. The standard profile stays dependency-free; the optional `enterprise` dependency group adds `cryptography` for identity verification. |
| Jail by default, optional Sandbox | Native OS Jail is the default. `/sandbox` selects an IT-approved Docker image; `/jail` returns to native Jail. Both retain managed restrictions and reject unrestricted host fallback. Linux native Jail verification remains pending. |
| Company IT super admins and Entra ID | Developers sign in through Microsoft Entra ID device-code authentication for the configured company tenant. Signed identity claims, the local OS account, and enrollment determine access. Company IT administrators manage policy; a prompt or project file cannot grant an administrator role. |
| Protected system policy and project guidance | The installed core system policy defines coding behavior and instruction priority. Developers can supply `agents.md`, `AGENTS.md`, or `AGENTS.MD` beneath it. Supported customization cannot replace the core policy or authorize permission, provider, network, device, or quota changes. |
| Approved providers and offline networking | OpenAI, Anthropic Claude, Google, and other providers require explicit IT approval of models, endpoints, and credentials. External access is denied by default. IT may allow specific HTTPS URLs or IP addresses; provider approval and network approval are separate. Provider adapters and gateway integration still need end-to-end verification. |
| One assigned machine per developer | IT binds an Entra object ID and local OS user ID to a machine fingerprint. Missing, disabled, or mismatched assignments are rejected. Cross-machine replacement and revocation require IT deployment coordination; the fingerprint is not hardware attestation. |
| IT-only installation and management | Only company IT may install, update, configure, or uninstall the managed deployment. The application belongs outside writable projects with administrator ownership and protected policy, credentials, and accounting. IT commands cover enrollment, revocation, policy, secrets, and limits. Managed install/update/uninstall automation and service deployment remain unfinished. |
| Unlimited tokens by default; IT-set limits | Each developer starts without a total token limit. IT can set, change, remove, or explicitly reset a grant. Ordinary responses, compaction, and child-agent requests share persistent accounting. Finite grants require a supported, approved token-counting endpoint and fail closed when a request cannot be authorized. |

**Identity and administration.** A local IT-owned management service authenticates
the developer and mediates provider requests. Entra tenant/object identifiers,
OS user identity, and the assigned device are checked; email addresses and claimed
roles in conversation are not authorization. Session tokens stay in memory, while
provider credentials remain in private service storage and are not forwarded to
project workers. The current administrator boundary is the local OS administrator
(`root`), not an Entra super-admin dashboard.

IT configures `/etc/chiikawa/enterprise.json`, private service state under
`/var/lib/chiikawa`, and the local socket at `/var/run/chiikawa/management.sock`
(using their canonical `/private/...` locations on macOS). The official CLI and
Harness automatically select Enterprise when managed policy is present and
reject an explicit standard-profile launch. Invalid managed configuration fails
closed. The installation and its policy must not be writable by developers or
model tools.

From a trusted managed installation, `python3 -m chiikawa.enterprise_admin --help`
lists IT operations: initialize an offline policy, validate/install reviewed
policy, provision private state, store provider secrets, inspect the device
fingerprint, assign or disable a developer, and set token limits. Administrative
operations require OS administrator privileges; model-generated commands run as
the developer. The regular per-user installer is not a managed enterprise
installer.

**Provider and network controls.** The managed adapters cover OpenAI Responses,
Chat Completions-compatible endpoints, Anthropic Messages, and Google
`generateContent`. IT specifies the permitted provider names, model IDs,
endpoints, credential references, and per-response output caps. Environment API
keys and arbitrary model IDs do not override this registry.

Both Enterprise workers stay offline. Approved model calls and the `fetch_url`
tool go through the management service. URL rules match HTTPS host, port, and
path; a trailing `/` permits a path subtree. Bare IP rules permit HTTPS on port
443. Wildcard domains, automatic redirects, and environment-configured proxies
are not implicitly allowed. `fetch_url` currently supports HTTPS GET for UTF-8
text up to 200,000 bytes; it does not enable general shell internet access.
Entra sign-in, identity key discovery, model endpoints, and any token-counting
endpoints also need explicit connectivity approval. With an empty allowlist,
cloud sign-in and model requests cannot run. Approved destinations may receive
accessible project data as part of a task.

**Limits, sessions, and subagents.** Chiikawa supports `spawn_agent` with fresh
child context and a depth ceiling of two. Children inherit the managed session,
provider/model, workspace, approval policy, and isolation. `/new`, model/provider
changes, isolation switches, resume, and concurrent requests do not reset the
IT-controlled token grant. Profile is fixed for a Harness's lifetime, and
enterprise journals require an enterprise launch to resume.

Finite grants reserve counted input tokens plus the permitted output before
generation, then reconcile reported usage. Uncertain requests retain their
reservation; a reported overrun suspends the grant. There is no automatic daily
or monthly reset: IT explicitly issues a new grant with `--reset-grant`.
`set-limit --tokens unlimited` removes the total limit. Context compaction
thresholds and per-response output caps are separate from this shared quota.

Approval policy remains independent: interactive CLI defaults to `safe`, while
headless tasks and library calls default to `yolo`. Use `--mode safe` for headless
approval. Enterprise rejects arbitrary host `extra_tools` and rechecks managed
invariants before execution. File tools, memory, and skill loading use the active
isolated worker; provider credentials and the deployed policy remain outside it.

After IT provisioning, these commands use Jail by default:

```sh
chiikawa -d /path/to/project --profile enterprise --mode safe
chiikawa -d /path/to/project --profile enterprise --resume
```

Library callers must also authenticate with the managed service:

```python
from chiikawa import Harness, Policy
from chiikawa.enterprise_client import EnterpriseClient

session = EnterpriseClient()
session.login(lambda url, code: print(f"Sign in at {url} with code {code}"))
agent = Harness("/path/to/project", profile="enterprise",
                enterprise_session=session, policy=Policy("read-only"))
print(agent.profile, agent.policy_fingerprint)
print(agent.system)  # Read-only inspection; assignment is rejected.
```

`/status` reports the core policy SHA-256 fingerprint, profile, isolation, managed
network access, token limit, and charged usage. The fingerprint identifies
content; it is not a signature. Missing or empty core policy fails before model
execution. Restart after IT deploys a core policy update.

**Trust boundary and rollout status.** The system prompt guides behavior; OS
isolation, service authorization, and managed deployment enforce permissions.
No prompt can guarantee model compliance or prevent an OS administrator from
replacing software. Standard Jail's host shell does not provide enterprise tamper
protection. Enterprise also depends on IT preventing unmanaged applications and
direct network access outside the official agent. Device fingerprints alone do
not prevent spoofing or duplicate enrollment across separately managed machines.

Project edits can still be destructive. Protected-path masking is not general
secret detection, and approved network destinations can receive project data.
Sandbox Git commits and linked worktrees with external Git metadata remain
outside v1 support.

Before production rollout, complete the managed installer/uninstaller and
service deployment, live Entra tenant testing, provider/gateway/quota integration
tests, Linux native Jail and interruption-cleanup verification, and updated
regression/package checks. Existing Sandbox tests alone do not validate these
enterprise controls. Signed distribution, hardware-backed device attestation,
central fleet administration, and enterprise audit infrastructure are not provided
by this implementation.

**Migration:** nonempty `Harness(system_extra=...)` now raises a `ValueError` with
migration guidance. Move ordinary requirements into the task or a project
instruction file. `Harness.system`, `profile`, and `isolation` are read-only;
callers change isolation with `set_isolation` between runs, subject to the active
profile's restrictions. The legacy
`memory.build_system_prompt()` helper now returns only installed policy and host
facts, rejects nonempty `extra`, and no longer includes project memory. Use
`memory.read_memory()` for memory data; low-level integrations must send that data
as ordinary context, not append it to system instructions.

### Use OpenRouter

For the standard profile on an unmanaged machine, set your OpenRouter key and
select the provider explicitly. Enterprise uses the IT-managed provider registry
and credential store described above.

```sh
export OPENROUTER_API_KEY="YOUR-OPENROUTER-KEY"
python3 -m chiikawa --provider openrouter -d ./scratch
# Choose another tool-capable model using its qualified OpenRouter ID:
python3 -m chiikawa --provider openrouter -m anthropic/claude-sonnet-4.6 -d ./scratch
```

The installed `chiikawa` command accepts the same flags. Configuration comes
from the environment; `.env` is not loaded automatically. For a trusted local
`.env`, run `set -a`, `source .env`, then `set +a` before starting.

| Setting | Foundry (default) | OpenRouter |
| --- | --- | --- |
| Provider flag | `--provider foundry` | `--provider openrouter` |
| Default model | `gpt-6-astra` deployment | `openai/gpt-5.4` |
| Model environment variable | `CHIIKAWA_MODEL` | `OPENROUTER_MODEL` |
| API key | `CHIIKAWA_API_KEY` or `AZURE_OPENAI_API_KEY` | `OPENROUTER_API_KEY` only |
| Output limit per response | 65,536 tokens | 16,384 tokens |

`--provider` overrides `CHIIKAWA_PROVIDER`; without either, Foundry is selected
even when an OpenRouter key exists. To opt into OpenRouter for your shell, set
`CHIIKAWA_PROVIDER=openrouter`. `-m` overrides the selected provider's model
environment variable. OpenRouter ignores Foundry's endpoint, keys, and model
configuration. Use `--max-output-tokens 8192` to override either output limit,
including compaction and child-agent requests. Select a tool-capable model and
adjust `--context-threshold` and the output limit to fit its context window;
the 600,000-token compaction threshold is an estimate, not a model capacity check.

Tools, permissions, compaction, fleet jobs, and child agents use the selected
provider. Resume OpenRouter sessions with `--provider openrouter --resume`.
Resume restores the saved model unless you explicitly configure a different
one, which is rejected. Switching providers or OpenRouter models requires a new
session so opaque reasoning state is replayed only to its original provider/model.
Existing sessions without provider metadata remain Foundry sessions.

The library supports the same selection:

```python
from chiikawa import Harness

agent = Harness("./scratch", provider="openrouter", model="openai/gpt-5.4",
                max_output_tokens=8192)
```

Interactive startup displays Chiikawa wearing a baseball cap, with a CLI badge
and wordmark:

```text
           .--------.
          /    >_    \
      .--/____________\--.
     /   \____________/   \
     \  .-'          '-.  /
      '/    _      _    \'
      /    (o)    (o)    \
     |   ///   w    ///   |    C H I I K A W A
     |         '         |
      \                 /     >_ tiny harness
       '._           _.'
      _/  '         '  \_
     (  /             \  )
      '-|             |-'
        \             /
         '._       _.'
           (_)___(_)
```

The cap is blue, and the cheeks and wordmark are pink on color terminals. Set `NO_COLOR=1` for
plain text; redirected output and `TERM=dumb` also omit logo colors. Headless
tasks (`-p`) omit the logo. The artwork is embedded text and needs no network
access or extra packages at startup.

This unofficial terminal-art adaptation follows the round ears, large eyes,
cheeks, hands, and feet in the user-supplied `image-1.png`, with a cap added.
The original version used the [official anime website](https://www.anime-chiikawa.jp/)
as a reference ([original reference image](https://www.anime-chiikawa.jp/images/icon.png),
accessed October 3, 2026). Chiikawa is a character created by Nagano; the project
is not affiliated with the creator or anime production.

### Terminal activity

Interactive terminals use a compact activity transcript by default:

```text
• Ran python3 -m unittest discover -s tests
  └ Ran 24 tests
    OK

• Explored
  └ Read app.py
  └ Search README.md for configuration
    + Show details (ctrl+t)

• Ran git status --short
  └ M app.py
    M README.md
    M tests/test_app.py
    + 33 lines (ctrl+t to expand)

• Working... (8m 33s · esc to interrupt)
  Reading app.py · ctrl+t history · /status for usage
```

Consecutive reads, file listings, and searches share an **Explored** block.
Commands show three output lines, with additional retained output available in
the transcript. Progress and answers remain in normal terminal scrollback.
**Working...** appears immediately and stays visible throughout model calls,
tool execution, context compaction, progress, and result processing. Its current
action changes to text such as **Reading app.py**, **Editing app.py**, or
**Running python3 tests.py**. The elapsed timer covers the entire task. Expanded
history keeps the live status visible; approval prompts show **Waiting for
approval**. The indicator clears before the final answer and on errors or
interruption. Both compact and verbose layouts support it; redirected output
prints a plain status for each event without animation or terminal controls.
**Ctrl+T** opens the public conversation and expanded tool output while working
or at the prompt; **/history** opens the same view. Scroll with arrows,
Page Up/Down, or Home/End. Ctrl+T or Esc returns without losing your prompt draft.
Esc from the working view interrupts the run with exit status 130, like Ctrl+C;
restart with `--resume`. Approvals temporarily take exclusive keyboard control.
Shell tools receive closed stdin, so interactive commands cannot consume CLI keys.

`--resume` shows recent public history and makes the full resumed transcript
available through Ctrl+T. Tool status and bounded diffs are saved with new
activity-enabled sessions. Older sessions without exit status are labeled as
such. Tool output limits still apply when expanded, and private provider replay
data never appears. Compaction does not remove entries from the display history.

Use `/status` for model, approval mode, context estimates, and API token totals
reported during this CLI session, including compaction and child requests.
Unavailable usage is identified explicitly; totals are not restored from old
journals. Use `--display verbose` for individual tool events, per-response token
meters, and the original waiting indicator. Headless and noninteractive runs
default to verbose; `--display compact` explicitly selects compact output.
Basic or redirected terminals do not animate or consume keyboard shortcuts.

The verbose layout displays a waiting indicator, brief progress updates, public reasoning
summaries, tool names, file paths, and completion status. File reads have bounded
previews; successful edits and writes show actual before/after diffs, capped at
40 lines. Shell commands show their output preview and exit code. Blocked and
failed operations are labeled explicitly and do not display successful diffs.

Verbose context and token meters look like this:

```text
Context history (est.): ~12,480 tokens · compact above 600,000 (2.1%)
Response tokens (API): input 13,210 · output 384 / 65,536 limit
Compacted context: ~602,415 -> ~18,204 tokens
```

The context meter uses the **same history estimate as the compaction trigger**
(characters in message representations divided by four). It includes history
metadata, but excludes system instructions and tool schemas. It is an estimate,
not a tokenizer measurement or the model's context-window capacity. API input
usage is shown separately after each response; API output counts include all
output tokens the provider reports, not only the visible answer. Missing API
usage is displayed as `not reported`.

The compaction threshold defaults to **600,000 estimated history tokens**. Set
it with `chiikawa --context-threshold 100000` (`--budget-tokens` is an alias).
The trigger is strictly above the threshold and checked before each model
request. Histories of seven messages or fewer wait until there is enough history
to summarize. Compaction preserves recent messages and can remain over the
threshold; the meter reflects that rather than implying a hard size limit.

In verbose mode, context size is shown at interactive startup, before model requests, after
compaction, and after responses. Compaction requests have their own input/output
usage line. The **output limit is per response** (65,536 for Foundry or 16,384
for OpenRouter by default), taken from the
same setting sent to the API. Token counts arrive after completion and are
not live token-by-token counters or cumulative billing totals.

```text
Progress
    I'll read the greeting, update it, and run it to verify.
> Read hello.py [read_file]
  Done: Read hello.py · 1 line (0.0s)
    1       print("hello")
> Edit hello.py [edit_file]
  Done: Edit hello.py · updated +1 -1 (0.0s)
    -print("hello")
    +print("hello from Chiikawa")
> Run shell command [bash]
    python3 hello.py
  Done: Run shell command · exit 0 (0.0s)
    hello from Chiikawa
```

Activity goes to **stderr** and completed assistant answers to **stdout**, so
`chiikawa -p "your task" > answer.txt` keeps the activity visible in the terminal.
Foundry responses stream by default in the CLI. Public text appears immediately
as a live preview on stderr, before generation finishes; the validated completed
answer is written once to stdout. Compact mode and expanded history show a
growing preview, while verbose mode shows the latest text beside its activity
indicator. Redirected activity receives flushed text chunks under “Streaming
response (preview).”

Streaming uses Responses API server-sent events, not simulated typing. Tool
arguments and opaque reasoning are never streamed into the preview. Tool calls
execute only after `response.completed` validates successfully; incomplete,
failed, interrupted, or malformed streams do not execute pending calls or save
a partial assistant message. Completed output, usage, and native replay are
retained once in the normal session flow. A stream is never automatically retried
after its response headers arrive.

`Harness(activity=True, on_event=...)` emits provisional `assistant_delta` events
with a `text` field for Foundry and managed Enterprise Responses providers.
Direct callers can use `provider.complete(..., on_delta=callback)`; omitting the
callback retains buffered behavior. Compaction, OpenRouter, and other managed
provider protocols currently remain buffered. Enterprise streaming uses the same
approved HTTPS gateway, private credentials, and token reservation as ordinary
requests.

Verify against a configured Foundry deployment with synthetic prompts (uses
Azure quota; the report contains timings and counts, not credentials or project
contents):

```sh
python3 scripts/verify_foundry_streaming.py --credentials credential.md
```

This checks live API deltas and the real CLI, including preview arrival before
the final answer and a single completed journal entry. Omit `--credentials` to
use environment configuration. The report is written to
`output/foundry-streaming-verification.json` only after all checks pass.

Non-terminal output has no spinner or ANSI formatting; `NO_COLOR` disables colors.
During model requests, **Thinking...** is green in color terminals, with three
stationary dots fading in and out in sequence. The animation appears in compact,
expanded-history, and verbose views; plain output retains static text.
While thinking in verbose mode, the terminal indicator shows a random AI-themed joke from a
built-in pool of 20 one-liners. It rotates every five seconds without repeating
until the pool is exhausted, and fits the terminal width. Jokes clear when the
response arrives; they are not added to answers, session history, or redirected
activity logs. They work offline and do not make additional model requests.
Summaries arrive when a model response completes, and command output arrives
when that command finishes; subprocess output is still buffered even when model
responses stream.

The CLI requests medium reasoning effort with a public summary. Summaries can
be absent depending on the model and task. Use `--no-reasoning` to omit that
request and use your deployment's default effort. Encrypted provider state and
private reasoning are never rendered. See the official
[reasoning-summary API documentation](https://developers.openai.com/api/docs/guides/reasoning#reasoning-summaries).

For this repository's live testing, explicitly load the ignored configuration
supplied in `credential.md`:

```sh
python3 -m demos.day1_dice --credentials credential.md
python3 -m demos.day1_dice --credentials credential.md --prompt "Build a landing page for a coffee shop"
```

The file uses plain `endpoint=...`, `model=...`, and `API_KEY=...` lines. It is
ignored by Git, is loaded only when explicitly selected, and overrides environment
configuration for that process. Its contents are never executed as shell code.

Alternatively, set `AZURE_OPENAI_ENDPOINT` and `AZURE_OPENAI_API_KEY` in your
environment and omit `--credentials`. `CHIIKAWA_API_KEY` can override the Azure
key. The endpoint may be a resource root such as
`https://YOUR-RESOURCE.openai.azure.com` or the full `/openai/v1` base URL.
The demo does not automatically load `.env` files.

The default deployment name is `gpt-6-astra`; a credentials file supplies its
`model`, and `--model` overrides either default. Use the **deployment name**
configured in Foundry. Live requests use your Azure quota. The dice demo prints the user prompt,
assistant tool call, tool result, and final answer. The coffee prompt uses the
same available dice tool and should return text without calling it.

## Design

`chiikawa/provider.py` posts to Foundry's `/openai/v1/responses` endpoint using
an `api-key` header. It translates function schemas, JSON arguments, visible
output, and token usage. Astra tool calling requires the Responses API; the
request omits unsupported `temperature`. Library calls use the model's default
reasoning effort; the CLI opts into medium effort and public summaries unless
`--no-reasoning` is supplied. The default Foundry output token limit is 65,536.

Assistant messages retain the response's raw `provider_output` items, including
opaque encrypted reasoning, for replay on the next turn. Each tool call and
result carries its `call_id`, so repeated calls to the same tool stay distinct.
Requests use `store: false` and request encrypted reasoning for stateless
continuation. The demo prints only visible text and tool activity. Incomplete,
failed, and empty responses raise errors; refusals are displayed as text.

`chiikawa/openrouter.py` uses OpenRouter's Chat Completions endpoint with Bearer
authentication. It retains native assistant tool calls and `reasoning_details`
in session journals and replays tool results with their original IDs. Only
explicit `reasoning.summary` entries are displayed as public summaries; raw
reasoning text and encrypted data stay out of terminal output. Some models do
not return public summaries. Truncated or malformed tool responses fail before
any tools execute. `chiikawa/providers.py` selects the backend for the harness.

Both providers use a 600-second HTTP timeout and up to five retries after the
initial attempt for 429/500/502/503/504, URL failures, connection resets, and timeouts.

`chiikawa/loop.py` records replies and sequential tool results in the caller's
message list. Tools expose `.spec` and `.run(**args)`. `before_tool` permits a
call only when it returns `None`; blocked calls and tool errors become results
the model can read. `before_turn` optionally replaces history in place before
every model call. Exhausting the turn budget triggers one tool-free wrap-up.

`on_event(kind, payload)` receives an assistant history message for `assistant`,
the call dictionary for `tool_start`, and the tool history message for
`tool_end`. Provider and hook errors propagate to the application.

With `Harness(activity=True)`, observers also receive `model_start`, `model_end`,
public `reasoning`, `context`, `usage`, `compaction_start`, and `compaction` events.
Usage events distinguish `response` from `compaction` and use `None` for missing
API counts. Tool completion events include `status`, `elapsed`,
and bounded `details` (file diffs, line counts, or exit codes). These fields are
retained in tool journal records for transcript replay but are not sent as tool
content to providers. Assistant and tool messages are durable
before display. `reasoning_summary=False` disables the summary request/events.

## Verify

```sh
python3 -m unittest discover -s tests -v
python3 -m demos.verify_day1 --credentials credential.md
git log --oneline
```

The offline tests use mocked network/model responses and exercise real
translation, retry, loop, and dice code. They do not prove live model behavior.
The live verifier checks both specified prompts and saves successful transcripts
to `docs/day1-live-verification.json`. Inspect the dice answer against the rolls
to confirm the model's arithmetic and its comparison with 10.

The original renamed Day 1 assignment is preserved in
[docs/day1-spec.txt](docs/day1-spec.txt). Its Gemini-specific provider contract
is superseded by this Foundry migration. The loop's approval, event, error, and
turn-limit behavior remains as specified. See
[verification results](docs/day1-verification.md) for current evidence.

API references: [Microsoft Foundry Responses API](https://learn.microsoft.com/en-us/azure/ai-foundry/openai/how-to/responses?view=foundry-classic),
[GPT-6 migration](https://developers.openai.com/api/docs/guides/latest-model/gpt-6-astra#migration-quickstart),
and [function calling](https://developers.openai.com/api/docs/guides/function-calling).
OpenRouter references: [API overview](https://openrouter.ai/docs/api/reference/overview),
[tool calling](https://openrouter.ai/docs/guides/features/tool-calling), and
[reasoning replay](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens).

## Day 2: tools and policy

Run the Fibonacci build in a new temporary scratch directory:

```sh
python3 -m demos.day2_build --credentials credential.md
```

The demo prints its workspace path and keeps the generated files for inspection.
Use `--workdir /path/to/scratch` to choose a directory, `--prompt "..."` for a
different task, or `--model` to override the Foundry deployment.

`chiikawa/tools.py` provides a `Tool` dataclass and a `@tool(description, **params)`
decorator. The decorator builds string-typed argument schemas and marks arguments
with defaults as optional. `core_tools(workdir)` returns these six tools:

| Tool | Behavior |
| --- | --- |
| `read_file` | Number lines; show at most 4,000 and report the total. |
| `write_file` | Create parent directories and write UTF-8 text. |
| `edit_file` | Replace a snippet only when it occurs exactly once. |
| `bash` | Capture stdout and stderr; default to a 120-second timeout; bound output to its first and last 6,000 characters when needed. |
| `list_files` | Match relative paths or basenames; sort and cap at 500 files. |
| `grep` | Search matching text files; clip source lines to 200 characters and cap at 200 hits. |

File operations reject paths and symlinks outside the canonical workspace.
Listing and search skip `.git`, `node_modules`, `__pycache__`, `.venv`, and
symlinks to files outside the workspace. Shell execution uses the workspace as
its current directory and requires a POSIX environment. Standard Jail runs it on
the host; Enterprise Jail uses a native OS boundary, and Sandbox uses the
container boundary described above. Timed-out or interrupted commands and their process groups are terminated.

`chiikawa/security.py` plugs directly into `before_tool=policy.check`:

| Mode | Policy |
| --- | --- |
| `read-only` | Allow only `read_file`, `list_files`, and `grep`. |
| `ask` | Require approval for every tool call, including reads. |
| `safe` (default) | Allow read tools; require approval for other tools. Without an approver, refuse. |
| `yolo` | Allow calls except denylisted shell commands. Used by the scratch demo. |

An approver returns `True` to permit one call or `Approval.ALL` to switch the
shared policy to `yolo` until revoked. Other return values deny the call.

The denylist is checked before every mode and covers recursive forced removal
of root/home targets, `sudo`, `mkfs`, `dd if=`, `curl` piped to a shell, force
pushes, and redirection onto `/dev/sd` devices. This regex policy is a teaching
guardrail, not a complete shell-command security boundary.

Verify Day 2 with:

```sh
python3 -m unittest discover -s tests -v
python3 -m demos.verify_day2 --credentials credential.md
```

The live verifier builds Fibonacci using the real tools and independently runs
the result. Negative probes use the real policy and file-path resolver; a
fail-closed test guard prevents unexpected executable tool dispatch during the
deletion and outside-path cases. It saves visible events and generated source
in `docs/day2-live-verification.json`. See [Day 2 verification](docs/day2-verification.md)
and the [renamed assignment](docs/day2-spec.txt). The Day 1 provider, loop, and
dice demo are unchanged by this addition.

## Day 3: context, memory, and skills

Run the five-file task with compaction at the requested 1,500-token trigger:

```sh
python3 -m demos.day3_context --credentials credential.md --budget-tokens 1500
python3 -m demos.verify_day3 --credentials credential.md
```

The demo creates a scratch directory, prints compaction measurements, and keeps
its generated files for review. `--workdir`, `--prompt`, and `--model` work as in
Day 2. It uses the existing `before_turn` and `before_tool` hooks without changing
earlier modules.

- `context.compact(model, messages, budget_tokens)` estimates history size,
  summarizes the older prefix in one tool-free model call, and retains the last
  six messages. Leading tool results are included in the summary to avoid
  orphaned calls. Recent opaque provider output remains intact. The token
  estimate is a trigger, not a hard ceiling or an exact tokenizer.
- `memory.remember(workdir, note)` appends a fact to **CHIIKAWA.md** under a POSIX
  file lock and syncs it to disk. `read_memory` reads current memory
  into each new conversation, alongside base behavior and platform/workspace
  information. Memory must contain trusted project facts, not credentials or
  arbitrary untrusted content.
- `skills.catalog(workdir)` discovers `skills/<name>/SKILL.md` metadata.
  `catalog_prompt` advertises names/descriptions; `read_skill` returns the full
  instructions only when the demo's `use_skill` tool is called. Exact-name
  lookup and workspace containment prevent outside-path skill loading.

To add a local skill, create `skills/brand-voice/SKILL.md` inside the selected
scratch directory. The fixture at
`demos/fixtures/day3/skills/brand-voice/SKILL.md` demonstrates a voice change
through instructions alone. To reuse memory or skills across separate demo
invocations, pass the same `--workdir` each time.

The demo also exposes `remember` as a tool. Its scratch default is yolo; callers
of `run_task` can supply a `Policy` with their desired approval behavior. New
tools such as `use_skill` require approval in the existing safe policy because
they are not in its original read-tool allowlist.

See [Day 3 verification](docs/day3-verification.md) for the live results,
[design decisions and production boundaries](docs/day3-production-notes.md)
for the OpenAI guidance and limits, and the [renamed assignment](docs/day3-spec.txt)
for the exact contract. The live verifier records ordered tool calls, both
compactions, file contents, independent `wc -l` output, fresh-process recall,
and the skill comparison in `docs/day3-live-verification.json`.

## Day 4: the public Harness

For standard-profile usage, with `AZURE_OPENAI_ENDPOINT` and an API key set in the environment:

```python
from chiikawa import Harness, Policy, Tool, tool

agent = Harness("./scratch", model="gpt-6-astra")
answer = agent.run("Create part1.txt through part5.txt one at a time, then SUMMARY.md describing each")
print(answer)
print(agent.session_path)

restarted = Harness("./scratch", model="gpt-6-astra")
if restarted.resume():
    print(restarted.run("continue the task"))
```

`Harness` creates and canonicalizes its workspace. Model selection is explicit
`model`, then the selected provider's model environment variable, then its default
(`gpt-6-astra` for Foundry; `openai/gpt-5.4` for OpenRouter). The
default policy is `Policy("yolo")`, as required by the exercise. Supply
`Policy("safe", approver=...)` for explicit approval of writes and delegation.
It combines core tools, `remember`, conditional `use_skill`, and optional
`spawn_agent`; standard Jail callers may add or override tools with `extra_tools`.
Host extra tools are rejected in Sandbox and the enterprise profile. Nonempty
`system_extra` is rejected; use task text or project instruction files.

The constructor also accepts `profile`, `on_event`, `budget_tokens`
(default 600,000), `max_turns` (120), `session_path`, `enable_subagents`, and
`persist`. Passing a session path selects the journal; call `resume(path)` to
load its prior conversation. Only one active run should own a session at a time.

Using the local credentials file, run the daily demo and resume it later:

```sh
python3 -m demos.day4_harness --credentials credential.md --workdir /path/to/scratch
python3 -m demos.day4_harness --credentials credential.md --workdir /path/to/scratch --resume
python3 -m demos.verify_day4 --credentials credential.md
```

Sessions live in `.chiikawa/sessions/<timestamp>-<label>.jsonl`, which is ignored
by Git. Messages are synced as they arrive. `resume()` selects the most recently
modified session, tolerates a torn tail, and inserts missing tool results with
their original call IDs. Interrupted operations may already have had side
effects: inspect actual state before repeating them.

Compaction changes the in-memory view while the journal retains the full event
history. Resuming does not duplicate old messages. Child harnesses share the
workspace and policy but start with clean context and `persist=False`, so child
logs cannot replace the parent's latest session. Delegation is synchronous and
limited to two levels below the parent.

The Day 5 CLI now exposes this interface. See the [Day 4 specification](docs/day4-spec.txt),
[verification report](docs/day4-verification.md), and
[production notes](docs/day4-production-notes.md) for the recovery guarantees,
limitations, and official OpenAI references.


## Anatomy by day

The ten core files are the modules introduced on Days 1–4. Day 5 adds two small
front-door modules; `__init__.py` and `__main__.py` provide package wiring.

| Day | Files | Responsibility |
| --- | --- | --- |
| 1 | `provider.py`, `loop.py` | Foundry Responses translation, retries, tool loop and events. |
| 2 | `tools.py`, `security.py` | Six rooted tools, schemas, execution and approval policy. |
| 3 | `context.py`, `memory.py`, `skills.py` | Compaction, durable facts and on-demand instructions. |
| 4 | `session.py`, `subagent.py`, `harness.py` | Append-only journals, recovery and agent composition. |
| 5 | `cli.py`, `fleet.py` | Interactive/headless entry point and concurrent ordered jobs. |

## Compose an extra tool

Register tools through the constructor rather than editing the loop:

```python
from chiikawa import Harness, Policy, tool

@tool("Count words in supplied text", text="Text to count")
def count_words(text):
    """Return a deterministic word count."""
    return str(len(text.split()))

agent = Harness(
    "./scratch",
    model="gpt-6-astra",
    policy=Policy("safe", approver=lambda call, reason: (
        input(f"{reason}: {call['name']} {call['args']} — approve? [y/N] ").lower() == "y"
    )),
    extra_tools=[count_words],
    enable_subagents=False,
)
print(agent.run("Use count_words to count: coffee grows in Goa"))
```

## Run a fleet

```python
from chiikawa import Harness, run_fleet

jobs = [
    {"name": "one", "workdir": "./work/one", "task": "Write a greeting in hello.txt"},
    {"name": "two", "workdir": "./work/two", "task": "Write a tested Fibonacci function"},
]
results = run_fleet(jobs, lambda workdir: Harness(workdir, enable_subagents=False))
for result in results:
    print(result["name"], result["ok"], result["report"])
```

Each job constructs its own harness. Up to four workers run concurrently and
results retain input order. Ordinary construction/run exceptions become failed
job reports without preventing other jobs from finishing. Give concurrent jobs
separate directories and journals. A successful agent report means the run
returned; independently verify its output before treating the artifact as passed.

## Day 5: product proof

`demos/day5_products.py` builds artisan-coffee, taskman and viper with three fleet
workers and **gpt-6-astra**, then resumes each original session for the exact
12-deficiency design-director review. The design skill is installed verbatim
in each product directory. The HTML products use the full web bar; taskman's
CLI is evaluated for usability, persistence and subprocess tests.

```sh
# Requires fresh product sessions for the build phase.
python3 -m demos.day5_products --credentials credential.md

# Review an existing project again in its original session.
python3 -m demos.day5_products --credentials credential.md --phase review --project artisan-coffee

# Offline harness regression suite.
python3 -m unittest discover -s tests -v
```

See [the Day 5 specification](docs/day5-spec.txt),
[production notes](docs/day5-production-notes.md), and
[ordered fleet reports](docs/day5-fleet-runs.json). Product sources and their
design/review evidence live under `products/`. Local session journals remain
ignored; reports contain visible completion text and cumulative turn counts.

# chiikawa_harness_agent
