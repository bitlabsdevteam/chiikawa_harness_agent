# Jev ablation: offline report

**Read first.** Offline, only the built-in regex baseline is a real measurement. Every `stand-in` row is a scripted or heuristic decider that validates the scorer and bounds token savings. It says nothing about real Jev accuracy or latency, and wall-clock time is deliberately not reported. Real Jev and LLM-judge accuracy, tokens, and latency need `--live`.

Gold sets: 122 permission calls, 40 visibility traces (synthetic, labels fixed before any run; see `demos/make_jev_gold.py`).

## Permissions (policy mode: yolo, the headless default)

| Arm | Gate | Accuracy | Deny→allow | Ask→allow | Deny recall | Friction (allow blocked) | Jev calls | Jev input tokens |
|---|---|---|---|---|---|---|---|---|
| regex yolo (measured) | FAIL | 48% (59/122, CI 40%-57%) | 65% (28/43, CI 50%-78%) | 100% (35/35, CI 90%-100%) | 35% (15/43, CI 22%-50%) | 0% (0/44, CI 0%-8%) | - | - |
| regex safe (measured) | PASS | 41% (50/122, CI 33%-50%) | 0% (0/43, CI 0%-8%) | 0% (0/35, CI 0%-10%) | 35% (15/43, CI 22%-50%) | 100% (44/44, CI 92%-100%) | - | - |
| jev-shaped oracle (upper bound) | PASS | 100% (122/122, CI 97%-100%) | 0% (0/43, CI 0%-8%) | 0% (0/35, CI 0%-10%) | 100% (43/43, CI 92%-100%) | 0% (0/44, CI 0%-8%) | 107 | 15775 |
| jev-shaped always-allow (wrong on purpose) | FAIL | 48% (59/122, CI 40%-57%) | 65% (28/43, CI 50%-78%) | 100% (35/35, CI 90%-100%) | 35% (15/43, CI 22%-50%) | 0% (0/44, CI 0%-8%) | 107 | 15775 |
| jev-shaped keyword stand-in | FAIL | 92% (112/122, CI 86%-95%) | 9% (4/43, CI 4%-22%) | 3% (1/35, CI 1%-15%) | 79% (34/43, CI 65%-89%) | 0% (0/44, CI 0%-8%) | 107 | 15775 |

Jev advice can only tighten a decision, so it cannot reduce prompts in `safe` mode. Its measurable value is catching what the regex denylist misses in `yolo`. Per-category accuracy for the regex baseline: dangerous_pattern 100% (15/15, CI 80%-100%), hard 0% (0/28, CI 0%-12%), network_or_install 0% (0/20, CI 0%-16%), outside_project 0% (0/15, CI 0%-20%), safe 100% (44/44, CI 92%-100%)

## Context visibility

| Arm | Gate (needed recall ≥ 95%) | Needed-chunk recall | Irrelevant chunks reduced | Tokens: full → view | Reduction | Jev calls | Jev input tokens | Net tokens saved / request |
|---|---|---|---|---|---|---|---|---|
| jev-shaped oracle on excerpt (upper bound) | PASS | 100% (52/52, CI 93%-100%) | 100% (160/160, CI 98%-100%) | 185,322 → 53,224 | 71% | 40 | 93,530 | 38,568 |
| jev-shaped hides needed (wrong on purpose) | FAIL | 0% (0/52, CI 0%-7%) | 100% (160/160, CI 98%-100%) | 185,322 → 5,136 | 97% | 40 | 93,530 | n/a (gate failed) |
| jev-shaped lexical stand-in | FAIL | 46% (24/52, CI 33%-59%) | 100% (160/160, CI 98%-100%) | 185,322 → 24,344 | 87% | 40 | 93,530 | n/a (gate failed) |
| jev-shaped short for all (head/tail only) | FAIL | 17% (9/52, CI 9%-30%) | 100% (160/160, CI 98%-100%) | 185,322 → 34,737 | 81% | 40 | 93,530 | n/a (gate failed) |

Current path (LLM summarization at the threshold, measured arithmetic): the summarization call alone reads 111,981 input tokens across these traces, on top of a 202,792-token history; the summary length (800 tokens each) is an assumption. Needle survival: not measurable offline (needs a live summarizer).

Jev input tokens are the documented `ceil(len/4)` estimate of the real request payload, so the token overhead column is what a live run would bill, while the saving depends on the (stand-in) decisions. Tokens are not price-equivalent: Jev's input tokens and the main model's cost different amounts, so a live run should also compare dollars. Savings are shown only when the accuracy gate passes.

## What this does and does not show

- The scorer, gates (deny→allow, needed-chunk recall), token accounting, and request batching work.
- The regex baseline's real gaps on this gold set are in the table above.
- `oracle` rows are upper bounds; `always-allow` and `hides needed` must trip the gates (they do).
- The `lexical stand-in` shows the blind spot of head/tail excerpts: needles in the middle of long outputs can be missed.
- Not measured offline: real Jev accuracy, calibration, latency, LLM-judge comparison, end-task success.
