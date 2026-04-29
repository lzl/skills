"""Market-regime scoring for the when-buy-bitcoin dashboard."""

from __future__ import annotations

import dataclasses
from typing import Any


BOTTOM_WEIGHTS = {
    "weekly_close_below_100w_sma": 1,
    "two_weekly_closes_below_100w_sma": 1,
    "price_near_200w_sma": 1,
    "price_below_200w_sma": 1,
    "price_near_or_below_300w_sma": 1,
    "price_near_or_below_400w_sma": 1,
    "bmsb_50w_bearish_structure": 1,
    "mvrv_z_score_below_0": 2,
    "price_below_realized_price": 1,
    "price_below_balanced_price": 2,
    "supply_profit_loss_converged": 2,
    "supply_loss_exceeds_profit": 3,
    "monthly_stoch_rsi_below_20": 1,
    "two_month_stoch_rsi_k_below_10": 1,
    "lmacd_mature_red": 1,
    "cycle_bottom_window": 1,
}


TREND_WEIGHTS = {
    "weekly_close_above_bmsb_upper": 1,
    "bmsb_reclaim_with_volume": 2,
    "weekly_close_above_50w_sma": 2,
    "golden_cross_50d_200d": 1,
    "bmsb_50w_bullish_structure": 1,
    "holds_above_bmsb_after_retest": 1,
    "weekly_close_above_invalidation_level": 1,
}

SIGNAL_ALIASES = {
    "weekly_close_above_bmsb_upper": ("weekly_above_bmsb", "price_above_bmsb"),
    "weekly_close_above_50w_sma": ("weekly_above_50w", "price_above_50w_sma"),
    "bmsb_50w_bullish_structure": ("bmsb_50w_bullish",),
    "weekly_close_below_100w_sma": ("weekly_below_100w", "price_below_100w_sma"),
    "price_near_200w_sma": ("near_200w",),
}


PHASE_ACTIONS = {
    "Bear Market / Preserve Cash": "Do not rush. Preserve cash. Avoid FOMO on counter-trend rallies.",
    "Capitulation Approach": "Prepare a plan. Watch the 200W SMA and on-chain capitulation signals.",
    "Bottom Watch": "Small DCA only, if this matches your predefined plan. Keep most cash available.",
    "Left-Side Accumulation Zone": "Consider measured, staged accumulation. Do not go all-in. Expect volatility.",
    "High-Conviction Bottom Candidate": "This is a strong bottom-candidate zone. Use a planned ladder; still wait for confirmation before maximum allocation.",
    "Right-Side Confirmation": "Trend is improving. Consider deploying remaining planned allocation in stages.",
    "Bull Recovery / Risk-On Confirmed": "Bear-market bottoming phase may be complete. Shift from bottom-hunting to trend-following risk management.",
    "Data Unavailable": "No action. Re-run when market data is available.",
}


@dataclasses.dataclass(frozen=True)
class SignalState:
    bottom_signals: dict[str, bool]
    trend_signals: dict[str, bool]
    unavailable_onchain: bool
    data_stale: bool
    reasons: list[str]
    missing: list[str]
    next_levels: list[str]
    context: dict[str, Any]


def signal_active(signals: dict[str, bool], name: str) -> bool:
    if signals.get(name):
        return True
    return any(signals.get(alias) for alias in SIGNAL_ALIASES.get(name, ()))


def score_signals(signals: dict[str, bool], weights: dict[str, int]) -> tuple[int, int]:
    score = sum(weight for name, weight in weights.items() if signal_active(signals, name))
    return score, sum(weights.values())


def supply_profit_loss_state(supply_profit: float | None, supply_loss: float | None) -> dict[str, Any]:
    if supply_profit is None or supply_loss is None or max(supply_profit, supply_loss) <= 0:
        return {
            "available": False,
            "gap": None,
            "converged": False,
            "loss_exceeds_profit": False,
        }
    gap = abs(float(supply_profit) - float(supply_loss)) / max(float(supply_profit), float(supply_loss))
    return {
        "available": True,
        "gap": gap,
        "converged": gap <= 0.15,
        "loss_exceeds_profit": float(supply_loss) > float(supply_profit),
    }


