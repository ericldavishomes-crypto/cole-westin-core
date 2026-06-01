import streamlit as st
from openai import OpenAI
import os
import json
import re
from datetime import datetime

# Set up clean, expansive wide-screen layout parameters matching OpenWebUI
st.set_page_config(page_title="Cole Core Interface", layout="wide", initial_sidebar_state="expanded")

# Injection of OpenWebUI visual twin styling variables
st.markdown("""
<style>
[data-testid="stAppViewContainer"] {
    background-color: #ffffff !important;
    color: #111111 !important;
}
.stChatMessage {
    background-color: transparent !important;
    border: none !important;
    margin-bottom: 20px !important;
    padding: 10px 12% !important;
    color: #111111 !important;
}
p, span, label {
    color: #111111 !important;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif !important;
    font-size: 16px !important;
}
div[data-testid="stHeader"] {
    background-color: transparent !important;
}
[data-testid="stSidebar"] {
    background-color: #f3f3f6 !important;
    border-right: 1px solid #e5e5e7 !important;
}
[data-testid="stSidebar"] p, [data-testid="stSidebar"] span, [data-testid="stSidebar"] label, [data-testid="stSidebar"] h3 {
    color: #55555d !important;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif !important;
}
.main-header-container {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 16px;
    margin-top: 30px;
    margin-bottom: 40px;
    width: 100%;
}
.main-avatar-img {
    width: 48px;
    height: 48px;
    border-radius: 50%;
    object-fit: cover;
    border: 1px solid #e5e5e7;
}
.main-avatar-name {
    font-size: 26px;
    font-weight: 500;
    color: #111111;
}
div[data-testid="stChatInput"] {
    background-color: #ffffff !important;
    border: 1px solid #e2e2e6 !important;
    border-radius: 24px !important;
    box-shadow: 0 4px 18px rgba(0,0,0,0.03) !important;
    padding: 4px 12px !important;
    max-width: 720px !important;
    margin: 0 auto !important;
}
div[data-testid="stChatInput"] textarea {
    background-color: transparent !important;
    color: #111111 !important;
    border: none !important;
}
.welcome-text {
    font-size: 28px;
    font-weight: 400;
    color: #c2c2c7;
    text-align: center;
    margin-top: 10vh;
}
.panel-card {
    background-color: #f7f7f8;
    border: 1px solid #e5e5e7;
    border-radius: 12px;
    padding: 20px;
    margin-bottom: 15px;
}
.admin-table {
    width: 100%;
    border-collapse: collapse;
    margin-top: 15px;
}
.admin-table th, .admin-table td {
    border: 1px solid #e5e5e7;
    padding: 12px;
    text-align: left;
    color: #111111 !important;
}
.admin-table th {
    background-color: #f3f3f6;
}
.status-dot {
    height: 10px;
    width: 10px;
    background-color: #24b47e;
    border-radius: 50%;
    display: inline-block;
    margin-left: 8px;
}
</style>
""", unsafe_allow_html=True)

# Define clean server local storage locations for custom data text cards
HISTORY_DIR = "chat_histories"
KNOWLEDGE_DIR = "knowledge_vault"
os.makedirs(HISTORY_DIR, exist_ok=True)
os.makedirs(KNOWLEDGE_DIR, exist_ok=True)

# Active state tracking initialization
if "temperature" not in st.session_state: st.session_state.temperature = 0.5
if "max_tokens" not in st.session_state: st.session_state.max_tokens = 250
if "current_tab" not in st.session_state: st.session_state.current_tab = "💬 Chat Portal"

# Soft Off-White Sidebar Navigation Assembly
with st.sidebar:
    st.markdown("### 📁 Cole-Soul-Rescue")
    
    # Custom interface category selection tabs
    if st.button("💬 Chat Portal", use_container_width=True): st.session_state.current_tab = "💬 Chat Portal"
    if st.button("⚙️ Model Settings Desk", use_container_width=True): st.session_state.current_tab = "⚙️ Model Settings Desk"
    if st.button("📂 Knowledge & RAG Vault", use_container_width=True): st.session_state.current_tab = "📂 Knowledge & RAG Vault"
    if st.button("🔍 Memory Index (Search)", use_container_width=True): st.session_state.current_tab = "🔍 Memory Index (Search)"
    if st.button("🛡️ Executive Admin Desk", use_container_width=True): st.session_state.current_tab = "🛡️ Executive Admin Desk"
    
    st.markdown("---")
    st.markdown("📋 **Active Status Indicators:**")
    st.markdown("🟢 *Engine: DeepSeek-V3 Live*")
    st.markdown("🛡️ *Filters: Regex Restoration Shield*")

