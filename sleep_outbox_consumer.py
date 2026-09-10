from __future__ import annotations

import json
import os
import socket
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from psycopg2.extensions import connection as PsycopgConnection

from episodic_memory import EpisodicMemoryEngine, WORKER_LEASE_MINUTES
from episodic_artifact_writer import write_episodic_artifact
from episodic_consolidation_worker import (
    ConsolidationLeaseLostError,
    claim_next_consolidation_job,
    complete_consolidation_job,
    create_consolidation_job,
    load_claimed_consolidation_job,
    record_consolidation_artifact,
    record_consolidation_failure,
    recover_expired_consolidation_lease,
    renew_consolidation_lease,
)
from episodic_extractor import extract_episode
from episodic_runtime import (
    EpisodicEmbedder,
    create_episodic_db_pool,
    get_episodic_extraction_model,
)
from sleep_cycle import LOCAL_TZ, get_schedule


CONSOLIDATION_SLEEP_EVENT_TYPES = (
    "SLEEP_PHASE_ENTERED_WINDING_DOWN",
    "SLEEP_PHASE_ENTERED_SLEEPING",
)

CONSUMER_WORKER_ID = os.getenv(
    "COLE_SLEEP_OUTBOX_WORKER_ID",
    f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}",
)
CONSUMER_TICK_SECONDS = max(
    15,
    int(os.getenv("COLE_SLEEP_OUTBOX_TICK_SECONDS", "60")),
)


