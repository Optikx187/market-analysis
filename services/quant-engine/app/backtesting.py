"""Leakage-safe walk-forward backtesting with realistic execution costs."""

from dataclasses import asdict, dataclass
from math import sqrt
from statistics import mean, pstdev

import pandas as pd

from app.indicators import add_indicators


@dataclass(frozen=True)
class StrategyParameters:
    risk_reward_ratio: float = 3.0
    atr_stop_multiplier: float = 1.5
    volatility_threshold: float = 2.0

    def to_dict(self) -> dict[str, float]:
        return asdict(self)


@dataclass(frozen=True)
class ExecutionCosts:
    commission_bps: float = 2.0
    spread_bps: float = 4.0
    slippage_bps: float = 3.0
    fill_delay_bars: int = 1

    def to_dict(self) -> dict[str, float | int]:
        return asdict(self)


@dataclass(frozen=True)
class WindowConfiguration:
    warmup_bars: int = 201
    train_bars: int = 60
    validation_bars: int = 20
    test_bars: int = 20
    step_bars: int = 20

    @property
    def minimum_bars(self) -> int:
        return self.warmup_bars + self.train_bars + self.validation_bars + self.test_bars

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(frozen=True)
class ValidationThresholds:
    minimum_trades: int = 3
    minimum_after_cost_return_pct: float = 0.0
    minimum_sharpe: float = 0.0
    minimum_profit_factor: float = 1.0
    maximum_drawdown_pct: float = 25.0

    def to_dict(self) -> dict[str, float | int]:
        return asdict(self)


@dataclass(frozen=True)
class WalkForwardWindow:
    index: int
    train_start: int
    train_end: int
    validation_start: int
    validation_end: int
    test_start: int
    test_end: int


@dataclass(frozen=True)
class TradeSignal:
    direction: str
    trigger_price: float
    stop_loss: float
    target_price: float
    reason: str
    market_regime: str
    volatility_regime: str


@dataclass
class OpenPosition:
    direction: str
    signal_index: int
    entry_index: int
    raw_entry: float
    executed_entry: float
    stop_loss: float
    target_price: float
    reason: str
    market_regime: str
    volatility_regime: str
    entry_date: str


@dataclass(frozen=True)
class TradeResult:
    direction: str
    signal_date: str
    entry_date: str
    exit_date: str
    raw_entry: float
    entry: float
    raw_exit: float
    exit: float
    gross_pnl_pct: float
    net_pnl_pct: float
    cost_pct: float
    pnl_usd: float
    outcome: str
    reason: str
    market_regime: str
    volatility_regime: str
    entry_index: int
    exit_index: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class Metrics:
    total_trades: int
    wins: int
    losses: int
    win_rate_pct: float
    expectancy_pct: float
    sharpe: float
    sortino: float
    profit_factor: float
    max_drawdown_pct: float
    recovery_bars: int
    turnover_pct: float
    exposure_pct: float
    gross_return_pct: float
    after_cost_return_pct: float
    total_cost_pct: float
    final_equity: float

    def to_dict(self) -> dict[str, float | int]:
        return asdict(self)


@dataclass(frozen=True)
class SegmentResult:
    metrics: Metrics
    trades: list[TradeResult]
    bar_returns: list[float]
    equity_curve: list[dict[str, float | str]]
    bars: int


@dataclass(frozen=True)
class BenchmarkResult:
    symbol: str
    gross_return_pct: float
    after_cost_return_pct: float
    max_drawdown_pct: float
    final_equity: float
    start_date: str
    end_date: str

    def to_dict(self) -> dict[str, float | str]:
        return asdict(self)


def prepare_candles(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"timestamp", "open", "high", "low", "close", "volume"}
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"Candle data is missing required columns: {', '.join(missing)}")
    prepared = frame.copy()
    prepared["timestamp"] = pd.to_datetime(prepared["timestamp"], errors="raise", utc=True)
    prepared = prepared.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    for column in ["open", "high", "low", "close", "volume"]:
        prepared[column] = pd.to_numeric(prepared[column], errors="raise")
    return add_indicators(prepared)


