\set ON_ERROR_STOP on
\pset pager off
SET TIME ZONE 'UTC';

\echo ASSERT schemas-and-domains
DO $$
DECLARE
    missing_count INTEGER;
BEGIN
    SELECT count(*)
      INTO missing_count
      FROM unnest(ARRAY[
          'identity',
          'market_data',
          'research',
          'portfolio',
          'notifications',
          'operations',
          'billing'
      ]) AS required(schema_name)
     WHERE NOT EXISTS (
         SELECT 1
           FROM information_schema.schemata
          WHERE schemata.schema_name = required.schema_name
     );
    IF missing_count <> 0 THEN
        RAISE EXCEPTION 'missing logical schemas: %', missing_count;
    END IF;

    IF NOT EXISTS (
        SELECT 1
          FROM information_schema.domains
         WHERE domain_schema = 'operations'
           AND domain_name = 'price_value'
           AND numeric_precision = 30
           AND numeric_scale = 12
    ) THEN
        RAISE EXCEPTION 'price domain precision mismatch';
    END IF;
    IF NOT EXISTS (
        SELECT 1
          FROM information_schema.domains
         WHERE domain_schema = 'operations'
           AND domain_name = 'quantity_value'
           AND numeric_precision = 38
           AND numeric_scale = 18
    ) THEN
        RAISE EXCEPTION 'quantity domain precision mismatch';
    END IF;
END
$$;

\echo ASSERT exact-decimal-round-trip
CREATE TEMPORARY TABLE precision_samples (
    price operations.price_value NOT NULL,
    quantity operations.quantity_value NOT NULL,
    money operations.money_value NOT NULL,
    ratio operations.ratio_value NOT NULL,
    score operations.score_value NOT NULL
);
INSERT INTO precision_samples VALUES (
    123456789012.123456789012,
    987654321098765432.123456789012345678,
    -999999999999999999.123456789012,
    -1.234567890123,
    99.12345678
);
DO $$
DECLARE
    sample precision_samples%ROWTYPE;
BEGIN
    SELECT * INTO STRICT sample FROM precision_samples;
    IF sample.price <> 123456789012.123456789012 THEN
        RAISE EXCEPTION 'price round-trip mismatch: %', sample.price;
    END IF;
    IF sample.quantity <> 987654321098765432.123456789012345678 THEN
        RAISE EXCEPTION 'quantity round-trip mismatch: %', sample.quantity;
    END IF;
    IF sample.money <> -999999999999999999.123456789012 THEN
        RAISE EXCEPTION 'money round-trip mismatch: %', sample.money;
    END IF;
    IF sample.ratio <> -1.234567890123 THEN
        RAISE EXCEPTION 'ratio round-trip mismatch: %', sample.ratio;
    END IF;
    IF sample.score <> 99.12345678 THEN
        RAISE EXCEPTION 'score round-trip mismatch: %', sample.score;
    END IF;
END
$$;

\echo ASSERT round-half-up
DO $$
BEGIN
    IF operations.round_half_up(2.345, 2) <> 2.35 THEN
        RAISE EXCEPTION 'positive tie rounding mismatch';
    END IF;
    IF operations.round_half_up(-2.345, 2) <> -2.35 THEN
        RAISE EXCEPTION 'negative tie rounding mismatch';
    END IF;
    IF operations.round_half_up(1.0049, 2) <> 1.00 THEN
        RAISE EXCEPTION 'below-tie rounding mismatch';
    END IF;
    IF operations.round_half_up(1.0050, 2) <> 1.01 THEN
        RAISE EXCEPTION 'at-tie rounding mismatch';
    END IF;
END
$$;

\echo FIXTURE identities-and-market-data
INSERT INTO identity.organizations (id, slug, display_name) VALUES
    ('018f0c8a-7b9d-7cc2-9b34-000000000001', 'contract-tenant', 'Contract Tenant');
INSERT INTO identity.users (id, subject, username, display_name) VALUES
    ('018f0c8a-7b9d-7cc2-9b34-000000000002', 'local:test-user', 'TestUser', 'Test User');
