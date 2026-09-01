from __future__ import annotations

import os
from dataclasses import dataclass

from openai import OpenAI
from psycopg2.pool import ThreadedConnectionPool

from episodic_memory import (
    EMBEDDING_DIMENSION,
    EMBEDDING_MODEL_NAME,
    EMBEDDING_MODEL_VERSION,
    validate_vector,
)


OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()

PROVIDER_EMBEDDING_MODEL = os.getenv(
    "COLE_EMBEDDING_MODEL",
    "openai/text-embedding-3-small",
).strip()


@dataclass
class EpisodicEmbedder:
    """
    Production embedding adapter for EpisodicMemoryEngine.

    The provider-facing model name may include the OpenRouter namespace,
    while the metadata exposed to EpisodicMemoryEngine remains the
    canonical embedding contract defined by episodic_memory.py.
    """

    model_name: str = EMBEDDING_MODEL_NAME
    dimension: int = EMBEDDING_DIMENSION
    model_version: str = EMBEDDING_MODEL_VERSION

    def __post_init__(self) -> None:
        if not OPENROUTER_API_KEY:
            raise RuntimeError(
                "OPENROUTER_API_KEY is required for episodic embeddings"
            )

        self._client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=OPENROUTER_API_KEY,
        )

    def embed_text(self, text: str) -> list[float]:
        clean_text = (text or "").replace("\n", " ").strip()

        if not clean_text:
            raise ValueError("Cannot embed empty episodic text")

        response = self._client.embeddings.create(
            input=[clean_text],
            model=PROVIDER_EMBEDDING_MODEL,
        )

        if not response.data:
            raise RuntimeError("Embedding response contained no vectors")

        vector = response.data[0].embedding
        validate_vector(vector, self.dimension)

        return vector
        

def create_episodic_db_pool(
    min_connections: int = 1,
    max_connections: int = 10,
) -> ThreadedConnectionPool:
    """
    Create the production PostgreSQL connection pool used by
    EpisodicMemoryEngine and the consolidation worker.
    """

    database_url = os.getenv("DATABASE_URL", "").strip()

    if not database_url:
        raise RuntimeError(
            "DATABASE_URL is required for episodic memory"
        )

    if min_connections < 1:
        raise ValueError("min_connections must be at least 1")

    if max_connections < min_connections:
        raise ValueError(
            "max_connections must be greater than or equal to min_connections"
        )

    return ThreadedConnectionPool(
        min_connections,
        max_connections,
        dsn=database_url,
    )
