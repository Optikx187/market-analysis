"""Pure performance-attribution, excursion and journaling math.

Everything in this module is deliberately free of database and HTTP access so
the reconciliation, cost and MFE/MAE rules stay directly testable.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

UNKNOWN = "Unknown"
NOT_RECORDED = "Not recorded"

DIMENSIONS = ("strategy", "ticker", "asset_type", "sector", "timeframe", "regime")

MISSING_VALUES = {"", "unknown", "none", "null", "n/a"}

CONFIDENCE_BANDS = ((0, 50), (50, 60), (60, 70), (70, 80), (80, 90), (90, 101))


def _money(value: float) -> float:
    return round(value + 0.0, 2)


def normalize_label(value: object) -> str:
    """Group missing metadata under ``Unknown`` instead of dropping it."""
    if value is None:
        return UNKNOWN
    text = str(value).strip()
    if text.lower() in MISSING_VALUES:
        return UNKNOWN
    return text


@dataclass(frozen=True)
class ExitCosts:
    entry_costs_allocated: float
    exit_fees: float
    exit_slippage: float

    @property
    def total(self) -> float:
        return self.entry_costs_allocated + self.exit_fees + self.exit_slippage


def allocate_entry_costs(
    entry_costs: float,
    already_allocated: float,
    quantity_closed: float,
    total_quantity: float,
    is_final_exit: bool,
) -> float:
    """Spread entry costs across exits so a full closure allocates them exactly."""
    if entry_costs <= 0 or total_quantity <= 0:
        return 0.0
    if is_final_exit:
        return max(0.0, entry_costs - already_allocated)
    share = entry_costs * (quantity_closed / total_quantity)
    return max(0.0, min(share, entry_costs - already_allocated))


def gross_exit_pnl(
    direction: str,
    entry_price: float,
    exit_price: float,
    quantity: float,
) -> float:
    """Long P&L is ``(exit - entry) * qty``; short P&L is ``(entry - exit) * qty``."""
    sign = 1 if direction.upper() == "BUY" else -1
    return (exit_price - entry_price) * quantity * sign


def net_exit_pnl(
    direction: str,
    entry_price: float,
    exit_price: float,
    quantity: float,
    costs: ExitCosts,
) -> tuple[float, float]:
    """Return ``(gross_pnl, net_pnl)`` for one exit fill."""
    gross = gross_exit_pnl(direction, entry_price, exit_price, quantity)
    return gross, gross - costs.total


def _parse_timestamp(value: object) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def select_window_candles(
    candles: list[dict[str, object]],
    opened_at: Optional[str],
    closed_at: Optional[str],
) -> list[dict[str, object]]:
    """Keep candles whose timestamp falls inside the holding period.

    Candles are kept when their timestamp cannot be parsed only if the holding
    period itself is unknown, so excursions never mix in unrelated history.
    """
    start = _parse_timestamp(opened_at)
    end = _parse_timestamp(closed_at)
    if start is None and end is None:
        return list(candles)
    window: list[dict[str, object]] = []
    for candle in candles:
        stamp = _parse_timestamp(candle.get("timestamp"))
        if stamp is None:
            continue
        if start is not None and stamp < start.replace(hour=0, minute=0, second=0, microsecond=0):
            continue
        if end is not None and stamp > end:
            continue
        window.append(candle)
    return window


def calculate_excursions(
    direction: str,
    entry_price: float,
    quantity: float,
    candles: list[dict[str, object]],
) -> Optional[dict[str, float]]:
    """Maximum favorable/adverse excursion from candle highs and lows.

    Returns ``None`` when no usable candle covers the holding period so callers
    can record the excursion as unavailable rather than reporting zero.
    """
    if entry_price <= 0 or quantity <= 0:
        return None
    highs = [
        float(candle["high"])
        for candle in candles
        if isinstance(candle.get("high"), (int, float))
    ]
    lows = [
        float(candle["low"])
        for candle in candles
        if isinstance(candle.get("low"), (int, float))
    ]
    if not highs or not lows:
        return None
    highest = max(highs)
    lowest = min(lows)
    if direction.upper() == "BUY":
        favorable_price, adverse_price = highest, lowest
        favorable = max(0.0, highest - entry_price)
        adverse = max(0.0, entry_price - lowest)
    else:
        favorable_price, adverse_price = lowest, highest
        favorable = max(0.0, entry_price - lowest)
        adverse = max(0.0, highest - entry_price)
    return {
        "mfe_usd": _money(favorable * quantity),
        "mae_usd": _money(adverse * quantity),
        "mfe_pct": round(favorable / entry_price * 100, 2),
        "mae_pct": round(adverse / entry_price * 100, 2),
        "mfe_price": favorable_price,
        "mae_price": adverse_price,
        "highest_high": highest,
        "lowest_low": lowest,
    }


def _planned_vs_actual(planned: Optional[float], actual: Optional[float]) -> dict[str, object]:
    if planned is None or actual is None:
        return {
            "planned": planned if planned is not None else NOT_RECORDED,
            "actual": actual if actual is not None else NOT_RECORDED,
            "difference": NOT_RECORDED,
            "difference_pct": NOT_RECORDED,
        }
    difference = actual - planned
    return {
        "planned": round(planned, 4),
        "actual": round(actual, 4),
        "difference": round(difference, 4),
        "difference_pct": round(difference / planned * 100, 2) if planned else NOT_RECORDED,
    }


def build_journal(
    trade: dict[str, object],
    executions: list[dict[str, object]],
    excursions: Optional[dict[str, float]],
) -> dict[str, object]:
    """Post-trade journal for a fully closed trade.

    Missing optional metadata is reported as ``Unknown``/``Not recorded`` rather
    than inferred.
    """
    direction = str(trade.get("direction") or UNKNOWN).upper()
    entry_price = float(trade.get("entry_price") or 0.0)
    quantity = float(trade.get("quantity") or 0.0)
    net_pnl = float(trade.get("net_pnl") or 0.0)
    gross_pnl = float(trade.get("gross_pnl") or 0.0)
    costs = float(trade.get("costs") or 0.0)
    planned_entry = trade.get("planned_entry_price")
    planned_exit = trade.get("planned_exit_price")
    planned_quantity = trade.get("planned_quantity")
    exits = [row for row in executions if str(row.get("kind")).upper() == "EXIT"]
    exit_quantity = sum(float(row.get("quantity") or 0.0) for row in exits)
    average_exit = (
        sum(float(row.get("price") or 0.0) * float(row.get("quantity") or 0.0) for row in exits)
        / exit_quantity
        if exit_quantity > 0
        else None
    )
    stop_loss = trade.get("stop_loss")
    target_price = trade.get("target_price")
    respected_stop = None
    reached_target = None
    if average_exit is not None and isinstance(stop_loss, (int, float)):
        respected_stop = average_exit > stop_loss if direction == "BUY" else average_exit < stop_loss
    if average_exit is not None and isinstance(target_price, (int, float)):
        reached_target = average_exit >= target_price if direction == "BUY" else average_exit <= target_price

    return {
        "trade_id": trade.get("id"),
        "ticker": trade.get("ticker"),
        "setup": {
            "strategy": normalize_label(trade.get("strategy")),
            "strategy_version": normalize_label(trade.get("strategy_version")),
            "direction": direction,
            "asset_type": normalize_label(trade.get("asset_type")),
            "sector": normalize_label(trade.get("sector")),
            "timeframe": normalize_label(trade.get("timeframe")),
            "regime": normalize_label(trade.get("regime")),
            "signal_confidence": (
                trade.get("signal_confidence")
                if trade.get("signal_confidence") is not None
                else NOT_RECORDED
            ),
            "signal_context": trade.get("signal_context") or NOT_RECORDED,
            "execution_context": trade.get("execution_context") or NOT_RECORDED,
        },
        "result": {
            "outcome": "WIN" if net_pnl > 0 else ("LOSS" if net_pnl < 0 else "BREAKEVEN"),
            "gross_pnl": _money(gross_pnl),
            "costs": _money(costs),
            "net_pnl": _money(net_pnl),
            "net_pnl_pct": (
                round(net_pnl / (entry_price * quantity) * 100, 2)
                if entry_price > 0 and quantity > 0
                else NOT_RECORDED
            ),
            "exit_count": len(exits),
            "partial_exits": max(0, len(exits) - 1),
        },
        "excursions": excursions if excursions else {
            "status": "unavailable",
            "mfe_usd": NOT_RECORDED,
            "mae_usd": NOT_RECORDED,
            "mfe_pct": NOT_RECORDED,
            "mae_pct": NOT_RECORDED,
        },
        "planned_vs_actual": {
            "entry": _planned_vs_actual(
                float(planned_entry) if isinstance(planned_entry, (int, float)) else None,
                entry_price,
            ),
            "exit": _planned_vs_actual(
                float(planned_exit) if isinstance(planned_exit, (int, float)) else None,
                average_exit,
            ),
            "size": _planned_vs_actual(
                float(planned_quantity) if isinstance(planned_quantity, (int, float)) else None,
                quantity,
            ),
        },
        "rule_adherence": {
            "planned_entry_recorded": planned_entry is not None,
            "planned_exit_recorded": planned_exit is not None,
            "planned_size_recorded": planned_quantity is not None,
            "size_within_plan": (
                quantity <= float(planned_quantity)
                if isinstance(planned_quantity, (int, float))
                else NOT_RECORDED
            ),
            "stop_respected": respected_stop if respected_stop is not None else NOT_RECORDED,
            "target_reached": reached_target if reached_target is not None else NOT_RECORDED,
            "costs_recorded": costs > 0,
        },
        "fills": [
            {
                "kind": row.get("kind"),
                "price": row.get("price"),
                "quantity": row.get("quantity"),
                "fees": row.get("fees"),
                "slippage": row.get("slippage"),
                "net_pnl": row.get("net_pnl"),
                "executed_at": row.get("executed_at"),
            }
            for row in executions
        ],
    }


def _bucket_stats(
    key: str,
    trades: list[dict[str, object]],
    min_sample_size: int,
) -> dict[str, object]:
    sample_size = len(trades)
    gross = sum(float(trade.get("gross_pnl") or 0.0) for trade in trades)
    costs = sum(float(trade.get("costs") or 0.0) for trade in trades)
    net = sum(float(trade.get("net_pnl") or 0.0) for trade in trades)
    wins = sum(1 for trade in trades if float(trade.get("net_pnl") or 0.0) > 0)
    losses = sum(1 for trade in trades if float(trade.get("net_pnl") or 0.0) < 0)
    confidences = [
        float(trade["signal_confidence"])
        for trade in trades
        if isinstance(trade.get("signal_confidence"), (int, float))
    ]
    sufficient = sample_size >= min_sample_size
    return {
        "key": key,
        "sample_size": sample_size,
        "gross_pnl": _money(gross),
        "costs": _money(costs),
        "net_pnl": _money(net),
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / sample_size * 100, 2) if sample_size else 0.0,
        "avg_net_pnl": _money(net / sample_size) if sample_size else 0.0,
        "avg_signal_confidence": (
            round(sum(confidences) / len(confidences), 2) if confidences else None
        ),
        "sufficient_sample": sufficient,
        "sample_note": "" if sufficient else f"Insufficient history ({sample_size}/{min_sample_size})",
        "recommendation": _recommendation(net, wins, sample_size, sufficient),
    }


def _recommendation(
    net: float,
    wins: int,
    sample_size: int,
    sufficient: bool,
) -> str:
    if not sufficient:
        return "insufficient_history"
    win_rate = wins / sample_size * 100 if sample_size else 0.0
    if net > 0 and win_rate >= 50:
        return "keep_enabled"
    if net < 0:
        return "review_or_disable"
    return "monitor"


def group_attribution(
    trades: list[dict[str, object]],
    dimension: str,
    min_sample_size: int,
) -> list[dict[str, object]]:
    buckets: dict[str, list[dict[str, object]]] = {}
    for trade in trades:
        key = normalize_label(trade.get(dimension))
        buckets.setdefault(key, []).append(trade)
    stats = [_bucket_stats(key, rows, min_sample_size) for key, rows in buckets.items()]
    return sorted(stats, key=lambda bucket: (-bucket["net_pnl"], bucket["key"]))


def confidence_calibration(
    trades: list[dict[str, object]],
    min_sample_size: int,
) -> list[dict[str, object]]:
    """Observed win rate per recorded-confidence band, plus an Unknown band."""
    bands: dict[str, list[dict[str, object]]] = {}
    for trade in trades:
        confidence = trade.get("signal_confidence")
        if not isinstance(confidence, (int, float)):
            bands.setdefault(UNKNOWN, []).append(trade)
            continue
        label = UNKNOWN
        for low, high in CONFIDENCE_BANDS:
            if low <= float(confidence) < high:
                label = f"{low}-{high - 1}%"
                break
        bands.setdefault(label, []).append(trade)
    calibration: list[dict[str, object]] = []
    for label, rows in bands.items():
        stats = _bucket_stats(label, rows, min_sample_size)
        calibration.append({
            "band": label,
            "sample_size": stats["sample_size"],
            "observed_win_rate": stats["win_rate"],
            "avg_signal_confidence": stats["avg_signal_confidence"],
            "net_pnl": stats["net_pnl"],
            "sufficient_sample": stats["sufficient_sample"],
            "sample_note": stats["sample_note"],
            "calibration_gap": (
                round(stats["win_rate"] - stats["avg_signal_confidence"], 2)
                if stats["sufficient_sample"] and stats["avg_signal_confidence"] is not None
                else None
            ),
        })
    return sorted(calibration, key=lambda row: row["band"])


def build_attribution(
    trades: list[dict[str, object]],
    portfolio_total_pnl: float,
    unfiltered_net_pnl: float,
    min_sample_size: int,
) -> dict[str, object]:
    """Summary, per-dimension buckets and exact reconciliation for closed trades."""
    summary = _bucket_stats("all", trades, min_sample_size)
    return {
        "min_sample_size": min_sample_size,
        "summary": {
            "sample_size": summary["sample_size"],
            "gross_pnl": summary["gross_pnl"],
            "costs": summary["costs"],
            "net_pnl": summary["net_pnl"],
            "wins": summary["wins"],
            "losses": summary["losses"],
            "win_rate": summary["win_rate"],
            "avg_net_pnl": summary["avg_net_pnl"],
            "sufficient_sample": summary["sufficient_sample"],
            "sample_note": summary["sample_note"],
        },
        "reconciliation": {
            "attributed_net_pnl": _money(unfiltered_net_pnl),
            "portfolio_total_pnl": _money(portfolio_total_pnl),
            "delta": _money(unfiltered_net_pnl - portfolio_total_pnl),
            "filtered_net_pnl": summary["net_pnl"],
            "reconciles": _money(unfiltered_net_pnl - portfolio_total_pnl) == 0.0,
        },
        "dimensions": {
            dimension: group_attribution(trades, dimension, min_sample_size)
            for dimension in DIMENSIONS
        },
        "confidence_calibration": confidence_calibration(trades, min_sample_size),
    }


CSV_COLUMNS = (
    "trade_id",
    "ticker",
    "direction",
    "strategy",
    "strategy_version",
    "asset_type",
    "sector",
    "timeframe",
    "regime",
    "signal_confidence",
    "entry_price",
    "planned_entry_price",
    "average_exit_price",
    "planned_exit_price",
    "quantity",
    "planned_quantity",
    "gross_pnl",
    "costs",
    "net_pnl",
    "net_pnl_pct",
    "mfe_usd",
    "mae_usd",
    "mfe_pct",
    "mae_pct",
    "excursion_status",
    "exit_count",
    "stop_respected",
    "target_reached",
    "size_within_plan",
    "opened_at",
    "closed_at",
)


def _csv_cell(value: object) -> str:
    if value is None:
        return NOT_RECORDED
    text = str(value)
    if any(character in text for character in (",", '"', "\n")):
        return '"' + text.replace('"', '""') + '"'
    return text


def attribution_csv(trades: list[dict[str, object]]) -> str:
    """One CSV row per attributed closed trade, including costs and adherence."""
    lines = [",".join(CSV_COLUMNS)]
    for trade in trades:
        adherence = trade.get("rule_adherence") or {}
        row = {
            **trade,
            "trade_id": trade.get("id"),
            "strategy": normalize_label(trade.get("strategy")),
            "strategy_version": normalize_label(trade.get("strategy_version")),
            "timeframe": normalize_label(trade.get("timeframe")),
            "regime": normalize_label(trade.get("regime")),
            "sector": normalize_label(trade.get("sector")),
            "stop_respected": adherence.get("stop_respected", NOT_RECORDED)
            if isinstance(adherence, dict) else NOT_RECORDED,
            "target_reached": adherence.get("target_reached", NOT_RECORDED)
            if isinstance(adherence, dict) else NOT_RECORDED,
            "size_within_plan": adherence.get("size_within_plan", NOT_RECORDED)
            if isinstance(adherence, dict) else NOT_RECORDED,
        }
        lines.append(",".join(_csv_cell(row.get(column)) for column in CSV_COLUMNS))
    return "\n".join(lines) + "\n"
