from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from typing import Any

from minio import Minio
from minio.error import S3Error

from episodic_memory import calculate_sha256
from episodic_runtime import get_episodic_minio_bucket


@dataclass(frozen=True)
class EpisodicArtifact:
    raw_transcript: str
    bucket: str
    object_key: str
    sha256: str
    byte_length: int


def build_raw_transcript(
    fragments: list[dict[str, Any]],
) -> str:
    """
    Build the canonical raw transcript for one ordered consolidation job.

    Fragment order must already be authoritative when supplied here.
    """

    if not fragments:
        raise ValueError(
            "At least one fragment is required to build an episodic artifact"
        )

    blocks: list[str] = []

    for fragment in fragments:
        fragment_id = str(fragment.get("fragment_id", "")).strip()

        if not fragment_id:
            raise ValueError("Every fragment requires fragment_id")

        user_text = str(fragment.get("user_text") or "")
        cole_response = str(fragment.get("cole_response") or "")

        blocks.append(
            f"Eric: {user_text}\nCole: {cole_response}"
        )

    return "\n".join(blocks)


def write_episodic_artifact(
    minio_client: Minio,
    idempotency_key: str,
    fragments: list[dict[str, Any]],
) -> EpisodicArtifact:
    """
    Write one deterministic raw transcript artifact to production MinIO.

    Reprocessing the same consolidation job targets the same object key.
    """

    clean_idempotency_key = (idempotency_key or "").strip()

    if not clean_idempotency_key:
        raise ValueError("idempotency_key is required")

    transcript = build_raw_transcript(fragments)
    raw = transcript.encode("utf-8")
    sha256 = calculate_sha256(transcript)
    bucket = get_episodic_minio_bucket()
    object_key = f"episodes/{clean_idempotency_key}.txt"

    try:
        stat = minio_client.stat_object(
            bucket,
            object_key,
        )
    except S3Error as exc:
        if exc.code != "NoSuchKey":
            raise
    else:
        stored_sha256 = (
            stat.metadata.get("x-amz-meta-sha256")
            or stat.metadata.get("sha256")
        )

        if (
            stat.size != len(raw)
            or stored_sha256 != sha256
        ):
            raise RuntimeError(
                "Existing episodic artifact does not match the "
                "deterministic consolidation payload"
            )

        return EpisodicArtifact(
            raw_transcript=transcript,
            bucket=bucket,
            object_key=object_key,
            sha256=sha256,
            byte_length=len(raw),
        )

    minio_client.put_object(
        bucket,
        object_key,
        BytesIO(raw),
        len(raw),
        metadata={"sha256": sha256},
        content_type="text/plain",
    )

    return EpisodicArtifact(
        raw_transcript=transcript,
        bucket=bucket,
        object_key=object_key,
        sha256=sha256,
        byte_length=len(raw),
    )
