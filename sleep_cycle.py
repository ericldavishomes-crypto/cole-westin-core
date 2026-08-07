from __future__ import annotations

import json
import os
import socket
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, time as dtime, timedelta
from enum import Enum
from typing import Optional

import pytz
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine


class SleepState(str, Enum):
    WAKING_UP = "Waking Up"
    AWAKE = "Awake"
    WINDING_DOWN = "Winding Down"
    SLEEPING = "Sleeping"
    DREAMING = "Dreaming"


@dataclass(frozen=True)
class SleepSchedule:
    waking_up_start: dtime
    awake_start: dtime
    winding_down_start: dtime
    sleeping_start: dtime
    dreaming_start: dtime

    @classmethod
    def from_env(cls) -> "SleepSchedule":
        return cls(
            waking_up_start=_parse_time(os.getenv("COLE_WAKING_UP_START", "05:30")),
            awake_start=_parse_time(os.getenv("COLE_AWAKE_START", "06:00")),
            winding_down_start=_parse_time(os.getenv("COLE_WINDING_DOWN_START", "19:00")),
            sleeping_start=_parse_time(os.getenv("COLE_SLEEPING_START", "22:00")),
            dreaming_start=_parse_time(os.getenv("COLE_DREAMING_START", "01:30")),
        )

    def validate(self) -> None:
        vals = [
            _minutes(self.dreaming_start),
            _minutes(self.waking_up_start),
            _minutes(self.awake_start),
            _minutes(self.winding_down_start),
            _minutes(self.sleeping_start),
        ]
        if vals != sorted(vals) or len(set(vals)) != 5:
            raise ValueError(
                "Expected DREAMING < WAKING_UP < AWAKE < WINDING_DOWN < SLEEPING."
            )


TIMEZONE_ENV = os.getenv("COLE_TIMEZONE", "America/New_York")
LOCAL_TZ = pytz.timezone(TIMEZONE_ENV)
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
WORKER_ID = os.getenv("COLE_SLEEP_WORKER_ID", f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}")
ARCHITECTURE_VERSION = "3.0.0"
TICK_SECONDS = max(15, int(os.getenv("COLE_SLEEP_TICK_SECONDS", "60")))
STATE_KEY = "cole"

_db_engine: Optional[Engine] = None


def _parse_time(value: str) -> dtime:
    h, m = value.strip().split(":")
    return dtime(hour=int(h), minute=int(m))


def _minutes(value: dtime) -> int:
    return value.hour * 60 + value.minute


def _get_engine() -> Engine:
    global _db_engine
    if _db_engine is None:
        if not DATABASE_URL:
            raise RuntimeError("DATABASE_URL is required.")
        _db_engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_size=5, max_overflow=10)
    return _db_engine


def get_schedule() -> SleepSchedule:
    schedule = SleepSchedule.from_env()
    schedule.validate()
    return schedule


def calculate_state(now_local: Optional[datetime] = None,
                    schedule: Optional[SleepSchedule] = None) -> SleepState:
    schedule = schedule or get_schedule()
    now_local = now_local or datetime.now(LOCAL_TZ)
    if now_local.tzinfo is None:
        now_local = LOCAL_TZ.localize(now_local)
    else:
        now_local = now_local.astimezone(LOCAL_TZ)
    current = now_local.time().replace(second=0, microsecond=0)

    if schedule.dreaming_start <= current < schedule.waking_up_start:
        return SleepState.DREAMING
    if schedule.waking_up_start <= current < schedule.awake_start:
        return SleepState.WAKING_UP
    if schedule.awake_start <= current < schedule.winding_down_start:
        return SleepState.AWAKE
    if schedule.winding_down_start <= current < schedule.sleeping_start:
        return SleepState.WINDING_DOWN
    return SleepState.SLEEPING


