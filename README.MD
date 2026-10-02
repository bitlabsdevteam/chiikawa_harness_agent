# Chiikawa

Days 1–3 of a minimal Python agent harness, built with only the standard library
and Python 3.10 or later. Chiikawa connects **gpt-6-astra on Microsoft Foundry** to a small, observable tool
loop. All harness code lives in `chiikawa/`; runnable examples live in `demos/`.

Day 2 adds rooted file tools, shell execution, and an approval policy. Day 3 adds
context compaction, durable project memory, and skills loaded on demand. The
package initializer is intentionally empty until Day 4. Later lessons add
sessions, subagents, and a CLI.

## Run

No packages need installing. From this repository's root, use the local testing
configuration supplied in `credential.md`:

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
request omits unsupported `temperature` and uses the model's default reasoning
effort. The output token limit remains 65,536.

Assistant messages retain the response's raw `provider_output` items, including
opaque encrypted reasoning, for replay on the next turn. Each tool call and
result carries its `call_id`, so repeated calls to the same tool stay distinct.
Requests use `store: false` and request encrypted reasoning for stateless
continuation. The demo prints only visible text and tool activity. Incomplete,
failed, and empty responses raise errors; refusals are displayed as text.

HTTP uses a 600-second timeout and up to five retries after the
initial attempt for 429/500/502/503, URL failures, and timeouts.

`chiikawa/loop.py` records replies and sequential tool results in the caller's
message list. Tools expose `.spec` and `.run(**args)`. `before_tool` permits a
call only when it returns `None`; blocked calls and tool errors become results
the model can read. `before_turn` optionally replaces history in place before
every model call. Exhausting the turn budget triggers one tool-free wrap-up.

`on_event(kind, payload)` receives an assistant history message for `assistant`,
the call dictionary for `tool_start`, and the tool history message for
`tool_end`. Provider and hook errors propagate to the application.

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
its current directory and requires a POSIX environment; it is not a filesystem
sandbox. Timed-out commands and their process groups are terminated.

`chiikawa/security.py` plugs directly into `before_tool=policy.check`:

| Mode | Policy |
| --- | --- |
| `read-only` | Allow only `read_file`, `list_files`, and `grep`. |
| `safe` (default) | Allow read tools; require an approver returning `True` for other tools. Without an approver, refuse. |
| `yolo` | Allow calls except denylisted shell commands. Used by the scratch demo. |

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
  file lock and syncs it to disk. `build_system_prompt` reads current memory
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
