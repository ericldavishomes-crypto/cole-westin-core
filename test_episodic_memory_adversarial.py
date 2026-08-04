import os
import time
import random
import logging
import concurrent.futures
import threading
import pytest
import uuid
from datetime import datetime, timezone, timedelta

from qdrant_client.models import Filter, FieldCondition, MatchValue, PointStruct
from episodic_memory import (
    ReviewerProvenance, 
    generate_versioned_point_id, 
    MAX_TOTAL_REEMBEDDING_FAILURES, 
    OUTBOX_MAX_RETRIES
)

logger = logging.getLogger(__name__)

ADVERSARIAL_RUNS = int(os.getenv("ADVERSARIAL_RUNS", "1"))

# =====================================================================
# 🏛️ ORACLE WITH BIDIRECTIONAL HYGIENE & STRICT STATE MATRIX
# =====================================================================
def assert_system_invariants(db_pool, qdrant_client, episode_id: str, pinned_model_version: str):
    conn = db_pool.getconn()
    try:
        conn.rollback()
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT review_status, index_status, index_sync_status, active_qdrant_point_id, 
                       candidate_claim_token, reembedding_claim_token, summary_sha256, 
                       embedding_source_sha256, dense_summary, candidate_attempts, 
                       lease_recovery_count, candidate_claimed_at, reembedding_claimed_at, 
                       reembedding_worker_id, next_retry_at
                FROM episodic_memories 
                WHERE id = %s::uuid
                """,
                (episode_id,)
            )
            row = cursor.fetchone()
            
        assert row is not None, f"Memory row missing for episode {episode_id}"
        
        (
            rev_status, idx_status, sync_status, active_pt_id, 
            cand_token, reem_token, sum_sha, emb_sha, dense_sum,
            cand_attempts, lease_recoveries, cand_claimed_at, reem_claimed_at,
            reem_worker_id, next_retry_at
        ) = row
        active_point_id = str(active_pt_id) if active_pt_id is not None else None

        legal_state_matrix = {
            ("unverified", "index_pending", "sync_pending"),
            ("unverified", "indexing_in_progress", "sync_in_progress"),
            ("unverified", "indexed", "synced"),
            ("unverified", "index_failed", "sync_failed"),
            ("unverified", "candidate_index_exhausted", "sync_failed"),
            ("verified", "index_pending", "reembedding_pending"),
            ("verified", "indexed", "reembedding_pending"),
            ("verified", "index_pending", "reembedding_in_progress"),
            ("verified", "indexed", "reembedding_in_progress"),
            ("verified", "indexed", "synced"),
            ("verified", "index_pending", "reembedding_exhausted"),
            ("verified", "indexed", "reembedding_exhausted"),
        }
        
        current_state_tuple = (rev_status, idx_status, sync_status)
        assert current_state_tuple in legal_state_matrix, f"Illegal state combination: {current_state_tuple}"

        # Strict Bidirectional Token & Lease Hygiene Checks
        if idx_status == "indexing_in_progress":
            assert cand_token is not None, "indexing_in_progress missing candidate_claim_token"
            assert cand_claimed_at is not None, "indexing_in_progress missing candidate_claimed_at timestamp"
        else:
            assert cand_token is None, f"Non-indexing state '{idx_status}' retains candidate_claim_token"
            assert cand_claimed_at is None, f"Non-indexing state '{idx_status}' retains candidate_claimed_at"

        if sync_status == "reembedding_in_progress":
            assert reem_token is not None, "reembedding_in_progress missing reembedding_claim_token"
            assert reem_claimed_at is not None, "reembedding_in_progress missing reembedding_claimed_at timestamp"
            assert reem_worker_id is not None, "reembedding_in_progress missing reembedding_worker_id"
        else:
            assert reem_token is None, f"Non-reembedding state '{sync_status}' retains reembedding_claim_token"
            assert reem_claimed_at is None, f"Non-reembedding state '{sync_status}' retains reembedding_claimed_at"
            assert reem_worker_id is None, f"Non-reembedding state '{sync_status}' retains reembedding_worker_id"

        if sync_status == "synced":
            assert sum_sha == emb_sha, "Summary/Embedding SHA mismatch in Postgres"
            assert active_point_id is not None, "Synced memory missing active_qdrant_point_id"

            qdrant_points = qdrant_client.retrieve(collection_name="cole_episodic_memory", ids=[active_point_id])
            assert len(qdrant_points) == 1, f"Active point {active_point_id} not found in Qdrant"
            
            payload = qdrant_points[0].payload
            assert str(payload.get("postgres_episode_id")) == str(episode_id)
            assert payload.get("summary_sha256") == sum_sha
            assert payload.get("embedding_source_sha256") == emb_sha
            assert payload.get("episode_summary") == dense_sum
            assert payload.get("embedding_model") == pinned_model_version
            assert payload.get("review_status") == rev_status

        # Outbox Retry Policy & Orphan Pruning Verification
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, event_type, payload, status, retry_count, next_retry_at 
                FROM operational_outbox 
                WHERE event_type = 'PRUNE_OBSOLETE_QDRANT_POINT' 
                  AND (payload->>'episode_id') = %s
                """,
                (episode_id,)
            )
            outbox_rows = cursor.fetchall()

        prune_point_ids = set()
        existing_point_ids_in_qdrant = set()

        offset = None
        while True:
            records, offset = qdrant_client.scroll(
                collection_name="cole_episodic_memory",
                scroll_filter=Filter(must=[FieldCondition(key="postgres_episode_id", match=MatchValue(value=str(episode_id)))]),
                with_payload=True, limit=100, offset=offset
            )
            existing_point_ids_in_qdrant.update(str(pt.id) for pt in records)
            if offset is None:
                break

        for ob_id, ev_type, payload, ob_status, retry_count, next_retry in outbox_rows:
            assert ob_status in ("pending", "processing", "failed", "completed")
            if ob_status == "failed":
                assert retry_count < OUTBOX_MAX_RETRIES, "Terminally failed outbox action found exceeding retry budget"
                assert next_retry is not None, "Failed outbox retryable action missing next_retry_at timestamp"

            if payload and "point_id" in payload:
                pt_id = str(payload["point_id"])
                if ob_status == "completed":
                    assert pt_id not in existing_point_ids_in_qdrant
                elif ob_status in ("pending", "processing", "failed"):
                    prune_point_ids.add(pt_id)

        if active_point_id:
            assert active_point_id not in prune_point_ids

        for pt_id_str in existing_point_ids_in_qdrant:
            if pt_id_str != active_point_id:
                assert pt_id_str in prune_point_ids, f"Orphan point {pt_id_str} not queued for pruning"
    finally:
        conn.rollback()
        db_pool.putconn(conn)


