\set ON_ERROR_STOP on

CREATE SCHEMA identity;
CREATE SCHEMA market_data;
CREATE SCHEMA research;
CREATE SCHEMA portfolio;
CREATE SCHEMA notifications;
CREATE SCHEMA operations;
CREATE SCHEMA billing;

COMMENT ON SCHEMA billing IS
    'Reserved for deferred plans, entitlements, usage, subscriptions, and invoices.';

CREATE DOMAIN operations.price_value AS NUMERIC(30,12)
    CHECK (VALUE >= 0);
CREATE DOMAIN operations.quantity_value AS NUMERIC(38,18)
    CHECK (VALUE >= 0);
CREATE DOMAIN operations.money_value AS NUMERIC(38,12);
CREATE DOMAIN operations.ratio_value AS NUMERIC(24,12);
CREATE DOMAIN operations.score_value AS NUMERIC(18,8)
    CHECK (VALUE >= 0 AND VALUE <= 100);
CREATE DOMAIN operations.indicator_value AS NUMERIC(30,12);
CREATE DOMAIN operations.currency_code AS TEXT
    CHECK (VALUE ~ '^[A-Z0-9]{3,10}$');

CREATE FUNCTION operations.round_half_up(
    input_value NUMERIC,
    decimal_places INTEGER
) RETURNS NUMERIC
LANGUAGE SQL
IMMUTABLE
STRICT
PARALLEL SAFE
AS $$
    SELECT round(input_value, decimal_places)
$$;

CREATE TABLE identity.organizations (
    id UUID PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9][a-z0-9-]{1,62}$'),
    display_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'suspended', 'closed')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE identity.users (
    id UUID PRIMARY KEY,
    subject TEXT NOT NULL UNIQUE,
    username TEXT NOT NULL,
    username_key TEXT GENERATED ALWAYS AS (lower(btrim(username))) STORED,
    display_name TEXT,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'disabled', 'deleted')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (username_key)
);

CREATE TABLE identity.memberships (
    tenant_id UUID NOT NULL REFERENCES identity.organizations(id),
    user_id UUID NOT NULL REFERENCES identity.users(id),
    role TEXT NOT NULL CHECK (role IN ('owner', 'admin', 'analyst', 'viewer')),
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'suspended', 'removed')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (tenant_id, user_id)
);

CREATE TABLE market_data.providers (
    id SMALLINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'degraded', 'disabled')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE market_data.venues (
    id UUID PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    timezone_name TEXT NOT NULL,
    is_24x7 BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE market_data.instruments (
    id UUID PRIMARY KEY,
    instrument_key TEXT NOT NULL UNIQUE,
    canonical_symbol TEXT NOT NULL,
    display_name TEXT NOT NULL,
    asset_class TEXT NOT NULL
        CHECK (asset_class IN ('stock', 'etf', 'crypto', 'forex', 'index')),
    base_asset TEXT NOT NULL,
    quote_asset operations.currency_code NOT NULL,
    venue_id UUID REFERENCES market_data.venues(id),
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'halted', 'delisted', 'disabled')),
    metadata JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_instruments_asset_symbol
    ON market_data.instruments (asset_class, canonical_symbol);

CREATE TABLE market_data.instrument_symbols (
    id UUID PRIMARY KEY,
    provider_id SMALLINT NOT NULL REFERENCES market_data.providers(id),
    instrument_id UUID NOT NULL REFERENCES market_data.instruments(id),
    provider_symbol TEXT NOT NULL,
    valid_from TIMESTAMPTZ NOT NULL,
    valid_to TIMESTAMPTZ,
    metadata JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (valid_to IS NULL OR valid_to > valid_from),
    UNIQUE (provider_id, instrument_id, provider_symbol, valid_from)
);

CREATE UNIQUE INDEX uq_instrument_symbols_active_provider_symbol
    ON market_data.instrument_symbols (provider_id, provider_symbol)
    WHERE valid_to IS NULL;
CREATE INDEX idx_instrument_symbols_instrument
    ON market_data.instrument_symbols (instrument_id, provider_id, valid_from DESC);

