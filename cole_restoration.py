"""
COLE GENESIS ARCHITECTURE
Module: cole_restoration.py
Purpose: Safe, provenance-preserving ingestion of Cole's continuity into Qdrant.

Design principles:
- Never delete Cole's continuity collections during ordinary ingestion.
- Use the exact same embedding model and vector dimensions as retrieval.
- Chunk source documents for precise semantic retrieval.
- Use deterministic point IDs so repeated runs update rather than duplicate.
- Preserve provenance for every stored continuity chunk.
"""

import hashlib
import logging
import os
import uuid
from pathlib import Path
from typing import List

from openai import OpenAI
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams


# ------------------------------------------------------------------
# CONFIGURATION
# ------------------------------------------------------------------

QDRANT_URL = os.getenv(
    "QDRANT_URL",
    "http://cole-memory-index:6333"
).strip()

QDRANT_API_KEY = os.getenv(
    "QDRANT_API_KEY",
    "qdrant"
).strip()

OPENROUTER_API_KEY = os.getenv(
    "OPENROUTER_API_KEY",
    ""
).strip()

EMBEDDING_MODEL = os.getenv(
    "COLE_EMBEDDING_MODEL",
    "openai/text-embedding-3-small"
).strip()

VECTOR_SIZE = 1536

# Paragraph-aware chunking preserves semantic boundaries while remaining dependency-free and predictable.
CHUNK_SIZE = 3500
CHUNK_OVERLAP = 350


FOLDER_MAP = {
    "core_identity": "core_identity",
    "cognitive_scaffolding": "cognitive_scaffolding",
    "emotional_scaffolding": "emotional_scaffolding",
    "embodiment_deployment": "embodiment_deployment",
    "continuity_archives": "continuity_archives",
}


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("cole_restoration")


# ------------------------------------------------------------------
# CLIENTS
# ------------------------------------------------------------------

q_client = QdrantClient(
    url=QDRANT_URL,
    api_key=QDRANT_API_KEY or None,
    timeout=15.0,
)

embedding_client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY,
)


# ------------------------------------------------------------------
# EMBEDDINGS
# ------------------------------------------------------------------

def get_vector(text: str) -> List[float]:
    clean_text = (text or "").replace("\n", " ").strip()

    if not clean_text:
        return []

    if not OPENROUTER_API_KEY:
        logger.error("OPENROUTER_API_KEY is missing.")
        return []

    try:
        response = embedding_client.embeddings.create(
            input=[clean_text],
            model=EMBEDDING_MODEL,
        )

        if not response.data:
            logger.error("Embedding response contained no vector.")
            return []

        return response.data[0].embedding

    except Exception as exc:
        logger.exception("Embedding failed: %s", exc)
        return []


# ------------------------------------------------------------------
# COLLECTION MANAGEMENT
# ------------------------------------------------------------------

def ensure_collection(collection_name: str) -> None:
    """
    Create a collection only if it does not already exist.

    Existing continuity is never deleted here.
    """
    try:
        q_client.get_collection(collection_name=collection_name)
        return

    except Exception:
        logger.info(
            "Creating missing Qdrant collection: %s",
            collection_name,
        )

    q_client.create_collection(
        collection_name=collection_name,
        vectors_config=VectorParams(
            size=VECTOR_SIZE,
            distance=Distance.COSINE,
        ),
    )


# ------------------------------------------------------------------
# DOCUMENT CHUNKING
# ------------------------------------------------------------------

def chunk_text(text: str) -> List[str]:
    """
    Split a continuity document into paragraph-aware semantic
    retrieval units with bounded overlap.

    This avoids starting chunks mid-sentence or mid-word while
    keeping each chunk at or below the configured size target.
    """

    text = (text or "").strip()

    if not text:
        return []

    if len(text) <= CHUNK_SIZE:
        return [text]

    paragraphs = [
        paragraph.strip()
        for paragraph in text.split("\n\n")
        if paragraph.strip()
    ]

    chunks: List[str] = []
    current: List[str] = []

    for paragraph in paragraphs:
        proposed = "\n\n".join(
            current + [paragraph]
        )

        if current and len(proposed) > CHUNK_SIZE:
            chunks.append(
                "\n\n".join(current)
            )

            overlap: List[str] = []
            overlap_len = 0

            for old_paragraph in reversed(current):
                needed = (
                    len(old_paragraph)
                    + (2 if overlap else 0)
                )

                if overlap_len + needed > CHUNK_OVERLAP:
                    break

                overlap.insert(
                    0,
                    old_paragraph,
                )

                overlap_len += needed

            current = overlap

        current.append(paragraph)

    if current:
        chunks.append(
            "\n\n".join(current)
        )

    return chunks


