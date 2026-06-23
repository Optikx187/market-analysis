"""Service A — Data Ingestion Service: price streaming & historical data."""

import asyncio
import logging
from datetime import datetime, timezone
from contextlib import asynccontextmanager

import httpx
import yfinance as yf
from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db, init_db, async_session
from app.models import Asset, AssetType, Candle
from app.ingestion import refresh_asset_data, load_candles
from app.ingestion import get_binance_symbol, get_crypto_name, CRYPTO_NAMES

CRYPTO_NAMES_SET = set(CRYPTO_NAMES.keys())

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_api_call_log: dict[str, str] = {}
_service_start_time: str = datetime.now(timezone.utc).isoformat()

# Connectivity tracking for Issue #12
_connectivity: dict[str, dict] = {
    "binance": {"online": False, "last_checked": None, "last_online": None, "last_offline": None},
    "yahoo": {"online": False, "last_checked": None, "last_online": None, "last_offline": None},
    "alpaca": {"online": False, "last_checked": None, "last_online": None, "last_offline": None},
}
_downtime_log: list[dict] = []


def record_api_success(provider: str) -> None:
    _api_call_log[provider] = datetime.now(timezone.utc).isoformat()


def _update_connectivity(provider: str, is_online: bool) -> None:
    now = datetime.now(timezone.utc).isoformat()
    state = _connectivity.get(provider)
    if not state:
        return
    was_online = state["online"]
    state["last_checked"] = now
    state["online"] = is_online
    if is_online:
        state["last_online"] = now
        if not was_online and state["last_offline"]:
            _downtime_log.append({
                "provider": provider,
                "went_offline": state["last_offline"],
                "came_online": now,
            })
            if len(_downtime_log) > 100:
                _downtime_log.pop(0)
    else:
        if was_online or state["last_offline"] is None:
            state["last_offline"] = now


class AssetCreate(BaseModel):
    ticker: str
    name: str
    asset_type: str


class AssetResponse(BaseModel):
    id: int
    ticker: str
    name: str
    asset_type: str
    is_active: bool
    model_config = {"from_attributes": True}


class SymbolLookupResponse(BaseModel):
    ticker: str
    name: str
    asset_type: str
    recognized: bool


class QuoteResponse(BaseModel):
    ticker: str
    name: str
    asset_type: str
    price: float | None
    change_pct: float | None
    volume: float | None
    updated_at: str


async def initial_fetch():
    async with async_session() as db:
        result = await db.execute(select(Asset).where(Asset.is_active == True))
        assets = result.scalars().all()
    for asset in assets:
        try:
            await refresh_asset_data(asset.ticker, asset.asset_type)
        except Exception as e:
            logger.error(f"Failed initial fetch for {asset.ticker}: {e}")


async def _check_provider_health(
    provider: str, url: str, timeout: int = 10, *, any_response_ok: bool = False
) -> bool:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url)
            if any_response_ok:
                return True
            resp.raise_for_status()
        return True
    except httpx.HTTPStatusError:
        return False
    except Exception:
        return False


async def _health_check_loop():
    """Periodically check external API connectivity and auto-backfill on reconnect."""
    await asyncio.sleep(5)  # initial delay
    first_check = True
    while True:
        try:
            binance_ok = await _check_provider_health(
                "binance", "https://api.binance.com/api/v3/ping"
            )
            was_binance_offline = not _connectivity["binance"]["online"]
            _update_connectivity("binance", binance_ok)
            if binance_ok:
                record_api_success("binance")

            yahoo_ok = await _check_provider_health(
                "yahoo",
                "https://query2.finance.yahoo.com/v8/finance/chart/SPY",
                any_response_ok=True,
            )
            was_yahoo_offline = not _connectivity["yahoo"]["online"]
            _update_connectivity("yahoo", yahoo_ok)
            if yahoo_ok:
                record_api_success("yahoo")

            # Alpaca health check (only if configured)
            alpaca_ok = False
            if settings.ALPACA_API_KEY and settings.ALPACA_API_SECRET:
                alpaca_ok = await _check_provider_health(
                    "alpaca",
                    "https://paper-api.alpaca.markets/v2/clock",
                )
                _update_connectivity("alpaca", alpaca_ok)
                if alpaca_ok:
                    record_api_success("alpaca")

            # Auto-backfill on reconnect (skip first check — initial_fetch handles startup)
            if not first_check:
                if (binance_ok and was_binance_offline) or (yahoo_ok and was_yahoo_offline):
                    logger.info("Provider reconnected — triggering data backfill")
                    asyncio.create_task(_backfill_all_assets())
            first_check = False

        except Exception as e:
            logger.error(f"Health check error: {e}")
        await asyncio.sleep(60)  # check every 60 seconds


