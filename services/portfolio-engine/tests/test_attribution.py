import asyncio
import datetime
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import attribution
from app import main
from app.database import Base, get_db


TODAY = datetime.datetime.now(datetime.timezone.utc).replace(
    hour=0, minute=0, second=0, microsecond=0, tzinfo=None,
).isoformat()
LAST_YEAR = "2020-01-01T00:00:00"

CANDLES = {
    "AAPL": [
        {"timestamp": LAST_YEAR, "high": 400, "low": 10, "close": 200},
        {"timestamp": TODAY, "high": 125, "low": 92, "close": 112},
    ],
    "BTC": [
        {"timestamp": TODAY, "high": 130, "low": 80, "close": 90},
    ],
}


@pytest.fixture
def client(tmp_path, monkeypatch) -> Iterator[TestClient]:
    database_path = tmp_path / "attribution-test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def create_tables() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def override_get_db():
        async with session_factory() as session:
            yield session

    async def empty_returns(tickers: set[str]) -> dict[str, dict[str, float]]:
        return {ticker: {} for ticker in tickers}

    async def fake_candles(ticker: str, interval: str) -> list[dict[str, object]]:
        return CANDLES.get(ticker, [])

    asyncio.run(create_tables())
    monkeypatch.setattr(main, "_fetch_return_series", empty_returns)
    monkeypatch.setattr(main, "_fetch_candles", fake_candles)
    main.app.dependency_overrides[get_db] = override_get_db
    yield TestClient(main.app)
    main.app.dependency_overrides.clear()
    asyncio.run(engine.dispose())


def _trade(ticker: str, direction: str, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "ticker": ticker,
        "direction": direction,
        "entry_price": 100,
        "quantity": 10,
        "stop_loss": 95 if direction == "BUY" else 105,
        "target_price": 120 if direction == "BUY" else 80,
        "asset_type": "stock" if direction == "BUY" else "crypto",
        "sector": "Technology" if direction == "BUY" else "Crypto",
        "strategy_name": "trend-v2",
        "strategy_version": "2.1.0",
        "timeframe": "1d",
        "signal_confidence": 80,
        "signal_context": {"rsi": 55, "reason": "breakout"},
        "execution_context": {"venue": "paper", "order_type": "market"},
        "planned_entry_price": 99,
        "planned_exit_price": 120,
        "planned_quantity": 12,
        "entry_fees": 1.0,
        "entry_slippage": 0.5,
    }
    payload.update(overrides)
    return payload


def test_gross_pnl_and_cost_allocation_are_exact() -> None:
    assert attribution.gross_exit_pnl("BUY", 100, 112, 10) == pytest.approx(120)
    assert attribution.gross_exit_pnl("SELL", 100, 88, 10) == pytest.approx(120)

    first = attribution.allocate_entry_costs(1.5, 0.0, 4, 10, False)
    second = attribution.allocate_entry_costs(1.5, first, 6, 10, True)
    assert first == pytest.approx(0.6)
    assert first + second == pytest.approx(1.5)
    assert attribution.allocate_entry_costs(0.0, 0.0, 10, 10, True) == 0.0

    costs = attribution.ExitCosts(entry_costs_allocated=1.5, exit_fees=2.0, exit_slippage=0.5)
    gross, net = attribution.net_exit_pnl("BUY", 100, 112, 10, costs)
    assert (gross, net) == (pytest.approx(120), pytest.approx(116))


def test_excursions_use_highs_and_lows_per_direction() -> None:
    candles = [{"high": 120, "low": 90}, {"high": 130, "low": 95}]

    long_excursions = attribution.calculate_excursions("BUY", 100, 10, candles)
    short_excursions = attribution.calculate_excursions("SELL", 100, 10, candles)

    assert long_excursions is not None and short_excursions is not None
    assert long_excursions["mfe_usd"] == pytest.approx(300)
    assert long_excursions["mae_usd"] == pytest.approx(100)
    assert short_excursions["mfe_usd"] == pytest.approx(100)
    assert short_excursions["mae_usd"] == pytest.approx(300)
    assert long_excursions["mfe_pct"] == pytest.approx(30)
    assert short_excursions["mae_pct"] == pytest.approx(30)


def test_excursions_fail_safely_without_candle_data() -> None:
    assert attribution.calculate_excursions("BUY", 100, 10, []) is None
    assert attribution.calculate_excursions("BUY", 100, 10, [{"close": 105}]) is None
    assert attribution.calculate_excursions("BUY", 0, 10, [{"high": 120, "low": 90}]) is None