INSERT INTO identity.memberships (tenant_id, user_id, role) VALUES (
    '018f0c8a-7b9d-7cc2-9b34-000000000001',
    '018f0c8a-7b9d-7cc2-9b34-000000000002',
    'owner'
);
INSERT INTO market_data.providers (code, display_name)
VALUES ('fixture', 'Fixture Provider');
INSERT INTO market_data.venues (
    id,
    code,
    display_name,
    timezone_name,
    is_24x7
) VALUES (
    '018f0c8a-7b9d-7cc2-9b34-000000000003',
    'XNAS',
    'Nasdaq',
    'America/New_York',
    FALSE
);
INSERT INTO market_data.instruments (
    id,
    instrument_key,
    canonical_symbol,
    display_name,
    asset_class,
    base_asset,
    quote_asset,
    venue_id
) VALUES (
    '018f0c8a-7b9d-7cc2-9b34-000000000004',
    'stock:XNAS:AAPL',
    'AAPL',
    'Apple Inc.',
    'stock',
    'AAPL',
    'USD',
    '018f0c8a-7b9d-7cc2-9b34-000000000003'
);
INSERT INTO market_data.instrument_symbols (
    id,
    provider_id,
    instrument_id,
    provider_symbol,
    valid_from
) VALUES (
    '018f0c8a-7b9d-7cc2-9b34-000000000005',
    1,
    '018f0c8a-7b9d-7cc2-9b34-000000000004',
    'AAPL',
    '2025-01-01T00:00:00Z'
);

INSERT INTO market_data.candles (
    provider_id,
    instrument_id,
    bar_interval,
    bucket_start,
    open_price,
    high_price,
    low_price,
    close_price,
    volume,
    provider_observed_at,
    ingested_at,
    content_checksum
)
SELECT
    1,
    '018f0c8a-7b9d-7cc2-9b34-000000000004',
    '1m',
    '2025-01-01T00:00:00Z'::TIMESTAMPTZ + (sequence_number * INTERVAL '1 minute'),
    100.000000000000 + sequence_number / 1000000.0,
    101.000000000000 + sequence_number / 1000000.0,
    99.000000000000 + sequence_number / 1000000.0,
    100.500000000000 + sequence_number / 1000000.0,
    1000.000000000000000000 + sequence_number,
    '2025-01-01T00:00:30Z'::TIMESTAMPTZ + (sequence_number * INTERVAL '1 minute'),
    '2025-01-01T00:00:31Z'::TIMESTAMPTZ + (sequence_number * INTERVAL '1 minute'),
    'fixture-' || sequence_number
FROM generate_series(1, 10000) AS series(sequence_number);

\echo ASSERT candle-uniqueness
DO $$
BEGIN
    BEGIN
        INSERT INTO market_data.candles (
            provider_id,
            instrument_id,
            bar_interval,
            bucket_start,
            open_price,
            high_price,
            low_price,
            close_price,
            volume,
            provider_observed_at,
            content_checksum
        ) VALUES (
            1,
            '018f0c8a-7b9d-7cc2-9b34-000000000004',
            '1m',
            '2025-01-01T00:01:00Z',
            100,
            101,
            99,
            100,
            1000,
            '2025-01-01T00:01:30Z',
            'duplicate'
        );
        RAISE EXCEPTION 'duplicate candle was accepted';
    EXCEPTION
        WHEN unique_violation THEN NULL;
    END;
END
$$;

\echo ASSERT utc-instant-semantics
SET TIME ZONE 'America/New_York';
DO $$
DECLARE
    stored TIMESTAMPTZ;
BEGIN
    SELECT bucket_start
      INTO STRICT stored
      FROM market_data.candles
     WHERE provider_id = 1
       AND instrument_id = '018f0c8a-7b9d-7cc2-9b34-000000000004'
       AND bar_interval = '1m'
       AND bucket_start = '2025-01-01T00:01:00Z';
    IF stored <> '2025-01-01T00:01:00Z'::TIMESTAMPTZ THEN
        RAISE EXCEPTION 'timestamptz instant changed: %', stored;
    END IF;
END
$$;
SET TIME ZONE 'UTC';

\echo FIXTURE tenant-owned-query-plans
INSERT INTO portfolio.portfolios (
    id,
    tenant_id,
    user_id,
    name,
    base_currency,
    balance,
    equity,
    peak_equity
) VALUES (
    '018f0c8a-7b9d-7cc2-9b34-000000000006',
    '018f0c8a-7b9d-7cc2-9b34-000000000001',
    '018f0c8a-7b9d-7cc2-9b34-000000000002',
    'Default',
    'USD',
    10000,
    10000,
    10000
);

