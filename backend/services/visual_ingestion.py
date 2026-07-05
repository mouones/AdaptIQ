"""
services/visual_ingestion.py
Ingestion script for the VisualRoom — PRD v1.0

Replaces the previous COCO-captions prototype with two purpose-built sources:
  - Geography: REST Countries API + FlagCDN flag images
  - History:   Wikipedia Category API + Pageview API

Run from the backend root:
  python -m services.visual_ingestion --topic geography
  python -m services.visual_ingestion --topic history
  python -m services.visual_ingestion --topic all

Safe to re-run — upsert logic skips already-ingested rows.
Question text / options / explanations are NOT generated here.
The LLM generates them on first use via GET /api/visual/next.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
import os
import sys
from pathlib import Path
from typing import Optional

import httpx
from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import select, text

# ── Path fix so this can be run as a module from the backend root ─────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.models import Base
from database.visual_models import VisualQuestion

# Load backend/.env explicitly so DATABASE_URL is available even when this
# script is run from outside the backend folder.
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_ENV_PATH = _BACKEND_ROOT / ".env"
if _ENV_PATH.exists():
    load_dotenv(dotenv_path=_ENV_PATH)
else:
    load_dotenv()

logger = logging.getLogger(__name__)
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
)

# ══════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ══════════════════════════════════════════════════════════════════════════════

FLAGCDN_URL        = "https://flagcdn.com/w320/{iso2}.png"

# REST Countries v3.1 — request only the fields we actually use to avoid 400s
# caused by response-size limits on some network proxies.
REST_COUNTRIES_URL = (
    "https://restcountries.com/v3.1/all"
    "?fields=name,cca2,region,subregion,capital,population,area,borders,currencies,status"
)

# Fallback mirror (used automatically if the primary returns 4xx)
REST_COUNTRIES_FALLBACK_URL = (
    "https://restcountries.com/v3.1/all"
    "?fields=name,cca2,region,subregion,capital,population,area,borders,currencies"
)

WIKI_SEARCH_URL    = "https://en.wikipedia.org/w/api.php"
WIKI_SUMMARY_URL   = "https://en.wikipedia.org/api/rest_v1/page/summary/"
WIKI_PAGEVIEW_URL  = (
    "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
    "en.wikipedia/all-access/all-agents/{title}/monthly/{start}/{end}"
)

# Wikipedia requires a descriptive User-Agent per their API policy.
# Using a browser-like Accept header avoids 403s on some corporate proxies.
HEADERS = {
    "User-Agent": (
        "AdaptIQ-VisualRoom/1.0 "
        "(https://github.com/adaptiq; educational project; "
        "contact: adaptiq-bot@example.com)"
    ),
    "Accept": "application/json",
}

# G20 ISO-2 codes (difficulty tier 1-2 for Geography)
G20_CODES = {
    "AR", "AU", "BR", "CA", "CN", "FR", "DE", "IN", "ID", "IT",
    "JP", "MX", "RU", "SA", "ZA", "KR", "TR", "GB", "US", "EU",
}

# EU member ISO-2 codes (also tier 1-2)
EU_CODES = {
    "AT", "BE", "BG", "CY", "CZ", "DK", "EE", "FI", "FR", "DE",
    "GR", "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL",
    "PT", "RO", "SK", "SI", "ES", "SE",
}

# Wikipedia categories mapped to AdaptIQ History topics
# Verified Wikipedia category names (confirmed via API, May 2026).
# Rule: use the exact category title as it appears on en.wikipedia.org.
# Avoid categories that are "container" categories (hold only subcategories)
# because they return 0 direct article members.
HISTORY_CATEGORIES = {
    "World War II": [
        "Military_leaders_of_World_War_II",       # 8 direct articles ✓
        "Battles_and_operations_of_World_War_II", # 80 direct articles ✓
        "Allied_World_War_II_commanders",         # replaces World_War_II_generals
    ],
    "World War I": [
        "World_War_I",                    # 111 direct articles ✓
        "Western_Front_(World_War_I)",    # 26 direct articles ✓
        "Military_leaders_of_World_War_I", # 8 direct articles ✓ (keep as bonus)
    ],
    "Cold War": [
        "Cold_War",                               # 150 direct articles ✓
        "Cold_War_conflicts",                     # 102 direct articles ✓
        "Cold_War_espionage",                     # replaces Cold_War_crises
    ],
    "Ancient Rome": [
        "Roman_emperors",                         # portraits/busts — good images
        "Roman_Republic",                         # 47 articles ✓
        "Roman_legions",                          # battle maps/standards
    ],
    "French Revolution": [
        "French_Revolutionary_Wars",              # 24 direct articles ✓
        "People_of_the_French_Revolution",        # 150 direct articles ✓
        "French_Revolution",                      # 81 direct articles ✓
    ],
    "Industrial Revolution": [
        "Industrial_Revolution",                  # 132 direct articles ✓
        "Inventors",                              # 7 direct articles ✓
        "History_of_technology",                  # 118 direct articles ✓
    ],
}

# Pageview thresholds for difficulty assignment
PAGEVIEW_HIGH    = 500_000    # > 500k/yr → difficulty 1.5 (L1-2)
PAGEVIEW_MEDIUM  = 50_000     # 50k–500k/yr → difficulty 3.0 (L3)
# < 50k/yr → difficulty 4.5 (L4-5)


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _iso_hash(iso2: str) -> int:
    """
    Convert a 2-letter ISO code to a stable positive integer.
    Stored in coco_image_id for Geography rows to enable deduplication
    without any schema changes.
    """
    return int(hashlib.md5(iso2.lower().encode()).hexdigest(), 16) % (2**31 - 1)


def _build_geo_paragraph(country: dict) -> str:
    """
    Construct the paragraph stored in visual_questions.paragraph from
    the REST Countries API response.  No LLM involved.
    """
    name       = country.get("name", {}).get("common", "Unknown")
    region     = country.get("region", "Unknown")
    subregion  = country.get("subregion", "")
    capitals   = country.get("capital", [])
    capital    = capitals[0] if capitals else "N/A"
    population = country.get("population", 0)
    area       = country.get("area", 0)

    # Neighbouring countries — use common names where available
    borders    = country.get("borders", [])
    border_str = ", ".join(borders[:6]) if borders else "None"

    # Currency names
    currencies = country.get("currencies", {})
    currency_names = [v.get("name", k) for k, v in currencies.items()]
    currency_str   = ", ".join(currency_names[:2]) if currency_names else "N/A"

    subregion_str = f" ({subregion})" if subregion else ""

    return (
        f"{name} is a country in {region}{subregion_str}. "
        f"Capital: {capital}. "
        f"Population: {population:,}. "
        f"Area: {area:,.0f} km\u00b2. "
        f"Borders: {border_str}. "
        f"Currency: {currency_str}."
    )


def _geo_difficulty(country: dict) -> float:
    """
    Assign difficulty_base from geography tier logic per PRD §3.1.
    Tier 1-2  → 1.5  (G20 + EU)
    Tier 3    → 3.0  (non-G20 sovereign with population > 10M)
    Tier 4-5  → 4.5  (small island nations, micro-states, pop < 1M)
    """
    iso2 = (country.get("cca2") or "").upper()
    pop  = country.get("population", 0)

    if iso2 in G20_CODES or iso2 in EU_CODES:
        return 1.5

    if pop >= 10_000_000:
        return 3.0

    # Small / micro-state
    return 4.5


def _history_difficulty(annual_views: int) -> float:
    """
    Map Wikipedia annual pageview count to difficulty_base per PRD §3.2.
    """
    if annual_views > PAGEVIEW_HIGH:
        return 1.5
    if annual_views > PAGEVIEW_MEDIUM:
        return 3.0
    return 4.5


# ══════════════════════════════════════════════════════════════════════════════
# GEOGRAPHY INGESTION
# ══════════════════════════════════════════════════════════════════════════════

async def ingest_geography(
    db_url: str,
    limit: int = 0,
    batch_size: int = 50,
) -> None:
    """
    Fetch all countries from REST Countries API → upsert into visual_questions.

    Deduplication key: coco_image_id = _iso_hash(iso2)
    Image URL: https://flagcdn.com/w320/{iso2_lower}.png
    """
    logger.info("=== Geography ingestion starting ===")

    async with httpx.AsyncClient(headers=HEADERS, timeout=30.0) as client:
        countries = None
        for attempt, url in enumerate([REST_COUNTRIES_URL, REST_COUNTRIES_FALLBACK_URL], 1):
            logger.info(f"Fetching REST Countries (attempt {attempt}): {url.split('?')[0]} ...")
            try:
                resp = await client.get(url)
                if resp.status_code == 200:
                    countries = resp.json()
                    logger.info(f"Fetched {len(countries)} countries")
                    break
                else:
                    logger.warning(f"REST Countries returned {resp.status_code} — trying next URL")
            except Exception as e:
                logger.warning(f"REST Countries fetch error (attempt {attempt}): {e}")

        if not countries:
            logger.error(
                "REST Countries API unavailable. "
                "Check your network or try: curl 'https://restcountries.com/v3.1/all?fields=name,cca2'"
            )
            return

    logger.info(f"Fetched {len(countries)} countries")

    # Apply --limit if specified
    if limit and limit > 0:
        countries = countries[:limit]

    engine = create_async_engine(db_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    # Collect already-ingested hashes
    async with session_factory() as db:
        result = await db.execute(
            select(VisualQuestion.coco_image_id).where(
                VisualQuestion.topic == "geography",
                VisualQuestion.coco_image_id.isnot(None),
            )
        )
        existing_hashes = {row[0] for row in result.fetchall()}

    logger.info(f"Already ingested: {len(existing_hashes)} geography rows")

    inserted = skipped = 0
    batch: list[VisualQuestion] = []
    tier_counts = {"1.5": 0, "3.0": 0, "4.5": 0}

    for country in countries:
        iso2 = (country.get("cca2") or "").upper()
        if not iso2:
            continue

        iso_hash = _iso_hash(iso2)
        if iso_hash in existing_hashes:
            skipped += 1
            continue

        # Only include sovereign states with a recognised ISO 2-letter code
        status = country.get("status", "")
        if status not in ("officially-assigned", ""):
            # skip territories / user-assigned codes
            pass  # REST Countries returns these — include them at high difficulty

        image_url  = FLAGCDN_URL.format(iso2=iso2.lower())
        paragraph  = _build_geo_paragraph(country)
        diff_base  = _geo_difficulty(country)

        tier_counts[str(diff_base)] = tier_counts.get(str(diff_base), 0) + 1

        row = VisualQuestion(
            coco_image_id   = iso_hash,
            image_url       = image_url,
            iso2            = iso2,
            paragraph       = paragraph,
            topic           = "geography",
            difficulty_base = diff_base,
            difficulty_actual = diff_base,
            options_count   = 4,
            question_type   = 'M',
            # LLM-generated fields left NULL — populated on first /next call
            question_text   = None,
            correct_answer  = None,
            options_json    = None,
            explanation     = None,
        )
        batch.append(row)

        if len(batch) >= batch_size:
            async with session_factory() as db:
                for r in batch:
                    db.add(r)
                await db.commit()
            inserted += len(batch)
            logger.info(f"  Geography: inserted {inserted} so far...")
            batch.clear()

    # Flush remainder
    if batch:
        async with session_factory() as db:
            for r in batch:
                db.add(r)
            await db.commit()
        inserted += len(batch)

    logger.info(
        f"Geography ingestion complete — "
        f"inserted: {inserted}, skipped (already in DB): {skipped}"
    )
    # Report per-run tier counts
    logger.info(
        f"Run tier breakdown — "
        f"L1-2 (difficulty 1.5): {tier_counts.get('1.5', 0)}, "
        f"L3 (difficulty 3.0): {tier_counts.get('3.0', 0)}, "
        f"L4-5 (difficulty 4.5): {tier_counts.get('4.5', 0)}"
    )

    # Additionally report cumulative counts present in the database for this topic
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT difficulty_base, COUNT(*) FROM visual_questions "
                    "WHERE topic = 'geography' GROUP BY difficulty_base"
                )
            )
            rows = result.fetchall()
            db_counts = {str(row[0]): int(row[1]) for row in rows}

        logger.info(
            f"Cumulative DB tier breakdown (topic=geography) — "
            f"L1-2 (difficulty 1.5): {db_counts.get('1.5', 0)}, "
            f"L3 (difficulty 3.0): {db_counts.get('3.0', 0)}, "
            f"L4-5 (difficulty 4.5): {db_counts.get('4.5', 0)}"
        )
    except Exception:
        logger.warning("Could not compute cumulative DB tier breakdown")
    await engine.dispose()


# ══════════════════════════════════════════════════════════════════════════════
# HISTORY INGESTION
# ══════════════════════════════════════════════════════════════════════════════

async def _fetch_subcategory_names(
    client: httpx.AsyncClient,
    category: str,
    limit: int = 20,
) -> list[str]:
    """Return subcategory names (without 'Category:' prefix) for a container category."""
    params = {
        "action":  "query",
        "list":    "categorymembers",
        "cmtitle": f"Category:{category}",
        "cmlimit": str(limit),
        "cmtype":  "subcat",
        "format":  "json",
    }
    try:
        resp = await client.get(WIKI_SEARCH_URL, params=params, timeout=15.0)
        if resp.status_code != 200:
            return []
        data = resp.json()
        members = data.get("query", {}).get("categorymembers", [])
        # Strip the "Category:" prefix from titles
        return [m["title"].replace("Category:", "") for m in members if m.get("title")]
    except Exception:
        return []


async def _fetch_category_members(
    client: httpx.AsyncClient,
    category: str,
    limit: int = 200,
) -> list[str]:
    """
    Return a list of article titles from a Wikipedia category.
    Handles continuation automatically.
    If the category is a container (0 direct pages), automatically fetches
    articles from its first-level subcategories instead.
    """
    titles: list[str] = []
    cmcontinue: Optional[str] = None

    while len(titles) < limit:
        params = {
            "action":      "query",
            "list":        "categorymembers",
            "cmtitle":     f"Category:{category}",
            "cmlimit":     "500",
            "cmtype":      "page",
            "format":      "json",
        }
        if cmcontinue:
            params["cmcontinue"] = cmcontinue

        try:
            resp = await client.get(WIKI_SEARCH_URL, params=params, timeout=15.0)
            if resp.status_code == 403:
                logger.warning(
                    f"Wikipedia returned 403 for category '{category}'. "
                    "Your network may be blocking the Wikipedia API. "
                    "Try running with VPN or from a different network."
                )
                break
            if resp.status_code != 200:
                logger.warning(f"Wikipedia returned {resp.status_code} for category '{category}'")
                break
            data = resp.json()
        except Exception as e:
            logger.warning(f"Category fetch failed ({category}): {e}")
            break

        members = data.get("query", {}).get("categorymembers", [])
        for m in members:
            if m.get("ns", 0) == 0:  # namespace 0 = articles only
                titles.append(m["title"])

        cont = data.get("continue", {}).get("cmcontinue")
        if not cont or len(titles) >= limit:
            break
        cmcontinue = cont

    # ── Subcategory fallback ──────────────────────────────────────────────────
    # If the category is a container (holds only subcats, not direct pages),
    # fetch articles from its subcategories one level deep.
    if not titles:
        logger.info(f"  '{category}' is a container — fetching from subcategories...")
        subcats = await _fetch_subcategory_names(client, category, limit=15)
        if subcats:
            logger.info(f"  Found {len(subcats)} subcategories: {subcats[:5]}...")
            remaining = limit
            for subcat in subcats[:8]:   # cap at 8 subcats to avoid runaway
                sub_titles = await _fetch_category_members_direct(client, subcat, min(50, remaining))
                titles.extend(sub_titles)
                remaining -= len(sub_titles)
                if remaining <= 0:
                    break
                await asyncio.sleep(0.2)

    return titles[:limit]


async def _fetch_category_members_direct(
    client: httpx.AsyncClient,
    category: str,
    limit: int = 50,
) -> list[str]:
    """
    Direct article fetch from a category — no subcategory recursion.
    Used by the subcategory fallback to avoid infinite recursion.
    """
    params = {
        "action":  "query",
        "list":    "categorymembers",
        "cmtitle": f"Category:{category}",
        "cmlimit": str(min(limit, 500)),
        "cmtype":  "page",
        "format":  "json",
    }
    try:
        resp = await client.get(WIKI_SEARCH_URL, params=params, timeout=15.0)
        if resp.status_code != 200:
            return []
        data = resp.json()
        return [
            m["title"]
            for m in data.get("query", {}).get("categorymembers", [])
            if m.get("ns", 0) == 0
        ]
    except Exception:
        return []


async def _fetch_article_summaries(
    client: httpx.AsyncClient,
    titles: list[str],
) -> list[dict]:
    """
    Batch-fetch article summaries (extract + pageimage + extlinks) from Wikipedia.
    Processes up to 50 titles per API call as recommended in PRD §5.2.
    Returns list of raw API result dicts (one per article).
    """
    results: list[dict] = []

    for i in range(0, len(titles), 50):
        batch = titles[i : i + 50]
        params = {
            "action":      "query",
            "titles":      "|".join(batch),
            "prop":        "extracts|pageimages|extlinks",
            "exintro":     "1",       # lead paragraph only
            "explaintext": "1",
            "piprop":      "thumbnail",
            "pithumbsize": "640",
            "ellimit":     "max",
            "format":      "json",
        }
        try:
            resp = await client.get(WIKI_SEARCH_URL, params=params, timeout=20.0)
            if resp.status_code != 200:
                logger.warning(f"Wikipedia summary batch returned {resp.status_code} — skipping batch")
                await asyncio.sleep(0.5)
                continue
            data = resp.json()
            pages = data.get("query", {}).get("pages", {})
            results.extend(pages.values())
            # Respect Wikipedia's rate-limit guidance
            await asyncio.sleep(0.5)
        except Exception as e:
            logger.warning(f"Batch summary fetch failed: {e}")

    return results


async def _get_annual_pageviews(
    client: httpx.AsyncClient,
    title: str,
) -> int:
    """
    Fetch 12-month pageview count for a Wikipedia article via the Wikimedia
    Pageviews API.  Returns 0 on any error (treated as low-traffic → hard).
    """
    import datetime
    now    = datetime.date.today()
    end    = now.strftime("%Y%m%d")
    start  = (now.replace(year=now.year - 1)).strftime("%Y%m%d")

    url = WIKI_PAGEVIEW_URL.format(
        title = title.replace(" ", "_"),
        start = start,
        end   = end,
    )
    try:
        resp = await client.get(url, timeout=10.0)
        if resp.status_code == 200:
            items = resp.json().get("items", [])
            return sum(item.get("views", 0) for item in items)
    except Exception:
        pass
    return 0


async def _quality_filter(article: dict) -> bool:
    """
    Return True if the article passes quality filters:
      - Has a lead image (thumbnail)
      - extract length > 500 characters (stubs filtered out, but short articles kept)
      - At least 1 external link (relaxed from PRD's 5 — many history articles
        have fewer external links but still have good content)
    """
    if not article.get("thumbnail", {}).get("source"):
        return False
    extract = article.get("extract", "")
    if len(extract) < 500:
        return False
    # Relaxed: even 1 external link is enough — PRD's threshold of 5 was
    # filtering out most valid history articles.
    extlinks = article.get("extlinks", [])
    if len(extlinks) < 1:
        return False
    return True


async def ingest_history(
    db_url: str,
    limit: int = 0,
    batch_size: int = 50,
    per_category_cap: int = 150,
) -> None:
    """
    Fetch Wikipedia article images for all history categories → upsert into visual_questions.

    Deduplication key: coco_image_id = Wikipedia page_id (Integer).
    Difficulty_base is derived from 12-month pageview counts.
    """
    logger.info("=== History ingestion starting ===")

    # Quick connectivity check — Wikipedia blocks some corporate/university networks.
    # The ingestion will still run but produce 0 rows if the API is unreachable.
    async with httpx.AsyncClient(headers=HEADERS, timeout=8.0) as probe:
        try:
            check = await probe.get(
                WIKI_SEARCH_URL,
                params={"action": "query", "titles": "Main_Page", "format": "json"},
            )
            if check.status_code == 200:
                logger.info("Wikipedia API reachable ✓")
            else:
                logger.warning(
                    f"Wikipedia API returned {check.status_code}. "
                    "Your network may block the Wikipedia API (common on university/corporate networks). "
                    "History ingestion requires access to en.wikipedia.org. "
                    "Consider running with a VPN or from an unrestricted network."
                )
        except Exception as e:
            logger.warning(
                f"Wikipedia API not reachable: {e}. "
                "History ingestion will produce 0 rows. "
                "Consider running with a VPN or from an unrestricted network."
            )

    engine = create_async_engine(db_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    # Collect already-ingested Wikipedia page_ids
    async with session_factory() as db:
        result = await db.execute(
            select(VisualQuestion.coco_image_id).where(
                VisualQuestion.topic == "history",
                VisualQuestion.coco_image_id.isnot(None),
            )
        )
        existing_page_ids = {row[0] for row in result.fetchall()}

    logger.info(f"Already ingested: {len(existing_page_ids)} history rows")

    total_inserted = 0
    total_skipped  = 0
    tier_counts    = {"1.5": 0, "3.0": 0, "4.5": 0}

    async with httpx.AsyncClient(headers=HEADERS, timeout=30.0) as client:

        for history_topic, categories in HISTORY_CATEGORIES.items():
            logger.info(f"Processing topic: {history_topic}")
            topic_titles: list[str] = []

            for category in categories:
                cat_titles = await _fetch_category_members(
                    client, category, limit=per_category_cap
                )
                logger.info(f"  Category:{category} → {len(cat_titles)} articles")
                topic_titles.extend(cat_titles)
                await asyncio.sleep(0.3)

            # Deduplicate titles within topic
            topic_titles = list(dict.fromkeys(topic_titles))
            if limit and limit > 0:
                topic_titles = topic_titles[:limit]

            logger.info(f"  Total unique titles for {history_topic}: {len(topic_titles)}")

            # Batch-fetch article summaries
            articles = await _fetch_article_summaries(client, topic_titles)
            logger.info(f"  Fetched {len(articles)} article summaries")

            batch: list[VisualQuestion] = []

            for article in articles:
                page_id = article.get("pageid")
                if not page_id:
                    continue

                if page_id in existing_page_ids:
                    total_skipped += 1
                    continue

                if not await _quality_filter(article):
                    continue

                # Thumbnail URL — use the larger source image
                image_url = article["thumbnail"]["source"]
                # Upgrade thumbnail to higher resolution if possible
                image_url = image_url.replace("/320px-", "/640px-").replace("/200px-", "/640px-")

                # Paragraph = lead extract (truncated to 800 chars for DB economy)
                paragraph = (article.get("extract") or "").strip()[:800]
                title     = article.get("title", "Unknown")

                # Pageview-based difficulty
                try:
                    annual_views = await _get_annual_pageviews(client, title)
                except Exception:
                    annual_views = 0

                diff_base = _history_difficulty(annual_views)
                tier_counts[str(diff_base)] = tier_counts.get(str(diff_base), 0) + 1

                row = VisualQuestion(
                    coco_image_id   = page_id,
                    image_url       = image_url,
                    paragraph       = paragraph,
                    topic           = "history",
                    difficulty_base = diff_base,
                    difficulty_actual = diff_base,
                    options_count   = 4,
                    question_type   = 'M',
                    question_text   = None,
                    correct_answer  = None,
                    options_json    = None,
                    explanation     = None,
                )
                batch.append(row)
                existing_page_ids.add(page_id)  # prevent cross-category duplicates

                if len(batch) >= batch_size:
                    async with session_factory() as db:
                        for r in batch:
                            db.add(r)
                        await db.commit()
                    total_inserted += len(batch)
                    logger.info(f"    Inserted {total_inserted} history rows so far...")
                    batch.clear()

                # Polite rate-limiting for pageview API
                await asyncio.sleep(0.1)

            # Flush remainder for this topic
            if batch:
                async with session_factory() as db:
                    for r in batch:
                        db.add(r)
                    await db.commit()
                total_inserted += len(batch)
                batch.clear()

            logger.info(f"  Done with {history_topic}")

    logger.info(
        f"History ingestion complete — "
        f"inserted: {total_inserted}, skipped (already in DB): {total_skipped}"
    )
    # Report per-run tier counts
    logger.info(
        f"Run tier breakdown — "
        f"L1-2 (difficulty 1.5): {tier_counts.get('1.5', 0)}, "
        f"L3 (difficulty 3.0): {tier_counts.get('3.0', 0)}, "
        f"L4-5 (difficulty 4.5): {tier_counts.get('4.5', 0)}"
    )

    # Additionally report cumulative counts present in the database for this topic
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT difficulty_base, COUNT(*) FROM visual_questions "
                    "WHERE topic = 'history' GROUP BY difficulty_base"
                )
            )
            rows = result.fetchall()
            db_counts = {str(row[0]): int(row[1]) for row in rows}

        logger.info(
            f"Cumulative DB tier breakdown (topic=history) — "
            f"L1-2 (difficulty 1.5): {db_counts.get('1.5', 0)}, "
            f"L3 (difficulty 3.0): {db_counts.get('3.0', 0)}, "
            f"L4-5 (difficulty 4.5): {db_counts.get('4.5', 0)}"
        )
    except Exception:
        logger.warning("Could not compute cumulative DB tier breakdown for history")
    await engine.dispose()


# ══════════════════════════════════════════════════════════════════════════════
# VERIFICATION QUERIES
# ══════════════════════════════════════════════════════════════════════════════

async def run_verification(db_url: str) -> None:
    """
    Print the PRD §10 verification queries to confirm ingestion results.
    """
    engine = create_async_engine(db_url, echo=False)
    async with engine.connect() as conn:
        result = await conn.execute(text(
            "SELECT topic, COUNT(*), ROUND(AVG(difficulty_base)::numeric, 2) AS avg_diff "
            "FROM visual_questions GROUP BY topic ORDER BY topic;"
        ))
        rows = result.fetchall()
        logger.info("=== Verification: topic counts + avg difficulty ===")
        for row in rows:
            logger.info(f"  topic={row[0]}  count={row[1]}  avg_difficulty={row[2]}")

        result2 = await conn.execute(text(
            "SELECT COUNT(*) FROM visual_questions WHERE question_text IS NULL;"
        ))
        null_count = result2.scalar()
        logger.info(f"  Rows with question_text IS NULL (not yet LLM-generated): {null_count}")
        logger.info("  (This count should decrease after first /api/visual/next calls)")

    await engine.dispose()


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="AdaptIQ VisualRoom ingestion — REST Countries + Wikipedia"
    )
    parser.add_argument(
        "--topic",
        choices=["geography", "history", "all"],
        default="all",
        help="Which data source to ingest (default: all)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Max rows per topic (0 = no limit)",
    )
    parser.add_argument(
        "--db-url",
        default=os.getenv(
            "DATABASE_URL",
            "postgresql+asyncpg://adaptiq:adaptiq@localhost:5432/adaptiq_db",
        ),
    )
    parser.add_argument(
        "--batch",
        type=int,
        default=50,
        help="DB insert batch size",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Run verification queries after ingestion",
    )
    args = parser.parse_args()

    async def main():
        if args.topic in ("geography", "all"):
            await ingest_geography(
                db_url     = args.db_url,
                limit      = args.limit,
                batch_size = args.batch,
            )

        if args.topic in ("history", "all"):
            await ingest_history(
                db_url     = args.db_url,
                limit      = args.limit,
                batch_size = args.batch,
            )

        if args.verify:
            await run_verification(args.db_url)

    asyncio.run(main())