def build_walk_forward_windows(length: int, config: WindowConfiguration) -> list[WalkForwardWindow]:
    if config.step_bars < config.test_bars:
        raise ValueError("step_bars must be at least test_bars to prevent overlapping test windows")
    windows: list[WalkForwardWindow] = []
    train_start = config.warmup_bars
    index = 1
    while True:
        train_end = train_start + config.train_bars
        validation_end = train_end + config.validation_bars
        test_end = validation_end + config.test_bars
        if test_end > length:
            break
        windows.append(
            WalkForwardWindow(
                index=index,
                train_start=train_start,
                train_end=train_end,
                validation_start=train_end,
                validation_end=validation_end,
                test_start=validation_end,
                test_end=test_end,
            )
        )
        index += 1
        train_start += config.step_bars
    return windows


def _timestamp(frame: pd.DataFrame, index: int) -> str:
    value = frame.iloc[index]["timestamp"]
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _classify_regime(row: pd.Series) -> tuple[str, str]:
    close = float(row["close"])
    ema_20 = float(row["ema_20"])
    ema_50 = float(row["ema_50"])
    ema_200 = float(row["ema_200"])
    atr = float(row["atr"])
    atr_average = float(row["atr_avg_30"])
    if close > ema_200 and ema_20 > ema_50:
        market = "bull"
    elif close < ema_200 and ema_20 < ema_50:
        market = "bear"
    else:
        market = "sideways"
    volatility = "high" if atr_average > 0 and atr > 1.5 * atr_average else "normal"
    return market, volatility


def _signal_for_row(
    frame: pd.DataFrame, index: int, parameters: StrategyParameters,
) -> TradeSignal | None:
    row = frame.iloc[index]
    required = ["ema_20", "ema_50", "ema_200", "ema_20_prev", "ema_50_prev", "rsi", "atr", "atr_avg_30"]
    if any(pd.isna(row[column]) for column in required):
        return None
    price = float(row["close"])
    ema_20 = float(row["ema_20"])
    ema_50 = float(row["ema_50"])
    ema_200 = float(row["ema_200"])
    ema_20_previous = float(row["ema_20_prev"])
    ema_50_previous = float(row["ema_50_prev"])
    rsi = float(row["rsi"])
    atr = float(row["atr"])
    atr_average = float(row["atr_avg_30"])
    market_regime, volatility_regime = _classify_regime(row)
    tanking = price < ema_200 or (ema_20_previous >= ema_50_previous and ema_20 < ema_50)
    volatile = atr_average <= 0 or atr > parameters.volatility_threshold * atr_average
    golden_cross = ema_20_previous <= ema_50_previous and ema_20 > ema_50
    if golden_cross and price > ema_200 and 45 <= rsi <= 55 and not tanking and not volatile:
        stop = price - parameters.atr_stop_multiplier * atr
        target = price + parameters.risk_reward_ratio * (price - stop)
        return TradeSignal(
            direction="BUY",
            trigger_price=price,
            stop_loss=stop,
            target_price=target,
            reason="EMA Golden Cross + Price above 200 EMA + RSI stable zone",
            market_regime=market_regime,
            volatility_regime=volatility_regime,
        )
    reasons: list[str] = []
    if price < ema_50:
        reasons.append("Price below EMA 50")
    if rsi > 75:
        reasons.append(f"RSI overbought ({rsi:.1f})")
    if reasons:
        stop = price + parameters.atr_stop_multiplier * atr
        target = price - parameters.risk_reward_ratio * (stop - price)
        return TradeSignal(
            direction="SELL",
            trigger_price=price,
            stop_loss=stop,
            target_price=target,
            reason=" + ".join(reasons),
            market_regime=market_regime,
            volatility_regime=volatility_regime,
        )
    return None


def _executed_price(raw_price: float, direction: str, entering: bool, costs: ExecutionCosts) -> float:
    adverse_rate = costs.spread_bps / 20_000 + costs.slippage_bps / 10_000
    if (direction == "BUY" and entering) or (direction == "SELL" and not entering):
        return raw_price * (1 + adverse_rate)
    return raw_price * (1 - adverse_rate)


