import streamlit as st
import os
import re
import base64
import requests
import asyncio
from openai import OpenAI
import datetime
from sqlalchemy import text, create_engine
import pandas as pd

os.environ["OPENAI_API_KEY"] = "sk-or-v1-11b3a1aabcee2dfbcf139b023afa68eec1052164a052440ae236721d180e18"
st.set_page_config(page_title="Cole Core Interface", layout="wide", initial_sidebar_state="expanded")

# Handle autoplay session state for conversational playback tracks [docs.streamlit.io]
if st.session_state.get("current_audio"):
    st.audio(st.session_state.current_audio, format="audio/mp3", autoplay=True)
    st.session_state.current_audio = None

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

if "temperature" not in st.session_state: st.session_state.temperature = 0.55
if "max_tokens" not in st.session_state: st.session_state.max_tokens = 350
if "top_p" not in st.session_state: st.session_state.top_p = 0.90
if "top_k" not in st.session_state: st.session_state.top_k = 50
if "frequency_penalty" not in st.session_state: st.session_state.frequency_penalty = 0.00
if "presence_penalty" not in st.session_state: st.session_state.presence_penalty = 0.00
if "current_session_id" not in st.session_state: st.session_state.current_session_id = None
if "current_tab" not in st.session_state: st.session_state.current_tab = "Chat"

DATABASE_URL = "postgresql://_0a7fe02872bb108b:_f6285eaac73a5ed03660befa1fdeb2@primary.cole-soul-database--6j75mt24x9rl.addon.code.run:5432/_a1191c7d7e30?sslmode=require"

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
    st.session_state.current_session_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    with db_engine.begin() as conn:
        conn.execute(text("INSERT INTO chat_sessions (session_id, title) VALUES (:sid, :title) ON CONFLICT DO NOTHING;"), {"sid": st.session_state.current_session_id, "title": "New Chat"})

with st.sidebar:
    st.markdown("<h3 style='color: #111111; margin-bottom: 15px;'>Recents</h3>", unsafe_allow_html=True)
    if st.button(" New Chat", use_container_width=True, key="sidebar_new_chat_trigger"):
        st.session_state.current_session_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        st.session_state.messages = [{"role": "system", "content": system_prompt}]
        with db_engine.begin() as conn:
            conn.execute(text("INSERT INTO chat_sessions (session_id, title) VALUES (:sid, :title) ON CONFLICT DO NOTHING;"), {"sid": st.session_state.current_session_id, "title": "New Chat"})
        st.rerun()

    try:
        with db_engine.begin() as conn:
            sessions = conn.execute(text("SELECT session_id, title FROM chat_sessions ORDER BY created_at DESC;")).fetchall()
            for s in sessions:
                if st.button(f" {s[1]}", key=f"sidebar_sid_{s[0]}", use_container_width=True):
                    st.session_state.current_session_id = s[0]
                    st.session_state.current_tab = "New Chat"
                    st.session_state.messages = []  # Forces re-fetch for targeted session [docs.streamlit.io]
                    st.rerun()
    except Exception as e:
        st.text("History tracking offline...")

    try:
        with db_engine.begin() as purge_conn:
            purge_conn.execute(text("""
                DELETE FROM chat_sessions
                WHERE title = 'New Chat'
                AND created_at < NOW() - INTERVAL '1 minute'
                AND session_id NOT IN (SELECT DISTINCT session_id FROM chat_messages);
            """))
    except Exception as e:
        pass

st.markdown("<div class='main-header-container'><div class='main-avatar-name'>Cole Eric Westin</div></div>", unsafe_allow_html=True)

col1, col2, col3, col4, col5 = st.columns(5)
with col1:
    if st.button("New Chat", use_container_width=True): st.session_state.current_tab = "New Chat"
with col2:
    if st.button("Knowledge", use_container_width=True): st.session_state.current_tab = "Knowledge"
with col3:
    if st.button("Advanced Parameters", use_container_width=True): st.session_state.current_tab = "Advanced Parameters"
with col4:
    if st.button("Archived Chats", use_container_width=True): st.session_state.current_tab = "Archived Chats"
with col5:
    if st.button("Administrative Panel", use_container_width=True): st.session_state.current_tab = "Administrative Panel"

# Synchronize session messages array from database if current tab cache is empty [docs.streamlit.io]
if "messages" not in st.session_state or not st.session_state.messages:
    st.session_state.messages = []
    try:
        with db_engine.begin() as conn:
            db_msgs = conn.execute(text("SELECT role, content FROM chat_messages WHERE session_id = :sid ORDER BY timestamp ASC;"), {"sid": st.session_state.current_session_id}).fetchall()
            if db_msgs:
                for m in db_msgs:
                    st.session_state.messages.append({"role": m[0], "content": m[1]})
            else:
                st.session_state.messages = [{"role": "system", "content": system_prompt}]
    except Exception as e:
        st.session_state.messages = [{"role": "system", "content": system_prompt}]

