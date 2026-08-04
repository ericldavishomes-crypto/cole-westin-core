from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import unicodedata
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional

from minio import Minio
from pydantic import BaseModel, Field
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PayloadSchemaType, PointStruct, VectorParams

logger = logging.getLogger(__name__)

ENV_MODE = os.getenv("ENV_MODE", "production")
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY")
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "localhost:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minioadmin")
MINIO_SECURE = os.getenv("MINIO_SECURE", "false").lower() == "true"

EPISODIC_COLLECTION_NAME = "cole_episodic_memory"
EMBEDDING_DIMENSION = 1536
EMBEDDING_MODEL_NAME = "text-embedding-3-small"
EMBEDDING_MODEL_VERSION = "text-embedding-3-small@v2026.1"
EPISODE_SCHEMA_VERSION = "1.0.0"
EXTRACTION_PROMPT_VERSION = "1.0.0"
ARCHITECTURE_VERSION = "2.5.2-RC1"
MAX_CANDIDATE_ATTEMPTS = 3
MAX_TOTAL_REEMBEDDING_FAILURES = 5
MAX_LEASE_RECOVERIES = 3
WORKER_LEASE_MINUTES = 15
OUTBOX_MAX_RETRIES = 5
VECTOR_POINT_NAMESPACE = uuid.NAMESPACE_DNS


