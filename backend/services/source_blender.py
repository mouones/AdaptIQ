"""
services/source_blender.py
40 / 40 / 20 source pipeline with narrative blending.

Source mix:
  40% — Structured / Official facts
         ├── Wikidata  (existing wikidata.py)
         ├── DBpedia   (SPARQL: dbpedia.org/sparql)
         ├── World Bank API (api.worldbank.org/v2)
         └── UN Data API   (data.un.org/ws/rest)
  40% — Wikipedia narrative (existing wikipedia.py)
  20% — HuggingFace QA templates (existing hf_dataset.py)

The blend() method returns a SourceBundle with:
  - structured_facts: list[str]   ← verified data points
  - narrative:        str         ← engaging story/context
  - hf_pattern:       str | None  ← optional question template
  - sources:          list[str]   ← provenance tags ["wikidata:Q142",...]
"""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass, field
from typing import Optional

import httpx
from rag.hf_dataset import async_get_hf_question
from rag.wikidata import fetch_wikidata_facts, format_wikidata_as_context
from rag.wikipedia import fetch_wikipedia_context

logger = logging.getLogger(__name__)


# ─── Output bundle ────────────────────────────────────────────────────────────

@dataclass
class SourceBundle:
    """Everything the question generator needs to build a blended question."""
    structured_facts: list[str]        = field(default_factory=list)
    narrative:        str              = ""
    hf_pattern:       Optional[str]    = None
    sources:          list[str]        = field(default_factory=list)
    topic:            str              = ""
    difficulty:       int              = 3

    @property
    def has_structured(self) -> bool:
        return len(self.structured_facts) > 0

    @property
    def has_narrative(self) -> bool:
        return len(self.narrative) > 40

    @property
    def is_valid(self) -> bool:
        """Minimum bar: at least one structured fact AND some narrative."""
        return self.has_structured and self.has_narrative

    def as_context(self) -> str:
        """Flat string context for the LLM prompt."""
        parts = []
        if self.structured_facts:
            parts.append("STRUCTURED FACTS:\n" + "\n".join(f"• {f}" for f in self.structured_facts[:5]))
        if self.narrative:
            parts.append(f"NARRATIVE CONTEXT:\n{self.narrative[:600]}")
        if self.hf_pattern:
            parts.append(f"QUESTION PATTERN (inspiration only):\n{self.hf_pattern}")
        return "\n\n".join(parts)


# ─── DBpedia fetcher ──────────────────────────────────────────────────────────

DBPEDIA_ENDPOINT = "https://dbpedia.org/sparql"

_DBPEDIA_QUERIES: dict[str, str] = {
    "Geography": """
        PREFIX dbo: <http://dbpedia.org/ontology/>
        PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
        SELECT ?name ?abstract WHERE {
          ?entity a dbo:Country ;
                  rdfs:label ?name ;
                  dbo:abstract ?abstract .
          FILTER(lang(?name) = 'en' && lang(?abstract) = 'en')
        } LIMIT 5
    """,
    "History": """
        PREFIX dbo: <http://dbpedia.org/ontology/>
        PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
        SELECT ?name ?abstract WHERE {
          ?entity a dbo:HistoricalEvent ;
                  rdfs:label ?name ;
                  dbo:abstract ?abstract .
          FILTER(lang(?name) = 'en' && lang(?abstract) = 'en')
        } LIMIT 5
    """,
}


def _normalize_topic_family(topic: str) -> str:
    normalized = (topic or "Mixed").strip().title()
    if normalized in {"History", "Geography"}:
        return normalized
    if normalized == "Mixed":
        return random.choice(["History", "Geography"])
    return "History"


def _dedupe_facts(facts: list[str], limit: int = 5) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for fact in facts:
        cleaned = " ".join(str(fact or "").split())
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(cleaned)
        if len(deduped) >= limit:
            break
    return deduped


