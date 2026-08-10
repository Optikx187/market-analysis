from __future__ import annotations

import math
import statistics
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, time, timedelta, timezone


@dataclass(frozen=True)
class RiskLimits:
    max_portfolio_heat_pct: float = 0.08
    max_ticker_exposure_pct: float = 0.20
    max_sector_exposure_pct: float = 0.35
    max_asset_class_exposure_pct: float = 0.70
    max_directional_exposure_pct: float = 0.80
    max_correlated_exposure_pct: float = 0.40
    correlation_threshold: float = 0.75
    daily_loss_limit_pct: float = 0.03
    weekly_loss_limit_pct: float = 0.06
    max_drawdown_pct: float = 0.12
    volatility_target_pct: float = 0.15
    fractional_kelly_cap: float = 0.10
    equity_shock_pct: float = 0.05
    crypto_shock_pct: float = 0.20


@dataclass(frozen=True)
class PositionInput:
    ticker: str
    direction: str
    entry_price: float
    quantity: float
    stop_loss: float
    asset_type: str = "stock"
    sector: str = "Unclassified"
    returns: dict[str, float] = field(default_factory=dict)

    @property
    def direction_sign(self) -> int:
        return 1 if self.direction.upper() == "BUY" else -1

    @property
    def notional(self) -> float:
        return max(0.0, self.entry_price * self.quantity)

    @property
    def risk_to_stop(self) -> float:
        if self.direction_sign > 0:
            distance = self.entry_price - self.stop_loss
        else:
            distance = self.stop_loss - self.entry_price
        return max(0.0, distance * self.quantity)


@dataclass(frozen=True)
class ClosedTradeResult:
    pnl: float
    closed_at: datetime


@dataclass(frozen=True)
class BreakerState:
    active: bool
    daily_loss_active: bool
    weekly_loss_active: bool
    drawdown_active: bool
    daily_pnl: float
    weekly_pnl: float
    daily_loss_pct: float
    weekly_loss_pct: float
    current_drawdown_pct: float
    reasons: tuple[str, ...]

    def as_dict(self, limits: RiskLimits) -> dict[str, object]:
        return {
            "active": self.active,
            "daily_loss_active": self.daily_loss_active,
            "weekly_loss_active": self.weekly_loss_active,
            "drawdown_active": self.drawdown_active,
            "daily_pnl": round(self.daily_pnl, 2),
            "weekly_pnl": round(self.weekly_pnl, 2),
            "daily_loss_pct": round(self.daily_loss_pct * 100, 2),
            "weekly_loss_pct": round(self.weekly_loss_pct * 100, 2),
            "current_drawdown_pct": round(self.current_drawdown_pct * 100, 2),
            "daily_limit_pct": round(limits.daily_loss_limit_pct * 100, 2),
            "weekly_limit_pct": round(limits.weekly_loss_limit_pct * 100, 2),
            "drawdown_limit_pct": round(limits.max_drawdown_pct * 100, 2),
            "allows_position_reduction": True,
            "reasons": list(self.reasons),
        }


def calculate_trade_pnl(
    direction: str,
    entry_price: float,
    exit_price: float,
    quantity: float,
) -> float:
    direction_sign = 1 if direction.upper() == "BUY" else -1
    return (exit_price - entry_price) * quantity * direction_sign


def calculate_return_series(candles: list[dict[str, object]]) -> dict[str, float]:
    points: list[tuple[str, float]] = []
    for candle in candles:
        timestamp = candle.get("timestamp")
        close = candle.get("close")
        if timestamp is None or not isinstance(close, (int, float)) or close <= 0:
            continue
        points.append((str(timestamp), float(close)))
    points.sort(key=lambda point: point[0])
    returns: dict[str, float] = {}
    for previous, current in zip(points, points[1:]):
        if previous[1] <= 0:
            continue
        returns[current[0]] = current[1] / previous[1] - 1
    return returns


def annualized_volatility(returns: dict[str, float], asset_type: str) -> float:
    values = list(returns.values())
    if len(values) < 2:
        return 0.0
    periods = 365 if asset_type.lower() == "crypto" else 252
    return statistics.stdev(values) * math.sqrt(periods)


