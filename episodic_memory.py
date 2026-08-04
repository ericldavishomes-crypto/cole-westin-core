import os
import json
import math
import uuid
import hashlib
import logging
import unicodedata
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional, Literal
from pydantic import BaseModel, Field

# Qdrant Client Imports
from qdrant_client import QdrantClient
from qdrant_client.models import (
    VectorParams, 
    Distance, 
    PointStruct, 
    PayloadSchemaType
)

# MinIO Client Import
from minio import Minio

logger = logging.getLogger(__name__)

# =====================================================================
# ⚙️ PRODUCTION CONFIGURATION & CONSTANTS
# =====================================================================
ENV_MODE = os.environ.get("ENV_MODE", "production")
if ENV_MODE == "production":
    QDRANT_URL = os.environ["QDRANT_URL"]
    MINIO_ENDPOINT = os.environ["MINIO_ENDPOINT"]
    MINIO_ACCESS_KEY = os.environ["MINIO_ACCESS_KEY"]
    MINIO_SECRET_KEY = os.environ["MINIO_SECRET_KEY"]
    MINIO_SECURE = os.environ.get("MINIO_SECURE", "true").lower() == "true"
else:
    QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
    MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "localhost:9000")
    MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
    MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minioadmin")
    MINIO_SECURE = os.getenv("MINIO_SECURE", "false").lower() == "true"

QDRANT_API_KEY = os.environ.get("QDRANT_API_KEY", None)
EPISODIC_COLLECTION_NAME = "cole_episodic_memory"
EMBEDDING_DIMENSION = 1536
EMBEDDING_MODEL_NAME = "text-embedding-3-small"
EMBEDDING_MODEL_VERSION = "text-embedding-3-small@v2026.1"  # Pinned deployment revision ID

MAX_TOTAL_REEMBEDDING_FAILURES = 5  # Unified budget (attempts + lease recoveries)
MAX_LEASE_RECOVERIES = 3
MAX_CANDIDATE_ATTEMPTS = 3
WORKER_LEASE_MINUTES = 15

# Global System Provenance Standards
EPISODE_SCHEMA_VERSION = "1.0.0"
EXTRACTION_PROMPT_VERSION = "1.0.0"
ARCHITECTURE_VERSION = "2.5.0-FINAL"

# Static namespace for versioned vector point generation
VECTOR_POINT_NAMESPACE = uuid.NAMESPACE_DNS

# =====================================================================
# 🛠️ HELPER FUNCTIONS & PYDANTIC SCHEMAS
# =====================================================================
def normalize_text(text: str) -> str:
    """Normalizes Unicode characters and whitespace for deterministic comparison."""
    text = unicodedata.normalize("NFKD", text)
    return " ".join(text.lower().split())

