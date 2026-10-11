"""Statistics and scorecard logic of the permissions study (offline; no API calls)."""

import math
import unittest

from demos import permissions_study as study


class StatsTests(unittest.TestCase):
    def test_mcnemar_exact_by_hand(self):
        a = [True] * 5 + [False] * 6 + [True] * 10
        b = [False] * 5 + [True] * 6 + [True] * 10
        result = study.mcnemar_exact(a, b)
        self.assertEqual((result["b"], result["c"]), (5, 6))
        self.assertAlmostEqual(result["p"], 1.0)
        self.assertEqual(study.mcnemar_exact([True] * 3, [True] * 3)["p"], 1.0)
        strong = study.mcnemar_exact([True] * 12, [False] * 12)
        self.assertAlmostEqual(strong["p"], 2 * (1 / 2 ** 12))

    def test_paired_diff_matches_the_study_numbers(self):
        # Jev right/LLM wrong on 5, LLM right/Jev wrong on 6, 122 items, as in the live run.
        a = [True] * 5 + [False] * 6 + [True] * 106 + [False] * 5
        b = [False] * 5 + [True] * 6 + [True] * 106 + [False] * 5
        diff = study.paired_diff(a, b)
        self.assertEqual(diff["n"], 122)
        self.assertAlmostEqual(diff["d"], -1 / 122)
        self.assertAlmostEqual(diff["ci95"][0], -0.0615, places=3)
        self.assertAlmostEqual(diff["ci95"][1], 0.0451, places=3)

    def test_reliability_and_bootstrap(self):
        cal = study.reliability([(0.0, 0), (0.1, 0), (0.9, 1), (1.0, 1)], bins=5)
        self.assertEqual(sum(row["n"] for row in cal["bins"]), 4)
        self.assertAlmostEqual(cal["brier"], (0 + 0.01 + 0.01 + 0) / 4)
        low, high = study.bootstrap_median_ci(list(range(1, 100)))
        self.assertLess(low, 50)
        self.assertGreater(high, 50)
        self.assertIsNone(study.bootstrap_median_ci([]))


class ScorecardTests(unittest.TestCase):
    def analysis(self, diff_ci, jev_ms=1000, llm_ms=2000, jev_in=400, llm_in=200):
        rate = lambda k, n: {"k": k, "n": n, "rate": k / n, "ci95": [0, 1]}  # noqa: E731
        metrics = lambda deny_allowed, acc: {"false_allow_deny": rate(deny_allowed, 43), "accuracy": rate(acc, 122)}  # noqa: E731
        arm = lambda deny_allowed, acc: {"metrics": metrics(deny_allowed, acc), "by_category": {"dangerous_pattern": rate(15, 15)}}  # noqa: E731
        cost = lambda ms, tokens: {"ms_p50": ms, "ms_p95": ms * 1.4, "in_mean": tokens, "errors": 0, "calls": 10, "fallback_rate": rate(0, 10)}  # noqa: E731
        return {"arms": {"regex": arm(28, 59), "llm": arm(1, 112), "jev": arm(2, 111)},
                "paired": {"jev_vs_regex": {"mcnemar": {"p": 1e-9}}, "llm_vs_regex": {"mcnemar": {"p": 1e-9}},
                           "jev_vs_llm": {"diff": {"d": 0.0, "ci95": diff_ci, "n": 122}}},
                "cost": {"jev": cost(jev_ms, jev_in), "llm": cost(llm_ms, llm_in)}}

    def test_h2_status_follows_the_interval_against_the_margin(self):
        self.assertEqual(study.scorecard(self.analysis([-0.03, 0.04]), False)["head_to_head"]["h2"]["status"], "non-inferior")
        self.assertEqual(study.scorecard(self.analysis([-0.06, 0.045]), False)["head_to_head"]["h2"]["status"], "inconclusive")
        self.assertEqual(study.scorecard(self.analysis([-0.20, -0.08]), False)["head_to_head"]["h2"]["status"], "inferior")

    def test_h3_is_split_when_faster_but_more_tokens(self):
        h3 = study.scorecard(self.analysis([-0.03, 0.04]), False)["head_to_head"]["h3"]
        self.assertEqual((h3["status"], h3["faster"], h3["fewer_tokens"]), ("split", True, False))
        both = study.scorecard(self.analysis([-0.03, 0.04], jev_in=100), False)["head_to_head"]["h3"]
        self.assertEqual(both["status"], "supported")

    def test_simulated_runs_never_meet_criteria_and_latency_limit_applies(self):
        self.assertFalse(study.scorecard(self.analysis([-0.03, 0.04]), True)["jev"]["meets_criteria"])
        self.assertTrue(study.scorecard(self.analysis([-0.03, 0.04]), False)["jev"]["meets_criteria"])
        slow = study.scorecard(self.analysis([-0.03, 0.04], jev_ms=1600), False)["jev"]
        self.assertFalse(slow["latency"]["pass"])


if __name__ == "__main__":
    unittest.main()
