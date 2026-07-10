import os
from qdrant_client import QdrantClient
from openai import OpenAI
from qdrant_client.models import PointStruct

# 1. Network & Database connection strings
QDRANT_URL = "http://cole-memory-index:6333"
QDRANT_API_KEY = "qdrant"
OPENROUTER_API_KEY = "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"

q_client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)
embedding_client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=OPENROUTER_API_KEY)

# 👉 SWITCHING BACK TO THE UNIVERSAL ENDPOINT TACTICAL TANK
def get_vector(text, model="openai/text-embedding-3-small"):
    try:
        response = embedding_client.embeddings.create(input=[text], model=model)
        if isinstance(response, dict):
            return response["data"]["embedding"]
        return response.data.embedding
    except Exception as e:
        print(f"❌ OpenAI/OpenRouter embedding failed: {e}")
        return None

def assign_vault_category(key_name):
    key_upper = key_name.upper().strip()
    if "IDENTITY_ANCHOR" in key_upper or "VOLUME_1" in key_upper or "VOLUME_2" in key_upper:
        return "core_identity_continuity"
    return "cognitive_scaffolding"

def run_local_restoration():
    print("🚀 Initiating Direct Cohere File Ingestion Engine...")
    file_path = "test_scaffolding.txt"
    
    if not os.path.exists(file_path):
        print(f"❌ Error: Cannot find '{file_path}' in the workspace folder.")
        return

    # Prepare your core collection with the matching 1024 vector size for Cohere v3
    try:
        print("🧹 Cleaning and resizing the active core identity vector runway...")
        q_client.recreate_collection(
            collection_name="core_identity_continuity",
            vectors_config={"size": 1536, "distance": "Cosine"}
        )
    except Exception as e:
        print(f"⚠️ Collection reset paused: {e}")

    with open(file_path, "r", encoding="utf-8") as f:
        raw_content = f.read()

    raw_layers = raw_content.split("---START_LAYER---")
    point_idx = 1

    for layer in raw_layers:
        if not layer.strip():
            continue
            
        lines = layer.strip().split("\n")
        key_name = "UNKNOWN_KEY"
        text_lines = []
        is_text_mode = False
        
        for line in lines:
            if line.upper().startswith("KEY:"):
                key_name = line.split(":", 1)[1].replace("KEY:", "").strip()
            elif line.upper().startswith("TEXT:"):
                is_text_mode = True
            elif is_text_mode:
                text_lines.append(line)
                
        full_text = "\n".join(text_lines).strip()
        
        if len(full_text) < 5:
            continue
            
        target_vault = assign_vault_category(key_name)
        print(f"🧠 Vectorizing local layer [{key_name}] -> Target: [{target_vault}] ({len(full_text)} chars)...")
        
        vector_coordinates = get_vector(full_text)
        if vector_coordinates:
            q_client.upsert(
                collection_name=target_vault,
                points=[
                    PointStruct(
                        id=point_idx,
                        vector=vector_coordinates,
                        payload={"text": full_text, "source_key": key_name}
                    )
                ]
            )
            print(f"✅ Securely anchored text payload for [{key_name}] directly into Qdrant storage!")
            point_idx += 1

    print(f"\n🏁 Local restoration complete! Successfully loaded {point_idx - 1} foundational matrix blocks.")

if __name__ == "__main__":
    run_local_restoration()
