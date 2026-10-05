import pytest

from app import ingestion, main
from app.models import AssetType


@pytest.mark.asyncio
async def test_fake_market_boundary_never_calls_external_providers(monkeypatch) -> None:
    class ForbiddenClient:
        def __init__(self, *args, **kwargs) -> None:
            raise AssertionError("sandbox attempted an outbound HTTP call")

    def forbidden_ticker(*args, **kwargs):
        raise AssertionError("sandbox attempted a Yahoo Finance call")

    monkeypatch.setattr(ingestion.settings, "MARKET_DATA_MODE", "fake")
    monkeypatch.setattr(main.settings, "MARKET_DATA_MODE", "fake")
    monkeypatch.setattr(main.httpx, "AsyncClient", ForbiddenClient)
    monkeypatch.setattr(main.yf, "Ticker", forbidden_ticker)
    monkeypatch.setattr(ingestion.yf, "Ticker", forbidden_ticker)

    candles = await ingestion.fetch_historical("BTC", AssetType.CRYPTO, "1d")
    lookup = await main.lookup_symbol("AAPL", "stock")
    quote = await main.get_quote("BTC", "crypto")
    earnings = await main.get_earnings("AAPL")

    assert len(candles) == 400
    assert lookup.recognized is True
    assert quote.price is not None
    assert earnings["has_earnings"] is False
