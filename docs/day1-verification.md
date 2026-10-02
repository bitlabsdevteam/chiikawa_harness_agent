# Day 1 verification — Microsoft Foundry migration

The provider now uses `gpt-6-astra` through the Foundry Responses API.
The original Gemini assignment is retained in `day1-spec.txt` as historical
context; the current provider contract and commands are in the README.

## Offline checks

`python3 -m unittest discover -s tests -v`: **19 tests passed** on Python 3.14.7.

- Provider: Azure endpoint normalization, authentication precedence, Responses
  request schema, output parsing, refusals, usage, failed/incomplete responses,
  HTTP/network retries, and error bodies.
- Tool continuity: opaque reasoning replay and distinct call IDs for repeated
  calls to the same function, exercised through the actual provider and loop.
- Loop: dice transcript/event order, text-only coffee response, blocking,
  unknown tools, tool exceptions, sequential execution, history replacement,
  turn exhaustion, and a zero-turn budget.
- Credentials: explicit file loading, environment overrides, no stdout output,
  and rejection of incomplete files before environment changes.
- All Python files parse with Python 3.10 grammar; execution on Python 3.10
  itself has not been tested. Provider and loop have 113 and 65 lines.
- No third-party Python packages are required. `git diff --check` passes.

## Live acceptance

Executed `python3 -m demos.verify_day1 --credentials credential.md` against
`https://cr-dev-foundry-01.openai.azure.com/openai/v1`, deployment `gpt-6-astra`.
Both checks passed. Timestamp and full visible transcripts are in
[day1-live-verification.json](day1-live-verification.json).

1. Dice: user prompt → assistant `roll_dice` call with `count="3"` → tool result
   `[2, 1, 1]` → assistant answer. Reviewed the arithmetic: 2 + 1 + 1 = 4;
   the assistant correctly said that 4 does not beat 10.
2. Coffee: “Build a landing page for a coffee shop” returned a complete HTML
   document as text with **zero tool calls**. This verifies provider behavior;
   it is not a browser validation of the generated page.

`credential.md` is ignored and untracked. Saved transcripts contain visible
dialogue only, without API keys or encrypted reasoning.