CREATE TABLE market_data.candles (
    provider_id SMALLINT NOT NULL REFERENCES market_data.providers(id),
    instrument_id UUID NOT NULL REFERENCES market_data.instruments(id),
    bar_interval TEXT NOT NULL
        CHECK (bar_interval IN ('1m', '5m', '15m', '1h', '4h', '1d', '1w')),
    bucket_start TIMESTAMPTZ NOT NULL,
    open_price operations.price_value NOT NULL,
    high_price operations.price_value NOT NULL,
    low_price operations.price_value NOT NULL,
    close_price operations.price_value NOT NULL,
    volume operations.quantity_value NOT NULL,
    provider_observed_at TIMESTAMPTZ NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    is_complete BOOLEAN NOT NULL DEFAULT TRUE,
    quality_status TEXT NOT NULL DEFAULT 'valid'
        CHECK (quality_status IN ('valid', 'warning', 'blocked')),
    quality_flags JSONB NOT NULL DEFAULT '[]'::JSONB,
    source_event_id TEXT,
    content_checksum TEXT NOT NULL,
    PRIMARY KEY (provider_id, instrument_id, bar_interval, bucket_start),
    CHECK (high_price >= open_price),
    CHECK (high_price >= close_price),
    CHECK (high_price >= low_price),
    CHECK (low_price <= open_price),
    CHECK (low_price <= close_price)
);

CREATE INDEX idx_candles_instrument_interval_time
    ON market_data.candles (instrument_id, bar_interval, bucket_start DESC)
    INCLUDE (
        provider_id,
        open_price,
        high_price,
        low_price,
        close_price,
        volume,
        quality_status,
        ingested_at
    );
CREATE INDEX idx_candles_provider_ingested
    ON market_data.candles (provider_id, ingested_at DESC);
CREATE INDEX idx_candles_bucket_brin
    ON market_data.candles USING BRIN (bucket_start);

CREATE TABLE research.analysis_definitions (
    id UUID PRIMARY KEY,
    module_name TEXT NOT NULL,
    module_version TEXT NOT NULL,
    display_name TEXT NOT NULL,
    output_schema_version INTEGER NOT NULL CHECK (output_schema_version > 0),
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'deprecated', 'disabled')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (module_name, module_version)
);

CREATE TABLE research.analysis_selections (
    tenant_id UUID NOT NULL REFERENCES identity.organizations(id),
    user_id UUID NOT NULL REFERENCES identity.users(id),
    analysis_definition_id UUID NOT NULL
        REFERENCES research.analysis_definitions(id),
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    configuration JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (tenant_id, user_id, analysis_definition_id),
    FOREIGN KEY (tenant_id, user_id)
        REFERENCES identity.memberships(tenant_id, user_id)
);

CREATE TABLE research.analysis_runs (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES identity.organizations(id),
    user_id UUID NOT NULL REFERENCES identity.users(id),
    analysis_definition_id UUID NOT NULL
        REFERENCES research.analysis_definitions(id),
    instrument_id UUID REFERENCES market_data.instruments(id),
    idempotency_key TEXT NOT NULL,
    request_payload JSONB NOT NULL,
    result_payload JSONB,
    status TEXT NOT NULL
        CHECK (status IN ('queued', 'running', 'completed', 'failed', 'cancelled')),
    error_code TEXT,
    source_fresh_through TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    UNIQUE (tenant_id, user_id, idempotency_key),
    FOREIGN KEY (tenant_id, user_id)
        REFERENCES identity.memberships(tenant_id, user_id)
);

CREATE INDEX idx_analysis_runs_tenant_definition_created
    ON research.analysis_runs (
        tenant_id,
        analysis_definition_id,
        created_at DESC
    );

CREATE TABLE research.backtest_runs (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES identity.organizations(id),
    user_id UUID NOT NULL REFERENCES identity.users(id),
    instrument_id UUID NOT NULL REFERENCES market_data.instruments(id),
    strategy_name TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    request_payload JSONB NOT NULL,
    result_payload JSONB NOT NULL,
    alert_eligible BOOLEAN NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (tenant_id, user_id)
        REFERENCES identity.memberships(tenant_id, user_id)
);

CREATE INDEX idx_backtest_runs_tenant_instrument_created
    ON research.backtest_runs (tenant_id, instrument_id, created_at DESC);

CREATE TABLE portfolio.portfolios (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES identity.organizations(id),
    user_id UUID NOT NULL REFERENCES identity.users(id),
    name TEXT NOT NULL,
    base_currency operations.currency_code NOT NULL,
    balance operations.money_value NOT NULL,
    equity operations.money_value NOT NULL,
    total_pnl operations.money_value NOT NULL DEFAULT 0,
    peak_equity operations.money_value NOT NULL,
    max_drawdown_ratio operations.ratio_value NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (tenant_id, user_id, name),
    FOREIGN KEY (tenant_id, user_id)
        REFERENCES identity.memberships(tenant_id, user_id)
);

