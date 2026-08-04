CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE IF NOT EXISTS event_fragments (
    fragment_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    session_id VARCHAR(255) NOT NULL,
    user_text TEXT,
    cole_response TEXT,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS episodic_memories (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    session_id VARCHAR(255) NOT NULL,
    idempotency_key VARCHAR(64) NOT NULL UNIQUE,
    minio_bucket VARCHAR(255) NOT NULL,
    minio_object_key TEXT NOT NULL,
    minio_sha256 VARCHAR(64) NOT NULL,
    minio_byte_length BIGINT NOT NULL CHECK (minio_byte_length >= 0),
    dense_summary TEXT NOT NULL,
    summary_sha256 VARCHAR(64) NOT NULL,
    embedding_source_sha256 VARCHAR(64),
    explicit_facts JSONB NOT NULL DEFAULT '[]'::jsonb,
    system_inferences JSONB NOT NULL DEFAULT '[]'::jsonb,
    review_status VARCHAR(50) NOT NULL DEFAULT 'unverified',
    lifecycle_state VARCHAR(50) NOT NULL DEFAULT 'consolidated',
    index_status VARCHAR(50) NOT NULL DEFAULT 'index_pending',
    index_sync_status VARCHAR(50) NOT NULL DEFAULT 'sync_pending',
    active_qdrant_point_id UUID,
    candidate_claim_token UUID,
    candidate_claimed_at TIMESTAMPTZ,
    candidate_summary_sha256 VARCHAR(64),
    candidate_attempts INTEGER NOT NULL DEFAULT 0,
    reembedding_claim_token UUID,
    reembedding_claimed_at TIMESTAMPTZ,
    reembedding_worker_id VARCHAR(255),
    reembedding_attempts INTEGER NOT NULL DEFAULT 0,
    lease_recovery_count INTEGER NOT NULL DEFAULT 0,
    next_retry_at TIMESTAMPTZ,
    episode_schema_version VARCHAR(50) NOT NULL,
    extraction_prompt_version VARCHAR(50) NOT NULL,
    extraction_model VARCHAR(255) NOT NULL,
    architecture_version VARCHAR(50) NOT NULL,
    embedding_model VARCHAR(255) NOT NULL,
    episode_started_at TIMESTAMPTZ NOT NULL,
    episode_ended_at TIMESTAMPTZ NOT NULL,
    consolidated_at TIMESTAMPTZ NOT NULL,
    last_ingestion_attempt_at TIMESTAMPTZ NOT NULL,
    verified_at TIMESTAMPTZ,
    verified_by VARCHAR(255),
    reviewer_type VARCHAR(50),
    review_method VARCHAR(255),
    review_notes TEXT,
    verification_version VARCHAR(50),
    verified_summary_method VARCHAR(50),
    verified_summary_model VARCHAR(255),
    verified_summary_prompt_version VARCHAR(50),
    verified_summary_claim_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    last_index_error TEXT,
    last_index_sync_at TIMESTAMPTZ,
    last_reembedding_error TEXT,
    last_reembedding_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT review_status_ck CHECK (review_status IN ('unverified','verified','rejected')),
    CONSTRAINT index_status_ck CHECK (index_status IN ('index_pending','indexing_in_progress','indexed','index_failed','candidate_index_exhausted')),
    CONSTRAINT sync_status_ck CHECK (index_sync_status IN ('sync_pending','sync_in_progress','synced','sync_failed','reembedding_pending','reembedding_in_progress','reembedding_exhausted')),
    CONSTRAINT candidate_lease_pair_ck CHECK (
        (candidate_claim_token IS NULL AND candidate_claimed_at IS NULL)
        OR (candidate_claim_token IS NOT NULL AND candidate_claimed_at IS NOT NULL)
    ),
    CONSTRAINT reembedding_lease_triplet_ck CHECK (
        (reembedding_claim_token IS NULL AND reembedding_claimed_at IS NULL AND reembedding_worker_id IS NULL)
        OR (reembedding_claim_token IS NOT NULL AND reembedding_claimed_at IS NOT NULL AND reembedding_worker_id IS NOT NULL)
    )
);

CREATE TABLE IF NOT EXISTS episode_fragment_sources (
    episode_id UUID NOT NULL REFERENCES episodic_memories(id) ON DELETE CASCADE,
    fragment_id UUID NOT NULL REFERENCES event_fragments(fragment_id) ON DELETE RESTRICT,
    source_order INTEGER NOT NULL CHECK (source_order >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (episode_id, fragment_id),
    UNIQUE (episode_id, source_order)
);

CREATE TABLE IF NOT EXISTS operational_outbox (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    event_type VARCHAR(100) NOT NULL,
    payload JSONB NOT NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'pending',
    retry_count INTEGER NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
    next_retry_at TIMESTAMPTZ,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT outbox_status_ck CHECK (status IN ('pending','processing','failed','completed','exhausted'))
);

CREATE INDEX IF NOT EXISTS idx_event_fragments_session_time ON event_fragments(session_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_episodic_state ON episodic_memories(review_status, index_status, index_sync_status);
CREATE INDEX IF NOT EXISTS idx_candidate_lease ON episodic_memories(candidate_claimed_at) WHERE index_status='indexing_in_progress';
CREATE INDEX IF NOT EXISTS idx_reembedding_lease ON episodic_memories(reembedding_claimed_at) WHERE index_sync_status='reembedding_in_progress';
CREATE INDEX IF NOT EXISTS idx_outbox_delivery ON operational_outbox(status, next_retry_at);
