from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime

from psycopg2.extensions import connection as PsycopgConnection

from episodic_memory import (
    EXTRACTION_PROMPT_VERSION,
    WORKER_LEASE_MINUTES,
    generate_episode_idempotency_key,
)


@dataclass(frozen=True)
class ConsolidationJobClaim:
    processing_id: str
    idempotency_key: str
    session_id: str
    retry_count: int
    max_retries: int
    lease_owner: str
    lease_token: str
    lease_acquired_at: datetime
    lease_expires_at: datetime


class ConsolidationLeaseLostError(RuntimeError):
    """Raised when a worker no longer owns a valid processing lease."""

def _enqueue_operational_outbox(
    cur,
    event_type: str,
    payload: dict,
) -> None:
    """
    Enqueue an operational event inside the caller's existing transaction.
    """

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
            event_type,
            json.dumps(payload),
        ),
    )
def claim_next_consolidation_job(
    conn: PsycopgConnection,
    lease_owner: str,
) -> ConsolidationJobClaim | None:
    """
    Atomically claim the oldest eligible consolidation job.

    Eligible jobs are:
      - pending jobs
      - retry_wait jobs whose retry schedule has elapsed

    Rows are selected with FOR UPDATE SKIP LOCKED so concurrent workers
    cannot claim the same job.

    Expired processing leases are intentionally NOT recovered here.
    Lease recovery is a separate operation with separate semantics.
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
                FROM episodic_consolidation_processing
                WHERE
                    retry_count < max_retries
                    AND (
                        status = 'pending'
                        OR (
                            status = 'retry_wait'
                            AND next_retry_at <= NOW()
                        )
                    )
                ORDER BY created_at
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            UPDATE episodic_consolidation_processing AS p
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
            WHERE p.id = claimable.id
            RETURNING
                p.id::text,
                p.idempotency_key,
                p.session_id,
                p.retry_count,
                p.max_retries,
                p.lease_owner,
                p.lease_token::text,
                p.lease_acquired_at,
                p.lease_expires_at;
            """,
            (
                clean_lease_owner,
                lease_token,
                WORKER_LEASE_MINUTES,
            ),
        )

        row = cur.fetchone()

    if row is None:
        return None

    return ConsolidationJobClaim(
        processing_id=row[0],
        idempotency_key=row[1],
        session_id=row[2],
        retry_count=row[3],
        max_retries=row[4],
        lease_owner=row[5],
        lease_token=row[6],
        lease_acquired_at=row[7],
        lease_expires_at=row[8],
    )


def complete_consolidation_job(
    conn: PsycopgConnection,
    processing_id: str,
    lease_token: str,
    episode_id: str,
) -> None:
    """
    Mark a consolidation job completed only while the caller still owns
    its live lease.

    Completion is CAS-guarded by processing ID, processing state,
    lease token, and lease expiration.
    """

    if not processing_id:
        raise ValueError("processing_id is required")

    if not lease_token:
        raise ValueError("lease_token is required")

    if not episode_id:
        raise ValueError("episode_id is required")

    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE episodic_consolidation_processing
            SET
                status = 'completed',
                episode_id = %s::uuid,
                completed_at = NOW(),
                lease_owner = NULL,
                lease_token = NULL,
                lease_acquired_at = NULL,
                lease_expires_at = NULL,
                next_retry_at = NULL,
                last_error = NULL,
                updated_at = NOW()
            WHERE
                id = %s::uuid
                AND status = 'processing'
                AND lease_token = %s::uuid
                AND lease_expires_at > NOW()
            RETURNING id;
            """,
            (
                episode_id,
                processing_id,
                lease_token,
            ),
        )

        completed = cur.fetchone()

    if completed is None:
        raise ConsolidationLeaseLostError(
            "Consolidation job completion rejected: "
            "lease is missing, expired, or no longer owned"
        )
def record_consolidation_failure(
    conn: PsycopgConnection,
    processing_id: str,
    lease_token: str,
    error_message: str,
) -> tuple[str, int]:
    """
    Record a failed consolidation attempt while the caller still owns
    its live lease.

    Retry delay mirrors the episodic re-embedding policy:
    1, 2, 4, 8, ... minutes, capped at 360 minutes.

    Returns:
        (new_status, new_retry_count)
    """

    if not processing_id:
        raise ValueError("processing_id is required")

    if not lease_token:
        raise ValueError("lease_token is required")

    clean_error = (error_message or "").strip()

    if not clean_error:
        raise ValueError("error_message is required")

    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE episodic_consolidation_processing
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
                    ELSE NOW() + (
                        LEAST(
                            POWER(2, retry_count),
                            360
                        )::INTEGER * INTERVAL '1 minute'
                    )
                END,
                last_error = %s,
                lease_owner = NULL,
                lease_token = NULL,
                lease_acquired_at = NULL,
                lease_expires_at = NULL,
                completed_at = NULL,
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
                processing_id,
                lease_token,
            ),
        )

        row = cur.fetchone()

        if row is None:
            raise ConsolidationLeaseLostError(
                "Consolidation failure update rejected: "
                "lease is missing, expired, or no longer owned"
            )

        new_status, new_retry_count = row

        if new_status == "exhausted":
            _enqueue_operational_outbox(
                cur,
                "CONSOLIDATION_PROCESSING_EXHAUSTED_ALERT",
                {
                    "processing_id": processing_id,
                    "retry_count": new_retry_count,
                    "error": clean_error,
                },
            )

        return new_status, new_retry_count

def recover_expired_consolidation_lease(
    conn: PsycopgConnection,
) -> tuple[str, str, int] | None:
    """
    Recover the oldest consolidation job whose processing lease expired.

    Lease recovery is accounted separately from ordinary processing
    failures. retry_count is never changed here.

    Returns:
        (processing_id, new_status, new_lease_recovery_count)

    Returns None when no expired processing lease is eligible.
    """

    with conn.cursor() as cur:
        cur.execute(
            """
            WITH recoverable AS (
                SELECT id
                FROM episodic_consolidation_processing
                WHERE
                    status = 'processing'
                    AND lease_expires_at <= NOW()
                    AND lease_recovery_count < max_lease_recoveries
                ORDER BY lease_expires_at, created_at
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            UPDATE episodic_consolidation_processing AS p
            SET
                lease_recovery_count = lease_recovery_count + 1,
                status = CASE
                    WHEN lease_recovery_count + 1 >= max_lease_recoveries
                        THEN 'exhausted'
                    ELSE 'pending'
                END,
                lease_owner = NULL,
                lease_token = NULL,
                lease_acquired_at = NULL,
                lease_expires_at = NULL,
                next_retry_at = NULL,
                completed_at = NULL,
                updated_at = NOW()
            FROM recoverable
            WHERE p.id = recoverable.id
            RETURNING
                p.id::text,
                p.status,
                p.lease_recovery_count;
            """
        )

        row = cur.fetchone()

        if row is None:
            return None

        processing_id, new_status, new_lease_recovery_count = row

        if new_status == "exhausted":
            _enqueue_operational_outbox(
                cur,
                "CONSOLIDATION_LEASE_RECOVERY_EXHAUSTED_ALERT",
                {
                    "processing_id": processing_id,
                    "lease_recovery_count": new_lease_recovery_count,
                },
            )

        return processing_id, new_status, new_lease_recovery_count
