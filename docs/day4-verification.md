# Day 4 verification

Implemented durable sessions, crash repair, bounded sub-agents, public `Harness`
composition, package exports, and the Day 5 CLI stub. All earlier implementation
modules remain unchanged. The renamed contract is in [day4-spec.txt](day4-spec.txt).
See [production notes](day4-production-notes.md) for the OpenAI guidance and
explicit durability, ownership, performance, and side-effect limits.

## Offline verification

`python3 -m unittest discover -s tests -q`: **80 tests passed** on Python 3.14.7,
including the previous 57 tests and 23 Day 4 tests.

- Unique timestamp/slug filenames, 40-character slug bound, private file modes,
  most-recent session selection, and workspace-contained default session paths.
- Unicode JSONL, preserved raw provider output/phase metadata, malformed JSON
  and UTF-8 tail tolerance, read-only loading, truncation before further appends,
  and a valid final record without its trailing newline.
- Missing results repaired in order with original call IDs; complete and legacy
  histories remain usable. Invalid record shapes and disk-sync failures surface.
- Concurrent record appenders produce complete, non-interleaved JSONL records.
- Public exports, model precedence, conditional skills, extra-tool overrides,
  supplied session paths, optional persistence, and the sub-agent depth ceiling.
- Messages are durable before external callbacks; final turn-limit messages
  are persisted. Provider failure preserves input; journal failure prevents
  further model execution.
- Resume persists only missing repair records and never re-appends existing
  history. Compaction preserves an append-only full journal without duplicating
  retained messages or skipping new events; later resume and continuation work.
- Children start with clean context, inherit model/system/policy configuration,
  enforce the parent's read-only restrictions, and create no session journals.

All Python files parse with Python 3.10 grammar and carry module/public-function
docstrings. Execution on Python 3.10 itself has not been tested. File locking
and crash verification use POSIX. `python3 -m chiikawa` successfully reaches the
documented CLI stub without making a model call.

## Live acceptance

Executed successfully:

```sh
python3 -m demos.verify_day4 --credentials credential.md
```

Deployment: `gpt-6-astra` at
`https://cr-dev-foundry-01.openai.azure.com/openai/v1`.
Timestamp, visible traces, repaired records, parent journals, and generated
artifacts are in [day4-live-verification.json](day4-live-verification.json).

### Actual SIGKILL and resume

1. A dedicated worker began the exact task: “Create part1.txt through part5.txt
   one at a time, then SUMMARY.md describing each”.
2. `part1.txt` and `part2.txt` were created. At the next `write_file` tool-start
   callback, the pending assistant call had already been synced to the journal.
   The observer paused there so the verifier could kill its own worker with
   SIGKILL (exit code `-9`). No simulated exception substituted for the kill.
3. A new Harness in a different process called `resume()`. The missing
   `write_file` result was restored with the exact interruption notice and its
   original `call_id`, then durably appended before the continuation task.
4. `run("continue the task")` inspected the directory and existing parts, then
   created the remaining parts and `SUMMARY.md`. The model read all six outputs
   and accurately reported completion.
5. Independent checks confirmed all six files are nonempty, the summary names
   every part, the original journal prefix is unchanged, the original user task
   appears once, and the same single session file was used throughout.

### Two ephemeral children and parent-run tests

1. The parent received the exact two-delegation task and issued two
   `spawn_agent` calls with self-contained contracts and separate file ownership.
2. The first child created `utils.py` with `slugify(text)`. The second created
   `test_utils.py` with exactly five plain assertions. Each child inspected its
   work and returned a brief report.
3. The **parent's own persisted journal** records `bash` with
   `python3 test_utils.py`, followed by `(exit 0, no output)`. Child callback
   events are not mistaken for parent actions.
4. AST inspection confirmed `slugify` and five assertions. A separate Python
   invocation also passed with exit code 0.
5. `.chiikawa/sessions` contains exactly one JSONL file, the parent's journal;
   `latest()` returns that same file.

An initial verifier run completed both model workflows but failed its final
path-equality check because macOS aliases `/var` to `/private/var`. The verifier
now compares canonical paths. A complete rerun passed and produced the saved
report. No harness behavior was changed to accommodate that verifier issue.

`credential.md` and `.chiikawa/` remain ignored by Git. Saved public evidence
excludes API keys and opaque provider output; raw local journals retain the
provider state necessary for resumption.
