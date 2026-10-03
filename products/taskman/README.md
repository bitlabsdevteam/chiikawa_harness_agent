# Taskman

A small local task ledger for your terminal. Capture a task, find its permanent ID, finish it, and see what remains. Tasks live in a readable JSON file you control. No account, service, network access or installation step is required.

## Requirements

- Python **3.9 or newer** on **macOS or Linux**.
- A terminal configured for UTF-8 to display multilingual descriptions.
- Only the Python standard library is used. Writer locking uses POSIX `fcntl`; Windows is not supported by this version.

Run the following commands from the directory containing `taskman.py`. Use `python3 taskman.py --help` for the overview or `python3 taskman.py COMMAND --help` for an individual command.

## Try it with an isolated store

This copyable shell session keeps the demo separate from your actual tasks. Keep the same shell open so `$STORE` retains its value.

```sh
# walkthrough
DEMO_DIR="$(mktemp -d "${TMPDIR:-/tmp}/taskman-demo.XXXXXX")"
STORE="$DEMO_DIR/tasks.json"

python3 taskman.py --store "$STORE" list
python3 taskman.py --store "$STORE" add "Book the dentist"
python3 taskman.py --store "$STORE" add "买茶 • café ☕"
python3 taskman.py --store "$STORE" done 1
python3 taskman.py --store "$STORE" list
python3 taskman.py --store "$STORE" stats
```

The final list is a plain, unboxed ledger:

```text
ID  STATUS  TASK
--  ------  ----------------
 1  done    Book the dentist
 2  open    买茶 • café ☕
```

Statistics for that store are:

```text
METRIC      VALUE
----------  -----
Total           2
Open            1
Done            1
Completion    50%
```

IDs are right-aligned and state labels are written out, not encoded by color. Lists default to **80 columns**. Use `list --width 40` for a narrow terminal or `list --width 120` for a wider view; the minimum is 24. Long descriptions continue underneath the task text, with the ID and state columns left blank. No description characters are discarded, including internal spaces. Wrapping can split a word, but keeps combining marks and common joined emoji together. Separator lines are bounded by the chosen width instead of growing to match an entire long task.

Unicode is retained as entered, apart from trimming surrounding ordinary whitespace. Cell-width estimates handle CJK, combining sequences, flags and common emoji. Terminal fonts and complex scripts may differ; this is not a full Unicode grapheme-rendering engine. An extraordinarily wide ID expands the minimum layout rather than hiding its digits. If stdout cannot encode a character, Taskman prints a backslash escape such as `\u4e70` while preserving the original Unicode in JSON.

The demo directory remains available for inspection. `printf '%s\n' "$DEMO_DIR"` prints its location. It contains the JSON file and a small writer-lock sidecar.

## Command reference

| Command | What it does |
| --- | --- |
| `add "DESCRIPTION"` | Saves a new open task and prints its assigned ID. |
| `list` | Shows all tasks, including completed tasks, in ascending ID order. |
| `list --status open` | Shows only unfinished tasks. |
| `list --status done` | Shows only completed tasks. |
| `list --status all` | Explicitly requests the default unfiltered list. |
| `done ID` | Marks one task complete. Repeating it succeeds without rewriting the ledger. |
| `rm ID` | Permanently deletes one task. There is no undo or confirmation prompt. |
| `stats` | Shows total, open and done counts, plus a rounded completion percentage. |

Every command accepts `--store PATH` and `--lock-timeout SECONDS` either before or after the subcommand. If supplied more than once, the last occurrence wins. Spell long options in full: abbreviations such as `--sto` are rejected rather than guessed. Quote paths containing spaces. Options must precede the `--` delimiter if you use one.

```sh
# walkthrough
python3 taskman.py list --store "$STORE" --status open
python3 taskman.py done --store "$STORE" 2
python3 taskman.py --store "$STORE" done 2
python3 taskman.py --store "$STORE" rm 1
python3 taskman.py --store "$STORE" add -- "--review the proposal"
```

The second `done 2` reports that task 2 is already done and exits successfully. The last command illustrates how to add a description beginning with a dash. `rm` accepts exactly one ID, not a row number or a range. To review a task before deleting it, run `list` first.

## Choosing your task file

Without `--store`, Taskman uses **`.taskman.json` in the current working directory**. It does not look for a repository root, search parent directories or choose a global home-directory store. Moving to a different directory therefore selects a different default ledger.

For a task collection you can reach from several directories, pass an absolute path consistently. A leading `~` is expanded; relative paths are relative to the current working directory. Taskman does not expand environment variables inside paths, although your shell can do so before invoking the command.

