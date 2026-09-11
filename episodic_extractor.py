from __future__ import annotations

import json
from typing import Any

from openai import OpenAI

from episodic_memory import (
    EXTRACTION_PROMPT_VERSION,
    ExtractedEpisodePayload,
)
from episodic_runtime import OPENROUTER_API_KEY


def _build_extraction_prompt(
    fragments: list[dict[str, Any]],
) -> str:
    if not fragments:
        raise ValueError("At least one fragment is required for episodic extraction")

    blocks: list[str] = []

    for fragment in fragments:
        fragment_id = str(fragment.get("fragment_id", "")).strip()
        user_text = str(fragment.get("user_text") or "")
        cole_response = str(fragment.get("cole_response") or "")

        if not fragment_id:
            raise ValueError("Every fragment requires fragment_id")

        blocks.append(
            "\n".join(
                [
                    f"FRAGMENT {fragment_id}",
                    f"Eric: {user_text}",
                    f"Cole: {cole_response}",
                ]
            )
        )

    source_text = "\n\n".join(blocks)

    return f"""
You are an evidence-bound episodic memory extractor.

Extraction prompt version: {EXTRACTION_PROMPT_VERSION}

SOURCE FRAGMENTS

{source_text}

Return ONLY one valid JSON object with exactly these top-level keys:

{{
  "dense_summary": "string",
  "explicit_facts": [
    {{
      "claim": "string",
      "evidence_quote": "exact substring of Eric's text",
      "source_fragment_ids": ["one or more source fragment UUIDs"],
      "extraction_confidence": 0.0
    }}
  ],
  "system_inferences": [
    {{
      "claim": "string",
      "inference_type": "affective_inference|contextual_inference|goal_inference",
      "confidence": 0.0,
      "evidence_quote": "exact substring of supporting Eric or Cole text",
      "source_fragment_ids": ["one or more source fragment UUIDs"]
    }}
  ]
}}

STRICT RULES

1. Explicit facts may come only from Eric's words.
2. Every explicit-fact evidence_quote must be an exact substring of Eric's
   text in every fragment ID cited by that fact.
3. Every system-inference evidence_quote must be an exact substring of Eric's
   or Cole's text in every fragment ID cited by that inference.
4. Use only fragment IDs supplied above.
5. Never invent, repair, reinterpret, or silently add facts.
6. Keep fact and inference provenance fragment-specific.
7. If evidence is insufficient, omit the claim.
8. Confidence values must be between 0.0 and 1.0.
9. dense_summary must summarize only information supported by these fragments.
10. Do not include markdown or commentary outside the JSON object.
""".strip()


def _validate_extraction_provenance(
    extracted: ExtractedEpisodePayload,
    fragments: list[dict[str, Any]],
) -> None:
    sources: dict[str, dict[str, str]] = {}

    for fragment in fragments:
        fragment_id = str(fragment.get("fragment_id", "")).strip()
        if not fragment_id:
            raise ValueError("Every fragment requires fragment_id")

        if fragment_id in sources:
            raise ValueError(f"Duplicate fragment_id: {fragment_id}")

        sources[fragment_id] = {
            "user_text": str(fragment.get("user_text") or ""),
            "cole_response": str(fragment.get("cole_response") or ""),
        }

    for fact in extracted.explicit_facts:
        for fragment_id in fact.source_fragment_ids:
            if fragment_id not in sources:
                raise ValueError(
                    f"Fact cites unknown source fragment: {fragment_id}"
                )

            if fact.evidence_quote not in sources[fragment_id]["user_text"]:
                raise ValueError(
                    "Fact evidence_quote is not an exact substring of "
                    f"Eric text for fragment {fragment_id}"
                )

    for inference in extracted.system_inferences:
        for fragment_id in inference.source_fragment_ids:
            if fragment_id not in sources:
                raise ValueError(
                    f"Inference cites unknown source fragment: {fragment_id}"
                )

            source = sources[fragment_id]
            combined = (
                source["user_text"]
                + "\n"
                + source["cole_response"]
            )

            if inference.evidence_quote not in combined:
                raise ValueError(
                    "Inference evidence_quote is not an exact substring of "
                    f"source text for fragment {fragment_id}"
                )


def extract_episode(
    fragments: list[dict[str, Any]],
    extraction_model: str,
) -> ExtractedEpisodePayload:
    if not OPENROUTER_API_KEY:
        raise RuntimeError(
            "OPENROUTER_API_KEY is required for episodic extraction"
        )

    model = (extraction_model or "").strip()

    if not model:
        raise ValueError("extraction_model is required")

    prompt = _build_extraction_prompt(fragments)

    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=OPENROUTER_API_KEY,
    )

    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "user",
                "content": prompt,
            }
        ],
        response_format={"type": "json_object"},
        temperature=0,
    )

    content = response.choices[0].message.content

    if not content:
        raise RuntimeError("Episodic extractor returned empty content")

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "Episodic extractor returned invalid JSON"
        ) from exc

    extracted = ExtractedEpisodePayload.model_validate(parsed)

    _validate_extraction_provenance(
        extracted=extracted,
        fragments=fragments,
    )

    return extracted
