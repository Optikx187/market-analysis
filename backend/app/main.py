"""Market Analysis Platform — FastAPI application."""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db, init_db, async_session
from app.models import (
    Asset,
    AssetType,
    Signal,
    SignalDirection,
    Trade,
    TradeStatus,
    SystemLog,
    WebhookEndpoint,
)
from app.schemas import (
    AssetCreate,
    AssetResponse,
    SignalResponse,
    TradeResponse,
    PortfolioResponse,
    BacktestRequest,
    BacktestResponse,
    SystemLogResponse,
    TickerSearchResult,
    WebhookCreate,
    WebhookResponse,
    WebhookTestResponse,
)
from app.data_ingestion import refresh_asset_data, load_candles
from app.quant_engine import evaluate_signals
from app.paper_trading import (
    execute_paper_trade,
    get_portfolio_metrics,
    snapshot_equity,
    get_or_create_portfolio,
    check_and_close_trades,
)
from app.notifications import send_signal_notification, build_signal_payload, send_api_webhook
from app.backtester import run_backtest

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SEED_ASSETS = [
    {"ticker": "SPY", "name": "S&P 500 ETF", "asset_type": AssetType.STOCK},
    {"ticker": "BTC", "name": "Bitcoin", "asset_type": AssetType.CRYPTO},
    {"ticker": "ETH", "name": "Ethereum", "asset_type": AssetType.CRYPTO},
]


async def seed_default_assets():
    async with async_session() as db:
        for asset_data in SEED_ASSETS:
            result = await db.execute(
                select(Asset).where(Asset.ticker == asset_data["ticker"])
            )
            if result.scalar_one_or_none() is None:
                asset = Asset(**asset_data)
                db.add(asset)
        await db.commit()
    logger.info("Default assets seeded")


async def initial_data_fetch():
    """Fetch historical data for all active assets on startup."""
    async with async_session() as db:
        result = await db.execute(select(Asset).where(Asset.is_active == True))
        assets = result.scalars().all()

    for asset in assets:
        try:
            await refresh_asset_data(asset.ticker, asset.asset_type)
        except Exception as e:
            logger.error(f"Failed to fetch data for {asset.ticker}: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await seed_default_assets()
    # Fetch initial data in background
    asyncio.create_task(initial_data_fetch())
    yield


app = FastAPI(title="Market Analysis Platform", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ──── Assets / Watchlist ────


@app.get("/api/assets", response_model=list[AssetResponse])
async def list_assets(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Asset).order_by(Asset.ticker))
    return result.scalars().all()


@app.post("/api/assets", response_model=AssetResponse)
async def add_asset(payload: AssetCreate, db: AsyncSession = Depends(get_db)):
    existing = await db.execute(select(Asset).where(Asset.ticker == payload.ticker.upper()))
    if existing.scalar_one_or_none():
        raise HTTPException(400, "Asset already exists")

    asset_type = AssetType.CRYPTO if payload.asset_type.lower() == "crypto" else AssetType.STOCK
    asset = Asset(
        ticker=payload.ticker.upper(),
        name=payload.name,
        asset_type=asset_type,
    )
    db.add(asset)
    await db.commit()
    await db.refresh(asset)

    # Fetch historical data in background
    asyncio.create_task(refresh_asset_data(asset.ticker, asset.asset_type))
    log = SystemLog(level="INFO", message=f"Added {asset.ticker} to watchlist")
    db.add(log)
    await db.commit()
    return asset


@app.delete("/api/assets/{ticker}")
async def remove_asset(ticker: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Asset).where(Asset.ticker == ticker.upper()))
    asset = result.scalar_one_or_none()
    if not asset:
        raise HTTPException(404, "Asset not found")
    asset.is_active = False
    await db.commit()
    return {"status": "removed", "ticker": ticker.upper()}


@app.get("/api/assets/search", response_model=list[TickerSearchResult])
async def search_tickers(q: str):
    """Search for tickers using yfinance."""
    import yfinance as yf

    results = []
    try:
        ticker = yf.Ticker(q.upper())
        info = ticker.info
        if info and info.get("symbol"):
            asset_type = "crypto" if info.get("quoteType") == "CRYPTOCURRENCY" else "stock"
            results.append(TickerSearchResult(
                ticker=info["symbol"],
                name=info.get("shortName", info["symbol"]),
                asset_type=asset_type,
            ))
    except Exception:
        pass
    return results