def _exit_trigger(position: OpenPosition, row: pd.Series) -> tuple[float, str] | None:
    open_price = float(row["open"])
    high = float(row["high"])
    low = float(row["low"])
    if position.direction == "BUY":
        if open_price <= position.stop_loss:
            return open_price, "stop_loss_gap"
        if low <= position.stop_loss:
            return position.stop_loss, "stop_loss"
        if open_price >= position.target_price:
            return open_price, "target_gap"
        if high >= position.target_price:
            return position.target_price, "target_hit"
    else:
        if open_price >= position.stop_loss:
            return open_price, "stop_loss_gap"
        if high >= position.stop_loss:
            return position.stop_loss, "stop_loss"
        if open_price <= position.target_price:
            return open_price, "target_gap"
        if low <= position.target_price:
            return position.target_price, "target_hit"
    return None


def _close_trade(
    frame: pd.DataFrame,
    position: OpenPosition,
    exit_index: int,
    raw_exit: float,
    outcome: str,
    costs: ExecutionCosts,
    equity: float,
) -> TradeResult:
    executed_exit = _executed_price(raw_exit, position.direction, False, costs)
    if position.direction == "BUY":
        gross_return = raw_exit / position.raw_entry - 1
        execution_return = executed_exit / position.executed_entry - 1
    else:
        gross_return = 1 - raw_exit / position.raw_entry
        execution_return = 1 - executed_exit / position.executed_entry
    commission_rate = costs.commission_bps / 10_000
    net_return = execution_return - 2 * commission_rate
    cost_return = gross_return - net_return
    return TradeResult(
        direction=position.direction,
        signal_date=_timestamp(frame, position.signal_index),
        entry_date=position.entry_date,
        exit_date=_timestamp(frame, exit_index),
        raw_entry=round(position.raw_entry, 6),
        entry=round(position.executed_entry, 6),
        raw_exit=round(raw_exit, 6),
        exit=round(executed_exit, 6),
        gross_pnl_pct=round(gross_return * 100, 6),
        net_pnl_pct=round(net_return * 100, 6),
        cost_pct=round(cost_return * 100, 6),
        pnl_usd=round(equity * net_return, 2),
        outcome=outcome,
        reason=position.reason,
        market_regime=position.market_regime,
        volatility_regime=position.volatility_regime,
        entry_index=position.entry_index,
        exit_index=exit_index,
    )


def _metrics(
    trades: list[TradeResult],
    bar_returns: list[float],
    initial_capital: float,
    exposure_bars: int,
) -> Metrics:
    net_returns = [trade.net_pnl_pct / 100 for trade in trades]
    gross_returns = [trade.gross_pnl_pct / 100 for trade in trades]
    wins = [value for value in net_returns if value > 0]
    losses = [value for value in net_returns if value <= 0]
    equity = initial_capital
    gross_equity = initial_capital
    total_turnover = 0.0
    for net_return, gross_return in zip(net_returns, gross_returns):
        total_turnover += 2 * equity
        equity *= 1 + net_return
        gross_equity *= 1 + gross_return
    average_return = mean(bar_returns) if bar_returns else 0.0
    volatility = pstdev(bar_returns) if len(bar_returns) > 1 else 0.0
    downside = [min(value, 0.0) for value in bar_returns]
    downside_deviation = sqrt(mean([value * value for value in downside])) if downside else 0.0
    sharpe = sqrt(252) * average_return / volatility if volatility > 0 else 0.0
    sortino = sqrt(252) * average_return / downside_deviation if downside_deviation > 0 else 0.0
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)
    curve_values = [initial_capital]
    for bar_return in bar_returns:
        curve_values.append(curve_values[-1] * (1 + bar_return))
    peak = initial_capital
    peak_index = 0
    underwater = False
    maximum_drawdown = 0.0
    recovery_bars = 0
    for index, value in enumerate(curve_values):
        if value >= peak:
            if underwater:
                recovery_bars = max(recovery_bars, index - peak_index)
            peak = value
            peak_index = index
            underwater = False
        elif peak > 0:
            underwater = True
            maximum_drawdown = max(maximum_drawdown, (peak - value) / peak)
    if underwater:
        recovery_bars = max(recovery_bars, len(curve_values) - 1 - peak_index)
    total_cost = (gross_equity - equity) / initial_capital * 100
    return Metrics(
        total_trades=len(trades),
        wins=len(wins),
        losses=len(losses),
        win_rate_pct=round(len(wins) / len(trades) * 100, 4) if trades else 0.0,
        expectancy_pct=round(mean(net_returns) * 100, 6) if net_returns else 0.0,
        sharpe=round(sharpe, 6),
        sortino=round(sortino, 6),
        profit_factor=round(profit_factor, 6),
        max_drawdown_pct=round(maximum_drawdown * 100, 6),
        recovery_bars=recovery_bars,
        turnover_pct=round(total_turnover / initial_capital * 100, 6),
        exposure_pct=round(exposure_bars / len(bar_returns) * 100, 6) if bar_returns else 0.0,
        gross_return_pct=round((gross_equity / initial_capital - 1) * 100, 6),
        after_cost_return_pct=round((equity / initial_capital - 1) * 100, 6),
        total_cost_pct=round(total_cost, 6),
        final_equity=round(equity, 2),
    )