def active_signal_count(state: SignalState) -> int:
    return sum(1 for value in state.bottom_signals.values() if value) + sum(
        1 for value in state.trend_signals.values() if value
    )


def determine_phase(bottom_score: int, trend_score: int, state: SignalState) -> str:
    weekly_above_bmsb = signal_active(state.trend_signals, "weekly_close_above_bmsb_upper")
    weekly_above_50w = signal_active(state.trend_signals, "weekly_close_above_50w_sma")
    reclaim_with_volume = signal_active(state.trend_signals, "bmsb_reclaim_with_volume")
    below_100w = signal_active(state.bottom_signals, "weekly_close_below_100w_sma")
    moving_toward_200w = signal_active(state.bottom_signals, "price_near_200w_sma")

    if trend_score >= 6 and weekly_above_bmsb and weekly_above_50w:
        return "Bull Recovery / Risk-On Confirmed"
    if trend_score >= 4 or (reclaim_with_volume and weekly_above_50w):
        return "Right-Side Confirmation"
    if bottom_score >= 10:
        return "High-Conviction Bottom Candidate"
    if 7 <= bottom_score <= 9:
        return "Left-Side Accumulation Zone"
    if 4 <= bottom_score <= 6:
        return "Bottom Watch"
    if (below_100w or moving_toward_200w) and bottom_score < 4:
        return "Capitulation Approach"
    return "Bear Market / Preserve Cash"


def confidence_level(
    bottom_score: int,
    trend_score: int,
    state: SignalState,
) -> str:
    if state.data_stale:
        return "Low"

    signal_count = active_signal_count(state)
    confidence = "Low"
    if signal_count >= 3 or bottom_score >= 4 or trend_score >= 4:
        confidence = "Medium"
    if (
        not state.unavailable_onchain
        and signal_count >= 6
        and (bottom_score >= 10 or trend_score >= 5)
    ):
        confidence = "High"
    if state.unavailable_onchain and trend_score >= 7:
        confidence = "High"
    elif state.unavailable_onchain and confidence == "High":
        confidence = "Medium"
    return confidence


def summary_for_phase(phase: str, bottom_score: int, trend_score: int, state: SignalState) -> str:
    if phase == "Data Unavailable":
        return "Market data could not be collected and no cache was available."
    onchain_note = " On-chain metrics are unavailable, so the read is price-structure heavy." if state.unavailable_onchain else ""
    stale_note = " Data is stale, so all conclusions are downgraded." if state.data_stale else ""
    return (
        f"Bottom-zone score is {bottom_score}; trend-confirmation score is {trend_score}. "
        f"The dashboard separates capitulation-style evidence from right-side recovery evidence."
        f"{onchain_note}{stale_note}"
    )


def classify_market(state: SignalState) -> dict[str, Any]:
    bottom_score, bottom_max = score_signals(state.bottom_signals, BOTTOM_WEIGHTS)
    trend_score, trend_max = score_signals(state.trend_signals, TREND_WEIGHTS)
    phase = determine_phase(bottom_score, trend_score, state)
    confidence = confidence_level(bottom_score, trend_score, state)
    return {
        "phase": phase,
        "recommended_action": PHASE_ACTIONS[phase],
        "summary": summary_for_phase(phase, bottom_score, trend_score, state),
        "main_reasons": state.reasons[:10],
        "missing_confirmations": state.missing[:10],
        "next_levels": state.next_levels[:12],
        "bottom_zone_score": bottom_score,
        "bottom_zone_score_max": bottom_max,
        "trend_confirmation_score": trend_score,
        "trend_confirmation_score_max": trend_max,
        "confidence": confidence,
    }
