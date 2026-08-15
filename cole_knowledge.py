import logging
import os
import re
from typing import Any, Dict, List

from openai import OpenAI
from qdrant_client import QdrantClient


# ---------------------------------------------------------
# CONFIGURATION CANDIDATES
# ---------------------------------------------------------

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

# These names intentionally match cole_restoration.py.
COLE_COLLECTIONS = [
    "core_identity",
    "embodiment_deployment",
    "emotional_scaffolding",
    "continuity_archives",
    "cognitive_scaffolding",
]

# Search several semantic candidates in each vault,
# then rank candidates together globally.
CANDIDATES_PER_COLLECTION = 8

# Maximum number of retrieved continuity records
# given to Cole per turn.
DEFAULT_GLOBAL_LIMIT = 6

# Prevent retrieval from flooding the active context window.
DEFAULT_CONTEXT_CHAR_LIMIT = 9000

# Temporary lexical-retrieval bridge.
# Current corpus is small enough that a bounded payload
# scan is inexpensive.
# This will later be replaced by native sparse/BM25 retrieval.
LEXICAL_SCAN_LIMIT = 500

# Number of lexical candidates admitted from each collection.
LEXICAL_CANDIDATES_PER_COLLECTION = 4

# Dense semantics remain primary.
# Lexical evidence provides a controlled boost for exact
# names, places, technical terms, and filenames.
LEXICAL_WEIGHT = 0.12


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
        logger.exception(
            "Cole query embedding failed: %s",
            exc,
        )
        return []


# ---------------------------------------------------------
# RETRIEVAL HELPERS
# ---------------------------------------------------------

_LEXICAL_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "but",
    "by", "do", "does", "for", "from", "had", "has", "have",
    "he", "her", "his", "i", "in", "is", "it", "me", "my",
    "of", "on", "or", "our", "so", "that", "the", "their",
    "them", "there", "they", "this", "to", "us", "was", "we",
    "were", "what", "when", "where", "which", "who", "why",
    "with", "you", "your",
}


def _normalize_lexical_text(text: str) -> str:
    """
    Normalize text for lightweight lexical comparison.
    """
    return " ".join(
        re.findall(r"[a-z0-9]+", (text or "").lower())
    )


def _meaningful_query_terms(user_prompt: str) -> List[str]:
    """
    Extract meaningful lexical terms from the current user query.

    Dense retrieval remains responsible for paraphrase/semantic matching.
    These terms provide exact lexical evidence for names, places,
    filenames, technical identifiers, and other explicit cues.
    """
    raw_terms = re.findall(
        r"[a-z0-9]+",
        (user_prompt or "").lower(),
    )

    return [
        term
        for term in raw_terms
        if len(term) >= 3 and term not in _LEXICAL_STOPWORDS
    ]


def _lexical_score(
    user_prompt: str,
    source_key: Any,
    text: str,
) -> float:
    """
    Return a bounded lexical relevance score from 0.0 to 1.0.

    Filename/source matches receive stronger credit than body-text matches
    because source names often contain high-value episodic or technical
    identifiers such as "Miami Trip" or a specific continuity volume.
    """
    query_terms = _meaningful_query_terms(user_prompt)

    if not query_terms:
        return 0.0

    unique_terms = list(dict.fromkeys(query_terms))

    source_norm = _normalize_lexical_text(
        str(source_key or "")
    )
    text_norm = _normalize_lexical_text(text)

    source_tokens = set(source_norm.split())
    text_tokens = set(text_norm.split())

    source_hits = sum(
        1 for term in unique_terms
        if term in source_tokens
    )

    text_hits = sum(
        1 for term in unique_terms
        if term in text_tokens
    )

    query_term_count = max(1, len(unique_terms))

    source_coverage = source_hits / query_term_count
    text_coverage = text_hits / query_term_count

    # Exact multi-word query fragments can be especially informative.
    meaningful_phrase = " ".join(unique_terms)
    phrase_bonus = 0.0

    if len(unique_terms) >= 2:
        if meaningful_phrase in source_norm:
            phrase_bonus = 0.30
        elif meaningful_phrase in text_norm:
            phrase_bonus = 0.15

    score = (
        (0.65 * source_coverage)
        + (0.35 * text_coverage)
        + phrase_bonus
    )

    return min(1.0, score)