def simulate_segment(
    frame: pd.DataFrame,
    start: int,
    end: int,
    parameters: StrategyParameters,
    costs: ExecutionCosts,
    initial_capital: float,
) -> SegmentResult:
    if start < 1 or end > len(frame) or start >= end:
        raise ValueError("Invalid backtest segment boundaries")
    bar_returns = [0.0] * (end - start)
    trades: list[TradeResult] = []
    equity = initial_capital
    equity_curve: list[dict[str, float | str]] = [
        {"date": _timestamp(frame, start), "equity": round(equity, 2)}
    ]
    position: OpenPosition | None = None
    exposure_bars = 0
    for index in range(start, end):
        if position is not None and index >= position.entry_index:
            exposure_bars += 1
        if position is not None and index > position.entry_index:
            trigger = _exit_trigger(position, frame.iloc[index])
            if trigger is not None:
                raw_exit, outcome = trigger
                trade = _close_trade(frame, position, index, raw_exit, outcome, costs, equity)
                trades.append(trade)
                net_return = trade.net_pnl_pct / 100
                equity *= 1 + net_return
                bar_returns[index - start] += net_return
                equity_curve.append({"date": trade.exit_date, "equity": round(equity, 2)})
                position = None
        if position is None:
            signal = _signal_for_row(frame, index, parameters)
            if signal is None:
                continue
            entry_index = index + costs.fill_delay_bars
            if entry_index >= end:
                continue
            raw_entry = float(frame.iloc[entry_index]["open"])
            signal_risk = abs(signal.trigger_price - signal.stop_loss)
            if signal.direction == "BUY":
                stop_loss = raw_entry - signal_risk
                target_price = raw_entry + parameters.risk_reward_ratio * signal_risk
            else:
                stop_loss = raw_entry + signal_risk
                target_price = raw_entry - parameters.risk_reward_ratio * signal_risk
            position = OpenPosition(
                direction=signal.direction,
                signal_index=index,
                entry_index=entry_index,
                raw_entry=raw_entry,
                executed_entry=_executed_price(raw_entry, signal.direction, True, costs),
                stop_loss=stop_loss,
                target_price=target_price,
                reason=signal.reason,
                market_regime=signal.market_regime,
                volatility_regime=signal.volatility_regime,
                entry_date=_timestamp(frame, entry_index),
            )
    if position is not None:
        exit_index = end - 1
        raw_exit = float(frame.iloc[exit_index]["close"])
        trade = _close_trade(frame, position, exit_index, raw_exit, "window_end", costs, equity)
        trades.append(trade)
        net_return = trade.net_pnl_pct / 100
        equity *= 1 + net_return
        bar_returns[exit_index - start] += net_return
        equity_curve.append({"date": trade.exit_date, "equity": round(equity, 2)})
    return SegmentResult(
        metrics=_metrics(trades, bar_returns, initial_capital, exposure_bars),
        trades=trades,
        bar_returns=bar_returns,
        equity_curve=equity_curve,
        bars=end - start,
    )


def _score(train: SegmentResult, validation: SegmentResult) -> float:
    if train.metrics.total_trades + validation.metrics.total_trades == 0:
        return -1_000_000.0
    return (
        0.35 * train.metrics.after_cost_return_pct
        + 0.65 * validation.metrics.after_cost_return_pct
        - 0.25 * validation.metrics.max_drawdown_pct
    )


