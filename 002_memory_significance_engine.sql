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
