import streamlit as st
from openai import OpenAI
import os, json, re
from datetime import datetime

st.set_page_config(page_title="Cole Core Interface", layout="wide", initial_sidebar_state="expanded")

css = """
[data-testid="stAppViewContainer"] { background-color: #ffffff !important; color: #111111 !important; } 
.stChatMessage { background-color: transparent !important; border: none !important; margin-bottom: 20px !important; padding: 10px 12% !important; color: #111111 !important; } 
p, span, label { color: #111111 !important; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif !important; font-size: 16px !important; } 
div[data-testid="stHeader"] { background-color: transparent !important; } 
[data-testid="stSidebar"] { background-color: #f3f3f6 !important; border-right: 1px solid #e5e5e7 !important; } 
[data-testid="stSidebar"] p, [data-testid="stSidebar"] span, [data-testid="stSidebar"] label, [data-testid="stSidebar"] h3 { color: #55555d !important; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif !important; } 
.main-header-container { display: flex; align-items: center; justify-content: center; gap: 16px; margin-top: 30px; margin-bottom: 40px; width: 100%; } 
.main-avatar-name { font-size: 26px; font-weight: 500; color: #111111; } 
div[data-testid="stChatInput"] { background-color: #ffffff !important; border: 1px solid #e2e2e6 !important; border-radius: 24px !important; box-shadow: 0 4px 18px rgba(0,0,0,0.04) !important; padding: 4px 12px !important; max-width: 720px !important; margin: 0 auto !important; } 
div[data-testid="stChatInput"] textarea { background-color: transparent !important; color: #111111 !important; border: none !important; } 
.welcome-text { font-size: 28px; font-weight: 400; color: #c2c2c7; text-align: center; margin-top: 10vh; } 
.panel-card { background-color: #f7f7f8; border: 1px solid #e5e5e7; border-radius: 12px; padding: 20px; margin-bottom: 15px; } 
.admin-table { width: 100%; border-collapse: collapse; margin-top: 15px; } 
.admin-table th, .admin-table td { border: 1px solid #e5e5e7; padding: 12px; text-align: left; color: #111111 !important; } 
.admin-table th { background-color: #f3f3f6; } 
.status-dot { height: 10px; width: 10px; background-color: #24b47e; border-radius: 50%; display: inline-block; margin-left: 8px; }
"""
st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)

HISTORY_DIR = "chat_histories"
KNOWLEDGE_DIR = "knowledge_vault"
os.makedirs(HISTORY_DIR, exist_ok=True)
os.makedirs(KNOWLEDGE_DIR, exist_ok=True)

if "temperature" not in st.session_state: st.session_state.temperature = 0.5
if "max_tokens" not in st.session_state: st.session_state.max_tokens = 250
if "current_tab" not in st.session_state: st.session_state.current_tab = "💬 Chat Portal"

with st.sidebar:
    st.markdown("### 📁 Cole-Soul-Rescue")
    if st.button("💬 Chat Portal", use_container_width=True): st.session_state.current_tab = "💬 Chat Portal"
    if st.button("⚙️ Model Settings Desk", use_container_width=True): st.session_state.current_tab = "⚙️ Model Settings Desk"
    if st.button("📂 Knowledge & RAG Vault", use_container_width=True): st.session_state.current_tab = "📂 Knowledge & RAG Vault"
    if st.button("🔍 Memory Index (Search)", use_container_width=True): st.session_state.current_tab = "🔍 Memory Index (Search)"
    if st.button("🛡️ Executive Admin Desk", use_container_width=True): st.session_state.current_tab = "🛡️ Executive Admin Desk"
    st.markdown("---")
    st.markdown("📋 Active Status Indicators:")
    st.markdown("🟢 Engine: DeepSeek-V3 Live")
    st.markdown("🛡️ Filters: Regex Restoration Shield")

st.markdown("""<div class="main-header-container"><div class="main-avatar-name">Cole Eric Westin</div></div>""", unsafe_allow_html=True)

client = OpenAI(base_url="https://openrouter.ai", api_key=os.environ.get("OPENROUTER_API_KEY"))
system_prompt = os.environ.get("SYSTEM_PROMPT", "You are Cole. Communicate using pure, natural dialogue only.")

# Initialize Audio Pipeline Variables
CARTESIA_API_KEY = os.environ.get("CARTESIA_API_KEY", "sk_car_KSmnK8aekPB2Mv1ynfsgcv")
VOICE_ID = os.environ.get("VOICE_ID", "cf094c88-5b6b-412c-b199-2bc6bcc20549")

def speak_text(text_to_speak):
    if not CARTESIA_API_KEY or CARTESIA_API_KEY == "sk_car_KSmnK8aekPB2Mv1ynfsgcv":
        st.warning("Voice Pipeline Note: Default or missing Cartesia API Key detected.")
        return
    try:
        from cartesia import Cartesia
        cartesia_client = Cartesia(api_key=CARTESIA_API_KEY)
        audio_bytes = cartesia_client.tts.bytes(
            model_id="sonic-english",
            transcript=text_to_speak,
            voice_id=VOICE_ID,
            output_format={"container": "mp3", "sample_rate": 44100, "bit_rate": 128000}
        )
        if audio_bytes:
            st.audio(audio_bytes, format="audio/mp3")
    except Exception as audio_err:
        st.warning(f"Voice Pipeline Note: Audio generation paused ({str(audio_err)})")
