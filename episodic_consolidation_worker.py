from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from psycopg2.extensions import connection as PsycopgConnection

from episodic_memory import WORKER_LEASE_MINUTES


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
