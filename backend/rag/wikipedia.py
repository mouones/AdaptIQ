"""
rag/wikipedia.py — Wikipedia API retrieval.

Provides difficulty-tiered search queries per topic, fetching rich
context paragraphs for use in MCQ generation.
"""

from __future__ import annotations
import asyncio
import re
import httpx

# ── Difficulty-tiered Wikipedia queries ──────────────────────────────────
WIKI_QUERIES: dict[str, dict[int, list[str]]] = {
    "History": {
        1: [
            "World War II major battles", "D-Day Normandy invasion",
            "French Revolution causes", "American Revolution 1776",
            "Roman Empire rise and fall",
        ],
        2: [
            "WWII battles France USA capitals", "Napoleon Bonaparte campaigns",
            "Cold War Berlin Wall", "World War I causes assassination",
            "Ancient Egypt pharaohs pyramids",
        ],
        3: [
            "19th century wars Europe Africa colonialism",
            "Ottoman Empire decline Balkans",
            "Thirty Years War Europe religious",
            "Crimean War 1853 causes",
            "Meiji Restoration Japan modernization",
        ],
        4: [
            "Byzantine Empire Justinian reconquest",
            "Mongol Empire Genghis Khan conquests",
            "Pre-1500 medieval crusades obscure",
            "Hundred Years War Joan of Arc",
            "Reconquista Iberian peninsula",
        ],
        5: [
            "Pre-1000 obscure battles Migration Period",
            "Early medieval kingdoms Carolingian",
            "Sassanid Persian Empire wars",
            "Tang Dynasty Central Asia expansion",
            "Viking raids Lindisfarne 793",
        ],
    },
    "Geography": {
        1: [
            "Major capitals Europe population large",
            "World capitals South America",
            "Largest countries world area",
            "Major rivers Europe Americas",
            "Oceans seas world geography",
        ],
        2: [
            "African capitals rivers Asia geography",
            "Mountain ranges highest peaks world",
            "Countries landlocked Central Asia",
            "Island nations Pacific Ocean",
            "Major deserts world Sahara Gobi",
        ],
        3: [
            "African capitals rivers bordering countries",
            "Smallest countries world area Vatican",
            "Southeast Asia archipelago geography",
            "Central Asian republic capitals",
            "Balkan peninsula countries borders",
        ],
        4: [
            "Smallest capitals islands Pacific",
            "Microstates Europe geography borders",
            "Obscure island territories geography",
            "Dependencies territories capitals world",
            "Exclave enclave countries geography",
        ],
        5: [
            "Smallest capitals population under 100k",
            "Disputed territories capitals geography",
            "Autonomous regions geography obscure",
            "Remote island capitals geography",
            "Enclaved nations geography trivia",
        ],
    },
    "Mixed": {
        1: ["World history geography combined major facts"],
        2: ["20th century history geography events"],
        3: ["World history geography connections medium"],
        4: ["Obscure history geography facts hard"],
        5: ["Rare historical geography obscure trivia"],
    },
}

WIKI_API = "https://en.wikipedia.org/api/rest_v1/page/summary/"
WIKI_SEARCH = "https://en.wikipedia.org/w/api.php"
HEADERS = {"User-Agent": "AdaptIQ-PFE/1.0 (educational project)"}


async def fetch_wikipedia_context(
    topic: str,
    difficulty: int,
    client: httpx.AsyncClient,
    n_sentences: int = 4,
) -> dict | None:
    """
    Select a difficulty-appropriate Wikipedia article and return its context.
    Returns: {title, context, url} or None on failure.
    """
    queries = WIKI_QUERIES.get(topic, WIKI_QUERIES["Mixed"])
    difficulty = max(1, min(5, difficulty))
    # Walk down difficulty levels until we find a working query
    for d in [difficulty, max(1, difficulty - 1), min(5, difficulty + 1)]:
        query_list = queries.get(d, queries.get(3, []))
        if not query_list:
            continue
        import random
        query = random.choice(query_list)
        result = await _search_and_fetch(query, client, n_sentences)
        if result:
            return result
    return None


async def _search_and_fetch(
    query: str,
    client: httpx.AsyncClient,
    n_sentences: int,
) -> dict | None:
    """Search Wikipedia and fetch the first result's summary."""
    try:
        # Step 1: OpenSearch to find best article title
        resp = await client.get(
            WIKI_SEARCH,
            params={
                "action": "query",
                "list": "search",
                "srsearch": query,
                "srlimit": 3,
                "format": "json",
            },
            headers=HEADERS,
            timeout=8.0,
        )
        results = resp.json().get("query", {}).get("search", [])
        if not results:
            return None

        title = results[0]["title"]

        # Step 2: Fetch page summary
        summary_resp = await client.get(
            WIKI_API + title.replace(" ", "_"),
            headers=HEADERS,
            timeout=8.0,
        )
        if summary_resp.status_code != 200:
            return None

        data = summary_resp.json()
        extract = data.get("extract", "")
        if len(extract) < 50:
            return None

        # Trim to n_sentences
        sentences = re.split(r"(?<=[.!?])\s+", extract)
        context = " ".join(sentences[:n_sentences])

        return {
            "title": data.get("title", title),
            "context": context,
            "url": data.get("content_urls", {}).get("desktop", {}).get("page", ""),
        }

    except (httpx.RequestError, KeyError, ValueError):
        return None


async def fetch_related_titles(
    query: str,
    client: httpx.AsyncClient,
    limit: int = 6,
) -> list[str]:
    """Fetch related article titles — used for distractor generation."""
    try:
        resp = await client.get(
            WIKI_SEARCH,
            params={
                "action": "query",
                "list": "search",
                "srsearch": query,
                "srlimit": limit,
                "format": "json",
            },
            headers=HEADERS,
            timeout=6.0,
        )
        results = resp.json().get("query", {}).get("search", [])
        return [r["title"] for r in results]
    except Exception:
        return []