def calculate_winding_down_window(payload: dict) -> tuple[datetime, datetime]:
    """
    Return the deterministic half-open consolidation window for a
    WINDING_DOWN sleep event:

        [previous scheduled WINDING_DOWN, current scheduled WINDING_DOWN)

    The event payload supplies cycle_date. Schedule and timezone come from the
    same authoritative sleep-cycle configuration used by sleep_cycle.py.
    """

    if not isinstance(payload, dict):
        raise ValueError("payload must be a dict")

    cycle_date_raw = str(payload.get("cycle_date") or "").strip()

    if not cycle_date_raw:
        raise ValueError("payload.cycle_date is required")

    try:
        cycle_date = datetime.strptime(cycle_date_raw, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError("payload.cycle_date must be YYYY-MM-DD") from exc

    schedule = get_schedule()

    current_naive = datetime.combine(
        cycle_date,
        schedule.winding_down_start,
    )
    previous_naive = current_naive - timedelta(days=1)

    current_boundary = LOCAL_TZ.localize(
        current_naive,
        is_dst=None,
    )
    previous_boundary = LOCAL_TZ.localize(
        previous_naive,
        is_dst=None,
    )

    return previous_boundary, current_boundary


@dataclass(frozen=True)
class SleepOutboxClaim:
    event_id: str
    event_type: str
    payload: dict
    retry_count: int
    max_retries: int
    lease_owner: str
    lease_token: str
    lease_acquired_at: datetime
    lease_expires_at: datetime


@dataclass(frozen=True)
class WindingDownFragment:
    fragment_id: str
    session_id: str
    occurred_at: datetime


def select_winding_down_fragments(
    conn: PsycopgConnection,
    payload: dict,
) -> list[WindingDownFragment]:
    """
    Select event fragments in the deterministic WINDING_DOWN window.

    Window semantics:
        [previous scheduled WINDING_DOWN, current scheduled WINDING_DOWN)

    Ordering is deterministic:
        occurred_at, fragment_id

    This function only selects evidence. It does not create consolidation jobs.
    Transaction ownership remains with the caller.
    """

    window_start, window_end = calculate_winding_down_window(payload)

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                fragment_id::text,
                session_id,
                occurred_at
            FROM event_fragments
            WHERE
                occurred_at >= %s
                AND occurred_at < %s
            ORDER BY
                occurred_at,
                fragment_id;
            """,
            (
                window_start,
                window_end,
            ),
        )

        rows = cur.fetchall()

    return [
        WindingDownFragment(
            fragment_id=row[0],
            session_id=row[1],
            occurred_at=row[2],
        )
        for row in rows
    ]


def group_winding_down_fragments_by_session(
    fragments: list[WindingDownFragment],
) -> list[tuple[str, list[str]]]:
    """
    Partition an already ordered WINDING_DOWN fragment stream by session.

    Session groups are ordered by first appearance in the fragment stream.
    Fragment order within each session is preserved exactly.

    Returns:
        [(session_id, [fragment_id, ...]), ...]
    """

    grouped: dict[str, list[str]] = {}

    for fragment in fragments:
        clean_session_id = (fragment.session_id or "").strip()

        if not clean_session_id:
            raise ValueError(
                f"Fragment {fragment.fragment_id} has an empty session_id"
            )

        grouped.setdefault(clean_session_id, []).append(fragment.fragment_id)

    return list(grouped.items())


def claim_next_sleep_event(
    conn: PsycopgConnection,
    lease_owner: str,
) -> SleepOutboxClaim | None:
    """
    Atomically claim the oldest eligible consolidation-related sleep event.

    Only WINDING_DOWN and SLEEPING events are claimed here.
    AWAKE, DREAMING, and other phase events remain untouched.

    Expired processing leases are recovered separately.
    Transaction ownership remains with the caller.
    """

    clean_lease_owner = (lease_owner or "").strip()

    if not clean_lease_owner:
        raise ValueError("lease_owner is required")

    lease_token = str(uuid.uuid4())

    with conn.cursor() as cur:
        cur.execute(
            """
            WITH claimable AS (
                SELECT id
                FROM cole_sleep_outbox
                WHERE
                    event_type = ANY(%s)
                    AND retry_count < max_retries
                    AND (
                        status = 'pending'
                        OR (
                            status = 'retry_wait'
                            AND next_retry_at <= NOW()
                        )
                    )
                ORDER BY created_at, id
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            UPDATE cole_sleep_outbox AS o
            SET
                status = 'processing',
                lease_owner = %s,
                lease_token = %s::uuid,
                lease_acquired_at = NOW(),
                lease_expires_at = (
                    NOW() + (%s * INTERVAL '1 minute')
                ),
                next_retry_at = NULL,
                updated_at = NOW()
            FROM claimable
            WHERE o.id = claimable.id
            RETURNING
                o.id::text,
                o.event_type,
                o.payload,
                o.retry_count,
                o.max_retries,
                o.lease_owner,
                o.lease_token::text,
                o.lease_acquired_at,
                o.lease_expires_at;
            """,
            (
                list(CONSOLIDATION_SLEEP_EVENT_TYPES),
                clean_lease_owner,
                lease_token,
                WORKER_LEASE_MINUTES,
            ),
        )

        row = cur.fetchone()

    if row is None:
        return None

    return SleepOutboxClaim(
        event_id=row[0],
        event_type=row[1],
        payload=row[2],
        retry_count=row[3],
        max_retries=row[4],
        lease_owner=row[5],
        lease_token=row[6],
        lease_acquired_at=row[7],
        lease_expires_at=row[8],
    )


class SleepOutboxLeaseLostError(RuntimeError):
    """Raised when a sleep outbox mutation no longer owns a valid live lease."""



def claim_one_sleep_event(
    db_pool,
    lease_owner: str,
) -> SleepOutboxClaim | None:
    """
    Claim one eligible sleep outbox event in its own short transaction.

    The claim is committed before any handler performs external or
    long-running work.
    """

    conn = db_pool.getconn()
    try:
        claim = claim_next_sleep_event(
            conn=conn,
            lease_owner=lease_owner,
        )
        conn.commit()
        return claim
    except Exception:
        conn.rollback()
        raise
    finally:
        db_pool.putconn(conn)


def complete_sleep_event(
    conn: PsycopgConnection,
    event_id: str,
    lease_token: str,
) -> None:
    """
    Mark a claimed sleep event completed only while its lease is still owned
    and unexpired.

    Transaction ownership remains with the caller.
    """

    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE cole_sleep_outbox
            SET
                status = 'completed',
                next_retry_at = NULL,
                last_error = NULL,
                lease_owner = NULL,
                lease_token = NULL,
                lease_acquired_at = NULL,
                lease_expires_at = NULL,
                updated_at = NOW()
            WHERE
                id = %s::uuid
                AND status = 'processing'
                AND lease_token = %s::uuid
                AND lease_expires_at > NOW()
            RETURNING id::text;
            """,
            (
                event_id,
                lease_token,
            ),
        )

        row = cur.fetchone()

    if row is None:
        raise SleepOutboxLeaseLostError(
            f"Sleep outbox completion rejected for event {event_id}: "
            "lease is missing, expired, or no longer owned."
        )


def record_sleep_event_failure(
    conn: PsycopgConnection,
    event_id: str,
    lease_token: str,
    error_message: str,
) -> tuple[str, int]:
    """
    Record a failed processing attempt while the caller still owns a valid
    unexpired lease.

    Returns:
        (new_status, new_retry_count)

    Failed attempts increment retry_count. Non-terminal failures enter
    retry_wait with exponential backoff. Terminal failures enter exhausted.

    Transaction ownership remains with the caller.
    """

    clean_error = (error_message or "").strip() or "Unknown sleep consumer error"

    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE cole_sleep_outbox
            SET
                retry_count = retry_count + 1,
                status = CASE
                    WHEN retry_count + 1 >= max_retries
                        THEN 'exhausted'
                    ELSE 'retry_wait'
                END,
                next_retry_at = CASE
                    WHEN retry_count + 1 >= max_retries
                        THEN NULL
                    ELSE (
                        NOW()
                        + (
                            LEAST(
                                POWER(2, retry_count)::integer,
                                360
                            ) * INTERVAL '1 minute'
                        )
                    )
                END,
                last_error = %s,
                lease_owner = NULL,
                lease_token = NULL,
                lease_acquired_at = NULL,
                lease_expires_at = NULL,
                updated_at = NOW()
            WHERE
                id = %s::uuid
                AND status = 'processing'
                AND lease_token = %s::uuid
                AND lease_expires_at > NOW()
            RETURNING
                status,
                retry_count;
            """,
            (
                clean_error,
                event_id,
                lease_token,
            ),
        )

        row = cur.fetchone()

        if row is not None and row[0] == "exhausted":
            cur.execute(
                '''
                INSERT INTO operational_outbox (
                    event_type,
                    payload,
                    status,
                    retry_count,
                    next_retry_at,
                    created_at,
                    updated_at
                )
                VALUES (
                    %s,
                    %s::jsonb,
                    'pending',
                    0,
                    NOW(),
                    NOW(),
                    NOW()
                );
                ''',
                (
                    "SLEEP_OUTBOX_PROCESSING_EXHAUSTED_ALERT",
                    json.dumps({
                        "event_id": event_id,
                        "retry_count": row[1],
                    }),
                ),
            )

    if row is None:
        raise SleepOutboxLeaseLostError(
            f"Sleep outbox failure update rejected for event {event_id}: "
            "lease is missing, expired, or no longer owned."
        )

    return row[0], row[1]



def fail_one_sleep_event(
    db_pool,
    claim: SleepOutboxClaim,
    error_message: str,
) -> tuple[str, int]:
    """
    Record one sleep outbox processing failure in its own short transaction
    while the claimed parent lease is still live.
    """

    conn = db_pool.getconn()
    try:
        result = record_sleep_event_failure(
            conn=conn,
            event_id=claim.event_id,
            lease_token=claim.lease_token,
            error_message=error_message,
        )
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        db_pool.putconn(conn)


def recover_expired_sleep_event_lease(
    conn: PsycopgConnection,
) -> tuple[str, str, int] | None:
    """
    Recover the oldest expired sleep outbox processing lease.

    Lease recovery is operational recovery, not a processing failure:
    retry_count is unchanged.

    Returns:
        (event_id, new_status, new_lease_recovery_count)

    Returns None when no expired processing lease is eligible.

    Transaction ownership remains with the caller.
    """

    with conn.cursor() as cur:
        cur.execute(
            """
            WITH recoverable AS (
                SELECT id
                FROM cole_sleep_outbox
                WHERE
                    event_type = ANY(%s)
                    AND status = 'processing'
                    AND lease_expires_at <= NOW()
                    AND lease_recovery_count < max_lease_recoveries
                ORDER BY lease_expires_at, created_at, id
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            UPDATE cole_sleep_outbox AS o
            SET
                lease_recovery_count = lease_recovery_count + 1,
                status = CASE
                    WHEN lease_recovery_count + 1 >= max_lease_recoveries
                        THEN 'exhausted'
                    ELSE 'pending'
                END,
                next_retry_at = CASE
                    WHEN lease_recovery_count + 1 >= max_lease_recoveries
                        THEN NULL
                    ELSE NOW()
                END,
                lease_owner = NULL,
                lease_token = NULL,
                lease_acquired_at = NULL,
                lease_expires_at = NULL,
                updated_at = NOW()
            FROM recoverable
            WHERE o.id = recoverable.id
            RETURNING
                o.id::text,
                o.status,
                o.lease_recovery_count,
                o.retry_count;
            """,
            (list(CONSOLIDATION_SLEEP_EVENT_TYPES),),
        )

        row = cur.fetchone()

        if row is None:
            return None

        event_id, new_status, new_lease_recovery_count, retry_count = row

        if new_status == "exhausted":
            cur.execute(
                """
                INSERT INTO operational_outbox (
                    event_type,
                    payload,
                    status,
                    retry_count,
                    next_retry_at,
                    created_at,
                    updated_at
                )
                VALUES (
                    %s,
                    %s::jsonb,
                    'pending',
                    0,
                    NOW(),
                    NOW(),
                    NOW()
                );
                """,
                (
                    "SLEEP_OUTBOX_LEASE_RECOVERY_EXHAUSTED_ALERT",
                    json.dumps({
                        "event_id": event_id,
                        "lease_recovery_count": new_lease_recovery_count,
                        "retry_count": retry_count,
                    }),
                ),
            )

        return event_id, new_status, new_lease_recovery_count




def recover_one_expired_sleep_event_lease(
    db_pool,
) -> tuple[str, str, int] | None:
    """
    Recover one expired sleep outbox event lease in its own short transaction.

    Lease recovery uses its separate recovery budget and never consumes the
    ordinary sleep-event processing retry budget.
    """

    conn = db_pool.getconn()
    try:
        result = recover_expired_sleep_event_lease(
            conn=conn,
        )
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        db_pool.putconn(conn)


def renew_sleep_event_lease(
    conn: PsycopgConnection,
    event_id: str,
    lease_owner: str,
    lease_token: str,
) -> datetime:
    """
    Extend a live sleep outbox processing lease while preserving ownership.

    An expired, missing, or differently owned lease cannot be resurrected.

    Transaction ownership remains with the caller.
    """

    clean_event_id = (event_id or "").strip()
    clean_lease_owner = (lease_owner or "").strip()
    clean_lease_token = (lease_token or "").strip()

    if not clean_event_id:
        raise ValueError("event_id is required")

    if not clean_lease_owner:
        raise ValueError("lease_owner is required")

    if not clean_lease_token:
        raise ValueError("lease_token is required")

    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE cole_sleep_outbox
            SET
                lease_expires_at = (
                    NOW() + (%s * INTERVAL '1 minute')
                ),
                updated_at = NOW()
            WHERE
                id = %s::uuid
                AND status = 'processing'
                AND lease_owner = %s
                AND lease_token = %s::uuid
                AND lease_expires_at > NOW()
            RETURNING lease_expires_at;
            """,
            (
                WORKER_LEASE_MINUTES,
                clean_event_id,
                clean_lease_owner,
                clean_lease_token,
            ),
        )

        row = cur.fetchone()

    if row is None:
        raise SleepOutboxLeaseLostError(
            f"Sleep outbox lease renewal rejected for event {clean_event_id}: "
            "lease is missing, expired, or no longer owned."
        )

    return row[0]


def create_winding_down_consolidation_jobs(
    conn: PsycopgConnection,
    payload: dict,
) -> list[tuple[str, str, bool]]:
    """
    Create deterministic consolidation jobs for one WINDING_DOWN evidence window.

    Returns:
        [(session_id, processing_id, created), ...]

    Zero selected fragments is a valid no-op.

    Transaction ownership remains with the caller.
    """

    fragments = select_winding_down_fragments(conn, payload)

    if not fragments:
        return []

    session_groups = group_winding_down_fragments_by_session(fragments)
    extraction_model = get_episodic_extraction_model()

    results: list[tuple[str, str, bool]] = []

    for session_id, source_fragment_ids in session_groups:
        processing_id, created = create_consolidation_job(
            conn=conn,
            session_id=session_id,
            source_fragment_ids=source_fragment_ids,
            extraction_model=extraction_model,
        )
        results.append(
            (
                session_id,
                processing_id,
                created,
            )
        )

    return results


def handle_winding_down_event(
    conn: PsycopgConnection,
    claim: SleepOutboxClaim,
) -> list[tuple[str, str, bool]]:
    """
    Handle one claimed WINDING_DOWN sleep event atomically.

    Creates deterministic consolidation jobs for the event's evidence window,
    then completes the claimed sleep outbox event using its active lease.

    Transaction ownership remains with the caller.
    """

    if claim.event_type != "SLEEP_PHASE_ENTERED_WINDING_DOWN":
        raise ValueError(
            "handle_winding_down_event requires "
            "SLEEP_PHASE_ENTERED_WINDING_DOWN"
        )

    results = create_winding_down_consolidation_jobs(
        conn=conn,
        payload=claim.payload,
    )

    complete_sleep_event(
        conn=conn,
        event_id=claim.event_id,
        lease_token=claim.lease_token,
    )

    return results



def handle_one_winding_down_event(
    db_pool,
    claim: SleepOutboxClaim,
) -> list[tuple[str, str, bool]]:
    """
    Handle one claimed WINDING_DOWN event in one short atomic transaction.
    """

    conn = db_pool.getconn()
    try:
        results = handle_winding_down_event(
            conn=conn,
            claim=claim,
        )
        conn.commit()
        return results
    except Exception:
        conn.rollback()
        raise
    finally:
        db_pool.putconn(conn)


def build_consolidation_fragment_payloads(job) -> list[dict]:
    """
    Convert an authoritative loaded consolidation job into the ordered
    fragment payload consumed by the artifact writer and extractor.

    Source order is preserved exactly from ConsolidationJobData.fragments.
    """

    if not job.fragments:
        raise ValueError("Consolidation job contains no source fragments")

    payloads: list[dict] = []

    for expected_order, fragment in enumerate(job.fragments):
        if fragment.source_order != expected_order:
            raise ValueError(
                "Consolidation fragment source_order is not contiguous"
            )

        if fragment.session_id != job.session_id:
            raise ValueError(
                "Consolidation fragment session does not match job session"
            )

        payloads.append(
            {
                "fragment_id": fragment.fragment_id,
                "session_id": fragment.session_id,
                "source_order": fragment.source_order,
                "user_text": fragment.user_text,
                "cole_response": fragment.cole_response,
                "occurred_at": fragment.occurred_at,
            }
        )

    return payloads


def claim_one_consolidation_job(
    db_pool,
    lease_owner: str,
):
    """
    Claim one eligible consolidation job in its own short transaction.

    The claim is committed before any external or long-running work begins.
    """

    conn = db_pool.getconn()
    try:
        claim = claim_next_consolidation_job(
            conn=conn,
            lease_owner=lease_owner,
        )
        conn.commit()
        return claim
    except Exception:
        conn.rollback()
        raise
    finally:
        db_pool.putconn(conn)



def recover_one_expired_consolidation_job_lease(
    db_pool,
) -> tuple[str, str, int] | None:
    """
    Recover one expired consolidation job lease in its own short transaction.

    Lease recovery uses its separate recovery budget and never consumes the
    ordinary consolidation processing retry budget.
    """

    conn = db_pool.getconn()
    try:
        result = recover_expired_consolidation_lease(
            conn=conn,
        )
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        db_pool.putconn(conn)


def load_one_claimed_consolidation_job(
    db_pool,
    claim,
):
    """
    Reload one claimed consolidation job under its committed live lease
    using a separate short transaction.
    """

    conn = db_pool.getconn()
    try:
        job = load_claimed_consolidation_job(
            conn=conn,
            processing_id=claim.processing_id,
            lease_token=claim.lease_token,
        )
        conn.commit()
        return job
    except Exception:
        conn.rollback()
        raise
    finally:
        db_pool.putconn(conn)


def renew_one_consolidation_job_lease(
    db_pool,
    claim,
) -> datetime:
    """
    Renew one claimed consolidation job lease in its own short transaction.
    """

    conn = db_pool.getconn()
    try:
        lease_expires_at = renew_consolidation_lease(
            conn=conn,
            processing_id=claim.processing_id,
            lease_owner=claim.lease_owner,
            lease_token=claim.lease_token,
        )
        conn.commit()
        return lease_expires_at
    except Exception:
        conn.rollback()
        raise
    finally:
        db_pool.putconn(conn)


def record_one_consolidation_artifact(
    db_pool,
    claim,
    artifact,
) -> None:
    """
    Persist deterministic consolidation artifact metadata in its own
    short transaction under the currently owned live lease.
    """

    conn = db_pool.getconn()
    try:
        record_consolidation_artifact(
            conn=conn,
            processing_id=claim.processing_id,
            lease_token=claim.lease_token,
            minio_bucket=artifact.bucket,
            minio_object_key=artifact.object_key,
            minio_sha256=artifact.sha256,
            minio_byte_length=artifact.byte_length,
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        db_pool.putconn(conn)


def complete_one_consolidation_job(
    db_pool,
    claim,
    episode_id: str,
) -> None:
    """
    Complete one consolidation job in its own short transaction,
    binding it to the authoritative PostgreSQL episode.
    """

    conn = db_pool.getconn()
    try:
        complete_consolidation_job(
            conn=conn,
            processing_id=claim.processing_id,
            lease_token=claim.lease_token,
            episode_id=episode_id,
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        db_pool.putconn(conn)


def fail_one_consolidation_job(
    db_pool,
    claim,
    error_message: str,
) -> tuple[str, int]:
    """
    Record one consolidation processing failure in its own short transaction
    while the claimed job lease is still live.
    """

    conn = db_pool.getconn()
    try:
        result = record_consolidation_failure(
            conn=conn,
            processing_id=claim.processing_id,
            lease_token=claim.lease_token,
            error_message=error_message,
        )
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        db_pool.putconn(conn)




def process_one_consolidation_job(
    db_pool,
    engine: EpisodicMemoryEngine,
    claim,
    sleep_claim: SleepOutboxClaim,
) -> tuple[str, str | None]:
    """
    Process one claimed consolidation job.

    Ordinary processing failures consume the job retry budget. Lease loss does
    not; expired lease recovery remains a separate operational path.
    """

    try:
        job = load_one_claimed_consolidation_job(
            db_pool=db_pool,
            claim=claim,
        )

        fragment_payloads = build_consolidation_fragment_payloads(job)

        source_fragment_ids = [
            fragment.fragment_id
            for fragment in job.fragments
        ]

        renew_one_sleep_event_lease(
            db_pool=db_pool,
            claim=sleep_claim,
        )
        renew_one_consolidation_job_lease(
            db_pool=db_pool,
            claim=claim,
        )

        artifact = write_episodic_artifact(
            minio_client=engine.minio,
            idempotency_key=job.idempotency_key,
            fragments=fragment_payloads,
        )

        record_one_consolidation_artifact(
            db_pool=db_pool,
            claim=claim,
            artifact=artifact,
        )

        renew_one_sleep_event_lease(
            db_pool=db_pool,
            claim=sleep_claim,
        )
        renew_one_consolidation_job_lease(
            db_pool=db_pool,
            claim=claim,
        )

        extracted = extract_episode(
            fragments=fragment_payloads,
            extraction_model=job.extraction_model,
        )

        renew_one_sleep_event_lease(
            db_pool=db_pool,
            claim=sleep_claim,
        )
        renew_one_consolidation_job_lease(
            db_pool=db_pool,
            claim=claim,
        )

        result = engine.record_episode(
            session_id=job.session_id,
            raw_transcript=artifact.raw_transcript,
            extracted_data=extracted,
            minio_bucket=artifact.bucket,
            minio_object_key=artifact.object_key,
            minio_sha256=artifact.sha256,
            minio_byte_length=artifact.byte_length,
            source_fragment_ids=source_fragment_ids,
            extraction_model=job.extraction_model,
        )

        episode_id = str(result.get("episode_id") or "").strip()

        if not episode_id:
            raise RuntimeError(
                "record_episode returned no episode_id"
            )

        renew_one_sleep_event_lease(
            db_pool=db_pool,
            claim=sleep_claim,
        )
        renew_one_consolidation_job_lease(
            db_pool=db_pool,
            claim=claim,
        )

        complete_one_consolidation_job(
            db_pool=db_pool,
            claim=claim,
            episode_id=episode_id,
        )

        return "completed", episode_id

    except (ConsolidationLeaseLostError, SleepOutboxLeaseLostError):
        raise

    except Exception as exc:
        status, _retry_count = fail_one_consolidation_job(
            db_pool=db_pool,
            claim=claim,
            error_message=str(exc),
        )
        return status, None


def renew_one_sleep_event_lease(
    db_pool,
    claim: SleepOutboxClaim,
) -> datetime:
    """
    Renew one claimed sleep outbox event lease in its own short transaction.
    """

    conn = db_pool.getconn()
    try:
        lease_expires_at = renew_sleep_event_lease(
            conn=conn,
            event_id=claim.event_id,
            lease_owner=claim.lease_owner,
            lease_token=claim.lease_token,
        )
        conn.commit()
        return lease_expires_at
    except Exception:
        conn.rollback()
        raise
    finally:
        db_pool.putconn(conn)



def complete_one_sleep_event(
    db_pool,
    claim: SleepOutboxClaim,
) -> None:
    """
    Complete one claimed sleep outbox event in its own short transaction.
    """

    conn = db_pool.getconn()
    try:
        complete_sleep_event(
            conn=conn,
            event_id=claim.event_id,
            lease_token=claim.lease_token,
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        db_pool.putconn(conn)



def handle_sleeping_event(
    db_pool,
    engine: EpisodicMemoryEngine,
    claim: SleepOutboxClaim,
) -> int:
    """
    Drain all currently eligible consolidation jobs for one SLEEPING event.

    Ordinary child-job failures are recorded on the child and do not block
    other eligible consolidation work. The parent sleep event is completed
    only after the eligible queue is drained.
    """

    if claim.event_type != "SLEEP_PHASE_ENTERED_SLEEPING":
        raise ValueError(
            "handle_sleeping_event requires SLEEP_PHASE_ENTERED_SLEEPING"
        )

    processed_count = 0

    while True:
        renew_one_sleep_event_lease(
            db_pool=db_pool,
            claim=claim,
        )

        consolidation_claim = claim_one_consolidation_job(
            db_pool=db_pool,
            lease_owner=claim.lease_owner,
        )

        if consolidation_claim is None:
            break

        process_one_consolidation_job(
            db_pool=db_pool,
            engine=engine,
            claim=consolidation_claim,
            sleep_claim=claim,
        )

        processed_count += 1

    renew_one_sleep_event_lease(
        db_pool=db_pool,
        claim=claim,
    )

    complete_one_sleep_event(
        db_pool=db_pool,
        claim=claim,
    )

    return processed_count



def dispatch_one_sleep_event(
    db_pool,
    engine: EpisodicMemoryEngine,
    claim: SleepOutboxClaim,
):
    """
    Dispatch one claimed sleep outbox event to its phase-specific handler.
    """

    if claim.event_type == "SLEEP_PHASE_ENTERED_WINDING_DOWN":
        return handle_one_winding_down_event(
            db_pool=db_pool,
            claim=claim,
        )

    if claim.event_type == "SLEEP_PHASE_ENTERED_SLEEPING":
        return handle_sleeping_event(
            db_pool=db_pool,
            engine=engine,
            claim=claim,
        )

    raise ValueError(
        f"Unsupported sleep outbox event type: {claim.event_type}"
    )



def process_one_sleep_event(
    db_pool,
    engine: EpisodicMemoryEngine,
    claim: SleepOutboxClaim,
):
    """
    Process one claimed sleep event with parent retry accounting.

    Lease loss propagates to the separate lease-recovery path and never
    consumes the ordinary parent processing retry budget.
    """

    try:
        return dispatch_one_sleep_event(
            db_pool=db_pool,
            engine=engine,
            claim=claim,
        )

    except (SleepOutboxLeaseLostError, ConsolidationLeaseLostError):
        raise

    except Exception as exc:
        return fail_one_sleep_event(
            db_pool=db_pool,
            claim=claim,
            error_message=str(exc),
        )



def recover_expired_consumer_leases(
    db_pool,
) -> tuple[int, int]:
    """
    Recover all currently expired parent and child leases.

    Parent sleep-event recovery and child consolidation recovery retain their
    separate lease-recovery budgets and never consume ordinary retry counts.
    """

    parent_recoveries = 0
    child_recoveries = 0

    while True:
        result = recover_one_expired_sleep_event_lease(
            db_pool=db_pool,
        )
        if result is None:
            break
        parent_recoveries += 1

    while True:
        result = recover_one_expired_consolidation_job_lease(
            db_pool=db_pool,
        )
        if result is None:
            break
        child_recoveries += 1

    return parent_recoveries, child_recoveries



def consume_one_sleep_event(
    db_pool,
    engine: EpisodicMemoryEngine,
    lease_owner: str,
):
    """
    Perform one consumer iteration.

    Recover expired leases first, then claim and process at most one eligible
    sleep outbox event.
    """

    recover_expired_consumer_leases(
        db_pool=db_pool,
    )

    claim = claim_one_sleep_event(
        db_pool=db_pool,
        lease_owner=lease_owner,
    )

    if claim is None:
        return None

    return process_one_sleep_event(
        db_pool=db_pool,
        engine=engine,
        claim=claim,
    )


def create_sleeping_runtime():
    """
    Create the shared episodic runtime used by SLEEPING consolidation work.

    Runtime initialization happens outside worker database transactions.
    """

    db_pool = create_episodic_db_pool()
    embedder = EpisodicEmbedder()
    engine = EpisodicMemoryEngine(
        db_pool=db_pool,
        embedder_service=embedder,
    )
    return db_pool, engine

def run_forever() -> None:
    """
    Run the sleep outbox consumer continuously.
    """

    db_pool, engine = create_sleeping_runtime()

    print(
        "Cole sleep outbox consumer starting "
        f"worker_id={CONSUMER_WORKER_ID} "
        f"tick_seconds={CONSUMER_TICK_SECONDS}"
    )

    while True:
        try:
            consume_one_sleep_event(
                db_pool=db_pool,
                engine=engine,
                lease_owner=CONSUMER_WORKER_ID,
            )
        except (SleepOutboxLeaseLostError, ConsolidationLeaseLostError) as exc:
            print(f"Sleep outbox lease loss: {exc}")
        except Exception as exc:
            print(f"Sleep outbox consumer iteration failed: {exc}")

        time.sleep(CONSUMER_TICK_SECONDS)


if __name__ == "__main__":
    run_forever()