INSERT INTO portfolio.paper_orders (
    id,
    tenant_id,
    user_id,
    portfolio_id,
    instrument_id,
    idempotency_key,
    side,
    order_type,
    status,
    quantity,
    created_at
)
SELECT
    ('018f0c8a-7b9d-7cc2-9b34-' || lpad(to_hex(sequence_number), 12, '0'))::UUID,
    '018f0c8a-7b9d-7cc2-9b34-000000000001',
    '018f0c8a-7b9d-7cc2-9b34-000000000002',
    '018f0c8a-7b9d-7cc2-9b34-000000000006',
    '018f0c8a-7b9d-7cc2-9b34-000000000004',
    'paper-' || sequence_number,
    'buy',
    'limit',
    CASE WHEN sequence_number % 5 = 0 THEN 'filled' ELSE 'pending' END,
    1.000000000000000000,
    '2025-01-01T00:00:00Z'::TIMESTAMPTZ + (sequence_number * INTERVAL '1 second')
FROM generate_series(100, 4099) AS series(sequence_number);

INSERT INTO notifications.destinations (
    id,
    tenant_id,
    user_id,
    name,
    destination_type,
    external_destination_id,
    secret_reference
) VALUES (
    '018f0c8a-7b9d-7cc2-9b34-000000000007',
    '018f0c8a-7b9d-7cc2-9b34-000000000001',
    '018f0c8a-7b9d-7cc2-9b34-000000000002',
    'Fixture Discord',
    'discord',
    'fixture-channel',
    'secret://fixture/discord'
);
INSERT INTO notifications.notifications (
    id,
    tenant_id,
    user_id,
    event_id,
    event_type,
    schema_version,
    instrument_id,
    title,
    body,
    payload
) VALUES (
    '018f0c8a-7b9d-7cc2-9b34-000000000008',
    '018f0c8a-7b9d-7cc2-9b34-000000000001',
    '018f0c8a-7b9d-7cc2-9b34-000000000002',
    '018f0c8a-7b9d-7cc2-9b34-000000000009',
    'analysis.completed',
    1,
    '018f0c8a-7b9d-7cc2-9b34-000000000004',
    'Fixture',
    'Fixture notification',
    '{}'::JSONB
);
INSERT INTO notifications.delivery_jobs (
    id,
    tenant_id,
    notification_id,
    destination_id,
    status,
    available_at
) VALUES (
    '018f0c8a-7b9d-7cc2-9b34-000000000010',
    '018f0c8a-7b9d-7cc2-9b34-000000000001',
    '018f0c8a-7b9d-7cc2-9b34-000000000008',
    '018f0c8a-7b9d-7cc2-9b34-000000000007',
    'pending',
    '2025-01-01T00:00:00Z'
);
INSERT INTO operations.audit_events (
    id,
    tenant_id,
    actor_id,
    event_type,
    subject_type,
    subject_id,
    payload,
    entry_hash,
    occurred_at
) VALUES (
    '018f0c8a-7b9d-7cc2-9b34-000000000011',
    '018f0c8a-7b9d-7cc2-9b34-000000000001',
    '018f0c8a-7b9d-7cc2-9b34-000000000002',
    'contract.verified',
    'postgresql_contract',
    'v1',
    '{}'::JSONB,
    'fixture-hash',
    '2025-01-01T00:00:00Z'
);

ANALYZE market_data.candles;
ANALYZE portfolio.paper_orders;
ANALYZE notifications.delivery_jobs;
ANALYZE operations.audit_events;
SET enable_seqscan = off;

\echo PLAN latest-candles
EXPLAIN (COSTS OFF)
SELECT bucket_start, open_price, high_price, low_price, close_price, volume
  FROM market_data.candles
 WHERE instrument_id = '018f0c8a-7b9d-7cc2-9b34-000000000004'
   AND bar_interval = '1m'
 ORDER BY bucket_start DESC
 LIMIT 100;

\echo PLAN open-paper-orders
EXPLAIN (COSTS OFF)
SELECT id, status, created_at
  FROM portfolio.paper_orders
 WHERE tenant_id = '018f0c8a-7b9d-7cc2-9b34-000000000001'
   AND user_id = '018f0c8a-7b9d-7cc2-9b34-000000000002'
   AND status IN ('pending', 'accepted', 'partially_filled', 'triggered')
 ORDER BY created_at DESC
 LIMIT 100;

\echo PLAN due-delivery-jobs
EXPLAIN (COSTS OFF)
SELECT id, available_at
  FROM notifications.delivery_jobs
 WHERE status IN ('pending', 'retry')
   AND available_at <= '2025-01-02T00:00:00Z'
 ORDER BY available_at, id
 LIMIT 100;

\echo PLAN tenant-audit
EXPLAIN (COSTS OFF)
SELECT id, event_type, occurred_at
  FROM operations.audit_events
 WHERE tenant_id = '018f0c8a-7b9d-7cc2-9b34-000000000001'
 ORDER BY occurred_at DESC, id DESC
 LIMIT 100;

RESET enable_seqscan;
\echo CONTRACT VERIFIED