async def _backfill_all_assets():
    """Re-fetch historical data for all active assets after a connectivity restore."""
    try:
        async with async_session() as db:
            result = await db.execute(select(Asset).where(Asset.is_active == True))
            assets = result.scalars().all()
        for asset in assets:
            try:
                await refresh_asset_data(asset.ticker, asset.asset_type)
                logger.info(f"Backfilled data for {asset.ticker}")
            except Exception as e:
                logger.warning(f"Backfill failed for {asset.ticker}: {e}")
    except Exception as e:
        logger.error(f"Backfill error: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    asyncio.create_task(initial_fetch())
    asyncio.create_task(_health_check_loop())
    yield


app = FastAPI(title="Data Ingestion Service", version="2.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"status": "healthy", "service": "data-ingestion"}


@app.get("/api/assets", response_model=list[AssetResponse])
async def list_assets(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Asset).where(Asset.is_active == True).order_by(Asset.ticker))
    return result.scalars().all()


@app.post("/api/assets", response_model=AssetResponse)
async def add_asset(payload: AssetCreate, db: AsyncSession = Depends(get_db)):
    existing = await db.execute(
        select(Asset).where(Asset.ticker == payload.ticker.upper()),
    )
    existing_assets = existing.scalars().all()
    if existing_assets:
        active = [a for a in existing_assets if a.is_active]
        if active:
            raise HTTPException(400, "Asset already exists")
        # Reactivate the first inactive asset
        existing_asset = existing_assets[0]
        existing_asset.is_active = True
        existing_asset.name = payload.name
        await db.commit()
        await db.refresh(existing_asset)
        asyncio.create_task(refresh_asset_data(existing_asset.ticker, existing_asset.asset_type))
        return existing_asset
    asset_type = AssetType.CRYPTO if payload.asset_type.lower() == "crypto" else AssetType.STOCK
    asset = Asset(
        ticker=payload.ticker.upper(), name=payload.name, asset_type=asset_type,
    )
    db.add(asset)
    await db.commit()
    await db.refresh(asset)
    asyncio.create_task(refresh_asset_data(asset.ticker, asset.asset_type))
    return asset


class AssetImportItem(BaseModel):
    ticker: str
    name: str
    asset_type: str


@app.get("/api/assets/export")
async def export_assets(db: AsyncSession = Depends(get_db)):
    """Export all active assets as a JSON list."""
    result = await db.execute(select(Asset).where(Asset.is_active == True).order_by(Asset.ticker))
    assets = result.scalars().all()
    return [{"ticker": a.ticker, "name": a.name, "asset_type": a.asset_type} for a in assets]


@app.post("/api/assets/import")
async def import_assets(items: list[AssetImportItem], db: AsyncSession = Depends(get_db)):
    """Bulk-import assets. Skips duplicates and reactivates soft-deleted tickers."""
    added = []
    skipped = []
    reactivated = []
    for item in items:
        t = item.ticker.upper()
        existing = await db.execute(select(Asset).where(Asset.ticker == t))
        existing_assets = existing.scalars().all()
        if existing_assets:
            active = [a for a in existing_assets if a.is_active]
            if active:
                skipped.append(t)
                continue
            ex = existing_assets[0]
            ex.is_active = True
            ex.name = item.name
            reactivated.append(t)
            continue
        asset_type = AssetType.CRYPTO if item.asset_type.lower() == "crypto" else AssetType.STOCK
        asset = Asset(ticker=t, name=item.name, asset_type=asset_type)
        db.add(asset)
        added.append(t)
    await db.commit()
    # Trigger background data refresh for new/reactivated assets
    for t in added + reactivated:
        r = await db.execute(select(Asset).where(Asset.ticker == t))
        a = r.scalar_one_or_none()
        if a:
            asyncio.create_task(refresh_asset_data(a.ticker, a.asset_type))
    return {"added": added, "reactivated": reactivated, "skipped": skipped, "total_imported": len(added) + len(reactivated)}


@app.delete("/api/assets/{ticker}")
async def remove_asset(ticker: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Asset).where(Asset.ticker == ticker.upper(), Asset.is_active == True)
    )
    assets = result.scalars().all()
    if not assets:
        raise HTTPException(404, "Asset not found")
    for asset in assets:
        asset.is_active = False
    await db.commit()
    return {"status": "removed", "ticker": ticker.upper()}


