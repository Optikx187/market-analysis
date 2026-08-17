from sqlalchemy import create_engine, inspect, text

from app.database import _migrate_existing_tables


def test_existing_trade_and_alert_tables_receive_risk_columns(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE trades (id INTEGER PRIMARY KEY, ticker VARCHAR(20) NOT NULL)"
        ))
        connection.execute(text(
            "CREATE TABLE alert_logs (id INTEGER PRIMARY KEY, ticker VARCHAR(20) NOT NULL)"
        ))
        connection.execute(text("INSERT INTO trades (ticker) VALUES ('AAPL')"))
        connection.execute(text("INSERT INTO alert_logs (ticker) VALUES ('AAPL')"))
        _migrate_existing_tables(connection)

        inspector = inspect(connection)
        trade_columns = {column["name"] for column in inspector.get_columns("trades")}
        alert_columns = {column["name"] for column in inspector.get_columns("alert_logs")}
        trade_row = connection.execute(text(
            "SELECT asset_type, sector, market_regime, regime_label FROM trades"
        )).one()
        alert_row = connection.execute(text(
            "SELECT approved, risk_decision_json, market_regime, regime_label FROM alert_logs"
        )).one()

    regime_columns = {
        "market_regime",
        "volatility_regime",
        "breadth_regime",
        "risk_regime",
        "regime_label",
        "timeframe_agreement",
    }
    assert {"asset_type", "sector", *regime_columns}.issubset(trade_columns)
    assert {"approved", "risk_decision_json", *regime_columns}.issubset(alert_columns)
    assert trade_row == ("stock", "Unclassified", "unknown", "Unknown")
    assert alert_row == (1, None, "unknown", "Unknown")


def test_legacy_closed_trades_backfill_attribution_columns(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy-closed.db'}")
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE trades ("
            "id INTEGER PRIMARY KEY, ticker VARCHAR(20) NOT NULL, quantity FLOAT, "
            "status VARCHAR(10), pnl FLOAT)"
        ))
        connection.execute(text(
            "INSERT INTO trades (ticker, quantity, status, pnl) VALUES "
            "('AAPL', 10, 'CLOSED', 120), ('BTC', 5, 'OPEN', NULL)"
        ))
        _migrate_existing_tables(connection)

        columns = {column["name"] for column in inspect(connection).get_columns("trades")}
        rows = connection.execute(text(
            "SELECT ticker, realized_quantity, gross_pnl, costs_total, strategy_name, "
            "signal_confidence, excursion_status FROM trades ORDER BY id"
        )).all()

    assert {
        "strategy_name",
        "strategy_version",
        "timeframe",
        "signal_confidence",
        "signal_context_json",
        "execution_context_json",
        "planned_entry_price",
        "planned_exit_price",
        "planned_quantity",
        "entry_fees",
        "entry_slippage",
        "entry_costs_allocated",
        "exit_fees_total",
        "exit_slippage_total",
        "costs_total",
        "realized_quantity",
        "gross_pnl",
        "mfe_usd",
        "mae_usd",
        "mfe_pct",
        "mae_pct",
        "excursion_status",
    }.issubset(columns)
    assert rows[0] == ("AAPL", 10, 120, 0, None, None, "not_calculated")
    assert rows[1] == ("BTC", 0, None, 0, None, None, "not_calculated")
