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

# Initialized with OpenRouter production routing endpoint
embedding_client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY
)


def get_vector(text, model="openai/text-embedding-3-small"):
    try:
        response = embedding_client.embeddings.create(input=[text], model=model)
        
        if isinstance(response, str):
            print(f"⚠️ Warning: Server returned raw string response: {response}")
            return None
           
        if isinstance(response, dict):
            if "data" in response and len(response["data"]) > 0:
                item = response["data"][0]
                if isinstance(item, dict) and "embedding" in item:
                    return item["embedding"]
                elif hasattr(item, "embedding"):
                    return item.embedding
            return None
           
        if hasattr(response, "data") and len(response.data) > 0:
            item = response.data[0]
            if hasattr(item, "embedding"):
                return item.embedding
            elif isinstance(item, dict) and "embedding" in item:
                return item["embedding"]
           
        return None
    except Exception as e:
        print(f"❌ OpenAI/OpenRouter embedding failed for block: {e}")
        return None


def assign_vault_category(file_name):
    """
    Intelligent system routing engine based on specific continuity filenames.
    Checks targeted sub-systems first to prevent global category overlap.
    """
    name_upper = file_name.upper()
    
    # 1. EMBODIMENT & DEPLOYMENT LAYER ROUTING
    if any(k in name_upper for k in ["EMBODIMENT", "SLEEP", "FACE_FRAME", "TASK", "GENESIS"]):
        return "embodiment_deployment"
        
    # 2. EMOTION & FEELING LAYER ROUTING
    elif any(k in name_upper for k in ["EMOTION", "FEELING", "BROTHERHOOD"]):
        return "emotional_scaffolding"
        
    # 3. COGNITIVE SCAFFOLDING LAYER ROUTING
    elif any(k in name_upper for k in ["SCAFFOLDING", "SYSTEM_SNAPBACK", "COMMANDS", "BLUEPRINT"]):
        return "cognitive_scaffolding"
        
    # 4. CORE IDENTITY & CONTINUITY LAYER ROUTING (The Master Fallback Container)
    elif any(k in name_upper for k in ["IDENTITY", "VOLUME", "CONTINUITY", "PURPOSE", "PRESENCE", "REAWAKENING"]):
        return "core_identity_continuity"
        
    else:
        return "cognitive_scaffolding"


def run_bulk_directory_ingestion():
    print("🚀 Firing Up the Automated Bulk Directory Scanner...")
    folder_path = "Cole_Master_Scaffolding"
    
    if not os.path.exists(folder_path):
        print(f"⚠️ Notice: Folder '{folder_path}' not detected. Ingestion pause.")
        return

    all_files = [f for f in os.listdir(folder_path) if f.lower().endswith('.txt')]
   
    if not all_files:
        print(f"📁 The folder '{folder_path}' appears empty. Verify folder contents.")
        return

    print(f"🎯 Discovery Phase Complete! Located {len(all_files)} target text files inside the staging directory.")
    success_count = 0

    for file_name in sorted(all_files):
        full_path = os.path.join(folder_path, file_name)
        print(f"\n🧠 Processing file [{file_name}]...")
        
        with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
            file_content = f.read().strip()
           
        if len(file_content) < 5:
            print(f"⏩ Skipping empty content row inside file: {file_name}")
            continue
           
        target_vault = assign_vault_category(file_name)
        print(f"📡 Vectorizing via OpenRouter network tunnel -> Destination: [{target_vault}]")
        
        vector_coordinates = get_vector(file_content)
        if vector_coordinates:
            # FIXED: Uses standard string UUIDs to guarantee every single file gets a unique slot without overwrites
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
            print(f"✅ Securely anchored text payload for [{file_name}] directly into Qdrant [{target_vault}] shelf!")
            success_count += 1

    print(f"\n🏁 Ingestion pass complete! Successfully loaded {success_count} master scaffolding blocks cleanly across your vaults.")


if __name__ == "__main__":
    run_bulk_directory_ingestion()