def rolling_correlation(
    first: dict[str, float],
    second: dict[str, float],
    minimum_observations: int = 10,
) -> float | None:
    common = sorted(set(first).intersection(second))
    if len(common) < minimum_observations:
        return None
    first_values = [first[key] for key in common]
    second_values = [second[key] for key in common]
    first_mean = statistics.fmean(first_values)
    second_mean = statistics.fmean(second_values)
    numerator = sum(
        (left - first_mean) * (right - second_mean)
        for left, right in zip(first_values, second_values)
    )
    first_variance = sum((value - first_mean) ** 2 for value in first_values)
    second_variance = sum((value - second_mean) ** 2 for value in second_values)
    denominator = math.sqrt(first_variance * second_variance)
    if denominator == 0:
        return None
    return max(-1.0, min(1.0, numerator / denominator))


def risk_correlation(
    first: PositionInput,
    second: PositionInput,
) -> float | None:
    correlation = rolling_correlation(first.returns, second.returns)
    if correlation is None:
        if first.ticker == second.ticker:
            correlation = 1.0
        else:
            return None
    return correlation * first.direction_sign * second.direction_sign


def evaluate_breakers(
    closed_trades: list[ClosedTradeResult],
    equity: float,
    peak_equity: float,
    limits: RiskLimits,
    now: datetime | None = None,
) -> BreakerState:
    evaluation_time = _as_utc(now or datetime.now(timezone.utc))
    day_start = datetime.combine(evaluation_time.date(), time.min, tzinfo=timezone.utc)
    week_start = day_start - timedelta(days=day_start.weekday())
    daily_pnl = sum(
        trade.pnl for trade in closed_trades if _as_utc(trade.closed_at) >= day_start
    )
    weekly_pnl = sum(
        trade.pnl for trade in closed_trades if _as_utc(trade.closed_at) >= week_start
    )
    daily_starting_equity = max(1.0, equity - daily_pnl)
    weekly_starting_equity = max(1.0, equity - weekly_pnl)
    daily_loss_pct = max(0.0, -daily_pnl / daily_starting_equity)
    weekly_loss_pct = max(0.0, -weekly_pnl / weekly_starting_equity)
    current_drawdown_pct = (
        max(0.0, (peak_equity - equity) / peak_equity) if peak_equity > 0 else 0.0
    )
    daily_active = daily_loss_pct >= limits.daily_loss_limit_pct
    weekly_active = weekly_loss_pct >= limits.weekly_loss_limit_pct
    drawdown_active = current_drawdown_pct >= limits.max_drawdown_pct
    reasons: list[str] = []
    if daily_active:
        reasons.append(
            f"Daily loss {daily_loss_pct:.2%} reached {limits.daily_loss_limit_pct:.2%} limit"
        )
    if weekly_active:
        reasons.append(
            f"Weekly loss {weekly_loss_pct:.2%} reached {limits.weekly_loss_limit_pct:.2%} limit"
        )
    if drawdown_active:
        reasons.append(
            f"Drawdown {current_drawdown_pct:.2%} reached {limits.max_drawdown_pct:.2%} limit"
        )
    return BreakerState(
        active=daily_active or weekly_active or drawdown_active,
        daily_loss_active=daily_active,
        weekly_loss_active=weekly_active,
        drawdown_active=drawdown_active,
        daily_pnl=daily_pnl,
        weekly_pnl=weekly_pnl,
        daily_loss_pct=daily_loss_pct,
        weekly_loss_pct=weekly_loss_pct,
        current_drawdown_pct=current_drawdown_pct,
        reasons=tuple(reasons),
    )