async def _fetch_wikidata_structured_facts(
    topic: str,
    difficulty: int,
    client: httpx.AsyncClient,
) -> list[str]:
    facts = await fetch_wikidata_facts(_normalize_topic_family(topic), difficulty, client)
    if not facts:
        return []
    context = format_wikidata_as_context(facts, n=5)
    return [line.strip() for line in context.splitlines() if line.strip()]


async def _fetch_wikipedia_narrative(
    topic: str,
    difficulty: int,
    client: httpx.AsyncClient,
) -> str:
    wiki_topic = (topic or "Mixed").strip().title()
    if wiki_topic not in {"History", "Geography", "Mixed"}:
        wiki_topic = "Mixed"
    result = await fetch_wikipedia_context(wiki_topic, difficulty, client, n_sentences=4)
    if not result:
        return ""
    return str(result.get("context") or "").strip()


async def _fetch_dbpedia(topic: str, client: httpx.AsyncClient) -> list[str]:
    """
    Run a DBpedia SPARQL query and return a list of fact strings.
    Returns [] on any network failure (graceful fallback).
    """
    query = _DBPEDIA_QUERIES.get(topic, _DBPEDIA_QUERIES.get("Geography", ""))
    if not query:
        return []
    try:
        resp = await client.get(
            DBPEDIA_ENDPOINT,
            params={"query": query, "format": "json"},
            headers={
                "Accept": "application/sparql-results+json, application/json;q=0.9",
                "User-Agent": "AdaptIQ/1.0",
            },
            timeout=8.0,
        )
        if resp.status_code != 200:
            return []
        bindings = resp.json().get("results", {}).get("bindings", [])
        facts = []
        for row in bindings[:3]:
            name  = row.get("name",     {}).get("value", "")
            abstract = row.get("abstract", {}).get("value", "")
            if name and abstract:
                # Keep only first sentence of abstract to avoid token bloat
                first_sentence = abstract.split(".")[0].strip() + "."
                facts.append(f"{name}: {first_sentence}")
        return facts
    except Exception as e:
        logger.debug(f"DBpedia fetch failed (non-fatal): {e}")
        return []


# ─── World Bank API fetcher ───────────────────────────────────────────────────

_WB_INDICATOR_MAP = {
    "Geography": [
        ("AG.SRF.TOTL.K2", "surface area km²"),
    ],
    "History": [],
    "Mixed": [],
}

_WB_COUNTRY_CODES = [
    "FR", "DE", "US", "GB", "BR", "CN", "IN", "JP", "ZA", "EG",
    "AU", "CA", "NG", "KE", "MX", "AR", "ID", "TR", "SA", "KR",
]


async def _fetch_worldbank(topic: str, client: httpx.AsyncClient) -> list[str]:
    """
    Pull 1 random country + 1-2 indicators from World Bank API.
    Returns a list of fact strings like ["France population: 68,000,000 (2022)"].
    """
    indicators = _WB_INDICATOR_MAP.get(topic, [])
    if not indicators:
        return []
    country    = random.choice(_WB_COUNTRY_CODES)
    facts      = []
    for indicator, label in indicators[:2]:
        url = (
            f"https://api.worldbank.org/v2/country/{country}"
            f"/indicator/{indicator}?format=json&mrv=1"
        )
        try:
            resp = await client.get(url, timeout=7.0)
            if resp.status_code != 200:
                continue
            data = resp.json()
            # World Bank returns [metadata, [values]]
            if len(data) < 2 or not data[1]:
                continue
            entry = data[1][0]
            value = entry.get("value")
            year  = entry.get("date", "")
            country_name = entry.get("country", {}).get("value", country)
            if value is not None:
                formatted = f"{int(value):,}" if value > 1000 else f"{value:.2f}"
                facts.append(f"{country_name} {label}: {formatted} ({year})")
        except Exception as e:
            logger.debug(f"World Bank fetch failed for {indicator}: {e}")
    return facts


# ─── UN Data API fetcher ──────────────────────────────────────────────────────

