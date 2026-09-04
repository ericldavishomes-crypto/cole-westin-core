-- =====================================================================
-- 005_sleep_outbox_consumer_safety.sql
--
-- Harden cole_sleep_outbox for safe concurrent consumption.
--
-- Principles:
--
--   1. Sleep phase events are durable triggers, not historical truth.
--   2. PostgreSQL event/fragment timestamps remain authoritative.
--   3. Concurrent workers must never claim the same outbox event.
--   4. retry_count records failed processing attempts.
--   5. lease_recovery_count records recovery from abandoned leases.
--   6. Worker death must not be misclassified as processing failure.
--   7. Both processing retries and lease recovery are bounded.
--   8. Existing pending sleep events remain valid and unchanged.
-- =====================================================================


-- ---------------------------------------------------------------------
-- 1. Processing retry and lease ownership state
-- ---------------------------------------------------------------------

ALTER TABLE cole_sleep_outbox
    ADD COLUMN max_retries INTEGER NOT NULL DEFAULT 5,
    ADD COLUMN last_error TEXT,
    ADD COLUMN lease_owner VARCHAR,
    ADD COLUMN lease_token UUID,
    ADD COLUMN lease_acquired_at TIMESTAMPTZ,
    ADD COLUMN lease_expires_at TIMESTAMPTZ,
    ADD COLUMN lease_recovery_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN max_lease_recoveries INTEGER NOT NULL DEFAULT 3;


-- ---------------------------------------------------------------------
-- 2. Consumer lifecycle invariants
-- ---------------------------------------------------------------------

ALTER TABLE cole_sleep_outbox
    ADD CONSTRAINT cole_sleep_outbox_status_ck
        CHECK (
            status IN (
                'pending',
                'processing',
                'retry_wait',
                'completed',
                'exhausted'
            )
        ),

    ADD CONSTRAINT cole_sleep_outbox_retry_count_ck
        CHECK (
            retry_count >= 0
            AND max_retries > 0
            AND retry_count <= max_retries
        ),

    ADD CONSTRAINT cole_sleep_outbox_lease_recovery_count_ck
        CHECK (
            lease_recovery_count >= 0
            AND max_lease_recoveries > 0
            AND lease_recovery_count <= max_lease_recoveries
        ),

    -- A consumer owns a complete lease or no lease at all.
    ADD CONSTRAINT cole_sleep_outbox_lease_ck
        CHECK (
            (
                lease_owner IS NULL
                AND lease_token IS NULL
                AND lease_acquired_at IS NULL
                AND lease_expires_at IS NULL
            )
            OR
            (
                lease_owner IS NOT NULL
                AND lease_token IS NOT NULL
                AND lease_acquired_at IS NOT NULL
                AND lease_expires_at IS NOT NULL
                AND lease_expires_at > lease_acquired_at
            )
        ),

    -- Only actively processing events may hold leases, and actively
    -- processing events must hold one.
    ADD CONSTRAINT cole_sleep_outbox_processing_lease_state_ck
        CHECK (
            (
                status = 'processing'
                AND lease_token IS NOT NULL
            )
            OR
            (
                status <> 'processing'
                AND lease_token IS NULL
            )
        ),

    -- Pending and retry-wait events carry an eligibility timestamp.
    -- Processing/completed/exhausted events do not.
    ADD CONSTRAINT cole_sleep_outbox_retry_schedule_ck
        CHECK (
            (
                status IN ('pending', 'retry_wait')
                AND next_retry_at IS NOT NULL
            )
            OR
            (
                status IN ('processing', 'completed', 'exhausted')
                AND next_retry_at IS NULL
            )
        );


-- ---------------------------------------------------------------------
-- 3. Delivery / claim indexes
-- ---------------------------------------------------------------------

CREATE INDEX IF NOT EXISTS idx_cole_sleep_outbox_delivery
    ON cole_sleep_outbox (
        status,
        next_retry_at,
        created_at
    );


CREATE INDEX IF NOT EXISTS idx_cole_sleep_outbox_lease_expiry
    ON cole_sleep_outbox (
        lease_expires_at
    )
    WHERE status = 'processing';


-- ---------------------------------------------------------------------
-- 4. Event-type chronology index
-- ---------------------------------------------------------------------
--
-- Supports efficient phase-specific chronology and later bounded
-- handling of WINDING_DOWN / SLEEPING triggers without scanning the
-- complete outbox.
-- ---------------------------------------------------------------------

CREATE INDEX IF NOT EXISTS idx_cole_sleep_outbox_event_type_created
    ON cole_sleep_outbox (
        event_type,
        created_at
    );


-- =====================================================================
-- End 005_sleep_outbox_consumer_safety.sql
-- =====================================================================