CREATE TABLE portfolio.trades (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES identity.organizations(id),
    user_id UUID NOT NULL REFERENCES identity.users(id),
    portfolio_id UUID NOT NULL REFERENCES portfolio.portfolios(id),
    instrument_id UUID NOT NULL REFERENCES market_data.instruments(id),
    direction TEXT NOT NULL CHECK (direction IN ('BUY', 'SELL')),
    status TEXT NOT NULL CHECK (status IN ('OPEN', 'CLOSED', 'CANCELLED')),
    entry_price operations.price_value NOT NULL,
    exit_price operations.price_value,
    quantity operations.quantity_value NOT NULL,
    realized_quantity operations.quantity_value NOT NULL DEFAULT 0,
    stop_loss operations.price_value,
    target_price operations.price_value,
    signal_confidence operations.score_value,
    timeframe_agreement operations.score_value,
    gross_pnl operations.money_value,
    net_pnl operations.money_value,
    pnl_ratio operations.ratio_value,
    opened_at TIMESTAMPTZ NOT NULL,
    closed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (tenant_id, user_id)
        REFERENCES identity.memberships(tenant_id, user_id),
    CHECK (realized_quantity <= quantity),
    CHECK (closed_at IS NULL OR closed_at >= opened_at)
);

CREATE INDEX idx_trades_tenant_status_opened
    ON portfolio.trades (tenant_id, user_id, status, opened_at DESC);

CREATE TABLE portfolio.trade_executions (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES identity.organizations(id),
    user_id UUID NOT NULL REFERENCES identity.users(id),
    trade_id UUID NOT NULL REFERENCES portfolio.trades(id),
    execution_kind TEXT NOT NULL CHECK (execution_kind IN ('ENTRY', 'EXIT')),
    price operations.price_value NOT NULL,
    quantity operations.quantity_value NOT NULL,
    fees operations.money_value NOT NULL DEFAULT 0,
    slippage operations.money_value NOT NULL DEFAULT 0,
    gross_pnl operations.money_value,
    net_pnl operations.money_value,
    executed_at TIMESTAMPTZ NOT NULL,
    external_execution_id TEXT,
    UNIQUE (trade_id, external_execution_id),
    FOREIGN KEY (tenant_id, user_id)
        REFERENCES identity.memberships(tenant_id, user_id)
);

CREATE TABLE portfolio.trade_journals (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES identity.organizations(id),
    user_id UUID NOT NULL REFERENCES identity.users(id),
    trade_id UUID NOT NULL UNIQUE REFERENCES portfolio.trades(id),
    net_pnl operations.money_value NOT NULL,
    summary TEXT NOT NULL,
    journal_payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (tenant_id, user_id)
        REFERENCES identity.memberships(tenant_id, user_id)
);

CREATE TABLE portfolio.paper_orders (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES identity.organizations(id),
    user_id UUID NOT NULL REFERENCES identity.users(id),
    portfolio_id UUID NOT NULL REFERENCES portfolio.portfolios(id),
    instrument_id UUID NOT NULL REFERENCES market_data.instruments(id),
    idempotency_key TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    order_type TEXT NOT NULL,
    status TEXT NOT NULL,
    quantity operations.quantity_value NOT NULL,
    filled_quantity operations.quantity_value NOT NULL DEFAULT 0,
    limit_price operations.price_value,
    stop_price operations.price_value,
    reserved_cash operations.money_value NOT NULL DEFAULT 0,
    average_fill_price operations.price_value,
    filled_notional operations.money_value NOT NULL DEFAULT 0,
    fees_total operations.money_value NOT NULL DEFAULT 0,
    slippage_total operations.money_value NOT NULL DEFAULT 0,
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (tenant_id, user_id, idempotency_key),
    FOREIGN KEY (tenant_id, user_id)
        REFERENCES identity.memberships(tenant_id, user_id),
    CHECK (filled_quantity <= quantity)
);

CREATE INDEX idx_paper_orders_open
    ON portfolio.paper_orders (tenant_id, user_id, created_at DESC)
    WHERE status IN ('pending', 'accepted', 'partially_filled', 'triggered');

