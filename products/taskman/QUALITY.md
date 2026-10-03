# Quality record

## Current verification

The current suite is run with `python3 -m unittest discover -s products/taskman -q`
from the repository root. Counts below are recomputed by `project_metrics()` in
`test_taskman.py`; the project-contract tests check this table against the files.

| Metric | Count |
| --- | --- |
| Test cases | 68 |
| CLI subprocess cases | 66 |
| Project-contract cases | 2 |
| Python source files | 2 |
| Documented definitions | 106 |
| README sections (H2) | 10 |
| README prose words | 2134 |
| Walkthrough blocks | 5 |
| Output snapshots | 2 |
| Review findings | 12 |

## Historical first-delivery record

The following sections preserve the original 48-test delivery record. Their
counts, timings, and layout limitations describe that earlier version; the
current metrics above and the acceptance matrix in `DESIGN.md` supersede them.

## Delivered scope and counts

- **5 working subcommands:** add, list, done, rm, stats.
- **48 subprocess unittest cases**, each with a private temporary directory; fault injection also runs in a child process. No task test uses a personal store.
- **6 help screens** (root plus five commands), tested for examples and zero file creation.
- **2 Python source files**, both with module docstrings. AST inspection found **70 documented class/function definitions**: 18 in taskman.py and 52 in test_taskman.py. No missing definition docstrings.
- **10 README sections** and **1,533 words outside fenced blocks**, counted by a word-token regex. This is informational, not a web-content quota.
- **3 runnable README walkthrough shell blocks**, plus the separate test/compile block. Both displayed output tables were compared exactly against real stdout.
- **12 numbered second-review findings**, all fixed, with named regression evidence in REVIEW.md. The numbering was checked to be exactly 1 through 12.
- **0 dependencies installed**, external assets, webpages or git repositories created. The project is standard-library-only.
- Web sections, visible prose quotas, CSS tokens, SVG counts, viewport sizes and browser interactions: **not applicable to this CLI**. DESIGN.md records the terminal-specific roles, wireframe, signature and first critique before implementation.

## Verification performed

Environment: **Darwin 25.6.0, Python 3.14.7, non-root uid 501**.

```sh
python3 -m unittest -v
python3 -m py_compile taskman.py test_taskman.py
python3 taskman.py --help
python3 taskman.py rm --help
```

Final full-suite result: **48 tests passed in 9.292 seconds; 0 failures, 0 errors, 0 skips**. Real unreadable-file, read-only-file and read-only-directory tests all ran successfully. Compilation completed without errors. Before the second review, the original 21-case suite was green; the extended 44-case suite also passed before the final four checks were added. No failing test remains.

An additional Python verification command extracted all three README walkthrough blocks and executed them together with `/bin/sh -eu`, redirecting HOME to a temporary directory. All exited successfully; both fenced text tables matched stdout exactly. The separate unittest/compile block was run directly above. An AST/review/content audit verified module/definition docstrings, 48 test methods, exact review numbering and the README counts stated here.

## Concrete output and usability checks

- The ledger header is `ID  STATUS  TASK`; an exact-output test also checks the wider three-digit ID rail. Task rows are sorted without rewriting a hand-reordered store.
- The demonstrated two-task ledger produces Total 2, Open 1, Done 1 and Completion 50%. Empty counts are zero with completion `n/a`.
- Unicode round trips include CJK, accents, a combining sequence, Arabic, Hebrew and a joined emoji. Ordinary text remains unchanged; dangerous terminal controls, Unicode separators and explicit bidi formatting are rejected on both capture and load.
- Duplicate completion succeeds while preserving bytes and nanosecond mtime. Unknown IDs fail. Deleting every task leaves the counter intact; the next ID is not reused.
- Invalid descriptions and malformed IDs exit 2. Corrupt storage and unknown IDs exit 1, use stderr, emit no expected-error traceback, and preserve task-file bytes.
- Truncated JSON, invalid UTF-8, duplicate keys, unknown fields, bad types, duplicate IDs and bad counters cannot trigger a silent reset or repair.
- Atomic failure injection at JSON serialization, fsync and replacement checks original bytes/mtime, temporary-file cleanup, no success output and no consumed ID. Actual permission failures complement these injected failures.
- Sixteen competing writer subprocesses retain all sixteen tasks with IDs 1–16 and next_id 17. Symlink lock targets are preserved and rejected.
- Final-store symlinks, dangling symlinks, directories and FIFOs are rejected deliberately; the FIFO test is bounded by a subprocess timeout.
- `--store` works on either side of each command, with later values winning. Tests cover absolute, relative, space-containing and default working-directory paths. Empty hints retain the selected store.

## Limits and nonclaims

- Runtime verification was on the Darwin/Python version above. The documented Python 3.9+ macOS/Linux target uses available standard-library APIs, but Linux and older Python versions were not exercised here. Windows is unsupported because of POSIX locking.
- Locking protects cooperating same-user writers on a local filesystem. It is not a security boundary against hostile directory changes or external editors; network filesystems and hard-link aliases are outside the guarantee.
- Atomic visibility is tested through replacement-failure preservation and concurrent writers, not physical power-loss simulation. The temporary file is fsynced; the parent directory is not. Forced termination can leave a temporary file. Persistent `.lock` files are intentional, including after some failed mutations.
- Basic permission bits are retained, not ownership, ACLs, extended attributes or hard-link identity. Root bypasses normal permission checks; permission tests explicitly skip there, although none were skipped in this run.
- Descriptions remain full length and may wrap in narrow terminals. No interactive terminal emulator, screen reader or grapheme-width renderer was tested. The stable ASCII ID/state columns are checked directly; the final description-column rule uses character counts.
- The JSON ledger is intended for a small personal collection and is loaded fully into memory. There is no paging, undo, task editing, reopen, due-date scheduling, migration or automatic corruption repair. These omissions are explicit in the README rather than hidden behind inactive commands.
