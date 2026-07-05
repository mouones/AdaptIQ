"""
ingest_shapes_v2.py
Improved shape ingestion - tries 110m first, then 50m for missing countries.
Also handles France/Norway edge cases in Natural Earth's ISO2 assignments.

Run from backend/:
    python.exe scripts/visual/ingest_shapes_v2.py
"""

import json, os, urllib.request
from pathlib import Path
from urllib.parse import urlparse
import psycopg2
from dotenv import load_dotenv

from geo_shape import ensure_shape_columns

BACKEND_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = BACKEND_ROOT / "generated" / "visual_shapes"


def _load_env() -> None:
    backend_env = BACKEND_ROOT / ".env"
    if backend_env.exists():
        load_dotenv(backend_env)
    else:
        load_dotenv()


def _db_kwargs() -> dict:
    database_url = os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://adaptiq:adaptiq@localhost:5433/adaptiq_db",
    )
    parsed = urlparse(database_url.replace("postgresql+asyncpg://", "postgresql://", 1))
    return dict(
        host=parsed.hostname or "localhost",
        port=parsed.port or 5433,
        dbname=(parsed.path or "/adaptiq_db").lstrip("/"),
        user=parsed.username or "adaptiq",
        password=parsed.password or "adaptiq",
    )

GEOJSON_110M = DATA_DIR / "ne_110m_countries.geojson"
GEOJSON_50M = DATA_DIR / "ne_50m_countries.geojson"

GEOJSON_110M_URL = (
    "https://raw.githubusercontent.com/nvkelso/natural-earth-vector"
    "/master/geojson/ne_110m_admin_0_countries.geojson"
)

GEOJSON_50M_URL = (
    "https://raw.githubusercontent.com/nvkelso/natural-earth-vector"
    "/master/geojson/ne_50m_admin_0_countries.geojson"
)

SVG_WIDTH  = 200
SVG_HEIGHT = 150
PADDING    = 8
FILL_COLOR = "#4A90D9"

# Natural Earth uses non-standard ISO2 for some countries.
# This maps the REST Countries ISO2 to what Natural Earth actually stores.
ISO2_REMAP = {
    "FR": "FX",   # Natural Earth uses FX for metropolitan France
    "NO": "NO",   # Norway should be in 50m - no remap needed
}