# ──── Data / Candles ────


@app.get("/api/candles/{ticker}")
async def get_candles(ticker: str, interval: str = "1d", db: AsyncSession = Depends(get_db)):
    df = await load_candles(db, ticker.upper(), interval)
    if df.empty:
        return []
    records = df.to_dict(orient="records")
    for r in records:
        if hasattr(r["timestamp"], "isoformat"):
            r["timestamp"] = r["timestamp"].isoformat()
    return records


@app.post("/api/data/refresh/{ticker}")
async def refresh_data(ticker: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Asset).where(Asset.ticker == ticker.upper()))
    asset = result.scalar_one_or_none()
    if not asset:
        raise HTTPException(404, "Asset not found in watchlist")
    await refresh_asset_data(asset.ticker, asset.asset_type)
    return {"status": "refreshed", "ticker": ticker.upper()}


# ──── Signals / Analysis ────


@app.post("/api/signals/analyze/{ticker}", response_model=SignalResponse | None)
async def analyze_ticker(ticker: str, db: AsyncSession = Depends(get_db)):
    """Run quantitative analysis on a ticker and generate signal if conditions met."""
    df = await load_candles(db, ticker.upper())
    if df.empty or len(df) < 201:
        raise HTTPException(400, f"Insufficient data for {ticker} (need 201+ candles)")

    signal_result = evaluate_signals(df)
    if signal_result is None:
        return None

    # Store signal
    signal = Signal(
        ticker=ticker.upper(),
        direction=signal_result.direction,
        trigger_price=signal_result.trigger_price,
        stop_loss=signal_result.stop_loss,
        target_price=signal_result.target_price,
        reason=signal_result.reason,
        risk_reward=signal_result.risk_reward,
        atr_value=signal_result.atr_value,
        rsi_value=signal_result.rsi_value,
        suppressed=signal_result.suppressed,
    )
    db.add(signal)
    await db.commit()
    await db.refresh(signal)

    # Execute paper trade if not suppressed
    if not signal_result.suppressed and signal_result.direction == SignalDirection.BUY:
        await execute_paper_trade(
            db, ticker.upper(), signal_result.direction,
            signal_result.trigger_price, signal_result.stop_loss, signal_result.target_price,
        )

    # Fetch active API webhooks
    wh_result = await db.execute(
        select(WebhookEndpoint).where(WebhookEndpoint.is_active == True)
    )
    webhooks = [
        {"id": w.id, "name": w.name, "url": w.url, "secret": w.secret}
        for w in wh_result.scalars().all()
    ]

    # Send notifications (Telegram + Discord + API webhooks)
    await send_signal_notification(
        ticker.upper(),
        signal_result.direction,
        signal_result.trigger_price,
        signal_result.reason,
        signal_result.stop_loss,
        signal_result.target_price,
        signal_result.suppressed,
        webhooks=webhooks,
    )

    return signal


@app.get("/api/signals", response_model=list[SignalResponse])
async def list_signals(limit: int = 50, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Signal).order_by(desc(Signal.created_at)).limit(limit)
    )
    return result.scalars().all()


# ──── Paper Trading / Portfolio ────


@app.get("/api/portfolio", response_model=PortfolioResponse)
async def get_portfolio(db: AsyncSession = Depends(get_db)):
    return await get_portfolio_metrics(db)


@app.get("/api/trades", response_model=list[TradeResponse])
async def list_trades(status: str | None = None, limit: int = 50, db: AsyncSession = Depends(get_db)):
    query = select(Trade).order_by(desc(Trade.opened_at)).limit(limit)
    if status:
        query = query.where(Trade.status == TradeStatus(status))
    result = await db.execute(query)
    return result.scalars().all()


@app.get("/api/trades/open", response_model=list[TradeResponse])
async def list_open_trades(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Trade).where(Trade.status == TradeStatus.OPEN).order_by(desc(Trade.opened_at))
    )
    return result.scalars().all()


# ──── Backtest ────


