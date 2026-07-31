# Episodic Memory Contract

**Version:** 1.0  
**Status:** Ratified  
**Owner:** Westin Genesis Architecture  
**Last Updated:** 2026-07-31

---

## Authoritative Principle

**Authoritative state always flows downstream.**

Downstream systems accelerate retrieval but never redefine truth.

---

## Episodic Memory Recall Invariant

1. **PostgreSQL** is the source of authoritative transactional truth.

2. An episode is **VERIFIED** only when its summary is derived strictly from accepted `event_fragments` and signed with structured reviewer provenance.

3. **Qdrant** is the downstream semantic index and **MUST NOT** be queried for normal recall until:

   - `review_status == "verified"`
   - `index_sync_status == "synced"`
   - Qdrant payload matches the verified summary stored in PostgreSQL.
   - `embedding_source_sha256 == summary_sha256`

4. Vector re-embedding is executed as an asynchronous, recoverable saga with explicit retry metadata.

5. Every state transition must be observable, retryable, and auditable. No failure may leave an episode in an unknown state.