def _record_from_hit(
    hit: Any,
    collection_name: str,
    user_prompt: str,
) -> Dict[str, Any]:
    """
    Convert one Qdrant point into Cole's provenance-rich retrieval record.
    """
    payload = hit.payload or {}

    text_chunk = str(
        payload.get("text", "")
    ).strip()

    dense_score = float(
        getattr(hit, "score", 0.0) or 0.0
    )

    lexical_score = _lexical_score(
        user_prompt=user_prompt,
        source_key=payload.get("source_key"),
        text=text_chunk,
    )

    return {
        "point_id": str(hit.id),
        "text": text_chunk,
        "collection": collection_name,
        "source_key": payload.get("source_key"),

        # Retrieval evidence
        "dense_score": dense_score,
        "lexical_score": lexical_score,
        "final_score": dense_score + (
            LEXICAL_WEIGHT * lexical_score
        ),

        # Backward compatibility for code expecting "score"
        "score": dense_score + (
            LEXICAL_WEIGHT * lexical_score
        ),

        # Modern provenance metadata when available.
        # Legacy points simply carry None.
        "chunk_index": payload.get("chunk_index"),
        "chunk_count": payload.get("chunk_count"),
        "content_hash": payload.get("content_hash"),
        "embedding_model": payload.get("embedding_model"),
        "provenance_type": payload.get("provenance_type"),
    }


# ---------------------------------------------------------
# DENSE QDRANT RETRIEVAL
# ---------------------------------------------------------