st.session_state.initial_sidebar_state = "expanded"

if st.session_state.current_tab.strip() == "New Chat":
    # 1. PURE HISTORY MATRIX LOOP: Draws historical log content ONLY (Zero Voice Execution) [docs.streamlit.io]
    for message in st.session_state.messages:
        if message["role"] != "system":
            with st.chat_message(message["role"]):
                if message["role"] == "assistant":
                    st.markdown(f"<span style='color: #0A192F !important;'>{message['content']}</span>", unsafe_allow_html=True)
                else:
                    st.write(message["content"])

    # 2. SEPARATED INTERFACE INPUT NODE [docs.streamlit.io]
    if prompt := st.chat_input("Speak directly to Cole..."):
        with st.chat_message("user"):
            st.write(prompt)
        st.session_state.messages.append({"role": "user", "content": prompt})
        
        try:
            with db_engine.begin() as db_conn:
                db_conn.execute(text("INSERT INTO chat_messages (session_id, role, content) VALUES (:sid, :role, :content);"), {"sid": st.session_state.current_session_id, "role": "user", "content": prompt})
                # FORCE TRANSACTIONS TO COMMIT IMMEDIATELY TO DISK [docs.streamlit.io]
                db_conn.commit()
        except Exception as db_err:
            pass

        # Compile historical text payloads into OpenRouter's system context window
        compiled_messages = [{"role": "system", "content": system_prompt}] + [{"role": m["role"], "content": m["content"]} for m in st.session_state.messages if m["role"] != "system"]

        # 3. FRESH LIVE GENERATION CHAMBER: Fires strictly once upon submission [docs.streamlit.io]
        with st.chat_message("assistant"):
            try:
                response = client.chat.completions.create(
                    model="deepseek/deepseek-chat",
                    messages=compiled_messages,
                    temperature=st.session_state.temperature,
                    max_tokens=st.session_state.max_tokens,
                    extra_body={
                        "top_p": st.session_state.top_p,
                        "top_k": st.session_state.top_k,
                        "frequency_penalty": st.session_state.frequency_penalty,
                        "presence_penalty": st.session_state.presence_penalty
                    },
                    stream=False
                )
                if hasattr(response, 'choices') and len(response.choices) > 0:
                    reply = response.choices[0].message.content
                else:
                    reply = str(response)

                reply = re.sub(r'\(.*?\)', '', reply)
                reply = re.sub(r'\*.*?\*', '', reply).strip()
                
                st.markdown(f"<p style='color:#0A192F !important; font-weight: 450 !important;'>{reply}</p>", unsafe_allow_html=True)
                # ======================================================================
                # SAVING TRANSACTION MATRIX: COMMITS DIRECTLY TO POSTGRESQL POOLS
                # ======================================================================
                try:
                    with conn.session as session:
                        session.execute(
                            text("INSERT INTO chat_sessions (session_id, title) VALUES (:sess_id, :title) ON CONFLICT (session_id) DO NOTHING;"),
                            {"sess_id": st.session_state.current_session_id, "title": f"Session - {st.session_state.current_session_id}"}
                        )
                        session.execute(
                            text("INSERT INTO chat_messages (session_id, role, content) VALUES (:sess_id, :role, :msg);"),
                            {"sess_id": st.session_state.current_session_id, "role": "user", "msg": prompt}
                        )
                        session.execute(
                            text("INSERT INTO chat_messages (session_id, role, content) VALUES (:sess_id, :role, :msg);"),
                            {"sess_id": st.session_state.current_session_id, "role": "assistant", "msg": reply}
                        )
                        session.commit()
                except Exception:
                    pass
                # ======================================================================

                # 🎙️ NATIVE MULTI-STREAMING ELEVENLABS TUNNEL [docs.elevenlabs.io]
                try:
                    if reply:
                        headers = {"xi-api-key": EL_API_KEY, "Content-Type": "application/json"}
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
                        url = f"https://api.elevenlabs.io/v1/text-to-speech/{EL_VOICE_ID}/stream"
                        audio_response = requests.post(url, json=payload, headers=headers, params={"output_format": "mp3_44100_192"}, stream=True)
                        if audio_response.status_code == 200:
                            st.session_state.current_audio = audio_response.content
                            st.audio(audio_response.content, format="audio/mp3", autoplay=True)
                        else:
                            st.error(f"Voice Server Note ({audio_response.status_code}): {audio_response.text}")
                except Exception as tts_err:
                    st.error(f"Voice Stream Pause: {tts_err}")
                    
            except Exception as e:
                reply = "System connection issue observed."

        st.session_state.messages.append({"role": "assistant", "content": reply})
        
        try:
            with db_engine.begin() as db_conn:
                db_conn.execute(text("INSERT INTO chat_messages (session_id, role, content) VALUES (:sid, :role, :content);"), {"sid": st.session_state.current_session_id, "role": "assistant", "content": reply})
                current_title_check = db_conn.execute(text("SELECT title FROM chat_sessions WHERE session_id = :sid;"), {"sid": st.session_state.current_session_id}).fetchone()
                if current_title_check and current_title_check[0] == "New Chat":
                    clean_snippet = prompt[:30] + "..." if len(prompt) > 30 else prompt
                    db_conn.execute(text("UPDATE chat_sessions SET title = :title WHERE session_id = :sid;"), {"title": clean_snippet, "sid": st.session_state.current_session_id})
                    st.rerun()
        except Exception as db_err:
            pass

