# Day 5: CLI and fleet production boundaries

Chiikawa remains a manual Responses API orchestrator. The application owns the
conversation, tool execution, persistence and concurrency; it does not claim
the hosted runtime, tracing service or lifecycle guarantees of an agent platform.
This follows OpenAI's [agent architecture guidance](https://developers.openai.com/api/docs/guides/agents).
The Foundry deployment remains **gpt-6-astra**.

## Explicit entry-point policy

The interactive CLI defaults to safe mode and requires a literal `y` for
approval. Empty input and EOF refuse. The specified headless default is yolo;
operators can choose `--mode safe` or `--mode read-only`. The underlying shell
tool is not a process sandbox. A directory resolver and command denylist cannot
replace OS/container isolation or least-privilege credentials. See OpenAI's
[agent safety guidance](https://developers.openai.com/api/docs/guides/agent-builder-safety).

Routine event output uses bounded one-line argument summaries and first-line
results. Approval prompts show complete arguments so the reviewer can see the
entire operation, including any command tail. This reduces terminal noise but is not secret redaction: tool
inputs and outputs may contain sensitive data. Credentials are loaded only by
the explicitly selected demo loader and are not included in product prompts.
Opaque provider reasoning is never printed.

Ctrl-C stops the active shell process group, exits with status 130 and points to
resume. A real SIGINT regression verifies that a delayed child write cannot
survive interruption. Persistence occurs before
tool dispatch, and recovery repairs incomplete call/result pairs. A journal
cannot roll back side effects; inspect interrupted operations before repeating
them. Missing resume sessions fail instead of silently starting a new task.

## Bounded concurrency and ownership

`run_fleet` uses `ThreadPoolExecutor` with four workers by default and returns
results in input order. Each job constructs a new harness, and ordinary factory
or run exceptions become that job's failed report. Process-control exceptions
are not swallowed. The executor waits for submitted work before returning.

Callers must provide independent directories or coordinate conflicting writes.
The product demonstration uses three separate workspaces and disables nested
sub-agents. Build and review phases never overlap for the same journal. Review
constructs a new harness and resumes the exact original durable conversation.
There is no distributed queue, global rate limiter, per-job cancellation API or
cross-process session lock. Provider retries handle transient failures; choose
fleet size according to deployment quota and workload.

## Verification, not just completion text

The product runner records model, phase, ordered reports, journal path and
cumulative assistant turns in `day5-fleet-runs.json`. The required second prompt
is sent in the original session. The project skill text is preserved verbatim.
A job reporting `ok: true` means only that its run returned normally. Product
acceptance additionally requires file checks, executable taskman tests, HTML
content measurements and browser behavior checks. Any shortfall triggers a
further harness review in the same session.

The offline harness suite tests actual concurrency using a two-worker barrier,
ordered return despite reversed completion, factory and run exception isolation,
CLI policy defaults, resume, approval and interruption handling. Mocked provider
tests do not substitute for the live Foundry product runs.

New composition stays in the CLI, fleet and product runner. The shell tool also
receives targeted Ctrl-C process-group cleanup so terminal interruption cannot
leave an active command behind. OpenAI's
[function calling guide](https://developers.openai.com/api/docs/guides/function-calling)
and [conversation state guide](https://developers.openai.com/api/docs/guides/conversation-state)
underpin the existing tool-result protocol and explicit state ownership.

## Observed recovery during the product run

The first review fleet completed artisan-coffee while taskman and viper returned
`ConnectionResetError: [Errno 54] Connection reset by peer`. Their independent
failures did not abort the coffee job. Follow-up fleet jobs resume only those two
original journals and finish their existing twelve-item reviews. Both the failed
phase and the recovery remain in the run report.

The provider now retries `ConnectionError` using its existing bounded backoff,
including a reset while reading an established response. A regression test
checks that the failed response is closed and a subsequent response succeeds.
This affects model requests only; it does not replay previously dispatched
local tool side effects.
