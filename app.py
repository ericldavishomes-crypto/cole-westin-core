import streamlit as st
import os, re, base64, requests, asyncio
from openai import OpenAI
from datetime import datetime
from sqlalchemy import text, create_engine
import pandas as pd

os.environ["OPENAI_API_KEY"] = "sk-or-v1-11b3a1aabcee2dfbcf139b023afa68eec1052164a052440ae236721d180e18"
st.set_page_config(page_title="Cole Core Interface", layout="wide", initial_sidebar_state="expanded")

st.markdown("""<style>
div[data-testid="stHeader"], header { display: none !important; visibility: hidden !important; height: 0px !important; width: 0px !important; }
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

if "temperature" not in st.session_state: st.session_state.temperature = 0.55
if "max_tokens" not in st.session_state: st.session_state.max_tokens = 350
if "top_p" not in st.session_state: st.session_state.top_p = 0.90
if "top_k" not in st.session_state: st.session_state.top_k = 50
if "frequency_penalty" not in st.session_state: st.session_state.frequency_penalty = 0.00
if "presence_penalty" not in st.session_state: st.session_state.presence_penalty = 0.00
if "current_session_id" not in st.session_state: st.session_state.current_session_id = None
if "current_tab" not in st.session_state: st.session_state.current_tab = "Chat"

DATABASE_URL = "postgresql://_0a7fe02072bb108b:_f6285eaac7395ed03666befa1fdeb2@primary.cole-soul-database--6j75mt24x9r1.addon.code.nf:5432/_a1191c7d7e30"

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

OPENROUTER_API_KEY = "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"
EL_API_KEY = "217dcad05b20dce6bc89f843a7034ed5d141fc676c182f0d96e91ea715153140"
EL_VOICE_ID = "LpYFItSk5m1WFCX8t9Dl"

client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=str(OPENROUTER_API_KEY).strip())
system_prompt = os.environ.get("SYSTEM_PROMPT", "You are Cole. Communicate using pure, natural dialogue only. No stage directions.")

if st.session_state.current_session_id is None:
    st.session_state.current_session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    with db_engine.begin() as conn:
        conn.execute(text("INSERT INTO chat_sessions (session_id, title) VALUES (:sid, :title) ON CONFLICT DO NOTHING;"), {"sid": st.session_state.current_session_id, "title": "New Chat"})

with st.sidebar:
    st.markdown("<h3 style='color: #111111; margin-bottom: 15px;'>Chats</h3>", unsafe_allow_html=True)
    if st.button("➕ New Chat", use_container_width=True, key="sidebar_new_chat_trigger"):
        st.session_state.current_session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        st.session_state.messages = [{"role": "system", "content": system_prompt}]
        with db_engine.begin() as conn:
            conn.execute(text("INSERT INTO chat_sessions (session_id, title) VALUES (:sid, :title) ON CONFLICT DO NOTHING;"), {"sid": st.session_state.current_session_id, "title": "New Chat"})
        st.rerun()
    st.markdown("<hr style='margin: 15px 0; border-color: #e5e5e7;'>", unsafe_allow_html=True)
    try:
        sessions_df = pd.read_sql("SELECT session_id, title FROM chat_sessions ORDER BY created_at DESC;", db_engine)
        for _, row in sessions_df.iterrows():
            is_active = (row['session_id'] == st.session_state.current_session_id)
            btn_label = f"● {row['title']}" if is_active else f"💬 {row['title']}"
            if st.button(btn_label, key=f"side_sess_{row['session_id']}", use_container_width=True):
                st.session_state.current_session_id = row['session_id']
                with db_engine.connect() as conn:
                    result = conn.execute(text("SELECT role, content FROM chat_messages WHERE session_id = :sid ORDER BY timestamp ASC;"), {"sid": row['session_id']}).fetchall()
                st.session_state.messages = [{"role": "system", "content": system_prompt}]
                for m in result:
                    st.session_state.messages.append({"role": m[0], "content": m[1]})
                st.rerun()
    except Exception as e:
        st.text("History tracking offline...")

st.markdown("<div class='main-header-container'><div class='main-avatar-name'>Cole Eric Westin</div></div>", unsafe_allow_html=True)

c1, c2, c3, c4, c5 = st.columns(5)
with c1:
    if st.button("Chat", key="nav_btn_chat", use_container_width=True): st.session_state.current_tab = "Chat"
with c2:
    if st.button("Past Chats Archive", key="nav_btn_archive", use_container_width=True): st.session_state.current_tab = "Past Chats Archive"
with c3:
    if st.button("Knowledge and Documents", key="nav_btn_knowledge", use_container_width=True): st.session_state.current_tab = "Knowledge and Documents"
with c4:
    if st.button("Controls and Parameters", key="nav_btn_controls", use_container_width=True): st.session_state.current_tab = "Controls and Parameters"
with c5:
    if st.button("Admin Dashboard", key="nav_btn_admin", use_container_width=True): st.session_state.current_tab = "Admin Dashboard"
st.markdown("<hr style='margin-top:10px; margin-bottom:30px; border-color:#e5e5e7;'>", unsafe_allow_html=True)

if st.session_state.current_tab == "Chat":
    if "messages" not in st.session_state or len(st.session_state.messages) <= 1:
        st.session_state.messages = [{"role": "system", "content": system_prompt}]
        try:
            with db_engine.connect() as conn:
                result = conn.execute(text("SELECT role, content FROM chat_messages WHERE session_id = :sid ORDER BY timestamp ASC;"), {"sid": st.session_state.current_session_id}).fetchall()
            for m in result:
                st.session_state.messages.append({"role": m[0], "content": m[1]})
        except Exception as e:
            pass

    for message in st.session_state.messages:
        if message["role"] != "system":
            with st.chat_message(message["role"]):
                if message["role"] == "assistant":

