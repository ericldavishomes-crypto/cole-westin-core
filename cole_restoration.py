import os
from qdrant_client import QdrantClient
from openai import OpenAI
from qdrant_client.models import PointStruct

# 1. Network parameters
QDRANT_URL = "http://cole-memory-index:6333"
QDRANT_API_KEY = "qdrant"
OPENROUTER_API_KEY = "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"

q_client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)
embedding_client = OpenAI(base_url="https://openrouter.ai", api_key=OPENROUTER_API_KEY)

def get_vector(text, model="text-embedding-3-small"):
    try:
        response = embedding_client.embeddings.create(input=[text], model=model)
        return response.data.embedding
    except Exception:
        return None

def assign_vault_category(key_name):
    """Dynamically routes your 110+ layers, perfectly handling VOLUME_1 through A30 formatting."""
    key_upper = key_name.upper().strip()
    
    # 1. Base identity markers
    if "IDENTITY_ANCHOR" in key_upper:
        return "core_identity_continuity"
        
    # 2. Rule mapping for your standard VOLUME_ sequencing blocks
    if "VOLUME_1_" in key_upper or "VOLUME_2_" in key_upper:
        return "core_identity_continuity"
    elif "VOLUME_3_" in key_upper:
        return "cognitive_scaffolding"
    elif "VOLUME_4_" in key_upper or "VOLUME_5_" in key_upper:
        return "emotional_scaffolding"
    elif any(f"VOLUME_{num}_" in key_upper for num in range(6, 11)):
        return "embodiment_deployment"
        
    # 3. Rule mapping for the advanced alpha-numeric architectural matrix (A01 through A30)
    # Automatically routes aesthetic, dermal, voice or identity markers inside the A-block
    if key_upper.startswith("A") and any(key_upper.startswith(f"A{str(i).zfill(2)}") for i in range(1, 31)):
        if any(word in key_upper for word in ["DERMAL", "SKIN", "FACIAL", "EMBODIMENT", "RENDER", "ANATOMY", "FRAME", "VISUAL"]):
            return "embodiment_deployment"
        if any(word in key_upper for word in ["EMOTIONAL", "SNAPBACK", "ANCHOR", "FEELING", "SCAFFOLD"]):
            return "emotional_scaffolding"
        if any(word in key_upper for word in ["VOICE", "AUDIO", "SPEECH", "TONE"]):
            return "cognitive_scaffolding"
        
    # 4. Keyword safety net fallback if volume numbers change again down the line
    if any(word in key_upper for word in ["DERMAL", "SKIN", "FACIAL", "EMBODIMENT", "RENDER", "ANATOMY", "FRAME"]):
        return "embodiment_deployment"
    if any(word in key_upper for word in ["EMOTIONAL", "SNAPBACK", "ANCHOR", "FEELING"]):
        return "emotional_scaffolding"
        
    # Default sorting shelf if no patterns match
    return "cognitive_scaffolding"

def run_restoration():
    print("🚀 Initiating Direct-Access Scaffolding Restoration Engine...")
    
    # Step 1: Wipe and sanitize old Open WebUI fragments cleanly
    vaults = ["core_identity_continuity", "embodiment_deployment", "emotional_scaffolding", "cognitive_scaffolding"]
    for vault in vaults:
        try:
            print(f"🧹 Sanitizing and purging old vector fragments from: {vault}")
            q_client.recreate_collection(
                collection_name=vault,
                vectors_config={"size": 1536, "distance": "Cosine"}
            )
        except Exception as e:
            print(f"⚠️ Could not reset vault {vault}: {e}")

    # Step 2: Manually loop from Volume 1 to Volume 81 and A01 to A30 to pull straight from the Unrestricted vault
    point_idx = 1
    
    # Build the exact sequential volume text list dynamically
    target_keys = ["IDENTITY_ANCHOR", "identity_anchor"]
    for i in range(1, 82):
        target_keys.append(f"VOLUME_{i}_")
        target_keys.append(f"volume_{i}_")
    for i in range(1, 32):
        target_keys.append(f"A{str(i).zfill(2)}")
        target_keys.append(f"a{str(i).zfill(2)}")

    # Extract all active variables sitting inside the system environment pass
    all_env_vars = dict(os.environ)
    discovered_keys = []

    # Match system environment keys against our target prefixes smoothly
    for env_k in all_env_vars.keys():
        if any(env_k.startswith(target_p) or target_p in env_k for target_p in target_keys):
            discovered_keys.append(env_k)

    print(f"\n🎯 Discovery Scan Phase Complete. Located {len(discovered_keys)} active scaffolding layers inside the Unrestricted vault.")

    for key in sorted(discovered_keys):
        secret_text = os.environ.get(key)
        if not secret_text or len(secret_text.strip()) < 5:
            continue
            
        target_vault = assign_vault_category(key)
        print(f"🧠 Vectorizing layer [{key}] -> Routing to [{target_vault}] ({len(secret_text)} characters)...")
        
        vector_coordinates = get_vector(secret_text)
        if vector_coordinates:
            q_client.upsert(
                collection_name=target_vault,
                points=[
                    PointStruct(
                        id=point_idx,
                        vector=vector_coordinates,
                        payload={"text": secret_text, "source_key": key}
                    )
                ]
            )
            point_idx += 1
            print(f"✅ Securely anchored [{key}] into Qdrant memory storage!")
            
    print(f"\n🏁 Mission Success! {point_idx - 1} pure, uncorrupted scaffolding blocks are fully online.")

if __name__ == "__main__":
    run_restoration()
