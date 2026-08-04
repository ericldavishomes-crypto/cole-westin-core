from __future__ import annotations

import concurrent.futures
import json
import os
import threading
import uuid

import pytest

from episodic_memory import (
    EMBEDDING_MODEL_VERSION,
    EPISODIC_COLLECTION_NAME,
    OUTBOX_MAX_RETRIES,
    ReviewerProvenance,
    generate_versioned_point_id,
)

ADVERSARIAL_RUNS = int(os.getenv("ADVERSARIAL_RUNS", "1"))


def assert_system_invariants(db_pool, qdrant_client, episode_id: str) -> None:
    conn = db_pool.getconn()
    try:
        conn.rollback()
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT review_status, index_status, index_sync_status,
                       active_qdrant_point_id, candidate_claim_token,
                       reembedding_claim_token, summary_sha256,
                       embedding_source_sha256, dense_summary,
                       candidate_claimed_at, reembedding_claimed_at,
                       reembedding_worker_id
                FROM episodic_memories WHERE id=%s::uuid
                """,
                (episode_id,),
            )
            row = cursor.fetchone()
        assert row is not None
        (
            review_status,
            index_status,
            sync_status,
            active_point,
            candidate_token,
            reembedding_token,
            summary_sha,
            embedding_sha,
            dense_summary,
            candidate_claimed_at,
            reembedding_claimed_at,
            reembedding_worker_id,
        ) = row

        legal_states = {
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
        assert (review_status, index_status, sync_status) in legal_states

        if index_status == "indexing_in_progress":
            assert candidate_token is not None and candidate_claimed_at is not None
        else:
            assert candidate_token is None and candidate_claimed_at is None

        if sync_status == "reembedding_in_progress":
            assert reembedding_token is not None
            assert reembedding_claimed_at is not None
            assert reembedding_worker_id is not None
        else:
            assert reembedding_token is None
            assert reembedding_claimed_at is None
            assert reembedding_worker_id is None

        active_id = str(active_point) if active_point else None
        if sync_status == "synced":
            assert summary_sha == embedding_sha
            assert active_id is not None
            points = qdrant_client.retrieve(
                collection_name=EPISODIC_COLLECTION_NAME,
                ids=[active_id],
            )
            assert len(points) == 1
            payload = points[0].payload
            assert str(payload["postgres_episode_id"]) == episode_id
            assert payload["summary_sha256"] == summary_sha
            assert payload["embedding_source_sha256"] == embedding_sha
            assert payload["episode_summary"] == dense_summary
            assert payload["embedding_model"] == EMBEDDING_MODEL_VERSION
            assert payload["review_status"] == review_status

        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT payload, status, retry_count, next_retry_at
                FROM operational_outbox
                WHERE event_type='PRUNE_OBSOLETE_QDRANT_POINT'
                  AND payload->>'episode_id'=%s
                """,
                (episode_id,),
            )
            rows = cursor.fetchall()
        for payload, status, retry_count, next_retry_at in rows:
            if isinstance(payload, str):
                payload = json.loads(payload)
            assert status in {"pending", "processing", "failed", "completed", "exhausted"}
            if status == "failed":
                assert retry_count < OUTBOX_MAX_RETRIES
                assert next_retry_at is not None
    finally:
        conn.rollback()
        db_pool.putconn(conn)


def test_stage_and_generation_isolation() -> None:
    episode_id = str(uuid.uuid4())
    summary_sha = "a" * 64
    candidate = generate_versioned_point_id(
        episode_id,
        summary_sha,
        EMBEDDING_MODEL_VERSION,
        "candidate",
        "candidate-token",
    )
    verified = generate_versioned_point_id(
        episode_id,
        summary_sha,
        EMBEDDING_MODEL_VERSION,
        "verified",
        "verified-token",
    )
    assert candidate != verified