def calculate_portfolio_risk(
    positions: list[PositionInput],
    equity: float,
    limits: RiskLimits,
) -> dict[str, object]:
    denominator = max(equity, 1.0)
    position_rows = [
        {
            "ticker": position.ticker,
            "direction": position.direction.upper(),
            "asset_type": position.asset_type.lower(),
            "sector": position.sector,
            "notional_usd": round(position.notional, 2),
            "risk_to_stop_usd": round(position.risk_to_stop, 2),
            "risk_to_stop_pct": round(position.risk_to_stop / denominator * 100, 2),
        }
        for position in positions
    ]
    raw_risk = sum(position.risk_to_stop for position in positions)
    correlation_penalty = 0.0
    matrix: dict[str, dict[str, float | None]] = {}
    correlated_edges: list[tuple[int, int]] = []
    observations_available = False
    for index, first in enumerate(positions):
        matrix.setdefault(first.ticker, {})[first.ticker] = 1.0
        for other_index in range(index + 1, len(positions)):
            second = positions[other_index]
            correlation = risk_correlation(first, second)
            matrix.setdefault(first.ticker, {})[second.ticker] = (
                round(correlation, 4) if correlation is not None else None
            )
            matrix.setdefault(second.ticker, {})[first.ticker] = (
                round(correlation, 4) if correlation is not None else None
            )
            matrix.setdefault(second.ticker, {})[second.ticker] = 1.0
            if correlation is None:
                continue
            observations_available = True
            positive_risk_correlation = max(0.0, correlation)
            correlation_penalty += min(first.risk_to_stop, second.risk_to_stop) * positive_risk_correlation
            if correlation >= limits.correlation_threshold:
                correlated_edges.append((index, other_index))

    effective_risk = raw_risk + correlation_penalty
    ticker_exposure = _exposure_by(positions, denominator, lambda position: position.ticker)
    sector_exposure = _exposure_by(
        [position for position in positions if position.sector.lower() != "unclassified"],
        denominator,
        lambda position: position.sector,
    )
    asset_exposure = _exposure_by(positions, denominator, lambda position: position.asset_type.lower())
    direction_exposure = _exposure_by(
        positions,
        denominator,
        lambda position: "LONG" if position.direction_sign > 0 else "SHORT",
    )
    correlated_cluster_pct, correlated_cluster = _largest_correlated_cluster(
        positions, correlated_edges, denominator
    )
    concentration_candidates: list[tuple[str, str, float]] = []
    for category, exposure in (
        ("ticker", ticker_exposure),
        ("sector", sector_exposure),
        ("asset_class", asset_exposure),
        ("direction", direction_exposure),
    ):
        concentration_candidates.extend(
            (category, name, percentage) for name, percentage in exposure.items()
        )
    if concentration_candidates:
        largest_category, largest_name, largest_pct = max(
            concentration_candidates, key=lambda item: item[2]
        )
        largest_concentration: dict[str, object] = {
            "category": largest_category,
            "name": largest_name,
            "pct": round(largest_pct, 2),
        }
    else:
        largest_concentration = {"category": "none", "name": "None", "pct": 0.0}

    stress_tests = _stress_tests(positions, limits)
    return {
        "positions": position_rows,
        "heat": {
            "raw_risk_usd": round(raw_risk, 2),
            "correlation_penalty_usd": round(correlation_penalty, 2),
            "effective_risk_usd": round(effective_risk, 2),
            "raw_pct": round(raw_risk / denominator * 100, 2),
            "effective_pct": round(effective_risk / denominator * 100, 2),
            "limit_pct": round(limits.max_portfolio_heat_pct * 100, 2),
            "utilization_pct": round(
                effective_risk / denominator / limits.max_portfolio_heat_pct * 100, 2
            ) if limits.max_portfolio_heat_pct > 0 else 0.0,
        },
        "exposure": {
            "ticker": ticker_exposure,
            "sector": sector_exposure,
            "asset_class": asset_exposure,
            "direction": direction_exposure,
            "limits": {
                "ticker_pct": round(limits.max_ticker_exposure_pct * 100, 2),
                "sector_pct": round(limits.max_sector_exposure_pct * 100, 2),
                "asset_class_pct": round(limits.max_asset_class_exposure_pct * 100, 2),
                "direction_pct": round(limits.max_directional_exposure_pct * 100, 2),
            },
            "largest_concentration": largest_concentration,
        },
        "correlation": {
            "threshold": limits.correlation_threshold,
            "matrix": matrix,
            "data_available": observations_available,
            "largest_cluster": correlated_cluster,
            "largest_cluster_pct": round(correlated_cluster_pct, 2),
            "limit_pct": round(limits.max_correlated_exposure_pct * 100, 2),
            "utilization_pct": round(
                correlated_cluster_pct / (limits.max_correlated_exposure_pct * 100) * 100,
                2,
            ) if limits.max_correlated_exposure_pct > 0 else 0.0,
        },
        "stress_tests": stress_tests,
    }


