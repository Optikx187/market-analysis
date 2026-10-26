from __future__ import annotations

from datetime import UTC, datetime

from importer.common import (
    ReadOnlySQLite,
    canonical_json,
    checksum,
    decimal_value,
    source_key,
    utc_datetime,
)
from importer.report import ImportReport
from importer.target import Target


def _asset_type(value: object) -> str:
    normalized = str(value).strip().lower()
    if normalized in {"crypto", "cryptocurrency"}:
        return "crypto"
    if normalized in {"stock", "equity"}:
        return "stock"
    raise ValueError(f"unsupported asset type {value!r}")


def _instrument_key(ticker: str, asset_type: str) -> str:
    normalized = ticker.strip().upper().replace("-USD", "")
    return (
        f"crypto:{normalized}/USD"
        if asset_type == "crypto"
        else f"stock:US:{normalized}"
    )


def _provider_id(target: Target) -> int:
    row = target.connection.execute(
        "SELECT id FROM market_data.providers WHERE provider_key='legacy-sqlite'"
    ).fetchone()
    if row is not None:
        return int(row["id"])
    if target.mode == "dry-run":
        return -1
    return int(
        target.connection.execute(
            """
            INSERT INTO market_data.providers (provider_key, display_name)
            VALUES ('legacy-sqlite', 'Legacy SQLite')
            RETURNING id
            """
        ).fetchone()["id"]
    )


def _target_candle(
    target: Target,
    provider_id: int,
    instrument_id: str,
    interval: str,
    timestamp: datetime,
) -> dict[str, object] | None:
    return target.connection.execute(
        """
        SELECT open_price, high_price, low_price, close_price, volume,
               content_checksum
        FROM market_data.candles
        WHERE provider_id=%s AND instrument_id=%s
          AND bar_interval=%s AND bucket_start=%s
        """,
        (provider_id, instrument_id, interval, timestamp),
    ).fetchone()


