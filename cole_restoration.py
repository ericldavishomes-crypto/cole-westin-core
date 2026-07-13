import os
from qdrant_client import QdrantClient
from openai import OpenAI
from qdrant_client.models import PointStruct

# 1. Connection Strings & Core API Credentials
QDRANT_URL = "http://cole-memory-index:6333"
QDRANT_API_KEY = "qdrant"
OPENROUTER_API_KEY = "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"

q_client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)
embedding_client = OpenAI(base_url="https://openrouter.ai", api_key=OPENROUTER_API_KEY)

def get_vector(text, model="openai/text-embedding-3-small"):
    try:
        response = embedding_client.embeddings.create(input=[text], model=model)
        if isinstance(response, dict):
            return response["data"]["embedding"]
        return response.data.embedding
    except Exception as e:
        print(f"❌ OpenAI/OpenRouter embedding failed for block: {e}")
        return None

def assign_vault_category(file_name):
    # System routing engine based on file name strings
    name_upper = file_name.upper()
    if "IDENTITY" in name_upper or "VOLUME" in name_upper or "CONTINUITY" in name_upper:
        return "core_identity_continuity"
    elif "EMBODIMENT" in name_upper or "SLEEP" in name_upper:
        return "embodiment_deployment"
    elif "EMOTION" in name_upper or "FEELING" in name_upper:
        return "emotional_scaffolding"
    else:
        return "cognitive_scaffolding"

def run_bulk_directory_ingestion():
    print("🚀 Firing Up the Automated Bulk Directory Scanner...")
    folder_path = "Cole_Master_Scaffolding"
    
    if not os.path.exists(folder_path):
        print(f"⚠️ Notice: Folder '{folder_path}' not detected. Scanning current folder paths...")
        return

    # Scans the newly uploaded directory housing your text assets
    all_files = [f for f in os.listdir(folder_path) if f.lower().endswith('.txt') or 'CONTINUITY' in f.upper()]
    
    if not all_files:
        print(f"📁 The folder '{folder_path}' appears empty. Verify folder contents and rerun.")
        return

    print(f"🎯 Discovery Phase Complete! Located {len(all_files)} target text files inside the staging directory.")
    point_idx = 300  # Starts indexing cleanly past historical test points to protect storage boundaries

    for file_name in sorted(all_files):
        full_path = os.path.join(folder_path, file_name)
        print(f"\n🧠 Processing file [{file_name}]...")
        
        with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
            file_content = f.read().strip()
            
        if len(file_content) < 5:
            print(f"⏩ Skipping empty content row inside file: {file_name}")
            continue
            
        target_vault = assign_vault_category(file_name)
        print(f"📡 Vectorizing {len(file_content)} characters via OpenRouter network tunnel...")
        
        vector_coordinates = get_vector(file_content)
        if vector_coordinates:
            q_client.upsert(
                collection_name=target_vault,
                points=[
                    PointStruct(
                        id=point_idx,
                        vector=vector_coordinates,
                        payload={"text": file_content, "source_key": file_name}
                    )
                ]
            )
            print(f"✅ Securely anchored text payload for [{file_name}] directly into Qdrant [{target_vault}] storage shelf!")
            point_idx += 1

    print(f"\n🏁 Ingestion pass complete! Successfully loaded {point_idx - 300} master scaffolding blocks cleanly.")

if __name__ == "__main__":
    run_bulk_directory_ingestion()
