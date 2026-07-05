"""
rag/wikidata.py — Wikidata SPARQL retrieval (10% of RAG pipeline).

Used primarily in CHALLENGE mode (difficulty 4-5) to provide
provably correct structured facts that defeat pattern-matching.
"""

from __future__ import annotations
import asyncio
import httpx
import random

SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"
HEADERS = {
    "User-Agent": "AdaptIQ-PFE/1.0",
    "Accept": "application/json",
}

# ── Difficulty-tiered SPARQL queries ─────────────────────────────────────
SPARQL_QUERIES = {
    "Geography": {
        "easy": """
            SELECT ?countryLabel ?capitalLabel ?popLabel WHERE {
              ?country wdt:P31 wd:Q6256; wdt:P36 ?capital.
              OPTIONAL { ?capital wdt:P1082 ?pop. }
              FILTER(?pop > 5000000)
              SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
            } LIMIT 40
        """,
        "hard": """
            SELECT ?countryLabel ?capitalLabel WHERE {
              ?country wdt:P31 wd:Q6256; wdt:P36 ?capital.
              ?capital wdt:P1082 ?pop.
              FILTER(?pop < 100000)
              SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
            } LIMIT 30
        """,
    },
    "History": {
        "easy": """
            SELECT ?battleLabel ?yearLabel WHERE {
              ?battle wdt:P31 wd:Q178561; wdt:P585 ?year.
              FILTER(YEAR(?year) > 1900)
              SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
            } LIMIT 30
        """,
        "hard": """
            SELECT ?battleLabel ?locationLabel ?yearLabel WHERE {
              ?battle wdt:P31 wd:Q178561; wdt:P276 ?location; wdt:P585 ?year.
              FILTER(YEAR(?year) < 1500)
              SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
            } LIMIT 20
        """,
    },
}


async def fetch_wikidata_facts(
    topic: str,
    difficulty: int,
    client: httpx.AsyncClient,
) -> list[dict] | None:
    """
    Execute a SPARQL query appropriate for topic + difficulty.
    Returns list of {label: value} bindings or None.
    """
    tier = "hard" if difficulty >= 4 else "easy"
    topic_queries = SPARQL_QUERIES.get(topic, SPARQL_QUERIES.get("History", {}))
    query = topic_queries.get(tier, topic_queries.get("easy"))
    if not query:
        return None

    try:
        resp = await client.get(
            SPARQL_ENDPOINT,
            params={"query": query, "format": "json"},
            headers=HEADERS,
            timeout=12.0,
        )
        if resp.status_code != 200:
            return None

        bindings = resp.json().get("results", {}).get("bindings", [])
        if not bindings:
            return None

        # Flatten bindings to simple key→value dicts
        facts = []
        for binding in bindings:
            row = {k: v.get("value", "") for k, v in binding.items()}
            facts.append(row)

        return facts

    except (httpx.RequestError, KeyError, ValueError):
        return None


def format_wikidata_as_context(facts: list[dict], n: int = 5) -> str:
    """Convert Wikidata bindings into a human-readable context string."""
    if not facts:
        return ""
    sample = random.sample(facts, min(n, len(facts)))
    lines = []
    for fact in sample:
        pairs = [f"{k.replace('Label', '')}: {v}" for k, v in fact.items() if v]
        lines.append("; ".join(pairs))
    return "\n".join(lines)