CREATE TABLE portfolio.paper_order_fills (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES identity.organizations(id),
    user_id UUID NOT NULL REFERENCES identity.users(id),
    order_id UUID NOT NULL REFERENCES portfolio.paper_orders(id),
    candle_provider_id SMALLINT NOT NULL,
    candle_instrument_id UUID NOT NULL,
    candle_interval TEXT NOT NULL,
    candle_bucket_start TIMESTAMPTZ NOT NULL,
    quantity operations.quantity_value NOT NULL,
    price operations.price_value NOT NULL,
    fees operations.money_value NOT NULL DEFAULT 0,
    slippage operations.money_value NOT NULL DEFAULT 0,
    filled_at TIMESTAMPTZ NOT NULL,
    FOREIGN KEY (
        candle_provider_id,
        candle_instrument_id,
        candle_interval,
        candle_bucket_start
    ) REFERENCES market_data.candles (
        provider_id,
        instrument_id,
        bar_interval,
        bucket_start
    ),
    FOREIGN KEY (tenant_id, user_id)
        REFERENCES identity.memberships(tenant_id, user_id)
);

CREATE TABLE portfolio.live_orders (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES identity.organizations(id),
    user_id UUID NOT NULL REFERENCES identity.users(id),
    portfolio_id UUID NOT NULL REFERENCES portfolio.portfolios(id),
    instrument_id UUID NOT NULL REFERENCES market_data.instruments(id),
    idempotency_key TEXT NOT NULL,
    client_order_id TEXT NOT NULL,
    broker_code TEXT NOT NULL,
    broker_order_id TEXT,
    sandbox BOOLEAN NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    order_type TEXT NOT NULL,
    status TEXT NOT NULL,
    quantity operations.quantity_value NOT NULL,
    filled_quantity operations.quantity_value NOT NULL DEFAULT 0,
    limit_price operations.price_value,
    stop_price operations.price_value,
    estimated_notional operations.money_value,
    average_fill_price operations.price_value,
    request_fingerprint TEXT NOT NULL,
    submitted_at TIMESTAMPTZ,
    reconciled_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (tenant_id, user_id, idempotency_key),
    UNIQUE (tenant_id, client_order_id),
    FOREIGN KEY (tenant_id, user_id)
        REFERENCES identity.memberships(tenant_id, user_id),
    CHECK (filled_quantity <= quantity)
);

CREATE UNIQUE INDEX uq_live_orders_broker_order
    ON portfolio.live_orders (broker_code, broker_order_id)
    WHERE broker_order_id IS NOT NULL;
CREATE INDEX idx_live_orders_open
    ON portfolio.live_orders (tenant_id, user_id, created_at DESC)
    WHERE status IN ('new', 'accepted', 'partially_filled', 'pending_cancel');

CREATE TABLE portfolio.live_order_fills (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES identity.organizations(id),
    user_id UUID NOT NULL REFERENCES identity.users(id),
    order_id UUID NOT NULL REFERENCES portfolio.live_orders(id),
    broker_fill_id TEXT NOT NULL,
    quantity operations.quantity_value NOT NULL,
    price operations.price_value NOT NULL,
    filled_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (order_id, broker_fill_id),
    FOREIGN KEY (tenant_id, user_id)
        REFERENCES identity.memberships(tenant_id, user_id)
);

CREATE TABLE portfolio.equity_snapshots (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES identity.organizations(id),
    user_id UUID NOT NULL REFERENCES identity.users(id),
    portfolio_id UUID NOT NULL REFERENCES portfolio.portfolios(id),
    equity operations.money_value NOT NULL,
    balance operations.money_value NOT NULL,
    captured_at TIMESTAMPTZ NOT NULL,
    FOREIGN KEY (tenant_id, user_id)
        REFERENCES identity.memberships(tenant_id, user_id)
);

CREATE INDEX idx_equity_snapshots_portfolio_time
    ON portfolio.equity_snapshots (portfolio_id, captured_at DESC);

CREATE TABLE notifications.destinations (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES identity.organizations(id),
    user_id UUID NOT NULL REFERENCES identity.users(id),
    name TEXT NOT NULL,
    destination_type TEXT NOT NULL,
    external_destination_id TEXT NOT NULL,
    secret_reference TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'disabled', 'failed')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (tenant_id, user_id, name),
    UNIQUE (tenant_id, destination_type, external_destination_id),
    FOREIGN KEY (tenant_id, user_id)
        REFERENCES identity.memberships(tenant_id, user_id)
);

