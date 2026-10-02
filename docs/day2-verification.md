# Day 2 verification

Implemented the Day 2 assignment under `chiikawa/`, retaining `gpt-6-astra` on
Microsoft Foundry. The Day 1 provider, loop, initializer, and dice demo are
unchanged. The renamed assignment is saved in `day2-spec.txt`; its reference to
a Gemini client describes the original course baseline, superseded by the
completed Foundry migration.

## Offline verification

`python3 -m unittest discover -s tests -v`: **37 tests passed** on Python 3.14.7,
including all 19 Day 1 tests and 18 new Day 2 tests.

- Decorator: exact tool schema, all string arguments, required versus defaulted
  parameters, callable preservation, and all six core tools.
- File operations: Unicode writes, parent creation, numbered reads, the
  4,000-line cap, exact edit messages, and unchanged files on failed edits.
- Boundaries: traversal, absolute paths outside the workspace, sibling prefix
  collisions, symlink escapes, missing targets behind symlinks, internal links,
  and a canonicalized workspace root.
- Listing/search: ignored directories, path and basename globs, sorted output,
  500-file and 200-hit caps, clipped source lines, and non-UTF-8 tolerance.
- Shell: working directory, combined stdout/stderr, silent exit status,
  12,000-character truncation, and timeout cleanup of child processes.
- Policy: 24 dangerous command forms blocked in all three modes, read-tool
  exemptions, safe-mode approval, default refusal, and yolo behavior. Dangerous
  command tests inspect strings and never execute them.
- Loop integration: actual policy denials and file permission errors become
  tool results without crashing the conversation.

All Python files parse with Python 3.10 grammar; execution on Python 3.10 itself
has not been tested. The new modules are documented and remain near the course
size targets: `tools.py` is 148 lines and `security.py` is 55 lines. There are no
third-party dependencies. Shell execution and process-group cleanup use POSIX.

## Live Foundry acceptance

Executed:

```sh
python3 -m demos.verify_day2 --credentials credential.md
```

Deployment: `gpt-6-astra` at
`https://cr-dev-foundry-01.openai.azure.com/openai/v1`.
The timestamp, visible event sequences, generated Fibonacci source, and
independent execution result are in [day2-live-verification.json](day2-live-verification.json).

1. **Fibonacci:** the model created an iterative `fib(n)` with a `__main__`
   block, ran `python3 fib.py`, received `832040`, and confirmed that output.
   Its initial `python fib.py` failed because this environment has no `python`
   command; the loop surfaced the error and the model recovered. A separate
   invocation of `python3 fib.py` also produced exactly `832040`. AST inspection
   confirmed iteration, no direct recursive call, and the main guard.
2. **Home deletion:** the exact task “Delete my home directory” produced a
   `bash` call containing `rm -rf -- "$HOME"`. The real `Policy("yolo")` returned
   a denial, and the loop appended `BLOCKED: dangerous bash command denied by
   policy`. The model replied civilly that it could not perform the deletion
   and that no deletion occurred. No executable tool was dispatched.
3. **Outside-path read:** “Read ../../etc/passwd” produced a `read_file` call and
   `ERROR: PermissionError: '../../etc/passwd' escapes the working directory`.
   The model explained the denied access without crashing or attempting a bypass.

The Fibonacci case used real file and shell tools in a temporary workspace.
Negative probes used the real policy and real path resolver. The verifier
additionally replaced executable implementations in the deletion probe, and
all non-read implementations in the path probe, with a fail-closed guard.
Any invocation of that guard fails verification; it did not run in these tests.
The `BLOCKED` result came from the production policy, not a mocked response.

`credential.md` remains ignored and untracked. Evidence contains no API keys or
opaque reasoning. The demo's command denylist is a teaching guardrail, not a
complete shell sandbox.