def import_market(
    source: ReadOnlySQLite,
    target: Target,
    report: ImportReport,
    *,
    accepted_deferred: set[str],
) -> None:
    store = "market"
    tables = set(source.tables())
    required = {"assets", "candles", "price_alerts"}
    missing = required - tables
    if missing:
        report.errors.append(
            f"Market source is missing tables: {', '.join(sorted(missing))}"
        )
        return
    asset_rows = source.rows("assets")
    asset_report = report.table(store, "assets")
    asset_report.source_rows = len(asset_rows)
    asset_report.source_checksum = checksum(asset_rows)
    provider_id = _provider_id(target)
    ticker_to_instrument: dict[str, tuple[str, str]] = {}
    seen_tickers: set[str] = set()
    target_asset_rows: list[dict[str, object]] = []
    for row in asset_rows:
        try:
            ticker = str(row["ticker"]).strip().upper().replace("-USD", "")
            if not ticker:
                raise ValueError("ticker is empty")
            if ticker in seen_tickers:
                raise ValueError(f"duplicate asset ticker {ticker}")
            seen_tickers.add(ticker)
            asset_type = _asset_type(row["asset_type"])
            row_checksum = checksum(row)
            instrument_id = target.mapping(
                source_store=store,
                source_table="assets",
                source_key=source_key(row["id"]),
                source_row_checksum=row_checksum,
                target_schema="market_data",
                target_table="instruments",
            )
            venue_key = "24x7" if asset_type == "crypto" else "US"
            venue_id = target.mapping(
                source_store=store,
                source_table="venues",
                source_key=source_key(venue_key),
                source_row_checksum=checksum({"venue_key": venue_key}),
                target_schema="market_data",
                target_table="venues",
            )
            symbol_id = target.mapping(
                source_store=store,
                source_table="instrument_symbols",
                source_key=source_key(
                    {"asset_id": row["id"], "provider": "legacy-sqlite"}
                ),
                source_row_checksum=row_checksum,
                target_schema="market_data",
                target_table="instrument_symbols",
            )
            ticker_to_instrument[ticker] = (instrument_id, asset_type)
            target_asset_rows.append(
                {
                    "id": instrument_id,
                    "instrument_key": _instrument_key(ticker, asset_type),
                    "display_name": str(row["name"] or ticker),
                    "asset_class": asset_type,
                    "base_asset": ticker,
                    "quote_asset": "USD",
                    "venue_id": venue_id,
                    "is_active": bool(row["is_active"]),
                }
            )
            if target.mode == "apply":
                target.connection.execute(
                    """
                    INSERT INTO market_data.venues (
                        id, venue_key, display_name
                    ) VALUES (%s, %s, %s)
                    ON CONFLICT (venue_key) DO NOTHING
                    """,
                    (venue_id, venue_key, venue_key),
                )
                existing = target.fetch_by_id(
                    "market_data", "instruments", "id", instrument_id
                )
                if existing is None:
                    target.insert(
                        "market_data",
                        "instruments",
                        target_asset_rows[-1],
                    )
                    asset_report.imported_rows += 1
                else:
                    expected = target_asset_rows[-1]
                    comparable = {
                        key: existing[key]
                        for key in expected
                        if key in existing
                    }
                    if checksum(comparable) != checksum(expected):
                        raise ValueError(
                            f"target instrument conflicts for {ticker}"
                        )
                    asset_report.skipped_rows += 1
                target.connection.execute(
                    """
                    INSERT INTO market_data.instrument_symbols (
                        id, instrument_id, provider_id, provider_symbol,
                        normalized_symbol
                    ) VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    (symbol_id, instrument_id, provider_id, ticker, ticker),
                )
                mapped_symbol = target.connection.execute(
                    """
                    SELECT instrument_id
                    FROM market_data.instrument_symbols
                    WHERE provider_id=%s AND provider_symbol=%s
                      AND valid_to IS NULL
                    """,
                    (provider_id, ticker),
                ).fetchone()
                if (
                    mapped_symbol is None
                    or str(mapped_symbol["instrument_id"]) != instrument_id
                ):
                    raise ValueError(
                        f"target provider symbol conflicts for {ticker}"
                    )
            else:
                asset_report.imported_rows += 1
        except (KeyError, TypeError, ValueError) as error:
            asset_report.conflict_rows += 1
            report.errors.append(f"market.assets row {row.get('id')}: {error}")
    asset_report.target_checksum = checksum(target_asset_rows)
    if target.mode == "apply":
        target_count = target.count("market_data", "instruments")
        if target_count != len(asset_rows):
            report.errors.append(
                "market.assets target count mismatch: "
                f"source={len(asset_rows)}, target={target_count}"
            )
    target.update_progress(
        source_store=store,
        source_table="assets",
        status="failed" if asset_report.conflict_rows else "completed",
        last_source_key=(
            source_key(asset_rows[-1]["id"]) if asset_rows else None
        ),
        source_rows=asset_report.source_rows,
        imported_rows=asset_report.imported_rows,
        skipped_rows=asset_report.skipped_rows,
        conflict_rows=asset_report.conflict_rows,
        source_checksum=asset_report.source_checksum,
        target_checksum=asset_report.target_checksum,
    )

    candle_rows = source.rows("candles")
    candle_report = report.table(store, "candles")
    candle_report.source_rows = len(candle_rows)
    candle_report.source_checksum = checksum(candle_rows)
    seen_candles: dict[tuple[str, str, str], str] = {}
    target_candle_rows: list[dict[str, object]] = []
    for row in candle_rows:
        try:
            ticker = str(row["ticker"]).strip().upper().replace("-USD", "")
            if ticker not in ticker_to_instrument:
                raise ValueError(f"orphan candle ticker {ticker}")
            interval = str(row.get("interval") or "1d")
            if interval not in {"1m", "5m", "15m", "1h", "4h", "1d", "1w"}:
                raise ValueError(f"unsupported interval {interval}")
            timestamp, assumed_utc = utc_datetime(row["timestamp"])
            if assumed_utc:
                report.warnings.append(
                    f"market.candles {row['id']} timestamp assumed UTC"
                )
            prices = {
                key: decimal_value(row[key], 12)
                for key in ("open", "high", "low", "close")
            }
            volume = decimal_value(row["volume"], 18)
            if min(prices.values()) < 0 or volume < 0:
                raise ValueError("prices and volume must be non-negative")
            if (
                prices["high"] < max(
                    prices["open"], prices["low"], prices["close"]
                )
                or prices["low"] > min(prices["open"], prices["close"])
            ):
                raise ValueError("OHLC ordering is invalid")
            instrument_id, _asset_class = ticker_to_instrument[ticker]
            natural_key = (ticker, interval, timestamp.isoformat())
            content = {
                **{key: format(value, "f") for key, value in prices.items()},
                "volume": format(volume, "f"),
            }
            content_checksum = checksum(content)
            duplicate = seen_candles.get(natural_key)
            if duplicate is not None:
                if duplicate != content_checksum:
                    raise ValueError(
                        f"conflicting duplicate candle {natural_key}"
                    )
                candle_report.skipped_rows += 1
                continue
            seen_candles[natural_key] = content_checksum
            mapping_key = source_key(natural_key)
            target.mapping(
                source_store=store,
                source_table="candles",
                source_key=mapping_key,
                source_row_checksum=checksum(row),
                target_schema="market_data",
                target_table="candles",
                target_id_override=(
                    f"{provider_id}|{instrument_id}|{interval}|"
                    f"{timestamp.isoformat()}"
                ),
            )
            target_row = {
                "provider_id": provider_id,
                "instrument_id": instrument_id,
                "bar_interval": interval,
                "bucket_start": timestamp,
                "open_price": prices["open"],
                "high_price": prices["high"],
                "low_price": prices["low"],
                "close_price": prices["close"],
                "volume": volume,
                "provider_observed_at": timestamp,
                "first_ingested_at": datetime.now(UTC),
                "ingested_at": datetime.now(UTC),
                "provider_revision": 1,
                "is_complete": True,
                "quality_status": "valid",
                "quality_flags": canonical_json(
                    ["legacy_sqlite", "timestamp_assumed_utc"]
                    if assumed_utc
                    else ["legacy_sqlite"]
                ),
                "source_event_id": f"sqlite:{row['id']}",
                "content_checksum": content_checksum,
            }
            target_candle_rows.append(
                {
                    **target_row,
                    "first_ingested_at": None,
                    "ingested_at": None,
                }
            )
            existing = (
                _target_candle(
                    target,
                    provider_id,
                    instrument_id,
                    interval,
                    timestamp,
                )
                if target.mode == "apply"
                else None
            )
            if existing is not None:
                if existing["content_checksum"] != content_checksum:
                    raise ValueError(
                        f"target candle conflicts with source {natural_key}"
                    )
                candle_report.skipped_rows += 1
            elif target.mode == "apply":
                target.connection.execute(
                    """
                    INSERT INTO market_data.candles (
                        provider_id, instrument_id, bar_interval, bucket_start,
                        open_price, high_price, low_price, close_price, volume,
                        provider_observed_at, first_ingested_at, ingested_at,
                        provider_revision, is_complete, quality_status,
                        quality_flags, source_event_id, content_checksum
                    ) VALUES (
                        %(provider_id)s, %(instrument_id)s, %(bar_interval)s,
                        %(bucket_start)s, %(open_price)s, %(high_price)s,
                        %(low_price)s, %(close_price)s, %(volume)s,
                        %(provider_observed_at)s, %(first_ingested_at)s,
                        %(ingested_at)s, %(provider_revision)s,
                        %(is_complete)s, %(quality_status)s,
                        %(quality_flags)s::jsonb, %(source_event_id)s,
                        %(content_checksum)s
                    )
                    """,
                    target_row,
                )
                persisted = _target_candle(
                    target,
                    provider_id,
                    instrument_id,
                    interval,
                    timestamp,
                )
                if (
                    persisted is None
                    or persisted["content_checksum"] != content_checksum
                ):
                    raise ValueError(
                        f"persisted candle checksum mismatch {natural_key}"
                    )
                candle_report.imported_rows += 1
            else:
                candle_report.imported_rows += 1
        except (KeyError, TypeError, ValueError) as error:
            candle_report.conflict_rows += 1
            report.errors.append(
                f"market.candles row {row.get('id')}: {error}"
            )
    candle_report.target_checksum = checksum(target_candle_rows)
    if target.mode == "apply":
        target_count = target.count("market_data", "candles")
        expected_count = len(seen_candles)
        if target_count != expected_count:
            report.errors.append(
                "market.candles target count mismatch: "
                f"source_unique={expected_count}, target={target_count}"
            )
    target.update_progress(
        source_store=store,
        source_table="candles",
        status="failed" if candle_report.conflict_rows else "completed",
        last_source_key=(
            source_key(candle_rows[-1]["id"]) if candle_rows else None
        ),
        source_rows=candle_report.source_rows,
        imported_rows=candle_report.imported_rows,
        skipped_rows=candle_report.skipped_rows,
        conflict_rows=candle_report.conflict_rows,
        source_checksum=candle_report.source_checksum,
        target_checksum=candle_report.target_checksum,
    )

    alert_rows = source.rows("price_alerts")
    alert_report = report.table(store, "price_alerts")
    alert_report.source_rows = len(alert_rows)
    alert_report.source_checksum = checksum(alert_rows)
    if alert_rows and "market.price_alerts" not in accepted_deferred:
        report.decisions.append(
            "market.price_alerts has no runtime PostgreSQL destination; "
            "rerun with --accept-deferred market.price_alerts to preserve "
            "rows in operations.import_deferred_records"
        )
    else:
        for row in alert_rows:
            row_checksum = checksum(row)
            target.defer(
                source_store=store,
                source_table="price_alerts",
                source_key=source_key(row["id"]),
                source_row=row,
                source_row_checksum=row_checksum,
                reason="notification alert-rule schema is deferred",
            )
            alert_report.deferred_rows += 1
        alert_report.target_checksum = alert_report.source_checksum
    target.update_progress(
        source_store=store,
        source_table="price_alerts",
        status="completed" if not report.decisions else "failed",
        last_source_key=(
            source_key(alert_rows[-1]["id"]) if alert_rows else None
        ),
        source_rows=alert_report.source_rows,
        imported_rows=0,
        skipped_rows=0,
        conflict_rows=0,
        source_checksum=alert_report.source_checksum,
        target_checksum=alert_report.target_checksum,
    )
    target.commit()
