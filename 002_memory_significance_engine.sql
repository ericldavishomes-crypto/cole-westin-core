-- ============================================================
-- MEMORY SIGNIFICANCE ENGINE
-- PostgreSQL Schema — v1.0.0
--
-- CORE INVARIANTS
-- 1. Episodic truth remains authoritative.
-- 2. Significance is derived and rebuildable.
-- 3. Derived events are append-only.
-- 4. Processing is idempotent.
-- 5. Relevance gates retrieval before significance.
-- 6. Permanence controls decay resistance, not relevance.
-- 7. Association topology is database-enforced.
-- ============================================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
-- ============================================================
-- 1. RETRIEVAL / EXPOSURE LEDGER
-- Created first because significance events may reference it.
-- Candidate != selected != injected != consequential.
-- ============================================================

CREATE TABLE IF NOT EXISTS memory_exposure_log (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    idempotency_key VARCHAR(128) NOT NULL UNIQUE,

    episode_id UUID NOT NULL
        REFERENCES episodic_memories(id)
        ON DELETE CASCADE,

    session_id VARCHAR(255),

    query_sha256 VARCHAR(64) NOT NULL,

    candidate_retrieved BOOLEAN NOT NULL DEFAULT FALSE,
    selected_for_context BOOLEAN NOT NULL DEFAULT FALSE,
    actually_injected BOOLEAN NOT NULL DEFAULT FALSE,
    referenced_in_response BOOLEAN NOT NULL DEFAULT FALSE,
    consequential_use BOOLEAN NOT NULL DEFAULT FALSE,

    rank_before_significance INTEGER
        CHECK (
            rank_before_significance IS NULL
            OR rank_before_significance > 0
        ),

    rank_after_significance INTEGER
        CHECK (
            rank_after_significance IS NULL
            OR rank_after_significance > 0
        ),

    dense_score DOUBLE PRECISION,
    lexical_score DOUBLE PRECISION,
    relevance_score DOUBLE PRECISION,

    significance_component DOUBLE PRECISION,
    associative_component DOUBLE PRECISION,
    final_score DOUBLE PRECISION,

    relevance_gate_passed BOOLEAN NOT NULL DEFAULT FALSE,

    reinforcement_eligible BOOLEAN NOT NULL DEFAULT FALSE,
    reinforcement_applied BOOLEAN NOT NULL DEFAULT FALSE,

    exposed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT exposure_stage_order_ck CHECK (
        (NOT selected_for_context OR candidate_retrieved)
        AND (NOT actually_injected OR selected_for_context)
        AND (NOT referenced_in_response OR actually_injected)
        AND (NOT consequential_use OR actually_injected)
        AND (NOT reinforcement_applied OR reinforcement_eligible)
    )
);
-- ============================================================
-- 2. CURRENT DERIVED SIGNIFICANCE STATE
-- Rebuildable sidecar state. Episodic truth remains untouched.
-- ============================================================