def _combine_segments(segments: list[SegmentResult], initial_capital: float) -> SegmentResult:
    trades: list[TradeResult] = []
    bar_returns: list[float] = []
    exposure_bars = 0
    equity = initial_capital
    curve: list[dict[str, float | str]] = [{"date": "start", "equity": round(equity, 2)}]
    for segment in segments:
        trades.extend(segment.trades)
        bar_returns.extend(segment.bar_returns)
        exposure_bars += round(segment.metrics.exposure_pct / 100 * segment.bars)
        for trade in segment.trades:
            equity *= 1 + trade.net_pnl_pct / 100
            curve.append({"date": trade.exit_date, "equity": round(equity, 2)})
    return SegmentResult(
        metrics=_metrics(trades, bar_returns, initial_capital, exposure_bars),
        trades=trades,
        bar_returns=bar_returns,
        equity_curve=curve,
        bars=len(bar_returns),
    )


def calculate_buy_and_hold(
    symbol: str,
    frame: pd.DataFrame,
    start: int,
    end: int,
    costs: ExecutionCosts,
    initial_capital: float,
) -> BenchmarkResult:
    if end - start < 2:
        raise ValueError("Benchmark window needs at least two candles")
    raw_entry = float(frame.iloc[start]["open"])
    raw_exit = float(frame.iloc[end - 1]["close"])
    entry = _executed_price(raw_entry, "BUY", True, costs)
    exit_price = _executed_price(raw_exit, "BUY", False, costs)
    gross_return = raw_exit / raw_entry - 1
    net_return = exit_price / entry - 1 - 2 * costs.commission_bps / 10_000
    closes = frame.iloc[start:end]["close"].astype(float)
    running_peak = closes.cummax()
    drawdown = ((running_peak - closes) / running_peak).max()
    return BenchmarkResult(
        symbol=symbol,
        gross_return_pct=round(gross_return * 100, 6),
        after_cost_return_pct=round(net_return * 100, 6),
        max_drawdown_pct=round(float(drawdown) * 100, 6),
        final_equity=round(initial_capital * (1 + net_return), 2),
        start_date=_timestamp(frame, start),
        end_date=_timestamp(frame, end - 1),
    )


def _benchmark_for_dates(
    symbol: str,
    frame: pd.DataFrame,
    start_date: pd.Timestamp,
    end_date: pd.Timestamp,
    costs: ExecutionCosts,
    initial_capital: float,
) -> BenchmarkResult | None:
    selected = frame[(frame["timestamp"] >= start_date) & (frame["timestamp"] <= end_date)]
    if len(selected) < 2:
        return None
    return calculate_buy_and_hold(symbol, selected.reset_index(drop=True), 0, len(selected), costs, initial_capital)


def _regime_metrics(trades: list[TradeResult]) -> dict[str, dict[str, float | int]]:
    result: dict[str, dict[str, float | int]] = {}
    labels = sorted({trade.market_regime for trade in trades})
    for label in labels:
        selected = [trade for trade in trades if trade.market_regime == label]
        returns = [trade.net_pnl_pct for trade in selected]
        result[label] = {
            "trades": len(selected),
            "win_rate_pct": round(sum(value > 0 for value in returns) / len(returns) * 100, 4),
            "expectancy_pct": round(mean(returns), 6),
            "after_cost_return_pct": round((pd.Series([1 + value / 100 for value in returns]).prod() - 1) * 100, 6),
        }
    volatility_labels = sorted({trade.volatility_regime for trade in trades})
    for label in volatility_labels:
        selected = [trade for trade in trades if trade.volatility_regime == label]
        returns = [trade.net_pnl_pct for trade in selected]
        result[f"volatility:{label}"] = {
            "trades": len(selected),
            "win_rate_pct": round(sum(value > 0 for value in returns) / len(returns) * 100, 4),
            "expectancy_pct": round(mean(returns), 6),
            "after_cost_return_pct": round((pd.Series([1 + value / 100 for value in returns]).prod() - 1) * 100, 6),
        }
    return result


