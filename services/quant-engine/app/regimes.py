"""Deterministic market-regime and multi-timeframe classification."""

from dataclasses import asdict, dataclass
import math

import pandas as pd

from app.indicators import add_indicators


TIMEFRAME_WEIGHTS = {
    "stock": {"1d": 0.50, "4h": 0.30, "1h": 0.20},
    "crypto": {"1d": 0.40, "4h": 0.35, "1h": 0.25},
}
SESSION_PROFILES = {
    "stock": "US equity sessions (business days, 09:30-16:00 ET)",
    "crypto": "24/7 UTC sessions",
}


@dataclass(frozen=True)
class RegimeSnapshot:
    trend: str
    volatility: str
    breadth: str
    risk: str
    label: str
    confidence: float
    breadth_pct_above_50: float | None
    asset_type: str
    session_profile: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _number(value: object) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _asset_type(value: str) -> str:
    return "crypto" if value.lower() == "crypto" else "stock"


def _label(trend: str, volatility: str, breadth: str, risk: str) -> str:
    return " · ".join(
        part.replace("_", " ").title()
        for part in (trend, f"{volatility} vol", f"{breadth} breadth", risk)
    )


def unknown_regime(asset_type: str) -> RegimeSnapshot:
    normalized = _asset_type(asset_type)
    return RegimeSnapshot(
        trend="unknown",
        volatility="unknown",
        breadth="unknown",
        risk="unknown",
        label=_label("unknown", "unknown", "unknown", "unknown"),
        confidence=0.0,
        breadth_pct_above_50=None,
        asset_type=normalized,
        session_profile=SESSION_PROFILES[normalized],
    )


def classify_prepared_row(
    row: pd.Series,
    asset_type: str,
    breadth_pct_above_50: float | None = None,
) -> RegimeSnapshot:
    normalized = _asset_type(asset_type)
    close = _number(row.get("close"))
    ema_20 = _number(row.get("ema_20"))
    ema_50 = _number(row.get("ema_50"))
    ema_200 = _number(row.get("ema_200"))
    atr = _number(row.get("atr"))
    atr_average = _number(row.get("atr_avg_30"))
    if None in (close, ema_20, ema_50, ema_200, atr, atr_average):
        return unknown_regime(normalized)

    if close > ema_200 and ema_20 > ema_50:
        trend = "bull"
    elif close < ema_200 and ema_20 < ema_50:
        trend = "bear"
    else:
        trend = "sideways"

    if atr_average <= 0:
        volatility = "unknown"
    elif atr > 1.5 * atr_average:
        volatility = "high"
    elif atr < 0.75 * atr_average:
        volatility = "low"
    else:
        volatility = "normal"

    breadth_value = _number(breadth_pct_above_50)
    if breadth_value is None:
        breadth = "unknown"
    elif breadth_value >= 60:
        breadth = "strong"
    elif breadth_value <= 40:
        breadth = "weak"
    else:
        breadth = "neutral"

    if breadth == "unknown" or volatility == "unknown":
        risk = "unknown"
    elif trend == "bear" or breadth == "weak" or volatility == "high":
        risk = "risk_off"
    elif trend == "bull" and breadth == "strong" and volatility in {"low", "normal"}:
        risk = "risk_on"
    else:
        risk = "neutral"

    known = sum(value != "unknown" for value in (trend, volatility, breadth, risk))
    confidence = round(known / 4 * 100, 1)
    return RegimeSnapshot(
        trend=trend,
        volatility=volatility,
        breadth=breadth,
        risk=risk,
        label=_label(trend, volatility, breadth, risk),
        confidence=confidence,
        breadth_pct_above_50=round(breadth_value, 1) if breadth_value is not None else None,
        asset_type=normalized,
        session_profile=SESSION_PROFILES[normalized],
    )


def prepare_regime_frame(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"open", "high", "low", "close", "volume"}
    if frame.empty or len(frame) < 201 or not required.issubset(frame.columns):
        return pd.DataFrame()
    prepared = frame.copy()
    if "timestamp" in prepared.columns:
        prepared["timestamp"] = pd.to_datetime(prepared["timestamp"], errors="coerce", utc=True)
        prepared = prepared.dropna(subset=["timestamp"]).sort_values("timestamp")
        prepared = prepared.drop_duplicates("timestamp")
    if len(prepared) < 201:
        return pd.DataFrame()
    return add_indicators(prepared.reset_index(drop=True))


def classify_regime(
    frame: pd.DataFrame,
    asset_type: str,
    breadth_pct_above_50: float | None = None,
) -> RegimeSnapshot:
    prepared = prepare_regime_frame(frame)
    if prepared.empty:
        return unknown_regime(asset_type)
    return classify_prepared_row(prepared.iloc[-1], asset_type, breadth_pct_above_50)