CREATE TABLE IF NOT EXISTS memory_significance_state (
    episode_id UUID PRIMARY KEY
        REFERENCES episodic_memories(id)
        ON DELETE CASCADE,

    identity_relevance DOUBLE PRECISION NOT NULL DEFAULT 0.0
        CHECK (identity_relevance BETWEEN 0.0 AND 1.0),

    relational_relevance DOUBLE PRECISION NOT NULL DEFAULT 0.0
        CHECK (relational_relevance BETWEEN 0.0 AND 1.0),

    goal_relevance DOUBLE PRECISION NOT NULL DEFAULT 0.0
        CHECK (goal_relevance BETWEEN 0.0 AND 1.0),

    affective_intensity DOUBLE PRECISION NOT NULL DEFAULT 0.0
        CHECK (affective_intensity BETWEEN 0.0 AND 1.0),

    causal_importance DOUBLE PRECISION NOT NULL DEFAULT 0.0
        CHECK (causal_importance BETWEEN 0.0 AND 1.0),

    unresolved_tension DOUBLE PRECISION NOT NULL DEFAULT 0.0
        CHECK (unresolved_tension BETWEEN 0.0 AND 1.0),

    resolution_significance DOUBLE PRECISION NOT NULL DEFAULT 0.0
        CHECK (resolution_significance BETWEEN 0.0 AND 1.0),

    recurrence DOUBLE PRECISION NOT NULL DEFAULT 0.0
        CHECK (recurrence BETWEEN 0.0 AND 1.0),

    associative_centrality DOUBLE PRECISION NOT NULL DEFAULT 0.0
        CHECK (associative_centrality BETWEEN 0.0 AND 1.0),

    confidence DOUBLE PRECISION NOT NULL DEFAULT 0.0
        CHECK (confidence BETWEEN 0.0 AND 1.0),

    permanence_class VARCHAR(32) NOT NULL DEFAULT 'ordinary'
        CHECK (
            permanence_class IN (
                'ephemeral',
                'ordinary',
                'persistent',
                'foundational'
            )
        ),

    raw_reinforcement_count BIGINT NOT NULL DEFAULT 0
        CHECK (raw_reinforcement_count >= 0),

    meaningful_reinforcement_count BIGINT NOT NULL DEFAULT 0
        CHECK (meaningful_reinforcement_count >= 0),

    reinforcement_component DOUBLE PRECISION NOT NULL DEFAULT 0.0
        CHECK (reinforcement_component BETWEEN 0.0 AND 1.0),

    temporal_component DOUBLE PRECISION NOT NULL DEFAULT 1.0
        CHECK (temporal_component BETWEEN 0.0 AND 1.0),

    significance_score DOUBLE PRECISION NOT NULL DEFAULT 0.0
        CHECK (significance_score BETWEEN 0.0 AND 1.0),

    significance_formula_version VARCHAR(64) NOT NULL,
    decay_policy_version VARCHAR(64) NOT NULL,
    reinforcement_policy_version VARCHAR(64) NOT NULL,
    association_policy_version VARCHAR(64) NOT NULL,

    state_version BIGINT NOT NULL DEFAULT 0
        CHECK (state_version >= 0),

    evaluated_as_of TIMESTAMPTZ NOT NULL,
    last_reinforced_at TIMESTAMPTZ,
    last_evaluated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
-- ============================================================
-- 3. IMMUTABLE SIGNIFICANCE EVENT LEDGER
-- Durable derived-event history used for audit and replay.
-- ============================================================

CREATE TABLE IF NOT EXISTS memory_significance_events (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    episode_id UUID NOT NULL
        REFERENCES episodic_memories(id)
        ON DELETE CASCADE,

    idempotency_key VARCHAR(128) NOT NULL UNIQUE,

    event_type VARCHAR(64) NOT NULL
        CHECK (
            event_type IN (
                'initialized',
                'dimension_changed',
                'reinforced',
                'decayed',
                'tension_increased',
                'tension_resolved',
                'permanence_changed',
                'centrality_recalculated',
                'human_reviewed',
                'policy_recalculated'
            )
        ),

    dimension VARCHAR(64),

    old_numeric_value DOUBLE PRECISION,
    new_numeric_value DOUBLE PRECISION,

    old_text_value TEXT,
    new_text_value TEXT,

    reason VARCHAR(160) NOT NULL,

    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    calculation_inputs JSONB NOT NULL DEFAULT '{}'::jsonb,

    trigger_episode_id UUID
        REFERENCES episodic_memories(id)
        ON DELETE SET NULL,

    trigger_exposure_id UUID
        REFERENCES memory_exposure_log(id)
        ON DELETE SET NULL,

    significance_formula_version VARCHAR(64) NOT NULL,
    decay_policy_version VARCHAR(64) NOT NULL,
    reinforcement_policy_version VARCHAR(64) NOT NULL,
    association_policy_version VARCHAR(64) NOT NULL,

    evaluated_as_of TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT significance_event_has_change_ck CHECK (
        old_numeric_value IS NOT NULL
        OR new_numeric_value IS NOT NULL
        OR old_text_value IS NOT NULL
        OR new_text_value IS NOT NULL
        OR event_type IN (
            'initialized',
            'human_reviewed',
            'policy_recalculated'
        )
    )
);
-- ============================================================
-- 4. IMMUTABLE REINFORCEMENT EVENT LEDGER
-- Tracks exposure/consequence-driven reinforcement separately.
-- ============================================================

CREATE TABLE IF NOT EXISTS memory_reinforcement_events (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    idempotency_key VARCHAR(128) NOT NULL UNIQUE,

    episode_id UUID NOT NULL
        REFERENCES episodic_memories(id)
        ON DELETE CASCADE,

    exposure_id UUID
        REFERENCES memory_exposure_log(id)
        ON DELETE SET NULL,

    session_id VARCHAR(255),

    reinforcement_type VARCHAR(64) NOT NULL
        CHECK (
            reinforcement_type IN (
                'exposure',
                'reference',
                'consequence',
                'resolution',
                'recurrence',
                'human_review'
            )
        ),

    raw_increment DOUBLE PRECISION NOT NULL
        CHECK (raw_increment >= 0.0),

    applied_increment DOUBLE PRECISION NOT NULL
        CHECK (applied_increment >= 0.0),

    cooldown_applied BOOLEAN NOT NULL DEFAULT FALSE,
    saturation_applied BOOLEAN NOT NULL DEFAULT FALSE,

    cooldown_window_seconds BIGINT
        CHECK (
            cooldown_window_seconds IS NULL
            OR cooldown_window_seconds >= 0
        ),

    prior_meaningful_reinforcement_count BIGINT NOT NULL DEFAULT 0
        CHECK (prior_meaningful_reinforcement_count >= 0),

    reason VARCHAR(160) NOT NULL,

    calculation_inputs JSONB NOT NULL DEFAULT '{}'::jsonb,

    reinforcement_policy_version VARCHAR(64) NOT NULL,

    evaluated_as_of TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
-- ============================================================
-- 5. CURRENT MEMORY ASSOCIATIONS
-- Derived association topology between verified episodes.
-- Symmetric edges are stored in canonical UUID order.
-- ============================================================

CREATE TABLE IF NOT EXISTS memory_associations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    source_episode_id UUID NOT NULL
        REFERENCES episodic_memories(id)
        ON DELETE CASCADE,

    target_episode_id UUID NOT NULL
        REFERENCES episodic_memories(id)
        ON DELETE CASCADE,

    association_type VARCHAR(64) NOT NULL
        CHECK (
            association_type IN (
                'same_person',
                'same_goal',
                'same_place',
                'same_event_chain',
                'causal',
                'contradiction',
                'reinforcement',
                'continuation',
                'resolution',
                'shared_theme'
            )
        ),

    directionality VARCHAR(16) NOT NULL
        CHECK (
            directionality IN (
                'directed',
                'symmetric'
            )
        ),

    strength DOUBLE PRECISION NOT NULL
        CHECK (strength BETWEEN 0.0 AND 1.0),

    confidence DOUBLE PRECISION NOT NULL
        CHECK (confidence BETWEEN 0.0 AND 1.0),

    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,

    derivation_method VARCHAR(64) NOT NULL
        CHECK (
            derivation_method IN (
                'deterministic_rule',
                'verified_claim_analysis',
                'consolidation_worker',
                'human_review'
            )
        ),

    association_policy_version VARCHAR(64) NOT NULL,

    evaluated_as_of TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT association_no_self_ck CHECK (
        source_episode_id <> target_episode_id
    ),

    CONSTRAINT association_symmetric_order_ck CHECK (
        directionality <> 'symmetric'
        OR source_episode_id < target_episode_id
    ),

    CONSTRAINT association_type_directionality_ck CHECK (
        (
            association_type IN (
                'same_person',
                'same_goal',
                'same_place',
                'shared_theme'
            )
            AND directionality = 'symmetric'
        )
        OR
        (
            association_type IN (
                'same_event_chain',
                'causal',
                'reinforcement',
                'continuation',
                'resolution'
            )
            AND directionality = 'directed'
        )
        OR
        (
            association_type = 'contradiction'
            AND directionality IN ('directed', 'symmetric')
        )
    ),

    CONSTRAINT memory_associations_unique_edge
        UNIQUE (
            source_episode_id,
            target_episode_id,
            association_type
        )
);
-- ============================================================
-- 6. IMMUTABLE ASSOCIATION EVENT LEDGER
-- Append-only history for association topology changes.
-- Uses the same structural rules as current association state.
-- ============================================================

CREATE TABLE IF NOT EXISTS memory_association_events (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    idempotency_key VARCHAR(128) NOT NULL UNIQUE,

    source_episode_id UUID NOT NULL
        REFERENCES episodic_memories(id)
        ON DELETE CASCADE,

    target_episode_id UUID NOT NULL
        REFERENCES episodic_memories(id)
        ON DELETE CASCADE,

    association_type VARCHAR(64) NOT NULL
        CHECK (
            association_type IN (
                'same_person',
                'same_goal',
                'same_place',
                'same_event_chain',
                'causal',
                'contradiction',
                'reinforcement',
                'continuation',
                'resolution',
                'shared_theme'
            )
        ),

    event_type VARCHAR(32) NOT NULL
        CHECK (
            event_type IN (
                'created',
                'strengthened',
                'weakened',
                'rejected',
                'reviewed',
                'removed'
            )
        ),

    directionality VARCHAR(16) NOT NULL
        CHECK (
            directionality IN (
                'directed',
                'symmetric'
            )
        ),

    old_strength DOUBLE PRECISION
        CHECK (
            old_strength IS NULL
            OR old_strength BETWEEN 0.0 AND 1.0
        ),

    new_strength DOUBLE PRECISION
        CHECK (
            new_strength IS NULL
            OR new_strength BETWEEN 0.0 AND 1.0
        ),

    confidence DOUBLE PRECISION
        CHECK (
            confidence IS NULL
            OR confidence BETWEEN 0.0 AND 1.0
        ),

    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,

    derivation_method VARCHAR(64) NOT NULL
        CHECK (
            derivation_method IN (
                'deterministic_rule',
                'verified_claim_analysis',
                'consolidation_worker',
                'human_review'
            )
        ),

    association_policy_version VARCHAR(64) NOT NULL,

    evaluated_as_of TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT association_event_no_self_ck CHECK (
        source_episode_id <> target_episode_id
    ),

    CONSTRAINT association_event_symmetric_order_ck CHECK (
        directionality <> 'symmetric'
        OR source_episode_id < target_episode_id
    ),

    CONSTRAINT association_event_type_directionality_ck CHECK (
        (
            association_type IN (
                'same_person',
                'same_goal',
                'same_place',
                'shared_theme'
            )
            AND directionality = 'symmetric'
        )
        OR
        (
            association_type IN (
                'same_event_chain',
                'causal',
                'reinforcement',
                'continuation',
                'resolution'
            )
            AND directionality = 'directed'
        )
        OR
        (
            association_type = 'contradiction'
            AND directionality IN ('directed', 'symmetric')
        )
    )
);
-- ============================================================
-- 7. SIGNIFICANCE PROCESSING / LEASE STATE
-- Prevents concurrent workers from silently overwriting
-- derived significance state.
-- ============================================================

CREATE TABLE IF NOT EXISTS memory_significance_processing (
    episode_id UUID PRIMARY KEY
        REFERENCES episodic_memories(id)
        ON DELETE CASCADE,

    lease_owner VARCHAR(255),
    lease_token UUID,
    lease_acquired_at TIMESTAMPTZ,
    lease_expires_at TIMESTAMPTZ,

    retry_count INTEGER NOT NULL DEFAULT 0
        CHECK (retry_count >= 0),

    last_error TEXT,

    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT significance_processing_lease_pairing_ck CHECK (
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
    )
);
-- ============================================================
-- 8. INDEXES
-- Support ranking, audit traversal, exposure analysis,
-- association traversal, and expired-lease recovery.
-- ============================================================

CREATE INDEX IF NOT EXISTS idx_memory_significance_score
    ON memory_significance_state (significance_score DESC);

CREATE INDEX IF NOT EXISTS idx_memory_significance_permanence
    ON memory_significance_state (permanence_class);

CREATE INDEX IF NOT EXISTS idx_memory_significance_evaluated
    ON memory_significance_state (evaluated_as_of);

CREATE INDEX IF NOT EXISTS idx_memory_significance_events_episode_time
    ON memory_significance_events (episode_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_memory_exposure_episode_time
    ON memory_exposure_log (episode_id, exposed_at DESC);

CREATE INDEX IF NOT EXISTS idx_memory_exposure_session_time
    ON memory_exposure_log (session_id, exposed_at DESC);

CREATE INDEX IF NOT EXISTS idx_memory_exposure_injected
    ON memory_exposure_log (episode_id, exposed_at DESC)
    WHERE actually_injected = TRUE;

CREATE INDEX IF NOT EXISTS idx_memory_reinforcement_episode_time
    ON memory_reinforcement_events (episode_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_memory_associations_source
    ON memory_associations (source_episode_id, strength DESC);

CREATE INDEX IF NOT EXISTS idx_memory_associations_target
    ON memory_associations (target_episode_id, strength DESC);

CREATE INDEX IF NOT EXISTS idx_memory_association_events_source_time
    ON memory_association_events (source_episode_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_memory_association_events_target_time
    ON memory_association_events (target_episode_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_memory_significance_processing_expired_lease
    ON memory_significance_processing (lease_expires_at)
    WHERE lease_expires_at IS NOT NULL;
