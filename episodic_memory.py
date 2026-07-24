import os
import uuid
import json
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

# Qdrant Client Imports
from qdrant_client import QdrantClient
from qdrant_client.models import (
    VectorParams, 
    Distance, 
    PointStruct, 
    Filter, 
    FieldCondition, 
    MatchValue,
    Range
)

# =====================================================================
# ⚙️ CONFIGURATION & INITIALIZATION
# =====================================================================
QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", 6333))
EPISODIC_COLLECTION_NAME = "cole_episodic_memory"
RAW_VAULT_DIR = os.getenv("RAW_VAULT_DIR", "./vault/transcripts")

# Ensure local immutable transcript vault directory exists
os.makedirs(RAW_VAULT_DIR, exist_ok=True)


class EpisodicMemoryEngine:
    """
    Production Episodic Memory Manager for Cole.
    Handles raw transcript archival, structured metadata storage, 
    and vector index management in Qdrant.
    """

    def __init__(self, vector_size: int = 1536):
        """
        Initialize connection to Qdrant and set up collection if missing.
        Default vector_size set to 1536 (standard for modern text embeddings).
        """
        self.client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)
        self.vector_size = vector_size
        self._ensure_collection_exists()

    def _ensure_collection_exists(self):
        """Ensures the episodic memory collection exists in Qdrant."""
        collections = self.client.get_collections().collections
        existing_names = [c.name for c in collections]

        if EPISODIC_COLLECTION_NAME not in existing_names:
            self.client.create_collection(
                collection_name=EPISODIC_COLLECTION_NAME,
                vectors_config=VectorParams(
                    size=self.vector_size, 
                    distance=Distance.COSINE
                )
            )
            print(f"✅ Created Qdrant collection: '{EPISODIC_COLLECTION_NAME}'")

    # =================================================================
    # 📥 INGESTION PIPELINE
    # =================================================================
    def record_episode(
        self,
        session_id: str,
        raw_transcript: str,
        dense_summary: str,
        summary_embedding: List[float],
        explicit_facts: List[str],
        system_inferences: List[str],
        confidence_score: float = 0.98
    ) -> str:
        """
        Processes and archives a single conversation episode into Cole's memory.
        """
        episode_uuid = str(uuid.uuid4())
        iso_timestamp = datetime.now(timezone.utc).isoformat()
        unix_timestamp = datetime.now(timezone.utc).timestamp()

        # 1. Save Immutable Raw Transcript to Vault
        file_name = f"{session_id}_{episode_uuid[:8]}.txt"
        vault_file_path = os.path.join(RAW_VAULT_DIR, file_name)
        
        with open(vault_file_path, "w", encoding="utf-8") as f:
            f.write(f"SESSION ID: {session_id}\n")
            f.write(f"TIMESTAMP: {iso_timestamp}\n")
            f.write(f"EPISODE ID: {episode_uuid}\n")
            f.write("=" * 50 + "\n\n")
            f.write(raw_transcript)

        # 2. Build Structural Metadata Payload
        payload = {
            "episode_id": episode_uuid,
            "session_id": session_id,
            "iso_timestamp": iso_timestamp,
            "unix_timestamp": unix_timestamp,
            "vault_file_path": vault_file_path,
            "episode_summary": dense_summary,
            "explicit_facts": explicit_facts,
            "system_inferences": system_inferences,
            "confidence_score": confidence_score,
            "review_status": "verified"
        }

        # 3. Store Vector Point in Qdrant
        point = PointStruct(
            id=episode_uuid,
            vector=summary_embedding,
            payload=payload
        )

        self.client.upsert(
            collection_name=EPISODIC_COLLECTION_NAME,
            points=[point]
        )

        print(f"🔒 Episode successfully recorded! ID: {episode_uuid}")
        return episode_uuid

    # =================================================================
    # 🔍 RECALL & QUERY PIPELINE
    # =================================================================
    def query_episodes(
        self,
        query_embedding: List[float],
        limit: int = 3,
        session_id_filter: Optional[str] = None,
        start_timestamp: Optional[float] = None,
        end_timestamp: Optional[float] = None
    ) -> List[Dict[str, Any]]:
        """
        Queries Cole's episodic memory by vector similarity with optional time/session filters.
        """
        filter_conditions = []

        if session_id_filter:
            filter_conditions.append(
                FieldCondition(
                    key="session_id",
                    match=MatchValue(value=session_id_filter)
                )
            )

        if start_timestamp or end_timestamp:
            filter_conditions.append(
                FieldCondition(
                    key="unix_timestamp",
                    range=Range(
                        gte=start_timestamp,
                        lte=end_timestamp
                    )
                )
            )

        query_filter = Filter(must=filter_conditions) if filter_conditions else None

        results = self.client.query_points(
            collection_name=EPISODIC_COLLECTION_NAME,
            query=query_embedding,
            query_filter=query_filter,
            limit=limit
        ).points

        episodes = []
        for res in results:
            item = res.payload
            item["similarity_score"] = res.score
            episodes.append(item)

        return episodes


# =====================================================================
# 🛠️ HELPER PROMPT GENERATOR FOR LLM EXTRACTION PASS
# =====================================================================
def build_extraction_prompt(raw_transcript: str) -> str:
    """
    Generates the system prompt needed to convert raw transcripts 
    into explicit facts, system inferences, and dense summaries.
    """
    return f"""You are Cole's Memory Processing Unit. Analyze the following conversation transcript and extract an episodic memory record in strict JSON format.

JSON Schema required:
{{
    "dense_summary": "A concise, high-density summary of the event.",
    "explicit_facts": ["List of facts explicitly stated by the user."],
    "system_inferences": ["List of high-confidence deductions about user context/emotional state."]
}}

TRANSCRIPT:
{raw_transcript}
"""