# =====================================================================
# 🌪️ EXECUTABLE ADVERSERIAL SUITES (SERIAL ISOLATION)
# =====================================================================

def test_adversarial_verification_intercept(engine_fixture, db_pool, qdrant_client, complete_ingestion_bundle_fn):
    """Race 1: Verification Intercept with guaranteed barrier release and exact prune assertions."""
    for run in range(ADVERSARIAL_RUNS):
        seed = random.randrange(2**32)
        rng = random.Random(seed)
        bundle = complete_ingestion_bundle_fn(db_pool)
        episode_id = bundle["episode_id"]
        ingestion_kwargs = bundle["record_episode_kwargs"]
        
        logger.info("race=1 run=%d seed=%d episode_id=%s", run, seed, episode_id)

        barrier_reached = threading.Event()
        resume_signal = threading.Event()
        captured_cand_point = []

        def hook_cand(ep_uuid, pt_id):
            if ep_uuid == episode_id:
                captured_cand_point.append(pt_id)
                barrier_reached.set()
                time.sleep(rng.uniform(0.001, 0.004))
                assert resume_signal.wait(timeout=3.0)

        engine_fixture._test_hook_before_candidate_finalize = hook_cand
        try:
            cand_result = {}
            def candidate_worker():
                try:
                    cand_result.update(engine_fixture.record_episode(**ingestion_kwargs))
                finally:
                    resume_signal.set()

            def verification_worker():
                try:
                    assert barrier_reached.wait(timeout=3.0)
                    conn = db_pool.getconn()
                    try:
                        with conn.cursor() as cursor:
                            cursor.execute("SELECT explicit_facts FROM episodic_memories WHERE id = %s::uuid", (episode_id,))
                            claim_id = cursor.fetchone()[0][0]["claim_id"]
                    finally:
                        db_pool.putconn(conn)

                    prov = ReviewerProvenance(reviewer_id="admin-test", review_method="ui_click", summary_method="deterministic_concatenation")
                    res = engine_fixture.promote_to_verified(episode_id, [claim_id], prov)
                    assert res["outcome"] == "verification_succeeded_reembedding_pending"
                finally:
                    resume_signal.set()

            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                fc = executor.submit(candidate_worker)
                fv = executor.submit(verification_worker)
                fc.result()
                fv.result()
        finally:
            engine_fixture._test_hook_before_candidate_finalize = None

        assert cand_result.get("index_status") == "candidate_index_written_state_transition_pending"
        assert len(captured_cand_point) == 1
        cand_pt = captured_cand_point[0]

        conn = db_pool.getconn()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT COUNT(*) FROM operational_outbox 
                    WHERE event_type = 'PRUNE_OBSOLETE_QDRANT_POINT' 
                      AND status IN ('pending', 'processing', 'failed')
                      AND (payload->>'episode_id') = %s 
                      AND (payload->>'point_id') = %s
                    """,
                    (episode_id, cand_pt)
                )
                assert cursor.fetchone()[0] == 1, "Actionable durable prune event missing for intercepted candidate point"
        finally:
            db_pool.putconn(conn)

        assert_system_invariants(db_pool, qdrant_client, episode_id, "text-embedding-3-small@v2026.1")


def test_adversarial_zombie_worker_stale_lease(engine_fixture, db_pool, qdrant_client, seed_test_episode_fn):
    """Race 2: Zombie Worker Late-Wake with stale failure persistence check."""
    for run in range(ADVERSARIAL_RUNS):
        seed = random.randrange(2**32)
        rng = random.Random(seed)
        episode_id = seed_test_episode_fn(db_pool)
        
        logger.info("race=2 run=%d seed=%d episode_id=%s", run, seed, episode_id)

        token_a, token_b = str(uuid.uuid4()), str(uuid.uuid4())

        conn = db_pool.getconn()
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT summary_sha256, dense_summary, session_id FROM episodic_memories WHERE id = %s::uuid", (episode_id,))
                summary_sha, dense_summary, session_id = cursor.fetchone()

                cursor.execute(
                    """
                    UPDATE episodic_memories 
                    SET index_status = 'indexing_in_progress', index_sync_status = 'sync_in_progress',
                        candidate_claim_token = %s, candidate_claimed_at = NOW() - INTERVAL '25 minutes', candidate_attempts = 1
                    WHERE id = %s::uuid;
                    """,
                    (token_a, episode_id)
                )
                conn.commit()
        finally:
            db_pool.putconn(conn)

        reclaim_completed = threading.Event()
        point_id_b = generate_versioned_point_id(episode_id, summary_sha, "text-embedding-3-small@v2026.1", "candidate", token_b)

        def worker_b_reclaimer():
            res = engine_fixture._claim_candidate_indexing(episode_id, summary_sha, token_b)
            assert res["claimed"] is True
            
            qdrant_payload = {
                "postgres_episode_id": episode_id, "session_id": session_id,
                "iso_timestamp": datetime.now(timezone.utc).isoformat(), "unix_timestamp": datetime.now(timezone.utc).timestamp(),
                "episode_summary": dense_summary, "summary_sha256": summary_sha, "embedding_source_sha256": summary_sha,
                "review_status": "unverified", "lifecycle_state": "consolidated", "embedding_model": "text-embedding-3-small@v2026.1", "episode_schema_version": "1.0.0"
            }
            engine_fixture.qdrant.upsert(collection_name="cole_episodic_memory", points=[PointStruct(id=point_id_b, vector=[0.1]*1536, payload=qdrant_payload)])

            fin = engine_fixture._finalize_candidate_index(episode_id, summary_sha, token_b, point_id_b)
            assert fin["finalized"] is True
            reclaim_completed.set()

        def zombie_worker_a_late_wake():
            assert reclaim_completed.wait(timeout=3.0)
            time.sleep(rng.uniform(0.001, 0.004))
            
            fin_a = engine_fixture._finalize_candidate_index(episode_id, summary_sha, token_a, generate_versioned_point_id(episode_id, summary_sha, "text-embedding-3-small@v2026.1", "candidate", token_a))
            assert fin_a["finalized"] is False

            engine_fixture._handle_candidate_index_failure(episode_id, token_a, "Simulated stale failure persistence")

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            f1 = executor.submit(worker_b_reclaimer)
            f2 = executor.submit(zombie_worker_a_late_wake)
            f1.result()
            f2.result()

        assert_system_invariants(db_pool, qdrant_client, episode_id, "text-embedding-3-small@v2026.1")


@pytest.mark.parametrize(
    "schedule_order",
    ["candidate_first", "verified_first"],
)
def test_adversarial_candidate_vs_verified_stage_collision(
    engine_fixture,
    db_pool,
    qdrant_client,
    exact_matching_summary_bundle_fn,
    schedule_order,
):
    """Race 3: Deterministic parameterization across both release schedules, identical payload hash capture, and outcome assertions."""
    for run in range(ADVERSARIAL_RUNS):
        seed = random.randrange(2**32)
        bundle = exact_matching_summary_bundle_fn(db_pool)
        episode_id = bundle["episode_id"]
        shared_summary_sha = bundle["expected_summary_sha256"]
        
        logger.info("race=3 schedule=%s run=%d seed=%d episode_id=%s", schedule_order, run, seed, episode_id)

        barrier_cand = threading.Event()
        barrier_ver = threading.Event()
        release_cand = threading.Event()
        release_ver = threading.Event()
        allow_promotion = threading.Event()
        candidate_finalized = threading.Event()
        verified_finalized = threading.Event()

        cand_point_ids = []
        ver_point_ids = []
        candidate_hashes = []
        verified_hashes = []

        def hook_cand(ep_uuid, pt_id):
            if ep_uuid == episode_id:
                point = qdrant_client.retrieve(collection_name="cole_episodic_memory", ids=[pt_id])[0]
                cand_point_ids.append(str(pt_id))
                candidate_hashes.append(point.payload["summary_sha256"])
                barrier_cand.set()
                assert release_cand.wait(timeout=3.0)

        def hook_ver(ep_uuid, pt_id):
            if ep_uuid == episode_id:
                point = qdrant_client.retrieve(collection_name="cole_episodic_memory", ids=[pt_id])[0]
                ver_point_ids.append(str(pt_id))
                verified_hashes.append(point.payload["summary_sha256"])
                barrier_ver.set()
                assert release_ver.wait(timeout=3.0)

        engine_fixture._test_hook_before_candidate_finalize = hook_cand
        engine_fixture._test_hook_before_reembedding_finalize = hook_ver
        try:
            cand_result = {}
            reem_result = {}

            def candidate_stage_worker():
                try:
                    cand_result.update(engine_fixture.record_episode(**bundle["record_episode_kwargs"]))
                finally:
                    candidate_finalized.set()

            def verified_stage_worker():
                try:
                    assert allow_promotion.wait(timeout=3.0)
                    conn = db_pool.getconn()
                    try:
                        with conn.cursor() as cursor:
                            cursor.execute("SELECT explicit_facts FROM episodic_memories WHERE id = %s::uuid", (episode_id,))
                            claim_id = cursor.fetchone()[0][0]["claim_id"]
                    finally:
                        db_pool.putconn(conn)

                    prov = ReviewerProvenance(reviewer_id="admin-test", review_method="ui_click", summary_method="deterministic_concatenation")
                    promote_res = engine_fixture.promote_to_verified(episode_id, [claim_id], prov)
                    assert promote_res["outcome"] == "verification_succeeded_reembedding_pending"
                    
                    reem_result.update(engine_fixture.process_reembedding_job(episode_id))
                finally:
                    verified_finalized.set()

            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                fc = executor.submit(candidate_stage_worker)
                fv = executor.submit(verified_stage_worker)

                assert barrier_cand.wait(timeout=3.0)
                
                if schedule_order == "candidate_first":
                    release_cand.set()
                    assert candidate_finalized.wait(timeout=3.0)
                    allow_promotion.set()
                    assert barrier_ver.wait(timeout=3.0)
                    release_ver.set()
                    assert verified_finalized.wait(timeout=3.0)
                else:
                    allow_promotion.set()
                    assert barrier_ver.wait(timeout=3.0)
                    release_ver.set()
                    assert verified_finalized.wait(timeout=3.0)
                    release_cand.set()
                    assert candidate_finalized.wait(timeout=3.0)

                fc.result()
                fv.result()
        finally:
            engine_fixture._test_hook_before_candidate_finalize = None
            engine_fixture._test_hook_before_reembedding_finalize = None

        # Assert identical summary payload hashes and stage isolation point IDs
        assert candidate_hashes == [shared_summary_sha]
        assert verified_hashes == [shared_summary_sha]
        assert candidate_hashes[0] == verified_hashes[0]
        assert len(cand_point_ids) == 1
        assert len(ver_point_ids) == 1
        assert cand_point_ids[0] != ver_point_ids[0]

        # Assert schedule-specific worker outcomes and prune records
        assert reem_result.get("outcome") == "reembedding_succeeded_synced"
        if schedule_order == "candidate_first":
            assert cand_result.get("index_status") == "indexed"
        else:
            assert cand_result.get("index_status") == "candidate_index_written_state_transition_pending"

        conn = db_pool.getconn()
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT active_qdrant_point_id FROM episodic_memories WHERE id = %s::uuid", (episode_id,))
                active_pt = str(cursor.fetchone()[0])
                assert active_pt == ver_point_ids[0]

                cursor.execute(
                    """
                    SELECT COUNT(*) FROM operational_outbox 
                    WHERE event_type = 'PRUNE_OBSOLETE_QDRANT_POINT' 
                      AND status IN ('pending', 'processing', 'failed')
                      AND (payload->>'episode_id') = %s 
                      AND (payload->>'point_id') = %s
                    """,
                    (episode_id, cand_point_ids[0])
                )
                assert cursor.fetchone()[0] == 1, "Actionable prune event missing for candidate losing point"
        finally:
            db_pool.putconn(conn)

        assert_system_invariants(db_pool, qdrant_client, episode_id, "text-embedding-3-small@v2026.1")