def test_verification_intercepts_candidate(
    engine_fixture,
    db_pool,
    qdrant_client,
    complete_ingestion_bundle_fn,
):
    for _ in range(ADVERSARIAL_RUNS):
        bundle = complete_ingestion_bundle_fn(db_pool)
        barrier = threading.Event()
        release = threading.Event()
        captured: list[tuple[str, str]] = []
        result: dict = {}

        def hook(episode_id, point_id):
            captured.append((episode_id, str(point_id)))
            barrier.set()
            assert release.wait(timeout=10)

        engine_fixture._test_hook_before_candidate_finalize = hook
        try:
            def candidate_worker():
                result.update(engine_fixture.record_episode(**bundle["record_episode_kwargs"]))

            def verification_worker():
                assert barrier.wait(timeout=10)
                episode_id = captured[0][0]
                conn = db_pool.getconn()
                try:
                    with conn.cursor() as cursor:
                        cursor.execute(
                            "SELECT explicit_facts FROM episodic_memories WHERE id=%s::uuid",
                            (episode_id,),
                        )
                        facts = cursor.fetchone()[0]
                        if isinstance(facts, str):
                            facts = json.loads(facts)
                        claim_id = facts[0]["claim_id"]
                finally:
                    db_pool.putconn(conn)
                response = engine_fixture.promote_to_verified(
                    episode_id,
                    [claim_id],
                    ReviewerProvenance(reviewer_id="test", review_method="test"),
                )
                assert response["outcome"] == "verification_succeeded_reembedding_pending"
                release.set()

            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                c = pool.submit(candidate_worker)
                v = pool.submit(verification_worker)
                c.result()
                v.result()
        finally:
            engine_fixture._test_hook_before_candidate_finalize = None
            release.set()

        assert result["index_status"] == "candidate_index_written_state_transition_pending"
        episode_id = captured[0][0]
        assert_system_invariants(db_pool, qdrant_client, episode_id)


@pytest.mark.parametrize("schedule_order", ["candidate_first", "verified_first"])
def test_candidate_vs_verified_stage_collision(
    engine_fixture,
    db_pool,
    qdrant_client,
    exact_matching_summary_bundle_fn,
    schedule_order,
):
    for _ in range(ADVERSARIAL_RUNS):
        bundle = exact_matching_summary_bundle_fn(db_pool)
        candidate_barrier = threading.Event()
        verified_barrier = threading.Event()
        release_candidate = threading.Event()
        release_verified = threading.Event()
        allow_promotion = threading.Event()
        candidate_points: list[tuple[str, str]] = []
        verified_points: list[tuple[str, str]] = []
        candidate_result: dict = {}
        reembedding_result: dict = {}

        def candidate_hook(episode_id, point_id):
            candidate_points.append((episode_id, str(point_id)))
            candidate_barrier.set()
            assert release_candidate.wait(timeout=10)

        def verified_hook(episode_id, point_id):
            verified_points.append((episode_id, str(point_id)))
            verified_barrier.set()
            assert release_verified.wait(timeout=10)

        engine_fixture._test_hook_before_candidate_finalize = candidate_hook
        engine_fixture._test_hook_before_reembedding_finalize = verified_hook
        try:
            def candidate_worker():
                candidate_result.update(
                    engine_fixture.record_episode(**bundle["record_episode_kwargs"])
                )

            def verified_worker():
                assert allow_promotion.wait(timeout=10)
                episode_id = candidate_points[0][0]
                conn = db_pool.getconn()
                try:
                    with conn.cursor() as cursor:
                        cursor.execute(
                            "SELECT explicit_facts FROM episodic_memories WHERE id=%s::uuid",
                            (episode_id,),
                        )
                        facts = cursor.fetchone()[0]
                        if isinstance(facts, str):
                            facts = json.loads(facts)
                        claim_id = facts[0]["claim_id"]
                finally:
                    db_pool.putconn(conn)
                promoted = engine_fixture.promote_to_verified(
                    episode_id,
                    [claim_id],
                    ReviewerProvenance(reviewer_id="test", review_method="test"),
                )
                assert promoted["outcome"] == "verification_succeeded_reembedding_pending"
                reembedding_result.update(engine_fixture.process_reembedding_job(episode_id))

            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                c = pool.submit(candidate_worker)
                v = pool.submit(verified_worker)
                assert candidate_barrier.wait(timeout=10)
                if schedule_order == "candidate_first":
                    release_candidate.set()
                    c.result()
                    allow_promotion.set()
                    assert verified_barrier.wait(timeout=10)
                    release_verified.set()
                    v.result()
                else:
                    allow_promotion.set()
                    assert verified_barrier.wait(timeout=10)
                    release_verified.set()
                    v.result()
                    release_candidate.set()
                    c.result()
        finally:
            engine_fixture._test_hook_before_candidate_finalize = None
            engine_fixture._test_hook_before_reembedding_finalize = None
            release_candidate.set()
            release_verified.set()
            allow_promotion.set()

        assert len(candidate_points) == 1
        assert len(verified_points) == 1
        assert candidate_points[0][1] != verified_points[0][1]
        assert reembedding_result["outcome"] == "reembedding_succeeded_synced"
        assert_system_invariants(db_pool, qdrant_client, candidate_points[0][0])
