-- =====================================================================
-- 004_episodic_consolidation_lease_recovery.sql
--
-- Separate stale-lease recovery accounting from ordinary processing
-- retry accounting for episodic consolidation jobs.
--
-- Principles:
--
--   1. retry_count records failed processing attempts.
--   2. lease_recovery_count records recovery from abandoned leases.
--   3. Worker death must not be misclassified as processing failure.
--   4. Lease recovery is explicitly bounded.
--   5. PostgreSQL remains authoritative.
-- =====================================================================


-- ---------------------------------------------------------------------
-- 1. Lease recovery accounting
-- ---------------------------------------------------------------------

ALTER TABLE episodic_consolidation_processing
    ADD COLUMN lease_recovery_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN max_lease_recoveries INTEGER NOT NULL DEFAULT 3;


-- ---------------------------------------------------------------------
-- 2. Lease recovery invariant
-- ---------------------------------------------------------------------

ALTER TABLE episodic_consolidation_processing
    ADD CONSTRAINT episodic_consolidation_lease_recovery_count_ck
        CHECK (
            lease_recovery_count >= 0
            AND max_lease_recoveries > 0
            AND lease_recovery_count <= max_lease_recoveries
        );


-- =====================================================================
-- End 004_episodic_consolidation_lease_recovery.sql
-- =====================================================================