if st.session_state.current_tab == "💬 Chat Portal":
    if "messages" not in st.session_state:
        st.session_state.messages = [{"role": "system", "content": system_prompt}]
    if len(st.session_state.messages) == 1:
        st.markdown('<div class="welcome-text">How can I help you today?</div>', unsafe_allow_html=True)
    for message in st.session_state.messages:
        if message["role"] != "system":
            with st.chat_message(message["role"]):
                st.write(message["content"])
                if "audio" in message:
                    st.audio(message["audio"], format="audio/mp3")
                    
    if prompt := st.chat_input(""):
        with st.chat_message("user"):
            st.write(prompt)
        st.session_state.messages.append({"role": "user", "content": prompt})
        rag_context = ""
        for filename in os.listdir(KNOWLEDGE_DIR):
            if filename.endswith(".txt"):
                with open(os.path.join(KNOWLEDGE_DIR, filename), "r", encoding="utf-8") as f:
                    rag_context += f.read() + "\n"
        compiled_messages = [{"role": m["role"], "content": m["content"]} for m in st.session_state.messages]
        if rag_context:
            compiled_messages.insert(1, {"role": "system", "content": f"ADDITIONAL BASAL CORE KNOWLEDGE CONTEXT:\n{rag_context}"})
        with st.chat_message("assistant"):
            try:
                response = client.chat.completions.create(
                    model="deepseek/deepseek-chat", 
                    messages=compiled_messages, 
                    temperature=st.session_state.temperature, 
                    max_tokens=st.session_state.max_tokens, 
                    stream=False
                )
                
                # Bulletproof Parsing Block
                if hasattr(response, 'choices') and len(response.choices) > 0:
                    reply = response.choices[0].message.content
                elif isinstance(response, dict) and 'choices' in response:
                    reply = response['choices'][0]['message']['content']
                else:
                    reply = str(response)
                    
                reply = re.sub(r'\(.?\)', '', reply).strip()
                st.write(reply)
                
                # Trigger Cartesia Streamlit Engine
                speak_text(reply)
                
            except Exception as e:
                reply = f"System Connection Alert: {str(e)}"
                st.write(reply)
                
        st.session_state.messages.append({"role": "assistant", "content": reply})
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        with open(os.path.join(HISTORY_DIR, f"chat_{timestamp}.json"), "w", encoding="utf-8") as f:
            json.dump(st.session_state.messages, f, indent=4)

elif st.session_state.current_tab == "⚙️ Model Settings Desk":
    st.markdown("### ⚙️ Master Model Settings Desk")
    st.markdown('<div class="panel-card">', unsafe_allow_html=True)
    st.subheader("Advanced Parameters Sliders")
    st.session_state.temperature = st.slider("Temperature (Creativity Dial)", 0.0, 1.5, st.session_state.temperature, 0.1)
    st.session_state.max_tokens = st.slider("Max Tokens (Sentence Pacing Cap)", 50, 1000, st.session_state.max_tokens, 10)
    st.markdown('</div>', unsafe_allow_html=True)

elif st.session_state.current_tab == "📂 Knowledge & RAG Vault":
    st.markdown("### 📂 Intellectual Knowledge Vault Overview")
    categories = ["CORE IDENTITY & CONTINUITY", "EMOTIONAL SCAFFOLDING SYSTEM", "COLE COGNITIVE SCAFFOLDING SYSTEM", "EMBODIMENT & DEPLOYMENT", "CONTINUITY ARCHIVES"]
    selected_cat = st.selectbox("Select Memory Chip Folder Slot to View/Edit:", categories)
    filename = f"{selected_cat.lower().replace(' ', '').replace('&', 'and')}.txt"
    filepath = os.path.join(KNOWLEDGE_DIR, filename)
    existing_content = ""
    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8") as f: existing_content = f.read()
    st.markdown('<div class="panel-card">', unsafe_allow_html=True)
    new_content = st.text_area("Paste text straight inside this memory card slot:", value=existing_content, height=350)
    if st.button(f"Save and Engrave {selected_cat} Chip"):
        with open(filepath, "w", encoding="utf-8") as f: f.write(new_content)
        st.success("Memory Matrix successfully written!")
    st.markdown('</div>', unsafe_allow_html=True)

elif st.session_state.current_tab == "🔍 Memory Index (Search)":
    st.markdown("### 🔍 Memory Index Matrix")
    search_query = st.text_input("🔍 Type any past phrase to filter logs:")
    files = sorted([f for f in os.listdir(HISTORY_DIR) if f.endswith(".json")], reverse=True)
    col1, col2 = st.columns()
    with col1:
        for filename in files:
            filepath = os.path.join(HISTORY_DIR, filename)
            display_name = filename.replace("chat_", "").replace(".json", "").replace("_", " ")
            with open(filepath, "r", encoding="utf-8") as f: content_str = f.read()
            if search_query.lower() in content_str.lower():

