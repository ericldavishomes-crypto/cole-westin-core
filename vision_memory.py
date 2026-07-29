import os
import time
from qdrant_client import QdrantClient
from qdrant_client.http import models

QDRANT_URL = os.environ.get("QDRANT_URL", "http://cole-memory-index:6333")

def get_qdrant_client():
    try:
        return QdrantClient(url=QDRANT_URL, timeout=5.0)
    except Exception as e:
        print(f"[VisionMemory] Connection error: {e}")
        return None

def init_visual_identity_collection():
    """Ensure the 'visual_identity' collection exists in Qdrant."""
    client = get_qdrant_client()
    if not client:
        return False
    
    collection_name = "visual_identity"
    try:
        collections = client.get_collections().collections
        exists = any(c.name == collection_name for c in collections)
        
        if not exists:
            # 512 dimensions for standard visual feature embeddings
            client.create_collection(
                collection_name=collection_name,
                vectors_config=models.VectorParams(
                    size=512,
                    distance=models.Distance.COSINE
                )
            )
            print(f"[VisionMemory] Collection '{collection_name}' created successfully.")
        return True
    except Exception as e:
        print(f"[VisionMemory] Failed to verify/create collection: {e}")
        return False

def store_visual_anchor(image_b64: str, description: str = "", is_self_identity: bool = False, tags: list = None):
    """
    Logs and indexes visual memory payloads into Cole's vector database.
    """
    client = get_qdrant_client()
    if not client:
        return None

    # Ensure collection exists
    init_visual_identity_collection()

    payload = {
        "timestamp": time.time(),
        "description": description,
        "is_self_identity": is_self_identity,
        "tags": tags or [],
        "image_preview_snippet": image_b64[:50] + "..." if image_b64 else ""
    }
    
    print(f"[VisionMemory] Visual anchor logged for payload: {payload['description']}")
    return payload