def evaluate_proposed_position(
    current_positions: list[PositionInput],
    proposal: PositionInput,
    closed_trades: list[ClosedTradeResult],
    equity: float,
    peak_equity: float,
    limits: RiskLimits,
    kelly_fraction: float = 0.0,
    annual_volatility: float = 0.0,
    intent: str = "increase",
    now: datetime | None = None,
) -> dict[str, object]:
    breakers = evaluate_breakers(closed_trades, equity, peak_equity, limits, now)
    before = calculate_portfolio_risk(current_positions, equity, limits)
    if intent == "reduce":
        return {
            "approved": True,
            "action": "approve_reduction",
            "requested_size_usd": round(proposal.notional, 2),
            "recommended_size_usd": 0.0,
            "reasons": [{
                "code": "POSITION_REDUCTION_ALLOWED",
                "message": "Position reduction remains allowed while risk controls are active",
            }],
            "breaker": breakers.as_dict(limits),
            "before": before,
            "after": before,
        }

    validation_reasons = _position_validation_reasons(proposal)
    if validation_reasons:
        return {
            "approved": False,
            "action": "rejected",
            "requested_size_usd": round(proposal.notional, 2),
            "recommended_size_usd": 0.0,
            "reasons": validation_reasons,
            "breaker": breakers.as_dict(limits),
            "before": before,
            "after": before,
        }

    requested_size = proposal.notional
    recommended_size = requested_size
    sizing_reasons: list[dict[str, object]] = []
    if kelly_fraction > 0:
        kelly_limit = equity * min(kelly_fraction, limits.fractional_kelly_cap)
        if recommended_size > kelly_limit:
            recommended_size = kelly_limit
            sizing_reasons.append({
                "code": "FRACTIONAL_KELLY_CAP",
                "message": (
                    f"Size reduced to ${kelly_limit:,.2f} by "
                    f"{limits.fractional_kelly_cap:.2%} fractional-Kelly cap"
                ),
                "requested": round(requested_size, 2),
                "limit": round(kelly_limit, 2),
            })
    if annual_volatility > limits.volatility_target_pct > 0:
        volatility_scalar = limits.volatility_target_pct / annual_volatility
        volatility_limit = requested_size * volatility_scalar
        if recommended_size > volatility_limit:
            recommended_size = volatility_limit
            sizing_reasons.append({
                "code": "VOLATILITY_TARGET",
                "message": (
                    f"Size reduced to ${volatility_limit:,.2f} because annualized volatility "
                    f"{annual_volatility:.2%} exceeds {limits.volatility_target_pct:.2%} target"
                ),
                "actual": round(annual_volatility * 100, 2),
                "limit": round(limits.volatility_target_pct * 100, 2),
            })
    scale = recommended_size / requested_size if requested_size > 0 else 0.0
    sized_proposal = replace(proposal, quantity=proposal.quantity * scale)
    after = calculate_portfolio_risk([*current_positions, sized_proposal], equity, limits)
    rejection_reasons = _limit_reasons(after, limits)
    if breakers.active:
        rejection_reasons = [
            {
                "code": "CIRCUIT_BREAKER_ACTIVE",
                "message": reason,
            }
            for reason in breakers.reasons
        ] + rejection_reasons
    approved = not rejection_reasons and recommended_size > 0
    action = "approved"
    if not approved:
        action = "rejected"
    elif sizing_reasons:
        action = "reduced"
    return {
        "approved": approved,
        "action": action,
        "requested_size_usd": round(requested_size, 2),
        "recommended_size_usd": round(recommended_size, 2) if approved else 0.0,
        "reasons": [*rejection_reasons, *sizing_reasons],
        "breaker": breakers.as_dict(limits),
        "before": before,
        "after": after,
    }


def _position_validation_reasons(proposal: PositionInput) -> list[dict[str, object]]:
    if proposal.entry_price <= 0 or proposal.quantity <= 0 or proposal.stop_loss <= 0:
        return [{
            "code": "INVALID_POSITION_SIZE",
            "message": "Entry price, quantity, and stop price must be positive",
        }]
    if proposal.direction_sign > 0 and proposal.stop_loss >= proposal.entry_price:
        return [{
            "code": "INVALID_STOP",
            "message": "Long-position stop must be below the entry price",
        }]
    if proposal.direction_sign < 0 and proposal.stop_loss <= proposal.entry_price:
        return [{
            "code": "INVALID_STOP",
            "message": "Short-position stop must be above the entry price",
        }]
    return []