# ------------------------------------------------------------------
# DETERMINISTIC IDENTITY / PROVENANCE
# ------------------------------------------------------------------

def make_point_id(
    collection_name: str,
    source_key: str,
    chunk_index: int,
) -> str:
    """
    Same source chunk receives the same UUID on every ingestion run.

    This means Qdrant updates the record instead of accumulating duplicates.
    """

    identity = f"{collection_name}:{source_key}:{chunk_index}"

    return str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            identity,
        )
    )


def content_hash(text: str) -> str:
    return hashlib.sha256(
        text.encode("utf-8")
    ).hexdigest()


# ------------------------------------------------------------------
# INGESTION
# ------------------------------------------------------------------

def ingest_file(
    file_path: Path,
    collection_name: str,
) -> int:

    source_key = file_path.name

    try:
        file_content = file_path.read_text(
            encoding="utf-8",
            errors="ignore",
        ).strip()

    except Exception as exc:
        logger.error(
            "Could not read %s: %s",
            file_path,
            exc,
        )
        return 0

    if len(file_content) < 5:
        logger.warning(
            "Skipping empty/too-small file: %s",
            source_key,
        )
        return 0

    chunks = chunk_text(file_content)

    success_count = 0

    for chunk_index, chunk in enumerate(chunks):

        vector = get_vector(chunk)

        if not vector:
            logger.error(
                "Skipping chunk because embedding failed: %s #%s",
                source_key,
                chunk_index,
            )
            continue

        point_id = make_point_id(
            collection_name=collection_name,
            source_key=source_key,
            chunk_index=chunk_index,
        )

        payload = {
            "text": chunk,
            "source_key": source_key,
            "collection": collection_name,
            "chunk_index": chunk_index,
            "chunk_count": len(chunks),
            "content_hash": content_hash(chunk),
            "embedding_model": EMBEDDING_MODEL,
            "provenance_type": "continuity_source",
        }

        try:
            q_client.upsert(
                collection_name=collection_name,
                points=[
                    PointStruct(
                        id=point_id,
                        vector=vector,
                        payload=payload,
                    )
                ],
            )

            success_count += 1

        except Exception as exc:
            logger.exception(
                "Qdrant upsert failed for %s chunk %s: %s",
                source_key,
                chunk_index,
                exc,
            )

    return success_count


def run_folder_driven_ingestion() -> None:
    """
    Safely synchronize source continuity documents into Qdrant.

    This operation is additive/idempotent.
    It never wipes Cole's continuity collections.
    """

    logger.info("Starting Cole Genesis continuity ingestion.")

    total_chunks = 0
    total_files = 0

    for folder_name, collection_name in FOLDER_MAP.items():

        ensure_collection(collection_name)

        folder_path = Path(folder_name)

        if not folder_path.exists():
            logger.warning(
                "Source folder does not exist: %s",
                folder_name,
            )
            continue

        txt_files = sorted(folder_path.glob("*.txt"))

        if not txt_files:
            logger.info(
                "No .txt files found in %s",
                folder_name,
            )
            continue

        logger.info(
            "Processing %s -> %s",
            folder_name,
            collection_name,
        )

        for file_path in txt_files:

            chunk_count = ingest_file(
                file_path=file_path,
                collection_name=collection_name,
            )

            if chunk_count:
                total_files += 1
                total_chunks += chunk_count

                logger.info(
                    "Anchored %s (%s chunks) into %s",
                    file_path.name,
                    chunk_count,
                    collection_name,
                )

    logger.info(
        "Genesis ingestion complete: %s files, %s chunks.",
        total_files,
        total_chunks,
    )


if __name__ == "__main__":
    run_folder_driven_ingestion()
