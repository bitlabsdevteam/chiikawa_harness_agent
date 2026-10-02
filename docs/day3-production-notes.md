# Day 3 design decisions and production boundaries

This module implements the assignment's explicit contracts using only Python's
standard library and the existing Foundry `gpt-6-astra` provider. Earlier modules
are unchanged. The practices below are grounded in official OpenAI guidance;
the acceptance tests establish the behavior of this implementation, not a
general certification that the entire harness is production-ready.

## Context is state, not a transcript to trim arbitrarily

[OpenAI's compaction guide](https://developers.openai.com/api/docs/guides/compaction)
describes reducing context while retaining state needed for subsequent turns.
This assignment specifically requires a tool-free `provider.complete` call
that summarizes visible history. It does **not** use OpenAI's native
`/responses/compact` endpoint or server-side encrypted compaction.

- The estimator uses the exact requested `len(str(message)) / 4` heuristic,
  including stored provider output. It is not a tokenizer or a context-window
  guarantee, and the system prompt/tool schemas are outside that estimate.
- The 1,500-token value triggers compaction; it is not a hard post-compaction
  limit. Histories of seven or fewer messages are unchanged, as specified.
- The summary instruction explicitly preserves the original task, files and
  their purposes, decisions, unresolved errors, and remaining work.
- Older text is clipped to 2,000 characters per message. Tool names and up to
  1,000 characters of JSON arguments retain filenames and operation context.
  Clipping can omit details; generated summaries remain fallible.
- The recent six-message slice retains original message objects and complete
  opaque provider output. Leading tool results move into the summarized prefix
  rather than disappearing or becoming orphaned API inputs.
- The summarizer receives transcript data in a user message and has no tools.
  Its output is also a user message, never promoted into the system prompt.
  Empty summaries, unexpected calls, and transport failures propagate before
  history is replaced. No success event is emitted for a failed compaction.

[OpenAI's reasoning-state guidance](https://developers.openai.com/api/docs/guides/reasoning#keeping-reasoning-items-in-context)
explains why function-call results and reasoning items must be preserved
together. The existing provider replays retained output unchanged. Old encrypted
reasoning is not decoded, printed, or sent to the summary model as text.

## Persistent memory has an explicit trust boundary

[OpenAI's agent-safety guidance](https://developers.openai.com/api/docs/guides/agent-builder-safety)
warns against putting untrusted data in higher-priority instructions. The
assignment explicitly places `CHIIKAWA.md` in the system prompt. Therefore it
must be **trusted, curated project memory**, not arbitrary scraped content or
unreviewed tool output. The base prompt calls it reference data and does not
grant it permission to override policy, but that wording is not a sandbox or
a complete defense against prompt injection.

- The memory path is canonicalized and cannot point outside the workspace.
- Each call to `build_system_prompt` reads the file anew; a new conversation
  does not inherit hidden in-process state or previous message history.
- Writers use an exclusive POSIX file lock and readers use a shared lock.
  Appends flush and `fsync` the file and its parent directory before success.
- Storage errors propagate. The file remains human-readable and append-only
  through `remember`; repeated calls can intentionally append duplicate notes.
- Locks coordinate this API's callers, not unrelated editors. These checks
  assume a trusted local workspace and do not defend against an attacker racing
  filesystem path replacement. The flat file has no provenance database,
  automatic secret redaction, recovery journal, or distributed consistency.

The demo's `remember` tool is subject to the supplied `Policy`. For real project
use, pass `Policy("safe", approver=...)` and review memory writes. The scratch
demo retains yolo behavior to run the supplied build exercise. The existing
shell tool and denylist are not a filesystem sandbox.

## Skills use progressive disclosure

[OpenAI's skills documentation](https://developers.openai.com/codex/skills)
recommends starting with names/descriptions and loading full instructions only
when a relevant skill is selected.

- Catalog entries advertise only metadata and paths; full instructions enter
  the conversation through `use_skill`, as a tool result.
- Exact catalog lookup prevents `../` skill-name traversal. External symlink
  targets are excluded. Local changes are discovered on the next lookup.
- The small front-matter reader supports one-line quoted/unquoted descriptions
  and simple `>`/`|` blocks. It is not a general YAML parser or full Agent Skills
  validator. Instructions and scripts are never executed by discovery/loading.
- A focused pirate-voice fixture changes a writing task by adding one data file,
  with identical harness source hashes before and after.

## Evidence is part of the feature

OpenAI recommends evaluations and trace inspection for agent behavior. Offline
tests cover failure paths and boundary invariants; live tests also validate
actual files, ordered tool calls, `wc -l` output, a new-process memory recall,
and before/after skill behavior. Events expose compaction counts and estimated
size changes. Saved traces exclude credentials and opaque provider state;
they still contain user text, summaries, and tool output, so production trace
retention and access controls remain an application responsibility.