@app.post("/api/assets/{ticker}/refresh")
async def refresh_asset(ticker: str, db: AsyncSession = Depends(get_db)):
    """Manually refresh historical data for an asset."""
    result = await db.execute(select(Asset).where(Asset.ticker == ticker.upper()))
    asset = result.scalar_one_or_none()
    if not asset:
        raise HTTPException(404, "Asset not found")
    if not asset.is_active:
        raise HTTPException(400, "Asset is not active")
    
    try:
        await refresh_asset_data(asset.ticker, asset.asset_type)
        # Check how many candles were stored
        df = await load_candles(db, asset.ticker, "1d")
        return {"status": "success", "ticker": ticker.upper(), "candles": len(df)}
    except Exception as e:
        raise HTTPException(500, f"Failed to refresh data: {str(e)}")


@app.get("/api/candles/{ticker}")
async def get_candles(
    ticker: str, interval: str = "1d", db: AsyncSession = Depends(get_db),
):
    df = await load_candles(db, ticker.upper(), interval)
    if df.empty:
        return []
    records = df.to_dict(orient="records")
    for r in records:
        if hasattr(r["timestamp"], "isoformat"):
            r["timestamp"] = r["timestamp"].isoformat()
    return records


@app.get("/api/assets/candle-counts")
async def get_all_candle_counts(db: AsyncSession = Depends(get_db)):
    """Return candle counts for all active assets in a single request."""
    result = await db.execute(select(Asset).where(Asset.is_active == True))
    assets = result.scalars().all()
    counts: dict[str, int] = {}
    for asset in assets:
        count_result = await db.execute(
            select(Candle.id).where(Candle.ticker == asset.ticker, Candle.interval == "1d")
        )
        counts[asset.ticker] = len(count_result.all())
    return counts


@app.post("/api/assets/refresh-all")
async def refresh_all_assets(db: AsyncSession = Depends(get_db)):
    """Refresh historical data for all active assets."""
    result = await db.execute(select(Asset).where(Asset.is_active == True))
    assets = result.scalars().all()
    results: dict[str, dict] = {}
    for asset in assets:
        try:
            await refresh_asset_data(asset.ticker, asset.asset_type)
            df = await load_candles(db, asset.ticker, "1d")
            results[asset.ticker] = {"status": "success", "candles": len(df)}
        except Exception as e:
            results[asset.ticker] = {"status": "error", "error": str(e)}
    return {"refreshed": len([r for r in results.values() if r["status"] == "success"]), "total": len(assets), "details": results}


@app.get("/api/symbols/lookup/{ticker}", response_model=SymbolLookupResponse)
async def lookup_symbol(ticker: str, asset_type: str = "stock"):
    ticker = ticker.upper()
    if asset_type.lower() == "crypto":
        symbol = get_binance_symbol(ticker)
        crypto_name = get_crypto_name(ticker)
        is_known = ticker in CRYPTO_NAMES_SET
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get("https://api.binance.com/api/v3/ticker/price", params={"symbol": symbol})
                resp.raise_for_status()
            record_api_success("binance")
            return SymbolLookupResponse(ticker=ticker, name=crypto_name, asset_type="crypto", recognized=True)
        except Exception:
            # Known crypto tickers are recognized even without Binance validation
            return SymbolLookupResponse(ticker=ticker, name=crypto_name, asset_type="crypto", recognized=is_known)
    has_market_data = False
    try:
        fi = yf.Ticker(ticker).fast_info
        has_market_data = fi is not None and hasattr(fi, "last_price") and fi.last_price is not None and fi.last_price > 0
    except Exception:
        has_market_data = False
    name = ticker
    try:
        t = yf.Ticker(ticker)
        info = t.info or {}
        name = (
            info.get("shortName")
            or info.get("longName")
            or info.get("displayName")
            or ticker
        )
    except Exception:
        pass
    recognized = has_market_data
    if recognized:
        record_api_success("yahoo")
    return SymbolLookupResponse(ticker=ticker, name=name, asset_type="stock", recognized=recognized)