def test_window_candles_exclude_history_outside_the_holding_period() -> None:
    candles = [
        {"timestamp": "2024-01-01T00:00:00", "high": 400, "low": 10},
        {"timestamp": "2024-06-02T15:00:00", "high": 120, "low": 90},
        {"timestamp": "not-a-date", "high": 999, "low": 1},
    ]

    window = attribution.select_window_candles(
        candles, "2024-06-01T10:00:00", "2024-06-03T10:00:00",
    )

    assert window == [candles[1]]
    assert attribution.select_window_candles(candles, None, None) == candles


def test_missing_metadata_groups_under_unknown_with_sample_safeguards() -> None:
    trades = [
        {"strategy": None, "net_pnl": 10, "gross_pnl": 12, "costs": 2},
        {"strategy": "  ", "net_pnl": -5, "gross_pnl": -4, "costs": 1},
        {"strategy": "trend-v2", "net_pnl": 20, "gross_pnl": 21, "costs": 1},
    ]

    groups = {group["key"]: group for group in attribution.group_attribution(trades, "strategy", 20)}

    assert set(groups) == {"Unknown", "trend-v2"}
    assert groups["Unknown"]["sample_size"] == 2
    assert groups["Unknown"]["net_pnl"] == pytest.approx(5)
    assert groups["trend-v2"]["sufficient_sample"] is False
    assert groups["trend-v2"]["recommendation"] == "insufficient_history"
    assert "2/20" in groups["Unknown"]["sample_note"]

    sufficient = attribution.group_attribution(
        [{"strategy": "trend-v2", "net_pnl": 5, "gross_pnl": 5, "costs": 0}] * 20,
        "strategy",
        20,
    )
    assert sufficient[0]["sufficient_sample"] is True
    assert sufficient[0]["recommendation"] == "keep_enabled"


def test_confidence_calibration_is_suppressed_below_minimum_sample() -> None:
    small = attribution.confidence_calibration(
        [{"signal_confidence": 80, "net_pnl": 5, "gross_pnl": 5, "costs": 0}], 20,
    )
    assert small[0]["band"] == "80-89%"
    assert small[0]["sufficient_sample"] is False
    assert small[0]["calibration_gap"] is None

    large = attribution.confidence_calibration(
        [{"signal_confidence": 80, "net_pnl": 5, "gross_pnl": 5, "costs": 0}] * 20, 20,
    )
    assert large[0]["sufficient_sample"] is True
    assert large[0]["observed_win_rate"] == pytest.approx(100)
    assert large[0]["calibration_gap"] == pytest.approx(20)

    unknown = attribution.confidence_calibration([{"net_pnl": 5}], 20)
    assert unknown[0]["band"] == attribution.UNKNOWN


def test_journal_marks_missing_metadata_and_compares_plan_to_actual() -> None:
    journal = attribution.build_journal(
        {
            "id": 7,
            "ticker": "MSFT",
            "direction": "BUY",
            "entry_price": 100,
            "quantity": 10,
            "gross_pnl": 120,
            "costs": 4,
            "net_pnl": 116,
            "planned_entry_price": 99,
        },
        [{"kind": "EXIT", "price": 112, "quantity": 10, "fees": 2, "slippage": 0.5, "net_pnl": 116}],
        None,
    )

    assert journal["setup"]["strategy"] == attribution.UNKNOWN
    assert journal["setup"]["signal_confidence"] == attribution.NOT_RECORDED
    assert journal["excursions"]["status"] == "unavailable"
    assert journal["excursions"]["mfe_usd"] == attribution.NOT_RECORDED
    assert journal["result"]["outcome"] == "WIN"
    assert journal["planned_vs_actual"]["entry"]["difference"] == pytest.approx(1)
    assert journal["planned_vs_actual"]["size"]["planned"] == attribution.NOT_RECORDED
    assert journal["rule_adherence"]["planned_size_recorded"] is False
    assert journal["rule_adherence"]["stop_respected"] == attribution.NOT_RECORDED


def test_full_close_without_costs_preserves_legacy_pnl_and_journals_once(client: TestClient) -> None:
    trade = client.post(
        "/api/trades/manual",
        json=_trade("AAPL", "BUY", entry_fees=0, entry_slippage=0),
    ).json()

    closed = client.post(f"/api/trades/{trade['id']}/close", json={"exit_price": 112})

    assert closed.status_code == 200
    body = closed.json()
    assert body["pnl"] == pytest.approx(120)
    assert body["gross_pnl"] == pytest.approx(120)
    assert body["costs_total"] == pytest.approx(0)
    assert body["remaining_quantity"] == pytest.approx(0)
    assert body["status"] == "CLOSED"

    journal = client.get(f"/api/trades/{trade['id']}/journal")
    assert journal.status_code == 200
    detail = journal.json()["journal"]
    assert detail["result"]["exit_count"] == 1
    assert detail["result"]["partial_exits"] == 0
    assert detail["excursions"]["mfe_usd"] == pytest.approx(250)
    assert detail["excursions"]["mae_usd"] == pytest.approx(80)
    assert detail["planned_vs_actual"]["exit"]["actual"] == pytest.approx(112)


