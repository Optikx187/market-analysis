"""Durable storage for backtest run configuration and results."""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


class BacktestStore:
    def __init__(self, database_path: str) -> None:
        self.database_path = database_path
        Path(database_path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS backtest_runs (
                    run_id TEXT PRIMARY KEY,
                    ticker TEXT NOT NULL,
                    strategy_version TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    alert_eligible INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_backtest_runs_ticker_created "
                "ON backtest_runs(ticker, created_at DESC)"
            )

    def save(
        self,
        ticker: str,
        strategy_version: str,
        request: dict[str, object],
        result: dict[str, object],
    ) -> str:
        run_id = str(uuid4())
        created_at = datetime.now(timezone.utc).isoformat()
        eligibility = result["alert_eligibility"]
        if not isinstance(eligibility, dict):
            raise ValueError("Backtest result is missing alert eligibility")
        eligible = bool(eligibility.get("eligible", False))
        stored_result = {**result, "run_id": run_id, "created_at": created_at}
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO backtest_runs (
                    run_id, ticker, strategy_version, created_at,
                    request_json, result_json, alert_eligible
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    ticker,
                    strategy_version,
                    created_at,
                    json.dumps(request),
                    json.dumps(stored_result),
                    int(eligible),
                ),
            )
        return run_id

    def get(self, run_id: str) -> dict[str, object] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT result_json FROM backtest_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            return None
        result = json.loads(row["result_json"])
        return result if isinstance(result, dict) else None

    def list(self, ticker: str | None = None, limit: int = 20) -> list[dict[str, object]]:
        query = (
            "SELECT run_id, ticker, strategy_version, created_at, alert_eligible "
            "FROM backtest_runs"
        )
        arguments: tuple[object, ...]
        if ticker:
            query += " WHERE ticker = ?"
            arguments = (ticker, limit)
        else:
            arguments = (limit,)
        query += " ORDER BY created_at DESC LIMIT ?"
        with self._connect() as connection:
            rows = connection.execute(query, arguments).fetchall()
        return [
            {
                "run_id": row["run_id"],
                "ticker": row["ticker"],
                "strategy_version": row["strategy_version"],
                "created_at": row["created_at"],
                "alert_eligible": bool(row["alert_eligible"]),
            }
            for row in rows
        ]