def normalize_text(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKD", text).lower().split())


def calculate_sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def normalize_uuid(value: Any) -> Optional[str]:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        return None


def generate_claim_id(claim_type: str, claim_text: str, source_fragment_ids: list[str]) -> str:
    return calculate_sha256(
        f"{claim_type}:{normalize_text(claim_text)}:{','.join(source_fragment_ids)}"
    )


def generate_versioned_point_id(
    episode_id: str,
    summary_sha256: str,
    model_version: str,
    index_stage: Literal["candidate", "verified"],
    generation_id: str,
) -> str:
    seed = f"{episode_id}:{summary_sha256}:{model_version}:{index_stage}:{generation_id}"
    return str(uuid.uuid5(VECTOR_POINT_NAMESPACE, seed))


def validate_vector(vector: list[float], expected_dim: int = EMBEDDING_DIMENSION) -> None:
    if len(vector) != expected_dim:
        raise ValueError(f"Expected vector dimension {expected_dim}; got {len(vector)}")
    if any(not math.isfinite(float(v)) for v in vector):
        raise ValueError("Vector contains NaN or Infinity")


class ReviewerProvenance(BaseModel):
    reviewer_type: Literal["human", "automated_rule", "model_assisted"] = "human"
    reviewer_id: str
    review_method: str
    verification_version: str = "1.0.0"
    summary_method: Literal[
        "deterministic_concatenation", "constrained_llm", "human_authored"
    ] = "deterministic_concatenation"
    summary_model: Optional[str] = None
    summary_prompt_version: Optional[str] = None
    verified_summary_claim_ids: list[str] = Field(default_factory=list)
    review_notes: Optional[str] = None


class RawFactClaim(BaseModel):
    claim: str
    evidence_quote: str
    source_fragment_ids: list[str] = Field(min_length=1)
    extraction_confidence: float = Field(default=0.95, ge=0.0, le=1.0)


class RawGranularInference(BaseModel):
    claim: str
    inference_type: Literal[
        "affective_inference", "contextual_inference", "goal_inference"
    ] = "affective_inference"
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_quote: str
    source_fragment_ids: list[str] = Field(min_length=1)


class ExtractedEpisodePayload(BaseModel):
    dense_summary: str
    explicit_facts: list[RawFactClaim] = Field(default_factory=list)
    system_inferences: list[RawGranularInference] = Field(default_factory=list)


class EpisodicMemoryEngine:
    def __init__(self, db_pool: Any, embedder_service: Any):
        if db_pool is None or embedder_service is None:
            raise RuntimeError("db_pool and embedder_service are required")
        actual = (
            getattr(embedder_service, "model_name", None),
            getattr(embedder_service, "dimension", None),
            getattr(embedder_service, "model_version", None),
        )
        expected = (EMBEDDING_MODEL_NAME, EMBEDDING_DIMENSION, EMBEDDING_MODEL_VERSION)
        if actual != expected:
            raise RuntimeError(f"Embedder metadata mismatch: expected {expected}, got {actual}")

        self.db_pool = db_pool
        self.embedder_service = embedder_service
        self.qdrant = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY, timeout=10)
        self.minio = Minio(
            MINIO_ENDPOINT,
            access_key=MINIO_ACCESS_KEY,
            secret_key=MINIO_SECRET_KEY,
            secure=MINIO_SECURE,
        )
        self._test_hook_before_candidate_finalize = None
        self._test_hook_before_reembedding_finalize = None
        self._ensure_qdrant_schema()

    def _ensure_qdrant_schema(self) -> None:
        names = [c.name for c in self.qdrant.get_collections().collections]
        if EPISODIC_COLLECTION_NAME not in names:
            self.qdrant.create_collection(
                collection_name=EPISODIC_COLLECTION_NAME,
                vectors_config=VectorParams(size=EMBEDDING_DIMENSION, distance=Distance.COSINE),
            )
        info = self.qdrant.get_collection(EPISODIC_COLLECTION_NAME)
        indexed = set(info.payload_schema or {})
        for field, schema in (
            ("postgres_episode_id", PayloadSchemaType.KEYWORD),
            ("session_id", PayloadSchemaType.KEYWORD),
            ("review_status", PayloadSchemaType.KEYWORD),
            ("summary_sha256", PayloadSchemaType.KEYWORD),
            ("unix_timestamp", PayloadSchemaType.FLOAT),
        ):
            if field not in indexed:
                self.qdrant.create_payload_index(
                    collection_name=EPISODIC_COLLECTION_NAME,
                    field_name=field,
                    field_schema=schema,
                )

    def _enqueue_outbox(self, cursor: Any, event_type: str, payload: dict[str, Any]) -> None:
        now = datetime.now(timezone.utc)
        cursor.execute(
            """
            INSERT INTO operational_outbox
                (event_type, payload, status, retry_count, next_retry_at, created_at, updated_at)
            VALUES (%s, %s::jsonb, 'pending', 0, %s, %s, %s)
            """,
            (event_type, json.dumps(payload), now, now, now),
        )

    def _verify_minio_artifact(
        self,
        bucket: str,
        object_key: str,
        raw_transcript: str,
        expected_bytes: int,
        expected_sha256: str,
    ) -> None:
        raw = raw_transcript.encode("utf-8")
        if len(raw) != expected_bytes:
            raise ValueError("Transcript byte length mismatch")
        if calculate_sha256(raw_transcript) != expected_sha256:
            raise ValueError("Transcript SHA-256 mismatch")
        response = None
        try:
            stat = self.minio.stat_object(bucket, object_key)
            if stat.size != expected_bytes:
                raise ValueError("MinIO object size mismatch")
            stored = stat.metadata.get("x-amz-meta-sha256") or stat.metadata.get("sha256")
            if not stored:
                response = self.minio.get_object(bucket, object_key)
                stored = hashlib.sha256(response.read()).hexdigest()
            if stored != expected_sha256:
                raise ValueError("MinIO object SHA-256 mismatch")
        finally:
            if response is not None:
                response.close()
                response.release_conn()

    def record_episode(
        self,
        session_id: str,
        raw_transcript: str,
        extracted_data: ExtractedEpisodePayload,
        minio_bucket: str,
        minio_object_key: str,
        minio_sha256: str,
        minio_byte_length: int,
        source_fragment_ids: list[str],
        extraction_model: str,
    ) -> dict[str, Any]:
        if not source_fragment_ids:
            raise ValueError("source_fragment_ids cannot be empty")
        self._verify_minio_artifact(
            minio_bucket,
            minio_object_key,
            raw_transcript,
            minio_byte_length,
            minio_sha256,
        )

        conn = self.db_pool.getconn()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT fragment_id::text, COALESCE(user_text, ''), COALESCE(cole_response, '')
                    FROM event_fragments
                    WHERE fragment_id = ANY(%s::uuid[]) AND session_id = %s
                    """,
                    (source_fragment_ids, session_id),
                )
                rows = cursor.fetchall()
                if len(rows) != len(source_fragment_ids):
                    raise ValueError("Source fragment mismatch or cross-session reference")
                user_map = {r[0]: normalize_text(r[1]) for r in rows}
                cole_map = {r[0]: normalize_text(r[2]) for r in rows}

                facts: list[dict[str, Any]] = []
                for fact in extracted_data.explicit_facts:
                    unique = len(fact.source_fragment_ids) == len(set(fact.source_fragment_ids))
                    subset = set(fact.source_fragment_ids).issubset(user_map)
                    quote = normalize_text(fact.evidence_quote)
                    supported = any(quote in user_map[fid] for fid in fact.source_fragment_ids)
                    facts.append(
                        {
                            "claim_id": generate_claim_id("fact", fact.claim, fact.source_fragment_ids),
                            "claim": fact.claim,
                            "evidence_quote": fact.evidence_quote,
                            "source_fragment_ids": fact.source_fragment_ids,
                            "review_status": "unverified" if unique and subset and supported else "rejected",
                        }
                    )

                inferences: list[dict[str, Any]] = []
                for inf in extracted_data.system_inferences:
                    quote = normalize_text(inf.evidence_quote)
                    in_user = any(quote in user_map[fid] for fid in inf.source_fragment_ids)
                    in_cole = any(quote in cole_map[fid] for fid in inf.source_fragment_ids)
                    speaker = "mixed" if in_user and in_cole else "user" if in_user else "cole" if in_cole else "unknown"
                    inferences.append(
                        {
                            "claim_id": generate_claim_id("inference", inf.claim, inf.source_fragment_ids),
                            "claim": inf.claim,
                            "inference_type": inf.inference_type,
                            "confidence": inf.confidence,
                            "evidence_quote": inf.evidence_quote,
                            "evidence_speaker": speaker,
                            "source_fragment_ids": inf.source_fragment_ids,
                            "review_status": "unverified" if (in_user or in_cole) else "rejected",
                        }
                    )

                summary_sha = calculate_sha256(extracted_data.dense_summary)
                idempotency_key = calculate_sha256(
                    f"{session_id}:{','.join(source_fragment_ids)}:{EPISODE_SCHEMA_VERSION}:"
                    f"{EXTRACTION_PROMPT_VERSION}:{extraction_model}:{EMBEDDING_MODEL_VERSION}"
                )
                now = datetime.now(timezone.utc)
                cursor.execute(
                    """
                    INSERT INTO episodic_memories (
                        session_id, idempotency_key, minio_bucket, minio_object_key,
                        minio_sha256, minio_byte_length, dense_summary, summary_sha256,
                        explicit_facts, system_inferences, review_status, lifecycle_state,
                        index_status, index_sync_status, candidate_attempts,
                        episode_schema_version, extraction_prompt_version, extraction_model,
                        architecture_version, embedding_model, episode_started_at,
                        episode_ended_at, consolidated_at, last_ingestion_attempt_at
                    ) VALUES (
                        %s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,
                        'unverified','consolidated','index_pending','sync_pending',0,
                        %s,%s,%s,%s,%s,%s,%s,%s,%s
                    )
                    ON CONFLICT (idempotency_key)
                    DO UPDATE SET last_ingestion_attempt_at = EXCLUDED.last_ingestion_attempt_at
                    RETURNING id::text, dense_summary, summary_sha256, review_status,
                              index_status, index_sync_status, (xmax = 0) AS inserted
                    """,
                    (
                        session_id,
                        idempotency_key,
                        minio_bucket,
                        minio_object_key,
                        minio_sha256,
                        minio_byte_length,
                        extracted_data.dense_summary,
                        summary_sha,
                        json.dumps(facts),
                        json.dumps(inferences),
                        EPISODE_SCHEMA_VERSION,
                        EXTRACTION_PROMPT_VERSION,
                        extraction_model,
                        ARCHITECTURE_VERSION,
                        EMBEDDING_MODEL_VERSION,
                        now,
                        now,
                        now,
                        now,
                    ),
                )
                episode_id, auth_summary, auth_sha, review_status, index_status, sync_status, inserted = cursor.fetchone()
                if inserted:
                    for order, fragment_id in enumerate(source_fragment_ids):
                        cursor.execute(
                            """
                            INSERT INTO episode_fragment_sources
                                (episode_id, fragment_id, source_order)
                            VALUES (%s::uuid, %s::uuid, %s)
                            """,
                            (episode_id, fragment_id, order),
                        )
                conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self.db_pool.putconn(conn)

        if not inserted and index_status == "indexed" and sync_status == "synced":
            return {"episode_id": episode_id, "index_status": "indexed_skipped_idempotent"}

        token = str(uuid.uuid4())
        claim = self._claim_candidate_indexing(episode_id, auth_sha, token)
        if not claim["claimed"]:
            return {"episode_id": episode_id, "index_status": "claim_failed", "detail": claim["reason"]}

        try:
            vector = self.embedder_service.embed_text(auth_summary)
            validate_vector(vector)
            point_id = generate_versioned_point_id(
                episode_id, auth_sha, EMBEDDING_MODEL_VERSION, "candidate", token
            )
            payload = self._payload(episode_id, session_id, auth_summary, auth_sha, review_status)
            self.qdrant.upsert(
                collection_name=EPISODIC_COLLECTION_NAME,
                points=[PointStruct(id=point_id, vector=vector, payload=payload)],
            )
            if self._test_hook_before_candidate_finalize:
                self._test_hook_before_candidate_finalize(episode_id, point_id)
            result = self._finalize_candidate_index(episode_id, auth_sha, token, point_id)
            return {
                "episode_id": episode_id,
                "index_status": "indexed" if result["finalized"] else "candidate_index_written_state_transition_pending",
                "active_qdrant_point_id": point_id if result["finalized"] else None,
                "detail": result["reason"],
            }
        except Exception as exc:
            try:
                self._handle_candidate_index_failure(episode_id, token, str(exc))
            except Exception:
                logger.exception("Could not persist candidate failure for %s", episode_id)
            raise RuntimeError(f"Candidate indexing failed: {exc}") from exc

    def _payload(self, episode_id: str, session_id: str, summary: str, summary_sha: str, review_status: str) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        return {
            "postgres_episode_id": episode_id,
            "session_id": session_id,
            "iso_timestamp": now.isoformat(),
            "unix_timestamp": now.timestamp(),
            "episode_summary": summary,
            "summary_sha256": summary_sha,
            "embedding_source_sha256": summary_sha,
            "review_status": review_status,
            "lifecycle_state": "consolidated",
            "embedding_model": EMBEDDING_MODEL_VERSION,
            "episode_schema_version": EPISODE_SCHEMA_VERSION,
        }

    def _claim_candidate_indexing(self, episode_id: str, summary_sha: str, token: str) -> dict[str, Any]:
        conn = self.db_pool.getconn()
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(minutes=WORKER_LEASE_MINUTES)
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE episodic_memories
                    SET index_status='indexing_in_progress', index_sync_status='sync_in_progress',
                        candidate_claim_token=%s::uuid, candidate_claimed_at=NOW(),
                        candidate_summary_sha256=%s, candidate_attempts=candidate_attempts+1
                    WHERE id=%s::uuid AND review_status='unverified' AND summary_sha256=%s
                      AND candidate_attempts < %s
                      AND (index_status IN ('index_pending','index_failed')
                           OR (index_status='indexing_in_progress' AND candidate_claimed_at < %s))
                    RETURNING id
                    """,
                    (token, summary_sha, episode_id, summary_sha, MAX_CANDIDATE_ATTEMPTS, cutoff),
                )
                claimed = cursor.fetchone() is not None
                if claimed:
                    conn.commit()
                    return {"claimed": True, "reason": None}
                conn.rollback()
                return {"claimed": False, "reason": "state shifted, exhausted, or lease active"}
        except Exception as exc:
            conn.rollback()
            return {"claimed": False, "reason": str(exc)}
        finally:
            self.db_pool.putconn(conn)

    def _finalize_candidate_index(self, episode_id: str, summary_sha: str, token: str, point_id: str) -> dict[str, Any]:
        conn = self.db_pool.getconn()
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT active_qdrant_point_id FROM episodic_memories WHERE id=%s::uuid FOR UPDATE", (episode_id,))
                row = cursor.fetchone()
                old_point = str(row[0]) if row and row[0] else None
                cursor.execute(
                    """
                    UPDATE episodic_memories
                    SET embedding_source_sha256=%s, active_qdrant_point_id=%s::uuid,
                        index_status='indexed', index_sync_status='synced',
                        candidate_claim_token=NULL, candidate_claimed_at=NULL,
                        last_index_error=NULL, last_index_sync_at=NOW()
                    WHERE id=%s::uuid AND review_status='unverified' AND summary_sha256=%s
                      AND index_status='indexing_in_progress' AND index_sync_status='sync_in_progress'
                      AND candidate_claim_token=%s::uuid
                    RETURNING id
                    """,
                    (summary_sha, point_id, episode_id, summary_sha, token),
                )
                if cursor.fetchone() is None:
                    self._enqueue_outbox(cursor, "PRUNE_OBSOLETE_QDRANT_POINT", {
                        "episode_id": episode_id,
                        "point_id": point_id,
                        "reason": "candidate_activation_cas_failed",
                    })
                    conn.commit()
                    return {"finalized": False, "reason": "state_shifted_concurrently"}
                if old_point and old_point != point_id:
                    self._enqueue_outbox(cursor, "PRUNE_OBSOLETE_QDRANT_POINT", {
                        "episode_id": episode_id,
                        "point_id": old_point,
                        "reason": "candidate_reindexed",
                    })
                conn.commit()
                return {"finalized": True, "reason": None}
        except Exception:
            conn.rollback()
            raise
        finally:
            self.db_pool.putconn(conn)

    def _handle_candidate_index_failure(self, episode_id: str, token: str, error_msg: str) -> None:
        conn = self.db_pool.getconn()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE episodic_memories
                    SET index_status=CASE WHEN candidate_attempts >= %s THEN 'candidate_index_exhausted' ELSE 'index_failed' END,
                        index_sync_status='sync_failed', last_index_error=%s,
                        candidate_claim_token=NULL, candidate_claimed_at=NULL
                    WHERE id=%s::uuid AND review_status='unverified'
                      AND index_status='indexing_in_progress' AND index_sync_status='sync_in_progress'
                      AND candidate_claim_token=%s::uuid
                    RETURNING candidate_attempts, index_status
                    """,
                    (MAX_CANDIDATE_ATTEMPTS, error_msg, episode_id, token),
                )
                row = cursor.fetchone()
                if row and row[1] == "candidate_index_exhausted":
                    self._enqueue_outbox(cursor, "CANDIDATE_INDEXING_EXHAUSTED_ALERT", {
                        "episode_id": episode_id,
                        "attempts": row[0],
                        "error": error_msg,
                    })
                conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self.db_pool.putconn(conn)

    def promote_to_verified(
        self,
        episode_id: str,
        verified_claim_ids: list[str],
        provenance: ReviewerProvenance,
        custom_verified_summary: Optional[str] = None,
    ) -> dict[str, Any]:
        if not verified_claim_ids:
            return {"outcome": "verification_failed", "reason": "At least one claim is required"}
        conn = self.db_pool.getconn()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT explicit_facts, system_inferences FROM episodic_memories WHERE id=%s::uuid FOR UPDATE",
                    (episode_id,),
                )
                row = cursor.fetchone()
                if not row:
                    conn.rollback()
                    return {"outcome": "verification_failed", "reason": "Episode not found"}
                facts = row[0] if isinstance(row[0], list) else json.loads(row[0])
                inferences = row[1] if isinstance(row[1], list) else json.loads(row[1])
                eligible = {x["claim_id"] for x in facts + inferences if x.get("review_status") != "rejected"}
                if not set(verified_claim_ids).issubset(eligible):
                    conn.rollback()
                    return {"outcome": "verification_failed", "reason": "Unknown or rejected claim"}
                accepted = []
                for item in facts + inferences:
                    if item.get("review_status") == "rejected":
                        continue
                    if item["claim_id"] in verified_claim_ids:
                        item["review_status"] = "verified"
                        accepted.append(item["claim"])
                    else:
                        item["review_status"] = "rejected"
                summary = " ".join(accepted) if provenance.summary_method == "deterministic_concatenation" else custom_verified_summary
                if not summary:
                    conn.rollback()
                    return {"outcome": "verification_failed", "reason": "Verified summary required"}
                summary_sha = calculate_sha256(summary)
                cursor.execute(
                    """
                    UPDATE episodic_memories
                    SET explicit_facts=%s::jsonb, system_inferences=%s::jsonb,
                        dense_summary=%s, summary_sha256=%s, review_status='verified',
                        verified_at=NOW(), verified_by=%s, reviewer_type=%s,
                        review_method=%s, review_notes=%s, verification_version=%s,
                        verified_summary_method=%s, verified_summary_model=%s,
                        verified_summary_prompt_version=%s, verified_summary_claim_ids=%s::jsonb,
                        candidate_claim_token=NULL, candidate_claimed_at=NULL,
                        index_status=CASE WHEN active_qdrant_point_id IS NOT NULL THEN 'indexed' ELSE 'index_pending' END,
                        index_sync_status='reembedding_pending', reembedding_attempts=0,
                        lease_recovery_count=0, next_retry_at=NOW()
                    WHERE id=%s::uuid
                    """,
                    (
                        json.dumps(facts),
                        json.dumps(inferences),
                        summary,
                        summary_sha,
                        provenance.reviewer_id,
                        provenance.reviewer_type,
                        provenance.review_method,
                        provenance.review_notes,
                        provenance.verification_version,
                        provenance.summary_method,
                        provenance.summary_model,
                        provenance.summary_prompt_version,
                        json.dumps(verified_claim_ids),
                        episode_id,
                    ),
                )
                conn.commit()
                return {"outcome": "verification_succeeded_reembedding_pending", "episode_id": episode_id, "verified_summary_sha256": summary_sha}
        except Exception as exc:
            conn.rollback()
            return {"outcome": "verification_failed", "reason": str(exc)}
        finally:
            self.db_pool.putconn(conn)

    def process_reembedding_job(self, episode_id: str, worker_id: Optional[str] = None) -> dict[str, Any]:
        worker_id = worker_id or f"worker-{uuid.uuid4().hex[:8]}"
        token = str(uuid.uuid4())
        conn = self.db_pool.getconn()
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(minutes=WORKER_LEASE_MINUTES)
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE episodic_memories
                    SET index_sync_status='reembedding_in_progress', reembedding_claimed_at=NOW(),
                        reembedding_worker_id=%s, reembedding_claim_token=%s::uuid,
                        lease_recovery_count=CASE WHEN index_sync_status='reembedding_in_progress'
                            AND reembedding_claimed_at < %s THEN lease_recovery_count+1 ELSE lease_recovery_count END
                    WHERE id=%s::uuid AND review_status='verified'
                      AND (reembedding_attempts + lease_recovery_count) < %s
                      AND lease_recovery_count < %s
                      AND (index_sync_status='reembedding_pending'
                           OR (index_sync_status='reembedding_in_progress' AND reembedding_claimed_at < %s))
                      AND (next_retry_at IS NULL OR next_retry_at <= NOW())
                    RETURNING dense_summary, summary_sha256, session_id,
                              reembedding_attempts, lease_recovery_count
                    """,
                    (worker_id, token, cutoff, episode_id, MAX_TOTAL_REEMBEDDING_FAILURES, MAX_LEASE_RECOVERIES, cutoff),
                )
                row = cursor.fetchone()
                if not row:
                    conn.rollback()
                    return {"outcome": "reembedding_skipped", "reason": "unavailable, exhausted, or leased"}
                summary, summary_sha, session_id, attempts, recoveries = row
                conn.commit()
        finally:
            self.db_pool.putconn(conn)

        try:
            vector = self.embedder_service.embed_text(summary)
            validate_vector(vector)
            if calculate_sha256(summary) != summary_sha:
                raise ValueError("Embedding source hash mismatch")
            point_id = generate_versioned_point_id(episode_id, summary_sha, EMBEDDING_MODEL_VERSION, "verified", token)
            payload = self._payload(episode_id, session_id, summary, summary_sha, "verified")
            self.qdrant.upsert(collection_name=EPISODIC_COLLECTION_NAME, points=[PointStruct(id=point_id, vector=vector, payload=payload)])
            if self._test_hook_before_reembedding_finalize:
                self._test_hook_before_reembedding_finalize(episode_id, point_id)
            return self._finalize_reembedding(episode_id, summary_sha, token, point_id)
        except Exception as exc:
            self._record_reembedding_failure(episode_id, token, str(exc), attempts + 1, recoveries)
            return {"outcome": "reembedding_failed", "reason": str(exc)}

    def _finalize_reembedding(self, episode_id: str, summary_sha: str, token: str, point_id: str) -> dict[str, Any]:
        conn = self.db_pool.getconn()
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT active_qdrant_point_id FROM episodic_memories WHERE id=%s::uuid FOR UPDATE", (episode_id,))
                row = cursor.fetchone()
                old_point = str(row[0]) if row and row[0] else None
                cursor.execute(
                    """
                    UPDATE episodic_memories
                    SET embedding_source_sha256=%s, active_qdrant_point_id=%s::uuid,
                        index_status='indexed', index_sync_status='synced',
                        reembedding_claimed_at=NULL, reembedding_worker_id=NULL,
                        reembedding_claim_token=NULL, last_index_error=NULL, last_index_sync_at=NOW()
                    WHERE id=%s::uuid AND review_status='verified'
                      AND index_sync_status='reembedding_in_progress' AND summary_sha256=%s
                      AND reembedding_claim_token=%s::uuid
                    RETURNING id
                    """,
                    (summary_sha, point_id, episode_id, summary_sha, token),
                )
                if cursor.fetchone() is None:
                    self._enqueue_outbox(cursor, "PRUNE_OBSOLETE_QDRANT_POINT", {
                        "episode_id": episode_id,
                        "point_id": point_id,
                        "reason": "verified_activation_cas_failed",
                    })
                    conn.commit()
                    return {"outcome": "reembedding_aborted_cas_failed", "reason": "state shifted concurrently"}
                if old_point and old_point != point_id:
                    self._enqueue_outbox(cursor, "PRUNE_OBSOLETE_QDRANT_POINT", {
                        "episode_id": episode_id,
                        "point_id": old_point,
                        "reason": "reembedding_promoted",
                    })
                conn.commit()
                return {"outcome": "reembedding_succeeded_synced", "episode_id": episode_id, "active_qdrant_point_id": point_id}
        except Exception:
            conn.rollback()
            raise
        finally:
            self.db_pool.putconn(conn)

    def _record_reembedding_failure(self, episode_id: str, token: str, error_msg: str, attempts: int, recoveries: int) -> None:
        total = attempts + recoveries
        exhausted = total >= MAX_TOTAL_REEMBEDDING_FAILURES
        conn = self.db_pool.getconn()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE episodic_memories
                    SET index_sync_status=%s, reembedding_attempts=%s,
                        last_reembedding_error=%s, last_reembedding_at=NOW(),
                        next_retry_at=%s, reembedding_claimed_at=NULL,
                        reembedding_worker_id=NULL, reembedding_claim_token=NULL
                    WHERE id=%s::uuid AND reembedding_claim_token=%s::uuid
                    RETURNING id
                    """,
                    (
                        "reembedding_exhausted" if exhausted else "reembedding_pending",
                        attempts,
                        error_msg,
                        None if exhausted else datetime.now(timezone.utc) + timedelta(minutes=min(2 ** max(attempts - 1, 0), 360)),
                        episode_id,
                        token,
                    ),
                )
                if cursor.fetchone() and exhausted:
                    self._enqueue_outbox(cursor, "REEMBEDDING_EXHAUSTED_ALERT", {
                        "episode_id": episode_id,
                        "attempts": attempts,
                        "lease_recoveries": recoveries,
                        "error": error_msg,
                    })
                conn.commit()
        except Exception:
            conn.rollback()
            logger.exception("Could not persist re-embedding failure for %s", episode_id)
        finally:
            self.db_pool.putconn(conn)
