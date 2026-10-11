"""Optional dollar pricing and the CLI guard in the permissions report (offline; uses the stored live report)."""

import contextlib
import io
import json
import unittest
from pathlib import Path

from demos import permissions_report as report_module

REPORT = Path(__file__).resolve().parent.parent / "docs" / "permissions-live.json"


@unittest.skipUnless(REPORT.exists(), "stored live report not present")
class DollarTests(unittest.TestCase):
    def setUp(self):
        self.report = json.loads(REPORT.read_text())

    def derived(self, prices):
        return report_module.derived(self.report, prices)

    def test_no_prices_means_no_dollar_claim(self):
        d = self.derived(None)
        self.assertIsNone(report_module.dollars(self.report, d))
        card = report_module.cost_card(self.report, d, lambda r: 1.0)
        self.assertTrue(card[1].startswith("JEV, unless the LLM input price is under"))
        self.assertIn("needs one number from you", card[3])

    def test_breakeven_llm_price_follows_the_formula(self):
        d = self.derived(None)
        cost = self.report["analysis"]["cost"]
        got = report_module.breakeven_llm_input_price(self.report, d, 0.66, 4)
        self.assertAlmostEqual(got, cost["jev"]["in_mean"] * 0.66 / (cost["llm"]["in_mean"] + d["out_mean"]["llm"] * 4))
        self.assertGreater(report_module.breakeven_llm_input_price(self.report, d, 0.66, 1),
                           report_module.breakeven_llm_input_price(self.report, d, 0.66, 8))
        self.assertEqual(report_module.JEV_PRICING[0], ("Starter pack", 0.66))

    def test_hypothetical_scenarios_use_measured_tokens_and_ordered_tiers(self):
        d = self.derived(None)
        cost = self.report["analysis"]["cost"]
        tier = report_module.SCENARIOS[1]
        llm, jev = report_module.scenario_costs(self.report, d, tier, 0.66)
        self.assertAlmostEqual(llm, 1000 * (cost["llm"]["in_mean"] * tier[1] + d["out_mean"]["llm"] * tier[2]) / 1e6)
        self.assertAlmostEqual(jev, 1000 * cost["jev"]["in_mean"] * 0.66 / 1e6)
        llms = [report_module.scenario_costs(self.report, d, t, 0.66)[0] for t in report_module.SCENARIOS]
        self.assertEqual(llms, sorted(llms))
        self.assertIn("HYPOTHETICAL", report_module.fig_scenarios(self.report, d))

    def test_dollars_per_thousand_decisions_follow_the_formula(self):
        prices = {"jev": 3.0, "llm_in": 5.0, "llm_out": 20.0}
        d = self.derived(prices)
        cost = self.report["analysis"]["cost"]
        money = report_module.dollars(self.report, d)
        self.assertAlmostEqual(money["jev"], 1000 * cost["jev"]["in_mean"] * 3.0 / 1e6)
        self.assertAlmostEqual(money["llm"], 1000 * (cost["llm"]["in_mean"] * 5.0 + d["out_mean"]["llm"] * 20.0) / 1e6)

    def test_card_names_the_cheaper_option_at_the_given_prices(self):
        cheap_jev = report_module.cost_card(self.report, self.derived({"jev": 0.1, "llm_in": 5.0, "llm_out": 20.0}), lambda r: 1.0)
        dear_jev = report_module.cost_card(self.report, self.derived({"jev": 50.0, "llm_in": 5.0, "llm_out": 20.0}), lambda r: 1.0)
        self.assertTrue(cheap_jev[1].startswith("JEV"))
        self.assertTrue(dear_jev[1].startswith("LLM JUDGE"))
        self.assertIn("ESTIMATED", cheap_jev[3])
        self.assertIn("at estimated prices", cheap_jev[1])

    def test_cli_requires_all_three_prices_or_none(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            report_module.main(["--report", str(REPORT), "--jev-usd-per-mtok", "3"])
        self.assertEqual(caught.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