def classify_breadth(frames: list[pd.DataFrame]) -> dict[str, object]:
    above = 0
    eligible = 0
    for frame in frames:
        prepared = prepare_regime_frame(frame)
        if prepared.empty:
            continue
        row = prepared.iloc[-1]
        close = _number(row.get("close"))
        ema_50 = _number(row.get("ema_50"))
        if close is None or ema_50 is None:
            continue
        eligible += 1
        above += int(close > ema_50)
    if eligible < 2:
        return {"label": "unknown", "pct_above_50": None, "eligible_assets": eligible}
    percentage = round(above / eligible * 100, 1)
    label = "strong" if percentage >= 60 else "weak" if percentage <= 40 else "neutral"
    return {"label": label, "pct_above_50": percentage, "eligible_assets": eligible}


def _timeframe_trend(frame: pd.DataFrame) -> str:
    required = {"open", "high", "low", "close", "volume"}
    if frame.empty or len(frame) < 50 or not required.issubset(frame.columns):
        return "unknown"
    prepared = frame.copy()
    if "timestamp" in prepared.columns:
        prepared["timestamp"] = pd.to_datetime(prepared["timestamp"], errors="coerce", utc=True)
        prepared = prepared.dropna(subset=["timestamp"]).sort_values("timestamp")
        prepared = prepared.drop_duplicates("timestamp")
    if len(prepared) < 50:
        return "unknown"
    prepared = add_indicators(prepared.reset_index(drop=True))
    row = prepared.iloc[-1]
    close = _number(row.get("close"))
    ema_20 = _number(row.get("ema_20"))
    ema_50 = _number(row.get("ema_50"))
    if None in (close, ema_20, ema_50):
        return "unknown"
    if close > ema_20 > ema_50:
        return "bullish"
    if close < ema_20 < ema_50:
        return "bearish"
    return "mixed"


def timeframe_confluence(
    frames: dict[str, pd.DataFrame],
    direction: str,
    asset_type: str,
) -> dict[str, object]:
    normalized = _asset_type(asset_type)
    weights = TIMEFRAME_WEIGHTS[normalized]
    expected = "bullish" if direction.upper() == "BUY" else "bearish"
    details: dict[str, dict[str, object]] = {}
    available_weight = 0.0
    agreeing_weight = 0.0
    available_count = 0
    for timeframe, weight in weights.items():
        trend = _timeframe_trend(frames.get(timeframe, pd.DataFrame()))
        available = trend != "unknown"
        agrees = trend == expected
        if available:
            available_count += 1
            available_weight += weight
            if agrees:
                agreeing_weight += weight
        details[timeframe] = {
            "trend": trend,
            "available": available,
            "agrees": agrees,
            "weight_pct": round(weight * 100, 1),
        }
    score = round(agreeing_weight / available_weight * 100, 1) if available_weight > 0 else 0.0
    available = available_count >= 2 and bool(details["1d"]["available"])
    return {
        "score": score,
        "available": available,
        "available_timeframes": available_count,
        "expected_trend": expected,
        "details": details,
        "asset_type": normalized,
        "session_profile": SESSION_PROFILES[normalized],
    }


def regime_controls(
    snapshot: RegimeSnapshot,
    direction: str,
    confluence: dict[str, object],
) -> dict[str, object]:
    direction = direction.upper()
    favorable_trend = (direction == "BUY" and snapshot.trend == "bull") or (
        direction == "SELL" and snapshot.trend == "bear"
    )
    adverse_trend = (direction == "BUY" and snapshot.trend == "bear") or (
        direction == "SELL" and snapshot.trend == "bull"
    )
    fit = 90.0 if favorable_trend else 20.0 if adverse_trend else 55.0
    favorable_risk = (direction == "BUY" and snapshot.risk == "risk_on") or (
        direction == "SELL" and snapshot.risk == "risk_off"
    )
    adverse_risk = (direction == "BUY" and snapshot.risk == "risk_off") or (
        direction == "SELL" and snapshot.risk == "risk_on"
    )
    if favorable_risk:
        fit += 10
    elif adverse_risk:
        fit -= 20
    if snapshot.volatility == "high":
        fit -= 20
    elif snapshot.volatility == "low":
        fit += 5
    fit = round(max(0.0, min(100.0, fit)), 1)

    if snapshot.asset_type == "crypto":
        multiplier = 0.80
    else:
        multiplier = 1.0
    if snapshot.risk in {"unknown", "neutral"}:
        multiplier *= 0.70
    elif adverse_risk:
        multiplier *= 0.25
    if snapshot.volatility == "high":
        multiplier *= 0.50
    elif snapshot.volatility == "unknown":
        multiplier *= 0.60

    confluence_available = bool(confluence.get("available", False))
    confluence_score = _number(confluence.get("score")) or 0.0
    if confluence_available:
        multiplier *= max(0.40, confluence_score / 100)
    else:
        multiplier *= 0.75
    allowed = fit >= 40 and (not confluence_available or confluence_score >= 50)
    reasons: list[str] = []
    if fit < 40:
        reasons.append(f"Regime fit {fit:.1f} is below 40.0")
    if confluence_available and confluence_score < 50:
        reasons.append(f"Timeframe agreement {confluence_score:.1f}% is below 50.0%")
    return {
        "allowed": allowed,
        "fit_score": fit,
        "size_multiplier": round(max(0.0, min(1.0, multiplier)), 4),
        "reasons": reasons,
    }
