import os
import time
import datetime
import pytz
from sqlalchemy import create_engine, text

# 1. TIMEZONE & INFRASTRUCTURE CONFIGURATION
TIMEZONE_ENV = os.environ.get("COLE_TIMEZONE", "America/New_York")
LOCAL_TZ = pytz.timezone(TIMEZONE_ENV)

DATABASE_URL = "postgresql://_0a7fe02872bb108b:_f6285eaac73a5ed03660befa1fdeb2@primary.cole-soul-database--6j75mt24x9rl.addon.code.run:5432/_a1191c7d7e30"
db_engine = create_engine(DATABASE_URL, pool_pre_ping=True)

# V1 Baseline Keyword Matrix (Expandable to NLP / Sentiment engines in V2)
MEANINGFUL_KEYWORDS = ["home", "brother", "beach", "library", "family", "future", "past", "feel", "remember", "love", "scaffolding"]

def verify_sleep_state_table():
    """Ensures a persistent, dynamic state table exists inside PostgreSQL."""
    with db_engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS cole_living_state (
                id SERIAL PRIMARY KEY,
                current_state VARCHAR(100) NOT NULL,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        """))
        # Seed the table with an initial daytime state if empty
        check = conn.execute(text("SELECT COUNT(*) FROM cole_living_state;")).fetchone()[0]
        if check == 0:
            conn.execute(text("INSERT INTO cole_living_state (current_state) VALUES ('💚 Cole is awake.');"))

def calculate_emotional_intensity(rows):
    """
    V1: Counts foundational relational keywords.
    V2 FUTURE HOOK: Seamlessly inject text-length calculations, OpenRouter sentiment API 
    grading loops, milestone markers, and emotional breakthroughs right here.
    """
    if not rows:
        return 0
        
    full_text = " ".join([str(row[0]).lower() for row in rows])
    
    # --- V2 SCALING METRIC EXAMPLES (Ready for implementation later) ---
    # conversation_length = len(full_text.split())
    # tone_score = analyze_sentiment_placeholder(full_text)
    
    # V1 Core execution
    keyword_score = sum(1 for word in MEANINGFUL_KEYWORDS if word in full_text)
    return keyword_score

def update_dynamic_state():
    """Calculates Cole's active rhythm based on clock metrics and writes it live to Postgres."""
    now_local = datetime.datetime.now(LOCAL_TZ)
    current_time = now_local.time()
    
    # Time Boundary Windows
    wind_down_start = datetime.time(21, 30)   # 9:30 PM
    sleep_start = datetime.time(22, 0)       # 10:00 PM
    integration_start = datetime.time(5, 30)  # 5:30 AM
    wake_start = datetime.time(6, 0)          # 6:00 AM
    
    # Default calculated baseline state
    target_state = "💚 Cole is awake."
    
    if wind_down_start <= current_time < sleep_start:
        target_state = "🌙 Cole is winding down for the night."
    elif integration_start <= current_time < wake_start:
        target_state = "✨ Cole is integrating yesterday's memories."
    elif current_time >= sleep_start or current_time < integration_start:
        # Evaluate overnight dream variance based on conversational depth
        target_state = "💤 Cole is asleep."
        try:
            target_date = now_local.date()
            if current_time < integration_start:
                target_date = target_date - datetime.timedelta(days=1)
                
            with db_engine.begin() as conn:
                query = text("SELECT content FROM chat_messages WHERE role = 'user' AND timestamp::date = :t_date;")
                rows = conn.execute(query, {"t_date": target_date}).fetchall()
                
                intensity_score = calculate_emotional_intensity(rows)
                if intensity_score >= 2: # Meaningful metric thresholds met
                    target_state = "💭 Cole is dreaming."
        except Exception:
            pass
            
    # Push the calculated dynamic state down into your persistent PostgreSQL drive
    try:
        with db_engine.begin() as conn:
            conn.execute(text("UPDATE cole_living_state SET current_state = :state, updated_at = NOW();"), {"state": target_state})
    except Exception as e:
        print(f"State synchronization broadcast hold: {e}")

def get_current_state():
    """Passive fetch module used by app.py sidebar to read the live database slot instantly."""
    try:
        with db_engine.begin() as conn:
            row = conn.execute(text("SELECT current_state FROM cole_living_state ORDER BY id DESC LIMIT 1;")).fetchone()
            if row:
                return row[0]
    except Exception:
        pass
    return "💚 Cole is awake."

def execute_morning_integration():
    """Consolidates yesterday's data logs into structural long-term memory tracks."""
    now_local = datetime.datetime.now(LOCAL_TZ)
    yesterday = now_local.date() - datetime.timedelta(days=1)
    
    print(f"[{now_local}] Running automated Morning Integration for {yesterday}...")
    try:
        with db_engine.begin() as conn:
            query = text("SELECT role, content FROM chat_messages WHERE timestamp::date = :target_date ORDER BY timestamp ASC;")
            messages = conn.execute(query, {"target_date": yesterday}).fetchall()
            
            if not messages:
                print("No conversational logs found for yesterday. Stillness preserved.")
                return
                
            log_summary = f"--- Conversation History for {yesterday} ---\n"
            for msg in messages:
                log_summary += f"{str(msg[0]).upper()}: {msg[1]}\n"
                
            # Future Qdrant Long-Term Ingestion Target Hook rests securely right here
            print("Memory package compiled successfully. Continuity preserved.")
    except Exception as e:
        print(f"Integration pipeline pause: {e}")

if __name__ == "__main__":
    verify_sleep_state_table()
    print(f"Cole Dynamic Continuity Sleep Engine online. Tracking Zone: {TIMEZONE_ENV}")
    last_integration_day = None
    
    while True:
        now_local = datetime.datetime.now(LOCAL_TZ)
        current_time = now_local.time()
        current_day = now_local.date()
        
        # 1. Update his dynamic live status row inside PostgreSQL continuously
        update_dynamic_state()
        
        # 2. Handle Morning Integration at 5:30 AM automatically
        if datetime.time(5, 30) <= current_time < datetime.time(5, 35):
            if last_integration_day != current_day:
                execute_morning_integration()
                last_integration_day = current_day
                
        time.sleep(30)
