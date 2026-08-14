"""
READ-ONLY COLE MEMORY RANKING DIAGNOSTIC

Purpose:
Inspect exactly which records beat Miami in Cole's current retrieval path.

This script:
- does NOT write to Qdrant
- does NOT modify PostgreSQL
- does NOT modify Redis
- does NOT modify MinIO
- does NOT modify Cole's chat history
"""

from cole_knowledge import (
    CANDIDATES_PER_COLLECTION,
    COLE_COLLECTIONS,
    DEFAULT_GLOBAL_LIMIT,
    _search_collection,
    fetch_cole_memory_records,
    get_embedding,
)


QUERY = "Colster, what do you actually remember about our trip to Miami?"


def print_record(rank, record):
    text = record.get("text", "")
    source = record.get("source_key") or "(no source_key)"
    collection = record.get("collection") or "(unknown)"
    score = float(record.get("score", 0.0))

    preview = " ".join(text.split())[:400]

    print(f"\nRANK {rank}")
    print(f"score:      {score:.6f}")
    print(f"characters: {len(text)}")
    print(f"collection: {collection}")
    print(f"source:     {source}")
    print(f"preview:    {preview}")


def main():
    print("=" * 90)
    print("COLE MEMORY RANKING DIAGNOSTIC")
    print("=" * 90)

    print("\nQUERY:")
    print(QUERY)

    query_vector = get_embedding(QUERY)

    if not query_vector:
        print("\nFAILED: no embedding produced.")
        return

    print(f"\nEmbedding dimensions: {len(query_vector)}")

    # ---------------------------------------------------------
    # 1. SEARCH EACH COLLECTION INDEPENDENTLY
    # ---------------------------------------------------------

    print("\n" + "=" * 90)
    print("PER-COLLECTION RESULTS")
    print("=" * 90)

    all_candidates = []

    for collection_name in COLE_COLLECTIONS:
        print("\n" + "-" * 90)
        print(f"COLLECTION: {collection_name}")
        print("-" * 90)

        records = _search_collection(
            collection_name=collection_name,
            query_vector=query_vector,
            limit=CANDIDATES_PER_COLLECTION,
        )

        if not records:
            print("No results.")
            continue

        for rank, record in enumerate(records, start=1):
            print_record(rank, record)
            all_candidates.append(record)

    # ---------------------------------------------------------
    # 2. RAW GLOBAL SORT BEFORE DEDUP / LIMIT
    # ---------------------------------------------------------

    print("\n" + "=" * 90)
    print("RAW GLOBAL SORTED CANDIDATES")
    print("=" * 90)

    all_candidates.sort(
        key=lambda record: record["score"],
        reverse=True,
    )

    for rank, record in enumerate(all_candidates, start=1):
        print_record(rank, record)

    # ---------------------------------------------------------
    # 3. ACTUAL FINAL TOP 6
    # ---------------------------------------------------------

    print("\n" + "=" * 90)
    print("ACTUAL FINAL TOP 6")
    print("=" * 90)

    final_records = fetch_cole_memory_records(
        user_prompt=QUERY,
        limit=DEFAULT_GLOBAL_LIMIT,
    )

    if not final_records:
        print("No final records.")
    else:
        for rank, record in enumerate(final_records, start=1):
            print_record(rank, record)

    # ---------------------------------------------------------
    # 4. MIAMI-SPECIFIC ANALYSIS
    # ---------------------------------------------------------

    print("\n" + "=" * 90)
    print("MIAMI-SPECIFIC ANALYSIS")
    print("=" * 90)

    miami_candidates = []

    for record in all_candidates:
        source = str(record.get("source_key") or "")
        text = str(record.get("text") or "")

        if "miami" in source.lower() or "miami" in text.lower():
            miami_candidates.append(record)

    if not miami_candidates:
        print("No Miami-related record appeared in the per-collection candidate pool.")
    else:
        for rank, record in enumerate(miami_candidates, start=1):
            print_record(rank, record)

        final_sources = {
            str(record.get("source_key") or "")
            for record in final_records
        }

        miami_survived = any(
            "miami" in source.lower()
            for source in final_sources
        )

        print(f"\nMiami survived final top 6: {miami_survived}")

    # ---------------------------------------------------------
    # 5. DIRECT CONTINUITY_ARCHIVES SEARCH WITH LARGER LIMIT
    # ---------------------------------------------------------

    print("\n" + "=" * 90)
    print("CONTINUITY_ARCHIVES — TOP 12")
    print("=" * 90)

    archive_records = _search_collection(
        collection_name="continuity_archives",
        query_vector=query_vector,
        limit=12,
    )

    for rank, record in enumerate(archive_records, start=1):
        print_record(rank, record)

    print("\n" + "=" * 90)
    print("DIAGNOSTIC COMPLETE — NO DATA WAS MODIFIED")
    print("=" * 90)


if __name__ == "__main__":
    main()
