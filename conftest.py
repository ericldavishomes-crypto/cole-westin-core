from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from io import BytesIO

import pytest
from minio import Minio
from psycopg2.pool import ThreadedConnectionPool

from episodic_memory import (
    ARCHITECTURE_VERSION,
    EMBEDDING_DIMENSION,
    EMBEDDING_MODEL_NAME,
    EMBEDDING_MODEL_VERSION,
    EPISODE_SCHEMA_VERSION,
    EpisodicMemoryEngine,
    ExtractedEpisodePayload,
    RawFactClaim,
    calculate_sha256,
)


@dataclass
class DeterministicEmbedder:
    model_name: str = EMBEDDING_MODEL_NAME
    dimension: int = EMBEDDING_DIMENSION
    model_version: str = EMBEDDING_MODEL_VERSION

    def embed_text(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        return [((digest[i % len(digest)] / 255.0) - 0.5) for i in range(self.dimension)]


@pytest.fixture(scope="session")
def db_pool():
    pool = ThreadedConnectionPool(1, 20, dsn=os.environ["TEST_DATABASE_URL"])
    migration = os.path.join(os.path.dirname(__file__), "migrations", "001_initial_episodic_schema.sql")
    conn = pool.getconn()
    try:
        with conn.cursor() as cursor, open(migration, "r", encoding="utf-8") as handle:
            cursor.execute(handle.read())
        conn.commit()
    finally:
        pool.putconn(conn)
    yield pool
    pool.closeall()


@pytest.fixture(scope="session")
def minio_client():
    return Minio(
        os.getenv("MINIO_ENDPOINT", "localhost:9000"),
        access_key=os.getenv("MINIO_ACCESS_KEY", "minioadmin"),
        secret_key=os.getenv("MINIO_SECRET_KEY", "minioadmin"),
        secure=os.getenv("MINIO_SECURE", "false").lower() == "true",
    )


@pytest.fixture
def engine_fixture(db_pool):
    return EpisodicMemoryEngine(db_pool, DeterministicEmbedder())


def _bundle(db_pool, minio_client, summary: str) -> dict:
    session_id = f"session-{uuid.uuid4()}"
    fragment_id = str(uuid.uuid4())
    user_text = "Eric stated a fact for an adversarial episodic-memory test."
    cole_text = "Cole acknowledged the stated fact."
    transcript = f"Eric: {user_text}\nCole: {cole_text}"
    raw = transcript.encode("utf-8")
    sha = calculate_sha256(transcript)
    bucket = os.getenv("TEST_MINIO_BUCKET", "cole-episodic-tests")
    if not minio_client.bucket_exists(bucket):
        minio_client.make_bucket(bucket)
    key = f"episodes/{uuid.uuid4()}.txt"
    minio_client.put_object(
        bucket,
        key,
        BytesIO(raw),
        len(raw),
        metadata={"sha256": sha},
        content_type="text/plain",
    )
    conn = db_pool.getconn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO event_fragments(fragment_id, session_id, user_text, cole_response)
                VALUES (%s::uuid, %s, %s, %s)
                """,
                (fragment_id, session_id, user_text, cole_text),
            )
        conn.commit()
    finally:
        db_pool.putconn(conn)

    payload = ExtractedEpisodePayload(
        dense_summary=summary,
        explicit_facts=[
            RawFactClaim(
                claim="Eric stated a fact for testing.",
                evidence_quote="Eric stated a fact",
                source_fragment_ids=[fragment_id],
            )
        ],
    )
    return {
        "record_episode_kwargs": {
            "session_id": session_id,
            "raw_transcript": transcript,
            "extracted_data": payload,
            "minio_bucket": bucket,
            "minio_object_key": key,
            "minio_sha256": sha,
            "minio_byte_length": len(raw),
            "source_fragment_ids": [fragment_id],
            "extraction_model": "deterministic-test-extractor",
        },
        "expected_summary_sha256": calculate_sha256(summary),
    }


@pytest.fixture
def complete_ingestion_bundle_fn(minio_client):
    return lambda db_pool: _bundle(
        db_pool,
        minio_client,
        "Eric stated a fact and Cole acknowledged it.",
    )


@pytest.fixture
def exact_matching_summary_bundle_fn(minio_client):
    return lambda db_pool: _bundle(
        db_pool,
        minio_client,
        "Exact matching summary for candidate and verified stages.",
    )


@pytest.fixture
def seed_test_episode_fn():
    def factory(db_pool):
        episode_id = str(uuid.uuid4())
        summary = "Seeded episode for stale-lease testing."
        summary_sha = calculate_sha256(summary)
        conn = db_pool.getconn()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO episodic_memories (
                        id, session_id, idempotency_key,
                        minio_bucket, minio_object_key, minio_sha256, minio_byte_length,
                        dense_summary, summary_sha256, embedding_source_sha256,
                        explicit_facts, system_inferences, review_status, lifecycle_state,
                        index_status, index_sync_status, candidate_attempts,
                        episode_schema_version, extraction_prompt_version, extraction_model,
                        architecture_version, embedding_model, episode_started_at,
                        episode_ended_at, consolidated_at, last_ingestion_attempt_at
                    ) VALUES (
                        %s::uuid, 'session-seed', %s,
                        'test', 'test', %s, 0,
                        %s, %s, %s,
                        %s::jsonb, '[]'::jsonb, 'unverified', 'consolidated',
                        'index_pending', 'sync_pending', 0,
                        %s, '1.0.0', 'fixture', %s, %s,
                        NOW(), NOW(), NOW(), NOW()
                    )
                    """,
                    (
                        episode_id,
                        calculate_sha256(episode_id),
                        "0" * 64,
                        summary,
                        summary_sha,
                        summary_sha,
                        json.dumps([
                            {
                                "claim_id": "fixture-claim-1",
                                "claim": "Seeded fact",
                                "evidence_quote": "Seeded fact",
                                "source_fragment_ids": [],
                                "review_status": "unverified",
                            }
                        ]),
                        EPISODE_SCHEMA_VERSION,
                        ARCHITECTURE_VERSION,
                        EMBEDDING_MODEL_VERSION,
                    ),
                )
            conn.commit()
        finally:
            db_pool.putconn(conn)
        return episode_id
    return factory