def calculate_sha256(content: str) -> str:
    """Computes SHA-256 hash of UTF-8 encoded string."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()

def generate_claim_id(claim_type: str, claim_text: str, source_fragment_ids: List[str]) -> str:
    """Generates a trusted, deterministic claim ID using raw source order."""
    ordered_sources = ",".join(source_fragment_ids)
    payload = f"{claim_type}:{normalize_text(claim_text)}:{ordered_sources}"
    return calculate_sha256(payload)

def generate_versioned_point_id(episode_id: str, summary_sha256: str, model_version: str) -> str:
    """Generates an immutable versioned Qdrant Point UUID based on summary and pinned model version."""
    unique_seed = f"{episode_id}:{summary_sha256}:{model_version}"
    return str(uuid.uuid5(VECTOR_POINT_NAMESPACE, unique_seed))

def validate_vector(vector: List[float], expected_dim: int = EMBEDDING_DIMENSION):
    """Validates vector length and checks for non-finite values (NaN or Inf)."""
    if len(vector) != expected_dim:
        raise ValueError(f"Vector size {len(vector)} does not match expected dimension {expected_dim}")
    for val in vector:
        if math.isnan(val) or math.isinf(val):
            raise ValueError("Vector contains non-finite values (NaN or Infinity).")

def calculate_exponential_backoff(attempts: int) -> datetime:
    """Calculates exponential backoff time (1m, 2m, 4m, 8m... max 6h)."""
    minutes = min(2 ** max(attempts - 1, 0), 360)
    return datetime.now(timezone.utc) + timedelta(minutes=minutes)

def is_valid_uuid(uuid_to_test: str) -> bool:
    """Safely validates if string is a valid UUID."""
    try:
        uuid_obj = uuid.UUID(uuid_to_test, version=4) if isinstance(uuid_to_test, str) else None
        return str(uuid_obj) == uuid_to_test if uuid_obj else False
    except (ValueError, AttributeError, TypeError):
        return False

class ReviewerProvenance(BaseModel):
    reviewer_type: Literal["human", "automated_rule", "model_assisted"] = "human"
    reviewer_id: str = Field(..., description="Unique ID of reviewer or service principal.")
    review_method: str = Field(..., description="Method or UI action used for verification.")
    verification_version: str = Field("1.0.0", description="Version of verification rules.")
    summary_method: Literal["deterministic_concatenation", "constrained_llm", "human_authored"] = "deterministic_concatenation"
    summary_model: Optional[str] = None
    summary_prompt_version: Optional[str] = None
    verified_summary_claim_ids: List[str] = Field(default_factory=list, description="Claim IDs supporting summary.")
    review_notes: Optional[str] = None

class RawFactClaim(BaseModel):
    claim: str = Field(..., description="Fact explicitly stated by user.")
    evidence_quote: str = Field(..., description="Direct quote from raw transcript.")
    source_fragment_ids: List[str] = Field(..., min_length=1)
    extraction_confidence: float = Field(0.95, ge=0.0, le=1.0)

class RawGranularInference(BaseModel):
    claim: str = Field(..., description="Inferred deduction about context/affective state.")
    inference_type: Literal["affective_inference", "contextual_inference", "goal_inference"] = "affective_inference"
    confidence: float = Field(..., ge=0.0, le=1.0)
    evidence_quote: str = Field(..., description="Direct quote supporting deduction.")
    source_fragment_ids: List[str] = Field(..., min_length=1)

class ExtractedEpisodePayload(BaseModel):
    dense_summary: str = Field(..., description="High-density summary of key events.")
    explicit_facts: List[RawFactClaim] = Field(default_factory=list)
    system_inferences: List[RawGranularInference] = Field(default_factory=list)


class EpisodicMemoryEngine:
    """
    Production Layer 2 Episodic Memory Coordinator for Cole (v2.5.0 Final).
    - Embedding failure boundary protection.
    - Candidate claim lease recovery & watchdog exhaustion.
    - Explicit pinned model version validation (`model_version`).
    - Active point UUID normalization during hydrated recall.
    - Clear candidate claims during promotion.
    """

    def __init__(self, db_pool, embedder_service):
        if db_pool is None:
            raise RuntimeError("PostgreSQL connection pool is strictly required.")
        if embedder_service is None:
            raise RuntimeError("Embedder service is strictly required for trusted vector generation.")

        embedder_model = getattr(embedder_service, "model_name", None)
        embedder_dim = getattr(embedder_service, "dimension", None)
        embedder_version = getattr(embedder_service, "model_version", None)
        
        if (
            embedder_model != EMBEDDING_MODEL_NAME 
            or embedder_dim != EMBEDDING_DIMENSION 
            or embedder_version != EMBEDDING_MODEL_VERSION
        ):
            raise RuntimeError(
                f"Embedder metadata mismatch! Expected model='{EMBEDDING_MODEL_NAME}', dim={EMBEDDING_DIMENSION}, "
                f"version='{EMBEDDING_MODEL_VERSION}'. Got model='{embedder_model}', dim={embedder_dim}, "
                f"version='{embedder_version}'."
            )

        self.db_pool = db_pool
        self.embedder_service = embedder_service
        self.qdrant = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY, timeout=10.0)
        self.minio = Minio(
            MINIO_ENDPOINT,
            access_key=MINIO_ACCESS_KEY,
            secret_key=MINIO_SECRET_KEY,
            secure=MINIO_SECURE
        )
        self._ensure_qdrant_schema_and_indexes()

    def _ensure_qdrant_schema_and_indexes(self):
        """Ensures Qdrant collection exists and matches required payload configuration."""
        collections = self.qdrant.get_collections().collections
        existing_names = [c.name for c in collections]

        if EPISODIC_COLLECTION_NAME not in existing_names:
            self.qdrant.create_collection(
                collection_name=EPISODIC_COLLECTION_NAME,
                vectors_config=VectorParams(size=EMBEDDING_DIMENSION, distance=Distance.COSINE)
            )

        info = self.qdrant.get_collection(collection_name=EPISODIC_COLLECTION_NAME)
        actual_size = info.config.params.vectors.size
        actual_distance = info.config.params.vectors.distance

        if actual_size != EMBEDDING_DIMENSION or actual_distance != Distance.COSINE:
            raise RuntimeError(
                f"Qdrant collection configuration error! Expected {EMBEDDING_DIMENSION}/COSINE, "
                f"found {actual_size}/{actual_distance}."
            )

        indexed_fields = info.payload_schema.keys() if info.payload_schema else []
        for field, p_type in [
            ("postgres_episode_id", PayloadSchemaType.KEYWORD),
            ("session_id", PayloadSchemaType.KEYWORD),
            ("review_status", PayloadSchemaType.KEYWORD),
            ("summary_sha256", PayloadSchemaType.KEYWORD),
            ("unix_timestamp", PayloadSchemaType.FLOAT)
        ]:
            if field not in indexed_fields:
                self.qdrant.create_payload_index(EPISODIC_COLLECTION_NAME, field, p_type)

    def _verify_minio_artifact(self, bucket: str, object_key: str, raw_transcript: str, expected_bytes: int, expected_sha256: str):
        """Validates transcript byte integrity against expected metadata and MinIO object storage."""
        local_bytes = raw_transcript.encode("utf-8")
        local_sha = calculate_sha256(raw_transcript)

        if len(local_bytes) != expected_bytes:
            raise ValueError(f"Local transcript byte length mismatch. Real: {len(local_bytes)}, Expected: {expected_bytes}")

        if local_sha.lower() != expected_sha256.lower():
            raise ValueError("Local transcript SHA-256 hash does not match expected caller input.")

        response = None
        try:
            stat = self.minio.stat_object(bucket, object_key)
            if stat.size != expected_bytes:
                raise ValueError(f"MinIO remote byte size mismatch. Stored: {stat.size}, Expected: {expected_bytes}")

            stored_sha = stat.metadata.get("x-amz-meta-sha256") or stat.metadata.get("sha256")

            if not stored_sha:
                response = self.minio.get_object(bucket, object_key)
                remote_content = response.read().decode("utf-8")
                stored_sha = calculate_sha256(remote_content)

            if stored_sha.lower() != local_sha.lower():
                raise ValueError("MinIO artifact SHA-256 hash mismatch.")

        except Exception as e:
            raise RuntimeError(f"MinIO artifact verification failed: {str(e)}")
        finally:
            if response:
                response.close()
                response.release_conn()

    # =================================================================
    # 📥 INGESTION PIPELINE (PROTECTED FAILURE BOUNDARY & CANDIDATE LEASES)
    # =================================================================
    def record_episode(
        self,
        session_id: str,
        raw_transcript: str,
        extracted_data: ExtractedEpisodePayload,
        minio_bucket: str,
        minio_object_key: str,
        minio_sha256: str,
        minio_byte_length: int,
        source_fragment_ids: List[str],
        extraction_model: str
    ) -> Dict[str, Any]:
        """
        Executes ingestion workflow.
        Includes candidate lease acquisition, failure-protected embedding generation,
        and versioned Qdrant indexing.
        """
        if not source_fragment_ids:
            raise ValueError("source_fragment_ids list cannot be empty.")

        self._verify_minio_artifact(minio_bucket, minio_object_key, raw_transcript, minio_byte_length, minio_sha256)

        conn = None
        try:
            conn = self.db_pool.getconn()
            with conn.cursor() as cursor:
                cursor.execute(
                    """SELECT fragment_id::text, COALESCE(user_text, ''), COALESCE(cole_response, '') 
                       FROM event_fragments 
                       WHERE fragment_id = ANY(%s::uuid[]) AND session_id = %s;""",
                    (source_fragment_ids, session_id)
                )
                frag_rows = cursor.fetchall()
                if len(frag_rows) != len(source_fragment_ids):
                    raise ValueError("Source fragment mismatch: Fragment list contains invalid IDs or cross-session items.")

                user_fragment_map = {row[0]: normalize_text(row[1]) for row in frag_rows}
                cole_fragment_map = {row[0]: normalize_text(row[2]) for row in frag_rows}

                processed_facts = []
                for fact in extracted_data.explicit_facts:
                    claim_id = generate_claim_id("fact", fact.claim, fact.source_fragment_ids)
                    norm_quote = normalize_text(fact.evidence_quote)
                    
                    cited_set = set(fact.source_fragment_ids)
                    is_valid_subset = len(fact.source_fragment_ids) == len(cited_set) and cited_set.issubset(user_fragment_map.keys())
                    
                    is_valid = is_valid_subset and any(
                        norm_quote in user_fragment_map[fid] for fid in fact.source_fragment_ids if fid in user_fragment_map
                    )
                    status = "unverified" if is_valid else "rejected"
                    
                    processed_facts.append({
                        "claim_id": claim_id,
                        "claim": fact.claim,
                        "evidence_quote": fact.evidence_quote,
                        "evidence_speaker": "user",
                        "source_fragment_ids": fact.source_fragment_ids,
                        "extraction_confidence": fact.extraction_confidence,
                        "review_status": status
                    })

                processed_inferences = []
                for inf in extracted_data.system_inferences:
                    claim_id = generate_claim_id("inference", inf.claim, inf.source_fragment_ids)
                    norm_quote = normalize_text(inf.evidence_quote)
                    
                    cited_set = set(inf.source_fragment_ids)
                    is_valid_subset = len(inf.source_fragment_ids) == len(cited_set) and cited_set.issubset(user_fragment_map.keys())

                    found_in_user = any(norm_quote in user_fragment_map[fid] for fid in inf.source_fragment_ids if fid in user_fragment_map)
                    found_in_cole = any(norm_quote in cole_fragment_map[fid] for fid in inf.source_fragment_ids if fid in cole_fragment_map)

                    if found_in_user and found_in_cole:
                        speaker_tag = "mixed"
                    elif found_in_user:
                        speaker_tag = "user"
                    elif found_in_cole:
                        speaker_tag = "cole"
                    else:
                        speaker_tag = "unknown"

                    is_valid = is_valid_subset and (found_in_user or found_in_cole)
                    status = "unverified" if is_valid else "rejected"

                    processed_inferences.append({
                        "claim_id": claim_id,
                        "claim": inf.claim,
                        "inference_type": inf.inference_type,
                        "confidence": inf.confidence,
                        "evidence_quote": inf.evidence_quote,
                        "evidence_speaker": speaker_tag,
                        "source_fragment_ids": inf.source_fragment_ids,
                        "review_status": status
                    })

                idempotency_payload = (
                    f"{session_id}:{','.join(source_fragment_ids)}:{EPISODE_SCHEMA_VERSION}:"
                    f"{EXTRACTION_PROMPT_VERSION}:{extraction_model}:{EMBEDDING_MODEL_VERSION}"
                )
                idempotency_key = calculate_sha256(idempotency_payload)
                summary_sha256 = calculate_sha256(extracted_data.dense_summary)
                consolidated_at_iso = datetime.now(timezone.utc).isoformat()

                cursor.execute(
                    "SELECT MIN(occurred_at), MAX(occurred_at) FROM event_fragments WHERE fragment_id = ANY(%s::uuid[]);",
                    (source_fragment_ids,)
                )
                started_dt, ended_dt = cursor.fetchone()
                started_at_iso = started_dt.isoformat() if started_dt else consolidated_at_iso
                ended_at_iso = ended_dt.isoformat() if ended_dt else consolidated_at_iso

                upsert_query = """
                    INSERT INTO episodic_memories 
                    (session_id, idempotency_key, minio_bucket, minio_object_key, minio_sha256, minio_byte_length,
                     dense_summary, summary_sha256, embedding_source_sha256, explicit_facts, system_inferences, 
                     record_type, review_status, lifecycle_state, index_status, index_sync_status, 
                     candidate_attempts, episode_schema_version, extraction_prompt_version, extraction_model, 
                     architecture_version, embedding_model, episode_started_at, episode_ended_at, consolidated_at, last_ingestion_attempt_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NULL, %s, %s, %s, %s, %s, %s, %s, 0, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (idempotency_key) DO UPDATE SET last_ingestion_attempt_at = EXCLUDED.last_ingestion_attempt_at
                    RETURNING id, dense_summary, summary_sha256, review_status, index_status, index_sync_status, (xmax = 0) AS is_inserted;
                """
                cursor.execute(upsert_query, (
                    session_id, idempotency_key, minio_bucket, minio_object_key, minio_sha256, minio_byte_length,
                    extracted_data.dense_summary, summary_sha256,
                    json.dumps(processed_facts), json.dumps(processed_inferences),
                    "candidate_episode", "unverified", "consolidated", "index_pending", "sync_pending",
                    EPISODE_SCHEMA_VERSION, EXTRACTION_PROMPT_VERSION, extraction_model, ARCHITECTURE_VERSION,
                    EMBEDDING_MODEL_VERSION, started_at_iso, ended_at_iso, consolidated_at_iso, consolidated_at_iso
                ))

                row = cursor.fetchone()
                episode_uuid = str(row[0])
                auth_summary = row[1]
                auth_summary_sha = row[2]
                auth_review_status = row[3]
                auth_index_status = row[4]
                auth_sync_status = row[5]
                is_inserted = row[6]

                if is_inserted:
                    lineage_query = """
                        INSERT INTO episode_fragment_sources (episode_id, fragment_id, source_order, created_at)
                        VALUES (%s::uuid, %s::uuid, %s, %s);
                    """
                    for idx, frag_id in enumerate(source_fragment_ids):
                        cursor.execute(lineage_query, (episode_uuid, frag_id, idx, consolidated_at_iso))

                conn.commit()
        except Exception as e:
            if conn:
                conn.rollback()
            raise RuntimeError(f"PostgreSQL episode insert failed: {str(e)}")
        finally:
            if conn:
                self.db_pool.putconn(conn)
                conn = None

        if not is_inserted and auth_index_status == "indexed" and auth_sync_status == "synced":
            return {
                "episode_id": episode_uuid,
                "status": auth_review_status,
                "is_inserted": False,
                "index_status": "indexed_skipped_idempotent"
            }

        # Candidate Acquisition using lease recovery logic
        candidate_token = str(uuid.uuid4())
        claim_res = self._claim_candidate_indexing(episode_uuid, auth_summary_sha, candidate_token)
        if not claim_res["claimed"]:
            return {
                "episode_id": episode_uuid,
                "status": auth_review_status,
                "is_inserted": is_inserted,
                "index_status": "claim_failed",
                "detail": claim_res["reason"]
            }

        # PROTECTED EMBEDDING AND INDEXING BOUNDARY
        try:
            vector_to_use = self.embedder_service.embed_text(auth_summary)
            validate_vector(vector_to_use)

            versioned_point_id = generate_versioned_point_id(episode_uuid, auth_summary_sha, EMBEDDING_MODEL_VERSION)

            qdrant_payload = {
                "postgres_episode_id": episode_uuid,
                "session_id": session_id,
                "iso_timestamp": consolidated_at_iso,
                "unix_timestamp": datetime.now(timezone.utc).timestamp(),
                "episode_summary": auth_summary,
                "summary_sha256": auth_summary_sha,
                "embedding_source_sha256": auth_summary_sha,
                "review_status": auth_review_status,
                "lifecycle_state": "consolidated",
                "embedding_model": EMBEDDING_MODEL_VERSION,
                "episode_schema_version": EPISODE_SCHEMA_VERSION
            }

            self.qdrant.upsert(
                collection_name=EPISODIC_COLLECTION_NAME,
                points=[PointStruct(id=versioned_point_id, vector=vector_to_use, payload=qdrant_payload)]
            )
            
            finalization = self._finalize_candidate_index(episode_uuid, auth_summary_sha, candidate_token, versioned_point_id)
            reported_index_status = "indexed" if finalization["finalized"] else "candidate_index_written_state_transition_pending"

            return {
                "episode_id": episode_uuid,
                "status": auth_review_status,
                "is_inserted": is_inserted,
                "index_status": reported_index_status,
                "active_qdrant_point_id": versioned_point_id if finalization["finalized"] else None,
                "detail": finalization["reason"]
            }

        except Exception as err:
            self._handle_candidate_index_failure(episode_uuid, candidate_token, str(err))
            raise RuntimeError(f"Candidate indexing failed: {str(err)}") from err

    def _claim_candidate_indexing(self, episode_uuid: str, summary_sha: str, candidate_token: str) -> Dict[str, Any]:
        """Claims/reclaims candidate indexing with candidate lease recovery."""
        conn = None
        now_dt = datetime.now(timezone.utc)
        now_iso = now_dt.isoformat()
        lease_cutoff = (now_dt - timedelta(minutes=WORKER_LEASE_MINUTES)).isoformat()

        try:
            conn = self.db_pool.getconn()
            with conn.cursor() as cursor:
                cursor.execute("""
                    UPDATE episodic_memories
                    SET index_status = 'indexing_in_progress',
                        index_sync_status = 'sync_in_progress',
                        candidate_claim_token = %s,
                        candidate_claimed_at = %s,
                        candidate_summary_sha256 = %s,
                        candidate_attempts = candidate_attempts + 1
                    WHERE id = %s::uuid
                      AND review_status = 'unverified'
                      AND summary_sha256 = %s
                      AND candidate_attempts < %s
                      AND (
                          index_status IN ('index_pending', 'index_failed')
                          OR (index_status = 'indexing_in_progress' AND candidate_claimed_at < %s)
                      )
                    RETURNING id;
                """, (candidate_token, now_iso, summary_sha, episode_uuid, summary_sha, MAX_CANDIDATE_ATTEMPTS, lease_cutoff))

                row = cursor.fetchone()
                if not row:
                    conn.rollback()
                    return {"claimed": False, "reason": "Candidate episode state shifted, max attempts reached, or lease actively held."}

                conn.commit()
                return {"claimed": True, "reason": None}
        except Exception as e:
            if conn:
                conn.rollback()
            return {"claimed": False, "reason": str(e)}
        finally:
            if conn:
                self.db_pool.putconn(conn)
                conn = None

    def _finalize_candidate_index(self, episode_uuid: str, summary_sha: str, candidate_token: str, point_id: str) -> Dict[str, Any]:
        """
        CAS update for candidate completion using candidate_claim_token.
        Emits PRUNE_OBSOLETE_QDRANT_POINT outbox event if an active point ID is replaced.
        """
        conn = None
        now_iso = datetime.now(timezone.utc).isoformat()
        try:
            conn = self.db_pool.getconn()
            with conn.cursor() as cursor:
                cursor.execute("SELECT active_qdrant_point_id FROM episodic_memories WHERE id = %s::uuid FOR UPDATE;", (episode_uuid,))
                old_row = cursor.fetchone()
                old_point_id_to_delete = old_row[0] if old_row and old_row[0] and old_row[0] != point_id else None

                cursor.execute("""
                    UPDATE episodic_memories 
                    SET embedding_source_sha256 = %s, 
                        active_qdrant_point_id = %s,
                        index_status = 'indexed', 
                        index_sync_status = 'synced',
                        last_index_error = NULL, 
                        last_index_sync_at = %s,
                        candidate_claim_token = NULL,
                        candidate_claimed_at = NULL
                    WHERE id = %s::uuid 
                      AND review_status = 'unverified' 
                      AND summary_sha256 = %s 
                      AND index_status = 'indexing_in_progress'
                      AND index_sync_status = 'sync_in_progress'
                      AND candidate_claim_token = %s
                    RETURNING id;
                """, (summary_sha, point_id, now_iso, episode_uuid, summary_sha, candidate_token))
                
                row = cursor.fetchone()
                if not row:
                    conn.rollback()
                    logger.info(f"Candidate indexing finalization skipped for {episode_uuid}: State shifted concurrently.")
                    return {"finalized": False, "reason": "state_shifted_concurrently"}

                if old_point_id_to_delete:
                    prune_payload = json.dumps({"point_id": old_point_id_to_delete, "episode_id": episode_uuid, "reason": "candidate_reindexed"})
                    cursor.execute("""
                        INSERT INTO operational_outbox (event_type, payload, created_at)
                        VALUES ('PRUNE_OBSOLETE_QDRANT_POINT', %s, %s);
                    """, (prune_payload, now_iso))

                conn.commit()
                return {"finalized": True, "reason": None}
        except Exception:
            if conn:
                conn.rollback()
            raise
        finally:
            if conn:
                self.db_pool.putconn(conn)
                conn = None

    def _handle_candidate_index_failure(self, episode_uuid: str, candidate_token: str, error_msg: str):
        """Token-guarded candidate failure recording using candidate_claim_token."""
        conn = None
        now_iso = datetime.now(timezone.utc).isoformat()
        try:
            conn = self.db_pool.getconn()
            with conn.cursor() as cursor:
                cursor.execute("""
                    UPDATE episodic_memories 
                    SET index_status = 'index_failed', 
                        index_sync_status = 'sync_failed', 
                        last_index_error = %s, 
                        last_index_sync_at = %s,
                        candidate_claim_token = NULL,
                        candidate_claimed_at = NULL
                    WHERE id = %s::uuid 
                      AND review_status = 'unverified'
                      AND index_status = 'indexing_in_progress'
                      AND index_sync_status = 'sync_in_progress'
                      AND candidate_claim_token = %s
                    RETURNING id;
                """, (error_msg, now_iso, episode_uuid, candidate_token))
                
                row = cursor.fetchone()
                if not row:
                    conn.rollback()
                    logger.info(f"Candidate failure recording skipped for {episode_uuid}: State transitioned concurrently.")
                    return
                conn.commit()
        except Exception:
            if conn:
                conn.rollback()
            raise
        finally:
            if conn:
                self.db_pool.putconn(conn)
                conn = None

    # =================================================================
    # ⚖️ SAGA PROMOTION WORKFLOW (CLEARS CANDIDATE CLAIMS)
    # =================================================================
    def promote_to_verified(
        self, 
        episode_uuid: str, 
        verified_claim_ids: List[str], 
        provenance: ReviewerProvenance,
        custom_verified_summary: Optional[str] = None
    ) -> Dict[str, Any]:
        """Stage 1 of Saga: Clears active candidate claim telemetry upon promotion."""
        if not verified_claim_ids:
            return {"outcome": "verification_failed", "reason": "At least one verified claim ID is strictly required."}

        if provenance.summary_method == "constrained_llm":
            if not provenance.summary_model or not provenance.summary_prompt_version:
                return {
                    "outcome": "verification_failed", 
                    "reason": "constrained_llm summary_method strictly requires summary_model and summary_prompt_version."
                }
        elif provenance.summary_method == "human_authored":
            if provenance.reviewer_type != "human":
                return {
                    "outcome": "verification_failed", 
                    "reason": "human_authored summary_method strictly requires reviewer_type == 'human'."
                }

        conn = None
        now_iso = datetime.now(timezone.utc).isoformat()
        try:
            conn = self.db_pool.getconn()
            with conn.cursor() as cursor:
                cursor.execute("SELECT explicit_facts, system_inferences FROM episodic_memories WHERE id = %s::uuid FOR UPDATE;", (episode_uuid,))
                row = cursor.fetchone()
                if not row:
                    conn.rollback()
                    return {"outcome": "verification_failed", "reason": "Episode ID not found."}

                facts = json.loads(row[0]) if isinstance(row[0], str) else row[0]
                inferences = json.loads(row[1]) if isinstance(row[1], str) else row[1]

                eligible_claim_ids = {
                    f["claim_id"] for f in facts if f.get("review_status") != "rejected"
                }.union({
                    inf["claim_id"] for inf in inferences if inf.get("review_status") != "rejected"
                })

                if not set(verified_claim_ids).issubset(eligible_claim_ids):
                    conn.rollback()
                    return {"outcome": "verification_failed", "reason": "Cannot verify claims that do not exist or were explicitly rejected."}

                accepted_fact_texts = []
                for f in facts:
                    if f.get("review_status") != "rejected":
                        if f["claim_id"] in verified_claim_ids:
                            f["review_status"] = "verified"
                            accepted_fact_texts.append(f["claim"])
                        else:
                            f["review_status"] = "rejected"

                accepted_inf_texts = []
                for inf in inferences:
                    if inf.get("review_status") != "rejected":
                        if inf["claim_id"] in verified_claim_ids:
                            inf["review_status"] = "verified"
                            accepted_inf_texts.append(inf["claim"])
                        else:
                            inf["review_status"] = "rejected"

                if provenance.summary_method == "deterministic_concatenation":
                    verified_summary = " ".join(accepted_fact_texts + accepted_inf_texts)
                    provenance.verified_summary_claim_ids = list(verified_claim_ids)
                elif custom_verified_summary:
                    if not provenance.verified_summary_claim_ids:
                        conn.rollback()
                        return {"outcome": "verification_failed", "reason": "Non-deterministic summary requires declared verified_summary_claim_ids."}
                    if set(provenance.verified_summary_claim_ids) != set(verified_claim_ids):
                        conn.rollback()
                        return {"outcome": "verification_failed", "reason": "verified_summary_claim_ids must match accepted claim IDs exactly."}
                    verified_summary = custom_verified_summary
                else:
                    conn.rollback()
                    return {"outcome": "verification_failed", "reason": "Custom verified summary string required for non-deterministic methods."}

                verified_summary_sha = calculate_sha256(verified_summary)

                cursor.execute("""
                    UPDATE episodic_memories 
                    SET explicit_facts = %s, system_inferences = %s, dense_summary = %s, summary_sha256 = %s,
                        review_status = 'verified', verified_at = %s, verified_by = %s, reviewer_type = %s,
                        review_method = %s, review_notes = %s, verification_version = %s,
                        verified_summary_method = %s, verified_summary_model = %s, verified_summary_prompt_version = %s,
                        verified_summary_claim_ids = %s,
                        candidate_claim_token = NULL, candidate_claimed_at = NULL,
                        index_sync_status = 'reembedding_pending', reembedding_attempts = 0, lease_recovery_count = 0, next_retry_at = %s
                    WHERE id = %s::uuid;
                """, (
                    json.dumps(facts), json.dumps(inferences), verified_summary, verified_summary_sha,
                    now_iso, provenance.reviewer_id, provenance.reviewer_type, provenance.review_method,
                    provenance.review_notes, provenance.verification_version,
                    provenance.summary_method, provenance.summary_model, provenance.summary_prompt_version,
                    json.dumps(provenance.verified_summary_claim_ids),
                    now_iso, episode_uuid
                ))
                conn.commit()

            return {
                "outcome": "verification_succeeded_reembedding_pending",
                "episode_id": episode_uuid,
                "verified_summary_sha256": verified_summary_sha
            }

        except Exception as e:
            if conn:
                conn.rollback()
            return {"outcome": "verification_failed", "reason": str(e)}
        finally:
            if conn:
                self.db_pool.putconn(conn)
                conn = None

    # =================================================================
    # 🔄 RE-EMBEDDING WORKFLOW
    # =================================================================
    def process_reembedding_job(self, episode_uuid: str, worker_id: Optional[str] = None) -> Dict[str, Any]:
        """Stage 2 of Saga (Trusted Worker Execution)."""
        if worker_id is None:
            worker_id = f"worker-{uuid.uuid4().hex[:8]}"

        claim_token = str(uuid.uuid4())
        state_conn = None
        finalize_conn = None
        now_dt = datetime.now(timezone.utc)
        now_iso = now_dt.isoformat()
        lease_cutoff = (now_dt - timedelta(minutes=WORKER_LEASE_MINUTES)).isoformat()

        try:
            state_conn = self.db_pool.getconn()
            with state_conn.cursor() as cursor:
                cursor.execute("""
                    UPDATE episodic_memories
                    SET index_sync_status = 'reembedding_in_progress',
                        reembedding_claimed_at = %s,
                        reembedding_worker_id = %s,
                        reembedding_claim_token = %s,
                        lease_recovery_count = CASE 
                            WHEN index_sync_status = 'reembedding_in_progress' AND reembedding_claimed_at < %s THEN lease_recovery_count + 1 
                            ELSE lease_recovery_count 
                        END
                    WHERE id = %s::uuid 
                      AND review_status = 'verified'
                      AND (reembedding_attempts + lease_recovery_count) < %s
                      AND lease_recovery_count < %s
                      AND (
                          index_sync_status = 'reembedding_pending'
                          OR (index_sync_status = 'reembedding_in_progress' AND reembedding_claimed_at < %s)
                      )
                      AND (next_retry_at IS NULL OR next_retry_at <= %s)
                    RETURNING dense_summary, summary_sha256, session_id, reembedding_attempts, lease_recovery_count;
                """, (
                    now_iso, worker_id, claim_token, lease_cutoff,
                    episode_uuid, MAX_TOTAL_REEMBEDDING_FAILURES, MAX_LEASE_RECOVERIES,
                    lease_cutoff, now_iso
                ))
                
                row = cursor.fetchone()
                if not row:
                    state_conn.rollback()
                    return {"outcome": "reembedding_skipped", "reason": "Episode unavailable, total failure budget exhausted, or active lease held."}

                claimed_summary, claimed_summary_sha, session_id, attempts, recoveries = row
                state_conn.commit()
        except Exception as e:
            if state_conn:
                state_conn.rollback()
            return {"outcome": "reembedding_failed", "reason": f"Database job acquisition failed: {str(e)}"}
        finally:
            if state_conn:
                self.db_pool.putconn(state_conn)
                state_conn = None

        try:
            generated_vector = self.embedder_service.embed_text(claimed_summary)
            validate_vector(generated_vector)
            calculated_source_sha = calculate_sha256(claimed_summary)

            if calculated_source_sha != claimed_summary_sha:
                raise ValueError("Calculated embedding text hash mismatch against claimed PostgreSQL summary_sha256.")

            versioned_point_id = generate_versioned_point_id(episode_uuid, claimed_summary_sha, EMBEDDING_MODEL_VERSION)

            qdrant_payload = {
                "postgres_episode_id": episode_uuid,
                "session_id": session_id,
                "iso_timestamp": now_iso,
                "unix_timestamp": now_dt.timestamp(),
                "episode_summary": claimed_summary,
                "summary_sha256": claimed_summary_sha,
                "embedding_source_sha256": calculated_source_sha,
                "review_status": "verified",
                "lifecycle_state": "consolidated",
                "embedding_model": EMBEDDING_MODEL_VERSION,
                "episode_schema_version": EPISODE_SCHEMA_VERSION
            }

            self.qdrant.upsert(
                collection_name=EPISODIC_COLLECTION_NAME,
                points=[PointStruct(id=versioned_point_id, vector=generated_vector, payload=qdrant_payload)]
            )

            finalize_conn = self.db_pool.getconn()
            with finalize_conn.cursor() as cursor:
                cursor.execute("SELECT active_qdrant_point_id FROM episodic_memories WHERE id = %s::uuid FOR UPDATE;", (episode_uuid,))
                old_row = cursor.fetchone()
                old_point_id_to_delete = old_row[0] if old_row and old_row[0] and old_row[0] != versioned_point_id else None

                cursor.execute("""
                    UPDATE episodic_memories 
                    SET embedding_source_sha256 = %s, 
                        active_qdrant_point_id = %s,
                        index_status = 'indexed', 
                        index_sync_status = 'synced',
                        last_index_sync_at = %s, 
                        last_index_error = NULL,
                        reembedding_claimed_at = NULL,
                        reembedding_worker_id = NULL,
                        reembedding_claim_token = NULL
                    WHERE id = %s::uuid 
                      AND index_sync_status = 'reembedding_in_progress'
                      AND summary_sha256 = %s
                      AND reembedding_claim_token = %s
                    RETURNING id;
                """, (calculated_source_sha, versioned_point_id, now_iso, episode_uuid, claimed_summary_sha, claim_token))
                
                cas_row = cursor.fetchone()
                if not cas_row:
                    finalize_conn.rollback()
                    logger.warning(f"CAS Guard Hit: Summary or lease token for episode {episode_uuid} changed during re-embedding. Aborting sync.")
                    return {
                        "outcome": "reembedding_aborted_cas_failed", 
                        "reason": "Summary changed or worker lease was reclaimed by another process during embedding generation."
                    }

                if old_point_id_to_delete:
                    prune_payload = json.dumps({"point_id": old_point_id_to_delete, "episode_id": episode_uuid, "reason": "reembedding_promoted"})
                    cursor.execute("""
                        INSERT INTO operational_outbox (event_type, payload, created_at)
                        VALUES ('PRUNE_OBSOLETE_QDRANT_POINT', %s, %s);
                    """, (prune_payload, now_iso))

                finalize_conn.commit()

            return {
                "outcome": "reembedding_succeeded_synced", 
                "episode_id": episode_uuid, 
                "active_qdrant_point_id": versioned_point_id
            }

        except Exception as err:
            self._record_reembedding_failure(episode_uuid, str(err), attempts + 1, recoveries, claim_token)
            return {"outcome": "reembedding_failed", "reason": str(err)}
        finally:
            if finalize_conn:
                self.db_pool.putconn(finalize_conn)
                finalize_conn = None

    def _record_reembedding_failure(self, episode_uuid: str, error_msg: str, attempts: int, recoveries: int, claim_token: str):
        """Token-gated failure recording using unified failure budget accounting."""
        conn = None
        now_iso = datetime.now(timezone.utc).isoformat()
        total_failures = attempts + recoveries
        is_exhausted = total_failures >= MAX_TOTAL_REEMBEDDING_FAILURES
        new_status = "reembedding_exhausted" if is_exhausted else "reembedding_pending"
        next_retry = calculate_exponential_backoff(attempts) if not is_exhausted else None
        next_retry_iso = next_retry.isoformat() if next_retry else None

        try:
            conn = self.db_pool.getconn()
            with conn.cursor() as cursor:
                cursor.execute("""
                    UPDATE episodic_memories 
                    SET index_sync_status = %s,
                        reembedding_attempts = %s,
                        last_reembedding_error = %s,
                        last_reembedding_at = %s,
                        next_retry_at = %s,
                        reembedding_claimed_at = NULL,
                        reembedding_worker_id = NULL,
                        reembedding_claim_token = NULL
                    WHERE id = %s::uuid
                      AND reembedding_claim_token = %s
                    RETURNING id;
                """, (new_status, attempts, error_msg, now_iso, next_retry_iso, episode_uuid, claim_token))

                updated_row = cursor.fetchone()
                if not updated_row:
                    conn.rollback()
                    logger.info(f"Stale worker failure update ignored for episode {episode_uuid}: Claim token no longer active.")
                    return

                if is_exhausted:
                    alert_payload = json.dumps({
                        "event": "reembedding_exhausted",
                        "episode_id": episode_uuid,
                        "total_failures": total_failures,
                        "attempts": attempts,
                        "lease_recoveries": recoveries,
                        "error": error_msg,
                        "timestamp": now_iso
                    })
                    cursor.execute("""
                        INSERT INTO operational_outbox (event_type, payload, created_at)
                        VALUES ('REEMBEDDING_EXHAUSTED_ALERT', %s, %s);
                    """, (alert_payload, now_iso))

                conn.commit()
        except Exception as db_err:
            if conn:
                conn.rollback()
            logger.critical(f"DURABLE ALERT: Failure recording failed for episode {episode_uuid}! Error: {str(db_err)}")
        finally:
            if conn:
                self.db_pool.putconn(conn)
                conn = None

    # =================================================================
    # 🧹 STALE LEASE & WATCHDOG SWEEP (CANDIDATE & RE-EMBEDDING)
    # =================================================================
    def sweep_exhausted_and_stale_leases(self) -> Dict[str, Any]:
        """
        Background Watchdog Pass:
        1. Sweeps abandoned candidate indexing jobs exceeding MAX_CANDIDATE_ATTEMPTS.
        2. Sweeps re-embedding leases exceeding total failure budget or max lease recoveries.
        """
        conn = None
        now_dt = datetime.now(timezone.utc)
        now_iso = now_dt.isoformat()
        lease_cutoff = (now_dt - timedelta(minutes=WORKER_LEASE_MINUTES)).isoformat()

        try:
            conn = self.db_pool.getconn()
            with conn.cursor() as cursor:
                # 1. Sweep Abandoned Candidates
                cursor.execute("""
                    UPDATE episodic_memories
                    SET index_status = 'candidate_index_exhausted',
                        index_sync_status = 'sync_failed',
                        last_index_error = 'Hard candidate limit exceeded: Abandoned candidate leases reached threshold.',
                        candidate_claimed_at = NULL,
                        candidate_claim_token = NULL
                    WHERE review_status = 'unverified'
                      AND index_status = 'indexing_in_progress'
                      AND candidate_claimed_at < %s
                      AND candidate_attempts >= %s
                    RETURNING id;
                """, (lease_cutoff, MAX_CANDIDATE_ATTEMPTS))

                exhausted_candidate_ids = [str(r[0]) for r in cursor.fetchall()]

                for ep_id in exhausted_candidate_ids:
                    alert_payload = json.dumps({
                        "event": "candidate_indexing_exhausted",
                        "episode_id": ep_id,
                        "error": "Hard candidate limit exceeded: Abandoned candidate leases reached threshold.",
                        "timestamp": now_iso
                    })
                    cursor.execute("""
                        INSERT INTO operational_outbox (event_type, payload, created_at)
                        VALUES ('CANDIDATE_INDEXING_EXHAUSTED_ALERT', %s, %s);
                    """, (alert_payload, now_iso))

                # 2. Sweep Abandoned Re-embeddings
                cursor.execute("""
                    UPDATE episodic_memories
                    SET index_sync_status = 'reembedding_exhausted',
                        last_reembedding_error = 'Hard crash limit exceeded: Max lease recoveries reached.',
                        reembedding_claimed_at = NULL,
                        reembedding_worker_id = NULL,
                        reembedding_claim_token = NULL
                    WHERE index_sync_status = 'reembedding_in_progress'
                      AND reembedding_claimed_at < %s
                      AND (
                          lease_recovery_count >= %s 
                          OR (reembedding_attempts + lease_recovery_count) >= %s
                      )
                    RETURNING id, lease_recovery_count, reembedding_attempts;
                """, (lease_cutoff, MAX_LEASE_RECOVERIES, MAX_TOTAL_REEMBEDDING_FAILURES))

                exhausted_reembed_rows = cursor.fetchall()
                exhausted_reembed_ids = []

                for row in exhausted_reembed_rows:
                    ep_id = str(row[0])
                    recoveries = row[1]
                    attempts = row[2]
                    exhausted_reembed_ids.append(ep_id)

                    alert_payload = json.dumps({
                        "event": "reembedding_lease_exhausted",
                        "episode_id": ep_id,
                        "lease_recovery_count": recoveries,
                        "reembedding_attempts": attempts,
                        "error": "Hard crash limit exceeded: Abandoned worker leases reached threshold.",
                        "timestamp": now_iso
                    })
                    cursor.execute("""
                        INSERT INTO operational_outbox (event_type, payload, created_at)
                        VALUES ('REEMBEDDING_EXHAUSTED_ALERT', %s, %s);
                    """, (alert_payload, now_iso))

                conn.commit()
                return {
                    "exhausted_candidate_count": len(exhausted_candidate_ids),
                    "exhausted_candidate_ids": exhausted_candidate_ids,
                    "exhausted_reembed_count": len(exhausted_reembed_ids),
                    "exhausted_reembed_ids": exhausted_reembed_ids
                }

        except Exception as e:
            if conn:
                conn.rollback()
            logger.error(f"Watchdog sweep failed: {str(e)}")
            return {"error": str(e)}
        finally:
            if conn:
                self.db_pool.putconn(conn)
                conn = None

    # =================================================================
    # 🔍 HYDRATED RECALL PIPELINE (NORMALIZED POINT ID & VALIDATED SCHEMAS)
    # =================================================================
    def query_episodes(
        self,
        query_embedding: List[float],
        limit: int = 3,
        score_threshold: float = 0.75,
        verified_only: bool = True
    ) -> List[Dict[str, Any]]:
        """Retrieves vector candidates and enforces active_qdrant_point_id invariant checks against PostgreSQL."""
        validate_vector(query_embedding)

        results = self.qdrant.query_points(
            collection_name=EPISODIC_COLLECTION_NAME,
            query=query_embedding,
            limit=limit * 5,
            score_threshold=score_threshold
        ).points

        if not results:
            return []

        # Validate and sanitize candidate UUIDs from Qdrant payloads
        postgres_ids = list({
            str(res.payload["postgres_episode_id"]) 
            for res in results 
            if res.payload and is_valid_uuid(str(res.payload.get("postgres_episode_id")))
        })
        
        point_scores = {str(res.id): float(res.score) for res in results}
        point_payloads = {str(res.id): res.payload for res in results}

        if not postgres_ids:
            return []

        conn = None
        hydrated_episodes = []
        try:
            conn = self.db_pool.getconn()
            with conn.cursor() as cursor:
                query = """
                    SELECT id, session_id, dense_summary, summary_sha256, embedding_source_sha256, 
                           explicit_facts, system_inferences, review_status, index_sync_status, 
                           consolidated_at, active_qdrant_point_id
                    FROM episodic_memories 
                    WHERE id = ANY(%s::uuid[]);
                """
                cursor.execute(query, (postgres_ids,))
                rows = cursor.fetchall()

                for row in rows:
                    ep_id_str = str(row[0])
                    sess_id = row[1]
                    pg_summary = row[2]
                    pg_sum_sha = row[3]
                    pg_emb_sha = row[4]
                    facts_json = row[5]
                    inf_json = row[6]
                    rev_status = row[7]
                    sync_status = row[8]
                    consolidated_at = row[9]
                    
                    # Explicit string normalization for active point UUID
                    active_point_id = str(row[10]) if row[10] is not None else None

                    if not active_point_id or active_point_id not in point_payloads:
                        continue

                    q_payload = point_payloads[active_point_id]

                    # Comprehensive Contract Verification against Qdrant Payload
                    if (
                        q_payload.get("postgres_episode_id") != ep_id_str or
                        q_payload.get("embedding_model") != EMBEDDING_MODEL_VERSION or
                        q_payload.get("review_status") != rev_status
                    ):
                        continue

                    if verified_only:
                        if rev_status != "verified" or sync_status != "synced" or pg_sum_sha != pg_emb_sha:
                            continue

                        if (
                            q_payload.get("summary_sha256") != pg_sum_sha or
                            q_payload.get("embedding_source_sha256") != pg_emb_sha or
                            q_payload.get("episode_summary") != pg_summary
                        ):
                            continue

                    facts = json.loads(facts_json) if isinstance(facts_json, str) else facts_json
                    inferences = json.loads(inf_json) if isinstance(inf_json, str) else inf_json

                    if verified_only:
                        out_facts = [f for f in facts if f.get("review_status") == "verified"]
                        out_inferences = [inf for inf in inferences if inf.get("review_status") == "verified"]
                    else:
                        out_facts = facts
                        out_inferences = inferences

                    hydrated_episodes.append({
                        "episode_id": ep_id_str,
                        "session_id": sess_id,
                        "dense_summary": pg_summary,
                        "explicit_facts": out_facts,
                        "system_inferences": out_inferences,
                        "review_status": rev_status,
                        "index_sync_status": sync_status,
                        "consolidated_at": consolidated_at,
                        "active_qdrant_point_id": active_point_id,
                        "similarity_score": point_scores.get(active_point_id, 0.0)
                    })
        except Exception:
            if conn:
                conn.rollback()
            raise
        finally:
            if conn:
                self.db_pool.putconn(conn)
                conn = None

        hydrated_episodes.sort(key=lambda x: x["similarity_score"], reverse=True)
        return hydrated_episodes[:limit]
