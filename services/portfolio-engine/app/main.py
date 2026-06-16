"""Service C — Portfolio & Integration Engine."""

import datetime
import base64
import logging
import os
import stat
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import httpx
from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import select, desc, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db, init_db, async_session
from app.models import (
    Trade, TradeStatus, SignalDirection, Portfolio, EquitySnapshot, AlertLog, CredentialSecret,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


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
    opened_at: Optional[datetime.datetime]
    closed_at: Optional[datetime.datetime]
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
    open_positions: int
    equity_curve: list[dict]


class SignalInput(BaseModel):
    ticker: str
    direction: str
    status: str
    trigger_price: float
    stop_loss: float
    target_price: float
    reason: str
    risk_reward: float
    atr_value: float
    rsi_value: float
    suppressed: bool
    kelly_pct: float
    optimal_size_usd: float
    volatility_scalar: float
    asset_type: str = "stock"


class SignalDecision(BaseModel):
    ticker: str
    direction: str
    status: str
    approved: bool
    trigger_price: float
    stop_loss: float
    target_price: float
    optimal_size_usd: float
    kelly_pct: float
    capital_overspend: bool
    reason: str
    paper_trade_executed: bool


class AlertLogResponse(BaseModel):
    id: int
    ticker: str
    direction: str
    status: str
    trigger_price: float
    stop_loss: Optional[float]
    target_price: Optional[float]
    optimal_size_usd: Optional[float]
    kelly_pct: Optional[float]
    capital_overspend: bool
    message: Optional[str]
    created_at: Optional[datetime.datetime]
    model_config = {"from_attributes": True}


class CredentialSaveRequest(BaseModel):
    credentials: dict[str, str]
    overwrite: bool = False


class CredentialRevealRequest(BaseModel):
    key: str


POSITION_SIZE_PCT = 0.02

PROVIDER_KEYS = {
    "binance": ["BINANCE_API_KEY", "BINANCE_API_SECRET"],
    "alpaca": ["ALPACA_API_KEY", "ALPACA_API_SECRET"],
    "telegram": ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"],
    "discord": ["DISCORD_WEBHOOK_URL"],
}

LOSS_TOLERANCE_KEY = "LOSS_TOLERANCE_PCT"


def _provider_for_key(key: str) -> str:
    for provider, keys in PROVIDER_KEYS.items():
        if key in keys:
            return provider
    return "unknown"


def _mask(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 6:
        return "•" * len(value)
    return f"{value[:3]}{'•' * 6}{value[-3:]}"


def _encode_secret(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode()).decode()


def _decode_secret(value: str) -> str:
    try:
        return base64.urlsafe_b64decode(value.encode()).decode()
    except Exception:
        return value


async def _get_secret(db: AsyncSession, key: str) -> Optional[CredentialSecret]:
    result = await db.execute(select(CredentialSecret).where(CredentialSecret.key == key))
    return result.scalar_one_or_none()


async def _save_secret(
    db: AsyncSession, key: str, value: str, verified: bool, last_error: Optional[str] = None,
    overwrite: bool = False,
) -> bool:
    existing = await _get_secret(db, key)
    if existing and existing.verified and not overwrite:
        return False
    if existing is None:
        existing = CredentialSecret(provider=_provider_for_key(key), key=key, value=_encode_secret(value))
        db.add(existing)
    else:
        existing.value = _encode_secret(value)
    existing.verified = verified
    existing.last_error = last_error
    return True


async def get_or_create_portfolio(db: AsyncSession) -> Portfolio:
    result = await db.execute(select(Portfolio).limit(1))
    portfolio = result.scalar_one_or_none()
    if portfolio is None:
        portfolio = Portfolio(
            balance=settings.INITIAL_BALANCE,
            equity=settings.INITIAL_BALANCE,
            peak_equity=settings.INITIAL_BALANCE,
        )
        db.add(portfolio)
        await db.commit()
        await db.refresh(portfolio)
    return portfolio


async def execute_paper_trade(
    db: AsyncSession, ticker: str, direction: SignalDirection,
    entry_price: float, stop_loss: float, target_price: float,
    quantity_override: Optional[float] = None,
) -> Optional[Trade]:
    portfolio = await get_or_create_portfolio(db)
    risk_per_share = abs(entry_price - stop_loss)
    if risk_per_share <= 0:
        return None
    max_risk = portfolio.equity * POSITION_SIZE_PCT
    quantity = quantity_override if quantity_override else max_risk / risk_per_share
    cost = quantity * entry_price
    if cost > portfolio.balance:
        quantity = portfolio.balance / entry_price
        cost = quantity * entry_price
    if quantity <= 0 or cost <= 0:
        return None
    trade = Trade(
        ticker=ticker, direction=direction, entry_price=entry_price,
        quantity=quantity, stop_loss=stop_loss, target_price=target_price,
        status=TradeStatus.OPEN,
    )
    db.add(trade)
    portfolio.balance -= cost
    await db.commit()
    await db.refresh(trade)
    return trade


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(title="Portfolio Engine Service", version="2.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"status": "healthy", "service": "portfolio-engine"}


@app.post("/api/process-signal", response_model=SignalDecision)
async def process_signal(signal: SignalInput, db: AsyncSession = Depends(get_db)):
    """Receive a signal from quant-engine, validate against guardrails, execute paper trade."""
    # Simple capital guardrail: position size should not exceed 5% of portfolio value
    portfolio = await db.execute(select(Portfolio).limit(1))
    portfolio_row = portfolio.scalar_one_or_none()
    portfolio_value = portfolio_row.equity if portfolio_row else settings.INITIAL_BALANCE
    
    position_limit = portfolio_value * 0.05  # 5% position limit
    overspend = signal.optimal_size_usd and signal.optimal_size_usd > position_limit
    
    approved = not signal.suppressed and not overspend

    paper_trade_executed = False
    if approved and signal.direction in ("BUY", "SELL"):
        trade = await execute_paper_trade(
            db, signal.ticker,
            SignalDirection(signal.direction),
            signal.trigger_price, signal.stop_loss, signal.target_price,
        )
        paper_trade_executed = trade is not None

    reason_parts = [signal.reason]
    if signal.suppressed:
        reason_parts.append(f"Signal suppressed ({signal.status})")
    if overspend:
        reason_parts.append(f"Position size ${signal.optimal_size_usd:.2f} exceeds 5% portfolio limit (${position_limit:.2f})")

    alert = AlertLog(
        ticker=signal.ticker,
        direction=signal.direction or "NONE",
        status=signal.status,
        trigger_price=signal.trigger_price,
        stop_loss=signal.stop_loss,
        target_price=signal.target_price,
        optimal_size_usd=signal.optimal_size_usd,
        kelly_pct=signal.kelly_pct,
        capital_overspend=overspend,
        message=" | ".join(reason_parts),
    )
    db.add(alert)
    await db.commit()

    return SignalDecision(
        ticker=signal.ticker,
        direction=signal.direction or "NONE",
        status=signal.status,
        approved=approved,
        trigger_price=signal.trigger_price,
        stop_loss=signal.stop_loss,
        target_price=signal.target_price,
        optimal_size_usd=signal.optimal_size_usd,
        kelly_pct=signal.kelly_pct,
        capital_overspend=overspend,
        reason=" | ".join(reason_parts),
        paper_trade_executed=paper_trade_executed,
    )


@app.get("/api/portfolio", response_model=PortfolioResponse)
async def get_portfolio(db: AsyncSession = Depends(get_db)):
    portfolio = await get_or_create_portfolio(db)
    total_trades = portfolio.win_count + portfolio.loss_count
    win_rate = (portfolio.win_count / total_trades * 100) if total_trades > 0 else 0

    open_result = await db.execute(select(Trade).where(Trade.status == TradeStatus.OPEN))
    open_positions = len(open_result.scalars().all())

    result = await db.execute(select(Trade).where(Trade.status == TradeStatus.CLOSED))
    closed = result.scalars().all()
    gross_profit = sum(t.pnl for t in closed if t.pnl and t.pnl > 0)
    gross_loss = abs(sum(t.pnl for t in closed if t.pnl and t.pnl < 0))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0

    snap_result = await db.execute(
        select(EquitySnapshot).order_by(EquitySnapshot.timestamp).limit(500),
    )
    snapshots = snap_result.scalars().all()
    equity_curve = [
        {"timestamp": s.timestamp.isoformat() if s.timestamp else "", "equity": s.equity}
        for s in snapshots
    ]

    return PortfolioResponse(
        balance=round(portfolio.balance, 2),
        equity=round(portfolio.equity, 2),
        total_pnl=round(portfolio.total_pnl, 2),
        win_count=portfolio.win_count,
        loss_count=portfolio.loss_count,
        win_rate=round(win_rate, 2),
        max_drawdown=round(portfolio.max_drawdown, 2),
        profit_factor=round(profit_factor, 2),
        peak_equity=round(portfolio.peak_equity, 2),
        open_positions=open_positions,
        equity_curve=equity_curve,
    )


@app.get("/api/trades", response_model=list[TradeResponse])
async def list_trades(
    status: Optional[str] = None, limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    query = select(Trade).order_by(desc(Trade.opened_at)).limit(limit)
    if status:
        query = query.where(Trade.status == TradeStatus(status))
    result = await db.execute(query)
    return result.scalars().all()


class ManualTradeInput(BaseModel):
    ticker: str
    direction: str  # "BUY" or "SELL"
    entry_price: float
    quantity: float
    stop_loss: Optional[float] = None
    target_price: Optional[float] = None


@app.post("/api/trades/manual", response_model=TradeResponse)
async def log_manual_trade(payload: ManualTradeInput, db: AsyncSession = Depends(get_db)):
    """Log a manually executed trade for tracking purposes."""
    portfolio = await get_or_create_portfolio(db)
    if payload.entry_price <= 0 or payload.quantity <= 0:
        raise HTTPException(400, "Entry price and quantity must be positive")
    direction = SignalDirection(payload.direction.upper())
    if direction == SignalDirection.SELL:
        stop = payload.stop_loss if payload.stop_loss is not None else payload.entry_price * 1.05
        target = payload.target_price if payload.target_price is not None else payload.entry_price * 0.85
    else:
        stop = payload.stop_loss if payload.stop_loss is not None else payload.entry_price * 0.95
        target = payload.target_price if payload.target_price is not None else payload.entry_price * 1.15
    trade = Trade(
        ticker=payload.ticker.upper(),
        direction=direction,
        entry_price=payload.entry_price,
        quantity=payload.quantity,
        stop_loss=stop,
        target_price=target,
        status=TradeStatus.OPEN,
    )
    db.add(trade)
    position_cost = payload.entry_price * payload.quantity
    if position_cost > portfolio.balance:
        raise HTTPException(400, f"Insufficient balance: need ${position_cost:,.2f} but only ${portfolio.balance:,.2f} available")
    portfolio.balance -= position_cost
    await db.commit()
    await db.refresh(trade)
    return trade


@app.get("/api/alerts", response_model=list[AlertLogResponse])
async def list_alerts(limit: int = 50, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(AlertLog).order_by(desc(AlertLog.created_at)).limit(limit),
    )
    return result.scalars().all()


@app.get("/api/settings/credentials")
async def credential_status(db: AsyncSession = Depends(get_db)):
    return await credential_status_all(db)


@app.get("/api/settings/credentials/all")
async def credential_status_all(db: AsyncSession = Depends(get_db)):
    """Aggregate masked credential status from DB and current env."""
    result = await db.execute(select(CredentialSecret))
    rows = result.scalars().all()
    by_key = {r.key: r for r in rows}
    env = _read_env(_find_env_path())
    providers = {}
    for provider, keys in PROVIDER_KEYS.items():
        configured = []
        verified = []
        masked = {}
        errors = {}
        for key in keys:
            row = by_key.get(key)
            raw = _decode_secret(row.value) if row else env.get(key, "")
            if raw:
                configured.append(key)
                masked[key] = _mask(raw)
            if row and row.verified:
                verified.append(key)
            if row and row.last_error:
                errors[key] = row.last_error
        providers[provider] = {
            "configured": len(configured) > 0,
            "verified": bool(verified) or (len(configured) == len(keys)),
            "configured_keys": configured,
            "verified_keys": verified,
            "masked": masked,
            "errors": errors,
        }
    return providers


def _find_env_path() -> Path:
    """Find the host-mounted .env when available, otherwise project root."""
    explicit = os.getenv("HOST_ENV_PATH")
    project_root = Path(__file__).resolve().parent.parent.parent.parent
    candidates = [Path(explicit)] if explicit else []
    candidates.extend([project_root / ".env", Path("/workspace/.env"), Path(".env")])
    logger.info(f"Looking for .env file. HOST_ENV_PATH={explicit}, candidates={candidates}")
    for p in candidates:
        exists = p.exists()
        logger.info(f"Checking {p}: exists={exists}")
        if exists:
            logger.info(f"Found .env file at: {p}")
            return p
    logger.warning(f"No .env file found, returning default: {project_root / '.env'}")
    return project_root / ".env"


def _read_env(path: Path) -> dict[str, str]:
    """Read key=value pairs from a .env file."""
    env: dict[str, str] = {}
    logger.info(f"Reading env from: {path}, exists={path.exists()}")
    if not path.exists():
        logger.warning(f"Env file does not exist: {path}")
        return env
    content = path.read_text()
    logger.info(f"Env file content length: {len(content)} chars")
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _write_env(path: Path, env: dict[str, str]) -> None:
    """Write key=value pairs to .env, preserving comments from .env.example."""
    example_path = path.parent / ".env.example"
    lines: list[str] = []
    written_keys: set[str] = set()

    template_path = example_path if example_path.exists() else None
    if template_path:
        for line in template_path.read_text().splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                lines.append(line)
                continue
            if "=" in stripped:
                key = stripped.split("=", 1)[0].strip()
                if key in env:
                    lines.append(f"{key}={env[key]}")
                    written_keys.add(key)
                else:
                    lines.append(line)

    for k, v in env.items():
        if k not in written_keys:
            lines.append(f"{k}={v}")

    path.write_text("\n".join(lines) + "\n")
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


@app.post("/api/settings/credentials/save")
async def save_credentials(req: CredentialSaveRequest, db: AsyncSession = Depends(get_db)):
    """Save credentials to DB and .env without overwriting verified secrets by default."""
    allowed_keys = {key for keys in PROVIDER_KEYS.values() for key in keys}
    filtered = {k: v for k, v in req.credentials.items() if k in allowed_keys and v}
    if not filtered:
        raise HTTPException(400, "No valid credentials provided")

    env_path = _find_env_path()
    existing = _read_env(env_path)
    saved = []
    skipped = []
    for key, value in filtered.items():
        changed = await _save_secret(db, key, value, verified=True, overwrite=req.overwrite)
        if changed:
            existing[key] = value
            saved.append(key)
        else:
            skipped.append(key)
    await db.commit()
    _write_env(env_path, existing)

    logger.info(f"Credentials saved via UI: {saved}")
    return {
        "saved": saved,
        "skipped": skipped,
        "message": "Credentials saved and synced to .env.",
    }


@app.post("/api/settings/credentials/reveal")
async def reveal_credential(req: CredentialRevealRequest, db: AsyncSession = Depends(get_db)):
    if req.key not in {key for keys in PROVIDER_KEYS.values() for key in keys}:
        raise HTTPException(400, "Credential key is not allowed")
    row = await _get_secret(db, req.key)
    if row:
        return {"key": req.key, "value": _decode_secret(row.value)}
    return {"key": req.key, "value": _read_env(_find_env_path()).get(req.key, "")}




@app.get("/api/settings/onboarding")
async def onboarding_status():
    """Check if the user has completed onboarding (has market data API credentials)."""
    env_path = _find_env_path()
    env = _read_env(env_path)
    # Only require market data APIs to skip onboarding
    has_market_data_cred = any(
        env.get(k)
        for k in [
            "BINANCE_API_KEY",
            "ALPACA_API_KEY",
        ]
    )
    has_any_asset = False
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{settings.DATA_INGESTION_URL}/api/assets")
            if resp.status_code == 200:
                has_any_asset = len(resp.json()) > 0
    except Exception:
        pass

    return {
        "completed": has_market_data_cred,
        "has_credentials": has_market_data_cred,
        "has_assets": has_any_asset,
    }


# Environment settings that can be viewed and adjusted
ADJUSTABLE_SETTINGS = {
    "RISK_REWARD_RATIO": {"type": "float", "default": 3.0, "description": "Target risk/reward ratio for signals"},
    "ATR_STOP_MULTIPLIER": {"type": "float", "default": 1.5, "description": "ATR multiplier for stop loss calculation"},
    "ATR_VOLATILITY_THRESHOLD": {"type": "float", "default": 2.0, "description": "ATR threshold for volatility filtering"},
    "TRAILING_STOP_PCT": {"type": "float", "default": 0.02, "description": "Trailing stop percentage (as decimal)"},
    "INITIAL_BALANCE": {"type": "float", "default": 10000.0, "description": "Initial paper trading balance"},
    "LOSS_TOLERANCE_PCT": {"type": "float", "default": 0.02, "description": "Max loss tolerance per trade as % of balance (e.g. 0.02 = 2%)"},
}

@app.get("/api/settings/env")
async def get_env_settings():
    """Get current environment settings (non-sensitive, viewable/adjustable)."""
    env_path = _find_env_path()
    env = _read_env(env_path)
    
    settings = {}
    for key, meta in ADJUSTABLE_SETTINGS.items():
        value = env.get(key)
        if value is not None:
            try:
                if meta["type"] == "float":
                    value = float(value)
                elif meta["type"] == "int":
                    value = int(value)
            except ValueError:
                value = meta["default"]
        else:
            value = meta["default"]
        settings[key] = {
            "value": value,
            "default": meta["default"],
            "type": meta["type"],
            "description": meta["description"],
        }
    return settings


@app.get("/api/settings/env-debug")
async def debug_env_path():
    """Debug endpoint to check env file path detection."""
    explicit = os.getenv("HOST_ENV_PATH")
    project_root = Path(__file__).resolve().parent.parent.parent.parent
    candidates = [Path(explicit)] if explicit else []
    candidates.extend([project_root / ".env", Path("/workspace/.env"), Path(".env")])
    
    results = []
    for p in candidates:
        results.append({
            "path": str(p),
            "exists": p.exists(),
            "is_file": p.is_file() if p.exists() else False,
        })
    
    chosen = _find_env_path()
    env_data = _read_env(chosen)
    
    return {
        "host_env_path": explicit,
        "project_root": str(project_root),
        "candidates": results,
        "chosen_path": str(chosen),
        "chosen_exists": chosen.exists(),
        "env_keys": list(env_data.keys()),
        "sample_values": {k: env_data.get(k) for k in list(env_data.keys())[:5]},
    }


@app.post("/api/settings/env")
async def update_env_setting(payload: dict):
    """Update a single environment setting."""
    key = payload.get("key")
    value = payload.get("value")
    
    if key not in ADJUSTABLE_SETTINGS:
        raise HTTPException(400, f"Setting {key} is not adjustable")
    
    meta = ADJUSTABLE_SETTINGS[key]
    try:
        if meta["type"] == "float":
            value = float(value)
        elif meta["type"] == "int":
            value = int(value)
    except ValueError:
        raise HTTPException(400, f"Invalid value type for {key}, expected {meta['type']}")
    
    env_path = _find_env_path()
    env = _read_env(env_path)
    env[key] = str(value)
    _write_env(env_path, env)
    
    return {"key": key, "value": value, "message": f"{key} updated successfully"}


class BalanceUpdateRequest(BaseModel):
    balance: float


@app.post("/api/portfolio/balance")
async def update_portfolio_balance(req: BalanceUpdateRequest, db: AsyncSession = Depends(get_db)):
    """Update the current portfolio balance. Use this to sync real account balance."""
    if req.balance < 0:
        raise HTTPException(400, "Balance cannot be negative")
    portfolio = await get_or_create_portfolio(db)
    old_balance = portfolio.balance

    # Account for capital locked in open paper trades
    open_result = await db.execute(select(Trade).where(Trade.status == TradeStatus.OPEN))
    open_trades = open_result.scalars().all()
    locked_capital = sum(t.quantity * t.entry_price for t in open_trades)

    portfolio.equity = req.balance
    portfolio.balance = req.balance - locked_capital
    if portfolio.balance < 0:
        portfolio.balance = 0.0
    if req.balance > portfolio.peak_equity:
        portfolio.peak_equity = req.balance
    await db.commit()
    return {
        "previous_balance": round(old_balance, 2),
        "new_balance": round(portfolio.balance, 2),
        "equity": round(req.balance, 2),
        "locked_in_positions": round(locked_capital, 2),
        "message": f"Balance updated from ${old_balance:,.2f} to ${req.balance:,.2f}",
    }


@app.get("/api/portfolio/recommendation")
async def trade_recommendation(
    ticker: str,
    current_price: float,
    db: AsyncSession = Depends(get_db),
):
    """Get a balance-aware trade recommendation with suggested $ amounts."""
    portfolio = await get_or_create_portfolio(db)
    env_path = _find_env_path()
    env = _read_env(env_path)
    loss_tolerance = float(env.get("LOSS_TOLERANCE_PCT", "0.02"))
    risk_reward = float(env.get("RISK_REWARD_RATIO", "3.0"))
    atr_multiplier = float(env.get("ATR_STOP_MULTIPLIER", "1.5"))

    trailing_stop_pct = float(env.get("TRAILING_STOP_PCT", "0.02"))

    max_loss_amount = portfolio.balance * loss_tolerance
    stop_distance_pct = trailing_stop_pct * atr_multiplier
    suggested_stop = current_price * (1 - stop_distance_pct)
    suggested_target = current_price * (1 + stop_distance_pct * risk_reward)
    risk_per_unit = current_price - suggested_stop
    suggested_quantity = max_loss_amount / risk_per_unit if risk_per_unit > 0 else 0
    suggested_position_usd = suggested_quantity * current_price
    if portfolio.balance > 0 and suggested_position_usd > portfolio.balance:
        suggested_quantity = portfolio.balance / current_price
        suggested_position_usd = portfolio.balance
    position_pct = (suggested_position_usd / portfolio.balance * 100) if portfolio.balance > 0 else 0

    return {
        "ticker": ticker,
        "account_balance": round(portfolio.balance, 2),
        "loss_tolerance_pct": loss_tolerance,
        "max_loss_amount": round(max_loss_amount, 2),
        "current_price": round(current_price, 2),
        "suggested_stop_loss": round(suggested_stop, 2),
        "suggested_target": round(suggested_target, 2),
        "suggested_quantity": round(suggested_quantity, 4),
        "suggested_position_usd": round(suggested_position_usd, 2),
        "position_pct_of_balance": round(position_pct, 2),
        "risk_reward_ratio": risk_reward,
    }
