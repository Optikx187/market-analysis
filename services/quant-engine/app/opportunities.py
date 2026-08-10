"""Transparent opportunity ranking and trade-plan generation."""

from dataclasses import dataclass
from datetime import datetime, timezone
import math

import pandas as pd


COMPONENT_WEIGHTS = {
    "historical_expectancy": 0.20,
    "confidence_calibration": 0.15,
    "reward_risk": 0.15,
    "liquidity": 0.10,
    "data_quality": 0.10,
    "regime_fit": 0.10,
    "timeframe_agreement": 0.10,
    "portfolio_fit": 0.10,
}
MINIMUM_OPPORTUNITY_SCORE = 50.0
ESTIMATED_COST_BPS = 9.0


@dataclass(frozen=True)
class OpportunityInput:
    ticker: str
    asset_type: str
    direction: str
    status: str
    trigger_price: float
    stop_loss: float
    target_price: float
    signal_reason: str
    risk_reward: float
    atr_value: float
    suppressed: bool
    quality: dict[str, object]
    candles: pd.DataFrame
    risk_decision: dict[str, object] | None
    backtest: dict[str, object] | None
    earnings: dict[str, object] | None = None
    evaluated_at: datetime | None = None


def _number(value: object, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _mapping(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _clamp(value: float, lower: float = 0.0, upper: float = 100.0) -> float:
    return max(lower, min(upper, value))


def _component(
    name: str,
    score: float,
    explanation: str,
    available: bool = True,
) -> dict[str, object]:
    finite_score = round(_clamp(score), 2) if available else 0.0
    weight = COMPONENT_WEIGHTS[name]
    return {
        "name": name,
        "label": name.replace("_", " ").title(),
        "score": finite_score,
        "weight_pct": round(weight * 100, 2),
        "contribution": round(finite_score * weight, 2),
        "available": available,
        "explanation": explanation,
    }


def _backtest_metrics(backtest: dict[str, object] | None) -> tuple[dict[str, object], bool, list[str]]:
    if backtest is None:
        return {}, False, ["No persisted out-of-sample backtest is available"]
    aggregate = _mapping(backtest.get("aggregate"))
    out_of_sample = _mapping(aggregate.get("out_of_sample"))
    eligibility = _mapping(backtest.get("alert_eligibility"))
    reasons_value = eligibility.get("reasons")
    reasons = [str(reason) for reason in reasons_value] if isinstance(reasons_value, list) else []
    eligible = bool(eligibility.get("eligible", False))
    if not out_of_sample:
        reasons.append("Backtest is missing out-of-sample metrics")
        eligible = False
    return out_of_sample, eligible, reasons


def _liquidity_component(candles: pd.DataFrame) -> tuple[float, str, bool, float]:
    required = {"high", "low", "close", "volume"}
    if candles.empty or not required.issubset(candles.columns):
        return 0.0, "OHLCV inputs are unavailable", False, ESTIMATED_COST_BPS
    recent = candles.tail(20).copy()
    for column in required:
        recent[column] = pd.to_numeric(recent[column], errors="coerce")
    recent = recent.dropna(subset=list(required))
    recent = recent[(recent["close"] > 0) & (recent["volume"] >= 0)]
    if recent.empty:
        return 0.0, "Recent OHLCV inputs are invalid", False, ESTIMATED_COST_BPS
    median_dollar_volume = _number((recent["close"] * recent["volume"]).median())
    median_range_bps = _number(
        (((recent["high"] - recent["low"]).abs() / recent["close"]) * 10_000).median(),
        1_000.0,
    )
    if median_dollar_volume <= 0:
        volume_score = 0.0
    else:
        volume_score = _clamp((math.log10(median_dollar_volume) - 5.0) * 33.33)
    tightness_score = _clamp(100.0 - median_range_bps / 5.0)
    score = volume_score * 0.7 + tightness_score * 0.3
    estimated_cost_bps = _clamp(ESTIMATED_COST_BPS + median_range_bps * 0.05, 3.0, 50.0)
    explanation = (
        f"20-bar median dollar volume ${median_dollar_volume:,.0f}; "
        f"range proxy {median_range_bps:.0f} bps"
    )
    return score, explanation, True, estimated_cost_bps


def _quality_component(quality: dict[str, object]) -> tuple[float, str, bool]:
    if not quality:
        return 0.0, "Data-quality report is unavailable", False
    status = str(quality.get("status", "unknown"))
    eligible = bool(quality.get("is_eligible", False))
    if not eligible:
        return 0.0, f"Data quality is {status} and blocks eligibility", True
    base = 100.0 if status == "healthy" else 65.0
    penalties = (
        _number(quality.get("missing_periods")) * 2.0
        + _number(quality.get("duplicate_timestamps")) * 5.0
        + _number(quality.get("invalid_ohlc")) * 10.0
        + _number(quality.get("anomaly_count")) * 2.0
    )
    age_hours = _number(quality.get("age_hours"))
    score = _clamp(base - penalties)
    return score, f"{status.title()} data; latest candle age {age_hours:.1f} hours", True


def _portfolio_component(risk_decision: dict[str, object] | None) -> tuple[float, str, bool]:
    if risk_decision is None:
        return 0.0, "Portfolio risk decision is unavailable", False
    if not bool(risk_decision.get("approved", False)):
        return 0.0, "Portfolio limits reject this additional risk", True
    after = _mapping(risk_decision.get("after"))
    heat = _mapping(after.get("heat"))
    utilization = _number(heat.get("utilization_pct"), 100.0)
    action = str(risk_decision.get("action", "approved"))
    score = _clamp(100.0 - utilization)
    if action == "reduced":
        score = min(score, 70.0)
    return score, f"Post-trade heat utilization {utilization:.1f}% ({action})", True


def _event_warnings(inputs: OpportunityInput) -> list[str]:
    warnings = ["Economic-event feed is not configured; verify scheduled macro releases before entry"]
    if inputs.asset_type == "crypto":
        return warnings
    if inputs.earnings is None:
        warnings.insert(0, "Earnings calendar is unavailable for this stock")
        return warnings
    days_until = int(_number(inputs.earnings.get("days_until"), -1.0))
    earnings_date = str(inputs.earnings.get("earnings_date", "unknown date"))
    if 0 <= days_until <= 14:
        warnings.insert(0, f"Earnings in {days_until} day(s) on {earnings_date}; gap risk is elevated")
    return warnings


def _trade_plan(inputs: OpportunityInput, recommended_size_usd: float, estimated_cost_bps: float) -> dict[str, object]:
    entry = max(inputs.trigger_price, 0.000001)
    risk_per_unit = abs(entry - inputs.stop_loss)
    quantity = recommended_size_usd / entry if recommended_size_usd > 0 else 0.0
    maximum_loss = quantity * risk_per_unit
    direction_sign = 1.0 if inputs.direction.upper() == "BUY" else -1.0
    entry_offset = max(inputs.atr_value * 0.25, entry * 0.001)
    if direction_sign > 0:
        entry_low = max(0.000001, entry - entry_offset)
        entry_high = entry
    else:
        entry_low = entry
        entry_high = entry + entry_offset
    first_target = max(0.000001, entry + direction_sign * risk_per_unit)
    second_target = max(0.000001, inputs.target_price)
    estimated_costs = recommended_size_usd * estimated_cost_bps / 10_000
    gross_reward = quantity * abs(second_target - entry)
    net_reward_risk = (
        max(0.0, gross_reward - estimated_costs) / (maximum_loss + estimated_costs)
        if maximum_loss + estimated_costs > 0
        else 0.0
    )
    holding_period = "10 calendar days" if inputs.asset_type == "crypto" else "5 trading days"
    return {
        "entry_zone": {"low": round(entry_low, 6), "high": round(entry_high, 6)},
        "stop_loss": round(inputs.stop_loss, 6),
        "targets": [
            {"price": round(first_target, 6), "exit_pct": 50, "label": "1R partial"},
            {"price": round(second_target, 6), "exit_pct": 50, "label": "Final target"},
        ],
        "position_size_usd": round(recommended_size_usd, 2),
        "quantity": round(quantity, 8),
        "maximum_planned_loss_usd": round(maximum_loss, 2),
        "estimated_cost_bps": round(estimated_cost_bps, 2),
        "estimated_costs_usd": round(estimated_costs, 2),
        "net_reward_risk": round(net_reward_risk, 2),
        "scale_in": [
            {"entry_pct": 50, "instruction": "Enter half near the favorable edge of the zone"},
            {"entry_pct": 50, "instruction": "Add only after price confirms the signal direction"},
        ],
        "scale_out": [
            {"exit_pct": 50, "instruction": "Take partial profit at 1R"},
            {"exit_pct": 50, "instruction": "Exit remainder at final target or invalidation"},
        ],
        "time_stop": holding_period,
        "invalidation_reason": (
            f"{inputs.signal_reason}; invalidate if price closes beyond the stop at "
            f"{inputs.stop_loss:.6f}"
        ),
    }


def build_opportunity(inputs: OpportunityInput) -> dict[str, object]:
    oos, backtest_eligible, backtest_reasons = _backtest_metrics(inputs.backtest)
    total_trades = int(_number(oos.get("total_trades")))
    expectancy = _number(oos.get("expectancy_pct"))
    win_rate = _number(oos.get("win_rate_pct"))
    has_backtest_sample = total_trades > 0
    expectancy_score = _clamp(50.0 + expectancy * 20.0)
    confidence_score = _clamp(win_rate) * min(1.0, total_trades / 30.0)
    liquidity_score, liquidity_explanation, liquidity_available, estimated_cost_bps = _liquidity_component(inputs.candles)
    quality_score, quality_explanation, quality_available = _quality_component(inputs.quality)
    portfolio_score, portfolio_explanation, portfolio_available = _portfolio_component(inputs.risk_decision)
    regime_score = 75.0 if inputs.status == "Healthy Trend" else 40.0
    if inputs.status == "Tanking" and inputs.direction.upper() == "SELL":
        regime_score = 70.0

    components = [
        _component(
            "historical_expectancy",
            expectancy_score,
            f"OOS expectancy {expectancy:.2f}% across {total_trades} trades" if has_backtest_sample else "No OOS trade sample",
            has_backtest_sample,
        ),
        _component(
            "confidence_calibration",
            confidence_score,
            f"OOS win rate {win_rate:.1f}% with sample-size adjustment" if has_backtest_sample else "No OOS calibration sample",
            has_backtest_sample,
        ),
        _component("reward_risk", _clamp(inputs.risk_reward / 3.0 * 100.0), f"Gross reward/risk {inputs.risk_reward:.2f}"),
        _component("liquidity", liquidity_score, liquidity_explanation, liquidity_available),
        _component("data_quality", quality_score, quality_explanation, quality_available),
        _component("regime_fit", regime_score, f"Signal-state proxy: {inputs.status}"),
        _component(
            "timeframe_agreement",
            0.0,
            "Only the daily signal is available; multi-timeframe confirmation is missing",
            False,
        ),
        _component("portfolio_fit", portfolio_score, portfolio_explanation, portfolio_available),
    ]
    total_score = round(sum(_number(component["contribution"]) for component in components), 2)
    risk_approved = bool(inputs.risk_decision and inputs.risk_decision.get("approved", False))
    quality_eligible = bool(inputs.quality.get("is_eligible", False))
    eligibility_reasons = list(backtest_reasons)
    if not quality_eligible:
        eligibility_reasons.append("Market data is not eligible")
    if not risk_approved:
        risk_summary = str((inputs.risk_decision or {}).get("summary", "")).strip()
        eligibility_reasons.append(
            risk_summary or "Portfolio risk controls rejected the proposed position"
        )
    if inputs.suppressed:
        eligibility_reasons.append(f"Signal is suppressed ({inputs.status})")
    if total_score < MINIMUM_OPPORTUNITY_SCORE:
        eligibility_reasons.append(
            f"Opportunity score {total_score:.2f} is below {MINIMUM_OPPORTUNITY_SCORE:.2f}"
        )
    eligible = (
        backtest_eligible
        and quality_eligible
        and risk_approved
        and not inputs.suppressed
        and total_score >= MINIMUM_OPPORTUNITY_SCORE
    )
    missing_inputs = [
        str(component["name"])
        for component in components
        if not bool(component["available"])
    ]
    risk_decision = inputs.risk_decision or {}
    recommended_size = _number(risk_decision.get("recommended_size_usd")) if eligible else 0.0
    evaluated_at = inputs.evaluated_at or datetime.now(timezone.utc)
    plan = _trade_plan(inputs, recommended_size, estimated_cost_bps)
    return {
        "id": f"{inputs.ticker}:{inputs.direction.upper()}",
        "ticker": inputs.ticker,
        "asset_type": inputs.asset_type,
        "direction": inputs.direction.upper(),
        "status": inputs.status,
        "score": total_score,
        "minimum_score": MINIMUM_OPPORTUNITY_SCORE,
        "eligible": eligible,
        "eligibility_reasons": [] if eligible else list(dict.fromkeys(eligibility_reasons)),
        "missing_inputs": missing_inputs,
        "components": components,
        "trade_plan": plan,
        "event_warnings": _event_warnings(inputs),
        "signal_reason": inputs.signal_reason,
        "evaluated_at": evaluated_at.astimezone(timezone.utc).isoformat(),
        "user_decision": "pending",
        "snoozed_until": None,
    }


def build_blocked_opportunity(
    ticker: str,
    asset_type: str,
    reason: str,
    evaluated_at: datetime,
) -> dict[str, object]:
    components = [
        _component(name, 0.0, reason if name == "data_quality" else "Input unavailable", name == "data_quality")
        for name in COMPONENT_WEIGHTS
    ]
    return {
        "id": f"{ticker}:BLOCKED",
        "ticker": ticker,
        "asset_type": asset_type,
        "direction": "NONE",
        "status": "Data Blocked",
        "score": 0.0,
        "minimum_score": MINIMUM_OPPORTUNITY_SCORE,
        "eligible": False,
        "eligibility_reasons": [reason],
        "missing_inputs": [name for name in COMPONENT_WEIGHTS if name != "data_quality"],
        "components": components,
        "trade_plan": None,
        "event_warnings": [],
        "signal_reason": reason,
        "evaluated_at": evaluated_at.astimezone(timezone.utc).isoformat(),
        "user_decision": "blocked",
        "snoozed_until": None,
    }