def test_short_close_records_short_excursions(client: TestClient) -> None:
    trade = client.post("/api/trades/manual", json=_trade("BTC", "SELL")).json()

    closed = client.post(f"/api/trades/{trade['id']}/close", json={"exit_price": 88})

    assert closed.status_code == 200
    assert closed.json()["mfe_usd"] == pytest.approx(200)
    assert closed.json()["mae_usd"] == pytest.approx(300)
    assert closed.json()["excursion_status"] == "calculated"


def test_excursion_status_unavailable_when_candles_missing(client: TestClient) -> None:
    trade = client.post("/api/trades/manual", json=_trade("NVDA", "BUY")).json()

    closed = client.post(f"/api/trades/{trade['id']}/close", json={"exit_price": 110})

    assert closed.status_code == 200
    assert closed.json()["excursion_status"] == "unavailable"
    assert closed.json()["mfe_usd"] is None
    detail = client.get(f"/api/trades/{trade['id']}/journal").json()["journal"]
    assert detail["excursions"]["status"] == "unavailable"


def test_partial_exits_allocate_costs_and_journal_only_on_full_closure(client: TestClient) -> None:
    trade = client.post("/api/trades/manual", json=_trade("AAPL", "BUY")).json()

    partial = client.post(
        f"/api/trades/{trade['id']}/close",
        json={"exit_price": 110, "quantity": 4, "fees": 1, "slippage": 0.25},
    )

    assert partial.status_code == 200
    assert partial.json()["status"] == "OPEN"
    assert partial.json()["remaining_quantity"] == pytest.approx(6)
    # gross 4 * 10 = 40, costs = 1 + 0.25 + 0.6 allocated entry costs
    assert partial.json()["pnl"] == pytest.approx(38.15)
    assert client.get(f"/api/trades/{trade['id']}/journal").status_code == 404

    final = client.post(
        f"/api/trades/{trade['id']}/close",
        json={"exit_price": 115, "quantity": 6, "fees": 1, "slippage": 0.25},
    )

    assert final.status_code == 200
    assert final.json()["status"] == "CLOSED"
    assert final.json()["remaining_quantity"] == pytest.approx(0)
    assert final.json()["gross_pnl"] == pytest.approx(130)
    assert final.json()["costs_total"] == pytest.approx(4.0)
    assert final.json()["pnl"] == pytest.approx(126.0)

    executions = client.get(f"/api/trades/{trade['id']}/executions").json()["executions"]
    assert [row["kind"] for row in executions] == ["ENTRY", "EXIT", "EXIT"]
    assert sum(row["entry_costs_allocated"] for row in executions if row["kind"] == "EXIT") == pytest.approx(1.5)

    journal = client.get(f"/api/trades/{trade['id']}/journal").json()["journal"]
    assert journal["result"]["exit_count"] == 2
    assert journal["result"]["partial_exits"] == 1
    assert journal["result"]["net_pnl"] == pytest.approx(126.0)
    assert journal["planned_vs_actual"]["size"]["difference"] == pytest.approx(-2)

    over_close = client.post(f"/api/trades/{trade['id']}/close", json={"exit_price": 115})
    assert over_close.status_code == 400


def test_partial_exit_rejects_quantity_above_remaining(client: TestClient) -> None:
    trade = client.post("/api/trades/manual", json=_trade("AAPL", "BUY")).json()

    response = client.post(
        f"/api/trades/{trade['id']}/close",
        json={"exit_price": 110, "quantity": 11},
    )

    assert response.status_code == 400
    assert "remaining" in response.json()["detail"]


