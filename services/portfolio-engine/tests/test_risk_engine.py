from datetime import datetime, timedelta, timezone
import math

from app.risk_engine import (
    ClosedTradeResult,
    PositionInput,
    RiskLimits,
    calculate_portfolio_risk,
    calculate_trade_pnl,
    evaluate_breakers,
    evaluate_proposed_position,
)


def _returns(kind: str, count: int = 40) -> dict[str, float]:
    values: dict[str, float] = {}
    for index in range(count):
        if kind == "trend":
            value = math.sin(index / 3) * 0.02 + index * 0.0001
        elif kind == "correlated":
            value = math.sin(index / 3) * 0.019 + index * 0.000095
        else:
            value = math.cos(index / 2) * 0.02
        values[f"2026-01-{index + 1:02d}"] = value
    return values


def _position(
    ticker: str,
    direction: str = "BUY",
    quantity: float = 10,
    stop_loss: float = 95,
    returns_kind: str = "trend",
    asset_type: str = "stock",
    sector: str = "Technology",
) -> PositionInput:
    return PositionInput(
        ticker=ticker,
        direction=direction,
        entry_price=100,
        quantity=quantity,
        stop_loss=stop_loss,
        asset_type=asset_type,
        sector=sector,
        returns=_returns(returns_kind),
    )


def test_long_and_short_pnl_and_stop_risk() -> None:
    long_position = _position("LONG")
    short_position = _position("SHORT", direction="SELL", stop_loss=105)

    assert long_position.risk_to_stop == 50
    assert short_position.risk_to_stop == 50
    assert calculate_trade_pnl("BUY", 100, 112, 10) == 120
    assert calculate_trade_pnl("BUY", 100, 88, 10) == -120
    assert calculate_trade_pnl("SELL", 100, 88, 10) == 120
    assert calculate_trade_pnl("SELL", 100, 112, 10) == -120


def test_correlated_positions_consume_more_heat_than_independent_positions() -> None:
    limits = RiskLimits()
    correlated = calculate_portfolio_risk(
        [_position("AAA"), _position("BBB", returns_kind="correlated")],
        10_000,
        limits,
    )
    independent = calculate_portfolio_risk(
        [_position("AAA"), _position("CCC", returns_kind="independent")],
        10_000,
        limits,
    )

    assert correlated["heat"]["effective_risk_usd"] > correlated["heat"]["raw_risk_usd"]
    assert correlated["heat"]["effective_risk_usd"] > independent["heat"]["effective_risk_usd"]
    assert correlated["correlation"]["largest_cluster"] == ["AAA", "BBB"]


def test_limit_breach_returns_exact_reason() -> None:
    limits = RiskLimits(
        max_portfolio_heat_pct=0.01,
        max_ticker_exposure_pct=1,
        max_sector_exposure_pct=1,
        max_asset_class_exposure_pct=1,
        max_directional_exposure_pct=1,
        max_correlated_exposure_pct=1,
    )
    decision = evaluate_proposed_position(
        [],
        _position("RISK", quantity=40),
        [],
        equity=10_000,
        peak_equity=10_000,
        limits=limits,
    )

    assert decision["approved"] is False
    assert decision["action"] == "rejected"
    assert decision["reasons"][0] == {
        "code": "MAX_PORTFOLIO_HEAT",
        "message": "Effective portfolio heat 2.00% exceeds 1.00% limit",
        "actual": 2.0,
        "limit": 1.0,
    }


def test_correlated_cluster_limit_rejects_addition() -> None:
    limits = RiskLimits(
        max_portfolio_heat_pct=1,
        max_ticker_exposure_pct=1,
        max_sector_exposure_pct=1,
        max_asset_class_exposure_pct=1,
        max_directional_exposure_pct=1,
        max_correlated_exposure_pct=0.40,
    )
    existing = _position("AAA", quantity=30)
    proposal = _position("BBB", quantity=20, returns_kind="correlated")
    decision = evaluate_proposed_position(
        [existing], proposal, [], 10_000, 10_000, limits
    )

    codes = {reason["code"] for reason in decision["reasons"]}
    assert decision["approved"] is False
    assert "MAX_CORRELATED_EXPOSURE" in codes
    assert decision["after"]["correlation"]["largest_cluster_pct"] == 50.0


def test_daily_weekly_and_drawdown_breakers_reset_by_window_or_recovery() -> None:
    now = datetime(2026, 7, 15, 18, tzinfo=timezone.utc)
    trade = ClosedTradeResult(pnl=-400, closed_at=now - timedelta(hours=2))
    limits = RiskLimits(
        daily_loss_limit_pct=0.03,
        weekly_loss_limit_pct=0.06,
        max_drawdown_pct=0.12,
    )

    active = evaluate_breakers([trade], equity=9_600, peak_equity=10_000, limits=limits, now=now)
    assert active.daily_loss_active is True
    assert active.weekly_loss_active is False
    assert active.drawdown_active is False

    next_day = evaluate_breakers(
        [trade], equity=9_600, peak_equity=10_000, limits=limits, now=now + timedelta(days=1)
    )
    assert next_day.daily_loss_active is False

    drawdown = evaluate_breakers([], equity=8_700, peak_equity=10_000, limits=limits, now=now)
    recovered = evaluate_breakers([], equity=9_000, peak_equity=10_000, limits=limits, now=now)
    assert drawdown.drawdown_active is True
    assert recovered.drawdown_active is False

    following_week = now + timedelta(days=7)
    reset = evaluate_breakers(
        [trade], equity=9_600, peak_equity=10_000, limits=limits, now=following_week
    )
    assert reset.weekly_loss_active is False