def _search_collection(
    collection_name: str,
    query_vector: List[float],
    limit: int,
    user_prompt: str = "",
) -> List[Dict[str, Any]]:
    """
    Search one continuity vault semantically and return
    provenance-rich records.
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
            "Qdrant dense search failed for collection '%s': %s",
            collection_name,
            exc,
        )
        return []

    records: List[Dict[str, Any]] = []

    for hit in results:
        record = _record_from_hit(
            hit=hit,
            collection_name=collection_name,
            user_prompt=user_prompt,
        )

        if record["text"]:
            records.append(record)

    return records


# ---------------------------------------------------------
# LIGHTWEIGHT LEXICAL CANDIDATE RETRIEVAL
# ---------------------------------------------------------

def _lexical_search_collection(
    collection_name: str,
    user_prompt: str,
    limit: int = LEXICAL_CANDIDATES_PER_COLLECTION,
) -> List[Dict[str, Any]]:
    """
    Temporary lexical bridge for Cole's small current corpus.

    Scans a bounded number of payload records and promotes exact lexical
    matches that dense-only retrieval may miss.

    This is intentionally transitional. It should later be replaced by
    native sparse/BM25 retrieval as Cole's episodic corpus grows.
    """
    try:
        points, _ = q_client.scroll(
            collection_name=collection_name,
            limit=LEXICAL_SCAN_LIMIT,
            with_payload=True,
            with_vectors=False,
        )

    except Exception as exc:
        logger.warning(
            "Qdrant lexical scan failed for collection '%s': %s",
            collection_name,
            exc,
        )
        return []

    lexical_records: List[Dict[str, Any]] = []

    for point in points:
        payload = point.payload or {}

        text_chunk = str(
            payload.get("text", "")
        ).strip()

        if not text_chunk:
            continue

        lexical_score = _lexical_score(
            user_prompt=user_prompt,
            source_key=payload.get("source_key"),
            text=text_chunk,
        )

        if lexical_score <= 0.0:
            continue

        lexical_records.append(
            {
                "point_id": str(point.id),
                "text": text_chunk,
                "collection": collection_name,
                "source_key": payload.get("source_key"),

                # Dense score is unknown on this lexical-only path.
                "dense_score": 0.0,
                "lexical_score": lexical_score,
                "final_score": LEXICAL_WEIGHT * lexical_score,
                "score": LEXICAL_WEIGHT * lexical_score,

                "chunk_index": payload.get("chunk_index"),
                "chunk_count": payload.get("chunk_count"),
                "content_hash": payload.get("content_hash"),
                "embedding_model": payload.get("embedding_model"),
                "provenance_type": payload.get("provenance_type"),
            }
        )

    lexical_records.sort(
        key=lambda record: record["lexical_score"],
        reverse=True,
    )

    return lexical_records[:max(1, limit)]


# ---------------------------------------------------------
# GLOBAL HYBRID-LITE RETRIEVAL
# ---------------------------------------------------------

def fetch_cole_memory_records(
    user_prompt: str,
    limit: int = DEFAULT_GLOBAL_LIMIT,
) -> List[Dict[str, Any]]:
    """
    Search all five continuity vaults using two complementary paths:

    1. Dense semantic retrieval
    2. Lightweight lexical retrieval

    Candidate results are fused, de-duplicated, and globally ranked.

    The original dense score, lexical score, and final fusion score remain
    separate so retrieval behavior is auditable.
    """
    query_vector = get_embedding(user_prompt)

    if not query_vector:
        return []

    candidate_map: Dict[str, Dict[str, Any]] = {}

    for collection_name in COLE_COLLECTIONS:

        dense_records = _search_collection(
            collection_name=collection_name,
            query_vector=query_vector,
            limit=CANDIDATES_PER_COLLECTION,
            user_prompt=user_prompt,
        )

        lexical_records = _lexical_search_collection(
            collection_name=collection_name,
            user_prompt=user_prompt,
            limit=LEXICAL_CANDIDATES_PER_COLLECTION,
        )

        # -------------------------------------------------
        # Admit dense candidates first.
        # -------------------------------------------------

        for record in dense_records:
            identity = (
                record.get("point_id")
                or (
                    f"{record.get('collection')}:"
                    f"{record.get('source_key')}:"
                    f"{record.get('chunk_index')}:"
                    f"{hash(record.get('text', ''))}"
                )
            )

            candidate_map[identity] = record

        # -------------------------------------------------
        # Merge lexical evidence into existing candidates,
        # or admit lexical-only candidates.
        # -------------------------------------------------

        for lexical_record in lexical_records:
            identity = (
                lexical_record.get("point_id")
                or (
                    f"{lexical_record.get('collection')}:"
                    f"{lexical_record.get('source_key')}:"
                    f"{lexical_record.get('chunk_index')}:"
                    f"{hash(lexical_record.get('text', ''))}"
                )
            )

            existing = candidate_map.get(identity)

            if existing is None:
                candidate_map[identity] = lexical_record
                continue

            lexical_score = max(
                float(existing.get("lexical_score", 0.0)),
                float(lexical_record.get("lexical_score", 0.0)),
            )

            dense_score = float(
                existing.get("dense_score", 0.0)
            )

            final_score = dense_score + (
                LEXICAL_WEIGHT * lexical_score
            )

            existing["lexical_score"] = lexical_score
            existing["final_score"] = final_score
            existing["score"] = final_score

    candidates = list(candidate_map.values())

    if not candidates:
        return []

    candidates.sort(
        key=lambda record: record["final_score"],
        reverse=True,
    )

    # De-duplicate identical text that may appear in multiple vaults.
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

        remaining_chars = context_char_limit - current_chars

        if remaining_chars <= 0:
            break

        if len(block) > remaining_chars:
            truncation_note = "\n[Retrieved memory truncated to fit active context.]"
            content_budget = max(
                0,
                remaining_chars - len(truncation_note),
            )
            truncated_block = block[:content_budget].rstrip()

            if truncated_block:
                truncated_block += truncation_note
                sections.append(truncated_block)
                current_chars += len(truncated_block)

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
