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
        _migrate_existing_tables(connection)

        inspector = inspect(connection)
        trade_columns = {column["name"] for column in inspector.get_columns("trades")}
        alert_columns = {column["name"] for column in inspector.get_columns("alert_logs")}
        trade_row = connection.execute(text(
            "INSERT INTO trades (ticker) VALUES ('AAPL') RETURNING asset_type, sector"
        )).one()
        alert_row = connection.execute(text(
            "INSERT INTO alert_logs (ticker) VALUES ('AAPL') RETURNING approved, risk_decision_json"
        )).one()

    assert {"asset_type", "sector"}.issubset(trade_columns)
    assert {"approved", "risk_decision_json"}.issubset(alert_columns)
    assert trade_row == ("stock", "Unclassified")
    assert alert_row == (1, None)
