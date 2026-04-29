import importlib.util
import pathlib
import sys
import unittest

import numpy as np
import pandas as pd


SKILL_DIR = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS_DIR = SKILL_DIR / "scripts"


def load_module(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class IndicatorFormulaTests(unittest.TestCase):
    def test_wilder_rsi_bounds_and_trend_response(self):
        indicators = load_module("indicators")
        close = pd.Series(
            [100, 102, 101, 104, 107, 106, 110, 114, 116, 115, 118, 121, 125, 128, 130],
            dtype="float64",
        )

        rsi = indicators.rsi_wilder(close, period=14)

        self.assertGreaterEqual(float(rsi.dropna().iloc[-1]), 50.0)
        self.assertLessEqual(float(rsi.dropna().iloc[-1]), 100.0)

    def test_stoch_rsi_returns_k_and_d_percent_values(self):
        indicators = load_module("indicators")
        close = pd.Series(np.linspace(100, 140, 40) + np.sin(np.arange(40)) * 3)

        stoch = indicators.stoch_rsi(close, period=14, smooth_k=3, smooth_d=3)

        self.assertIn("k", stoch.columns)
        self.assertIn("d", stoch.columns)
        latest_k = float(stoch["k"].dropna().iloc[-1])
        self.assertGreaterEqual(latest_k, 0.0)
        self.assertLessEqual(latest_k, 100.0)

    def test_log_macd_uses_log_prices(self):
        indicators = load_module("indicators")
        close = pd.Series(np.geomspace(100, 400, 80))

        macd = indicators.log_macd(close)

        self.assertIn("histogram", macd.columns)
        self.assertTrue(np.isfinite(macd["histogram"].dropna().iloc[-1]))

    def test_simplified_bbwp_is_percent_ranked(self):
        indicators = load_module("indicators")
        close = pd.Series(100 + np.sin(np.arange(80) / 3) * 5 + np.arange(80) * 0.2)

        bbwp = indicators.simplified_bbwp(close, length=20, lookback=30)

        latest = float(bbwp.dropna().iloc[-1])
        self.assertGreaterEqual(latest, 0.0)
        self.assertLessEqual(latest, 100.0)


class ClassifierTests(unittest.TestCase):
    def test_supply_profit_loss_convergence(self):
        classifier = load_module("classifier")

        result = classifier.supply_profit_loss_state(100.0, 88.0)

        self.assertTrue(result["converged"])
        self.assertFalse(result["loss_exceeds_profit"])
        self.assertAlmostEqual(result["gap"], 0.12)

    def test_right_side_confirmation_can_override_bottom_watch(self):
        classifier = load_module("classifier")
        signals = classifier.SignalState(
            bottom_signals={"near_200w": True, "monthly_stoch_low": True},
            trend_signals={
                "weekly_above_bmsb": True,
                "bmsb_reclaim_with_volume": True,
                "weekly_above_50w": True,
            },
            unavailable_onchain=True,
            data_stale=False,
            reasons=[],
            missing=[],
            next_levels=[],
            context={"price_structure_heavy": True},
        )

        outcome = classifier.classify_market(signals)

        self.assertEqual(outcome["phase"], "Right-Side Confirmation")
        self.assertIn("Trend is improving", outcome["recommended_action"])
        self.assertEqual(outcome["confidence"], "Medium")


if __name__ == "__main__":
    unittest.main()
