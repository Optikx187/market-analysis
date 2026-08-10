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
