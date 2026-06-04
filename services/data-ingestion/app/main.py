"""Service A — Data Ingestion Service: price streaming & historical data."""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db, init_db, async_session
from app.models import Asset, AssetType, Candle
from app.ingestion import refresh_asset_data, load_candles

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SEED_ASSETS = [
    {"ticker": "SPY", "name": "S&P 500 ETF", "asset_type": AssetType.STOCK},
    {"ticker": "BTC", "name": "Bitcoin", "asset_type": AssetType.CRYPTO},
    {"ticker": "ETH", "name": "Ethereum", "asset_type": AssetType.CRYPTO},
]


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


async def seed_defaults():
    async with async_session() as db:
        for a in SEED_ASSETS:
            res = await db.execute(select(Asset).where(Asset.ticker == a["ticker"]))
            if res.scalar_one_or_none() is None:
                db.add(Asset(**a))
        await db.commit()
    logger.info("Default assets seeded")


async def initial_fetch():
    async with async_session() as db:
        result = await db.execute(select(Asset).where(Asset.is_active == True))
        assets = result.scalars().all()
    for asset in assets:
        try:
            await refresh_asset_data(asset.ticker, asset.asset_type)
        except Exception as e:
            logger.error(f"Failed initial fetch for {asset.ticker}: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await seed_defaults()
    asyncio.create_task(initial_fetch())
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
    if existing.scalar_one_or_none():
        raise HTTPException(400, "Asset already exists")
    asset_type = AssetType.CRYPTO if payload.asset_type.lower() == "crypto" else AssetType.STOCK
    asset = Asset(
        ticker=payload.ticker.upper(), name=payload.name, asset_type=asset_type,
    )
    db.add(asset)
    await db.commit()
    await db.refresh(asset)
    asyncio.create_task(refresh_asset_data(asset.ticker, asset.asset_type))
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


@app.post("/api/data/refresh/{ticker}")
async def refresh_data(ticker: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Asset).where(Asset.ticker == ticker.upper()))
    asset = result.scalar_one_or_none()
    if not asset:
        raise HTTPException(404, "Asset not found")
    await refresh_asset_data(asset.ticker, asset.asset_type)
    return {"status": "refreshed", "ticker": ticker.upper()}
