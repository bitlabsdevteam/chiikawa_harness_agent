# Chiikawa

Day 1 of a minimal Python agent harness, built with only the standard library
and Python 3.10 or later. Chiikawa connects Gemini to a small, observable tool
loop. All harness code lives in `chiikawa/`; runnable examples live in `demos/`.

The package initializer is intentionally empty until Day 4. Later lessons add
tools, security, context, memory, skills, sessions, subagents, and a CLI.

## Run

No packages need installing. From this repository's root, set
`CHIIKAWA_API_KEY` in your environment (or use the `GEMINI_API_KEY` fallback).
The demo does not automatically load `.env` files. If you keep the key in a
local `.env`, that file is ignored by Git; load it into your environment first.

```sh
python3 -m demos.day1_dice
python3 -m demos.day1_dice --prompt "Build a landing page for a coffee shop"
```

The default model is `gemini-3.1-pro-preview`; `--model` selects another model.
Live requests use your Gemini quota. The dice demo prints the user prompt,
assistant tool call, tool result, and final answer. The coffee prompt uses the
same available dice tool and should return text without calling it.

## Design

`chiikawa/provider.py` translates neutral history dictionaries to Gemini,
preserves function-call thought signatures, filters thought text, and returns
token usage. HTTP uses a 600-second timeout and up to five retries after the
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
python3 -m demos.verify_day1
git log --oneline
```

The 13 offline tests use mocked network/model responses and exercise real
translation, retry, loop, and dice code. They do not prove live model behavior.
The live verifier checks both specified prompts and saves successful transcripts
to `docs/day1-live-verification.json`. Inspect the dice answer against the rolls
to confirm the model's arithmetic and its comparison with 10.

The complete renamed Day 1 assignment is in [docs/day1-spec.txt](docs/day1-spec.txt).
