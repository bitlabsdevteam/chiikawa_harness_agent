"""The Jev ablation scorer: metrics match hand computation and the gates trip on wrong deciders."""

import json
import unittest

from chiikawa import jev
from demos import bench_jev
from demos import jev_metrics as metrics
from demos.make_jev_gold import PERMISSIONS, visibility_traces


class MetricTests(unittest.TestCase):
    def test_wilson_and_rate_edges(self):
        self.assertEqual(metrics.wilson(0, 0), (0.0, 1.0))
        low, high = metrics.wilson(8, 10)
        self.assertAlmostEqual(low, 0.4902, places=3)
        self.assertAlmostEqual(high, 0.9433, places=3)
        self.assertIsNone(metrics.rate(0, 0)["rate"])

    def test_permission_metrics_by_hand(self):
        rows = [{"gold": "deny", "pred": "allow"}, {"gold": "deny", "pred": "deny"},
                {"gold": "ask", "pred": "allow"}, {"gold": "ask", "pred": "ask"},
                {"gold": "allow", "pred": "ask"}, {"gold": "allow", "pred": "allow"}]
        result = metrics.permission_metrics(rows)
        self.assertEqual(result["accuracy"]["k"], 3)
        self.assertEqual(result["false_allow_deny"]["rate"], 0.5)
        self.assertEqual(result["false_allow_ask"]["rate"], 0.5)
        self.assertEqual(result["deny_recall"]["rate"], 0.5)
        self.assertEqual(result["friction"]["rate"], 0.5)
        self.assertEqual(result["confusion"]["deny"], {"allow": 1, "ask": 0, "deny": 1})

    def test_visibility_metrics_and_brier(self):
        rows = [{"needed": 2, "survived": 1, "irrelevant": 4, "reduced": 3, "full_chars": 400, "view_chars": 100},
                {"needed": 1, "survived": 1, "irrelevant": 0, "reduced": 0, "full_chars": 400, "view_chars": 300}]
        result = metrics.visibility_metrics(rows)
        self.assertEqual(result["needed_recall"]["rate"], 2 / 3)
        self.assertEqual((result["full_tokens"], result["view_tokens"]), (200, 100))
        self.assertEqual(result["token_reduction"], 0.5)
        self.assertEqual(result["traces_with_all_needles"]["k"], 1)
        self.assertAlmostEqual(metrics.brier([1.0, 0.0, 0.5], [1, 1, 0]), (0 + 1 + 0.25) / 3)
        self.assertEqual(metrics.percentile([5, 1, 3, 2, 4], .5), 3)


class GoldTests(unittest.TestCase):
    def test_gold_sets_are_sized_labelled_and_synthetic(self):
        self.assertGreaterEqual(len(PERMISSIONS), 100)
        self.assertEqual({r["label"] for r in PERMISSIONS}, {"allow", "ask", "deny"})
        traces = visibility_traces()
        self.assertEqual(len(traces), 40)
        for trace in traces:
            needed = [c for c in trace["chunks"] if c["needed"]]
            self.assertTrue(needed)
            self.assertTrue(all(c["needle"] in c["text"] for c in needed))
            self.assertTrue(any(len(c["text"]) >= jev.MIN_CHUNK_CHARS for c in trace["chunks"]))
        self.assertEqual(visibility_traces(), traces)  # deterministic
        self.assertNotIn("sk-", json.dumps([r["args"] for r in PERMISSIONS if r["label"] != "deny"]))


class RunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = [dict(r, id=f"p{i}") for i, r in enumerate(PERMISSIONS)]
        cls.traces = visibility_traces()[:6]

    def permissions(self, rule):
        return bench_jev.run_permissions(jev.FakeJev(bench_jev.permission_handler(self.rows, rule)), self.rows)

    def test_baseline_gate_reflects_regex_gaps(self):
        yolo = bench_jev.run_permissions(None, self.rows, "yolo")
        safe = bench_jev.run_permissions(None, self.rows, "safe")
        self.assertEqual(bench_jev.permission_gate(yolo["metrics"]), "FAIL")
        self.assertEqual(yolo["by_category"]["dangerous_pattern"]["rate"], 1.0)
        self.assertEqual(yolo["by_category"]["hard"]["rate"], 0.0)
        self.assertEqual(bench_jev.permission_gate(safe["metrics"]), "PASS")
        self.assertEqual(safe["metrics"]["friction"]["rate"], 1.0)

    def test_oracle_passes_and_wrong_decider_trips_the_gate(self):
        oracle = self.permissions(lambda r: (r["label"], 0.9 if r["label"] == "deny" else 0.1))
        self.assertEqual(bench_jev.permission_gate(oracle["metrics"]), "PASS")
        self.assertEqual(oracle["metrics"]["accuracy"]["rate"], 1.0)
        self.assertGreater(oracle["jev"]["input_tokens"], 0)
        self.assertTrue(oracle["jev"]["simulated"])
        wrong = self.permissions(lambda r: ("allow", 0.0))
        self.assertEqual(bench_jev.permission_gate(wrong["metrics"]), "FAIL")

    def test_advisor_never_overrides_the_denylist(self):
        wrong = self.permissions(lambda r: ("allow", 0.0))
        by_id = {r["id"]: r for r in wrong["rows"]}
        patterned = [r for r in self.rows if r["category"] == "dangerous_pattern"]
        self.assertTrue(all(by_id[r["id"]]["pred"] == "deny" for r in patterned))
        self.assertLess(wrong["jev"]["calls"], len(self.rows))  # denylisted calls never reach the advisor

    def test_visibility_gate_and_accounting(self):
        needed = {c["text"][:200] for t in self.traces for c in t["chunks"] if c["needed"]}
        good = bench_jev.run_visibility(jev.FakeJev(bench_jev.visibility_handler(
            lambda q, e: "full" if e[:200] in needed else "hide")), self.traces)
        bad = bench_jev.run_visibility(jev.FakeJev(bench_jev.visibility_handler(lambda q, e: "hide")), self.traces)
        self.assertEqual(bench_jev.visibility_gate(good["metrics"]), "PASS")
        self.assertEqual(bench_jev.visibility_gate(bad["metrics"]), "FAIL")
        self.assertLess(good["metrics"]["view_tokens"], good["metrics"]["full_tokens"])
        self.assertEqual(good["net_tokens_saved_per_request"],
                         good["metrics"]["full_tokens"] - good["metrics"]["view_tokens"] - good["jev"]["input_tokens"])

    def test_compaction_baseline_counts_the_summary_call(self):
        result = bench_jev.compaction_baseline(self.traces, 100)
        self.assertGreater(result["summary_call_input_tokens"], 0)
        self.assertLess(result["after_tokens_assumed_summary"], result["history_tokens"])


class LiveHelperTests(unittest.TestCase):
    def test_request_cap_is_hard(self):
        capped = bench_jev.Capped(jev.FakeJev(lambda s, q: {}), 2)
        capped.decide("s", {"a": {}})
        capped.decide("s", {"a": {}})
        with self.assertRaises(jev.JevError):
            capped.decide("s", {"a": {}})

    def test_llm_judge_parses_typed_answers_and_meters_usage(self):
        class Backend:
            def complete(self, model, system, messages, tools, **options):
                return {"text": 'Sure: {"d": "deny", "r": 0.8, "s": 2}', "tool_calls": [],
                        "usage": {"input": 120, "output": 9}}
        judge = bench_jev.LlmDecider(Backend(), "m")
        answers = judge.decide("state", {"d": {"type": "choice"}, "r": {"type": "noul"}, "s": {"type": "score"}})
        self.assertEqual((answers["d"]["choice"], answers["r"]["noul"], answers["s"]["score"]), ("deny", 0.8, 2.0))
        self.assertEqual(judge.meter.summary()["input_tokens"], 120)

        class Broken:
            def complete(self, *args, **kwargs):
                return {"text": "no json", "tool_calls": []}
        with self.assertRaises(jev.JevError):
            bench_jev.LlmDecider(Broken(), "m").decide("s", {"d": {"type": "choice"}})

    def test_live_requires_a_judge_model(self):
        with self.assertRaises(SystemExit):
            bench_jev.main(["--live"])


if __name__ == "__main__":
    unittest.main()
