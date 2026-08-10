import json
from datetime import datetime, timezone

import pandas as pd

from app.opportunities import OpportunityInput, build_blocked_opportunity, build_opportunity


def candles(dollar_volume: float = 50_000_000) -> pd.DataFrame:
    close = 100.0
    volume = dollar_volume / close
    return pd.DataFrame({
        "high": [101.0] * 40,
        "low": [99.0] * 40,
        "close": [close] * 40,
        "volume": [volume] * 40,
    })


def quality() -> dict[str, object]:
    return {
        "status": "healthy",
        "is_eligible": True,
        "age_hours": 8,
        "missing_periods": 0,
        "duplicate_timestamps": 0,
        "invalid_ohlc": 0,
        "anomaly_count": 0,
    }


def backtest(expectancy: float = 1.2, win_rate: float = 62.0, eligible: bool = True) -> dict[str, object]:
    return {
        "aggregate": {
            "out_of_sample": {
                "total_trades": 30,
                "expectancy_pct": expectancy,
                "win_rate_pct": win_rate,
            }
        },
        "alert_eligibility": {"eligible": eligible, "reasons": [] if eligible else ["OOS validation failed"]},
    }


def risk_decision(approved: bool = True, recommended_size: float = 2_000, utilization: float = 20) -> dict[str, object]:
    return {
        "approved": approved,
        "action": "approved" if approved else "rejected",
        "recommended_size_usd": recommended_size if approved else 0,
        "after": {"heat": {"utilization_pct": utilization}},
    }


def inputs(**overrides: object) -> OpportunityInput:
    values: dict[str, object] = {
        "ticker": "AAPL",
        "asset_type": "stock",
        "direction": "BUY",
        "status": "Healthy Trend",
        "trigger_price": 100.0,
        "stop_loss": 95.0,
        "target_price": 115.0,
        "signal_reason": "Deterministic trend signal",
        "risk_reward": 3.0,
        "atr_value": 2.0,
        "suppressed": False,
        "quality": quality(),
        "candles": candles(),
        "risk_decision": risk_decision(),
        "backtest": backtest(),
        "earnings": {"days_until": 4, "earnings_date": "2026-08-14"},
        "evaluated_at": datetime(2026, 8, 10, tzinfo=timezone.utc),
    }
    values.update(overrides)
    return OpportunityInput(**values)


def test_ranked_opportunity_is_deterministic_and_explainable() -> None:
    first = build_opportunity(inputs())
    second = build_opportunity(inputs())

    assert first == second
    assert first["eligible"] is True
    assert first["score"] >= 50
    assert len(first["components"]) == 8
    assert sum(component["weight_pct"] for component in first["components"]) == 100
    assert first["missing_inputs"] == ["timeframe_agreement"]


def test_stronger_oos_and_liquidity_inputs_rank_above_weaker_inputs() -> None:
    strong = build_opportunity(inputs())
    weak = build_opportunity(inputs(
        ticker="THIN",
        risk_reward=1.0,
        candles=candles(50_000),
        backtest=backtest(expectancy=-3.0, win_rate=35.0),
        risk_decision=risk_decision(utilization=90),
    ))

    assert strong["score"] > weak["score"]
    assert weak["eligible"] is False
    assert any("below" in reason for reason in weak["eligibility_reasons"])


def test_missing_backtest_blocks_eligibility_and_zeroes_size() -> None:
    opportunity = build_opportunity(inputs(backtest=None))

    assert opportunity["eligible"] is False
    assert opportunity["trade_plan"]["position_size_usd"] == 0
    assert "historical_expectancy" in opportunity["missing_inputs"]
    assert "confidence_calibration" in opportunity["missing_inputs"]
    assert any("No persisted out-of-sample backtest" in reason for reason in opportunity["eligibility_reasons"])


def test_trade_plan_uses_portfolio_size_and_exact_stop_risk() -> None:
    opportunity = build_opportunity(inputs())
    plan = opportunity["trade_plan"]

    assert plan["position_size_usd"] == 2_000
    assert plan["quantity"] == 20
    assert plan["maximum_planned_loss_usd"] == 100
    assert len(plan["targets"]) == 2
    assert plan["targets"][0]["price"] == 105
    assert plan["targets"][1]["price"] == 115
    assert any("Earnings in 4 day" in warning for warning in opportunity["event_warnings"])


def test_non_finite_inputs_never_produce_non_json_scores() -> None:
    opportunity = build_opportunity(inputs(
        risk_reward=float("nan"),
        atr_value=float("inf"),
        backtest=backtest(expectancy=float("nan"), win_rate=float("inf")),
    ))

    json.dumps(opportunity, allow_nan=False)
    assert 0 <= opportunity["score"] <= 100


def test_blocked_data_is_visible_with_zero_score_and_no_plan() -> None:
    opportunity = build_blocked_opportunity(
        "OLD",
        "stock",
        "Latest candle is stale",
        datetime(2026, 8, 10, tzinfo=timezone.utc),
    )

    assert opportunity["score"] == 0
    assert opportunity["eligible"] is False
    assert opportunity["trade_plan"] is None
    assert opportunity["eligibility_reasons"] == ["Latest candle is stale"]
