# Day 1 verification

- Standard-library offline suite: 13 tests passed on Python 3.14.7.
- All Python files parse with Python 3.10 grammar; execution on Python 3.10
  itself has not been tested. Provider and loop have 103 and 61 lines,
  respectively, within the assignment's approximate size targets.
- Provider: message translation, signature preservation, thought filtering,
  request schema, usage, key selection, HTTP/network retries, and error bodies.
- Loop: dice transcript/event order, text-only coffee response, blocking,
  unknown tools, tool exceptions, sequential execution, history replacement,
  turn exhaustion, and a zero-turn budget.
- Live Gemini acceptance checks: pending credentials. Neither
  `CHIIKAWA_API_KEY` nor `GEMINI_API_KEY` was available during implementation.
  Offline scripted responses do not count as live acceptance evidence.

Run `python3 -m demos.verify_day1` after setting a key. It saves the real dice
and coffee transcripts only after both checks pass. Review the dice arithmetic,
then update this report and commit the live evidence as the final milestone.
