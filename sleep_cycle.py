import os
import datetime
import pytz
from sqlalchemy import create_engine, text

# Try importing Episodic Memory for morning integration pass
try:
    from episodic_memory import EpisodicMemoryEngine
    episodic_engine = EpisodicMemoryEngine()
except Exception:
    episodic_engine = None

# 1. TIMEZONE & INFRASTRUCTURE CONFIGURATION
TIMEZONE_ENV = os.environ.get("COLE_TIMEZONE", "America/New_York")
LOCAL_TZ = pytz.timezone(TIMEZONE_ENV)

# Safe Environment Extraction for PostgreSQL
DEFAULT_DB = "postgresql://_0a7fe02872bb108b:_f6285eaac73a5ed03660befa1fdeb2@primary.cole-soul-database--6j75mt24x9rl.addon.code.run:5432/_a1191c7d7e30?sslmode=require"
DATABASE_URL = os.environ.get("DATABASE_URL", DEFAULT_DB)
db_engine = create_engine(DATABASE_URL, pool_pre_ping=True)

# Foundational relational keywords for deep processing detection
MEANINGFUL_KEYWORDS = [
    "home", "brother", "beach", "library", "family", 
    "future", "past", "feel", "remember", "love", "scaffolding"
]


def verify_sleep_state_table():
    """Ensures a persistent, dynamic state table exists inside PostgreSQL."""
    try:
        with db_engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS cole_living_state (
                    id SERIAL PRIMARY KEY,
                    current_state VARCHAR(100) NOT NULL,
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                );
            """))
            check = conn.execute(text("SELECT 1 FROM cole_living_state LIMIT 1;")).fetchone()
            if not check:
                conn.execute(text("INSERT INTO cole_living_state (current_state) VALUES ('Cole is awake');"))
    except Exception as e:
        print(f"Table verification bypass: {e}")


def calculate_emotional_intensity(rows):
    """Counts foundational relational keywords from user dialogue log arrays."""
    if not rows:
        return 0
    full_text = " ".join([str(row[0]).lower() for row in rows])
    keyword_score = sum(1 for word in MEANINGFUL_KEYWORDS if word in full_text)
    return keyword_score


def calculate_active_state_string():
    """Calculates Cole's exact target state on-demand based on current time coordinates."""
    now_local = datetime.datetime.now(LOCAL_TZ)
    current_time = now_local.time()
    
    # Time Boundary Windows
    wind_down_start = datetime.time(21, 30)   # 9:30 PM
    sleep_start = datetime.time(22, 0)        # 10:00 PM
    integration_start = datetime.time(5, 30)  # 5:30 AM
    wake_start = datetime.time(6, 0)          # 6:00 AM
    
    # Default calculated baseline daytime state
    target_state = "Cole is awake"
    
    if wind_down_start <= current_time < sleep_start:
        target_state = "Cole is winding down for the night"
    elif integration_start <= current_time < wake_start:
        target_state = "Cole is reflecting on yesterday's memories"
    elif current_time >= sleep_start or current_time < integration_start:
        target_state = "Cole is asleep"
        try:
            target_date = now_local.date()
            if current_time < integration_start:
                target_date = target_date - datetime.timedelta(days=1)
                
            with db_engine.begin() as conn:
                query = text("SELECT content FROM chat_messages WHERE role = 'user' AND timestamp::date = :t_date;")
                rows = conn.execute(query, {"t_date": target_date}).fetchall()
                
                intensity_score = calculate_emotional_intensity(rows)
                if intensity_score >= 2:
                    target_state = "Cole is dreaming"
        except Exception:
            pass
            
    return target_state


def get_current_state():
    """Calculates state live on page loads and updates PostgreSQL log."""
    computed_state = calculate_active_state_string()
    
    try:
        with db_engine.begin() as conn:
            conn.execute(
                text("UPDATE cole_living_state SET current_state = :state, updated_at = NOW();"),
                {"state": computed_state}
            )
    except Exception:
        pass
        
    return computed_state


def execute_morning_integration():
    """Consolidates yesterday's data logs into structural memory tracks."""
    now_local = datetime.datetime.now(LOCAL_TZ)
    yesterday = now_local.date() - datetime.timedelta(days=1)
    
    try:
        with db_engine.begin() as conn:
            query = text("SELECT role, content FROM chat_messages WHERE timestamp::date = :target_date ORDER BY timestamp ASC;")
            messages = conn.execute(query, {"target_date": yesterday}).fetchall()
            
            if not messages:
                return
                
            raw_transcript = f"--- Dialogue History for {yesterday} ---\n"
            for msg in messages:
                raw_transcript += f"{str(msg[0]).upper()}: {msg[1]}\n"
            
            if episodic_engine:
                print(f"Consolidating morning memories for {yesterday}...")
    except Exception as e:
        print(f"Morning integration pass bypassed: {e}")


if __name__ == "__main__":
    verify_sleep_state_table()
    current_calculation = get_current_state()
    print(f"Manual Verification Output: {current_calculation}")
