# Taskman interaction design

## Subject and scope
A local task ledger for a terminal: capture a task, find its permanent ID, finish it, and remove it deliberately. This is a CLI, not a landing page; web section, prose, CSS, SVG and viewport quotas do not apply.

## Compact design plan
- **Semantic palette (four tokens):** ink = primary content; muted = table rules; success = completed state; danger = errors. All four render as plain terminal text, never color alone. No ANSI escapes or terminal theme assumptions.
- **Type roles:** terminal monospace throughout; command names in help headings, right-aligned numeric IDs/counts, left-aligned state labels, unabridged descriptions. Documentation uses short headings, prose and copyable shell blocks.
- **Wireframe:** `taskman.py [--store PATH] COMMAND …`; result/confirmation on stdout; `taskman: error: …` on stderr; list header → bounded rule → one task row with indented continuations as needed; stats header → rule → counts; empty states explain the next command.
- **Signature:** a narrow permanent-ID rail next to literal `open` / `done` states. IDs remain meaningful after deletion, so a task can be addressed without counting rows.
- **Interaction contract:** five focused subcommands; isolated stores; atomic mutation; predictable exit codes; idempotent completion; no automatic repair of malformed storage; help available without touching a store.

## First design-director review and revision (before implementation)
The initial concept of a colorful dashboard, checkmark badges and boxed tables would be generic decoration rather than task utility. Revise to a quiet, unboxed ledger with ASCII separators and explicit state words. Do not hide long descriptions behind ellipses. Avoid a surprising default filter: `list` shows every task, with an explicit state filter. Put success messages on one line with the actual affected ID. Reserve stderr for actionable failures, and show an add example when a store is empty. Store format and failure guarantees must be documented, not inferred from UI decoration.

## Renewed critique and revised terminal plan
The quiet ledger is task-specific, but monochrome alone is not sufficient design. The previous plan did not specify narrow-terminal behavior, waiting feedback, interruption, stream failure, or how recovery examples would be verified. Revise the existing design rather than decorating it: retain the four semantic tokens and the permanent-ID rail; add an indented continuation rail for long descriptions. No text is discarded, and continuation lines never masquerade as new tasks.

- **Layout:** `list --width COLUMNS` defaults to 80 columns, minimum 24. Wrap only task descriptions; keep IDs and state words on the first physical line. Count wide CJK characters as two cells and combining marks as zero; keep joined emoji together. Never print an unbounded separator. At extraordinary ID widths, expand the minimum layout to keep the ID intact.
- **Waiting:** writers wait at most five seconds by default; `--lock-timeout SECONDS` accepts finite nonnegative values, including zero for fail-fast. A busy-store error names the store and offers retry guidance, never advice to delete a live lock.
- **Cancellation:** Ctrl-C ends with a short stderr message and exit 130, releases any held lock and removes a pre-replacement temporary file. A cancellation or output failure can occur after a commit; inspect the same store before retrying an addition.
- **Output:** no ANSI colors. Explicitly flush stdout so broken pipes and other stream failures are handled before process shutdown. A closed consumer gets exit 1 without a traceback; output encoding uses backslash escapes for unrepresentable Unicode, never a changed ledger.
- **Recovery:** identify the invalid task by array position and, when valid, ID. Give copyable backup/restore examples with a stopped-writers precondition and prove them in an isolated shell walkthrough.

## Acceptance matrix
| Concern | Concrete acceptance check |
| --- | --- |
| Narrow, normal and wide output | 40-, 80- and 120-column subprocess snapshots; bounded lines and recoverable complete descriptions |
| Multilingual output | CJK, combining marks and joined emoji stay readable; invisible-only descriptions fail |
| Input mistakes | Reject abbreviated long options and non-ASCII-decimal ID spellings before touching storage |
| Responsiveness | Held-lock deadline is bounded; Ctrl-C exits 130 without a traceback |
| Stream composition | Closed stdout and ASCII-only stdout do not crash or damage the ledger |
| Storage guarantees | Partial-write failure cleanup; reader snapshots before and after a paused atomic replacement |
| Documentation | Execute every marked README walkthrough; compare literal tables and validate the schema example |
| Review integrity | Exactly twelve current findings, real test references, AST docstrings and independently recomputed counts |

## Verification plan
The first delivery's second review is complete. REVIEW.md now records the renewed full-file review requested after delivery, with exactly twelve current deficiencies, implemented fixes and named evidence. QUALITY.md records current test commands, measured counts and limitations, not obsolete timings. All six delivered files are in scope: taskman.py, test_taskman.py, README.md, DESIGN.md, REVIEW.md and QUALITY.md. Web quotas remain inapplicable.