def _limit_reasons(snapshot: dict[str, object], limits: RiskLimits) -> list[dict[str, object]]:
    reasons: list[dict[str, object]] = []
    heat = snapshot["heat"]
    if isinstance(heat, dict):
        effective_pct = float(heat["effective_pct"])
        limit_pct = limits.max_portfolio_heat_pct * 100
        if effective_pct > limit_pct:
            reasons.append(_limit_reason(
                "MAX_PORTFOLIO_HEAT",
                "Effective portfolio heat",
                effective_pct,
                limit_pct,
            ))
    exposure = snapshot["exposure"]
    if isinstance(exposure, dict):
        for category, limit, code, label in (
            ("ticker", limits.max_ticker_exposure_pct, "MAX_TICKER_EXPOSURE", "Ticker exposure"),
            ("sector", limits.max_sector_exposure_pct, "MAX_SECTOR_EXPOSURE", "Sector exposure"),
            ("asset_class", limits.max_asset_class_exposure_pct, "MAX_ASSET_CLASS_EXPOSURE", "Asset-class exposure"),
            ("direction", limits.max_directional_exposure_pct, "MAX_DIRECTIONAL_EXPOSURE", "Directional exposure"),
        ):
            values = exposure.get(category, {})
            if not isinstance(values, dict):
                continue
            for name, actual in values.items():
                actual_pct = float(actual)
                if actual_pct > limit * 100:
                    reasons.append(_limit_reason(
                        code,
                        f"{label} for {name}",
                        actual_pct,
                        limit * 100,
                    ))
    correlation = snapshot["correlation"]
    if isinstance(correlation, dict):
        cluster_pct = float(correlation["largest_cluster_pct"])
        limit_pct = limits.max_correlated_exposure_pct * 100
        if cluster_pct > limit_pct:
            cluster = ", ".join(str(item) for item in correlation["largest_cluster"])
            reasons.append(_limit_reason(
                "MAX_CORRELATED_EXPOSURE",
                f"Correlated exposure ({cluster})",
                cluster_pct,
                limit_pct,
            ))
    return reasons


def _limit_reason(code: str, label: str, actual_pct: float, limit_pct: float) -> dict[str, object]:
    return {
        "code": code,
        "message": f"{label} {actual_pct:.2f}% exceeds {limit_pct:.2f}% limit",
        "actual": round(actual_pct, 2),
        "limit": round(limit_pct, 2),
    }


def _exposure_by(
    positions: list[PositionInput],
    equity: float,
    key: Callable[[PositionInput], str],
) -> dict[str, float]:
    totals: dict[str, float] = {}
    for position in positions:
        name = str(key(position))
        totals[name] = totals.get(name, 0.0) + position.notional
    return {
        name: round(notional / equity * 100, 2)
        for name, notional in sorted(totals.items())
    }


def _largest_correlated_cluster(
    positions: list[PositionInput],
    edges: list[tuple[int, int]],
    equity: float,
) -> tuple[float, list[str]]:
    adjacency = {index: set() for index in range(len(positions))}
    for first, second in edges:
        adjacency[first].add(second)
        adjacency[second].add(first)
    visited: set[int] = set()
    largest_pct = 0.0
    largest_tickers: list[str] = []
    for start in adjacency:
        if start in visited or not adjacency[start]:
            continue
        stack = [start]
        component: set[int] = set()
        while stack:
            index = stack.pop()
            if index in component:
                continue
            component.add(index)
            visited.add(index)
            stack.extend(adjacency[index] - component)
        exposure_pct = sum(positions[index].notional for index in component) / equity * 100
        if exposure_pct > largest_pct:
            largest_pct = exposure_pct
            largest_tickers = sorted({positions[index].ticker for index in component})
    return largest_pct, largest_tickers


def _stress_tests(
    positions: list[PositionInput],
    limits: RiskLimits,
) -> list[dict[str, object]]:
    equity_pnl = sum(
        -limits.equity_shock_pct * position.notional * position.direction_sign
        for position in positions
        if position.asset_type.lower() != "crypto"
    )
    crypto_pnl = sum(
        -limits.crypto_shock_pct * position.notional * position.direction_sign
        for position in positions
        if position.asset_type.lower() == "crypto"
    )
    return [
        {
            "name": "Broad equity shock",
            "description": f"Stocks fall {limits.equity_shock_pct:.0%}",
            "estimated_pnl": round(equity_pnl, 2),
        },
        {
            "name": "Crypto shock",
            "description": f"Crypto falls {limits.crypto_shock_pct:.0%}",
            "estimated_pnl": round(crypto_pnl, 2),
        },
        {
            "name": "Combined risk-off shock",
            "description": (
                f"Stocks fall {limits.equity_shock_pct:.0%}; "
                f"crypto falls {limits.crypto_shock_pct:.0%}"
            ),
            "estimated_pnl": round(equity_pnl + crypto_pnl, 2),
        },
    ]


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