```sh
# walkthrough
python3 taskman.py --store "$HOME/personal-tasks.json" add "Arrange a weekend walk"
python3 taskman.py --store "$HOME/personal-tasks.json" list
```

A missing file is an empty ledger. `list`, `stats` and help do not create a ledger or a lock file. The first successful `add` creates the JSON file. Parent directories must already exist; Taskman never creates them implicitly. A mutation that fails before replacement can create the lock sidecar, but does not create or rewrite task JSON. A failure to print a confirmation after replacement does not roll back the saved task.

Use separate paths for independent collections. IDs are unique within one ledger, not globally across all your files. A store and its reserved `.lock` sidecar must not be used as two separate task collections.

## IDs and completion

The first task gets ID 1. Every committed addition increments the saved `next_id` counter. Deleting a task, including deleting the last task, does **not** make its ID available again. Additions that fail before saving do not consume IDs. A confirmation-output failure or cancellation can occur after saving: inspect the selected store before retrying, or you may create a second task. Never infer a task's ID from its current row position. Command-line IDs use digits `0-9` and must be greater than zero; leading zeros are allowed, but signs, underscores, spaces and non-ASCII digit spellings are rejected.

Completion is a boolean state, not a timestamp. There are no due dates, recurrence, editing or reopening commands. A duplicate completion is intentionally harmless, which makes repeated shell commands safe. An unknown ID is an error for both `done` and `rm`; neither command invents a task.

`stats` counts only tasks currently in the ledger, not deleted history. Completion is `done / total`, displayed as a whole percentage. An empty store reports zero counts and `n/a` for completion rather than a misleading success percentage.

## Description validation

Descriptions must be quoted when they contain spaces and must include a letter, number, punctuation character or symbol. Zero-width formatting alone, standalone combining marks and whitespace-only descriptions are rejected instead of producing blank-looking rows. Surrounding spaces are removed. Line breaks, tabs, terminal control characters, Unicode line/paragraph separators, surrogate code points and directional formatting controls are rejected. Ordinary Arabic, Hebrew, CJK, accented text, combining marks and emoji joiners are accepted. Natural right-to-left text is not transliterated or reversed by Taskman.

Bad command arguments fail before storage is accessed. The same description rules are applied when loading a manually edited ledger, so unsafe stored text is not printed into the table. There is no automatic sanitization that might silently change an existing task.

## JSON storage format

The file is UTF-8 JSON without a byte-order mark, indented for inspection and terminated with a newline. A minimal nonempty ledger looks like this:

```json
{
  "version": 1,
  "next_id": 2,
  "tasks": [
    {
      "id": 1,
      "description": "Book the dentist",
      "done": false
    }
  ]
}
```

Root fields must be exactly `version`, `next_id` and `tasks`. Task fields must be exactly `id`, `description` and `done`. Version must be integer 1, IDs must be unique positive integers, and `next_id` must be a positive integer greater than every surviving ID. JSON booleans are not accepted as integers. Duplicate object keys, unknown fields, missing fields, wrong types, invalid encoding and unsupported versions are errors. Stored descriptions must already be trimmed. An empty ledger still retains its saved counter.

You may inspect the JSON directly, but copying a backup is safer than editing it in place. If you edit, stop all writers first and retain the previous `next_id`; historical deleted IDs cannot be reconstructed from the remaining rows. There is no migration, import or automatic repair command.

## Atomic writes and concurrent commands

Mutations take an exclusive advisory lock, then load, validate, change and save the ledger while holding that lock. Concurrent Taskman writers using the same path are serialized, preventing duplicate IDs and lost additions. A busy writer waits up to **five seconds** by default, then exits 1 with retry guidance. Use `--lock-timeout 0` to fail immediately, or a finite nonnegative value such as `--lock-timeout 20` to wait longer. This limits lock waiting, not the duration of filesystem operations. Read-only commands ignore this option and do not take a lock: they see the complete version before or after a replacement, not a partially written JSON file.

The sidecar is normally `PATH.lock`. For store basenames longer than 240 filesystem-encoded bytes, it is `.taskman-<sha256>.lock` beside the store; the digest is SHA-256 of the basename's filesystem bytes. Temporary files use the short `.taskman-` prefix. Thus a valid near-limit store filename still works. These generated lock names are reserved, just like ordinary `.lock` sidecars. Do not mix writer versions while a collection is in use.

Saving writes a temporary file in the same directory, flushes it, calls `fsync`, closes it and replaces the ledger with `os.replace`. Success is printed only after saving succeeds. A handled failure before replacement leaves the previous ledger intact and removes the temporary file. New stores and lock files are owner-only (`0600`, subject to umask). Existing basic read/write/execute permission bits are preserved during replacement, and an existing read-only ledger is not silently overwritten. The containing directory must allow temporary-file creation and replacement.