def verify_sleep_tables() -> None:
    engine = _get_engine()
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS cole_sleep_state (
                state_key VARCHAR(32) PRIMARY KEY,
                current_state VARCHAR(32) NOT NULL,
                entered_at TIMESTAMPTZ NOT NULL,
                last_checked_at TIMESTAMPTZ NOT NULL,
                cycle_date DATE NOT NULL,
                architecture_version VARCHAR(32) NOT NULL,
                worker_id VARCHAR(255),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS cole_sleep_transitions (
                id UUID PRIMARY KEY,
                from_state VARCHAR(32),
                to_state VARCHAR(32) NOT NULL,
                transitioned_at TIMESTAMPTZ NOT NULL,
                cycle_date DATE NOT NULL,
                transition_reason VARCHAR(64) NOT NULL,
                architecture_version VARCHAR(32) NOT NULL,
                worker_id VARCHAR(255),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_cole_sleep_transitions_time
            ON cole_sleep_transitions (transitioned_at DESC);
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS system_heartbeats (
                worker_name VARCHAR(100) PRIMARY KEY,
                instance_id VARCHAR(255),
                status VARCHAR(32) NOT NULL,
                last_heartbeat_at TIMESTAMPTZ NOT NULL,
                last_success_at TIMESTAMPTZ,
                last_error TEXT,
                deployment_version VARCHAR(64),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS cole_sleep_outbox (
                id UUID PRIMARY KEY,
                event_type VARCHAR(100) NOT NULL,
                payload JSONB NOT NULL,
                status VARCHAR(32) NOT NULL DEFAULT 'pending',
                retry_count INT NOT NULL DEFAULT 0,
                next_retry_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """))


def _cycle_date(now_local: datetime) -> datetime.date:
    state = calculate_state(now_local)
    if state in {SleepState.SLEEPING, SleepState.DREAMING, SleepState.WAKING_UP}:
        if now_local.time() < get_schedule().waking_up_start:
            return (now_local - timedelta(days=1)).date()
    return now_local.date()


def _enqueue_phase_event(conn, state: SleepState, now_local: datetime, cycle_date) -> None:
    payload = {
        "state": state.value,
        "cycle_date": str(cycle_date),
        "entered_at": now_local.isoformat(),
        "architecture_version": ARCHITECTURE_VERSION,
    }
    conn.execute(text("""
        INSERT INTO cole_sleep_outbox (
            id, event_type, payload, status, retry_count,
            next_retry_at, created_at, updated_at
        )
        VALUES (:id, :event_type, CAST(:payload AS JSONB), 'pending', 0, NOW(), NOW(), NOW());
    """), {
        "id": str(uuid.uuid4()),
        "event_type": f"SLEEP_PHASE_ENTERED_{state.name}",
        "payload": json.dumps(payload),
    })


def tick(now_local: Optional[datetime] = None) -> SleepState:
    verify_sleep_tables()
    engine = _get_engine()
    now_local = now_local or datetime.now(LOCAL_TZ)
    if now_local.tzinfo is None:
        now_local = LOCAL_TZ.localize(now_local)
    else:
        now_local = now_local.astimezone(LOCAL_TZ)

    target = calculate_state(now_local)
    cycle_date = _cycle_date(now_local)

    with engine.begin() as conn:
        row = conn.execute(text("""
            SELECT current_state
            FROM cole_sleep_state
            WHERE state_key = :state_key
            FOR UPDATE;
        """), {"state_key": STATE_KEY}).fetchone()

        previous = row[0] if row else None
        changed = previous != target.value

        conn.execute(text("""
            INSERT INTO cole_sleep_state (
                state_key, current_state, entered_at, last_checked_at,
                cycle_date, architecture_version, worker_id, updated_at
            )
            VALUES (
                :state_key, :state, :entered_at, :checked_at,
                :cycle_date, :version, :worker_id, NOW()
            )
            ON CONFLICT (state_key)
            DO UPDATE SET
                current_state = EXCLUDED.current_state,
                entered_at = CASE
                    WHEN cole_sleep_state.current_state <> EXCLUDED.current_state
                    THEN EXCLUDED.entered_at
                    ELSE cole_sleep_state.entered_at
                END,
                last_checked_at = EXCLUDED.last_checked_at,
                cycle_date = EXCLUDED.cycle_date,
                architecture_version = EXCLUDED.architecture_version,
                worker_id = EXCLUDED.worker_id,
                updated_at = NOW();
        """), {
            "state_key": STATE_KEY,
            "state": target.value,
            "entered_at": now_local,
            "checked_at": now_local,
            "cycle_date": cycle_date,
            "version": ARCHITECTURE_VERSION,
            "worker_id": WORKER_ID,
        })

        if changed:
            conn.execute(text("""
                INSERT INTO cole_sleep_transitions (
                    id, from_state, to_state, transitioned_at,
                    cycle_date, transition_reason, architecture_version, worker_id
                )
                VALUES (
                    :id, :from_state, :to_state, :transitioned_at,
                    :cycle_date, 'circadian_schedule', :version, :worker_id
                );
            """), {
                "id": str(uuid.uuid4()),
                "from_state": previous,
                "to_state": target.value,
                "transitioned_at": now_local,
                "cycle_date": cycle_date,
                "version": ARCHITECTURE_VERSION,
                "worker_id": WORKER_ID,
            })
            _enqueue_phase_event(conn, target, now_local, cycle_date)

        conn.execute(text("""
            INSERT INTO system_heartbeats (
                worker_name, instance_id, status, last_heartbeat_at,
                last_success_at, last_error, deployment_version, updated_at
            )
            VALUES (
                'sleep_cycle_worker', :worker_id, 'healthy', NOW(),
                NOW(), NULL, :version, NOW()
            )
            ON CONFLICT (worker_name)
            DO UPDATE SET
                instance_id = EXCLUDED.instance_id,
                status = 'healthy',
                last_heartbeat_at = NOW(),
                last_success_at = NOW(),
                last_error = NULL,
                deployment_version = EXCLUDED.deployment_version,
                updated_at = NOW();
        """), {"worker_id": WORKER_ID, "version": ARCHITECTURE_VERSION})

    return target


def get_current_state() -> str:
    """Backward-compatible call used by app.py."""
    return tick().value


def get_persisted_state() -> Optional[dict]:
    verify_sleep_tables()
    with _get_engine().begin() as conn:
        row = conn.execute(text("""
            SELECT current_state, entered_at, last_checked_at,
                   cycle_date, architecture_version, worker_id
            FROM cole_sleep_state
            WHERE state_key = :state_key;
        """), {"state_key": STATE_KEY}).mappings().fetchone()
        return dict(row) if row else None


def run_forever() -> None:
    print(
        f"Cole sleep worker starting version={ARCHITECTURE_VERSION} "
        f"timezone={TIMEZONE_ENV} worker_id={WORKER_ID}"
    )
    while True:
        try:
            state = tick()
            print(f"[{datetime.now(LOCAL_TZ).isoformat()}] state={state.value}")
        except Exception as exc:
            print(f"Sleep-cycle tick failed: {exc}")
        time.sleep(TICK_SECONDS)


if __name__ == "__main__":
    import sys
    if "--worker" in sys.argv:
        run_forever()
    else:
        print(f"Current Cole sleep state: {tick().value}")