async def _fetch_undata(topic: str, client: httpx.AsyncClient) -> list[str]:
    """
    Minimal UN Data REST call — pulls GDP/population data as structured facts.
    Falls back silently if unavailable (UN Data has rate limits).
    """
    # UN comtrade / UNdata REST — use a simple population series
    try:
        url = "https://data.un.org/ws/rest/data/DF_UNData_WDI/A.SP_POP_TOTL../ALL/?detail=full&lastNObservations=1&format=jsondata"
        resp = await client.get(url, timeout=6.0, headers={"Accept": "application/json"})
        if resp.status_code != 200:
            return []
        # UN Data returns SDMX-JSON — structure is complex, extract 2-3 observations
        data   = resp.json()
        series = data.get("dataSets", [{}])[0].get("series", {})
        facts  = []
        for key, val in list(series.items())[:3]:
            obs = val.get("observations", {})
            if obs:
                value = list(obs.values())[0][0]
                if value:
                    facts.append(f"UN population data point: {int(value):,}")
        return facts[:2]
    except Exception as e:
        logger.debug(f"UN Data fetch failed (non-fatal): {e}")
        return []


# ─── Source blender ───────────────────────────────────────────────────────────

class SourceBlender:
    """
    Orchestrates the 40/40/20 source mix and assembles a SourceBundle.

    Fetch strategy (with graceful degradation):
      1. Try all structured sources in parallel (Wikidata, DBpedia, WB, UN).
      2. Fetch Wikipedia narrative.
      3. Optionally attach an HF question pattern.
      4. If structured sources all fail, fall back to Wikipedia-only context
         (the validator will still accept if narrative quality is high enough).
    """

    async def blend(
        self,
        topic: str,
        difficulty: int,
        http_client: httpx.AsyncClient,
        wikidata_facts: Optional[list[str]] = None,    # pre-fetched from existing agentic.py
        wiki_narrative: Optional[str] = None,           # pre-fetched from existing agentic.py
        hf_question: Optional[dict] = None,             # pre-fetched from existing hf_dataset.py
    ) -> SourceBundle:
        """
        Build a SourceBundle.

        Existing agentic pipeline results can be passed in directly to avoid
        double-fetching. Missing narrative / structured context is fetched here.
        """
        bundle  = SourceBundle(topic=topic, difficulty=difficulty)
        sources = []
        topic_family = (topic or "Mixed").strip().title()

        # ── Concurrent fetches for the pieces we don't already have ───────────
        # Wikidata (structured), DBpedia (structured), the Wikipedia narrative,
        # and the HF QA pattern have no data dependencies on each other, so fetch
        # the missing ones in parallel instead of serializing four slow
        # third-party calls. WorldBank stays a sequential fallback below because
        # it only runs when Wikidata + DBpedia yielded nothing — parallelizing it
        # would issue a network call that the sequential version never made.
        # Each helper swallows its own errors and returns a safe default so
        # gather never raises and the assembled result set is identical to the
        # previous sequential implementation.
        async def _fetch_wikidata_missing() -> list[str]:
            try:
                fetched = await _fetch_wikidata_structured_facts(topic_family, difficulty, http_client)
                return list(fetched or [])[:3]
            except Exception as e:
                logger.debug(f"Wikidata skipped: {e}")
                return []

        async def _fetch_dbpedia_facts() -> list[str]:
            try:
                facts = await _fetch_dbpedia(_normalize_topic_family(topic_family), http_client)
                return list(facts or [])
            except Exception as e:
                logger.debug(f"DBpedia skipped: {e}")
                return []

        async def _fetch_narrative_missing() -> str:
            try:
                return await _fetch_wikipedia_narrative(topic_family, difficulty, http_client)
            except Exception as e:
                logger.debug(f"Wikipedia narrative skipped: {e}")
                return ""

        async def _fetch_hf_missing() -> Optional[dict]:
            try:
                return await async_get_hf_question(topic_family, difficulty)
            except Exception as e:
                logger.debug(f"HF pattern skipped: {e}")
                return None

        wikidata_coro = _fetch_wikidata_missing() if not wikidata_facts else None
        narrative_coro = _fetch_narrative_missing() if not wiki_narrative else None
        hf_coro = _fetch_hf_missing() if not hf_question else None
        dbpedia_coro = _fetch_dbpedia_facts()

        # Gather only the coroutines we actually created, keyed by name.
        pending = {
            name: coro
            for name, coro in (
                ("wikidata", wikidata_coro),
                ("dbpedia", dbpedia_coro),
                ("narrative", narrative_coro),
                ("hf", hf_coro),
            )
            if coro is not None
        }
        gathered = dict(zip(pending.keys(), await asyncio.gather(*pending.values())))

        # ── Structured facts (40%) — assembled in the original order ──────────
        structured: list[str] = []

        if wikidata_facts:
            structured.extend(wikidata_facts[:3])
            sources.extend([f"wikidata:{i}" for i in range(len(wikidata_facts[:3]))])
        else:
            fetched_wikidata = gathered.get("wikidata") or []
            if fetched_wikidata:
                structured.extend(fetched_wikidata)
                sources.extend([f"wikidata:{i}" for i in range(len(fetched_wikidata))])

        dbpedia_facts = gathered.get("dbpedia") or []
        if dbpedia_facts:
            structured.extend(dbpedia_facts[:2])
            sources.extend([f"dbpedia:{i}" for i in range(len(dbpedia_facts[:2]))])
            logger.debug(f"DBpedia returned {len(dbpedia_facts)} facts")

        if not structured and topic_family == "Geography":
            try:
                wb_facts = await _fetch_worldbank(topic_family, http_client)
                if wb_facts:
                    structured.extend(wb_facts)
                    sources.extend([f"worldbank:{i}" for i in range(len(wb_facts))])
                    logger.debug(f"WorldBank returned {len(wb_facts)} facts")
            except Exception as e:
                logger.debug(f"WorldBank skipped: {e}")

        bundle.structured_facts = _dedupe_facts(structured)

        # ── Narrative / Wikipedia (40%) ───────────────────────────────────────
        if wiki_narrative:
            bundle.narrative = wiki_narrative
            sources.append("wiki:narrative")
        else:
            bundle.narrative = gathered.get("narrative") or ""
            if bundle.narrative:
                sources.append("wiki:narrative")

        # ── HF QA pattern (20%) ──────────────────────────────────────────────
        if hf_question:
            bundle.hf_pattern = hf_question.get("question", "")
            q_id = hf_question.get("id", "0")
            sources.append(f"squad:{q_id}")
        else:
            fetched_hf = gathered.get("hf")
            if fetched_hf and fetched_hf.get("question"):
                bundle.hf_pattern = str(fetched_hf.get("question") or "")
                sources.append(f"squad:{fetched_hf.get('id', '0')}")

        bundle.sources = sources

        if not bundle.has_structured and not bundle.has_narrative:
            logger.warning(f"[Blender] No sources available for topic={topic!r}")
        else:
            logger.info(
                f"[Blender] topic={topic!r} "
                f"structured={len(bundle.structured_facts)} "
                f"narrative={len(bundle.narrative)}chars "
                f"hf={'yes' if bundle.hf_pattern else 'no'}"
            )

        return bundle


    def build_blend_prompt(self, bundle: SourceBundle) -> str:
        """
        Format the blended context into a prompt section for the LLM.

        Example output:
          STRUCTURED FACTS:
          • Paris: capital of France, population 2.16M (2023)
          • France GDP: $2.78T (2022)

          NARRATIVE CONTEXT:
          Paris, often called the City of Light, has been the cultural
          capital of Europe for centuries...

          QUESTION PATTERN (inspiration only):
          What is the estimated population of the capital city of France?
        """
        return bundle.as_context()
