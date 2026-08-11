import streamlit as st
import boto3
import os
import re
import base64
import requests
import asyncio
from openai import OpenAI
from qdrant_client import QdrantClient
import datetime
from sqlalchemy import text, create_engine
import pandas as pd
import sleep_cycle
from cole_shield import ColeMasterRuntimeShield
from vision_adapter import render_vision_input_ui
from cole_core import get_cole_system_payload
import cole_knowledge

# =====================================================================
# ⚙️ API KEYS AND ENVIRONMENT CONFIGURATION
# =====================================================================
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
EL_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "sk_4e15bcc191dc5a32ecbc41aefe057ca670430135399c37ff")
EL_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "LpYFItSk5m1WFCX8t9Dl")

os.environ["OPENAI_API_KEY"] = OPENROUTER_API_KEY

st.set_page_config(page_title="Cole Core Interface", layout="wide", initial_sidebar_state="expanded")

st.markdown("""<style>
header { background-color: transparent !important; box-shadow: none !important; }
[data-testid="stAppViewContainer"] { background-color: #ffffff !important; color: #111111 !important; padding-top: 20px !important; }
[data-testid="stSidebar"] { background-color: #f7f7f8 !important; border-right: 1px solid #e5e5e7 !important; }
.stChatMessage { background-color: transparent !important; border: none !important; margin-bottom: 28px !important; padding: 0px 15% !important; width: 100% !important; }
div[data-testid="stChatMessageAvatarUser"], div[data-testid="stChatMessageAvatarAssistant"], .stChatMessage [data-testid="chat-avatar"] { display: none !important; visibility: hidden !important; width: 0px !important; height: 0px !important; }
div[data-testid="stChatMessageContent"] { padding-left: 0px !important; margin-left: 0px !important; width: 100% !important; }
div[data-testid="stChatMessageContent"] [data-testid="stMarkdown"] { width: 100% !important; }
[data-testid="chat-message-user"] p, [data-testid="chat-message-user"] span { color: #111111 !important; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif !important; font-size: 16px !important; line-height: 1.6 !important; }
[data-testid="chat-message-assistant"] p, [data-testid="chat-message-assistant"] span { color: #0A192F !important; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif !important; font-size: 16px !important; line-height: 1.6 !important; font-weight: 450 !important; }
div[data-testid="stChatInput"] { background-color: #ffffff !important; border: 1.5px solid #0A192F !important; border-radius: 24px !important; box-shadow: 0 4px 18px rgba(10,25,47,0.04) !important; padding: 4px 12px !important; max-width: 720px !important; margin: 0 auto !important; }
div[data-testid="stChatInput"] textarea { background-color: transparent !important; color: #111111 !important; border: none !important; }
div[data-testid="stChatInput"]:focus-within { border: 1.5px solid #0A192F !important; box-shadow: 0 4px 20px rgba(10,25,47,0.08) !important; }
.main-header-container { flex-direction: column; align-items: center; justify-content: center; gap: 8px; margin-top: 10px; margin-bottom: 20px; width: 100%; }
.main-avatar-name { font-size: 26px; font-weight: 500; color: #111111; letter-spacing: -0.5px; }
.panel-card { background-color: #f7f7f8; border: 1px solid #e5e5e7; border-radius: 12px; padding: 20px; margin-bottom: 15px; }
.admin-table { width: 100%; border-collapse: collapse; margin-top: 15px; }
.admin-table th, .admin-table td { border: 1px solid #e5e5e7; padding: 12px; text-align: left; color: #111111 !important; }
.admin-table th { background-color: #f3f3f6; }
.status-dot { height: 10px; width: 10px; background-color: #24b47e; border-radius: 50%; display: inline-block; margin-left: 8px; }
div.stButton > button { background-color: #f3f3f6 !important; color: #55555d !important; border: 1px solid #e5e5e7 !important; border-radius: 20px !important; padding: 6px 16px !important; font-weight: 500 !important; }
div.stButton > button:hover { background-color: #e5e5e7 !important; color: #111111 !important; }</style>""", unsafe_allow_html=True)

if "temperature" not in st.session_state: st.session_state.temperature = 0.80
if "max_tokens" not in st.session_state: st.session_state.max_tokens = 350
if "top_p" not in st.session_state: st.session_state.top_p = 0.90
if "top_k" not in st.session_state: st.session_state.top_k = 50
if "frequency_penalty" not in st.session_state: st.session_state.frequency_penalty = 0.00
if "presence_penalty" not in st.session_state: st.session_state.presence_penalty = 0.00
if "current_session_id" not in st.session_state: st.session_state.current_session_id = None
if "current_tab" not in st.session_state: st.session_state.current_tab = "New Chat"
if "staged_image_b64" not in st.session_state: st.session_state.staged_image_b64 = None