The lock file normally remains on disk; it is not an abandoned job indicator. The operating system releases the lock when a process exits. Do not delete the sidecar while writers are running, as that could let new processes use a different lock. Do not use symlinks, directories, pipes or devices as stores. Symlink lock paths are also rejected. Parent-directory symlinks are allowed, but use a consistent path and do not rename directories while commands are active.

These guarantees are for cooperating same-user processes on a local POSIX filesystem. They do not cover external editors ignoring locks, hostile directory modifications, hard-link aliases, network filesystem behavior or physical power loss. The parent directory is not fsynced; atomic visibility is not a promise of power-loss durability. Forced termination before replacement can leave a temporary file. Atomic replacement does not preserve ownership, ACLs, extended attributes or hard-link identity. Back up important ledgers independently.

## Errors and recovery

Successful output goes to stdout and is explicitly flushed. Diagnostics go to stderr, include the selected store for storage/task failures, and do not show a Python traceback for expected errors. Invalid stored tasks are identified by their one-based array position and, when valid, their ID. A closed output pipe exits 1 quietly, including for help output. Other output failures warn that the task may already be saved. Ctrl-C exits 130 with a short message, releases the lock and cleans up a temporary file if replacement has not occurred. Always inspect the same store before retrying an interrupted mutation.

| Exit code | Meaning |
| --- | --- |
| `0` | Success, including empty results and duplicate completion. |
| `1` | Storage/permission failure, corrupt data, unknown task ID, busy lock or output failure. |
| `2` | Invalid command syntax, description, ID, filter, width, timeout or path argument. |
| `130` | Interrupted with Ctrl-C; check whether the mutation committed before retrying. |

For a corrupt file, Taskman refuses **all** commands that read tasks and leaves its bytes unchanged. Help still works. Make a backup before manually correcting the reported format issue or restoring a known-good version. Do not delete a corrupt file just to silence an error unless you deliberately want to discard that ledger and reset its ID history. An empty file is corrupt JSON, not an empty ledger.

For a permission error, check the JSON file, its parent directory and the `.lock` sidecar. Read-only `list` and `stats` can work when mutation is disallowed. Do not work around access failures by running Taskman as root. Unknown IDs can be checked with `list --status all` against the **same** `--store` path.

### Back up and restore deliberately

Continue the isolated demo in the same shell. **Stop all writers before copying or restoring a ledger.** These examples are not a substitute for the live writer lock: copying a snapshot is safe, but restoring it while another process writes can lose work. Keep the complete file, including `next_id`, rather than copying individual rows. Restoring an older backup also restores its older counter and history.

```sh
# walkthrough
BACKUP="$(mktemp "$DEMO_DIR/tasks.backup.XXXXXX")"
# Only restore after both the copy and validation succeed.
cp -p "$STORE" "$BACKUP" &&
python3 taskman.py --store "$BACKUP" list && {
    RESTORE="$(mktemp "$DEMO_DIR/restore.XXXXXX")"
    cp -p "$BACKUP" "$RESTORE" &&
    mv -f "$RESTORE" "$STORE" &&
    python3 taskman.py --store "$STORE" stats
}
```

Keep a backup before any manual correction. The `list` check above refuses malformed backups without changing their bytes. The final rename replaces the selected demo ledger in one operation; it is not a promise of power-loss durability.

When you are finished with this demo and no writers are active, the following removes only the named demo files and requires the directory to be empty. It does not delete the separate personal ledger from the home-directory example.

```sh
# walkthrough
rm -- "$STORE" "$STORE.lock" "$BACKUP"
rmdir -- "$DEMO_DIR"
unset STORE BACKUP DEMO_DIR RESTORE
```

## Run the tests

```sh
python3 -m unittest -v
python3 -m py_compile taskman.py test_taskman.py
python3 -m unittest -v test_taskman.ProjectContractTests
```

CLI tests launch subprocesses and use temporary stores, including a private working directory for the default-path test. They never use your personal ledger. Coverage includes CLI help, argument positions, validation, Unicode, stable IDs, filters, counts, file modes, corruption preservation, nonregular paths, concurrent writers, deadlines, interrupts, wrapped output and write failures. Real permission tests require a non-root POSIX user and are explicitly skipped under root. Fault-injection cases run inside a child process; readiness markers make interrupt and replacement-boundary checks deterministic.

The project-contract tests execute every shell block marked `# walkthrough` in order, with HOME and TMPDIR isolated, compare the two displayed tables, validate the JSON example, and check the documentation's measured counts and review evidence. These checks run with the full suite too; the final command is a focused, reproducible documentation audit.
