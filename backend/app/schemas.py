"""Pydantic schemas for API request/response."""

from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime


class AssetCreate(BaseModel):
    ticker: str
    name: str
    asset_type: str  # "crypto" or "stock"


class AssetResponse(BaseModel):
    id: int
    ticker: str
    name: str
    asset_type: str
    is_active: bool

    model_config = {"from_attributes": True}


class SignalResponse(BaseModel):
    id: int
    ticker: str
    direction: str
    trigger_price: float
    stop_loss: float
    target_price: float
    reason: Optional[str]
    risk_reward: Optional[float]
    atr_value: Optional[float]
    rsi_value: Optional[float]
    suppressed: bool
    created_at: Optional[datetime]

    model_config = {"from_attributes": True}


class TradeResponse(BaseModel):
    id: int
    ticker: str
    direction: str
    entry_price: float
    exit_price: Optional[float]
    quantity: float
    stop_loss: float
    target_price: float
    trailing_stop: Optional[float]
    status: str
    pnl: Optional[float]
    pnl_pct: Optional[float]
    opened_at: Optional[datetime]
    closed_at: Optional[datetime]

    model_config = {"from_attributes": True}


class PortfolioResponse(BaseModel):
    balance: float
    equity: float
    total_pnl: float
    win_count: int
    loss_count: int
    win_rate: float
    max_drawdown: float
    profit_factor: float
    peak_equity: float
    equity_curve: List[dict]


class BacktestRequest(BaseModel):
    ticker: str


class BacktestResponse(BaseModel):
    ticker: str
    total_trades: int
    wins: int
    losses: int
    win_rate: float
    total_pnl: float
    max_drawdown: float
    profit_factor: float
    initial_balance: float
    final_balance: float
    equity_curve: List[float]
    trades: List[dict]


class SystemLogResponse(BaseModel):
    id: int
    level: str
    message: str
    created_at: Optional[datetime]

    model_config = {"from_attributes": True}


class TickerSearchResult(BaseModel):
    ticker: str
    name: str
    asset_type: str


class NotificationConfig(BaseModel):
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    discord_webhook_url: Optional[str] = None


class WebhookCreate(BaseModel):
    name: str
    url: str
    secret: Optional[str] = None


class WebhookResponse(BaseModel):
    id: int
    name: str
    url: str
    is_active: bool
    secret: Optional[str] = None
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class WebhookTestResponse(BaseModel):
    webhook_id: int
    name: str
    url: str
    success: bool
    status_code: Optional[int] = None
    error: Optional[str] = None
