"""
rag/hf_dataset.py — HuggingFace "ChavyvAkvar/MCQ-generated" dataset retrieval (20%).

Streams random samples, filters by topic keyword, and returns
pre-validated Q/A pairs that bypass LLM generation entirely.
The dataset is loaded once at startup and cached in memory.
"""


import asyncio
import random
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Global cache — loaded once at startup
_dataset = None
_dataset_loaded = False

TOPIC_KEYWORDS = {
    "History": [
        "war", "battle", "revolution", "empire", "dynasty", "ancient",
        "medieval", "century", "historical", "king", "queen", "treaty",
        "independence", "colonialism", "civilization",
    ],
    "Geography": [
        "capital", "country", "continent", "ocean", "river", "mountain",
        "island", "population", "located", "border", "region", "city",
        "territory", "nation", "geography",
    ],
    "Mixed": [],  # no filter for Mixed
}


def _load_dataset_sync():
    """Load dataset synchronously — called once in startup event."""
    global _dataset, _dataset_loaded
    if _dataset_loaded:
        return
    try:
        from datasets import load_dataset
        ds = load_dataset(
            "ChavyvAkvar/MCQ-generated",
            split="train",
        )
        _dataset = ds
        logger.info(f"HF dataset loaded: {len(ds)} items")
    except Exception as e:
        logger.warning(f"Primary HF dataset failed ({e}), falling back to sciq")
        try:
            from datasets import load_dataset
            _dataset = load_dataset("sciq", split="train")
            logger.info("SciQ fallback loaded")
        except Exception as e2:
            logger.error(f"HF dataset unavailable: {e2}")
            _dataset = None
    _dataset_loaded = True


async def load_hf_dataset() -> bool:
    """Async wrapper for startup loading.

    Returns True when a dataset is available in memory, else False.
    """
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _load_dataset_sync)
    return _dataset is not None


def _normalize_row(row: dict) -> Optional[dict]:
    """
    Normalize across different HF dataset schemas.
    Returns {question, correct_answer, distractors, support} or None.
    """
    q = str(row.get("question") or row.get("Question") or "").strip()
    correct = str(
        row.get("correct_answer") or row.get("answer") or row.get("AnswerKey") or ""
    ).strip()

    # Collect distractors
    dists = []
    for key in [
        "distractor1", "distractor2", "distractor3",
        "distractor_1", "distractor_2", "distractor_3",
    ]:
        d = row.get(key)
        if d and str(d).strip():
            dists.append(str(d).strip())

    support = str(
        row.get("support") or row.get("context") or row.get("Context") or ""
    ).strip()

    if not q or not correct or len(dists) < 2:
        return None
    return {
        "question": q,
        "correct_answer": correct,
        "distractors": dists,
        "support": support,
    }


def get_hf_question(topic: str, difficulty: int) -> Optional[dict]:
    """
    Synchronously sample a question from the HF dataset.
    Run in executor to avoid blocking the event loop.
    
    Returns dict matching QuestionOut fields, or None.
    """
    global _dataset
    if _dataset is None:
        return None

    try:
        n = len(_dataset)
        keywords = TOPIC_KEYWORDS.get(topic, [])
        sample_size = min(400, n)
        indices = random.sample(range(n), sample_size)

        best = None
        # Try to find a topically relevant question
        for idx in indices:
            row = _dataset[idx]
            q_text = str(row.get("question") or "").lower()
            support = str(row.get("support") or row.get("context") or "").lower()

            if keywords and any(kw in q_text or kw in support for kw in keywords):
                norm = _normalize_row(row)
                if norm:
                    best = norm
                    break

        # Fallback: just take any valid row
        if best is None:
            for idx in random.sample(indices, min(50, len(indices))):
                norm = _normalize_row(_dataset[idx])
                if norm:
                    best = norm
                    break

        if best is None:
            return None

        # Build shuffled options
        opts = [best["correct_answer"]] + best["distractors"][:3]
        # Pad to 4 options if needed
        pads = ["None of the above", "Cannot be determined", "All of the above"]
        while len(opts) < 4:
            opts.append(pads.pop(0))
        random.shuffle(opts)

        return {
            "question": best["question"],
            "correct_answer": best["correct_answer"],
            "options": opts[:4],
            "explanation": best["support"][:300] if best["support"] else "From curated MCQ dataset.",
            "source": "huggingface",
        }
    except Exception as e:
        logger.warning(f"HF dataset retrieval failed: {e}")
        return None


async def async_get_hf_question(topic: str, difficulty: int) -> Optional[dict]:
    """Async wrapper — runs blocking HF access in thread pool."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, get_hf_question, topic, difficulty)