async def _alpaca_stock_quote(ticker: str) -> QuoteResponse | None:
    """Try to get a stock quote from Alpaca. Returns None if unconfigured or fails."""
    if not settings.ALPACA_API_KEY or not settings.ALPACA_API_SECRET:
        return None
    headers = {
        "APCA-API-KEY-ID": settings.ALPACA_API_KEY,
        "APCA-API-SECRET-KEY": settings.ALPACA_API_SECRET,
    }
    now = datetime.now(timezone.utc).isoformat()
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            snap_resp = await client.get(
                f"https://data.alpaca.markets/v2/stocks/{ticker}/snapshot",
                headers=headers,
            )
            snap_resp.raise_for_status()
            snap = snap_resp.json()
        trade = snap.get("latestTrade") or {}
        prev_bar = snap.get("prevDailyBar") or {}
        daily_bar = snap.get("dailyBar") or {}
        price = trade.get("p")
        prev_close = prev_bar.get("c")
        volume = daily_bar.get("v")
        change_pct = round(((price - prev_close) / prev_close) * 100, 2) if price and prev_close else None
        record_api_success("alpaca")
        return QuoteResponse(
            ticker=ticker, name=ticker, asset_type="stock",
            price=float(price) if price else None,
            change_pct=change_pct,
            volume=float(volume) if volume else None,
            updated_at=now,
        )
    except Exception as e:
        logger.debug(f"Alpaca quote failed for {ticker}: {e}")
        return None


@app.get("/api/quotes/{ticker}", response_model=QuoteResponse)
async def get_quote(ticker: str, asset_type: str = "stock"):
    ticker = ticker.upper()
    now = datetime.now(timezone.utc).isoformat()
    if asset_type.lower() == "crypto":
        symbol = get_binance_symbol(ticker)
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get("https://api.binance.com/api/v3/ticker/24hr", params={"symbol": symbol})
            resp.raise_for_status()
            data = resp.json()
        record_api_success("binance")
        return QuoteResponse(
            ticker=ticker,
            name=ticker,
            asset_type="crypto",
            price=float(data.get("lastPrice")) if data.get("lastPrice") else None,
            change_pct=float(data.get("priceChangePercent")) if data.get("priceChangePercent") else None,
            volume=float(data.get("volume")) if data.get("volume") else None,
            updated_at=now,
        )
    # Stocks: try Alpaca first, then Yahoo
    alpaca_quote = await _alpaca_stock_quote(ticker)
    if alpaca_quote:
        return alpaca_quote
    info = yf.Ticker(ticker).fast_info
    if info:
        record_api_success("yahoo")
    last = getattr(info, "last_price", None) or (info.get("last_price") if isinstance(info, dict) else None)
    previous = getattr(info, "previous_close", None) or (info.get("previous_close") if isinstance(info, dict) else None)
    volume = getattr(info, "last_volume", None) or (info.get("last_volume") if isinstance(info, dict) else None)
    change_pct = ((last - previous) / previous * 100) if last and previous else None
    name = ticker
    try:
        name = yf.Ticker(ticker).info.get("shortName") or ticker
    except Exception:
        pass
    return QuoteResponse(
        ticker=ticker,
        name=name,
        asset_type="stock",
        price=float(last) if last else None,
        change_pct=round(change_pct, 2) if change_pct is not None else None,
        volume=float(volume) if volume else None,
        updated_at=now,
    )


@app.get("/api/status")
async def system_status():
    """Return service uptime, API call timestamps, and connectivity state."""
    return {
        "service": "data-ingestion",
        "started_at": _service_start_time,
        "current_time": datetime.now(timezone.utc).isoformat(),
        "last_api_calls": _api_call_log,
        "connectivity": _connectivity,
        "downtime_log": _downtime_log[-10:],
    }


@app.get("/api/settings/credentials")
async def credential_status():
    """Return which credential groups are configured (without exposing values)."""
    return {
        "binance": bool(settings.BINANCE_API_KEY and settings.BINANCE_API_SECRET),
        "alpaca": bool(settings.ALPACA_API_KEY and settings.ALPACA_API_SECRET),
    }


@app.post("/api/data/refresh/{ticker}")
async def refresh_data(ticker: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Asset).where(Asset.ticker == ticker.upper()))
    asset = result.scalar_one_or_none()
    if not asset:
        raise HTTPException(404, "Asset not found")
    await refresh_asset_data(asset.ticker, asset.asset_type)
    return {"status": "refreshed", "ticker": ticker.upper()}
