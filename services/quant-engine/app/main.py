"""Service B — Quantitative Analytics & Risk Engine."""

import logging

import httpx
import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional

from app.config import settings
from app.risk_engine import evaluate_risk_profile
from app.signals import evaluate_signals

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Quant Engine Service", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)


class SignalResponse(BaseModel):
    ticker: str
    direction: Optional[str]
    status: str
    trigger_price: float
    stop_loss: float
    target_price: float
    reason: str
    risk_reward: float
    atr_value: float
    rsi_value: float
    asset_type: str = "stock"
    suppressed: bool
    kelly_pct: float
    optimal_size_usd: float
    volatility_scalar: float


class RiskProfileResponse(BaseModel):
    ticker: str
    is_tanking: bool
    tanking_reason: Optional[str]
    win_rate_30d: float
    risk_reward_ratio: float
    kelly_fraction: float
    volatility_scalar: float
    optimal_position_pct: float
    optimal_position_usd: float
    atr_current: float
    atr_avg_30: float
    ema_20: float
    ema_50: float
    ema_200: float
    rsi: float
    current_price: float
    recommend_liquidate: bool


class AnalyzeRequest(BaseModel):
    ticker: str
    available_capital: float = 10_000.0
    asset_type: str = "stock"


async def fetch_candles_from_service_a(ticker: str) -> pd.DataFrame:
    url = f"{settings.DATA_INGESTION_URL}/api/candles/{ticker}"
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    if not data:
        return pd.DataFrame()
    return pd.DataFrame(data)


@app.get("/health")
async def health():
    return {"status": "healthy", "service": "quant-engine"}


@app.post("/api/analyze", response_model=Optional[SignalResponse])
async def analyze(req: AnalyzeRequest):
    df = await fetch_candles_from_service_a(req.ticker)
    if df.empty or len(df) < 201:
        raise HTTPException(400, f"Insufficient data for {req.ticker} (need 201+ candles)")

    result = evaluate_signals(df, req.available_capital)
    if result is None:
        return None

    return SignalResponse(
        ticker=req.ticker,
        direction=result.direction,
        status=result.status,
        trigger_price=result.trigger_price,
        stop_loss=result.stop_loss,
        target_price=result.target_price,
        reason=result.reason,
        risk_reward=result.risk_reward,
        atr_value=result.atr_value,
        rsi_value=result.rsi_value,
        suppressed=result.suppressed,
        kelly_pct=result.kelly_pct,
        optimal_size_usd=result.optimal_size_usd,
        volatility_scalar=result.volatility_scalar,
        asset_type=req.asset_type,
    )


@app.post("/api/risk-profile", response_model=Optional[RiskProfileResponse])
async def risk_profile(req: AnalyzeRequest):
    df = await fetch_candles_from_service_a(req.ticker)
    if df.empty or len(df) < 201:
        raise HTTPException(400, f"Insufficient data for {req.ticker}")

    profile = evaluate_risk_profile(df, req.ticker, req.available_capital)
    if profile is None:
        return None

    return RiskProfileResponse(
        ticker=profile.ticker,
        is_tanking=profile.is_tanking,
        tanking_reason=profile.tanking_reason,
        win_rate_30d=profile.win_rate_30d,
        risk_reward_ratio=profile.risk_reward_ratio,
        kelly_fraction=profile.kelly_fraction,
        volatility_scalar=profile.volatility_scalar,
        optimal_position_pct=profile.optimal_position_pct,
        optimal_position_usd=profile.optimal_position_usd,
        atr_current=profile.atr_current,
        atr_avg_30=profile.atr_avg_30,
        ema_20=profile.ema_20,
        ema_50=profile.ema_50,
        ema_200=profile.ema_200,
        rsi=profile.rsi,
        current_price=profile.current_price,
        recommend_liquidate=profile.recommend_liquidate,
    )