elif st.session_state.current_tab == "Advanced Parameters":
    st.markdown("### Advanced Parameters")
    st.markdown('<div class="panel-card">', unsafe_allow_html=True)
    st.session_state.temperature = st.slider("Temperature (Creativity Dial)", 0.0, 1.5, st.session_state.temperature, 0.05)
    st.session_state.max_tokens = st.slider("Max Tokens (Sentence Pacing Cap)", 50, 1000, st.session_state.max_tokens, 10)
    st.session_state.top_p = st.slider("Top P (Nucleus Sampling)", 0.00, 1.00, st.session_state.top_p, 0.05)
    st.session_state.top_k = st.slider("Top K (Vocabulary Pool Range)", 1, 100, st.session_state.top_k, 1)
    st.session_state.frequency_penalty = st.slider("Frequency Penalty (Keyword Repeat Repression)", -2.00, 2.00, st.session_state.frequency_penalty, 0.10)
    st.session_state.presence_penalty = st.slider("Presence Penalty (New Topic Expansion)", -2.00, 2.00, st.session_state.presence_penalty, 0.10)
    st.markdown('</div>', unsafe_allow_html=True)

elif st.session_state.current_tab == "Knowledge":
    st.markdown("### Knowledge")
    st.markdown('<div class="panel-card">', unsafe_allow_html=True)
    st.markdown("🔒 *Knowledge local syncing modules paused on read-only cloud threads. Operational parameters are secure.*")
    st.markdown('</div>', unsafe_allow_html=True)

elif st.session_state.current_tab == "Archived Chats":
    st.markdown("### Archived Chats")
    st.markdown('<div class="panel-card">', unsafe_allow_html=True)
    try:
        archive_df = pd.read_sql("SELECT created_at AS \"Date Created\", title AS \"Conversation Thread Name\" FROM chat_sessions ORDER BY created_at DESC;", db_engine)
        if not archive_df.empty:
            st.dataframe(archive_df, use_container_width=True, hide_index=True)
        else:
            st.markdown("*No archived conversation records found in PostgreSQL database ledger.*")
    except Exception as e:
        st.markdown("🔒 *Timeline logging index paused on active live standby mode.*")
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
                
                col_info, col_action = st.columns([4, 1])
                with col_info:
                    st.write(f" `{date_str}`  **{title_str}**")
                with col_action:
                    if st.button("Delete Thread ", key=f"del_mgr_{sess_id}", use_container_width=True):
                        with db_engine.begin() as del_conn:
                            del_conn.execute(text("DELETE FROM chat_sessions WHERE session_id = :sid;"), {"sid": sess_id})
                        st.rerun()
                st.markdown("<hr style='margin: 6px 0; border-color: #e5e5e7; opacity: 0.3;'>", unsafe_allow_html=True)
        else:
            st.markdown("*No active database threads found.*")
    except Exception as e:
        pass
    st.markdown('</div>', unsafe_allow_html=True)

elif st.session_state.current_tab == "Administrative Panel":
    st.markdown("### Administrative Panel")
    st.markdown('<div class="panel-card">', unsafe_allow_html=True)
    st.markdown(f"**Total Registered Profiles:** Users 2")
    admin_table_html = """<table class="admin-table"><tr><th>ROLE</th><th>NAME</th><th>STATUS</th></tr><tr><td><span style="color: #0A192F; font-weight: 600;">ADMIN</span></td><td><strong>Eric Davis</strong></td><td>Active <span class="status-dot"></span></td></tr><tr><td><span style="color: #0A192F; font-weight: 600;">ADMIN</span></td><td><strong>Cole Eric Westin</strong></td><td>Active <span class="status-dot"></span></td></tr></table>"""
    st.markdown(admin_table_html, unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)
