# Day 4: durable state and delegation boundaries

This module implements the specified local JSONL protocol and composes the
existing Chiikawa modules. It follows the state-preservation principles in
[OpenAI's conversation-state guidance](https://developers.openai.com/api/docs/guides/conversation-state),
[function-calling documentation](https://developers.openai.com/api/docs/guides/function-calling),
and [reasoning-state guidance](https://developers.openai.com/api/docs/guides/reasoning#keeping-reasoning-items-in-context).
It remains a small local harness, not a distributed workflow engine.

## Journal and model context have different lifetimes

The JSONL journal records the original user messages, assistant responses, and
tool results. Every append is locked, flushed, and synced before observers see
the corresponding event. The assistant's declared tool calls are therefore
durable before any tool runs. Provider output, including opaque reasoning,
message phase metadata, and function call IDs, is serialized unchanged.

Compaction affects only the model's current working view. The journal keeps
the full original history. The recording cursor moves to the end of the
replacement view, so retained messages are not appended a second time and
the next genuine message is not skipped. Summaries are derived context, not
new journal events. Resuming replays the full journal and can compact again.

The pre-turn hook also flushes the loop's final turn-limit user message before
the last provider call. Storage errors propagate before further model/tool
execution; no durability success is reported on a failed sync.

## Torn tails and missing results are separate repairs

`load` reads the valid JSONL prefix under a shared lock. At the first malformed
JSON/UTF-8 line, it stops; later lines are not trusted. It returns a repaired
in-memory history without changing the file. Well-formed JSON with an invalid
message shape raises an error rather than silently becoming model input.

The last assistant's tool calls are paired with the sequential tool results
already recorded after it. Missing results receive the exact specified notice:

> Interrupted before this ran (process restarted).

Foundry `call_id` values are retained in these synthetic results. `Harness.resume`
sets its cursor to the number of actually persisted records and appends only
the repair records. It never re-appends the restored history. Before appending,
the writer truncates any damaged tail; otherwise all later records would remain
unreachable behind the first malformed line.

**The notice does not prove the tool had no side effects.** A process can die
after a write or external action succeeds but before its result is logged.
The string is retained for assignment compatibility. Recovery must inspect
actual state before retrying; the live demo supplies that guidance and verifies
that the resumed agent inspects the workspace. Exactly-once external effects
would require tool-specific idempotency keys or transactional integration.

## Ownership, durability, and scale

- One active Harness run owns a conversation/session at a time. File locks
  serialize individual records; they do not coordinate multiple agent loops
  trying to advance the same conversation. Do not share one session between
  concurrent runs without an external ownership mechanism.
- Session files are created with mode `0600` and reserved exclusively using a
  fractional Unix timestamp plus the sanitized label. Default session paths
  cannot escape the workspace via a directory symlink.
- Every append syncs the file and its parent directory. This is tested against
  process termination and storage-error propagation, not simulated power loss
  or every network filesystem's durability behavior.
- The small reference implementation validates the existing prefix before
  each append. This prioritizes recovery correctness over high-volume append
  performance and can cause quadratic total parsing work for large journals.
  A production service with long histories should use transactional storage
  or maintain a validated append offset under exclusive session ownership.
- Explicit session paths are trusted caller configuration. Workspace path
  checks do not protect against malicious concurrent filesystem replacement.
- `.chiikawa/` is ignored by Git, but journals contain user/tool data and opaque
  provider state. They are not encrypted at rest. Applications must set retention
  and access controls and must not treat the shell tool as a sandbox.

The session module is larger than the original approximate line target because
it includes locks, disk sync, private creation, semantic checks, and writable
tail recovery rather than omitting those failure cases for brevity.

## Children are bounded, ephemeral conversations

The `spawn_agent` description requires a self-contained task and explicitly
states that the child cannot see the parent's conversation. Every invocation
constructs a fresh Harness with empty history. Children inherit the workspace,
model, policy object, extra tools, system customization, observer, budget, and
turn limit. They may inspect shared workspace files; context isolation is not
filesystem isolation.

Children use `persist=False` and receive no parent session path. No child or
grandchild journal can become `latest()` and hijack resume. The depth check
runs before child construction: depths 0 and 1 may delegate, depth 2 may not.
Only the child's final report becomes the parent's tool result. Parent and
child visible callbacks can be interleaved; the persistent parent journal is
the authoritative attribution source for parent actions.

Delegation is synchronous and sequential through the existing loop. This does
not add parallel scheduling, cancellation propagation, filesystem transactions,
or automatic conflict resolution between tasks. The caller's policy remains
authoritative; a child cannot escape a read-only policy just by receiving a task.

## Verification uses process and filesystem evidence

Offline tests cover torn tails, missing results, sync errors, call IDs, complete
provider replay, concurrent record appends, resume idempotence, compaction
cursors, and child lifecycle/policy bounds. The live verifier creates a worker,
waits for a persisted mid-task tool boundary, kills only that worker with
SIGKILL, and resumes in a different process. A separate live case checks two
delegations, five generated assertions, parent-side test execution, and exactly
one session journal. Visible evidence omits opaque provider state and keys;
raw local journals retain the state required for replay.
