"""
READ-ONLY QDRANT MEMORY RETRIEVAL DIAGNOSTIC

This script does not write, update, or delete anything in Qdrant.
It tests Cole's existing retrieval pipeline and reports what survives.
"""

from cole_knowledge import (
    CANDIDATES_PER_COLLECTION,
    DEFAULT_CONTEXT_CHAR_LIMIT,
    DEFAULT_GLOBAL_LIMIT,
    _search_collection,
    fetch_cole_memories,
    fetch_cole_memory_records,
    get_embedding,
)


QUERY = (
    "I was thinking about our Miami trip. "
    "Do you remember anything about it?"
)


def print_record(rank, record):
    text = record.get("text", "")
    source = record.get("source_key") or "(no source_key)"
    score = record.get("score", 0.0)
    collection = record.get("collection", "(unknown)")

    preview = " ".join(text.split())[:300]

    print(f"\nRANK {rank}")
    print(f"score:      {score:.6f}")
    print(f"characters: {len(text)}")
    print(f"collection: {collection}")
    print(f"source:     {source}")
    print(f"preview:    {preview}")


def main():
    print("=" * 80)
    print("COLE MEMORY RETRIEVAL DIAGNOSTIC")
    print("=" * 80)

    print(f"\nQUERY:\n{QUERY}")

    # -----------------------------------------------------
    # 1. CREATE QUERY EMBEDDING
    # -----------------------------------------------------

    query_vector = get_embedding(QUERY)

    print("\n" + "=" * 80)
    print("QUERY EMBEDDING")
    print("=" * 80)

    if not query_vector:
        print("FAILED: No query embedding was produced.")
        return

    print(f"Embedding dimensions: {len(query_vector)}")

    # -----------------------------------------------------
    # 2. SEARCH CONTINUITY_ARCHIVES DIRECTLY
    # -----------------------------------------------------

    print("\n" + "=" * 80)
    print("CONTINUITY_ARCHIVES DIRECT RESULTS")
    print("=" * 80)

    archive_records = _search_collection(
        collection_name="continuity_archives",
        query_vector=query_vector,
        limit=CANDIDATES_PER_COLLECTION,
    )

    if not archive_records:
        print("No results returned from continuity_archives.")
    else:
        for rank, record in enumerate(archive_records, start=1):
            print_record(rank, record)

    # -----------------------------------------------------
    # 3. RUN GLOBAL FIVE-COLLECTION RANKING
    # -----------------------------------------------------

    print("\n" + "=" * 80)
    print("GLOBAL RANKED RESULTS")
    print("=" * 80)

    global_records = fetch_cole_memory_records(
        user_prompt=QUERY,
        limit=DEFAULT_GLOBAL_LIMIT,
    )

    if not global_records:
        print("No global records returned.")
    else:
        for rank, record in enumerate(global_records, start=1):
            print_record(rank, record)

    # -----------------------------------------------------
    # 4. SIMULATE THE CURRENT CHARACTER-BUDGET LOGIC
    # -----------------------------------------------------

    print("\n" + "=" * 80)
    print("CURRENT 9000-CHARACTER BUDGET SIMULATION")
    print("=" * 80)

    current_chars = 0
    surviving_sources = []

    for rank, record in enumerate(global_records, start=1):
        source_label = (
            record["collection"]
            .replace("_", " ")
            .title()
        )

        source_key = record.get("source_key")

        provenance = f"[{source_label}]"

        if source_key:
            provenance += f" [Source: {source_key}]"

        block = f"{provenance}\n{record['text']}"
        block_length = len(block)

        print(
            f"Rank {rank}: block={block_length}, "
            f"current={current_chars}, "
            f"would_total={current_chars + block_length}"
        )

        if current_chars + block_length > DEFAULT_CONTEXT_CHAR_LIMIT:
            print(
                f"--> BREAK occurs at rank {rank}. "
                "All lower-ranked records are skipped."
            )
            break

        surviving_sources.append(source_key or "(no source_key)")
        current_chars += block_length

    print(f"\nCharacter budget: {DEFAULT_CONTEXT_CHAR_LIMIT}")
    print(f"Characters accepted: {current_chars}")
    print("Sources surviving current builder:")

    if surviving_sources:
        for source in surviving_sources:
            print(f"  - {source}")
    else:
        print("  NONE")

    # -----------------------------------------------------
    # 5. CALL THE REAL FINAL CONTEXT BUILDER
    # -----------------------------------------------------

    print("\n" + "=" * 80)
    print("ACTUAL fetch_cole_memories() OUTPUT")
    print("=" * 80)

    final_context = fetch_cole_memories(QUERY)

    print(f"Final context characters: {len(final_context)}")

    if final_context:
        print("\nFinal context preview:")
        print(final_context[:2000])
    else:
        print(
            "\nEMPTY FINAL CONTEXT: Cole would receive no "
            "retrieved continuity for this query."
        )

    print("\n" + "=" * 80)
    print("DIAGNOSTIC COMPLETE -- NO QDRANT DATA WAS MODIFIED")
    print("=" * 80)


if __name__ == "__main__":
    main()
