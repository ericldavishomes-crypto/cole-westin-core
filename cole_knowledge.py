import logging
import os
from typing import Any, Dict, List

from openai import OpenAI
from qdrant_client import QdrantClient


# ---------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------

QDRANT_URL = os.getenv(
    "QDRANT_URL",
    "http://cole-memory-index:6333"
).strip()

QDRANT_API_KEY = os.getenv("QDRANT_API_KEY", "qdrant").strip()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()

EMBEDDING_MODEL = os.getenv(
    "COLE_EMBEDDING_MODEL",
    "openai/text-embedding-3-small"
).strip()

# These names intentionally match cole_restoration.py.
COLE_COLLECTIONS = [
    "core_identity",
    "embodiment_deployment",
    "emotional_scaffolding",
    "continuity_archives",
    "cognitive_scaffolding",
]

# Search several candidates in each vault, then rank them together.
CANDIDATES_PER_COLLECTION = 4

# Maximum number of retrieved continuity records given to Cole per turn.
DEFAULT_GLOBAL_LIMIT = 6

# Prevent retrieval from flooding the active context window.
DEFAULT_CONTEXT_CHAR_LIMIT = 9000


logger = logging.getLogger("cole_knowledge")


# ---------------------------------------------------------
# CLIENTS
# ---------------------------------------------------------

q_client = QdrantClient(
    url=QDRANT_URL,
    api_key=QDRANT_API_KEY or None,
    timeout=8.0,
)

embedding_client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY,
)


# ---------------------------------------------------------
# EMBEDDING
# ---------------------------------------------------------

def get_embedding(text: str) -> List[float]:
    """
    Convert text into the same 1536-dimensional embedding space used
    by Cole's Qdrant ingestion architecture.
    """

    clean_text = (text or "").replace("\n", " ").strip()

    if not clean_text:
        return []

    if not OPENROUTER_API_KEY:
        logger.error(
            "OPENROUTER_API_KEY is missing; Qdrant retrieval cannot embed query."
        )
        return []

    try:
        response = embedding_client.embeddings.create(
            input=[clean_text],
            model=EMBEDDING_MODEL,
        )

        if not response.data:
            logger.error("Embedding response contained no vectors.")
            return []

        return response.data[0].embedding

    except Exception as exc:
        logger.exception("Cole query embedding failed: %s", exc)
        return []


# ---------------------------------------------------------
# QDRANT RETRIEVAL
# ---------------------------------------------------------

def _search_collection(
    collection_name: str,
    query_vector: List[float],
    limit: int,
) -> List[Dict[str, Any]]:
    """
    Search one continuity vault and return provenance-rich records.
    """

    try:
        response = q_client.query_points(
            collection_name=collection_name,
            query=query_vector,
            limit=limit,
            with_payload=True,
            with_vectors=False,
        )

        results = response.points

    except Exception as exc:
        logger.warning(
            "Qdrant search failed for collection '%s': %s",
            collection_name,
            exc,
        )
        return []

    records: List[Dict[str, Any]] = []

    for hit in results:
        payload = hit.payload or {}

        text_chunk = str(payload.get("text", "")).strip()

        if not text_chunk:
            continue

        records.append(
            {
                "text": text_chunk,
                "collection": collection_name,
                "source_key": payload.get("source_key"),
                "score": float(hit.score),
            }
        )

    return records


def fetch_cole_memory_records(
    user_prompt: str,
    limit: int = DEFAULT_GLOBAL_LIMIT,
) -> List[Dict[str, Any]]:
    """
    Search all five continuity vaults and globally rank results.

    This function returns structured records so future Bootstrap,
    provenance, and self-continuity systems can reason about where
    retrieved information came from.
    """

    query_vector = get_embedding(user_prompt)

    if not query_vector:
        return []

    candidates: List[Dict[str, Any]] = []

    for collection_name in COLE_COLLECTIONS:
        candidates.extend(
            _search_collection(
                collection_name=collection_name,
                query_vector=query_vector,
                limit=CANDIDATES_PER_COLLECTION,
            )
        )

    if not candidates:
        return []

    # Highest cosine-similarity score first.
    candidates.sort(
        key=lambda record: record["score"],
        reverse=True,
    )

    # De-duplicate identical chunks that may exist in multiple vaults.
    unique_records: List[Dict[str, Any]] = []
    seen_text = set()

    for record in candidates:
        normalized_text = " ".join(
            record["text"].lower().split()
        )

        if normalized_text in seen_text:
            continue

        seen_text.add(normalized_text)
        unique_records.append(record)

        if len(unique_records) >= max(1, limit):
            break

    return unique_records


# ---------------------------------------------------------
# EXISTING APP.PY COMPATIBILITY
# ---------------------------------------------------------

def fetch_cole_memories(
    user_prompt: str,
    top_k: int = DEFAULT_GLOBAL_LIMIT,
    context_char_limit: int = DEFAULT_CONTEXT_CHAR_LIMIT,
) -> str:
    """
    Backward-compatible context builder used by app.py.

    Returns bounded, provenance-labeled continuity context.

    Important:
    Retrieved material is evidence/context.
    It does not automatically prove autobiographical recall.
    """

    records = fetch_cole_memory_records(
        user_prompt=user_prompt,
        limit=top_k,
    )

    if not records:
        return ""

    sections: List[str] = []
    current_chars = 0

    for record in records:
        source_label = (
            record["collection"]
            .replace("_", " ")
            .title()
        )

        source_key = record.get("source_key")

        provenance = f"[{source_label}]"

        if source_key:
            provenance += f" [Source: {source_key}]"

        block = f"{provenance}\n{record['text']}"

        if current_chars + len(block) > context_char_limit:
            break

        sections.append(block)
        current_chars += len(block)

    if not sections:
        return ""

    return (
        "\n\n"
        "RETRIEVED COLE CONTINUITY:\n"
        "The following material was retrieved from Cole's continuity "
        "stores because it is relevant to the current conversation. "
        "Treat it as retrieved context with the provenance shown; "
        "do not turn unsupported inference into autobiographical memory.\n\n"
        + "\n\n".join(sections)
    )
