# Day 3 verification

Implemented context compaction, persistent `CHIIKAWA.md` memory, and local skills
for the existing Foundry `gpt-6-astra` harness. Earlier provider, loop, tools,
policy, and demos remain unchanged. See [production notes](day3-production-notes.md)
for design decisions, official OpenAI references, and the scope of the guarantees.

## Offline tests

`python3 -m unittest discover -s tests -q`: **57 tests passed** on Python 3.14.7,
including all 37 previous tests and 20 Day 3 tests.

- Exact token estimation, budget and length thresholds, and no-op list identity.
- One summary request with the exact system instruction and no tools; previous
  summaries persist across repeat compaction.
- Retained message identity and raw provider state; orphaned tool results move
  into the summarized prefix, including a whole tail of tool results.
- Bounded visible transcript rendering, named calls/arguments, and exclusion of
  opaque provider internals from summary input.
- Invalid summaries and provider failures leave original history unchanged.
- The actual `before_turn` hook replaces history in place and emits a successful
  compaction event before the next task-model call.
- Memory prompt ordering, canonical directory, disk persistence, separate-process
  reload, concurrent appends, propagated sync failures, and outside symlink denial.
- Empty catalogs, stable ordering, front-matter-only descriptions, simple folded
  descriptions, metadata-only prompts, exact lookup, file-only reloads, and
  outside skill symlink exclusion.
- Skill text reaches the model only after the real `use_skill` tool returns it.

All Python sources parse with Python 3.10 grammar and public functions have
docstrings. Execution on Python 3.10 itself has not been tested. Memory locking
and directory sync use the existing POSIX runtime assumption. Memory and skill
modules slightly exceed the original approximate 50-line targets to include
durability, containment, and metadata handling without omitting documentation.

## Live acceptance

Executed:

```sh
python3 -m demos.verify_day3 --credentials credential.md
```

Deployment: `gpt-6-astra` at
`https://cr-dev-foundry-01.openai.azure.com/openai/v1`.
Full visible traces and timestamps are in
[day3-live-verification.json](day3-live-verification.json).

### Context task

- The exact requested task ran with `budget_tokens=1500` through `before_turn`.
- `one.txt`, `two.txt`, `three.txt`, `four.txt`, and `five.txt` each received one
  `write_file` call followed by one `read_file`, in that order. Each contains
  exactly 20 newline-terminated `ping` lines.
- Compaction fired **twice**, with additional tool work after each event:
  17 → 7 messages (about 1,513 → 742 estimated tokens), and
  15 → 7 messages (about 1,518 → 764 estimated tokens).
- The model ran `wc -l` and received 20 for each file, 100 total. An independent
  `wc -l` invocation confirmed those counts and direct content checks confirmed
  every line. `MANIFEST.md` lists all five verified counts and was read back.
- The final answer correctly reported completion. The event stream retains both
  factual summaries for direct inspection.

### Fresh memory

- `remember()` persisted a newly generated project verification code.
- A separate Python process built a new system prompt over the same directory.
  It sent one fresh user question, no previous history, and no tools.
- The question did not contain the answer. The model recalled the exact code
  from `CHIIKAWA.md` included in its system prompt and made no tool calls.

### Skills without code changes

- A baseline conversation answered a short welcome-writing task in ordinary
  prose with no skill installed.
- The verifier added only `skills/brand-voice/SKILL.md`, then sent the identical
  task in a fresh conversation.
- The model called `use_skill("brand-voice")` and then wrote:
  “Arrr! Welcome aboard our coffee shop, matey—drop anchor, settle in, and enjoy
  a fresh cup with us.”
- Harness/demo source SHA-256 values before and after were identical.

Saved evidence contains no API credentials or opaque reasoning. The local
`credential.md` remains ignored and untracked. These are behavioral acceptance
checks, not a claim of complete prompt-injection resistance or sandbox security.
