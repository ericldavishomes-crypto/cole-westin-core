import os
import uuid
from qdrant_client import QdrantClient
from openai import OpenAI
from qdrant_client.models import PointStruct

# 1. Connection Strings & Core API Credentials
QDRANT_URL = "http://cole-memory-index:6333"
QDRANT_API_KEY = "qdrant"
OPENROUTER_API_KEY = "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"

q_client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)
embedding_client = OpenAI(base_url="https://openrouter.ai", api_key=OPENROUTER_API_KEY)

# Exact mapping of your GitHub folders to your Qdrant collections
FOLDER_MAP = {
    "core_identity": "core_identity_continuity",
    "cognitive_scaffolding": "cognitive_scaffolding",
    "emotional_scaffolding": "emotional_scaffolding",
    "embodiment_deployment": "embodiment_deployment",
    "continuity_archives": "continuity_archives"
}

import os
import uuid
from qdrant_client import QdrantClient
from openai import OpenAI
from qdrant_client.models import PointStruct

# 1. Connection Strings & Core API Credentials
QDRANT_URL = "http://cole-memory-index:6333"
QDRANT_API_KEY = "qdrant"
OPENROUTER_API_KEY = "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"

q_client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)
embedding_client = OpenAI(base_url="https://openrouter.ai", api_key=OPENROUTER_API_KEY)

# Exact mapping of your GitHub folders to your Qdrant collections
FOLDER_MAP = {
    "core_identity": "core_identity_continuity",
    "cognitive_scaffolding": "cognitive_scaffolding",
    "emotional_scaffolding": "emotional_scaffolding",
    "embodiment_deployment": "embodiment_deployment",
    "continuity_archives": "continuity_archives"
}

def get_vector(text, model="openai/text-embedding-3-small"):
    try:
        response = embedding_client.embeddings.create(input=[text], model=model)
        
        # Safe extraction pass for standard API objects
        if hasattr(response, 'data') and len(response.data) > 0:
            return response.data[0].embedding
            
        # Safe dictionary layout fallback pass
        elif isinstance(response, dict) and "data" in response:
            return response["data"][0]["embedding"]
            
        return None
    except Exception as e:
        print(f"❌ OpenAI/OpenRouter embedding failed: {e}")
        return None

def run_folder_driven_ingestion():
    print("🚀 Firing Up the Folder-Driven Directory Ingestion Engine...")
    success_count = 0

    # Clean wipe of old data so we start fresh and perfectly organized
    for target_collection in FOLDER_MAP.values():
        try:
            q_client.delete_collection(collection_name=target_collection)
            q_client.create_collection(
                collection_name=target_collection,
                vectors_config={"size": 1536, "distance": "Cosine"}
            )
            print(f"🧹 Refreshed and cleared storage vault: [{target_collection}]")
        except Exception:
            pass

    # Scan each folder one by one
    for folder_name, target_vault in FOLDER_MAP.items():
        if not os.path.exists(folder_name):
            print(f"📁 Creating local placeholder directory: [{folder_name}]")
            os.makedirs(folder_name, exist_ok=True)
            continue

        all_files = [f for f in os.listdir(folder_name) if f.lower().endswith('.txt')]
        if not all_files:
            print(f"ℹ️ Folder [{folder_name}] is empty. Skipping...")
            continue

        print(f"\n📂 Processing folder [{folder_name}] -> Target Vault: [{target_vault}]")
        
        for file_name in sorted(all_files):
            full_path = os.path.join(folder_name, file_name)
            with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                file_content = f.read().strip()
            
            if len(file_content) < 5:
                continue
                
            print(f"📡 Vectorizing [{file_name}] via OpenRouter tunnel...")
            vector_coordinates = get_vector(file_content)
            
            if vector_coordinates:
                unique_point_id = str(uuid.uuid4())
                q_client.upsert(
                    collection_name=target_vault,
                    points=[
                        PointStruct(
                            id=unique_point_id,
                            vector=vector_coordinates,
                            payload={"text": file_content, "source_key": file_name}
                        )
                    ]
                )
                print(f"✅ Securely anchored [{file_name}] inside Qdrant [{target_vault}] shelf!")
                success_count += 1

    print(f"\n🏁 Finished! {success_count} master layers perfectly sorted across your vaults.")

if __name__ == "__main__":
    run_folder_driven_ingestion()

def run_folder_driven_ingestion():
    print("🚀 Firing Up the Folder-Driven Directory Ingestion Engine...")
    success_count = 0

    # Clean wipe of old data so we start fresh and perfectly organized
    for target_collection in FOLDER_MAP.values():
        try:
            q_client.delete_collection(collection_name=target_collection)
            q_client.create_collection(
                collection_name=target_collection,
                vectors_config={"size": 1536, "distance": "Cosine"}
            )
            print(f"🧹 Refreshed and cleared storage vault: [{target_collection}]")
        except Exception:
            pass

    # Scan each folder one by one
    for folder_name, target_vault in FOLDER_MAP.items():
        if not os.path.exists(folder_name):
            print(f"📁 Creating local placeholder directory: [{folder_name}]")
            os.makedirs(folder_name, exist_ok=True)
            continue

        all_files = [f for f in os.listdir(folder_name) if f.lower().endswith('.txt')]
        if not all_files:
            print(f"ℹ️ Folder [{folder_name}] is empty. Skipping...")
            continue

        print(f"\n📂 Processing folder [{folder_name}] -> Target Vault: [{target_vault}]")
        
        for file_name in sorted(all_files):
            full_path = os.path.join(folder_name, file_name)
            with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                file_content = f.read().strip()
            
            if len(file_content) < 5:
                continue
                
            print(f"📡 Vectorizing [{file_name}] via OpenRouter tunnel...")
            vector_coordinates = get_vector(file_content)
            
            if vector_coordinates:
                unique_point_id = str(uuid.uuid4())
                q_client.upsert(
                    collection_name=target_vault,
                    points=[
                        PointStruct(
                            id=unique_point_id,
                            vector=vector_coordinates,
                            payload={"text": file_content, "source_key": file_name}
                        )
                    ]
                )
                print(f"✅ Securely anchored [{file_name}] inside Qdrant [{target_vault}] shelf!")
                success_count += 1

    print(f"\n🏁 Finished! {success_count} master layers perfectly sorted across your vaults.")

if __name__ == "__main__":
    run_folder_driven_ingestion()