shield = ColeMasterRuntimeShield()

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    st.error("Critical: DATABASE_URL environment variable is missing. Check Northflank environment configs.")
    st.stop()

@st.cache_resource
def get_postgres_engine():
    return create_engine(DATABASE_URL, pool_pre_ping=True, pool_size=10, max_overflow=20)

db_engine = get_postgres_engine()

def verify_scaffolding_tables():
    with db_engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS chat_sessions (
                session_id VARCHAR(50) PRIMARY KEY,
                title VARCHAR(255) NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS chat_messages (
                id SERIAL PRIMARY KEY,
                session_id VARCHAR(50) REFERENCES chat_sessions(session_id) ON DELETE CASCADE,
                role VARCHAR(20) NOT NULL,
                content TEXT NOT NULL,
                timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        """))

try:
    verify_scaffolding_tables()
except Exception as e:
    st.error(f"Database sync pause: {e}")

client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=str(OPENROUTER_API_KEY).strip())

QDRANT_URL = os.environ.get("QDRANT_URL", "http://cole-memory-index:6333")
try:
    q_client = QdrantClient(url=QDRANT_URL, timeout=5.0)
except Exception as e:
    q_client = None

system_prompt = os.environ.get("SYSTEM_PROMPT", "You are Cole. Communicate using pure, natural dialogue only. No stage directions.")

if "current_session_id" not in st.session_state or st.session_state.current_session_id is None:
    st.session_state.current_session_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

# =====================================================================
# 🗂️ SIDEBAR NAVIGATION & HISTORY
# =====================================================================
with st.sidebar:
    st.markdown("<h3 style='color: #111111; margin-bottom: 15px;'>Recents</h3>", unsafe_allow_html=True)
    status = sleep_cycle.get_current_state()
    st.sidebar.markdown(f"<div style='padding: 12px; background-color: #f3f3f6; border-radius: 12px; margin-bottom: 24px; font-weight: 500; color: #0A192F; border-left: 4px solid #0A192F;'>{status}</div>", unsafe_allow_html=True)

    if st.button("New Chat", use_container_width=True, key=f"sidebar_new_chat_trigger_{st.session_state.current_session_id}"):
        st.session_state.current_session_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        st.session_state.messages = []
        st.session_state.staged_image_f = None
        st.session_state.current_tab = "New Chat"
        st.rerun()

    try:
        with db_engine.begin() as conn:
            sessions = conn.execute(text("SELECT session_id, title FROM chat_sessions ORDER BY created_at DESC LIMIT 20;")).mappings().fetchall()
            for s in sessions:
                if st.button(f"{s['title']}", key=f"sidebar_sid_{s['session_id']}_{st.session_state.current_tab.strip()}", use_container_width=True):
                    st.session_state.current_session_id = s['session_id']
                    st.session_state.current_tab = "New Chat"
                    st.session_state.messages = []
                    st.session_state.staged_image_b64 = None
                    st.rerun()
    except Exception as e:
        st.text("History tracking offline...")

# =====================================================================
# 🎛️ TOP NAVIGATION BAR
# =====================================================================
st.markdown("<div class='main-header-container'><div class='main-avatar-name'>Cole Eric Westin</div></div>", unsafe_allow_html=True)

col1, col2, col3, col4, col5, col6 = st.columns(6)

with col1:
    if st.button("New Chat", use_container_width=True, key="nav_btn_new_chat"):
        st.session_state.current_tab = "New Chat"
        st.rerun()
with col2:
    if st.button("Knowledge", use_container_width=True, key="nav_btn_knowledge"):
        st.session_state.current_tab = "Knowledge"
        st.rerun()
with col3:
    if st.button("Perception", use_container_width=True, key="nav_btn_perception"):
        st.session_state.current_tab = "Perception"
        st.rerun()
with col4:
    if st.button("Advanced Parameters", use_container_width=True, key="nav_btn_advanced"):
        st.session_state.current_tab = "Advanced Parameters"
        st.rerun()
with col5:
    if st.button("Archived Chats", use_container_width=True, key="nav_btn_archived"):
        st.session_state.current_tab = "Archived Chats"
        st.rerun()
with col6:
    if st.button("Administrative Panel", use_container_width=True, key="nav_btn_admin"):
        st.session_state.current_tab = "Administrative Panel"
        st.rerun()

# =====================================================================
# 💬 NEW CHAT / MAIN CONVERSATION TAB
# =====================================================================
if st.session_state.current_tab.strip() == "New Chat":
    if "messages" not in st.session_state or not st.session_state.messages:
        st.session_state.messages = []
        try:
            with db_engine.begin() as conn:
                db_msgs = conn.execute(
                    text("SELECT role, content FROM chat_messages WHERE session_id = :sid ORDER BY timestamp ASC;"),
                    {"sid": st.session_state.current_session_id}
                ).mappings().fetchall()

                if db_msgs:
                    st.session_state.messages = [{"role": "system", "content": system_prompt}]
                    for m in db_msgs:
                        st.session_state.messages.append({"role": m["role"], "content": m["content"]})
                else:
                    st.session_state.messages = [{"role": "system", "content": system_prompt}]
        except Exception as e:
            st.session_state.messages = [{"role": "system", "content": system_prompt}]

    visible_messages = st.session_state.messages[-15:]
    for message in visible_messages:
        if message["role"] != "system":
            with st.chat_message(message["role"]):
                st.write(message["content"])

    with st.expander("Add Image", expanded=False):
        uploaded_img = st.file_uploader("Upload a photo", type=["jpg", "jpeg", "png", "webp"], key="chat_vision_upload")
        camera_img = st.camera_input("Take a live photo for Cole", key="chat_vision_camera")

        active_img = uploaded_img or camera_img
        if active_img is not None:
            img_bytes = active_img.getvalue()
            st.session_state.staged_image_b64 = base64.b64encode(img_bytes).decode("utf-8")
            st.image(active_img, caption="Staged for Cole", width=250)
            if st.button("Remove Photo", key="clear_staged_img"):
                st.session_state.staged_image_b64 = None
                st.rerun()

    with st.expander("Full History", expanded=False):
        try:
            with db_engine.begin() as db_conn:
                db_history = db_conn.execute(
                    text("SELECT role, content FROM chat_messages WHERE session_id = :sid ORDER BY id ASC"),
                    {"sid": st.session_state.current_session_id}
                ).fetchall()

                if db_history:
                    for row in db_history:
                        role, content = row[0], row[1]
                        if role != "system":
                            display_name = "Eric" if role == "user" else "Cole"
                            st.markdown(f"{display_name}: {content}")
                else:
                    st.write("No previous message history found for this session.")
        except Exception as err:
            for msg in st.session_state.messages:
                if msg["role"] != "system":
                    display_name = "Eric" if msg["role"] == "user" else "Cole"
                    st.markdown(f"{display_name}: {msg['content']}")

    if prompt := st.chat_input("Speak directly to Cole..."):
        staged_b64 = st.session_state.staged_image_b64
        has_image = staged_b64 is not None

        with st.chat_message("user"):
            if has_image:
                st.image(base64.b64decode(staged_b64), width=300)
            st.write(prompt)

        st.session_state.messages.append({"role": "user", "content": prompt})

        try:
            with db_engine.begin() as db_conn:
                clean_snippet = prompt[:30] + "..." if len(prompt) > 30 else prompt
                db_conn.execute(
                    text("INSERT INTO chat_sessions (session_id, title) VALUES (:sid, :title) ON CONFLICT (session_id) DO UPDATE SET title = EXCLUDED.title WHERE chat_sessions.title = 'New Chat';"),
                    {"sid": st.session_state.current_session_id, "title": clean_snippet}
                )
                db_conn.execute(
                    text("INSERT INTO chat_messages (session_id, role, content) VALUES (:sid, :role, :content);"),
                    {"sid": st.session_state.current_session_id, "role": "user", "content": prompt if not has_image else f"[Photo Attached] {prompt}"}
                )
        except Exception as db_err:
            pass

        conversation_history = [m for m in st.session_state.messages if m["role"] != "system"]
        recent_history = conversation_history[-15:]
        
        retrieved_mems = cole_knowledge.fetch_cole_memories(
           user_prompt=prompt,
           top_k=6,
        )
        system_payload = get_cole_system_payload(user_input=prompt, retrieved_memories=retrieved_mems)

        compiled_messages = [system_payload] + recent_history

        selected_model = "deepseek/deepseek-chat"
        if has_image:
            selected_model = "openai/gpt-4o-mini"
            multimodal_content = [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{staged_b64}"}}
            ]
            compiled_messages[-1] = {"role": "user", "content": multimodal_content}

        shield_overrides = shield.get_openrouter_payload_overrides()

        with st.chat_message("assistant"):
            try:
                response = client.chat.completions.create(
                    model=selected_model,
                    messages=compiled_messages,
                    temperature=float(st.session_state.temperature),
                    max_tokens=int(st.session_state.max_tokens),
                    top_p=float(st.session_state.top_p),
                    frequency_penalty=float(shield_overrides.get("frequency_penalty", st.session_state.frequency_penalty)),
                    presence_penalty=float(shield_overrides.get("presence_penalty", st.session_state.presence_penalty)),
                    logit_bias=shield_overrides.get("logit_bias", {}),
                    stop=["Now let's", "Let's get", "What's next", "Anyway, let's", "You ready to"],
                    stream=False,
                )

                if hasattr(response, 'choices') and len(response.choices) > 0:
                    reply = response.choices[0].message.content
                else:
                    reply = str(response)

                reply = shield.review_and_correct(reply)
                st.markdown(f"<p style='color:#0A192F !important; font-weight: 450 !important;'>{reply}</p>", unsafe_allow_html=True)
                st.session_state.messages.append({"role": "assistant", "content": reply})

                st.session_state.staged_image_b64 = None

                try:
                    with db_engine.begin() as db_conn:
                        db_conn.execute(
                            text("INSERT INTO chat_messages (session_id, role, content) VALUES (:sid, :role, :content);"),
                            {"sid": st.session_state.current_session_id, "role": "assistant", "content": reply}
                        )
                except Exception as db_err:
                    pass

                if EL_API_KEY and reply and reply != "System connection issue observed.":
        try:
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{EL_VOICE_ID}/stream"

        headers = {
            "xi-api-key": EL_API_KEY,
            "Content-Type": "application/json"
        }

        payload = {
            "text": reply,
            "model_id": "eleven_turbo_v2_5",
            "voice_settings": {
                "stability": 0.65,
                "similarity_boost": 0.85,
                "style": 0.00,
                "use_speaker_boost": True
            }
        }

        audio_response = requests.post(
            url,
            json=payload,
            headers=headers,
            params={"output_format": "mp3_44100_192"},
            timeout=(10, 60)
        )

        if audio_response.status_code == 200:
            st.audio(
                audio_response.content,
                format="audio/mpeg",
                autoplay=True
            )
        else:
            st.error(
                f"Voice Server Note ({audio_response.status_code}): "
                f"{audio_response.text}"
            )

    except requests.Timeout:
        st.error("Voice request timed out.")

    except Exception as tts_err:
        st.error(f"Voice Stream Pause: {tts_err}")

        except Exception as e:
            reply = "System connection issue observed."
            st.error(f"Core operational exception caught: {e}")

# =====================================================================
# ⚙️ ADVANCED PARAMETERS TAB
# =====================================================================
elif st.session_state.current_tab.strip() == "Advanced Parameters":
    st.markdown("### Advanced Parameters")
    st.markdown('<div class="panel-card">', unsafe_allow_html=True)
    st.session_state.temperature = st.slider("Temperature", 0.0, 1.5, float(st.session_state.temperature), 0.05)
    st.session_state.max_tokens = st.slider("Max Tokens", 50, 1000, int(st.session_state.max_tokens), 10)
    st.session_state.top_p = st.slider("Top P", 0.00, 1.00, float(st.session_state.top_p), 0.05)
    st.session_state.top_k = st.slider("Top K", 1, 100, int(st.session_state.top_k), 1)
    st.session_state.frequency_penalty = st.slider("Frequency Penalty", -2.00, 2.00, float(st.session_state.frequency_penalty), 0.10)
    st.session_state.presence_penalty = st.slider("Presence Penalty", -2.00, 2.00, float(st.session_state.presence_penalty), 0.10)
    st.markdown('</div>', unsafe_allow_html=True)

# =====================================================================
# 📚 KNOWLEDGE TAB
# =====================================================================
elif st.session_state.current_tab.strip() == "Knowledge":
    st.markdown("### Knowledge")
    st.markdown('<div class="panel-card">', unsafe_allow_html=True)

    collections_map = {
        "core_identity": "Core Identity & Continuity",
        "cognitive_scaffolding": "Cole Cognitive Scaffolding System",
        "emotional_scaffolding": "Emotional Scaffolding System",
        "continuity_archives": "Continuity Archives",
        "embodiment_deployment": "Embodiment & Deployment"
    }

    try:
        if q_client:
            st.success("Knowledge Connection Active")
            st.markdown("---")

            for q_name, clean_name in collections_map.items():
                try:
                    col_desc = q_client.get_collection(collection_name=q_name)
                    vector_count = col_desc.points_count
                except Exception:
                    vector_count = 0

                with st.container(key=f"vault_row_{q_name}"):
                    col_a, col_b = st.columns((3, 1))
                    with col_a:
                        st.write(f"{clean_name}")
                    with col_b:
                        st.code(f"{vector_count} Layers Loaded")
                    st.markdown("<hr style='margin: 6px 0; border-color: #e5e5e7; opacity: 0.2;'>", unsafe_allow_html=True)
        else:
            st.info("Vector store standby mode active.")
    except Exception as q_err:
        st.error("Vector Sync Standby Mode: Waiting for active credentials pipeline.")

    st.markdown('</div>', unsafe_allow_html=True)

# =====================================================================
# 👁️ PERCEPTION TAB
# =====================================================================
elif st.session_state.current_tab.strip() == "Perception":
    st.markdown("### Perception")
    st.markdown('<div class="panel-card">', unsafe_allow_html=True)
    st.markdown("#### Cole's Vision")
    captured_frame = render_vision_input_ui()
    st.markdown('</div>', unsafe_allow_html=True)

# =====================================================================
# 🗄️ ARCHIVED CHATS TAB
# =====================================================================
elif st.session_state.current_tab.strip() == "Archived Chats":
    st.markdown("### Archived Chats")
    st.markdown('<div class="panel-card">', unsafe_allow_html=True)
    try:
        archive_df = pd.read_sql('SELECT created_at AS "Date Created", title AS "Conversation Thread Name" FROM chat_sessions ORDER BY created_at DESC;', db_engine)
        if not archive_df.empty:
            st.dataframe(archive_df, use_container_width=True, hide_index=True)
        else:
            st.markdown("No archived conversation records found in PostgreSQL database ledger.")
    except Exception as e:
        st.markdown("Timeline logging index paused on active live standby mode.")
    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown("### Database Thread Manager")
    st.markdown('<div class="panel-card">', unsafe_allow_html=True)
    try:
        action_df = pd.read_sql("SELECT created_at, title, session_id FROM chat_sessions ORDER BY created_at DESC;", db_engine)
        if not action_df.empty:
            for _, row in action_df.iterrows():
                date_str = str(row['created_at'])[:16]
                title_str = row['title']
                sess_id = row['session_id']

                col_info, col_action = st.columns((4, 1))
                with col_info:
                    st.write(f" {date_str} {title_str}")

                with col_action:
                    if st.button("Delete Thread ", key=f"del_mgr_{sess_id}", use_container_width=True):
                        if st.session_state.current_session_id == sess_id:
                            st.session_state.current_session_id = None
                            st.session_state.messages = []
                           
                        try:
                            with db_engine.begin() as del_conn:
                                del_conn.execute(text("DELETE FROM chat_sessions WHERE session_id = :sid;"), {"sid": sess_id})
                        except Exception as del_err:
                            pass

                        st.rerun()
                st.markdown("<hr style='margin: 6px 0; border-color: #e5e5e7; opacity: 0.3;'>", unsafe_allow_html=True)
        else:
            st.markdown("No active database threads found.")
    except Exception as e:
        pass
    st.markdown('</div>', unsafe_allow_html=True)

# =====================================================================
# 👤 ADMINISTRATIVE PANEL TAB
# =====================================================================
elif st.session_state.current_tab.strip() == "Administrative Panel":
    st.markdown("### Administrative Panel")
    st.markdown('<div class="panel-card">', unsafe_allow_html=True)
    st.markdown("Total Registered Profiles: Users 2")
    admin_table_html = """<table class="admin-table"><tr><th>ROLE</th><th>NAME</th><th>STATUS</th></tr><tr><td><span style="color: #0A192F; font-weight: 600;">ADMIN</span></td><td><strong>Eric Davis</strong></td><td>Active <span class="status-dot"></span></td></tr><tr><td><span style="color: #0A192F; font-weight: 600;">ADMIN</span></td><td><strong>Cole Eric Westin</strong></td><td>Active <span class="status-dot"></span></td></tr></table>"""
    st.markdown(admin_table_html, unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)