@app.post("/api/backtest", response_model=BacktestResponse)
async def run_backtest_endpoint(payload: BacktestRequest, db: AsyncSession = Depends(get_db)):
    df = await load_candles(db, payload.ticker.upper())
    if df.empty or len(df) < 201:
        raise HTTPException(400, f"Insufficient data for {payload.ticker}")

    result = run_backtest(df, payload.ticker.upper())
    return BacktestResponse(
        ticker=result.ticker,
        total_trades=result.total_trades,
        wins=result.wins,
        losses=result.losses,
        win_rate=result.win_rate,
        total_pnl=round(result.total_pnl, 2),
        max_drawdown=round(result.max_drawdown, 2),
        profit_factor=round(result.profit_factor, 2),
        initial_balance=result.initial_balance,
        final_balance=round(result.final_balance, 2),
        equity_curve=result.equity_curve,
        trades=[{
            "direction": t.direction,
            "entry_price": t.entry_price,
            "exit_price": t.exit_price,
            "pnl": round(t.pnl, 2),
            "pnl_pct": round(t.pnl_pct, 2),
        } for t in result.trades],
    )


# ──── API Webhooks ────


@app.get("/api/webhooks", response_model=list[WebhookResponse])
async def list_webhooks(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(WebhookEndpoint).order_by(WebhookEndpoint.created_at)
    )
    return result.scalars().all()


@app.post("/api/webhooks", response_model=WebhookResponse)
async def add_webhook(payload: WebhookCreate, db: AsyncSession = Depends(get_db)):
    webhook = WebhookEndpoint(
        name=payload.name,
        url=payload.url,
        secret=payload.secret,
    )
    db.add(webhook)
    log = SystemLog(level="INFO", message=f"Added API webhook: {payload.name}")
    db.add(log)
    await db.commit()
    await db.refresh(webhook)
    return webhook


@app.delete("/api/webhooks/{webhook_id}")
async def remove_webhook(webhook_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(WebhookEndpoint).where(WebhookEndpoint.id == webhook_id)
    )
    webhook = result.scalar_one_or_none()
    if not webhook:
        raise HTTPException(404, "Webhook not found")
    await db.delete(webhook)
    log = SystemLog(level="INFO", message=f"Removed API webhook: {webhook.name}")
    db.add(log)
    await db.commit()
    return {"status": "removed", "id": webhook_id}


@app.patch("/api/webhooks/{webhook_id}/toggle", response_model=WebhookResponse)
async def toggle_webhook(webhook_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(WebhookEndpoint).where(WebhookEndpoint.id == webhook_id)
    )
    webhook = result.scalar_one_or_none()
    if not webhook:
        raise HTTPException(404, "Webhook not found")
    webhook.is_active = not webhook.is_active
    status = "enabled" if webhook.is_active else "disabled"
    log = SystemLog(level="INFO", message=f"API webhook {webhook.name} {status}")
    db.add(log)
    await db.commit()
    await db.refresh(webhook)
    return webhook


@app.post("/api/webhooks/{webhook_id}/test", response_model=WebhookTestResponse)
async def test_webhook(webhook_id: int, db: AsyncSession = Depends(get_db)):
    """Send a test signal payload to a specific webhook endpoint."""
    result = await db.execute(
        select(WebhookEndpoint).where(WebhookEndpoint.id == webhook_id)
    )
    webhook = result.scalar_one_or_none()
    if not webhook:
        raise HTTPException(404, "Webhook not found")

    test_payload = build_signal_payload(
        ticker="TEST",
        direction=SignalDirection.BUY,
        trigger_price=50000.0,
        reason="Test signal — webhook connectivity check",
        stop_loss=48500.0,
        target_price=54500.0,
        suppressed=False,
    )
    ok, status_code, error = await send_api_webhook(
        webhook.url, test_payload, webhook.secret
    )
    return WebhookTestResponse(
        webhook_id=webhook.id,
        name=webhook.name,
        url=webhook.url,
        success=ok,
        status_code=status_code,
        error=error,
    )


# ──── System Logs ────


@app.get("/api/logs", response_model=list[SystemLogResponse])
async def list_logs(limit: int = 100, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(SystemLog).order_by(desc(SystemLog.created_at)).limit(limit)
    )
    return result.scalars().all()


# ──── Health ────


@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "1.0.0"}