def test_active_breaker_rejects_new_risk_but_allows_reduction() -> None:
    now = datetime(2026, 7, 15, 18, tzinfo=timezone.utc)
    closed = [ClosedTradeResult(pnl=-500, closed_at=now - timedelta(hours=1))]
    limits = RiskLimits(daily_loss_limit_pct=0.03)
    proposal = _position("NEW")

    increase = evaluate_proposed_position(
        [], proposal, closed, 9_500, 10_000, limits, now=now
    )
    reduction = evaluate_proposed_position(
        [], proposal, closed, 9_500, 10_000, limits, intent="reduce", now=now
    )

    assert increase["approved"] is False
    assert increase["reasons"][0]["code"] == "CIRCUIT_BREAKER_ACTIVE"
    assert reduction["approved"] is True
    assert reduction["action"] == "approve_reduction"


def test_fractional_kelly_and_volatility_caps_reduce_size() -> None:
    limits = RiskLimits(
        max_portfolio_heat_pct=1,
        max_ticker_exposure_pct=1,
        max_sector_exposure_pct=1,
        max_asset_class_exposure_pct=1,
        max_directional_exposure_pct=1,
        max_correlated_exposure_pct=1,
        fractional_kelly_cap=0.10,
        volatility_target_pct=0.15,
    )
    decision = evaluate_proposed_position(
        [],
        _position("SIZE", quantity=50),
        [],
        10_000,
        10_000,
        limits,
        kelly_fraction=0.20,
        annual_volatility=1.0,
    )

    assert decision["approved"] is True
    assert decision["action"] == "reduced"
    assert decision["recommended_size_usd"] == 750
    assert {reason["code"] for reason in decision["reasons"]} == {
        "FRACTIONAL_KELLY_CAP",
        "VOLATILITY_TARGET",
    }


def test_invalid_long_and_short_stops_return_exact_reasons() -> None:
    long_decision = evaluate_proposed_position(
        [], _position("LONG", stop_loss=101), [], 10_000, 10_000, RiskLimits()
    )
    short_decision = evaluate_proposed_position(
        [], _position("SHORT", direction="SELL", stop_loss=99), [], 10_000, 10_000, RiskLimits()
    )

    assert long_decision["reasons"] == [{
        "code": "INVALID_STOP",
        "message": "Long-position stop must be below the entry price",
    }]
    assert short_decision["reasons"] == [{
        "code": "INVALID_STOP",
        "message": "Short-position stop must be above the entry price",
    }]


def test_each_concentration_limit_returns_its_reason_code() -> None:
    cases = [
        ("max_ticker_exposure_pct", "MAX_TICKER_EXPOSURE"),
        ("max_sector_exposure_pct", "MAX_SECTOR_EXPOSURE"),
        ("max_asset_class_exposure_pct", "MAX_ASSET_CLASS_EXPOSURE"),
        ("max_directional_exposure_pct", "MAX_DIRECTIONAL_EXPOSURE"),
    ]
    for limit_name, expected_code in cases:
        limit_values = {
            "max_portfolio_heat_pct": 1,
            "max_ticker_exposure_pct": 1,
            "max_sector_exposure_pct": 1,
            "max_asset_class_exposure_pct": 1,
            "max_directional_exposure_pct": 1,
            "max_correlated_exposure_pct": 1,
            limit_name: 0.15,
        }
        decision = evaluate_proposed_position(
            [_position("AAA", quantity=10)],
            _position("AAA" if limit_name == "max_ticker_exposure_pct" else "BBB", quantity=10),
            [],
            10_000,
            10_000,
            RiskLimits(**limit_values),
        )

        assert expected_code in {reason["code"] for reason in decision["reasons"]}


def test_stress_scenarios_respect_long_and_short_direction() -> None:
    snapshot = calculate_portfolio_risk(
        [
            _position("SPY", quantity=10, asset_type="stock"),
            _position("BTC", direction="SELL", quantity=10, stop_loss=105, asset_type="crypto"),
        ],
        10_000,
        RiskLimits(),
    )
    stress = {scenario["name"]: scenario["estimated_pnl"] for scenario in snapshot["stress_tests"]}

    assert stress["Broad equity shock"] == -50
    assert stress["Crypto shock"] == 200
    assert stress["Combined risk-off shock"] == 150