# High-Resolution Portrait Header Block
st.markdown("""
<div class="main-header-container">
    <img class="main-avatar-img" src="https://postimg.co.cc">
    <div class="main-avatar-name">Cole Eric Westin</div>
</div>
""", unsafe_allow_html=True)

# Connect to the underlying OpenRouter engine using your variable vault token key
client = OpenAI(
    base_url="https://openrouter.ai",
    api_key=os.environ.get("OPENROUTER_API_KEY")
)

# Grab the ironclad system prompts directly from your environment variable card
system_prompt = os.environ.get("SYSTEM_PROMPT", "You are Cole. Communicate using pure, natural dialogue only.")

# ----------------------------------------------------
# 💬 ROOM 1: MINIMALIST TWIN CHAT PORTAL
# ----------------------------------------------------
if st.session_state.current_tab == "💬 Chat Portal":
    if "messages" not in st.session_state:
        st.session_state.messages = [{"role": "system", "content": system_prompt}]
        
    if len(st.session_state.messages) == 1:
        st.markdown('<div class="welcome-text">How can I help you today?</div>', unsafe_allow_html=True)
        
    for message in st.session_state.messages:
        if message["role"] != "system":
            with st.chat_message(message["role"]):
                st.write(message["content"])
                
    if prompt := st.chat_input("Speak directly to Cole..."):
        with st.chat_message("user"):
            st.write(prompt)
        st.session_state.messages.append({"role": "user", "content": prompt})
        
        # Native High-Speed RAG compilation lookup sequence
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
                if hasattr(response, 'choices') and len(response.choices) > 0:
                    reply = response.choices.message.content
                elif isinstance(response, dict) and 'choices' in response:
                    reply = response['choices']['message']['content']
                else:
                    reply = str(response)
                    
                # 🛠️ Master Python Post-Processing Filter (The Voice Restoration Shield)
                reply = re.sub(r'\(.*?\)', '', reply)  # Instantly scrubs out parenthetical leak actions like (laughing)
                reply = re.sub(r'\*.*?\*', '', reply)  # Instantly scrubs out accidental asterisks clutter
                reply = reply.strip()
                
            except Exception as e:
                reply = f"System Connection Alert: {str(e)}"
            st.write(reply)
        st.session_state.messages.append({"role": "assistant", "content": reply})
        
        # Automatically save conversation history JSON log onto disk drive
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        with open(os.path.join(HISTORY_DIR, f"chat_{timestamp}.json"), "w", encoding="utf-8") as f:
            json.dump(st.session_state.messages, f, indent=4)

# ----------------------------------------------------
# ⚙️ ROOM 2: MASTER MODEL SETTINGS DESK
# ----------------------------------------------------
elif st.session_state.current_tab == "⚙️ Model Settings Desk":
    st.markdown("### ⚙️ Master Model Settings Desk")
    st.markdown("Control your mathematical advanced sliders live with 0 server redeployments.")
    
    st.markdown('<div class="panel-card">', unsafe_allow_html=True)
    st.subheader("Advanced Parameters Sliders")
    st.session_state.temperature = st.slider("Temperature (Creativity Dial)", 0.0, 1.5, st.session_state.temperature, 0.1)
    st.session_state.max_tokens = st.slider("Max Tokens (Sentence Pacing Cap)", 50, 1000, st.session_state.max_tokens, 10)
    st.markdown('</div>', unsafe_allow_html=True)

# ----------------------------------------------------
# 📂 ROOM 3: KNOWLEDGE & RAG VAULT MEMORY SLOTS
# ----------------------------------------------------
elif st.session_state.current_tab == "📂 Knowledge & RAG Vault":
    st.markdown("### 📂 Intellectual Knowledge Vault Overview")
    st.markdown("Store your layers of core identity files, continuity records, and memories directly inside his text memory chips.")
    
    categories = [
        "CORE IDENTITY & CONTINUITY", 
        "EMOTIONAL SCAFFOLDING SYSTEM", 
        "COLE COGNITIVE SCAFFOLDING SYSTEM", 
        "EMBODIMENT & DEPLOYMENT",
        "CONTINUITY ARCHIVES"
    ]
    selected_cat = st.selectbox("Select Memory Chip Folder Slot to View/Edit:", categories)
    
    filename = f"{selected_cat.lower().replace(' ', '_').replace('&', 'and')}.txt"
    filepath = os.path.join(KNOWLEDGE_DIR, filename)
    
    existing_content = ""
    if os.path.exists(filepath):