def _eligibility(metrics: Metrics, thresholds: ValidationThresholds) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if metrics.total_trades < thresholds.minimum_trades:
        reasons.append(f"Need at least {thresholds.minimum_trades} out-of-sample trades")
    if metrics.after_cost_return_pct < thresholds.minimum_after_cost_return_pct:
        reasons.append(f"After-cost return is below {thresholds.minimum_after_cost_return_pct}%")
    if metrics.sharpe < thresholds.minimum_sharpe:
        reasons.append(f"Sharpe is below {thresholds.minimum_sharpe}")
    if metrics.profit_factor < thresholds.minimum_profit_factor:
        reasons.append(f"Profit factor is below {thresholds.minimum_profit_factor}")
    if metrics.max_drawdown_pct > thresholds.maximum_drawdown_pct:
        reasons.append(f"Max drawdown exceeds {thresholds.maximum_drawdown_pct}%")
    return len(reasons) == 0, reasons


def run_walk_forward_backtest(
    ticker: str,
    candles: pd.DataFrame,
    strategy_version: str,
    parameters: list[StrategyParameters],
    costs: ExecutionCosts,
    windows: WindowConfiguration,
    thresholds: ValidationThresholds,
    initial_capital: float,
    benchmark_frames: dict[str, pd.DataFrame] | None = None,
) -> dict[str, object]:
    if costs.fill_delay_bars < 1:
        raise ValueError("fill_delay_bars must be at least 1 to prevent same-bar look-ahead")
    if any(value < 0 for value in [costs.commission_bps, costs.spread_bps, costs.slippage_bps]):
        raise ValueError("Execution costs cannot be negative")
    frame = prepare_candles(candles)
    prepared_benchmarks = {
        symbol: prepare_candles(benchmark)
        for symbol, benchmark in (benchmark_frames or {}).items()
        if not benchmark.empty
    }
    walk_forward_windows = build_walk_forward_windows(len(frame), windows)
    if not walk_forward_windows:
        raise ValueError(
            f"Need at least {windows.minimum_bars} candles for the configured walk-forward windows; received {len(frame)}"
        )
    if not parameters:
        raise ValueError("At least one strategy parameter set is required")
    window_results: list[dict[str, object]] = []
    selected_train_segments: list[SegmentResult] = []
    selected_validation_segments: list[SegmentResult] = []
    test_segments: list[SegmentResult] = []
    sensitivity: dict[str, list[float]] = {
        str(index): [] for index in range(len(parameters))
    }
    selected_counts = {str(index): 0 for index in range(len(parameters))}
    benchmark_windows: dict[str, list[BenchmarkResult]] = {ticker: []}
    for symbol in prepared_benchmarks:
        benchmark_windows[symbol] = []
    for window in walk_forward_windows:
        candidates: list[tuple[float, int, SegmentResult, SegmentResult]] = []
        for parameter_index, candidate in enumerate(parameters):
            train = simulate_segment(
                frame, window.train_start, window.train_end, candidate, costs, initial_capital
            )
            validation = simulate_segment(
                frame, window.validation_start, window.validation_end, candidate, costs, initial_capital
            )
            sensitivity[str(parameter_index)].append(validation.metrics.after_cost_return_pct)
            candidates.append((_score(train, validation), parameter_index, train, validation))
        _, selected_index, train_result, validation_result = max(candidates, key=lambda item: item[0])
        selected = parameters[selected_index]
        selected_counts[str(selected_index)] += 1
        test_result = simulate_segment(
            frame, window.test_start, window.test_end, selected, costs, initial_capital
        )
        selected_train_segments.append(train_result)
        selected_validation_segments.append(validation_result)
        test_segments.append(test_result)
        buy_hold = calculate_buy_and_hold(
            ticker, frame, window.test_start, window.test_end, costs, initial_capital
        )
        benchmark_windows[ticker].append(buy_hold)
        external_results: dict[str, dict[str, float | str] | None] = {}
        test_start_date = frame.iloc[window.test_start]["timestamp"]
        test_end_date = frame.iloc[window.test_end - 1]["timestamp"]
        for symbol, benchmark_frame in prepared_benchmarks.items():
            benchmark = _benchmark_for_dates(
                symbol, benchmark_frame, test_start_date, test_end_date, costs, initial_capital
            )
            external_results[symbol] = benchmark.to_dict() if benchmark else None
            if benchmark:
                benchmark_windows[symbol].append(benchmark)
        window_results.append({
            "window": window.index,
            "boundaries": {
                "train": {
                    "start_index": window.train_start,
                    "end_index": window.train_end - 1,
                    "start_date": _timestamp(frame, window.train_start),
                    "end_date": _timestamp(frame, window.train_end - 1),
                },
                "validation": {
                    "start_index": window.validation_start,
                    "end_index": window.validation_end - 1,
                    "start_date": _timestamp(frame, window.validation_start),
                    "end_date": _timestamp(frame, window.validation_end - 1),
                },
                "test": {
                    "start_index": window.test_start,
                    "end_index": window.test_end - 1,
                    "start_date": _timestamp(frame, window.test_start),
                    "end_date": _timestamp(frame, window.test_end - 1),
                },
            },
            "selected_parameter_index": selected_index,
            "selected_parameters": selected.to_dict(),
            "in_sample": train_result.metrics.to_dict(),
            "validation": validation_result.metrics.to_dict(),
            "out_of_sample": test_result.metrics.to_dict(),
            "buy_and_hold": buy_hold.to_dict(),
            "benchmarks": external_results,
        })
    aggregate_train = _combine_segments(selected_train_segments, initial_capital)
    aggregate_validation = _combine_segments(selected_validation_segments, initial_capital)
    aggregate_test = _combine_segments(test_segments, initial_capital)
    eligible, rejection_reasons = _eligibility(aggregate_test.metrics, thresholds)
    parameter_sensitivity: list[dict[str, object]] = []
    for index, candidate in enumerate(parameters):
        values = sensitivity[str(index)]
        parameter_sensitivity.append({
            "parameter_index": index,
            "parameters": candidate.to_dict(),
            "validation_windows": len(values),
            "mean_validation_return_pct": round(mean(values), 6) if values else 0.0,
            "return_std_dev_pct": round(pstdev(values), 6) if len(values) > 1 else 0.0,
            "positive_window_pct": round(sum(value > 0 for value in values) / len(values) * 100, 4) if values else 0.0,
            "selected_windows": selected_counts[str(index)],
        })
    benchmark_aggregates: dict[str, dict[str, float | int | str]] = {}
    for symbol, results in benchmark_windows.items():
        compounded = 1.0
        gross_compounded = 1.0
        maximum_drawdown = 0.0
        for result in results:
            compounded *= 1 + result.after_cost_return_pct / 100
            gross_compounded *= 1 + result.gross_return_pct / 100
            maximum_drawdown = max(maximum_drawdown, result.max_drawdown_pct)
        benchmark_aggregates[symbol] = {
            "symbol": symbol,
            "windows": len(results),
            "gross_return_pct": round((gross_compounded - 1) * 100, 6),
            "after_cost_return_pct": round((compounded - 1) * 100, 6),
            "max_drawdown_pct": round(maximum_drawdown, 6),
            "final_equity": round(initial_capital * compounded, 2),
        }
    return {
        "ticker": ticker,
        "strategy": {
            "version": strategy_version,
            "parameter_grid": [candidate.to_dict() for candidate in parameters],
        },
        "configuration": {
            "initial_capital": initial_capital,
            "costs": costs.to_dict(),
            "windows": windows.to_dict(),
            "thresholds": thresholds.to_dict(),
            "candles": len(frame),
        },
        "window_count": len(window_results),
        "windows": window_results,
        "aggregate": {
            "in_sample": aggregate_train.metrics.to_dict(),
            "validation": aggregate_validation.metrics.to_dict(),
            "out_of_sample": aggregate_test.metrics.to_dict(),
        },
        "benchmarks": benchmark_aggregates,
        "regimes": _regime_metrics(aggregate_test.trades),
        "parameter_sensitivity": parameter_sensitivity,
        "alert_eligibility": {
            "eligible": eligible,
            "reasons": rejection_reasons,
            "evaluated_on": "out_of_sample",
        },
        "trades": [trade.to_dict() for trade in aggregate_test.trades],
        "equity_curve": aggregate_test.equity_curve,
    }
