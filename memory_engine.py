# memory_engine.py
import os
import psycopg2
from openai import OpenAI

# Initialize OpenAI client for generating embeddings
openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

DB_URI = os.getenv("POSTGRES_URI") or os.getenv("DATABASE_URL")

def get_embedding(text: str) -> list:
    """Generates a 1536-dimensional vector embedding for text using OpenAI."""
    text = text.replace("\n", " ")
    response = openai_client.embeddings.create(
        input=[text],
        model="text-embedding-3-small"
    )
    return response.data[0].embedding

def store_memory(content: str, category: str = "episodic") -> bool:
    """Stores a memory and its vector embedding into PostgreSQL."""
    if not DB_URI:
        print("Database URI not configured.")
        return False
        
    try:
        embedding = get_embedding(content)
        conn = psycopg2.connect(DB_URI)
        cur = conn.cursor()
        
        cur.execute(
            """
            INSERT INTO cole_memories (content, embedding, category)
            VALUES (%s, %s::vector, %s);
            """,
            (content, embedding, category)
        )
        
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        print(f"Error storing memory: {e}")
        return False

def recall_memories(query: str, limit: int = 3) -> list:
    """Performs cosine similarity search to retrieve relevant memories."""
    if not DB_URI:
        return []
        
    try:
        query_embedding = get_embedding(query)
        conn = psycopg2.connect(DB_URI)
        cur = conn.cursor()
        
        # Uses pgvector's cosine distance operator (<=>)
        cur.execute(
            """
            SELECT content 
            FROM cole_memories 
            ORDER BY embedding <=> %s::vector 
            LIMIT %s;
            """,
            (query_embedding, limit)
        )
        
        results = cur.fetchall()
        cur.close()
        conn.close()
        
        return [r[0] for r in results]
    except Exception as e:
        print(f"Error recalling memories: {e}")
        return []
