from __future__ import annotations

import logging

logger = logging.getLogger("[rag]")


def reciprocal_rank_fusion(
    result_lists: list[list[dict]],
    k: int = 60,
    top_n: int = 20,
) -> list[dict]:
    """Merge multiple ranked result lists using RRF.

    Each result list is a list of dicts with at least an 'id' key.
    Returns merged results with rrf_score, deduplicated by id.
    """
    scores: dict[str, float] = {}
    seen: dict[str, dict] = {}

    for results in result_lists:
        for rank, doc in enumerate(results):
            doc_id = str(doc.get("id", ""))
            if not doc_id:
                continue
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank + 1)
            if doc_id not in seen:
                seen[doc_id] = doc

    # Normalize scores to 0-1 range
    max_score = max(scores.values()) if scores else 1.0
    for doc_id in scores:
        scores[doc_id] /= max_score

    # Sort by score and return top_n
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_n]

    results = []
    for doc_id, score in ranked:
        doc = seen[doc_id].copy()
        doc["rrf_score"] = score
        doc["score"] = score
        results.append(doc)

    logger.info(
        "RRF fusion: %d input lists -> %d unique -> top %d",
        len(result_lists),
        len(seen),
        len(results),
    )
    return results


def deduplicate_by_trigram(
    chunks: list[dict],
    similarity_threshold: float = 0.85,
) -> list[dict]:
    """Remove near-duplicate chunks based on trigram overlap.

    Uses Jaccard similarity on word-level trigrams — cheap and effective
    for catching chunks that differ only by whitespace or minor edits.
    This is a lexical check, not semantic; paraphrased duplicates slip through.
    Keeps the higher-scored chunk in each duplicate pair.

    Must be called AFTER RRF (chunks already sorted by score descending).
    """
    if len(chunks) <= 1:
        return chunks

    def _trigrams(text: str) -> set[str]:
        words = text.lower().split()
        if len(words) < 3:
            return set(words)
        return {" ".join(words[i : i + 3]) for i in range(len(words) - 2)}

    def _jaccard(a: set[str], b: set[str]) -> float:
        if not a or not b:
            return 0.0
        return len(a & b) / len(a | b)

    kept: list[dict] = []
    kept_trigrams: list[set[str]] = []

    for chunk in chunks:
        content = chunk.get("content", "")
        tri = _trigrams(content)

        is_dup = False
        for existing_tri in kept_trigrams:
            if _jaccard(tri, existing_tri) >= similarity_threshold:
                is_dup = True
                break

        if not is_dup:
            kept.append(chunk)
            kept_trigrams.append(tri)

    removed = len(chunks) - len(kept)
    if removed:
        logger.info(
            "Semantic dedup: removed %d near-duplicates from %d chunks",
            removed,
            len(chunks),
        )
    return kept
