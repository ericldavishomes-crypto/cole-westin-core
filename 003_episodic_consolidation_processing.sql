-- =====================================================================
-- 003_episodic_consolidation_processing.sql
--
-- Upstream processing state for episodic consolidation.
--
-- Architecture:
--
--   event_fragments
--        ↓
--   episodic_consolidation_processing
--        ↓
--   MinIO transcript artifact
--        ↓
--   structured extraction
--        ↓
--   EpisodicMemoryEngine.record_episode()
--        ↓
--   episodic_memories + episode_fragment_sources
--
-- Principles:
--
--   1. event_fragments remain immutable source evidence.
--   2. Processing state lives beside source evidence, not inside it.
--   3. A fragment may participate in more than one consolidation job.
--   4. Successful episode provenance remains authoritative in
--      episode_fragment_sources.
--   5. This table tracks operational ownership, retries, leases,
--      artifact creation, and successful handoff only.
--   6. Worker retries must be idempotent.
--   7. PostgreSQL remains authoritative.
-- =====================================================================


-- ---------------------------------------------------------------------
-- 1. Consolidation processing jobs
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS episodic_consolidation_processing (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    -- Deterministic worker-generated key.
    --
    -- Recommended input:
    -- session_id
    -- + ordered source fragment IDs
    -- + episode schema version
    -- + extraction prompt version
    -- + extraction model
    --
    -- This prevents duplicate logical jobs while still allowing the same
    -- fragment to participate in a different legitimate episode window.
    idempotency_key VARCHAR(64) NOT NULL UNIQUE,

    session_id VARCHAR NOT NULL,

    status VARCHAR(32) NOT NULL DEFAULT 'pending',

    -- Operational retry state.
    retry_count INTEGER NOT NULL DEFAULT 0,
    max_retries INTEGER NOT NULL DEFAULT 5,
    next_retry_at TIMESTAMPTZ,
    last_error TEXT,

    -- Worker lease.
    lease_owner VARCHAR,
    lease_token UUID,
    lease_acquired_at TIMESTAMPTZ,
    lease_expires_at TIMESTAMPTZ,

    -- Transcript artifact created before record_episode().
    --
    -- These fields are intentionally nullable while the job is still
    -- upstream of artifact creation. Once any artifact field exists,
    -- the complete artifact identity must exist.
    minio_bucket VARCHAR,
    minio_object_key VARCHAR,
    minio_sha256 VARCHAR(64),
    minio_byte_length BIGINT,

    -- Extraction metadata used for this processing attempt.
    extraction_model VARCHAR,
    extraction_prompt_version VARCHAR,

    -- Set only after record_episode() has successfully returned an
    -- authoritative PostgreSQL episode.
    episode_id UUID
        REFERENCES episodic_memories(id)
        ON DELETE SET NULL,

    completed_at TIMESTAMPTZ,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT episodic_consolidation_status_ck
        CHECK (
            status IN (
                'pending',
                'processing',
                'retry_wait',
                'completed',
                'exhausted'
            )
        ),

    CONSTRAINT episodic_consolidation_retry_count_ck
        CHECK (
            retry_count >= 0
            AND max_retries > 0
            AND retry_count <= max_retries
        ),

    -- A processing job owns a complete lease or no lease at all.
    CONSTRAINT episodic_consolidation_lease_ck
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

    -- Only actively processing jobs may hold leases, and actively
    -- processing jobs must hold one.
    CONSTRAINT episodic_consolidation_processing_lease_state_ck
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

    -- Artifact metadata is all-or-nothing.
    CONSTRAINT episodic_consolidation_artifact_ck
        CHECK (
            (
                minio_bucket IS NULL
                AND minio_object_key IS NULL
                AND minio_sha256 IS NULL
                AND minio_byte_length IS NULL
            )
            OR
            (
                minio_bucket IS NOT NULL
                AND minio_object_key IS NOT NULL
                AND minio_sha256 IS NOT NULL
                AND minio_byte_length IS NOT NULL
                AND minio_byte_length >= 0
                AND minio_sha256 ~ '^[0-9a-fA-F]{64}$'
            )
        ),

    -- Retry-wait jobs must have a retry time. Other states must not.
    CONSTRAINT episodic_consolidation_retry_schedule_ck
        CHECK (
            (
                status = 'retry_wait'
                AND next_retry_at IS NOT NULL
            )
            OR
            (
                status <> 'retry_wait'
                AND next_retry_at IS NULL
            )
        ),

    -- Completion means an authoritative episode exists.
    CONSTRAINT episodic_consolidation_completion_ck
        CHECK (
            (
                status = 'completed'
                AND episode_id IS NOT NULL
                AND completed_at IS NOT NULL
            )
            OR
            (
                status <> 'completed'
                AND completed_at IS NULL
            )
        ),

    CONSTRAINT episodic_consolidation_idempotency_key_ck
        CHECK (
            idempotency_key ~ '^[0-9a-fA-F]{64}$'
        )
);


-- ---------------------------------------------------------------------
-- 2. Ordered fragment membership for each consolidation job
-- ---------------------------------------------------------------------
--
-- This is deliberately a separate relational table rather than UUID[].
--
-- Benefits:
--   - real FK protection to event_fragments
--   - deterministic source order
--   - no global uniqueness on fragment_id
--   - therefore one fragment may legitimately participate in more than
--     one episode/consolidation window
--
-- Successful final provenance is still written independently by
-- EpisodicMemoryEngine into episode_fragment_sources.
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS episodic_consolidation_fragments (
    processing_id UUID NOT NULL
        REFERENCES episodic_consolidation_processing(id)
        ON DELETE CASCADE,

    fragment_id UUID NOT NULL
        REFERENCES event_fragments(fragment_id)
        ON DELETE RESTRICT,

    source_order INTEGER NOT NULL,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    PRIMARY KEY (processing_id, fragment_id),

    CONSTRAINT episodic_consolidation_fragments_order_ck
        CHECK (source_order >= 0),

    CONSTRAINT episodic_consolidation_fragments_order_key
        UNIQUE (processing_id, source_order)
);


-- ---------------------------------------------------------------------
-- 3. Delivery / claim indexes
-- ---------------------------------------------------------------------

CREATE INDEX IF NOT EXISTS idx_episodic_consolidation_delivery
    ON episodic_consolidation_processing (
        status,
        next_retry_at,
        created_at
    );


CREATE INDEX IF NOT EXISTS idx_episodic_consolidation_session
    ON episodic_consolidation_processing (
        session_id,
        created_at
    );


CREATE INDEX IF NOT EXISTS idx_episodic_consolidation_lease_expiry
    ON episodic_consolidation_processing (
        lease_expires_at
    )
    WHERE status = 'processing';


CREATE INDEX IF NOT EXISTS idx_episodic_consolidation_episode
    ON episodic_consolidation_processing (
        episode_id
    )
    WHERE episode_id IS NOT NULL;


CREATE INDEX IF NOT EXISTS idx_episodic_consolidation_fragment
    ON episodic_consolidation_fragments (
        fragment_id
    );


-- =====================================================================
-- End 003_episodic_consolidation_processing.sql
-- =====================================================================
