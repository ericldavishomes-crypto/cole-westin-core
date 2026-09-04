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

def create_consolidation_job(
    conn: PsycopgConnection,
    session_id: str,
    source_fragment_ids: list[str],
    extraction_model: str,
) -> tuple[str, bool]:
    """
    Create one deterministic consolidation processing job.

    The caller supplies the already-selected ordered fragment window.
    This function does not decide which fragments belong together.

    Returns:
        (processing_id, created)

    Transaction ownership remains with the caller.
    """

    clean_session_id = (session_id or "").strip()
    clean_extraction_model = (extraction_model or "").strip()

    if not clean_session_id:
        raise ValueError("session_id is required")

    if not clean_extraction_model:
        raise ValueError("extraction_model is required")

    if not source_fragment_ids:
        raise ValueError("source_fragment_ids must not be empty")

    normalized_fragment_ids: list[str] = []

    for fragment_id in source_fragment_ids:
        try:
            normalized_fragment_ids.append(str(uuid.UUID(str(fragment_id))))
        except (ValueError, AttributeError, TypeError) as exc:
            raise ValueError(
                f"Invalid source fragment UUID: {fragment_id}"
            ) from exc

    if len(set(normalized_fragment_ids)) != len(normalized_fragment_ids):
        raise ValueError("source_fragment_ids must not contain duplicates")

    idempotency_key = generate_episode_idempotency_key(
        session_id=clean_session_id,
        source_fragment_ids=normalized_fragment_ids,
        extraction_model=clean_extraction_model,
    )

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                fragment_id::text,
                session_id
            FROM event_fragments
            WHERE fragment_id = ANY(%s::uuid[]);
            """,
            (normalized_fragment_ids,),
        )

        source_rows = cur.fetchall()

        if len(source_rows) != len(normalized_fragment_ids):
            found_ids = {row[0] for row in source_rows}
            missing_ids = [
                fragment_id
                for fragment_id in normalized_fragment_ids
                if fragment_id not in found_ids
            ]
            raise ValueError(
                f"Unknown source fragment IDs: {missing_ids}"
            )

        wrong_session_ids = [
            fragment_id
            for fragment_id, fragment_session_id in source_rows
            if fragment_session_id != clean_session_id
        ]

        if wrong_session_ids:
            raise ValueError(
                "All source fragments must belong to the requested session_id"
            )

        cur.execute(
            """
            INSERT INTO episodic_consolidation_processing (
                idempotency_key,
                session_id,
                extraction_model,
                extraction_prompt_version
            )
            VALUES (
                %s,
                %s,
                %s,
                %s
            )
            ON CONFLICT (idempotency_key) DO NOTHING
            RETURNING id::text;
            """,
            (
                idempotency_key,
                clean_session_id,
                clean_extraction_model,
                EXTRACTION_PROMPT_VERSION,
            ),
        )

        inserted_row = cur.fetchone()

        if inserted_row is not None:
            processing_id = inserted_row[0]

            for source_order, fragment_id in enumerate(normalized_fragment_ids):
                cur.execute(
                    """
                    INSERT INTO episodic_consolidation_fragments (
                        processing_id,
                        fragment_id,
                        source_order
                    )
                    VALUES (
                        %s::uuid,
                        %s::uuid,
                        %s
                    );
                    """,
                    (
                        processing_id,
                        fragment_id,
                        source_order,
                    ),
                )

            return processing_id, True

        cur.execute(
            """
            SELECT
                id::text,
                session_id,
                extraction_model,
                extraction_prompt_version
            FROM episodic_consolidation_processing
            WHERE idempotency_key = %s;
            """,
            (idempotency_key,),
        )

        existing_job = cur.fetchone()

        if existing_job is None:
            raise RuntimeError(
                "Idempotency conflict occurred but existing consolidation job "
                "could not be loaded"
            )

        processing_id = existing_job[0]

        if existing_job[1] != clean_session_id:
            raise RuntimeError(
                "Existing consolidation job has unexpected session_id"
            )

        if existing_job[2] != clean_extraction_model:
            raise RuntimeError(
                "Existing consolidation job has unexpected extraction_model"
            )

        if existing_job[3] != EXTRACTION_PROMPT_VERSION:
            raise RuntimeError(
                "Existing consolidation job has unexpected extraction_prompt_version"
            )

        cur.execute(
            """
            SELECT fragment_id::text
            FROM episodic_consolidation_fragments
            WHERE processing_id = %s::uuid
            ORDER BY source_order;
            """,
            (processing_id,),
        )

        existing_fragment_ids = [row[0] for row in cur.fetchall()]

        if existing_fragment_ids != normalized_fragment_ids:
            raise RuntimeError(
                "Existing consolidation job fragment membership does not match "
                "its deterministic idempotency key"
            )

        return processing_id, False
        
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