def download_geojson(url, local_path):
    if os.path.exists(local_path) and os.path.getsize(local_path) > 50_000:
        print(f"  Using cached {local_path}")
        return True
    print(f"  Downloading {local_path}...")
    req = urllib.request.Request(url, headers={"User-Agent": "AdaptIQ-PFE/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
        with open(local_path, "wb") as f:
            f.write(data)
        print(f"  Downloaded {len(data):,} bytes")
        return True
    except Exception as e:
        print(f"  Download failed: {e}")
        return False


def load_features(path) -> dict:
    """Returns iso2_upper -> list of rings."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    lookup = {}
    for feature in data["features"]:
        props = feature.get("properties", {})
        iso2 = (
            props.get("ISO_A2") or props.get("iso_a2") or ""
        ).upper().strip()
        if not iso2 or iso2 in ("-1", ""):
            continue
        geom = feature.get("geometry", {})
        if not geom:
            continue
        rings = []
        if geom["type"] == "Polygon":
            rings.append(geom["coordinates"][0])
        elif geom["type"] == "MultiPolygon":
            for poly in geom["coordinates"]:
                rings.append(poly[0])
        if rings:
            # Store under both the original and uppercased key
            lookup[iso2] = rings
    return lookup


def rings_to_svg_path(rings, width, height, padding):
    all_lons, all_lats = [], []
    for ring in rings:
        for lon, lat in ring:
            all_lons.append(lon)
            all_lats.append(lat)
    if not all_lons:
        return ""
    min_lon, max_lon = min(all_lons), max(all_lons)
    min_lat, max_lat = min(all_lats), max(all_lats)
    lon_span = max_lon - min_lon or 1
    lat_span = max_lat - min_lat or 1
    draw_w = width  - 2 * padding
    draw_h = height - 2 * padding
    scale = min(draw_w / lon_span, draw_h / lat_span)
    scaled_w = lon_span * scale
    scaled_h = lat_span * scale
    offset_x = padding + (draw_w - scaled_w) / 2
    offset_y = padding + (draw_h - scaled_h) / 2

    def proj(lon, lat):
        x = round((lon - min_lon) * scale + offset_x, 2)
        y = round((max_lat - lat) * scale + offset_y, 2)
        return x, y

    parts = []
    for ring in rings:
        if len(ring) < 3:
            continue
        pts = " ".join(f"{proj(lon,lat)[0]},{proj(lon,lat)[1]}" for lon,lat in ring)
        parts.append(f"M {pts} Z")
    return " ".join(parts)


def make_svg(rings):
    path = rings_to_svg_path(rings, SVG_WIDTH, SVG_HEIGHT, PADDING)
    if not path:
        return ""
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {SVG_WIDTH} {SVG_HEIGHT}">'
        f'<path d="{path}" fill="{FILL_COLOR}" stroke="white" stroke-width="0.5"/>'
        f'</svg>'
    )


def main():
    _load_env()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ensure_shape_columns()

    # Load 110m (already on disk)
    print("Loading 110m dataset...")
    if not os.path.exists(GEOJSON_110M):
        print(f"  {GEOJSON_110M} missing, downloading...")
        download_geojson(GEOJSON_110M_URL, GEOJSON_110M)
    features_110 = load_features(GEOJSON_110M)
    print(f"  {len(features_110)} countries in 110m")

    # Download + load 50m for better small-country coverage
    print("Loading 50m dataset...")
    ok = download_geojson(GEOJSON_50M_URL, GEOJSON_50M)
    features_50 = load_features(GEOJSON_50M) if ok else {}
    print(f"  {len(features_50)} countries in 50m")

    # Merge: 110m first, fill gaps with 50m
    merged = {**features_50, **features_110}   # 110m wins on conflict
    print(f"  Merged: {len(merged)} unique countries")

    # Apply ISO2 remaps for Natural Earth quirks
    for our_iso2, ne_iso2 in ISO2_REMAP.items():
        if ne_iso2 in merged and our_iso2 not in merged:
            merged[our_iso2] = merged[ne_iso2]
            print(f"  Remap: {ne_iso2} - {our_iso2}")

    conn = psycopg2.connect(**_db_kwargs())
    cur  = conn.cursor()

    # One-time backfill: derive iso2 from the flag image URL for geography rows
    # that were ingested before iso2 was stored explicitly.
    cur.execute(
        """
        UPDATE visual_questions
        SET iso2 = upper(split_part(split_part(image_url, '/', 5), '.', 1))
        WHERE topic = 'geography'
          AND iso2 IS NULL
          AND image_url LIKE 'https://flagcdn.com/w320/%.png';
        """
    )
    conn.commit()

    # Fetch rows still missing shapes
    cur.execute("""
        SELECT id, iso2
        FROM visual_questions
        WHERE topic = 'geography'
        AND iso2 IS NOT NULL
        AND shape_svg IS NULL
        ORDER BY iso2;
    """)
    rows = cur.fetchall()
    print(f"\nRows still needing shapes: {len(rows)}")

    generated = 0
    skipped   = []

    for row_id, iso2 in rows:
        rings = merged.get(iso2)
        if not rings:
            skipped.append(iso2)
            continue
        svg = make_svg(rings)
        if not svg:
            skipped.append(iso2)
            continue
        cur.execute(
            "UPDATE visual_questions SET shape_svg = %s WHERE id = %s",
            (svg, row_id)
        )
        generated += 1

    conn.commit()

    cur.execute("SELECT COUNT(*) FROM visual_questions WHERE topic='geography' AND shape_svg IS NOT NULL;")
    filled = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM visual_questions WHERE topic='geography' AND shape_svg IS NULL;")
    still_null = cur.fetchone()[0]
    conn.close()

    print(f"\nDone.")
    print(f"  Generated this run: {generated}")
    print(f"  Skipped this run:   {len(skipped)}  - {skipped}")
    print(f"  Total shapes in DB: {filled}")
    print(f"  Still missing:      {still_null} (small islands/territories  - OK to ignore)")


if __name__ == "__main__":
    main()