def test_attribution_reconciles_exactly_to_portfolio_total_pnl(client: TestClient) -> None:
    long_trade = client.post("/api/trades/manual", json=_trade("AAPL", "BUY")).json()
    short_trade = client.post(
        "/api/trades/manual",
        json=_trade("BTC", "SELL", strategy_name="momentum-v1", timeframe="4h", signal_confidence=70),
    ).json()
    legacy = client.post(
        "/api/trades/manual",
        json={
            "ticker": "MSFT",
            "direction": "BUY",
            "entry_price": 100,
            "quantity": 10,
            "stop_loss": 95,
            "target_price": 120,
        },
    ).json()

    client.post(f"/api/trades/{long_trade['id']}/close", json={"exit_price": 110, "quantity": 4, "fees": 1})
    client.post(f"/api/trades/{long_trade['id']}/close", json={"exit_price": 115, "quantity": 6, "fees": 1})
    client.post(f"/api/trades/{short_trade['id']}/close", json={"exit_price": 88, "fees": 2})
    client.post(f"/api/trades/{legacy['id']}/close", json={"exit_price": 90})

    portfolio = client.get("/api/portfolio").json()
    payload = client.get("/api/attribution").json()

    assert payload["reconciliation"]["attributed_net_pnl"] == pytest.approx(portfolio["total_pnl"])
    assert payload["reconciliation"]["delta"] == 0
    assert payload["reconciliation"]["reconciles"] is True
    assert payload["summary"]["sample_size"] == 3
    assert payload["min_sample_size"] == main.settings.ATTRIBUTION_MIN_SAMPLE_SIZE

    strategies = {group["key"]: group for group in payload["dimensions"]["strategy"]}
    assert set(strategies) == {"trend-v2", "momentum-v1", attribution.UNKNOWN}
    assert strategies[attribution.UNKNOWN]["sample_size"] == 1
    assert strategies[attribution.UNKNOWN]["net_pnl"] == pytest.approx(-100)
    assert all(group["sufficient_sample"] is False for group in strategies.values())
    timeframes = {group["key"] for group in payload["dimensions"]["timeframe"]}
    assert timeframes == {"1d", "4h", attribution.UNKNOWN}

    filtered = client.get("/api/attribution", params={"strategy": "trend-v2"}).json()
    assert filtered["summary"]["sample_size"] == 1
    assert filtered["reconciliation"]["filtered_net_pnl"] == pytest.approx(126.5)
    assert filtered["reconciliation"]["attributed_net_pnl"] == pytest.approx(portfolio["total_pnl"])
    assert len(filtered["journals"]) == 1


def test_json_and_csv_exports_are_filterable(client: TestClient) -> None:
    long_trade = client.post("/api/trades/manual", json=_trade("AAPL", "BUY")).json()
    short_trade = client.post(
        "/api/trades/manual",
        json=_trade("BTC", "SELL", strategy_name="momentum-v1"),
    ).json()
    client.post(f"/api/trades/{long_trade['id']}/close", json={"exit_price": 112, "fees": 2})
    client.post(f"/api/trades/{short_trade['id']}/close", json={"exit_price": 88, "fees": 2})

    json_export = client.get("/api/attribution/export", params={"format": "json"})
    csv_export = client.get("/api/attribution/export", params={"format": "csv"})
    filtered_csv = client.get(
        "/api/attribution/export", params={"format": "csv", "strategy": "momentum-v1"},
    )

    assert json_export.status_code == 200
    assert len(json_export.json()["trades"]) == 2
    assert csv_export.status_code == 200
    assert csv_export.headers["content-type"].startswith("text/csv")
    lines = csv_export.text.strip().splitlines()
    assert lines[0] == ",".join(attribution.CSV_COLUMNS)
    assert len(lines) == 3
    assert "trend-v2" in csv_export.text and "momentum-v1" in csv_export.text

    filtered_lines = filtered_csv.text.strip().splitlines()
    assert len(filtered_lines) == 2
    assert "momentum-v1" in filtered_lines[1]
    assert "trend-v2" not in filtered_csv.text

    assert client.get("/api/attribution/export", params={"format": "xml"}).status_code == 400


def test_csv_export_marks_missing_metadata_without_inferring(client: TestClient) -> None:
    legacy = client.post(
        "/api/trades/manual",
        json={
            "ticker": "MSFT",
            "direction": "BUY",
            "entry_price": 100,
            "quantity": 10,
            "stop_loss": 95,
            "target_price": 120,
        },
    ).json()
    client.post(f"/api/trades/{legacy['id']}/close", json={"exit_price": 105})

    csv_export = client.get("/api/attribution/export", params={"format": "csv"})

    row = dict(zip(attribution.CSV_COLUMNS, csv_export.text.strip().splitlines()[1].split(",")))
    assert row["strategy"] == attribution.UNKNOWN
    assert row["timeframe"] == attribution.UNKNOWN
    assert row["signal_confidence"] == attribution.NOT_RECORDED
    assert row["planned_quantity"] == attribution.NOT_RECORDED
    assert row["net_pnl"] == "50.0"
