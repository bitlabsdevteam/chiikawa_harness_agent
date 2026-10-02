# Chiikawa

Day 1 of a minimal Python agent harness, built with only the standard library
and Python 3.10 or later. Chiikawa connects **gpt-6-astra on Microsoft Foundry** to a small, observable tool
loop. All harness code lives in `chiikawa/`; runnable examples live in `demos/`.

The package initializer is intentionally empty until Day 4. Later lessons add
tools, security, context, memory, skills, sessions, subagents, and a CLI.

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
