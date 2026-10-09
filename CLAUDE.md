# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working in this repository.

**Read [AGENTS.MD](AGENTS.MD) first.** It is the authoritative agent guide (repository map, invariants, Jail/Sandbox rules, verification matrix, packaging). This file is a condensed entry point; if the two disagree, AGENTS.MD wins. Update AGENTS.MD (not just this file) when public behavior, commands, or architecture change.

## What this is

Chiikawa is a small, observable, resumable coding-agent harness (`chiikawa-harness`). Python 3.10+, **no third-party runtime dependencies** (optional `enterprise` extra: `cryptography`). Supports Microsoft Foundry and OpenRouter, six core tools, approval policies, durable JSONL sessions, context compaction, bounded child agents, fleets, and optional Docker isolation. Hosts: macOS, Linux, WSL (POSIX primitives are intentional). Do not swap in an agent framework or provider SDK.

## Commands

Run from the repo root; no install or venv needed to run from source.

```sh
python3 -m chiikawa --help
python3 -m chiikawa -d ./scratch                       # interactive; no credentials needed to start
python3 -m unittest discover -s tests -v               # full offline suite
python3 -m unittest discover -s tests -p 'test_isolation.py' -v   # single test file
python3 -m unittest tests.test_day2.SomeClass.test_name           # single test (run from repo root)
git diff --check
```

- Always use discovery with `-s tests` (Docker tests import terminal helpers from that directory).
- Docker tests are skipped unless `CHIIKAWA_TEST_DOCKER=1`; build the image only via explicit `python3 -m chiikawa --sandbox-setup`. Never claim skipped Docker tests as boundary verification.
- Packaging: `python3 -m build && python3 scripts/build_release.py && python3 scripts/check_dist.py`. Never hand-edit `dist/`, `build/`, `*.egg-info/`.
- No formatter/linter is configured. Test-to-area mapping is in AGENTS.MD ("Verification by change type").

## Architecture (big picture)

- `cli.py` / `commands.py` / `prompt.py` / `terminal.py` / `transcript.py`: CLI, slash commands, line editor, activity display.
- `harness.py`: composition root; one Harness = one conversation, one run at a time. `loop.py`: sequential model/tool loop with a policy hook before each tool call.
- `providers.py` selects `provider.py` (Foundry Responses API, SSE streaming) or `openrouter.py`. Credentials never cross providers.
- `tools.py` (six core tools), `security.py` (approval policy + shell denylist), `runtime.py` + `sandbox_worker.py` + `Sandbox.Dockerfile` (Jail/Docker runtimes).
- `session.py` (locked, durable JSONL journals), `context.py` (compaction), `memory.py` (`CHIIKAWA.md`), `skills.py` (`skills/*/SKILL.md`), `subagent.py`, `fleet.py`.
- `system_policy.py` + `SYSTEM_PROMPT.md`: application-owned protected policy; project text never goes into provider system fields.
- `enterprise_*.py`: enterprise profile/service modules (identity, gateway, quota, policy, jail, admin, etc.). These are **not listed in AGENTS.MD's repository map**; read the module and its `tests/test_enterprise_*.py` before editing.

## Key invariants (details in AGENTS.MD)

- Policy returns `None` to allow; anything else (even `""`) blocks. Approvals accept literal `True` or `Approval.ALL` only. CLI defaults: `safe` interactive, `yolo` for `-p`; `Harness()` defaults to `yolo`.
- Jail restricts file tools only; shell runs with host permissions. Sandbox failures must never fall back to Jail; do not weaken protections to make tests pass.
- Journals: flush before exposing events or running tools; compaction replaces the working view, not the journal.
- Preserve exit codes (0 / 1 / 2 / 130), terminal restoration, and escaping of untrusted terminal controls.
- Package import must not read credentials, touch the network, or create sessions.

## JEV wiki reference

- Any implementation work regarding JEV must first consult the wiki index at `/Users/davidbong/Documents/my_second_brain_vault/index.md` and follow the pages it links to.

## Safety and scope

- Never print or inspect `.env`, `credential.md`, or `.chiikawa/` contents; use synthetic sentinel secrets in tests. Do not source `.env`.
- `scratch/`, `output/`, `products/` may hold user work; don't delete or reformat them. `docs/day*-verification.md` are historical, not proof of current behavior.
- Don't commit, push, tag, publish, or run credentialed/billable demos (`demos/verify_day*.py`, live model prompts) unless the user asks.
- Check `git status --short` first and preserve existing edits.