CREATE TABLE notifications.routes (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES identity.organizations(id),
    user_id UUID NOT NULL REFERENCES identity.users(id),
    destination_id UUID NOT NULL REFERENCES notifications.destinations(id),
    analysis_definition_id UUID REFERENCES research.analysis_definitions(id),
    event_type TEXT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    filter_payload JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (
        tenant_id,
        user_id,
        destination_id,
        analysis_definition_id,
        event_type
    ),
    FOREIGN KEY (tenant_id, user_id)
        REFERENCES identity.memberships(tenant_id, user_id)
);

CREATE TABLE notifications.notifications (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES identity.organizations(id),
    user_id UUID NOT NULL REFERENCES identity.users(id),
    event_id UUID NOT NULL,
    event_type TEXT NOT NULL,
    schema_version INTEGER NOT NULL CHECK (schema_version > 0),
    instrument_id UUID REFERENCES market_data.instruments(id),
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (tenant_id, event_id),
    FOREIGN KEY (tenant_id, user_id)
        REFERENCES identity.memberships(tenant_id, user_id)
);

CREATE TABLE notifications.delivery_jobs (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES identity.organizations(id),
    notification_id UUID NOT NULL REFERENCES notifications.notifications(id),
    destination_id UUID NOT NULL REFERENCES notifications.destinations(id),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'leased', 'retry', 'delivered', 'dead')),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    available_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    leased_until TIMESTAMPTZ,
    last_error_code TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (tenant_id, notification_id, destination_id)
);

CREATE INDEX idx_delivery_jobs_due
    ON notifications.delivery_jobs (available_at, id)
    WHERE status IN ('pending', 'retry');

CREATE TABLE notifications.delivery_attempts (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES identity.organizations(id),
    delivery_job_id UUID NOT NULL REFERENCES notifications.delivery_jobs(id),
    attempt_number INTEGER NOT NULL CHECK (attempt_number > 0),
    outcome TEXT NOT NULL CHECK (outcome IN ('delivered', 'retry', 'dead')),
    provider_message_id TEXT,
    response_code TEXT,
    response_hash TEXT,
    error_code TEXT,
    attempted_at TIMESTAMPTZ NOT NULL,
    UNIQUE (delivery_job_id, attempt_number)
);

CREATE INDEX idx_delivery_attempts_tenant_time
    ON notifications.delivery_attempts (tenant_id, attempted_at DESC);

CREATE TABLE operations.idempotency_keys (
    tenant_id UUID NOT NULL REFERENCES identity.organizations(id),
    operation TEXT NOT NULL,
    actor_id UUID NOT NULL REFERENCES identity.users(id),
    idempotency_key TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('started', 'completed', 'failed')),
    response_payload JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (tenant_id, operation, actor_id, idempotency_key),
    CHECK (expires_at > created_at)
);

CREATE INDEX idx_idempotency_expiry
    ON operations.idempotency_keys (expires_at);

CREATE TABLE operations.consumer_receipts (
    consumer_name TEXT NOT NULL,
    event_id UUID NOT NULL,
    event_type TEXT NOT NULL,
    processed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    result_hash TEXT,
    expires_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (consumer_name, event_id),
    CHECK (expires_at > processed_at)
);

CREATE INDEX idx_consumer_receipts_expiry
    ON operations.consumer_receipts (expires_at);

CREATE TABLE operations.import_runs (
    id UUID PRIMARY KEY,
    tenant_id UUID REFERENCES identity.organizations(id),
    source_type TEXT NOT NULL,
    source_fingerprint TEXT NOT NULL,
    status TEXT NOT NULL
        CHECK (status IN ('planned', 'running', 'reconciled', 'failed')),
    rows_read BIGINT NOT NULL DEFAULT 0 CHECK (rows_read >= 0),
    rows_written BIGINT NOT NULL DEFAULT 0 CHECK (rows_written >= 0),
    rows_rejected BIGINT NOT NULL DEFAULT 0 CHECK (rows_rejected >= 0),
    reconciliation_payload JSONB NOT NULL DEFAULT '{}'::JSONB,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (source_type, source_fingerprint)
);

CREATE TABLE operations.audit_events (
    id UUID PRIMARY KEY,
    tenant_id UUID REFERENCES identity.organizations(id),
    actor_id UUID REFERENCES identity.users(id),
    event_type TEXT NOT NULL,
    subject_type TEXT NOT NULL,
    subject_id TEXT,
    payload JSONB NOT NULL,
    previous_hash TEXT,
    entry_hash TEXT NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX idx_audit_events_tenant_time
    ON operations.audit_events (tenant_id, occurred_at DESC, id DESC);
